"""Bridge endpoints: pairing (issue API key), status, and the outbound WSS relay endpoint."""

from __future__ import annotations

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect
from starlette.concurrency import run_in_threadpool

from app.api.deps import Principal, get_principal
from app.core.errors import Forbidden
from app.core.logging import bind_context
from app.relay.registry import registry
from app.schemas.bridge import BridgeResponse, CreateBridgeRequest
from app.services import bridge_service

router = APIRouter(tags=["bridge"])


@router.post("/bridges", response_model=BridgeResponse, status_code=201)
async def create_bridge(
    body: CreateBridgeRequest, principal: Principal = Depends(get_principal)
) -> BridgeResponse:
    if principal.role not in ("owner", "admin"):
        raise Forbidden("Only an owner or admin can pair a bridge.", code="forbidden")
    bridge_id, api_key = await run_in_threadpool(
        bridge_service.create_bridge, principal.org_id, body.name
    )
    return BridgeResponse(bridge_id=bridge_id, name=body.name, api_key=api_key, online=False)


@router.get("/bridge/status")
async def bridge_status(principal: Principal = Depends(get_principal)) -> dict:
    return {
        "online": registry.is_online(principal.org_id),
        "company_guid": registry.company_guid(principal.org_id),
    }


@router.websocket("/bridge/ws")
async def bridge_ws(websocket: WebSocket) -> None:
    """The bridge dials in here (WSS in prod) and authenticates with its API key (?key=...)."""
    auth = await run_in_threadpool(bridge_service.authenticate, websocket.query_params.get("key"))
    if auth is None:
        await websocket.close(code=4401)  # reject unauthenticated
        return
    org_id, bridge_id = auth
    bind_context(org_id=org_id, bridge_id=bridge_id)
    await websocket.accept()
    await registry.connect(org_id, bridge_id, websocket)
    await run_in_threadpool(bridge_service.touch_last_seen, bridge_id)
    try:
        while True:
            frame = await websocket.receive_json()
            await registry.on_frame(org_id, frame)
    except WebSocketDisconnect:
        pass
    finally:
        await registry.disconnect(org_id, websocket)
