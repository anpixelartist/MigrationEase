"""Request dependencies: resolve the authenticated principal (user + active org + role)."""

from __future__ import annotations

from dataclasses import dataclass

import jwt
from fastapi import Header

from app.core.errors import Unauthorized
from app.core.logging import bind_context
from app.core.security import decode_token
from app.services import auth_service


@dataclass
class Principal:
    user_id: str
    email: str
    org_id: str
    role: str


def get_principal(
    authorization: str | None = Header(default=None),
    x_org_id: str | None = Header(default=None),
) -> Principal:
    """Validate the bearer token and resolve the active organization (X-Org-Id, else the default)."""
    if not authorization or not authorization.lower().startswith("bearer "):
        raise Unauthorized("Missing or malformed Authorization header.")
    token = authorization.split(" ", 1)[1].strip()
    try:
        payload = decode_token(token)
    except jwt.PyJWTError as exc:
        raise Unauthorized("Invalid or expired token.", detail=str(exc)) from exc

    user_id = str(payload.get("sub"))
    email = str(payload.get("email", ""))
    org_id, role = auth_service.resolve_org_for_user(user_id, x_org_id)
    bind_context(user_id=user_id, org_id=org_id)
    return Principal(user_id=user_id, email=email, org_id=org_id, role=role)
