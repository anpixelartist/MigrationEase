"""Auth use-cases: signup (user + personal org + owner membership), login, org resolution.

Each function opens its own short-lived session (unit of work) — consistent with the threadpool
execution model used across the service layer.
"""

from __future__ import annotations

import re

from sqlalchemy import select

from app.core.errors import BadRequest, Conflict, Forbidden, Unauthorized
from app.core.logging import get_logger
from app.core.security import create_access_token, hash_password, verify_password
from app.db.base import new_session
from app.db.models import Membership, Organization, User
from app.schemas.auth import OrgMembership, TokenResponse, UserResponse

log = get_logger("auth")

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_MIN_PASSWORD = 8


def _slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.strip().lower()).strip("-")
    return slug or "org"


def _unique_slug(session, base: str) -> str:
    root = _slugify(base)
    slug = root
    suffix = 1
    while session.scalar(select(Organization).where(Organization.slug == slug)) is not None:
        suffix += 1
        slug = f"{root}-{suffix}"
    return slug


def _user_response(session, user: User) -> UserResponse:
    rows = session.execute(
        select(Membership, Organization)
        .join(Organization, Membership.org_id == Organization.id)
        .where(Membership.user_id == user.id)
        .order_by(Membership.id)
    ).all()
    orgs = [OrgMembership(org_id=org.id, name=org.name, role=m.role) for m, org in rows]
    return UserResponse(id=user.id, email=user.email, full_name=user.full_name, orgs=orgs)


def signup(email: str, password: str, full_name: str | None, org_name: str | None) -> TokenResponse:
    email = (email or "").strip().lower()
    if not _EMAIL_RE.match(email):
        raise BadRequest("Enter a valid email address.", code="invalid_email")
    if len(password or "") < _MIN_PASSWORD:
        raise BadRequest(f"Password must be at least {_MIN_PASSWORD} characters.", code="weak_password")

    with new_session() as session:
        if session.scalar(select(User).where(User.email == email)) is not None:
            raise Conflict("An account with this email already exists.", code="email_taken")
        user = User(email=email, password_hash=hash_password(password), full_name=full_name)
        session.add(user)
        session.flush()
        org = Organization(
            name=org_name or f"{email.split('@')[0]}'s workspace",
            slug=_unique_slug(session, org_name or email.split("@")[0]),
        )
        session.add(org)
        session.flush()
        session.add(Membership(org_id=org.id, user_id=user.id, role="owner"))
        session.commit()
        log.info("signup", user_id=user.id, org_id=org.id)
        token = create_access_token(user_id=user.id, email=user.email)
        return TokenResponse(access_token=token, user=_user_response(session, user))


def login(email: str, password: str) -> TokenResponse:
    email = (email or "").strip().lower()
    with new_session() as session:
        user = session.scalar(select(User).where(User.email == email))
        if user is None or not verify_password(user.password_hash, password):
            log.warning("login.failed", email=email)
            raise Unauthorized("Invalid email or password.", code="invalid_credentials")
        token = create_access_token(user_id=user.id, email=user.email)
        log.info("login", user_id=user.id)
        return TokenResponse(access_token=token, user=_user_response(session, user))


def get_user_response(user_id: str) -> UserResponse:
    with new_session() as session:
        user = session.get(User, user_id)
        if user is None:
            raise Unauthorized("User no longer exists.", code="invalid_credentials")
        return _user_response(session, user)


def resolve_org_for_user(user_id: str, org_id: str | None) -> tuple[str, str]:
    """Return (org_id, role) for the user, validating membership. Defaults to the first org."""
    with new_session() as session:
        if session.get(User, user_id) is None:
            raise Unauthorized("User no longer exists.", code="invalid_credentials")
        if org_id:
            membership = session.scalar(
                select(Membership).where(Membership.user_id == user_id, Membership.org_id == org_id)
            )
            if membership is None:
                raise Forbidden("You are not a member of this organization.", code="not_a_member")
            return org_id, membership.role
        membership = session.scalar(
            select(Membership).where(Membership.user_id == user_id).order_by(Membership.id)
        )
        if membership is None:
            raise Forbidden("This user has no organization.", code="no_org")
        return membership.org_id, membership.role
