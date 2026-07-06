"""Minimal in-process sliding-window rate limiter for the password auth endpoints.

Keyed by client IP; configured via ``TM_AUTH_RATE_LIMIT`` ("N/second|minute|hour", empty =
disabled). Per-process state is acceptable here: the limit exists to slow credential stuffing on
/auth/login|signup, and Keycloak's own brute-force protection covers the OIDC path. Multi-replica
deployments that need a shared budget should put the limit at the ingress/LB as well.
"""

from __future__ import annotations

import re
import threading
import time
from collections import defaultdict, deque

from fastapi import Request

from app.core.config import get_settings
from app.core.errors import TooManyRequests

_PERIODS = {"second": 1, "minute": 60, "hour": 3600}
_SPEC_RE = re.compile(r"^\s*(\d+)\s*/\s*(second|minute|hour)\s*$")


def _parse(spec: str) -> tuple[int, int] | None:
    m = _SPEC_RE.match(spec or "")
    if not m:
        return None
    return int(m.group(1)), _PERIODS[m.group(2)]


class SlidingWindowLimiter:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def check(self, key: str, limit: int, window_seconds: int) -> bool:
        """Record a hit for ``key``; True if within budget, False if over the limit."""
        now = time.monotonic()
        with self._lock:
            hits = self._hits[key]
            while hits and now - hits[0] > window_seconds:
                hits.popleft()
            if len(hits) >= limit:
                return False
            hits.append(now)
            return True


_limiter = SlidingWindowLimiter()


def auth_rate_limit(request: Request) -> None:
    """FastAPI dependency: throttle password-auth endpoints per client IP."""
    parsed = _parse(get_settings().auth_rate_limit)
    if parsed is None:
        return
    limit, window = parsed
    client_ip = request.client.host if request.client else "unknown"
    if not _limiter.check(f"auth:{client_ip}", limit, window):
        raise TooManyRequests(
            "Too many authentication attempts.",
            detail="Please wait a moment before trying again.",
        )
