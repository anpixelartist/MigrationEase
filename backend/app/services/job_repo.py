"""Persistent, org-scoped job store.

Durable record (DB): org/user, status, the raw upload bytes, mapping, validation summary, resolution
verdicts, generated XML, push result. Heavy working objects (parsed DataFrame, profile, mapped df)
live in a per-process LRU cache and are rebuilt from the durable record on a cache miss — so a job
survives a restart/eviction and can continue from where it left off.

Tenant isolation: every lookup is filtered by ``org_id``; a job from another org is a 404.
"""

from __future__ import annotations

import threading
import uuid
from collections import OrderedDict
from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import select

from app.core.errors import JobNotFound
from app.core.logging import get_logger
from app.db.base import new_session, set_tenant
from app.db.models import JobRecord
from app.pipeline.contracts import ErrorEnvelope, StageName, StageResult
from app.pipeline.entities import EntityType
from app.pipeline.parsing import parse_file
from app.pipeline.profiling import profile
from app.pipeline.push.response_parser import ImportResult
from app.pipeline.resolution import ResolutionVerdict
from app.services.job_store import Job, JobStatus
from app.services.pipeline_service import build_mapped_df
from app.storage import ObjectNotFound, artifact_key, get_storage, upload_key

log = get_logger("job_repo")


def _validation_to_json(validation) -> dict | None:
    if validation is None:
        return None
    return {
        "ok": validation.ok,
        "stats": validation.stats,
        "errors": [e.model_dump() for e in validation.errors],
    }


def _validation_from_json(data: dict | None):
    if not data:
        return None
    return StageResult(
        ok=data.get("ok", False),
        stage=StageName.VALIDATE,
        data=None,
        errors=[ErrorEnvelope(**e) for e in data.get("errors", [])],
        stats=data.get("stats", {}),
    )


class DbJobStore:
    def __init__(self, max_cache: int = 500) -> None:
        self._cache: OrderedDict[str, Job] = OrderedDict()
        self._locks: dict[str, threading.Lock] = {}
        self._guard = threading.RLock()
        self._max = max_cache

    # ---- cache helpers ----
    def _cache_put(self, job: Job) -> None:
        with self._guard:
            self._cache[job.id] = job
            self._cache.move_to_end(job.id)
            self._locks.setdefault(job.id, threading.Lock())
            while len(self._cache) > self._max:
                old_id, _ = self._cache.popitem(last=False)
                self._locks.pop(old_id, None)

    # ---- create ----
    def create(self, org_id: str, user_id: str, entity_type: EntityType) -> Job:
        job_id = uuid.uuid4().hex
        with new_session() as session:
            set_tenant(session, org_id)
            session.add(
                JobRecord(
                    id=job_id,
                    org_id=org_id,
                    created_by=user_id,
                    entity_type=entity_type.value,
                    status=JobStatus.CREATED.value,
                )
            )
            session.commit()
        job = Job(id=job_id, entity_type=entity_type, org_id=org_id)
        self._cache_put(job)
        log.info("job.created", job_id=job_id, org_id=org_id, entity=entity_type.value)
        return job

    # ---- read (org-scoped) ----
    def get(self, org_id: str, job_id: str) -> Job:
        with self._guard:
            cached = self._cache.get(job_id)
            if cached is not None and cached.org_id == org_id:
                self._cache.move_to_end(job_id)
                return cached
        with new_session() as session:
            set_tenant(session, org_id)
            record = session.scalar(
                select(JobRecord).where(JobRecord.id == job_id, JobRecord.org_id == org_id)
            )
            if record is None:
                raise JobNotFound(f"No job with id '{job_id}'.")
            job = self._hydrate(record)
        self._cache_put(job)
        log.info("job.hydrated", job_id=job_id, org_id=org_id, status=job.status.value)
        return job

    @contextmanager
    def lock(self, org_id: str, job_id: str) -> Iterator[Job]:
        job = self.get(org_id, job_id)  # ensures loaded + org check
        with self._guard:
            lock = self._locks.setdefault(job_id, threading.Lock())
        with lock:
            yield job
            # persist only on a clean exit (a raising stage leaves the durable record untouched)
            self._persist(job)

    def count(self) -> int:
        with new_session() as session:
            return session.query(JobRecord).count()

    # ---- persistence ----
    def _persist(self, job: Job) -> None:
        with new_session() as session:
            if job.org_id:
                set_tenant(session, job.org_id)
            record = session.get(JobRecord, job.id)
            if record is None:  # pragma: no cover - created rows always exist
                return
            record.status = job.status.value
            record.filename = job.filename
            record.company = job.company
            storage = get_storage()
            if job.upload_bytes is not None and not record.upload_key:
                record.upload_key = storage.put(upload_key(job.org_id, job.id), job.upload_bytes)
            if job.xml is not None:
                record.xml_key = storage.put(artifact_key(job.org_id, job.id), job.xml)
            record.mapping_json = job.mapping
            record.constants_json = job.constants or None
            record.validation_json = _validation_to_json(job.validation)
            record.resolution_json = (
                [v.model_dump() for v in job.resolution] if job.resolution else None
            )
            record.push_json = (
                job.push_result.model_dump(exclude={"raw_xml"}) if job.push_result else None
            )
            record.notes_json = job.notes or None
            if job.validation is not None:
                record.error_count = int(job.validation.stats.get("error_count", 0))
            session.add(record)
            session.commit()

    def _hydrate(self, record: JobRecord) -> Job:
        job = Job(id=record.id, entity_type=EntityType(record.entity_type), org_id=record.org_id)
        job.status = JobStatus(record.status)
        job.filename = record.filename
        job.company = record.company
        job.notes = list(record.notes_json or [])
        job.constants = dict(record.constants_json or {})
        job.mapping = record.mapping_json

        storage = get_storage()
        upload_bytes = None
        if record.upload_key:
            try:
                upload_bytes = storage.get(record.upload_key)
            except ObjectNotFound:
                log.warning("hydrate.upload_missing", job_id=record.id, key=record.upload_key)
        job.upload_bytes = upload_bytes
        if record.xml_key:
            try:
                job.xml = storage.get(record.xml_key)
            except ObjectNotFound:
                job.xml = None

        if upload_bytes:
            parsed = parse_file(upload_bytes, record.filename or "upload.csv")
            if parsed.ok:
                job.df = parsed.data
                job.profile = profile(parsed.data).data
                if record.mapping_json:
                    job.mapped_df = build_mapped_df(parsed.data, record.mapping_json, record.constants_json or {})

        job.validation = _validation_from_json(record.validation_json)
        if record.resolution_json:
            job.resolution = [ResolutionVerdict(**v) for v in record.resolution_json]
        if record.push_json:
            job.push_result = ImportResult(**record.push_json)
        return job


_store: DbJobStore | None = None


def get_db_store() -> DbJobStore:
    global _store
    if _store is None:
        from app.core.config import get_settings

        _store = DbJobStore(max_cache=get_settings().max_jobs)
    return _store


def reset_store() -> None:
    """Test helper: drop the cached store (and its working-set cache)."""
    global _store
    _store = None
