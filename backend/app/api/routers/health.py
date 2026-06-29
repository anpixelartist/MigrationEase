"""Liveness / readiness."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from starlette.concurrency import run_in_threadpool

from app.services.job_repo import DbJobStore, get_db_store

router = APIRouter(tags=["health"])


@router.get("/healthz")
async def healthz(store: DbJobStore = Depends(get_db_store)) -> dict[str, object]:
    jobs = await run_in_threadpool(store.count)
    return {"status": "ok", "jobs": jobs}
