"""In-memory job store + the job state machine.

A ``Job`` accumulates the artefacts of one migration (parsed DataFrame, profile, mapping, validation,
resolution, generated XML, push result). The store is a process-local, thread-safe LRU map — fine for
a single-process dev/MVP backend and deliberately behind a small surface so a Postgres-backed store
can replace it without touching callers.

State machine (forward-only, with re-runs allowed where it's safe):

    CREATED --upload--> PARSED --mapping--> MAPPED --validate--> VALIDATED
        --resolve(optional)--> RESOLVED --generate--> GENERATED --push--> PUSHED
"""

from __future__ import annotations

import datetime as _dt
import threading
import uuid
from collections import OrderedDict
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from app.core.errors import InvalidState, JobNotFound
from app.pipeline.entities import EntityType


class JobStatus(str, Enum):
    CREATED = "created"
    PARSED = "parsed"
    MAPPED = "mapped"
    VALIDATED = "validated"
    RESOLVED = "resolved"
    GENERATED = "generated"
    PUSHED = "pushed"


# Which statuses each action may run from.
_ALLOWED: dict[str, set[JobStatus]] = {
    "upload": set(JobStatus),  # re-upload allowed from any state (resets downstream)
    "map": {JobStatus.PARSED, JobStatus.MAPPED, JobStatus.VALIDATED, JobStatus.RESOLVED},
    "validate": {JobStatus.MAPPED, JobStatus.VALIDATED, JobStatus.RESOLVED},
    "resolve": {JobStatus.VALIDATED, JobStatus.RESOLVED, JobStatus.GENERATED},
    "generate": {JobStatus.VALIDATED, JobStatus.RESOLVED, JobStatus.GENERATED},
    "push": {JobStatus.GENERATED, JobStatus.PUSHED},
}


def _now() -> _dt.datetime:
    return _dt.datetime.now(_dt.timezone.utc)


@dataclass
class Job:
    id: str
    entity_type: EntityType
    status: JobStatus = JobStatus.CREATED
    created_at: _dt.datetime = field(default_factory=_now)
    updated_at: _dt.datetime = field(default_factory=_now)

    org_id: str | None = None  # tenant owner (set by the persistent store)
    filename: str | None = None
    company: str | None = None
    upload_bytes: bytes | None = None  # raw upload, kept so state can be rebuilt on a cache miss

    # stage payloads (not serialized; held in-process)
    df: Any = None  # parsed pandas DataFrame (all-string)
    profile: Any = None  # ProfileSignals
    mapping: dict[str, str | None] | None = None  # target -> source
    constants: dict[str, str] = field(default_factory=dict)
    mapped_df: Any = None
    validation: Any = None  # StageResult[pd.DataFrame]
    resolution: Any = None  # list[ResolutionVerdict]
    xml: bytes | None = None
    push_result: Any = None  # ImportResult
    notes: list[str] = field(default_factory=list)

    def require(self, action: str) -> None:
        allowed = _ALLOWED[action]
        if self.status not in allowed:
            raise InvalidState(
                f"Cannot '{action}' while job is '{self.status.value}'.",
                detail=f"Allowed from: {sorted(s.value for s in allowed)}.",
            )

    def advance(self, status: JobStatus) -> None:
        self.status = status
        self.updated_at = _now()


class InMemoryJobStore:
    def __init__(self, max_jobs: int = 500) -> None:
        self._jobs: OrderedDict[str, Job] = OrderedDict()
        self._locks: dict[str, threading.Lock] = {}
        self._guard = threading.RLock()
        self._max_jobs = max_jobs

    def create(self, entity_type: EntityType) -> Job:
        with self._guard:
            if len(self._jobs) >= self._max_jobs:
                # evict the least-recently-used job (and its lock)
                old_id, _ = self._jobs.popitem(last=False)
                self._locks.pop(old_id, None)
            job = Job(id=uuid.uuid4().hex, entity_type=entity_type)
            self._jobs[job.id] = job
            self._locks[job.id] = threading.Lock()
            return job

    def get(self, job_id: str) -> Job:
        with self._guard:
            job = self._jobs.get(job_id)
            if job is None:
                raise JobNotFound(f"No job with id '{job_id}'.", code="job_not_found")
            self._jobs.move_to_end(job_id)  # LRU touch
            return job

    @contextmanager
    def lock(self, job_id: str) -> Iterator[Job]:
        """Acquire the per-job lock and yield the job (serializes mutations of one job)."""
        with self._guard:
            job = self._jobs.get(job_id)
            if job is None:
                raise JobNotFound(f"No job with id '{job_id}'.", code="job_not_found")
            lock = self._locks.setdefault(job_id, threading.Lock())
            self._jobs.move_to_end(job_id)
        with lock:
            yield job

    def count(self) -> int:
        with self._guard:
            return len(self._jobs)


# module-level singleton (swap for a DI-provided store later)
_store: InMemoryJobStore | None = None


def get_store() -> InMemoryJobStore:
    global _store
    if _store is None:
        from app.core.config import get_settings

        _store = InMemoryJobStore(max_jobs=get_settings().max_jobs)
    return _store
