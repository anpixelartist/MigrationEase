"""Auth flow, tenant isolation, and durable persistence."""

from __future__ import annotations

from conftest import make_authed


def test_signup_login_me(raw_client):
    resp = raw_client.post(
        "/auth/signup", json={"email": "a@b.com", "password": "password123", "full_name": "Aa"}
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["token_type"] == "bearer"
    assert body["user"]["email"] == "a@b.com"
    assert len(body["user"]["orgs"]) == 1 and body["user"]["orgs"][0]["role"] == "owner"
    token = body["access_token"]

    assert raw_client.post("/auth/login", json={"email": "a@b.com", "password": "password123"}).status_code == 200

    me = raw_client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200 and me.json()["email"] == "a@b.com"


def test_signup_duplicate_email(raw_client):
    raw_client.post("/auth/signup", json={"email": "d@b.com", "password": "password123"})
    r = raw_client.post("/auth/signup", json={"email": "d@b.com", "password": "password123"})
    assert r.status_code == 409 and r.json()["code"] == "email_taken"


def test_signup_weak_password(raw_client):
    r = raw_client.post("/auth/signup", json={"email": "w@b.com", "password": "short"})
    assert r.status_code == 400 and r.json()["code"] == "weak_password"


def test_signup_invalid_email(raw_client):
    r = raw_client.post("/auth/signup", json={"email": "notanemail", "password": "password123"})
    assert r.status_code == 400 and r.json()["code"] == "invalid_email"


def test_login_bad_credentials(raw_client):
    raw_client.post("/auth/signup", json={"email": "x@b.com", "password": "password123"})
    r = raw_client.post("/auth/login", json={"email": "x@b.com", "password": "wrongpassword"})
    assert r.status_code == 401 and r.json()["code"] == "invalid_credentials"


def test_jobs_require_auth(raw_client):
    assert raw_client.post("/jobs", json={"entity_type": "ledger"}).status_code == 401


def test_invalid_token_rejected(raw_client):
    r = raw_client.post(
        "/jobs", json={"entity_type": "ledger"}, headers={"Authorization": "Bearer not-a-jwt"}
    )
    assert r.status_code == 401


def test_tenant_isolation(raw_client):
    org_a = make_authed(raw_client)
    org_b = make_authed(raw_client)
    job_id = org_a.post("/jobs", json={"entity_type": "ledger"}).json()["id"]
    assert org_a.get(f"/jobs/{job_id}").status_code == 200  # owner sees it
    assert org_b.get(f"/jobs/{job_id}").status_code == 404  # another org cannot


def test_persistence_survives_cache_reset(raw_client):
    client = make_authed(raw_client)
    job_id = client.post("/jobs", json={"entity_type": "ledger"}).json()["id"]
    client.post(
        f"/jobs/{job_id}/file",
        files={"file": ("l.csv", b"Ledger Name,Under\nCust A,Sundry Debtors\n", "text/csv")},
    )
    client.post(f"/jobs/{job_id}/mapping", json={"mapping": {"name": "Ledger Name", "parent": "Under"}})

    # drop the in-process working cache; the durable DB record remains
    from app.services import job_repo

    job_repo.reset_store()

    # GET must rebuild the working state (parsed df, mapped df) from the persisted upload bytes
    got = client.get(f"/jobs/{job_id}")
    assert got.status_code == 200
    assert got.json()["status"] == "mapped"
    assert got.json()["rows"] == 1

    # and downstream stages still work on the hydrated state
    from test_api import run_validate

    val = run_validate(client, job_id, known_groups=["Sundry Debtors"])
    assert val["state"] == "done" and val["result"]["ok"] is True
