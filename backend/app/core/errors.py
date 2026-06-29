"""Unified error model + global exception handlers (RFC 7807 problem+json).

Every failure path raises an :class:`AppError` (or a subclass) which carries an HTTP status, a stable
``code``, an optional human ``detail``, and an optional list of :class:`ErrorEnvelope` (the same shape
the pipeline produces). Unhandled exceptions are logged with a traceback and returned as a generic
500 that never leaks internals.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.core.logging import get_logger
from app.pipeline.contracts import ErrorCode, ErrorEnvelope

_PROBLEM_MEDIA = "application/problem+json"


class AppError(Exception):
    status_code: int = 500
    code: str = ErrorCode.VALIDATION_ERROR

    def __init__(
        self,
        message: str,
        *,
        detail: str | None = None,
        errors: list[ErrorEnvelope] | None = None,
        status_code: int | None = None,
        code: str | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.detail = detail
        self.errors = errors or []
        if status_code is not None:
            self.status_code = status_code
        if code is not None:
            self.code = code

    def to_problem(self) -> dict[str, Any]:
        return {
            "type": "about:blank",
            "title": self.message,
            "status": self.status_code,
            "code": self.code,
            "detail": self.detail,
            "errors": [e.model_dump() for e in self.errors],
        }


class Unauthorized(AppError):
    status_code = 401
    code = "unauthorized"


class Forbidden(AppError):
    status_code = 403
    code = "forbidden"


class Conflict(AppError):
    status_code = 409
    code = "conflict"


class JobNotFound(AppError):
    status_code = 404
    code = "job_not_found"


class InvalidEntityType(AppError):
    status_code = 422
    code = "invalid_entity_type"


class InvalidState(AppError):
    """The requested step cannot run from the job's current status."""

    status_code = 409
    code = "invalid_state"


class BadRequest(AppError):
    status_code = 400
    code = "bad_request"


class PayloadTooLarge(AppError):
    status_code = 413
    code = ErrorCode.FILE_TOO_LARGE


class UnsupportedMedia(AppError):
    status_code = 415
    code = ErrorCode.UNSUPPORTED_FORMAT


class UnprocessableData(AppError):
    """A stage rejected the data; ``errors`` carries the per-row/column envelopes."""

    status_code = 422
    code = "unprocessable_data"


class TooManyJobs(AppError):
    status_code = 429
    code = "too_many_jobs"


class ServiceUnavailable(AppError):
    status_code = 503
    code = "service_unavailable"


def register_exception_handlers(app: FastAPI) -> None:
    log = get_logger("api.error")

    @app.exception_handler(AppError)
    async def _app_error(request: Request, exc: AppError) -> JSONResponse:
        log.warning(
            "app_error",
            code=exc.code,
            status=exc.status_code,
            path=request.url.path,
            method=request.method,
            detail=exc.detail,
            error_count=len(exc.errors),
        )
        return JSONResponse(exc.to_problem(), status_code=exc.status_code, media_type=_PROBLEM_MEDIA)

    @app.exception_handler(RequestValidationError)
    async def _request_validation(request: Request, exc: RequestValidationError) -> JSONResponse:
        log.warning("request_validation_error", path=request.url.path, method=request.method)
        problem = {
            "type": "about:blank",
            "title": "Request validation failed",
            "status": 422,
            "code": "request_validation_error",
            "detail": "One or more request fields are invalid.",
            "errors": exc.errors(),
        }
        return JSONResponse(problem, status_code=422, media_type=_PROBLEM_MEDIA)

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        # Log everything (with traceback) but never leak internals to the client.
        log.error(
            "unhandled_exception",
            exc_info=True,
            path=request.url.path,
            method=request.method,
            exc_type=type(exc).__name__,
        )
        problem = {
            "type": "about:blank",
            "title": "Internal Server Error",
            "status": 500,
            "code": "internal_error",
            "detail": "An unexpected error occurred. The incident has been logged.",
            "errors": [],
        }
        return JSONResponse(problem, status_code=500, media_type=_PROBLEM_MEDIA)
