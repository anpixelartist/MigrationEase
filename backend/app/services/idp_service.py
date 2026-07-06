"""Map validated OIDC (Keycloak) token claims onto the app's own identity + tenancy tables.

Design rule (tenant safety): Keycloak authenticates *who* is calling; the app database decides
*which org* they may act in. ``sub``/``azp`` from a **verified** token are looked up in
``users``/``memberships``/``service_accounts`` — token claims never carry tenant authority, and
frontend-supplied tenant ids (X-Org-Id) are only honoured after a membership check
(``auth_service.resolve_org_for_user``), exactly as in the legacy flow.
"""

from __future__ import annotations

import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.core.config import get_settings
from app.core.errors import BadRequest, Conflict, Forbidden, Unauthorized
from app.core.logging import get_logger
from app.db.base import new_session
from app.db.models import Membership, Organization, ServiceAccount, User
from app.services.auth_service import _unique_slug  # same slug rules as signup

log = get_logger("idp")

_CLIENT_ID_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{1,254}$")
_SERVICE_EMAIL_DOMAIN = "service-accounts.local"


def resolve_user_identity(claims: dict[str, Any]) -> tuple[str, str]:
    """Return ``(user_id, email)`` for a validated end-user token, creating/linking as needed.

    Order: (1) existing user by ``idp_sub``; (2) link an existing local account by *verified*
    email (migration path for pre-Keycloak users); (3) JIT-provision user + personal org + owner
    membership — the same shape ``auth_service.signup`` produces.
    """
    settings = get_settings()
    sub = str(claims["sub"])
    email = str(claims.get("email") or "").strip().lower()
    email_verified = bool(claims.get("email_verified", False))

    with new_session() as session:
        user = session.scalar(select(User).where(User.idp_sub == sub))
        if user is not None:
            return user.id, user.email

        if email and settings.oidc_link_by_email:
            existing = session.scalar(select(User).where(User.email == email))
            if existing is not None:
                if not email_verified:
                    # An unverified email must not take over an existing local account.
                    raise Unauthorized(
                        "Email not verified.",
                        detail="Verify your email with the identity provider, then sign in again.",
                        code="email_unverified",
                    )
                if existing.idp_sub is not None and existing.idp_sub != sub:
                    raise Conflict("This account is already linked to another identity.", code="idp_conflict")
                existing.idp_sub = sub
                session.commit()
                log.info("idp.link", user_id=existing.id)
                return existing.id, existing.email

        if not settings.oidc_jit_provisioning:
            raise Forbidden("No account for this identity.", code="no_account")
        if not email:
            raise Unauthorized("Token is missing the email claim.", code="missing_email")
        if settings.oidc_require_verified_email and not email_verified:
            # Don't mint a first-class identity (+ workspace) for an unverified email — it would let
            # anyone self-register any address and poison verified-email linking / invites later.
            raise Unauthorized(
                "Email not verified.",
                detail="Verify your email with the identity provider, then sign in again.",
                code="email_unverified",
            )

        user = User(email=email, password_hash=None, full_name=claims.get("name"), idp_sub=sub)
        session.add(user)
        try:
            session.flush()
        except IntegrityError:
            # Concurrent first request already provisioned this identity — reuse it.
            session.rollback()
            user = session.scalar(select(User).where(User.idp_sub == sub))
            if user is None:
                raise Unauthorized("Could not resolve identity.", code="invalid_credentials") from None
            return user.id, user.email
        org = Organization(
            name=f"{email.split('@')[0]}'s workspace",
            slug=_unique_slug(session, email.split("@")[0]),
        )
        session.add(org)
        session.flush()
        session.add(Membership(org_id=org.id, user_id=user.id, role="owner"))
        session.commit()
        log.info("idp.jit_provision", user_id=user.id, org_id=org.id)
        return user.id, user.email


def resolve_service_account(client_id: str) -> tuple[str, str, str, str] | None:
    """Return ``(user_id, email, org_id, role)`` for a registered, active M2M client — else None.

    Returning None (rather than raising) lets the caller distinguish "unknown azp → treat as a
    user token" from "registered but revoked", which is rejected here.
    """
    with new_session() as session:
        sa_row = session.scalar(select(ServiceAccount).where(ServiceAccount.client_id == client_id))
        if sa_row is None:
            return None
        if sa_row.status != "active":
            raise Forbidden("This service account has been revoked.", code="service_account_revoked")
        user = session.get(User, sa_row.user_id)
        if user is None:  # should not happen; fail closed
            raise Unauthorized("Service account user missing.", code="invalid_credentials")
        return user.id, user.email, sa_row.org_id, sa_row.role


def register_service_account(*, org_id: str, actor_role: str, client_id: str, name: str | None, role: str) -> dict:
    """Org owner/admin authorizes a Keycloak client (by clientId) for machine access to THIS org."""
    if actor_role not in ("owner", "admin"):
        raise Forbidden("Only org owners/admins can register service accounts.", code="admin_required")
    client_id = (client_id or "").strip()
    if not _CLIENT_ID_RE.match(client_id):
        raise BadRequest("Invalid client_id.", code="invalid_client_id")
    if role not in ("admin", "member"):
        raise BadRequest("role must be 'admin' or 'member'.", code="invalid_role")
    # A public/standard-flow client (the SPA) must never become a service account: every end-user
    # token carries azp=<web client id>, so registering it would map ALL those tokens onto one org
    # (auth-confusion hijack). Reject the configured web client id.
    settings = get_settings()
    if settings.oidc_web_client_id and client_id.lower() == settings.oidc_web_client_id.strip().lower():
        raise BadRequest(
            "That client id is the public login client and cannot be a service account.",
            code="public_client_forbidden",
        )

    with new_session() as session:
        if session.scalar(select(ServiceAccount).where(ServiceAccount.client_id == client_id)) is not None:
            raise Conflict("This client_id is already registered.", code="client_id_taken")
        # Synthetic user so downstream FKs (jobs.created_by) and /auth/me-style lookups work.
        email = f"{client_id.lower()}@{_SERVICE_EMAIL_DOMAIN}"
        user = session.scalar(select(User).where(User.email == email))
        if user is None:
            user = User(email=email, password_hash=None, full_name=f"Service: {client_id}")
            session.add(user)
            session.flush()
        session.add(Membership(org_id=org_id, user_id=user.id, role=role))
        sa_row = ServiceAccount(
            org_id=org_id, user_id=user.id, client_id=client_id, name=name or client_id, role=role
        )
        session.add(sa_row)
        session.commit()
        log.info("idp.service_account.registered", org_id=org_id, client_id=client_id, role=role)
        return {"id": sa_row.id, "client_id": client_id, "name": sa_row.name, "role": role, "status": "active"}
