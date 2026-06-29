"""In-process registry of connected bridges + request/response dispatch over their WS connections."""

from __future__ import annotations

import asyncio
import base64
from typing import Any, Protocol

from app.core.logging import get_logger

log = get_logger("relay")


class BridgeOffline(Exception):
    pass


class WSLike(Protocol):
    async def send_json(self, data: Any) -> None: ...


class _Conn:
    def __init__(self, ws: WSLike, bridge_id: str) -> None:
        self.ws = ws
        self.bridge_id = bridge_id
        self.company_guid: str | None = None
        self.pending: dict[str, asyncio.Future[bytes]] = {}


class BridgeRegistry:
    def __init__(self) -> None:
        self._by_org: dict[str, _Conn] = {}

    async def connect(self, org_id: str, bridge_id: str, ws: WSLike) -> None:
        # last writer wins: replace any stale connection for this org
        old = self._by_org.get(org_id)
        if old is not None:
            self._fail_pending(old, "replaced by a new connection")
        self._by_org[org_id] = _Conn(ws, bridge_id)
        log.info("bridge.connected", org_id=org_id, bridge_id=bridge_id)

    async def disconnect(self, org_id: str, ws: WSLike) -> None:
        conn = self._by_org.get(org_id)
        if conn is not None and conn.ws is ws:
            self._fail_pending(conn, "bridge disconnected")
            del self._by_org[org_id]
            log.info("bridge.disconnected", org_id=org_id, bridge_id=conn.bridge_id)

    def is_online(self, org_id: str) -> bool:
        return org_id in self._by_org

    def company_guid(self, org_id: str) -> str | None:
        conn = self._by_org.get(org_id)
        return conn.company_guid if conn else None

    async def dispatch(
        self, org_id: str, job_id: str, xml: bytes, company_guid: str | None, *, timeout: float = 120.0
    ) -> bytes:
        conn = self._by_org.get(org_id)
        if conn is None:
            raise BridgeOffline("no bridge connected for this organization")
        future: asyncio.Future[bytes] = asyncio.get_event_loop().create_future()
        conn.pending[job_id] = future
        await conn.ws.send_json(
            {
                "type": "push_job",
                "job_id": job_id,
                "company_guid": company_guid or "",
                "xml_b64": base64.b64encode(xml).decode("ascii"),
            }
        )
        try:
            return await asyncio.wait_for(future, timeout)
        except asyncio.TimeoutError as exc:
            raise BridgeOffline("bridge did not respond in time") from exc
        finally:
            conn.pending.pop(job_id, None)

    async def on_frame(self, org_id: str, frame: dict[str, Any]) -> None:
        conn = self._by_org.get(org_id)
        if conn is None:
            return
        kind = frame.get("type")
        if kind == "heartbeat":
            conn.company_guid = frame.get("company_guid") or conn.company_guid
        elif kind == "job_result":
            future = conn.pending.get(str(frame.get("job_id")))
            if future is not None and not future.done():
                tally_xml = str(frame.get("tally_xml", ""))
                future.set_result(tally_xml.encode("utf-8", "replace"))
        elif kind == "job_error":
            future = conn.pending.get(str(frame.get("job_id")))
            if future is not None and not future.done():
                future.set_exception(BridgeOffline(str(frame.get("reason", "bridge error"))))

    @staticmethod
    def _fail_pending(conn: _Conn, reason: str) -> None:
        for future in conn.pending.values():
            if not future.done():
                future.set_exception(BridgeOffline(reason))
        conn.pending.clear()


registry = BridgeRegistry()
