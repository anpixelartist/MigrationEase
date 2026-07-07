"""Cross-process bridge relay (Redis pub/sub) — exercised against an in-memory fake Redis.

The scenario being protected: bridge WebSockets live in API processes, push tasks run in a
separate worker. The worker publishes a dispatch request; exactly ONE replica holding the socket
claims it (SET NX), relays over the WS, and publishes Tally's reply back.
"""

from __future__ import annotations

import asyncio
import base64
import json
import uuid

import pytest

from app.relay import redis_relay
from app.relay.registry import BridgeNotConnected, BridgeRegistry


# ---- minimal async fake of the redis-py surface the relay uses ---------------------------------


class FakePubSub:
    def __init__(self, server: "FakeRedis") -> None:
        self._server = server
        self._queues: dict[str, asyncio.Queue] = {}

    async def subscribe(self, channel: str) -> None:
        q: asyncio.Queue = asyncio.Queue()
        self._queues[channel] = q
        self._server.subs.setdefault(channel, []).append(q)

    async def unsubscribe(self, channel: str) -> None:
        q = self._queues.pop(channel, None)
        if q is not None:
            self._server.subs.get(channel, []).remove(q)

    async def get_message(self, ignore_subscribe_messages: bool = False, timeout: float = 1.0):
        for channel, q in self._queues.items():
            try:
                data = await asyncio.wait_for(q.get(), timeout)
            except asyncio.TimeoutError:
                return None
            return {"type": "message", "channel": channel, "data": data}
        await asyncio.sleep(min(timeout, 0.01))
        return None

    async def aclose(self) -> None:
        for channel in list(self._queues):
            await self.unsubscribe(channel)


class FakeRedis:
    def __init__(self) -> None:
        self.kv: dict[str, str] = {}
        self.subs: dict[str, list[asyncio.Queue]] = {}

    async def set(self, key: str, value: str, nx: bool = False, ex: int | None = None):
        if nx and key in self.kv:
            return None
        self.kv[key] = value
        return True

    async def exists(self, key: str) -> int:
        return 1 if key in self.kv else 0

    async def delete(self, key: str) -> None:
        self.kv.pop(key, None)

    async def publish(self, channel: str, data: str) -> None:
        for q in self.subs.get(channel, []):
            q.put_nowait(data)

    def pubsub(self) -> FakePubSub:
        return FakePubSub(self)


class FakeWS:
    """Quacks like the registry's WSLike; records dispatched frames."""

    def __init__(self) -> None:
        self.frames: list[dict] = []

    async def send_json(self, data) -> None:
        self.frames.append(data)


@pytest.fixture
def fake_redis(monkeypatch):
    server = FakeRedis()
    monkeypatch.setattr(redis_relay, "get_client", lambda: server)
    return server


# ---- requester side (what the taskiq worker runs) -----------------------------------------------


async def test_dispatch_fails_fast_when_no_bridge_anywhere(fake_redis):
    with pytest.raises(BridgeNotConnected):
        await redis_relay.dispatch("org-none", "job1", b"<XML/>", None, timeout=1.0)


async def test_dispatch_round_trip_via_pubsub(fake_redis):
    org = f"org-{uuid.uuid4().hex}"
    await fake_redis.set(redis_relay.PRESENCE_PREFIX + org, "1")

    async def remote_replica() -> None:
        # simulate the API replica: receive the dispatch request, publish Tally's reply
        pubsub = fake_redis.pubsub()
        await pubsub.subscribe(redis_relay.DISPATCH_CHANNEL)
        msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=2.0)
        req = json.loads(msg["data"])
        assert base64.b64decode(req["xml_b64"]) == b"<ENVELOPE/>"
        await fake_redis.publish(
            redis_relay.REPLY_PREFIX + req["request_id"],
            json.dumps(
                {"ok": True, "response_b64": base64.b64encode(b"<RESPONSE/>").decode()}
            ),
        )

    replica = asyncio.create_task(remote_replica())
    await asyncio.sleep(0.05)  # let the replica subscribe before we publish
    response = await redis_relay.dispatch(org, "job-1", b"<ENVELOPE/>", "Test1", timeout=2.0)
    await replica
    assert response == b"<RESPONSE/>"


async def test_dispatch_surfaces_remote_error(fake_redis):
    org = f"org-{uuid.uuid4().hex}"
    await fake_redis.set(redis_relay.PRESENCE_PREFIX + org, "1")

    async def remote_replica() -> None:
        pubsub = fake_redis.pubsub()
        await pubsub.subscribe(redis_relay.DISPATCH_CHANNEL)
        msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=2.0)
        req = json.loads(msg["data"])
        await fake_redis.publish(
            redis_relay.REPLY_PREFIX + req["request_id"],
            json.dumps({"ok": False, "error": "tally crashed mid-chunk"}),
        )

    replica = asyncio.create_task(remote_replica())
    await asyncio.sleep(0.05)
    with pytest.raises(Exception, match="tally crashed"):
        await redis_relay.dispatch(org, "job-1", b"<ENVELOPE/>", None, timeout=2.0)
    await replica


# ---- responder side (what each API replica runs) -------------------------------------------------


def _request_payload(org: str, request_id: str) -> str:
    return json.dumps(
        {
            "request_id": request_id,
            "org_id": org,
            "job_id": "job-9",
            "company": "Test1",
            "xml_b64": base64.b64encode(b"<ENVELOPE/>").decode(),
            "timeout": 2.0,
        }
    )


async def test_responder_relays_via_local_socket_and_replies(fake_redis, monkeypatch):
    org = f"org-{uuid.uuid4().hex}"
    local_registry = BridgeRegistry()
    monkeypatch.setattr(redis_relay, "registry", local_registry)
    ws = FakeWS()
    await local_registry.connect(org, "bridge-1", ws)

    request_id = uuid.uuid4().hex
    reply_ps = fake_redis.pubsub()
    await reply_ps.subscribe(redis_relay.REPLY_PREFIX + request_id)

    handler = asyncio.create_task(redis_relay._handle_request(_request_payload(org, request_id)))
    await asyncio.sleep(0.05)
    assert ws.frames and ws.frames[0]["type"] == "push_job"  # XML went out over the local WS
    await local_registry.on_frame(
        org, {"type": "job_result", "job_id": "job-9", "tally_xml": "<RESPONSE/>"}
    )
    await handler

    msg = await reply_ps.get_message(ignore_subscribe_messages=True, timeout=2.0)
    reply = json.loads(msg["data"])
    assert reply["ok"] is True
    assert base64.b64decode(reply["response_b64"]) == b"<RESPONSE/>"


async def test_claim_prevents_duplicate_relay(fake_redis, monkeypatch):
    """Two replicas both holding a socket must not BOTH relay (= double Tally import)."""
    org = f"org-{uuid.uuid4().hex}"
    local_registry = BridgeRegistry()
    monkeypatch.setattr(redis_relay, "registry", local_registry)
    ws = FakeWS()
    await local_registry.connect(org, "bridge-1", ws)

    request_id = uuid.uuid4().hex
    payload = _request_payload(org, request_id)
    h1 = asyncio.create_task(redis_relay._handle_request(payload))
    h2 = asyncio.create_task(redis_relay._handle_request(payload))  # second replica, same request
    await asyncio.sleep(0.05)
    assert len(ws.frames) == 1  # the SET-NX claim let exactly one through
    await local_registry.on_frame(
        org, {"type": "job_result", "job_id": "job-9", "tally_xml": "<RESPONSE/>"}
    )
    await asyncio.gather(h1, h2)
    assert len(ws.frames) == 1


async def test_responder_ignores_requests_for_orgs_it_does_not_hold(fake_redis, monkeypatch):
    local_registry = BridgeRegistry()
    monkeypatch.setattr(redis_relay, "registry", local_registry)
    await redis_relay._handle_request(_request_payload("org-elsewhere", uuid.uuid4().hex))
    assert fake_redis.kv == {}  # no claim taken -> another replica remains free to handle it
