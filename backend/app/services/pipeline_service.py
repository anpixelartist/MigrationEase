"""Pipeline orchestration — runs each stage over a Job, with logging + structured error capture.

Callers must hold the job lock (see ``InMemoryJobStore.lock``). Every function enforces the state
machine, logs start/outcome with the job id and counts, and converts stage failures into AppErrors
(problem+json) rather than letting exceptions escape.
"""

from __future__ import annotations

import datetime as _dt
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
from app.services.job_store import STALE_PUSH_SECONDS, Job, JobStatus

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

    # Store rows into StagedRecord database table as the core pipeline aggregation layer
    from app.db.base import new_session
    from app.db.models import StagedRecord

    with new_session() as db:
        records = []
        for i, row in enumerate(df.to_dict("records")):
            order_id = row.get("order_id") or row.get("voucher_number") or row.get("Name")
            records.append(
                StagedRecord(
                    org_id=job.org_id,
                    job_id=job.id,
                    order_id=str(order_id).strip() if order_id else None,
                    data=row,
                )
            )
        db.add_all(records)
        db.commit()

    job.advance(JobStatus.PARSED)
    log.info("parse.ok", job_id=job.id, rows=int(df.shape[0]), columns=int(df.shape[1]), notes=notes)


def suggest_mapping(job: Job) -> MappingProposal:
    if job.df is None:
        raise InvalidState("Upload a file before requesting mapping suggestions.")
    proposal = auto_map(job.df, job.entity_type).data
    _overlay_saved_template(job, proposal)
    log.info(
        "map.suggested",
        job_id=job.id,
        unmapped_required=proposal.unmapped_required,
        sources=len(job.df.columns),
        applied_template=proposal.applied_template,
    )
    return proposal


def _overlay_saved_template(job: Job, proposal: MappingProposal) -> None:
    """Strengthen the fuzzy proposal with the org's best-matching saved template (if any).

    A user who once mapped this export shape by hand and saved it gets it auto-applied on the next
    import: the template's columns win (method=template, auto-accepted), and its constants ride along
    for the UI to pre-fill. Never fails the request — a template lookup error just leaves the fuzzy
    proposal untouched.
    """
    try:
        from app.services import template_service

        tmpl = template_service.best_match(job.org_id, job.entity_type, list(job.df.columns))
    except Exception:  # pragma: no cover - template overlay is best-effort, never blocks mapping
        log.warning("map.template_overlay_failed", job_id=job.id, exc_info=True)
        return
    if tmpl is None:
        return

    present = set(job.df.columns)
    by_field = {s.target_field: s for s in proposal.suggestions}
    for field, col in tmpl.mapping.items():
        if col in present and field in by_field:
            s = by_field[field]
            s.source_column = col
            s.method = "template"
            s.confidence = max(s.confidence, 0.99)
            s.status = "auto_accept"

    used = {s.source_column for s in proposal.suggestions if s.source_column}
    proposal.unmapped_sources = [c for c in job.df.columns if c not in used]
    proposal.unmapped_required = [
        s.target_field for s in proposal.suggestions if s.required and s.source_column is None
    ]
    proposal.applied_template = tmpl.name
    proposal.applied_constants = dict(tmpl.constants)


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


def run_generation(job: Job, company: str | None, cutover_date: str | None = None, b2c_summary: bool = False, settlement_mode: bool = False) -> dict[str, Any]:
    job.require("generate")
    if job.mapped_df is None or job.validation is None:
        raise InvalidState("Map and validate before generating.")

    blocking = [e for e in job.validation.errors if e.severity == "error"]
    if blocking:
        raise UnprocessableData(
            "Resolve validation errors before generating XML.", errors=blocking, code="validation_failed"
        )

    if job.entity_type == EntityType.VOUCHER:
        return _generate_vouchers(job, company, cutover_date, b2c_summary=b2c_summary, settlement_mode=settlement_mode)

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


def _generate_vouchers(job: Job, company: str | None, cutover_date: str | None = None, b2c_summary: bool = False, settlement_mode: bool = False) -> dict[str, Any]:
    """Group the mapped rows into balanced vouchers and build the Tally "Vouchers" envelope."""
    df = job.mapped_df

    opening_balances = {}
    if cutover_date and "date" in df.columns and not settlement_mode:
        from datetime import datetime
        try:
            cutover = datetime.strptime(cutover_date, "%Y-%m-%d").date()
            from app.pipeline.coercion import coerce_date, coerce_amount_drcr
            from decimal import Decimal

            def is_post_cutover(val):
                try:
                    d = coerce_date(val)
                    return d is None or d >= cutover
                except ValueError:
                    return True

            mask = df["date"].apply(is_post_cutover)
            pre_df = df[~mask]
            df = df[mask]

            for _, row in pre_df.iterrows():
                ledger = str(row.get("ledger_name", "")).strip()
                party = str(row.get("party_ledger", "")).strip()
                if not ledger:
                    continue
                ad = coerce_amount_drcr(row.get("amount"), row.get("dr_cr"))
                if ad is None:
                    continue
                mag, is_debit = ad

                # Accrue main ledger
                current_net = opening_balances.get(ledger, Decimal("0"))
                change = mag if is_debit else -mag
                opening_balances[ledger] = current_net + change

                # Accrue counter-party ledger to maintain Double-Entry integrity
                if party:
                    current_party_net = opening_balances.get(party, Decimal("0"))
                    party_change = -mag if is_debit else mag
                    opening_balances[party] = current_party_net + party_change

        except ValueError:
            pass # ignore bad cutover date format

    if settlement_mode:
        from app.pipeline.voucher import settlement_rows_to_vouchers
        vouchers, conv_errors = settlement_rows_to_vouchers(df)
    else:
        vouchers, conv_errors = rows_to_vouchers(df)
    if b2c_summary:
        from app.pipeline.voucher import aggregate_b2c_daily
        vouchers = aggregate_b2c_daily(vouchers)

    ledgers = []
    from app.pipeline.entities import Ledger, TallyAction

    # We extracted both `ledger_name` and `party_ledger` into `opening_balances`.
    # `party_ledger` represents the customer leg and safely belongs in "Sundry Debtors".
    # All other ledgers (Sales, Taxes) should default to "Primary" to prevent breaking the CoAs.
    # We check job.mapped_df to ensure we capture parties that only existed prior to cutover.
    party_ledgers = {str(row.get("party_ledger", "")).strip() for _, row in job.mapped_df.iterrows() if str(row.get("party_ledger", "")).strip()}

    for name, net_balance in opening_balances.items():
        if net_balance == 0:
            continue
        parent_group = "Sundry Debtors" if name in party_ledgers else "Primary"
        ledgers.append(
            Ledger(
                name=name,
                parent=parent_group,
                action=TallyAction.CREATE, # IMPORTDUPS handles the upsert/alter logic safely
                opening_balance=abs(net_balance),
                opening_is_debit=net_balance > 0,
            )
        )

    total = len(vouchers)
    company_name = company or job.company or "Company"
    try:
        from app.pipeline.conversion.builder import build_and_serialize
        xml = build_and_serialize(company_name, vouchers=vouchers, ledgers=ledgers)
    except ValueError as exc:  # an unbalanced voucher slipping past validation
        raise UnprocessableData(f"Could not build unified XML: {exc}", code="xml_build_failed") from exc

    lines = sum(len(v.lines) for v in vouchers)
    from decimal import Decimal
    debit_total = sum((v.debit_total for v in vouchers), Decimal("0"))
    credit_total = sum((v.credit_total for v in vouchers), Decimal("0"))

    job.company = company_name
    job.xml = xml
    job.push_result = None
    job.notes = [f"generated={total}", f"voucher_lines={lines}", f"convert_errors={len(conv_errors)}", f"opening_balances={len(ledgers)}"]
    job.advance(JobStatus.GENERATED)
    log.info("generate.vouchers.ok", job_id=job.id, vouchers=total, lines=lines, bytes=len(xml), ob=len(ledgers))
    return {
        "generated": total,
        "held": 0,
        "skipped": 0,
        "errors": conv_errors,
        "bytes": len(xml),
        "debit_total": str(debit_total),
        "credit_total": str(credit_total),
    }


def ensure_pushable(job: Job) -> None:
    """Raise unless a push may start now (also used by the router as a fast pre-check)."""
    if job.status is JobStatus.PUSHING:
        age = (_dt.datetime.now(_dt.timezone.utc) - job.updated_at).total_seconds()
        if age < STALE_PUSH_SECONDS:
            raise InvalidState(
                "A push is already in progress for this job.",
                code="push_in_progress",
                detail=f"Claimed {int(age)}s ago; retry allowed after {STALE_PUSH_SECONDS}s.",
            )
        log.warning("push.reclaim_stale", job_id=job.id, stale_seconds=int(age))
    else:
        job.require("push")
    if job.xml is None:
        raise InvalidState("Generate the XML before pushing.")


def claim_push(job: Job) -> None:
    """Atomically claim the push (call under the job lock) — the idempotency guard.

    Concurrent/duplicate pushes get a 409 instead of silently double-importing into Tally. A
    ``PUSHING`` claim whose worker died is reclaimable after ``STALE_PUSH_SECONDS``.
    """
    ensure_pushable(job)
    job.advance(JobStatus.PUSHING)


def record_push_result(job: Job, result: Any) -> JobStatus:
    """Persist Tally's verdict and pick the terminal status (call under the job lock).

    - all rows accepted            -> PUSHED       (re-push blocked: would duplicate everything)
    - nothing imported             -> PUSH_FAILED  (safe to retry)
    - some imported, some errored  -> PUSHED_PARTIAL (re-push blocked: would duplicate the
      successes; recovery = fix data -> re-generate -> push)
    """
    job.push_result = result
    imported = result.created + result.altered + result.combined
    if result.is_success:
        status = JobStatus.PUSHED
    elif imported == 0:
        status = JobStatus.PUSH_FAILED
    else:
        status = JobStatus.PUSHED_PARTIAL
        job.notes.append(
            f"partial import: {imported} row(s) already in Tally, {result.errors} error(s), "
            f"{len(result.line_errors)} line error(s) — re-push blocked to avoid duplicates"
        )
    job.advance(status)
    log.info(
        "push.done",
        job_id=job.id,
        status=status.value,
        created=result.created,
        altered=result.altered,
        errors=result.errors,
        exceptions=result.exceptions,
        line_errors=len(result.line_errors),
    )
    return status


def record_push_failure(job: Job, reason: str) -> None:
    """Mark a push whose response never arrived (call under the job lock).

    Retry is allowed, but if the transport died AFTER Tally received the XML the import may have
    happened — the note tells the user to verify in Tally before re-pushing.
    """
    job.notes.append(f"push failed: {reason} — verify in Tally before re-pushing")
    job.advance(JobStatus.PUSH_FAILED)
    log.warning("push.failed", job_id=job.id, reason=reason)


def run_push(job: Job, settings: Settings) -> Any:
    """Direct dev push to a local Tally gateway. Caller must have claimed via ``claim_push``."""
    if job.xml is None:
        raise InvalidState("Generate the XML before pushing.")
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

    return result
