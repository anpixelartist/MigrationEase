"""Bridge pairing/auth + the in-process relay registry."""

from __future__ import annotations

import asyncio

import pytest
from starlette.websockets import WebSocketDisconnect

from app.relay.registry import BridgeOffline, BridgeRegistry
from app.services import bridge_service


# ---- pairing / auth ----
def test_create_bridge_and_status(client):
    resp = client.post("/bridges", json={"name": "My PC"})
    assert resp.status_code == 201
    body = resp.json()
    assert body["api_key"].startswith("bk_") and body["bridge_id"]
    assert client.get("/bridge/status").json()["online"] is False


def test_create_bridge_requires_auth(raw_client):
    assert raw_client.post("/bridges", json={"name": "x"}).status_code == 401


def test_authenticate(client):
    api_key = client.post("/bridges", json={"name": "PC"}).json()["api_key"]
    assert bridge_service.authenticate(api_key) is not None
    assert bridge_service.authenticate("bk_wrong") is None
    assert bridge_service.authenticate(None) is None


def test_ws_rejects_bad_key(raw_client):
    with pytest.raises(WebSocketDisconnect):
        with raw_client.websocket_connect("/bridge/ws?key=bk_invalid"):
            pass


# ---- registry dispatch ----
class _FakeWS:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send_json(self, data) -> None:
        self.sent.append(data)


@pytest.mark.asyncio
async def test_registry_dispatch_roundtrip():
    reg = BridgeRegistry()
    ws = _FakeWS()
    await reg.connect("org1", "b1", ws)
    assert reg.is_online("org1")

    task = asyncio.create_task(reg.dispatch("org1", "job1", b"<ENVELOPE/>", "GUID-1"))
    await asyncio.sleep(0)  # let dispatch send the frame
    assert ws.sent and ws.sent[0]["type"] == "push_job" and ws.sent[0]["job_id"] == "job1"

    await reg.on_frame(
        "org1",
        {"type": "job_result", "job_id": "job1", "tally_xml": "<RESPONSE><CREATED>1</CREATED></RESPONSE>"},
    )
    result = await task
    assert b"CREATED" in result


@pytest.mark.asyncio
async def test_registry_offline_raises():
    with pytest.raises(BridgeOffline):
        await BridgeRegistry().dispatch("nope", "j", b"x", None)


@pytest.mark.asyncio
async def test_registry_job_error_propagates():
    reg = BridgeRegistry()
    await reg.connect("o", "b", _FakeWS())
    task = asyncio.create_task(reg.dispatch("o", "j1", b"x", None))
    await asyncio.sleep(0)
    await reg.on_frame("o", {"type": "job_error", "job_id": "j1", "reason": "company_mismatch"})
    with pytest.raises(BridgeOffline):
        await task


@pytest.mark.asyncio
async def test_registry_disconnect_fails_pending():
    reg = BridgeRegistry()
    ws = _FakeWS()
    await reg.connect("o", "b", ws)
    task = asyncio.create_task(reg.dispatch("o", "j1", b"x", None))
    await asyncio.sleep(0)
    await reg.disconnect("o", ws)
    assert not reg.is_online("o")
    with pytest.raises(BridgeOffline):
        await task
