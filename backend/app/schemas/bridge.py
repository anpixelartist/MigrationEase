"""Bridge API schemas."""

from __future__ import annotations

from pydantic import BaseModel


class CreateBridgeRequest(BaseModel):
    name: str | None = None


class BridgeResponse(BaseModel):
    bridge_id: str
    name: str | None = None
    api_key: str  # shown once
    online: bool = False
