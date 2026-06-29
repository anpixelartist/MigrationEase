"""Bridge pairing + authentication.

Bridges authenticate with an org-scoped API key (``bk_...``). We store only the key's SHA-256 (a
searchable deterministic hash — unlike passwords, API keys need lookup-by-value). The raw key is
returned exactly once at creation.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import secrets

from sqlalchemy import select

from app.db.base import new_session
from app.db.models import Bridge


def _hash_key(raw_key: str) -> str:
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()


def create_bridge(org_id: str, name: str | None) -> tuple[str, str]:
    """Create a bridge for an org. Returns (bridge_id, raw_api_key)."""
    raw_key = "bk_" + secrets.token_hex(24)
    with new_session() as session:
        bridge = Bridge(
            org_id=org_id,
            name=(name or "Bridge").strip()[:200],
            api_key_hash=_hash_key(raw_key),
            status="active",
        )
        session.add(bridge)
        session.commit()
        return bridge.id, raw_key


def authenticate(raw_key: str | None) -> tuple[str, str] | None:
    """Return (org_id, bridge_id) for a valid active key, else None."""
    if not raw_key:
        return None
    key_hash = _hash_key(raw_key)
    with new_session() as session:
        bridge = session.scalar(
            select(Bridge).where(Bridge.api_key_hash == key_hash, Bridge.status == "active")
        )
        if bridge is None:
            return None
        return bridge.org_id, bridge.id


def touch_last_seen(bridge_id: str) -> None:
    with new_session() as session:
        bridge = session.get(Bridge, bridge_id)
        if bridge is not None:
            bridge.last_seen_at = _dt.datetime.now(_dt.timezone.utc)
            session.add(bridge)
            session.commit()
