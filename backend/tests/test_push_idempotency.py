"""Push idempotency: the state machine must make double-importing into Tally impossible.

Covers: re-push blocked after success and after partial import, retry allowed after a clean
failure, concurrent-claim rejection (with stale-claim recovery), and claim release when no bridge
was ever reached. Direct pushes stub the Tally gateway at the urllib layer.
"""

from __future__ import annotations

import datetime as dt
import io
import urllib.request

import pytest

from app.core.config import get_settings
from app.services.job_store import STALE_PUSH_SECONDS, JobStatus
from test_api import _create, _drive_to_validated, run_generate, run_push

SUCCESS_XML = b"""<RESPONSE><CREATED>2</CREATED><ALTERED>0</ALTERED><DELETED>0</DELETED>
<COMBINED>0</COMBINED><IGNORED>0</IGNORED><ERRORS>0</ERRORS><CANCELLED>0</CANCELLED>
<EXCEPTIONS>0</EXCEPTIONS></RESPONSE>"""

PARTIAL_XML = b"""<RESPONSE><CREATED>1</CREATED><ALTERED>0</ALTERED><DELETED>0</DELETED>
<COMBINED>0</COMBINED><IGNORED>0</IGNORED><ERRORS>1</ERRORS><CANCELLED>0</CANCELLED>
<EXCEPTIONS>0</EXCEPTIONS><LINEERROR>Ledger 'Beta Corp' rejected</LINEERROR></RESPONSE>"""

FAILED_XML = b"""<RESPONSE><CREATED>0</CREATED><ALTERED>0</ALTERED><DELETED>0</DELETED>
<COMBINED>0</COMBINED><IGNORED>0</IGNORED><ERRORS>2</ERRORS><CANCELLED>0</CANCELLED>
<EXCEPTIONS>0</EXCEPTIONS><LINEERROR>bad</LINEERROR></RESPONSE>"""


@pytest.fixture
def direct_push(monkeypatch):
    """Enable dev direct-push and let each test choose the Tally response bytes."""
    monkeypatch.setenv("TM_DIRECT_TALLY_PUSH", "true")
    get_settings.cache_clear()
    state = {"body": SUCCESS_XML}

    class _Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(
        urllib.request, "urlopen", lambda req, timeout=0: _Resp(state["body"])
    )
    yield state
    get_settings.cache_clear()


def _generated_job(client) -> str:
    job_id = _create(client)
    _drive_to_validated(client, job_id)
    gen = run_generate(client, job_id, "Test1")
    assert gen["state"] == "done"
    return job_id


def test_successful_push_blocks_repush(client, direct_push):
    job_id = _generated_job(client)
    r = run_push(client, job_id)
    assert r["state"] == "done" and r["result"]["status"] == "pushed"
    assert r["result"]["is_success"] is True

    # Re-pushing an already-imported batch would duplicate every ledger in Tally -> 409.
    again = client.post(f"/jobs/{job_id}/push")
    assert again.status_code == 409, again.text
    assert client.get(f"/jobs/{job_id}").json()["status"] == "pushed"


def test_partial_import_blocks_repush_and_allows_regenerate(client, direct_push):
    direct_push["body"] = PARTIAL_XML
    job_id = _generated_job(client)
    r = run_push(client, job_id)
    assert r["state"] == "done" and r["result"]["status"] == "pushed_partial"
    assert r["result"]["is_success"] is False and r["result"]["created"] == 1

    # 1 row is already inside Tally: a blind re-push would duplicate it.
    assert client.post(f"/jobs/{job_id}/push").status_code == 409
    # The recovery path — fix the data and regenerate — is open...
    direct_push["body"] = SUCCESS_XML
    gen = run_generate(client, job_id, "Test1")
    assert gen["state"] == "done"
    # ...and a fresh generate re-arms the push.
    assert run_push(client, job_id)["result"]["status"] == "pushed"


def test_failed_import_allows_retry(client, direct_push):
    direct_push["body"] = FAILED_XML
    job_id = _generated_job(client)
    r = run_push(client, job_id)
    assert r["state"] == "done" and r["result"]["status"] == "push_failed"
    assert client.get(f"/jobs/{job_id}").json()["status"] == "push_failed"

    # Nothing was imported, so retry is safe — and this time Tally accepts it.
    direct_push["body"] = SUCCESS_XML
    assert run_push(client, job_id)["result"]["status"] == "pushed"


def test_concurrent_push_claim_is_rejected(client, direct_push):
    job_id = _generated_job(client)
    from app.services.job_repo import get_db_store

    store = get_db_store()
    org_id = client.get("/auth/me").json()["orgs"][0]["org_id"]
    # simulate an in-flight push claimed by another worker
    with store.lock(org_id, job_id) as job:
        job.advance(JobStatus.PUSHING)

    r = client.post(f"/jobs/{job_id}/push")
    assert r.status_code == 409

    # ...but an ABANDONED claim (worker died mid-push) becomes reclaimable.
    with store.lock(org_id, job_id) as job:
        job.updated_at = dt.datetime.now(dt.timezone.utc) - dt.timedelta(
            seconds=STALE_PUSH_SECONDS + 5
        )
    assert run_push(client, job_id)["result"]["status"] == "pushed"


def test_no_bridge_releases_claim_for_retry(client):
    # direct push OFF and no bridge connected -> 503, and the job returns to 'generated'
    job_id = _generated_job(client)
    r = run_push(client, job_id)
    assert r["state"] == "error" and r["problem"]["status"] == 503
    assert r["problem"]["code"] == "bridge_unavailable"
    assert client.get(f"/jobs/{job_id}").json()["status"] == "generated"
