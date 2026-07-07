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


def _client_ip(request: Request, settings) -> str:
    """Real client IP for keying the limiter.

    Behind a TLS-terminating proxy, ``request.client.host`` is the proxy, so all clients share one
    bucket. When ``trust_forwarded_for`` is set, take the entry ``forwarded_for_depth`` from the RIGHT
    of X-Forwarded-For — the address the OUTERMOST trusted proxy observed. Entries further left are
    client-supplied and untrusted, so this can't be spoofed past the trusted-proxy count.
    """
    if settings.trust_forwarded_for:
        parts = [p.strip() for p in request.headers.get("x-forwarded-for", "").split(",") if p.strip()]
        depth = max(1, settings.forwarded_for_depth)
        if len(parts) >= depth:
            return parts[-depth]
    return request.client.host if request.client else "unknown"


def auth_rate_limit(request: Request) -> None:
    """FastAPI dependency: throttle password-auth endpoints per client IP."""
    settings = get_settings()
    parsed = _parse(settings.auth_rate_limit)
    if parsed is None:
        return
    limit, window = parsed
    client_ip = _client_ip(request, settings)
    if not _limiter.check(f"auth:{client_ip}", limit, window):
        raise TooManyRequests(
            "Too many authentication attempts.",
            detail="Please wait a moment before trying again.",
        )
