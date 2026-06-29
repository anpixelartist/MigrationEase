"""taskiq broker: in-process InMemoryBroker for dev/tests, RedisStreamBroker (acks) for prod.

The broker is created once at import from settings (``TM_BROKER_URL``). ``ensure_started`` performs
the idempotent startup so the API works whether or not a lifespan hook ran (e.g. under TestClient).
"""

from __future__ import annotations

import asyncio

from taskiq import AsyncBroker, InMemoryBroker

from app.core.config import get_settings


def _build() -> AsyncBroker:
    settings = get_settings()
    if settings.broker_url.startswith("redis"):
        from taskiq_redis import RedisAsyncResultBackend, RedisStreamBroker

        result_backend = RedisAsyncResultBackend(settings.broker_result_url or settings.broker_url)
        return RedisStreamBroker(settings.broker_url).with_result_backend(result_backend)
    # dev/tests: run the task inline on kiq (no separate worker). Prod uses Redis -> truly off-path.
    return InMemoryBroker(await_inplace=True)


broker: AsyncBroker = _build()

_started = False
_start_lock = asyncio.Lock()


async def ensure_started() -> None:
    global _started
    if _started:
        return
    async with _start_lock:
        if not _started:
            await broker.startup()
            _started = True
