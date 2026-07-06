"""Pipeline orchestration — runs each stage over a Job, with logging + structured error capture.

Callers must hold the job lock (see ``InMemoryJobStore.lock``). Every function enforces the state
machine, logs start/outcome with the job id and counts, and converts stage failures into AppErrors
(problem+json) rather than letting exceptions escape.
"""

from __future__ import annotations

import re
import urllib.error
import urllib.request
from typing import Any

import pandas as pd

from app.core.config import Settings
from app.core.errors import (
    BadRequest,
    InvalidState,
    ServiceUnavailable,
    UnprocessableData,
)
from app.core.logging import get_logger
from app.pipeline.contracts import ErrorCode
from app.pipeline.conversion import build_and_serialize
from app.pipeline.conversion.voucher_builder import build_and_serialize_vouchers
from app.pipeline.coercion import row_to_master
from app.pipeline.entities import EntityType, Group, Ledger, StockItem, Unit
from app.pipeline.voucher import rows_to_vouchers
from app.pipeline.mapping import MappingProposal, auto_map
from app.pipeline.mapping.catalog import load_catalog
from app.pipeline.mapping.templates import apply_template
from app.pipeline.parsing import parse_file
from app.pipeline.profiling import profile
from app.pipeline.push.response_parser import XMLSecurityError, parse_import_response
from app.pipeline.resolution import resolve
from app.pipeline.validation import validate
from app.services.job_store import Job, JobStatus

log = get_logger("pipeline")

_UNNAMED = re.compile(r"^Unnamed: \d+$")


def _norm(value: Any) -> str:
    return " ".join(str(value or "").strip().casefold().split())


def build_mapped_df(df: pd.DataFrame, mapping: dict[str, str | None] | None,
                    constants: dict[str, str] | None) -> pd.DataFrame:
    """Build the canonical-keyed DataFrame from a source df + accepted mapping + constants."""
    clean = {t: s for t, s in (mapping or {}).items() if s and s in df.columns}
    data: dict[str, list[Any]] = {t: df[s].tolist() for t, s in clean.items()}
    n = len(df)
    for target, value in (constants or {}).items():
        data[target] = [value] * n
    return pd.DataFrame(data)


def _reset_from(job: Job, *, mapping: bool = False) -> None:
    """Clear downstream artefacts when an upstream stage re-runs.

    Always clears validation/resolution/xml/push (downstream of mapping). When ``mapping=True``
    (used on re-upload) it also clears the mapping itself and the mapped DataFrame.
    """
    if mapping:
        job.mapping = None
        job.mapped_df = None
    job.validation = None
    job.resolution = None
    job.xml = None
    job.push_result = None


# --------------------------------------------------------------------------------------------------
def ingest(job: Job, content: bytes, filename: str | None, settings: Settings) -> None:
    job.require("upload")
    result = parse_file(content, filename or "upload.csv", max_rows=settings.max_rows)
    if not result.ok:
        code = result.errors[0].code if result.errors else ErrorCode.PARSE_FAILED
        log.warning("parse.failed", job_id=job.id, code=code)
        raise UnprocessableData("The file could not be read.", errors=result.errors, code=code)

    df = result.data
    if df.shape[1] > settings.max_columns:
        raise UnprocessableData(
            f"The file has {df.shape[1]} columns, exceeding the {settings.max_columns} limit.",
            code="too_many_columns",
        )

    notes: list[str] = []
    dropped = result.stats.get("dropped_columns") or []
    if dropped:
        notes.append(f"{len(dropped)} empty column(s) ignored: {', '.join(dropped[:6])}")
    unnamed = [c for c in df.columns if _UNNAMED.match(str(c))]
    if unnamed:
        notes.append(f"{len(unnamed)} unnamed column(s) detected")
    if len(set(df.columns)) != len(df.columns):
        notes.append("duplicate column headers detected")

    job.df = df
    job.profile = profile(df).data
    job.filename = filename
    job.upload_bytes = content  # kept durable so working state can be rebuilt on a cache miss
    job.notes = notes
    _reset_from(job, mapping=True)
    job.advance(JobStatus.PARSED)
    log.info("parse.ok", job_id=job.id, rows=int(df.shape[0]), columns=int(df.shape[1]), notes=notes)


def suggest_mapping(job: Job) -> MappingProposal:
    if job.df is None:
        raise InvalidState("Upload a file before requesting mapping suggestions.")
    proposal = auto_map(job.df, job.entity_type).data
    log.info(
        "map.suggested",
        job_id=job.id,
        unmapped_required=proposal.unmapped_required,
        sources=len(job.df.columns),
    )
    return proposal


def apply_mapping(job: Job, mapping: dict[str, str | None], constants: dict[str, str], template: str | None = None) -> None:
    job.require("map")
    if job.df is None:
        raise InvalidState("Upload a file before mapping.")

    if template:
        try:
            tmpl_mapping, tmpl_constants = apply_template(template)
            mapping = {**tmpl_mapping, **(mapping or {})}
            constants = {**tmpl_constants, **(constants or {})}
        except ValueError as exc:
            raise BadRequest(str(exc), code="invalid_template")

    clean = {t: s for t, s in (mapping or {}).items() if s}
    unknown = sorted({s for s in clean.values() if s not in job.df.columns})
    if unknown:
        raise BadRequest("Some mapped source columns are not in the file.", detail=f"Unknown: {unknown}")

    catalog = load_catalog(job.entity_type)
    valid_targets = {f.key for f in catalog.fields} | {"opening_balance_drcr", "guid", "old_name"}
    # check column-mapped AND constant targets — a constant on an unknown field would silently vanish
    bad_targets = sorted((set(clean) | set(constants or {})) - valid_targets)
    if bad_targets:
        raise BadRequest(f"Unknown target field(s) for {job.entity_type.value}.", detail=f"{bad_targets}")

    required = [f.key for f in catalog.fields if f.required]
    mapped_targets = set(clean) | set(constants or {})
    missing = [r for r in required if r not in mapped_targets]
    if missing:
        raise UnprocessableData(
            "Required fields are not mapped.", code="missing_required_mapping", detail=f"Map: {missing}"
        )

    job.mapping = mapping
    job.constants = constants or {}
    job.mapped_df = build_mapped_df(job.df, mapping, constants)
    _reset_from(job)  # clear downstream (validation/resolution/xml), keep the new mapped_df
    job.advance(JobStatus.MAPPED)
    log.info("map.applied", job_id=job.id, mapped_fields=len(clean), constants=len(constants or {}))


def run_validation(
    job: Job,
    *,
    known_groups: list[str] | None = None,
    known_units: list[str] | None = None,
    known_states: list[str] | None = None,
    known_stock_groups: list[str] | None = None,
) -> Any:
    job.require("validate")
    if job.mapped_df is None:
        raise InvalidState("Map columns before validating.")
    result = validate(
        job.mapped_df,
        job.entity_type,
        known_groups=known_groups,
        known_units=known_units,
        known_states=known_states,
        known_stock_groups=known_stock_groups,
    )
    job.validation = result
    job.resolution = None
    job.xml = None
    job.push_result = None
    job.advance(JobStatus.VALIDATED)
    log.info(
        "validate.done",
        job_id=job.id,
        ok=result.ok,
        errors=result.stats.get("error_count", 0),
        warnings=result.stats.get("warning_count", 0),
    )
    return result


def run_resolution(job: Job, existing: list[dict[str, Any]] | None) -> Any:
    job.require("resolve")
    if job.entity_type == EntityType.VOUCHER:
        raise BadRequest(
            "Resolution (create-vs-update matching) applies to masters, not vouchers.",
            code="resolution_not_applicable",
        )
    if job.mapped_df is None:
        raise InvalidState("Map and validate before resolving.")
    incoming = [
        {"name": row.get("name"), "parent": row.get("parent"), "source_row": idx}
        for idx, row in enumerate(job.mapped_df.to_dict("records"), start=1)
    ]
    verdicts = resolve(incoming, existing or [])
    job.resolution = verdicts
    job.xml = None
    job.push_result = None
    job.advance(JobStatus.RESOLVED)
    buckets: dict[str, int] = {}
    for v in verdicts:
        buckets[v.decision] = buckets.get(v.decision, 0) + 1
    log.info("resolve.done", job_id=job.id, **buckets)
    return verdicts


def run_generation(job: Job, company: str | None) -> dict[str, Any]:
    job.require("generate")
    if job.mapped_df is None or job.validation is None:
        raise InvalidState("Map and validate before generating.")

    blocking = [e for e in job.validation.errors if e.severity == "error"]
    if blocking:
        raise UnprocessableData(
            "Resolve validation errors before generating XML.", errors=blocking, code="validation_failed"
        )

    if job.entity_type == EntityType.VOUCHER:
        return _generate_vouchers(job, company)

    verdict_by_row = {v.source_row: v for v in (job.resolution or [])}
    units: list[Unit] = []
    groups: list[Group] = []
    items: list[StockItem] = []
    ledgers: list[Ledger] = []
    conv_errors = []
    held = skipped = 0

    for idx, row in enumerate(job.mapped_df.to_dict("records"), start=1):
        rowc = dict(row)
        verdict = verdict_by_row.get(idx)
        if verdict is not None:
            if verdict.decision == "conflict":
                held += 1
                continue
            rowc["_action"] = verdict.action or "Create"
            if verdict.decision == "update":
                if verdict.matched_guid:
                    rowc["guid"] = verdict.matched_guid
                if verdict.matched_name and _norm(verdict.matched_name) != _norm(row.get("name")):
                    rowc["old_name"] = verdict.matched_name

        master, errs = row_to_master(job.entity_type, rowc, source_row=idx)
        conv_errors.extend(errs)
        if master is None:
            skipped += 1
            continue
        if isinstance(master, Unit):
            units.append(master)
        elif isinstance(master, Group):
            groups.append(master)
        elif isinstance(master, StockItem):
            items.append(master)
        elif isinstance(master, Ledger):
            ledgers.append(master)

    total = len(units) + len(groups) + len(items) + len(ledgers)
    if total == 0:
        raise UnprocessableData(
            "No importable rows after conversion (all skipped or held for conflict).",
            errors=conv_errors,
            code="nothing_to_generate",
            detail=f"held_conflicts={held}, skipped_rows={skipped}",
        )

    company_name = company or job.company or "Company"
    try:
        xml = build_and_serialize(
            company_name, units=units, groups=groups, stock_items=items, ledgers=ledgers
        )
    except ValueError as exc:  # e.g. cyclic group hierarchy slipping through
        raise UnprocessableData(f"Could not build Tally XML: {exc}", code="xml_build_failed") from exc

    job.company = company_name
    job.xml = xml
    job.push_result = None
    job.notes = [f"generated={total}", f"held={held}", f"skipped={skipped}", f"convert_errors={len(conv_errors)}"]
    job.advance(JobStatus.GENERATED)
    log.info("generate.ok", job_id=job.id, generated=total, held=held, skipped=skipped,
             convert_errors=len(conv_errors), bytes=len(xml))
    return {"generated": total, "held": held, "skipped": skipped, "errors": conv_errors, "bytes": len(xml)}


def _generate_vouchers(job: Job, company: str | None) -> dict[str, Any]:
    """Group the mapped rows into balanced vouchers and build the Tally "Vouchers" envelope."""
    vouchers, conv_errors = rows_to_vouchers(job.mapped_df)
    total = len(vouchers)
    if total == 0:
        raise UnprocessableData(
            "No importable vouchers after grouping (all rows skipped or unbalanced).",
            errors=conv_errors, code="nothing_to_generate",
        )
    company_name = company or job.company or "Company"
    try:
        xml = build_and_serialize_vouchers(company_name, vouchers)
    except ValueError as exc:  # an unbalanced voucher slipping past validation
        raise UnprocessableData(f"Could not build voucher XML: {exc}", code="xml_build_failed") from exc

    lines = sum(len(v.lines) for v in vouchers)
    job.company = company_name
    job.xml = xml
    job.push_result = None
    job.notes = [f"generated={total}", f"voucher_lines={lines}", f"convert_errors={len(conv_errors)}"]
    job.advance(JobStatus.GENERATED)
    log.info("generate.vouchers.ok", job_id=job.id, vouchers=total, lines=lines, bytes=len(xml))
    return {"generated": total, "held": 0, "skipped": 0, "errors": conv_errors, "bytes": len(xml)}


def run_push(job: Job, settings: Settings) -> Any:
    job.require("push")
    if job.xml is None:
        raise InvalidState("Generate XML before pushing.")
    if not settings.direct_tally_push:
        raise ServiceUnavailable(
            "Direct push is disabled; connect a bridge agent to relay to Tally.",
            code="bridge_unavailable",
            detail="Set TM_DIRECT_TALLY_PUSH=true for same-machine dev pushes.",
        )

    # localhost HTTP only — stdlib urllib (no TLS/certifi needed; cloud<->bridge transport is separate)
    request = urllib.request.Request(
        settings.tally_url, data=job.xml, headers={"Content-Type": "text/xml"}, method="POST"
    )
    try:
        with urllib.request.urlopen(request, timeout=settings.tally_timeout_seconds) as resp:
            body = resp.read()
    except (urllib.error.URLError, OSError) as exc:
        log.error("push.unreachable", job_id=job.id, exc_info=True)
        raise ServiceUnavailable(
            "Could not reach the Tally gateway.", code=ErrorCode.TALLY_UNREACHABLE, detail=str(exc)
        ) from exc

    try:
        result = parse_import_response(body)
    except XMLSecurityError as exc:
        log.error("push.bad_response", job_id=job.id, detail=str(exc))
        raise UnprocessableData(
            "Tally returned a response that could not be parsed.", code="bad_tally_response", detail=str(exc)
        ) from exc

    job.push_result = result
    job.advance(JobStatus.PUSHED)
    log.info(
        "push.done",
        job_id=job.id,
        created=result.created,
        altered=result.altered,
        errors=result.errors,
        exceptions=result.exceptions,
        line_errors=len(result.line_errors),
    )
    return result
