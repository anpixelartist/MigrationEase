"""Production-hardening guards for the OIDC path (service-level, no live Keycloak needed)."""

from __future__ import annotations

import uuid

import pytest

from app.core.config import get_settings
from app.core.errors import BadRequest, Unauthorized
from app.services import idp_service
from tests.conftest import make_authed


def _claims(email="new@example.com", verified=False):
    return {"sub": f"kc-{uuid.uuid4().hex[:8]}", "email": email, "email_verified": verified, "name": "New User"}


def test_public_web_client_cannot_be_service_account(raw_client):
    """Registering the SPA's public client id would map every end-user token onto one org."""
    owner = make_authed(raw_client, email="owner@example.com")
    org_id = owner.org_id()
    web_client = get_settings().oidc_web_client_id  # "tallymigration-web"

    with pytest.raises(BadRequest) as exc:
        idp_service.register_service_account(
            org_id=org_id, actor_role="owner", client_id=web_client, name="hijack", role="member"
        )
    assert exc.value.code == "public_client_forbidden"


def test_confidential_client_registers_fine(raw_client):
    owner = make_authed(raw_client, email="owner2@example.com")
    out = idp_service.register_service_account(
        org_id=owner.org_id(), actor_role="owner", client_id="reporting-m2m", name="Reporting", role="member"
    )
    assert out["client_id"] == "reporting-m2m"
    assert out["status"] == "active"


def test_jit_requires_verified_email_by_default(raw_client):
    """Default posture: an unverified email cannot JIT-provision a first-class identity."""
    with pytest.raises(Unauthorized) as exc:
        idp_service.resolve_user_identity(_claims(verified=False))
    assert exc.value.code == "email_unverified"


def test_jit_provisions_when_email_verified(raw_client):
    user_id, email = idp_service.resolve_user_identity(_claims(email="ok@example.com", verified=True))
    assert email == "ok@example.com"
    assert user_id


def test_jit_verified_gate_can_be_disabled_for_dev(raw_client, monkeypatch):
    monkeypatch.setenv("TM_OIDC_REQUIRE_VERIFIED_EMAIL", "false")
    get_settings.cache_clear()
    user_id, email = idp_service.resolve_user_identity(_claims(email="dev@example.com", verified=False))
    assert email == "dev@example.com" and user_id
    get_settings.cache_clear()
