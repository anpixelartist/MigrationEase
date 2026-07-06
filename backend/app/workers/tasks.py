"""Background tasks for the long pipeline stages (validate / generate / push).

Each task hydrates the job from the durable store (so it works in a separate worker process that
doesn't share the API's working cache), runs the synchronous stage in a thread (off the worker's
event loop), persists via the store lock, and returns a JSON-serializable envelope:

    {"state": "done",  "result": {...stage summary...}}
    {"state": "error", "problem": {...RFC7807...}}      # a handled AppError

Unexpected exceptions propagate and surface as a task error in the result backend.
"""

from __future__ import annotations

import asyncio
from typing import Any

from app.core.config import get_settings
from app.core.errors import AppError, ServiceUnavailable, UnprocessableData
from app.pipeline.push.response_parser import XMLSecurityError, parse_import_response
from app.relay import redis_relay
from app.relay.registry import BridgeNotConnected, BridgeOffline
from app.services import pipeline_service as svc
from app.services.job_repo import get_db_store
from app.services.job_store import JobStatus
from app.workers.broker import broker
from app.db.base import new_session
from app.db.models import StagedRecord
from sqlalchemy import delete
import datetime as _dt


def _done(result: dict[str, Any]) -> dict[str, Any]:
    return {"state": "done", "result": result}


def _error(exc: AppError) -> dict[str, Any]:
    return {"state": "error", "problem": exc.to_problem()}


@broker.task
async def validate_task(
    org_id: str,
    job_id: str,
    known_groups: list[str] | None = None,
    known_units: list[str] | None = None,
    known_states: list[str] | None = None,
    known_stock_groups: list[str] | None = None,
) -> dict[str, Any]:
    def _run() -> dict[str, Any]:
        try:
            with get_db_store().lock(org_id, job_id) as job:
                result = svc.run_validation(
                    job,
                    known_groups=known_groups,
                    known_units=known_units,
                    known_states=known_states,
                    known_stock_groups=known_stock_groups,
                )
                return _done(
                    {
                        "ok": result.ok,
                        "stats": result.stats,
                        "errors": [e.model_dump() for e in result.errors],
                    }
                )
        except AppError as exc:
            return _error(exc)

    return await asyncio.to_thread(_run)


@broker.task
async def generate_task(org_id: str, job_id: str, company: str | None = None, cutover_date: str | None = None) -> dict[str, Any]:
    def _run() -> dict[str, Any]:
        try:
            with get_db_store().lock(org_id, job_id) as job:
                summary = svc.run_generation(job, company, cutover_date)
                return _done(
                    {
                        "status": job.status.value,
                        "generated": summary["generated"],
                        "held_conflicts": summary["held"],
                        "skipped_rows": summary["skipped"],
                        "bytes": summary["bytes"],
                        "debit_total": summary.get("debit_total"),
                        "credit_total": summary.get("credit_total"),
                        "convert_errors": [e.model_dump() for e in summary["errors"]],
                    }
                )
        except AppError as exc:
            return _error(exc)

    return await asyncio.to_thread(_run)


@broker.task
async def push_task(org_id: str, job_id: str) -> dict[str, Any]:
    """Push the generated XML into Tally — idempotently.

    Sequence: (1) atomically CLAIM the push under the job lock (concurrent/duplicate pushes get
    409 — a double import would duplicate accounting entries); (2) send — direct HTTP in dev, or
    via the bridge relay (local socket or Redis pub/sub to the replica that holds it); (3) record
    Tally's actual verdict, which decides PUSHED / PUSHED_PARTIAL / PUSH_FAILED.
    """
    settings = get_settings()
    store = get_db_store()

    def _claim() -> tuple[bytes, str | None]:
        with store.lock(org_id, job_id) as job:
            svc.claim_push(job)
            assert job.xml is not None  # guaranteed by claim_push
            return job.xml, job.company

    def _record(result: Any) -> str:
        with store.lock(org_id, job_id) as job:
            return svc.record_push_result(job, result).value

    def _record_failure(reason: str) -> None:
        with store.lock(org_id, job_id) as job:
            svc.record_push_failure(job, reason)

    def _release_claim() -> None:
        # nothing was ever sent -> safe to hand the job back untouched
        with store.lock(org_id, job_id) as job:
            job.advance(JobStatus.GENERATED)

    try:
        xml, company = await asyncio.to_thread(_claim)
    except AppError as exc:
        return _error(exc)

    # dev: direct push to a local Tally gateway (sync urllib in a thread)
    if settings.direct_tally_push:
        def _run_direct() -> dict[str, Any]:
            try:
                with store.lock(org_id, job_id) as job:
                    result = svc.run_push(job, settings)
                    status = svc.record_push_result(job, result)
                    return _done({"status": status.value, **result.model_dump(exclude={"raw_xml"})})
            except ServiceUnavailable as exc:  # gateway unreachable — nothing was imported
                with store.lock(org_id, job_id) as job:
                    svc.record_push_failure(job, exc.message)
                return _error(exc)
            except AppError as exc:
                with store.lock(org_id, job_id) as job:
                    svc.record_push_failure(job, exc.message)
                return _error(exc)

        return await asyncio.to_thread(_run_direct)

    # prod: relay the XML to the org's bridge (this process, or another replica via Redis)
    try:
        response = await redis_relay.dispatch(org_id, job_id, xml, company)
    except BridgeNotConnected as exc:
        await asyncio.to_thread(_release_claim)  # pre-send failure: retry is always safe
        return _error(
            ServiceUnavailable(
                "No bridge is connected for this organization.",
                code="bridge_unavailable",
                detail=str(exc),
            )
        )
    except BridgeOffline as exc:
        # The XML may have reached Tally before the transport died — mark PUSH_FAILED with a
        # verify-first warning instead of pretending nothing happened.
        await asyncio.to_thread(_record_failure, str(exc))
        return _error(
            ServiceUnavailable(
                "The bridge did not return a result for this push.",
                code="bridge_no_result",
                detail=f"{exc} — verify in Tally whether the import happened before re-pushing.",
            )
        )

    try:
        result = parse_import_response(response)
    except XMLSecurityError as exc:
        await asyncio.to_thread(_record_failure, f"unparseable Tally response: {exc}")
        return _error(
            UnprocessableData(
                "Tally returned a response that could not be parsed.",
                code="bad_tally_response",
                detail=str(exc),
            )
        )

    status = await asyncio.to_thread(_record, result)
    return _done({"status": status, **result.model_dump(exclude={"raw_xml"})})


@broker.task(schedule=[{"cron": "0 3 * * *"}])  # runs daily at 3am
async def cleanup_staged_records_task() -> dict[str, Any]:
    """Cron task enforcing the DPDP 30-day auto-erasure policy for staging data."""
    def _run() -> dict[str, Any]:
        cutoff_date = _dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(days=30)
        try:
            with new_session() as db:
                stmt = delete(StagedRecord).where(StagedRecord.created_at < cutoff_date)
                result = db.execute(stmt)
                db.commit()
                deleted_count = result.rowcount
                return _done({"deleted_staged_records": deleted_count})
        except Exception as exc:
            return {"state": "error", "problem": str(exc)}

    return await asyncio.to_thread(_run)
