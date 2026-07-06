"""Job lifecycle endpoints (auth-gated, org-scoped, persisted)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, UploadFile
from fastapi.responses import Response
from starlette.concurrency import run_in_threadpool

from app.api.deps import Principal, get_principal
from app.core.config import Settings, get_settings
from app.core.errors import InvalidEntityType, InvalidState, PayloadTooLarge
from app.pipeline.entities import EntityType
from app.schemas.jobs import (
    CreateJobRequest,
    GenerateRequest,
    JobResponse,
    MappingRequest,
    ResolveRequest,
    ValidateRequest,
)
from app.services import pipeline_service as svc
from app.services.job_repo import DbJobStore, get_db_store
from app.services.job_store import Job
from app.workers import tasks
from app.workers.broker import broker, ensure_started

router = APIRouter(prefix="/jobs", tags=["jobs"])

_UPLOAD_CHUNK = 256 * 1024


def _entity(value: str) -> EntityType:
    try:
        return EntityType(value)
    except ValueError as exc:
        raise InvalidEntityType(
            f"Unknown entity type '{value}'.", detail=f"Valid: {[e.value for e in EntityType]}"
        ) from exc


def _view(job: Job) -> JobResponse:
    rows = int(job.df.shape[0]) if job.df is not None else None
    cols = int(job.df.shape[1]) if job.df is not None else None
    return JobResponse(
        id=job.id,
        entity_type=job.entity_type.value,
        status=job.status.value,
        filename=job.filename,
        company=job.company,
        rows=rows,
        columns=cols,
        notes=job.notes,
        created_at=job.created_at.isoformat(),
        updated_at=job.updated_at.isoformat(),
    )


async def _read_capped(upload: UploadFile, limit: int) -> bytes:
    buffer = bytearray()
    while True:
        chunk = await upload.read(_UPLOAD_CHUNK)
        if not chunk:
            break
        buffer.extend(chunk)
        if len(buffer) > limit:
            raise PayloadTooLarge(f"The upload exceeds the {limit // (1024 * 1024)} MB limit.")
    return bytes(buffer)


# --------------------------------------------------------------------------------------------------
@router.post("", response_model=JobResponse, status_code=201)
async def create_job(
    body: CreateJobRequest,
    principal: Principal = Depends(get_principal),
    store: DbJobStore = Depends(get_db_store),
) -> JobResponse:
    job = store.create(principal.org_id, principal.user_id, _entity(body.entity_type))
    return _view(job)


@router.get("/{job_id}", response_model=JobResponse)
async def get_job(
    job_id: str,
    principal: Principal = Depends(get_principal),
    store: DbJobStore = Depends(get_db_store),
) -> JobResponse:
    return await run_in_threadpool(lambda: _view(store.get(principal.org_id, job_id)))


@router.post("/{job_id}/file", response_model=JobResponse)
async def upload_file(
    job_id: str,
    file: UploadFile,
    principal: Principal = Depends(get_principal),
    store: DbJobStore = Depends(get_db_store),
    settings: Settings = Depends(get_settings),
) -> JobResponse:
    content = await _read_capped(file, settings.max_upload_bytes)

    def _op() -> JobResponse:
        with store.lock(principal.org_id, job_id) as job:
            svc.ingest(job, content, file.filename, settings)
            return _view(job)

    return await run_in_threadpool(_op)


@router.get("/{job_id}/profile")
async def get_profile(
    job_id: str,
    principal: Principal = Depends(get_principal),
    store: DbJobStore = Depends(get_db_store),
) -> dict:
    def _op() -> dict:
        job = store.get(principal.org_id, job_id)
        if job.profile is None:
            raise InvalidState("Upload a file before requesting the profile.")
        return job.profile.model_dump()

    return await run_in_threadpool(_op)


@router.get("/{job_id}/mapping/suggestions")
async def mapping_suggestions(
    job_id: str,
    principal: Principal = Depends(get_principal),
    store: DbJobStore = Depends(get_db_store),
) -> dict:
    def _op() -> dict:
        with store.lock(principal.org_id, job_id) as job:
            return svc.suggest_mapping(job).model_dump()

    return await run_in_threadpool(_op)


@router.post("/{job_id}/mapping", response_model=JobResponse)
async def apply_mapping(
    job_id: str,
    body: MappingRequest,
    principal: Principal = Depends(get_principal),
    store: DbJobStore = Depends(get_db_store),
) -> JobResponse:
    def _op() -> JobResponse:
        with store.lock(principal.org_id, job_id) as job:
            svc.apply_mapping(job, body.mapping, body.constants, template=body.template)
            return _view(job)

    return await run_in_threadpool(_op)


@router.post("/{job_id}/validate", status_code=202)
async def validate_job(
    job_id: str,
    body: ValidateRequest,
    principal: Principal = Depends(get_principal),
    store: DbJobStore = Depends(get_db_store),
) -> dict:
    await run_in_threadpool(lambda: store.get(principal.org_id, job_id).require("validate"))
    await ensure_started()
    task = await tasks.validate_task.kiq(
        principal.org_id,
        job_id,
        known_groups=body.known_groups,
        known_units=body.known_units,
        known_states=body.known_states,
        known_stock_groups=body.known_stock_groups,
    )
    return {"task_id": task.task_id, "state": "pending"}


@router.get("/{job_id}/tasks/{task_id}")
async def task_status(
    job_id: str,
    task_id: str,
    principal: Principal = Depends(get_principal),
    store: DbJobStore = Depends(get_db_store),
) -> dict:
    await run_in_threadpool(lambda: store.get(principal.org_id, job_id))  # org check / 404
    await ensure_started()
    backend = broker.result_backend
    if not await backend.is_result_ready(task_id):
        return {"state": "pending"}
    result = await backend.get_result(task_id)
    if result.is_err:
        return {
            "state": "error",
            "problem": {
                "type": "about:blank",
                "title": "Task failed",
                "status": 500,
                "code": "task_failed",
                "detail": "The background task raised an unexpected error.",
                "errors": [],
            },
        }
    return result.return_value


@router.get("/{job_id}/validation")
async def get_validation(
    job_id: str,
    principal: Principal = Depends(get_principal),
    store: DbJobStore = Depends(get_db_store),
) -> dict:
    def _op() -> dict:
        job = store.get(principal.org_id, job_id)
        if job.validation is None:
            raise InvalidState("Run validation first.")
        return {
            "ok": job.validation.ok,
            "stats": job.validation.stats,
            "errors": [e.model_dump() for e in job.validation.errors],
        }

    return await run_in_threadpool(_op)


@router.get("/{job_id}/vouchers/preview")
async def voucher_preview(
    job_id: str,
    principal: Principal = Depends(get_principal),
    store: DbJobStore = Depends(get_db_store),
) -> dict:
    """Group the mapped rows into vouchers for the wizard's multi-line review screen."""
    def _op() -> dict:
        from app.pipeline.voucher import rows_to_vouchers

        job = store.get(principal.org_id, job_id)
        if job.entity_type != EntityType.VOUCHER:
            raise InvalidState("Voucher preview is only available for voucher imports.")
        if job.mapped_df is None:
            raise InvalidState("Map columns before previewing vouchers.")
        vouchers, errors = rows_to_vouchers(job.mapped_df)
        return {
            "count": len(vouchers),
            "error_count": sum(1 for e in errors if e.severity == "error"),
            "warning_count": sum(1 for e in errors if e.severity == "warning"),
            "vouchers": [
                {
                    "voucher_number": v.reference,
                    "date": v.date.isoformat(),
                    "voucher_type": v.voucher_type,
                    "party_ledger": v.party_ledger,
                    "debit_total": str(v.debit_total),
                    "credit_total": str(v.credit_total),
                    "balanced": v.is_balanced,
                    "lines": [
                        {
                            "ledger_name": ln.ledger_name,
                            "dr_cr": "Dr" if ln.is_debit else "Cr",
                            "amount": str(ln.amount),
                            "stock_item": ln.stock_item,
                            "quantity": str(ln.quantity) if ln.quantity is not None else None,
                            "unit": ln.unit,
                        }
                        for ln in v.lines
                    ],
                }
                for v in vouchers[:100]
            ],
        }

    return await run_in_threadpool(_op)


@router.post("/{job_id}/resolve")
async def resolve_job(
    job_id: str,
    body: ResolveRequest,
    principal: Principal = Depends(get_principal),
    store: DbJobStore = Depends(get_db_store),
) -> dict:
    def _op() -> dict:
        with store.lock(principal.org_id, job_id) as job:
            verdicts = svc.run_resolution(job, body.existing)
            buckets: dict[str, int] = {}
            for v in verdicts:
                buckets[v.decision] = buckets.get(v.decision, 0) + 1
            return {"buckets": buckets, "verdicts": [v.model_dump() for v in verdicts]}

    return await run_in_threadpool(_op)


@router.post("/{job_id}/generate", status_code=202)
async def generate_job(
    job_id: str,
    body: GenerateRequest,
    principal: Principal = Depends(get_principal),
    store: DbJobStore = Depends(get_db_store),
) -> dict:
    await run_in_threadpool(lambda: store.get(principal.org_id, job_id).require("generate"))
    await ensure_started()
    task = await tasks.generate_task.kiq(principal.org_id, job_id, company=body.company, cutover_date=body.cutover_date)
    return {"task_id": task.task_id, "state": "pending"}


@router.get("/{job_id}/artifact")
async def get_artifact(
    job_id: str,
    principal: Principal = Depends(get_principal),
    store: DbJobStore = Depends(get_db_store),
) -> Response:
    import datetime

    def _op() -> Response:
        job = store.get(principal.org_id, job_id)
        if job.xml is None:
            raise InvalidState("Generate the XML before downloading it.")

        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M")
        company_slug = "".join(c if c.isalnum() else "_" for c in (job.company or "Company"))
        filename = f"{company_slug}_{job.entity_type.value.capitalize()}_{timestamp}.xml"

        return Response(
            content=job.xml,
            media_type="application/xml",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    return await run_in_threadpool(_op)


@router.post("/{job_id}/push", status_code=202)
async def push_job(
    job_id: str,
    principal: Principal = Depends(get_principal),
    store: DbJobStore = Depends(get_db_store),
) -> dict:
    # Fast, advisory pre-check (the task's claim under the job lock is the real guard).
    await run_in_threadpool(lambda: svc.ensure_pushable(store.get(principal.org_id, job_id)))
    await ensure_started()
    task = await tasks.push_task.kiq(principal.org_id, job_id)
    return {"task_id": task.task_id, "state": "pending"}
