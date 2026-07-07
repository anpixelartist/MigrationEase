"""Cross-process bridge relay over Redis pub/sub.

Problem this solves: bridge WebSockets terminate in an **API** process (in-memory
``BridgeRegistry``), but with ``TM_BROKER_URL`` set, push tasks run in a **separate taskiq
worker** whose registry is empty — so production pushes used to fail unconditionally.

Flow (only active when the broker is Redis; otherwise everything is in-process and unused):

    worker: dispatch()            API replica holding the socket: responder loop
    ───────────────────           ────────────────────────────────────────────
    presence check  ─────────►    (bridge connect/heartbeat refreshes bridge:online:{org})
    publish bridge:dispatch ──►   claim request via SET NX (exactly-one replica relays,
    subscribe bridge:reply:{id}    even if several hold a connection for the org)
    await reply    ◄──────────    registry.dispatch() over the local WS, publish the reply

The SET-NX claim is load-bearing: a duplicate relay would import the batch into Tally twice.
"""

from __future__ import annotations

import asyncio
import base64
import json
import uuid
from typing import Any

from app.core.config import get_settings
from app.core.logging import get_logger
from app.relay.registry import BridgeNotConnected, BridgeOffline, registry

log = get_logger("relay.redis")

DISPATCH_CHANNEL = "bridge:dispatch"
REPLY_PREFIX = "bridge:reply:"
CLAIM_PREFIX = "bridge:claim:"
PRESENCE_PREFIX = "bridge:online:"
PRESENCE_TTL_SECONDS = 90  # refreshed by bridge heartbeats (~15 s apart)

_client: Any = None


def _redis_url() -> str | None:
    url = get_settings().broker_url
    return url if url.startswith("redis") else None


def get_client() -> Any:
    """Shared async Redis client; None when the broker is not Redis. Patched in tests."""
    global _client
    if _client is None:
        url = _redis_url()
        if url is None:
            return None
        import redis.asyncio as aioredis

        _client = aioredis.from_url(url, decode_responses=True)
    return _client


def reset_client() -> None:
    """Test helper: drop the cached client so a new TM_BROKER_URL takes effect."""
    global _client
    _client = None


# ---- presence (which orgs have a live bridge somewhere in the fleet) ---------------------------


async def presence_connected(org_id: str) -> None:
    client = get_client()
    if client is not None:
        await client.set(PRESENCE_PREFIX + org_id, "1", ex=PRESENCE_TTL_SECONDS)


async def presence_refresh(org_id: str) -> None:
    await presence_connected(org_id)


async def presence_disconnected(org_id: str) -> None:
    client = get_client()
    if client is not None:
        await client.delete(PRESENCE_PREFIX + org_id)


async def is_online_anywhere(org_id: str) -> bool:
    """Bridge online on this replica, or (per Redis presence) on any other."""
    if registry.is_online(org_id):
        return True
    client = get_client()
    if client is None:
        return False
    return bool(await client.exists(PRESENCE_PREFIX + org_id))


# ---- requester side (runs in the taskiq worker) -------------------------------------------------


async def dispatch(
    org_id: str, job_id: str, xml: bytes, company: str | None, *, timeout: float = 120.0
) -> bytes:
    """Relay XML to the org's bridge, wherever its socket lives, and await Tally's response."""
    # Fast path: this process holds the socket (dev in-process broker, or the lucky replica).
    if registry.is_online(org_id):
        return await registry.dispatch(org_id, job_id, xml, company, timeout=timeout)

    client = get_client()
    if client is None:
        raise BridgeNotConnected("no bridge connected for this organization")
    if not await client.exists(PRESENCE_PREFIX + org_id):
        raise BridgeNotConnected("no bridge connected for this organization")

    request_id = uuid.uuid4().hex
    pubsub = client.pubsub()
    await pubsub.subscribe(REPLY_PREFIX + request_id)
    try:
        await client.publish(
            DISPATCH_CHANNEL,
            json.dumps(
                {
                    "request_id": request_id,
                    "org_id": org_id,
                    "job_id": job_id,
                    "company": company or "",
                    "xml_b64": base64.b64encode(xml).decode("ascii"),
                    "timeout": timeout,
                }
            ),
        )
        # +10 s so the responder's own registry timeout fires first (its error is more precise).
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout + 10.0
        while True:
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise BridgeOffline("bridge did not respond in time")
            message = await pubsub.get_message(
                ignore_subscribe_messages=True, timeout=min(remaining, 1.0)
            )
            if message is None:
                continue
            reply = json.loads(message["data"])
            if reply.get("ok"):
                return base64.b64decode(reply["response_b64"])
            raise BridgeOffline(str(reply.get("error") or "bridge error"))
    finally:
        try:
            await pubsub.unsubscribe(REPLY_PREFIX + request_id)
            await pubsub.aclose()
        except Exception:  # noqa: BLE001 — cleanup must not mask the real outcome
            pass


# ---- responder side (runs in every API process) --------------------------------------------------


async def _handle_request(raw: str) -> None:
    try:
        req = json.loads(raw)
        org_id = str(req["org_id"])
        request_id = str(req["request_id"])
    except (ValueError, KeyError):
        log.warning("relay.bad_request_frame")
        return
    if not registry.is_online(org_id):
        return  # some other replica holds the socket (or nobody — requester times out)
    client = get_client()
    # Exactly-once relay even if several replicas hold a connection for this org.
    claimed = await client.set(CLAIM_PREFIX + request_id, "1", nx=True, ex=300)
    if not claimed:
        return
    reply_channel = REPLY_PREFIX + request_id
    try:
        response = await registry.dispatch(
            org_id,
            str(req["job_id"]),
            base64.b64decode(req["xml_b64"]),
            str(req.get("company") or "") or None,
            timeout=float(req.get("timeout", 120.0)),
        )
        payload = {"ok": True, "response_b64": base64.b64encode(response).decode("ascii")}
    except BridgeOffline as exc:
        payload = {"ok": False, "error": str(exc)}
    await client.publish(reply_channel, json.dumps(payload))


async def responder_loop() -> None:
    client = get_client()
    if client is None:
        return
    pubsub = client.pubsub()
    await pubsub.subscribe(DISPATCH_CHANNEL)
    log.info("relay.responder_started")
    try:
        async for message in pubsub.listen():
            if message.get("type") != "message":
                continue
            # handle concurrently: a slow Tally push must not block other orgs' requests
            asyncio.create_task(_handle_request(message["data"]))
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001
        log.error("relay.responder_crashed", exc_info=True)
    finally:
        try:
            await pubsub.unsubscribe(DISPATCH_CHANNEL)
            await pubsub.aclose()
        except Exception:  # noqa: BLE001
            pass


_responder_task: asyncio.Task | None = None


async def start_responder() -> None:
    """App-startup hook: run the responder loop in this API process (no-op without Redis)."""
    global _responder_task
    if _redis_url() is None or _responder_task is not None:
        return
    _responder_task = asyncio.get_running_loop().create_task(responder_loop())


async def stop_responder() -> None:
    global _responder_task
    if _responder_task is not None:
        _responder_task.cancel()
        _responder_task = None
