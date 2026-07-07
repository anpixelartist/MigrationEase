"""OIDC (Keycloak) access-token validation.

The API is a pure OAuth2 *resource server*: it never talks to Keycloak interactively, it only
verifies RS256/ES256 bearer tokens offline against the realm's JWKS (fetched lazily and cached by
``jwt.PyJWKClient``) and enforces issuer + audience + expiry.

Tenant context is NEVER read from these tokens — see ``app.services.idp_service`` which maps the
validated ``sub``/``azp`` onto the app's own users/memberships/service_accounts tables.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any

import jwt

from app.core.config import get_settings

# Explicit allow-list — Keycloak realm keys are RS256 by default; ES256 supported for
# realms configured with EC keys. HS* is never accepted here (no alg-confusion with
# the legacy first-party secret).
_ALLOWED_ALGS = ["RS256", "ES256"]


@lru_cache(maxsize=4)
def _jwk_client(jwks_url: str) -> jwt.PyJWKClient:
    # PyJWKClient caches fetched keys and refreshes on unknown ``kid`` (key rotation).
    return jwt.PyJWKClient(jwks_url, cache_keys=True, lifespan=300)


def _signing_key_for(token: str) -> Any:
    """Resolve the verification key for the token's ``kid`` from the realm JWKS."""
    return _jwk_client(get_settings().oidc_jwks_endpoint).get_signing_key_from_jwt(token).key


def decode_oidc_token(token: str) -> dict[str, Any]:
    """Verify a Keycloak access token; returns its claims.

    Raises ``jwt.PyJWTError`` (invalid signature / issuer / audience / expiry) or
    ``jwt.exceptions.PyJWKClientError`` (JWKS unreachable, unknown kid).
    """
    settings = get_settings()
    return jwt.decode(
        token,
        _signing_key_for(token),
        algorithms=_ALLOWED_ALGS,
        issuer=settings.oidc_issuer,
        audience=settings.oidc_audience,
        options={"require": ["exp", "sub", "iss", "aud"]},
    )


def token_algorithm(token: str) -> str:
    """The (unverified) ``alg`` header — used only to ROUTE between validators in hybrid
    mode; each validator still enforces its own explicit algorithm allow-list."""
    return str(jwt.get_unverified_header(token).get("alg", ""))
