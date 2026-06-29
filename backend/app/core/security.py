"""Password hashing (Argon2id) + JWT (HS256), using the vetted libraries argon2-cffi and PyJWT."""

from __future__ import annotations

import datetime as _dt
from typing import Any

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from app.core.config import get_settings

_hasher = PasswordHasher()  # Argon2id with OWASP-sane defaults


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        _hasher.verify(password_hash, password)
        return True
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def create_access_token(*, user_id: str, email: str, minutes: int | None = None) -> str:
    settings = get_settings()
    now = _dt.datetime.now(_dt.timezone.utc)
    payload = {
        "sub": user_id,
        "email": email,
        "iat": now,
        "exp": now + _dt.timedelta(minutes=minutes or settings.jwt_expire_minutes),
        "type": "access",
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm="HS256")


def decode_token(token: str) -> dict[str, Any]:
    """Decode + verify a JWT. Raises jwt.PyJWTError on invalid/expired tokens."""
    settings = get_settings()
    return jwt.decode(
        token,
        settings.jwt_secret,
        algorithms=["HS256"],  # explicit allow-list (no alg-confusion)
        options={"require": ["exp", "sub"]},
    )
