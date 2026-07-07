"""Keycloak/OIDC integration: token validation, JIT provisioning, email linking, tenant
isolation, machine-to-machine service accounts, auth modes, and rate limiting.

Tokens are minted with a local RSA key; the realm JWKS lookup is stubbed so validation runs the
real PyJWT verification path (signature, issuer, audience, expiry) without a Keycloak server.
"""

from __future__ import annotations

import datetime as dt
import uuid

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from app.core import oidc
from app.core.config import get_settings

ISSUER = "http://testidp/realms/tallymigration"
AUDIENCE = "tallymigration-api"

_PRIVATE_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_PUBLIC_KEY = _PRIVATE_KEY.public_key()


class _FakeSigningKey:
    key = _PUBLIC_KEY


class _FakeJWKClient:
    def get_signing_key_from_jwt(self, token: str) -> _FakeSigningKey:
        return _FakeSigningKey()


def mint(
    *,
    sub: str | None = None,
    email: str | None = None,
    email_verified: bool = True,
    azp: str = "tallymigration-web",
    aud: str = AUDIENCE,
    iss: str = ISSUER,
    expires_in: int = 300,
    name: str | None = None,
) -> str:
    now = dt.datetime.now(dt.timezone.utc)
    claims: dict = {
        "iss": iss,
        "aud": aud,
        "sub": sub or f"kc-{uuid.uuid4().hex}",
        "azp": azp,
        "iat": now,
        "exp": now + dt.timedelta(seconds=expires_in),
    }
    if email is not None:
        claims["email"] = email
        claims["email_verified"] = email_verified
    if name is not None:
        claims["name"] = name
    return jwt.encode(claims, _PRIVATE_KEY, algorithm="RS256", headers={"kid": "test-key"})


@pytest.fixture
def oidc_env(monkeypatch):
    """Switch the app to hybrid mode against the fake IdP."""
    monkeypatch.setenv("TM_AUTH_MODE", "hybrid")
    monkeypatch.setenv("TM_OIDC_ISSUER", ISSUER)
    monkeypatch.setenv("TM_OIDC_AUDIENCE", AUDIENCE)
    get_settings.cache_clear()
    monkeypatch.setattr(oidc, "_jwk_client", lambda url: _FakeJWKClient())
    yield
    get_settings.cache_clear()


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# ---- end-user tokens -------------------------------------------------------------------------


def test_oidc_jit_provisions_user_and_org(raw_client, oidc_env):
    token = mint(email="new.user@example.com", name="New User")
    me = raw_client.get("/auth/me", headers=_bearer(token))
    assert me.status_code == 200, me.text
    body = me.json()
    assert body["email"] == "new.user@example.com"
    assert len(body["orgs"]) == 1 and body["orgs"][0]["role"] == "owner"

    # Same subject again → same account, no duplicate provisioning.
    again = raw_client.get("/auth/me", headers=_bearer(mint(sub=jwt.decode(token, options={"verify_signature": False})["sub"], email="new.user@example.com")))
    assert again.status_code == 200
    assert again.json()["id"] == body["id"]


def test_oidc_links_existing_account_by_verified_email(raw_client, oidc_env):
    signup = raw_client.post("/auth/signup", json={"email": "old@example.com", "password": "password123"})
    assert signup.status_code == 201
    legacy_user_id = signup.json()["user"]["id"]

    me = raw_client.get("/auth/me", headers=_bearer(mint(email="old@example.com", email_verified=True)))
    assert me.status_code == 200
    assert me.json()["id"] == legacy_user_id  # linked, not duplicated


def test_oidc_unverified_email_cannot_take_over_account(raw_client, oidc_env):
    raw_client.post("/auth/signup", json={"email": "victim@example.com", "password": "password123"})
    me = raw_client.get("/auth/me", headers=_bearer(mint(email="victim@example.com", email_verified=False)))
    assert me.status_code == 401
    assert me.json()["code"] == "email_unverified"


def test_oidc_rejects_wrong_audience_issuer_and_expired(raw_client, oidc_env):
    for bad in (
        mint(email="a@example.com", aud="someone-else"),
        mint(email="a@example.com", iss="http://evil/realms/x"),
        mint(email="a@example.com", expires_in=-60),
    ):
        r = raw_client.get("/auth/me", headers=_bearer(bad))
        assert r.status_code == 401


def test_oidc_tenant_isolation_between_jit_users(raw_client, oidc_env):
    token_a = mint(email="tenant.a@example.com")
    token_b = mint(email="tenant.b@example.com")
    job = raw_client.post("/jobs", json={"entity_type": "ledger"}, headers=_bearer(token_a))
    assert job.status_code == 201
    job_id = job.json()["id"]
    assert raw_client.get(f"/jobs/{job_id}", headers=_bearer(token_a)).status_code == 200
    # Another tenant must not even learn the job exists.
    assert raw_client.get(f"/jobs/{job_id}", headers=_bearer(token_b)).status_code == 404


def test_hybrid_mode_still_accepts_legacy_tokens(raw_client, oidc_env):
    r = raw_client.post("/auth/signup", json={"email": "legacy@example.com", "password": "password123"})
    assert r.status_code == 201
    me = raw_client.get("/auth/me", headers=_bearer(r.json()["access_token"]))
    assert me.status_code == 200 and me.json()["email"] == "legacy@example.com"


# ---- machine-to-machine (client credentials) --------------------------------------------------


def test_service_account_m2m_flow(raw_client, oidc_env):
    # Org owner (OIDC user) registers the client id for THEIR org.
    owner = mint(email="owner@example.com")
    reg = raw_client.post(
        "/auth/service-accounts",
        json={"client_id": "example-m2m", "name": "ERP sync", "role": "member"},
        headers=_bearer(owner),
    )
    assert reg.status_code == 201, reg.text
    assert reg.json()["client_id"] == "example-m2m"

    # A client-credentials token (azp=example-m2m, no email claim) can now act inside that org.
    svc = mint(azp="example-m2m", sub=f"service-account-{uuid.uuid4().hex}")
    job = raw_client.post("/jobs", json={"entity_type": "ledger"}, headers=_bearer(svc))
    assert job.status_code == 201, job.text

    # ... and the org owner sees the job it created (same tenant).
    assert raw_client.get(f"/jobs/{job.json()['id']}", headers=_bearer(owner)).status_code == 200

    # Duplicate registration of the same client id is rejected.
    dup = raw_client.post(
        "/auth/service-accounts", json={"client_id": "example-m2m"}, headers=_bearer(owner)
    )
    assert dup.status_code == 409


def test_unregistered_client_credentials_token_is_rejected(raw_client, oidc_env):
    svc = mint(azp="rogue-client", sub="service-account-rogue")  # valid signature, no org mapping
    r = raw_client.post("/jobs", json={"entity_type": "ledger"}, headers=_bearer(svc))
    assert r.status_code == 401


def test_service_account_registration_requires_admin(raw_client, oidc_env):
    owner = mint(email="boss@example.com")
    raw_client.post(
        "/auth/service-accounts", json={"client_id": "svc-x"}, headers=_bearer(owner)
    )  # owner of org X — fine (201), just setting up

    # A different JIT user is owner of their OWN org, so they can register for their org,
    # but a 'member' role must be rejected. Simulate by inserting a member membership directly.
    from app.db.base import new_session
    from app.db.models import Membership, User
    from sqlalchemy import select

    member_token = mint(email="member@example.com")
    assert raw_client.get("/auth/me", headers=_bearer(member_token)).status_code == 200
    with new_session() as session:
        user = session.scalar(select(User).where(User.email == "member@example.com"))
        m = session.scalar(select(Membership).where(Membership.user_id == user.id))
        m.role = "member"
        session.commit()

    r = raw_client.post(
        "/auth/service-accounts", json={"client_id": "svc-y"}, headers=_bearer(member_token)
    )
    assert r.status_code == 403 and r.json()["code"] == "admin_required"


# ---- auth modes & hardening --------------------------------------------------------------------


def test_keycloak_mode_disables_password_endpoints(raw_client, oidc_env, monkeypatch):
    monkeypatch.setenv("TM_AUTH_MODE", "keycloak")
    get_settings.cache_clear()
    r = raw_client.post("/auth/login", json={"email": "a@b.com", "password": "password123"})
    assert r.status_code == 403 and r.json()["code"] == "password_auth_disabled"
    r = raw_client.post("/auth/signup", json={"email": "a@b.com", "password": "password123"})
    assert r.status_code == 403
    # OIDC tokens still work; legacy HS256 tokens do not exist in this mode.
    assert raw_client.get("/auth/me", headers=_bearer(mint(email="kc@example.com"))).status_code == 200


def test_auth_config_endpoint(raw_client, oidc_env):
    cfg = raw_client.get("/auth/config")
    assert cfg.status_code == 200
    body = cfg.json()
    assert body["mode"] == "hybrid"
    assert body["issuer"] == ISSUER
    assert body["client_id"] == "tallymigration-web"


def test_auth_config_legacy_default(raw_client):
    body = raw_client.get("/auth/config").json()
    assert body == {"mode": "legacy", "issuer": None, "client_id": None}


def test_password_login_rate_limit(raw_client, monkeypatch):
    monkeypatch.setenv("TM_AUTH_RATE_LIMIT", "3/minute")
    get_settings.cache_clear()
    payload = {"email": "nobody@example.com", "password": "wrong-password"}
    for _ in range(3):
        assert raw_client.post("/auth/login", json=payload).status_code == 401
    r = raw_client.post("/auth/login", json=payload)
    assert r.status_code == 429 and r.json()["code"] == "rate_limited"
    get_settings.cache_clear()


def test_idp_only_user_cannot_password_login(raw_client, oidc_env):
    assert raw_client.get("/auth/me", headers=_bearer(mint(email="sso.only@example.com"))).status_code == 200
    r = raw_client.post("/auth/login", json={"email": "sso.only@example.com", "password": "password123"})
    assert r.status_code == 401 and r.json()["code"] == "invalid_credentials"
