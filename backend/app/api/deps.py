"""Request dependencies: resolve the authenticated principal (user + active org + role).

Two token types, selected by ``TM_AUTH_MODE``:

- **legacy** — first-party HS256 JWTs from /auth/login|signup (symmetric ``TM_JWT_SECRET``).
- **keycloak** — OIDC RS256/ES256 access tokens verified against the realm JWKS
  (issuer + audience enforced). End-user tokens resolve through users/memberships (JIT
  provisioning / verified-email linking); client-credentials tokens resolve through the
  admin-registered ``service_accounts`` table.
- **hybrid** accepts both during migration, routed by the token's ``alg`` header — each
  validator still enforces its own explicit algorithm allow-list, so there is no confusion path.

Tenant context always comes from a server-side membership check (never from token claims or the
raw X-Org-Id header).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import jwt
from fastapi import Header

from app.core.config import get_settings
from app.core.errors import Forbidden, Unauthorized
from app.core.logging import bind_context
from app.core.oidc import decode_oidc_token, token_algorithm
from app.core.security import decode_token
from app.services import auth_service, idp_service


@dataclass
class Principal:
    user_id: str
    email: str
    org_id: str
    role: str
    auth_kind: str = "user"  # user | service


def _legacy_principal(payload: dict[str, Any], x_org_id: str | None) -> Principal:
    user_id = str(payload.get("sub"))
    email = str(payload.get("email", ""))
    org_id, role = auth_service.resolve_org_for_user(user_id, x_org_id)
    return Principal(user_id=user_id, email=email, org_id=org_id, role=role)


def _oidc_principal(claims: dict[str, Any], x_org_id: str | None) -> Principal:
    # Client-credentials caller? Only if an org admin registered this clientId (azp) — the
    # mapping table is the sole tenancy authority for machine tokens.
    azp = claims.get("azp") or claims.get("client_id")
    if azp:
        svc = idp_service.resolve_service_account(str(azp))
        if svc is not None:
            user_id, email, org_id, role = svc
            if x_org_id and x_org_id != org_id:
                raise Forbidden("This service account is not a member of that organization.", code="not_a_member")
            return Principal(user_id=user_id, email=email, org_id=org_id, role=role, auth_kind="service")

    user_id, email = idp_service.resolve_user_identity(claims)
    org_id, role = auth_service.resolve_org_for_user(user_id, x_org_id)
    return Principal(user_id=user_id, email=email, org_id=org_id, role=role)


def get_principal(
    authorization: str | None = Header(default=None),
    x_org_id: str | None = Header(default=None),
) -> Principal:
    """Validate the bearer token and resolve the active organization (X-Org-Id, else the default)."""
    if not authorization or not authorization.lower().startswith("bearer "):
        raise Unauthorized("Missing or malformed Authorization header.")
    token = authorization.split(" ", 1)[1].strip()
    mode = get_settings().auth_mode

    try:
        if mode == "legacy":
            principal = _legacy_principal(decode_token(token), x_org_id)
        elif mode == "keycloak":
            principal = _oidc_principal(decode_oidc_token(token), x_org_id)
        else:  # hybrid: route by header alg; each decoder re-enforces its own allow-list
            if token_algorithm(token).startswith("HS"):
                principal = _legacy_principal(decode_token(token), x_org_id)
            else:
                principal = _oidc_principal(decode_oidc_token(token), x_org_id)
    except jwt.PyJWTError as exc:
        # Uniform 401 — never echo which check failed beyond the generic detail.
        raise Unauthorized("Invalid or expired token.", detail=str(exc)) from exc

    bind_context(user_id=principal.user_id, org_id=principal.org_id)
    return principal
