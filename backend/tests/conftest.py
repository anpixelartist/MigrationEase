"""Shared test fixtures: a fresh in-memory DB per test + an authenticated client wrapper."""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings


@pytest.fixture(autouse=True)
def _fresh_env(monkeypatch, tmp_path):
    """Isolate each test: fresh file SQLite (safe for worker-thread access), engine, and store."""
    db_path = tmp_path / "test.db"
    monkeypatch.setenv("TM_DATABASE_URL", f"sqlite:///{db_path.as_posix()}")
    monkeypatch.setenv("TM_STORAGE_BACKEND", "memory")
    get_settings.cache_clear()
    from app.db import base as db_base
    from app.services import job_repo
    from app import storage

    db_base.reset_engine()
    db_base.init_db()
    job_repo.reset_store()
    storage.reset_storage()
    yield
    db_base.reset_engine()
    job_repo.reset_store()
    storage.reset_storage()
    get_settings.cache_clear()


class AuthedClient:
    """Wraps TestClient, injecting an Authorization header on every request."""

    def __init__(self, tc: TestClient, headers: dict[str, str]) -> None:
        self.tc = tc
        self.headers = headers

    def _merge(self, kwargs: dict) -> dict:
        headers = dict(self.headers)
        headers.update(kwargs.pop("headers", {}) or {})
        kwargs["headers"] = headers
        return kwargs

    def get(self, url, **kwargs):
        return self.tc.get(url, **self._merge(kwargs))

    def post(self, url, **kwargs):
        return self.tc.post(url, **self._merge(kwargs))

    def delete(self, url, **kwargs):
        return self.tc.delete(url, **self._merge(kwargs))

    def raw(self) -> TestClient:
        return self.tc

    def org_id(self) -> str:
        return self.get("/auth/me").json()["orgs"][0]["org_id"]


def make_authed(tc: TestClient, email: str | None = None, password: str = "password123") -> AuthedClient:
    email = email or f"user-{uuid.uuid4().hex[:10]}@example.com"
    resp = tc.post("/auth/signup", json={"email": email, "password": password})
    assert resp.status_code == 201, resp.text
    token = resp.json()["access_token"]
    return AuthedClient(tc, {"Authorization": f"Bearer {token}"})


@pytest.fixture
def raw_client(_fresh_env) -> TestClient:
    from app.main import create_app

    return TestClient(create_app())


@pytest.fixture
def client(raw_client) -> AuthedClient:
    return make_authed(raw_client)
