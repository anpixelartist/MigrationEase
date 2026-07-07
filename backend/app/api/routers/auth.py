"""Auth endpoints: signup, login, me, public auth config, and M2M service-account registration.

The password endpoints exist only for ``TM_AUTH_MODE=legacy|hybrid``; in ``keycloak`` mode the
SPA signs in at Keycloak (Authorization Code + PKCE) and this API only *verifies* tokens.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from starlette.concurrency import run_in_threadpool

from app.api.deps import Principal, get_principal
from app.core.config import get_settings
from app.core.errors import Forbidden
from app.core.ratelimit import auth_rate_limit
from app.schemas.auth import (
    AuthConfigResponse,
    LoginRequest,
    ServiceAccountRequest,
    ServiceAccountResponse,
    SignupRequest,
    TokenResponse,
    UserResponse,
)
from app.services import auth_service, idp_service

router = APIRouter(prefix="/auth", tags=["auth"])


def _require_password_auth() -> None:
    if get_settings().auth_mode == "keycloak":
        raise Forbidden(
            "Password authentication is disabled.",
            detail="Sign in through the identity provider.",
            code="password_auth_disabled",
        )


@router.get("/config", response_model=AuthConfigResponse)
async def auth_config() -> AuthConfigResponse:
    """Public, secret-free bootstrap info: which login flow the SPA should render."""
    s = get_settings()
    oidc = s.auth_mode in ("hybrid", "keycloak")
    return AuthConfigResponse(
        mode=s.auth_mode,
        issuer=s.oidc_issuer if oidc else None,
        client_id=s.oidc_web_client_id if oidc else None,
    )


@router.post("/signup", response_model=TokenResponse, status_code=201, dependencies=[Depends(auth_rate_limit)])
async def signup(body: SignupRequest) -> TokenResponse:
    _require_password_auth()
    return await run_in_threadpool(
        auth_service.signup, body.email, body.password, body.full_name, body.org_name
    )


@router.post("/login", response_model=TokenResponse, dependencies=[Depends(auth_rate_limit)])
async def login(body: LoginRequest) -> TokenResponse:
    _require_password_auth()
    return await run_in_threadpool(auth_service.login, body.email, body.password)


@router.get("/me", response_model=UserResponse)
async def me(principal: Principal = Depends(get_principal)) -> UserResponse:
    return await run_in_threadpool(auth_service.get_user_response, principal.user_id)


@router.post("/service-accounts", response_model=ServiceAccountResponse, status_code=201)
async def register_service_account(
    body: ServiceAccountRequest, principal: Principal = Depends(get_principal)
) -> ServiceAccountResponse:
    """Authorize a Keycloak client (client-credentials) for machine access to the CALLER's org.

    The caller must be an owner/admin of the org resolved from their own validated token — this
    server-side check is what makes M2M tenancy trustworthy (the M2M token's claims never are).
    """
    result = await run_in_threadpool(
        idp_service.register_service_account,
        org_id=principal.org_id,
        actor_role=principal.role,
        client_id=body.client_id,
        name=body.name,
        role=body.role,
    )
    return ServiceAccountResponse(**result)
