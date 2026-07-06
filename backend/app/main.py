"""FastAPI application factory.

Wires structured logging, a request-id/logging + body-size middleware, CORS, the global problem+json
exception handlers, and the job-lifecycle routers.
"""

from __future__ import annotations

import time
import uuid

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.core.config import get_settings
from app.core.errors import register_exception_handlers
from app.core.logging import bind_context, clear_context, configure_logging, get_logger
from app.db.base import init_db
from app.api.routers import auth, bridge, health, jobs

_PROBLEM_MEDIA = "application/problem+json"


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(level=settings.log_level, json_output=settings.log_json)
    log = get_logger("api.request")

    if settings.sentry_dsn:
        import sentry_sdk

        sentry_sdk.init(
            dsn=settings.sentry_dsn,
            environment=settings.environment,
            traces_sample_rate=0.1,
            send_default_pii=False,  # tenant data must never leave the system
        )

    init_db()  # create tables for dev (SQLite); prod uses Alembic migrations

    # Cross-process bridge relay: every API replica answers Redis dispatch requests for the
    # bridge sockets IT holds (no-op without a Redis broker).
    from contextlib import asynccontextmanager

    from app.relay import redis_relay

    @asynccontextmanager
    async def _lifespan(_: FastAPI):
        await redis_relay.start_responder()
        yield
        await redis_relay.stop_responder()

    app = FastAPI(title=settings.app_name, version="0.1.0", lifespan=_lifespan)

    # The API is bearer-token (no cookies), so credentialed CORS is only enabled when explicit
    # origins are configured — "*" + credentials is an invalid, insecure combination.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=settings.cors_origins != ["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    if settings.metrics_enabled:
        from prometheus_fastapi_instrumentator import Instrumentator

        Instrumentator(excluded_handlers=["/metrics", "/healthz"]).instrument(app).expose(
            app, endpoint="/metrics", include_in_schema=False
        )

    @app.middleware("http")
    async def context_and_logging(request: Request, call_next):
        request_id = request.headers.get("x-request-id") or uuid.uuid4().hex
        clear_context()
        bind_context(request_id=request_id)

        # cheap body-size guard before reading the body
        content_length = request.headers.get("content-length")
        if content_length:
            try:
                if int(content_length) > settings.max_upload_bytes:
                    return JSONResponse(
                        {
                            "type": "about:blank",
                            "title": "Payload too large",
                            "status": 413,
                            "code": "file_too_large",
                            "detail": f"Body exceeds {settings.max_upload_bytes} bytes.",
                            "errors": [],
                        },
                        status_code=413,
                        media_type=_PROBLEM_MEDIA,
                    )
            except ValueError:
                pass

        start = time.perf_counter()
        try:
            response = await call_next(request)
        finally:
            duration_ms = round((time.perf_counter() - start) * 1000, 1)
        status = response.status_code
        event = "request"
        if status >= 500:
            log.error(event, method=request.method, path=request.url.path, status=status, duration_ms=duration_ms)
        else:
            log.info(event, method=request.method, path=request.url.path, status=status, duration_ms=duration_ms)
        response.headers["x-request-id"] = request_id
        clear_context()
        return response

    register_exception_handlers(app)
    app.include_router(health.router)
    app.include_router(auth.router)
    app.include_router(bridge.router)
    app.include_router(jobs.router)

    log.info("app.started", environment=settings.environment, direct_push=settings.direct_tally_push)
    return app


app = create_app()
