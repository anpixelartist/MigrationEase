"""API integration tests: full lifecycle + extensive edge cases (TestClient)."""

from __future__ import annotations

import io

import pandas as pd

from app.core.config import get_settings

# `client` (authenticated) and `raw_client` fixtures + the in-memory DB setup live in conftest.py.

LEDGER_CSV = (
    b"Ledger Name,Under,Opening Bal\n"
    b"Cust A,Sundry Debtors,15000\n"
    b"Cust B,Sundry Debtors,(500)\n"
)
LEDGER_MAPPING = {"mapping": {"name": "Ledger Name", "parent": "Under", "opening_balance": "Opening Bal"}}


# ---- helpers ----
def _create(client, entity="ledger") -> str:
    r = client.post("/jobs", json={"entity_type": entity})
    assert r.status_code == 201
    return r.json()["id"]


def _upload(client, job_id, content=LEDGER_CSV, filename="ledgers.csv"):
    return client.post(f"/jobs/{job_id}/file", files={"file": (filename, content, "text/csv")})


def _await_task(client, job_id, enqueue_resp) -> dict:
    """Take a 202 {task_id} response, poll the task to completion, return the terminal envelope."""
    assert enqueue_resp.status_code == 202, enqueue_resp.text
    task_id = enqueue_resp.json()["task_id"]
    for _ in range(200):
        body = client.get(f"/jobs/{job_id}/tasks/{task_id}").json()
        if body.get("state") != "pending":
            return body
    raise AssertionError("background task did not finish")


def run_validate(client, job_id, **known) -> dict:
    return _await_task(client, job_id, client.post(f"/jobs/{job_id}/validate", json=known))


def run_generate(client, job_id, company=None) -> dict:
    return _await_task(client, job_id, client.post(f"/jobs/{job_id}/generate", json={"company": company}))


def run_push(client, job_id) -> dict:
    return _await_task(client, job_id, client.post(f"/jobs/{job_id}/push"))


def _drive_to_validated(client, job_id, *, known_groups=("Sundry Debtors",)):
    assert _upload(client, job_id).status_code == 200
    assert client.post(f"/jobs/{job_id}/mapping", json=LEDGER_MAPPING).status_code == 200
    return run_validate(client, job_id, known_groups=list(known_groups))


# ---- happy path ----
def test_full_lifecycle_ledger(client):
    job_id = _create(client)
    assert _upload(client, job_id).json()["status"] == "parsed"

    prof = client.get(f"/jobs/{job_id}/profile").json()
    assert prof["row_count"] == 2 and prof["column_count"] == 3

    sugg = client.get(f"/jobs/{job_id}/mapping/suggestions").json()
    assert sugg["unmapped_required"] == []

    assert client.post(f"/jobs/{job_id}/mapping", json=LEDGER_MAPPING).json()["status"] == "mapped"

    val = run_validate(client, job_id, known_groups=["Sundry Debtors"])
    assert val["state"] == "done" and val["result"]["ok"] is True

    gen = run_generate(client, job_id, "Test1")
    assert gen["state"] == "done" and gen["result"]["generated"] == 2

    art = client.get(f"/jobs/{job_id}/artifact")
    assert art.status_code == 200
    body = art.content
    assert b"<LEDGER" in body
    assert b"<OPENINGBALANCE>15000.00</OPENINGBALANCE>" in body
    assert b"<OPENINGBALANCE>-500.00</OPENINGBALANCE>" in body  # "(500)" coerced to a credit


# ---- entity / job edge cases ----
def test_invalid_entity_type(client):
    r = client.post("/jobs", json={"entity_type": "banana"})
    assert r.status_code == 422 and r.json()["code"] == "invalid_entity_type"


def test_get_missing_job(client):
    r = client.get("/jobs/does-not-exist")
    assert r.status_code == 404 and r.json()["code"] == "job_not_found"


def test_upload_to_missing_job(client):
    r = _upload(client, "nope")
    assert r.status_code == 404


# ---- upload edge cases ----
def test_upload_empty_file(client):
    job_id = _create(client)
    r = _upload(client, job_id, content=b"")
    assert r.status_code == 422 and r.json()["code"] == "empty_file"


def test_upload_corrupt_xlsx(client):
    job_id = _create(client)
    r = _upload(client, job_id, content=b"this is not really an excel file", filename="bad.xlsx")
    assert r.status_code == 422 and r.json()["code"] == "parse_failed"


def test_upload_single_column_not_split(client):
    job_id = _create(client)
    r = _upload(client, job_id, content=b"Ledger Name\nCash\nHDFC Bank\n")
    assert r.json()["columns"] == 1


def test_upload_xlsx_roundtrip(client):
    df = pd.DataFrame({"Ledger Name": ["A"], "Under": ["Sundry Debtors"]})
    buf = io.BytesIO()
    df.to_excel(buf, index=False)
    job_id = _create(client)
    r = _upload(client, job_id, content=buf.getvalue(), filename="x.xlsx")
    assert r.status_code == 200 and r.json()["columns"] == 2


def test_upload_too_large(client):
    # the middleware closes over the cached Settings singleton, so mutating it takes effect live
    settings = get_settings()
    original = settings.max_upload_bytes
    settings.max_upload_bytes = 500  # blocks a 5 KB upload, allows the small create JSON
    try:
        job_id = _create(client)
        r = _upload(client, job_id, content=b"x" * 5000)
        assert r.status_code == 413 and r.json()["code"] == "file_too_large"
    finally:
        settings.max_upload_bytes = original


# ---- state-machine edge cases ----
def test_mapping_before_upload_is_conflict(client):
    job_id = _create(client)
    r = client.post(f"/jobs/{job_id}/mapping", json=LEDGER_MAPPING)
    assert r.status_code == 409 and r.json()["code"] == "invalid_state"


def test_profile_before_upload(client):
    job_id = _create(client)
    assert client.get(f"/jobs/{job_id}/profile").status_code == 409


def test_validate_before_mapping(client):
    job_id = _create(client)
    _upload(client, job_id)
    assert client.post(f"/jobs/{job_id}/validate", json={}).status_code == 409


def test_artifact_before_generate(client):
    job_id = _create(client)
    _drive_to_validated(client, job_id)
    assert client.get(f"/jobs/{job_id}/artifact").status_code == 409


def test_reupload_resets_downstream(client):
    job_id = _create(client)
    _upload(client, job_id)
    client.post(f"/jobs/{job_id}/mapping", json=LEDGER_MAPPING)
    # re-upload -> back to parsed, mapping cleared
    assert _upload(client, job_id).json()["status"] == "parsed"
    assert client.get(f"/jobs/{job_id}/validation").status_code == 409


# ---- mapping edge cases ----
def test_mapping_missing_required(client):
    job_id = _create(client)
    _upload(client, job_id)
    r = client.post(f"/jobs/{job_id}/mapping", json={"mapping": {"name": "Ledger Name"}})
    assert r.status_code == 422 and r.json()["code"] == "missing_required_mapping"


def test_mapping_unknown_source_column(client):
    job_id = _create(client)
    _upload(client, job_id)
    r = client.post(
        f"/jobs/{job_id}/mapping",
        json={"mapping": {"name": "Ledger Name", "parent": "NoSuchColumn"}},
    )
    assert r.status_code == 400 and r.json()["code"] == "bad_request"


def test_mapping_unknown_target_field(client):
    job_id = _create(client)
    _upload(client, job_id)
    r = client.post(
        f"/jobs/{job_id}/mapping",
        json={"mapping": {"name": "Ledger Name", "parent": "Under", "bogus": "Opening Bal"}},
    )
    assert r.status_code == 400


# ---- validation edge cases ----
def test_validation_unknown_group_blocks_generate(client):
    job_id = _create(client)
    _upload(client, job_id, content=b"Ledger Name,Under\nCust A,Sundry Debtrs\n")
    client.post(f"/jobs/{job_id}/mapping", json={"mapping": {"name": "Ledger Name", "parent": "Under"}})
    val = run_validate(client, job_id, known_groups=["Sundry Debtors"])
    assert val["state"] == "done" and val["result"]["ok"] is False
    assert any(e["code"] == "unknown_group" for e in val["result"]["errors"])
    gen = run_generate(client, job_id, "T")
    assert gen["state"] == "error" and gen["problem"]["code"] == "validation_failed"


def test_validation_duplicate_name(client):
    job_id = _create(client)
    _upload(client, job_id, content=b"Ledger Name,Under\nDup,Sundry Debtors\nDup,Sundry Debtors\n")
    client.post(f"/jobs/{job_id}/mapping", json={"mapping": {"name": "Ledger Name", "parent": "Under"}})
    val = run_validate(client, job_id, known_groups=["Sundry Debtors"])
    assert any(e["code"] == "duplicate_in_file" for e in val["result"]["errors"])


def test_validation_without_snapshot_is_lenient(client):
    job_id = _create(client)
    r = _drive_to_validated(client, job_id, known_groups=())  # no known groups
    assert r["result"]["ok"] is True  # group membership not checked when snapshot absent


# ---- resolution + generate (update path) ----
def test_resolve_and_generate_alter(client):
    job_id = _create(client)
    _upload(client, job_id, content=b"Ledger Name,Under\nCust A,Sundry Debtors\n")
    client.post(f"/jobs/{job_id}/mapping", json={"mapping": {"name": "Ledger Name", "parent": "Under"}})
    run_validate(client, job_id, known_groups=["Sundry Debtors"])
    res = client.post(
        f"/jobs/{job_id}/resolve",
        json={"existing": [{"name": "Cust A", "parent": "Sundry Debtors", "guid": "g-1"}]},
    ).json()
    assert res["buckets"].get("update") == 1
    run_generate(client, job_id, "Test1")
    body = client.get(f"/jobs/{job_id}/artifact").content
    assert b'ACTION="Alter"' in body
    assert b"<GUID>g-1</GUID>" in body


# ---- push gating ----
def test_push_disabled_errors_with_503_problem(client):
    job_id = _create(client)
    _drive_to_validated(client, job_id)
    run_generate(client, job_id, "Test1")
    p = run_push(client, job_id)
    assert p["state"] == "error"
    assert p["problem"]["status"] == 503 and p["problem"]["code"] == "bridge_unavailable"
