"""Auth request/response models."""

from __future__ import annotations

from pydantic import BaseModel, Field


class SignupRequest(BaseModel):
    email: str
    password: str
    full_name: str | None = None
    org_name: str | None = None


class LoginRequest(BaseModel):
    email: str
    password: str


class OrgMembership(BaseModel):
    org_id: str
    name: str
    role: str


class UserResponse(BaseModel):
    id: str
    email: str
    full_name: str | None = None
    orgs: list[OrgMembership] = Field(default_factory=list)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserResponse


class AuthConfigResponse(BaseModel):
    """Public bootstrap info the SPA needs to pick a login flow. Contains no secrets."""

    mode: str  # legacy | hybrid | keycloak
    issuer: str | None = None
    client_id: str | None = None  # public SPA client (Authorization Code + PKCE)


class ServiceAccountRequest(BaseModel):
    client_id: str = Field(min_length=2, max_length=255)
    name: str | None = Field(default=None, max_length=200)
    role: str = "member"  # admin | member


class ServiceAccountResponse(BaseModel):
    id: str
    client_id: str
    name: str
    role: str
    status: str
