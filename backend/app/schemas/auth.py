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
