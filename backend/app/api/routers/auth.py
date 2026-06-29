"""Auth endpoints: signup, login, me."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from starlette.concurrency import run_in_threadpool

from app.api.deps import Principal, get_principal
from app.schemas.auth import LoginRequest, SignupRequest, TokenResponse, UserResponse
from app.services import auth_service

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/signup", response_model=TokenResponse, status_code=201)
async def signup(body: SignupRequest) -> TokenResponse:
    return await run_in_threadpool(
        auth_service.signup, body.email, body.password, body.full_name, body.org_name
    )


@router.post("/login", response_model=TokenResponse)
async def login(body: LoginRequest) -> TokenResponse:
    return await run_in_threadpool(auth_service.login, body.email, body.password)


@router.get("/me", response_model=UserResponse)
async def me(principal: Principal = Depends(get_principal)) -> UserResponse:
    return await run_in_threadpool(auth_service.get_user_response, principal.user_id)
