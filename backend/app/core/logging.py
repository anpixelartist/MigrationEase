"""Structured logging on the stdlib (no third-party dep).

Every log line is a JSON object carrying the bound context (request_id, job_id, ...) plus any
structured fields passed to the call. Use :func:`get_logger` and the ``BoundLogger`` methods:

    log = get_logger("pipeline.parse")
    log.info("stage.start", job_id=jid, filename=name)
    log.error("stage.failed", exc_info=True, stage="parse")

Context is bound per-request via context vars so it propagates without threading it through calls.
"""

from __future__ import annotations

import datetime as _dt
import json
import logging
import sys
from contextvars import ContextVar
from typing import Any

_context: ContextVar[dict[str, Any]] = ContextVar("log_context", default={})

# Standard LogRecord attributes we never want to duplicate into the JSON payload.
_RESERVED = set(
    logging.makeLogRecord({}).__dict__.keys()
) | {"fields", "message", "asctime"}


def bind_context(**kwargs: Any) -> None:
    """Merge non-None key/values into the current logging context."""
    current = dict(_context.get())
    for key, value in kwargs.items():
        if value is not None:
            current[key] = value
    _context.set(current)


def clear_context() -> None:
    _context.set({})


def get_context() -> dict[str, Any]:
    return dict(_context.get())


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": _dt.datetime.fromtimestamp(record.created, _dt.timezone.utc).isoformat(),
            "level": record.levelname,
            "event": record.getMessage(),
            "logger": record.name,
        }
        payload.update(_context.get())
        fields = getattr(record, "fields", None)
        if isinstance(fields, dict):
            payload.update(fields)
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str, ensure_ascii=False)


class PlainFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        ctx = {**_context.get(), **(getattr(record, "fields", {}) or {})}
        suffix = " ".join(f"{k}={v}" for k, v in ctx.items())
        base = f"{record.levelname:<7} {record.name} {record.getMessage()}"
        line = f"{base}  {suffix}".rstrip()
        if record.exc_info:
            line += "\n" + self.formatException(record.exc_info)
        return line


class BoundLogger:
    """Thin wrapper giving ``log.info("event", **fields)`` ergonomics over stdlib logging."""

    def __init__(self, logger: logging.Logger) -> None:
        self._logger = logger

    def _emit(self, level: int, event: str, *, exc_info: bool = False, **fields: Any) -> None:
        if self._logger.isEnabledFor(level):
            self._logger.log(level, event, extra={"fields": fields}, exc_info=exc_info)

    def debug(self, event: str, **f: Any) -> None:
        self._emit(logging.DEBUG, event, **f)

    def info(self, event: str, **f: Any) -> None:
        self._emit(logging.INFO, event, **f)

    def warning(self, event: str, **f: Any) -> None:
        self._emit(logging.WARNING, event, **f)

    def error(self, event: str, *, exc_info: bool = False, **f: Any) -> None:
        self._emit(logging.ERROR, event, exc_info=exc_info, **f)


def configure_logging(level: str = "INFO", json_output: bool = True) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter() if json_output else PlainFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level.upper())
    # quiet noisy access logs; we emit our own request log
    logging.getLogger("uvicorn.access").handlers[:] = []
    for noisy in ("httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def get_logger(name: str) -> BoundLogger:
    return BoundLogger(logging.getLogger(name))
