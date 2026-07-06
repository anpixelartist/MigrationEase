"""Validation stage — run a Pandera schema per master type, normalize failures into ErrorEnvelopes.

``validate()`` returns a :class:`StageResult` whose ``ok`` is True only when there are no
*error*-severity findings; warnings (format issues, optional fields) do not block. The normalized
errors drive the "error explanation center" and include a fuzzy "did you mean ...?" suggestion for
unknown group/unit/state values (rapidfuzz), which also pre-warms the resolution step (plan §6).
"""

from __future__ import annotations

import math
from collections.abc import Iterable

import pandas as pd
from pandera.errors import SchemaErrors
from rapidfuzz import process

from app.pipeline.contracts import ErrorCode, ErrorEnvelope, Severity, StageName, StageResult
from app.pipeline.entities import EntityType, Group
from app.pipeline.validation.schemas import build_schema

__all__ = ["validate"]


def _suggest(value: str, choices: list[str] | None) -> str | None:
    if not choices or not value:
        return None
    match = process.extractOne(str(value), choices, score_cutoff=80)
    if match is None:
        return None
    return f"Did you mean '{match[0]}'?"


def _classify(
    column: str | None,
    check: str | None,
    failure_case: object,
    entity: EntityType,
    suggestion_sets: dict[str, list[str] | None],
) -> tuple[str, Severity, str, str | None]:
    col = column or "(table)"
    check_l = check or ""
    fc = "" if failure_case is None else str(failure_case)

    if check_l.startswith("column_in_dataframe"):
        return ErrorCode.MISSING_COLUMN, "error", f"Required column '{col}' is missing from the file.", None
    if check_l == "not_nullable":
        return ErrorCode.REQUIRED_MISSING, "error", f"'{col}' is required and cannot be blank.", None
    if check_l == "field_uniqueness":
        return ErrorCode.DUPLICATE_IN_FILE, "error", f"Duplicate {col} '{fc}' appears more than once in the file.", None
    if check_l.startswith("str_length"):
        return ErrorCode.NAME_TOO_LONG, "error", f"'{col}' value '{fc}' exceeds the maximum length.", None
    if check_l == "unit_symbol":
        return (
            ErrorCode.BAD_UNIT_NAME,
            "error",
            f"Unit symbol '{fc}' is invalid — Tally unit names cannot contain spaces (use e.g. 'Nos', 'Kg').",
            None,
        )
    if check_l.startswith("isin"):
        if col == "parent" and entity in (EntityType.LEDGER, EntityType.GROUP):
            code, sev = ErrorCode.UNKNOWN_GROUP, "error"
        elif col == "base_units":
            code, sev = ErrorCode.UNKNOWN_UNIT, "error"
        elif col == "parent":  # stock-item parent (stock group) — softer
            code, sev = ErrorCode.VALUE_NOT_ALLOWED, "warning"
        else:  # state, drcr, ...
            code, sev = ErrorCode.VALUE_NOT_ALLOWED, "warning"
        return code, sev, f"'{fc}' is not a known {col}.", _suggest(fc, suggestion_sets.get(col))
    if check_l.startswith("str_matches"):
        code = {"gstin": ErrorCode.BAD_GSTIN, "pan": ErrorCode.BAD_PAN, "hsn": ErrorCode.BAD_HSN}.get(
            col or "", ErrorCode.VALUE_NOT_ALLOWED
        )
        return code, "warning", f"'{col}' value '{fc}' is not in the expected format.", None
    if check_l == "decimal_parseable":
        return ErrorCode.BAD_DECIMAL, "warning", f"'{col}' value '{fc}' is not a valid number.", None
    return ErrorCode.VALIDATION_ERROR, "error", f"'{col}' failed validation ({check_l}).", None


def _source_row(index: object) -> int | None:
    if index is None:
        return None
    try:
        value = int(index)
    except (TypeError, ValueError):
        return None
    if isinstance(index, float) and math.isnan(index):
        return None
    return value + 1  # 1-based for the user-facing file row


def _failures_to_envelopes(
    failure_cases: pd.DataFrame,
    entity: EntityType,
    suggestion_sets: dict[str, list[str] | None],
) -> list[ErrorEnvelope]:
    envelopes: list[ErrorEnvelope] = []
    for record in failure_cases.to_dict("records"):
        column = record.get("column")
        code, severity, message, suggestion = _classify(
            column, record.get("check"), record.get("failure_case"), entity, suggestion_sets
        )
        envelopes.append(
            ErrorEnvelope(
                code=code,
                severity=severity,
                stage=StageName.VALIDATE,
                entity=entity,
                source_row=_source_row(record.get("index")),
                source_column=column,
                target_field=column,
                message=message,
                raw=str(record.get("check")),
                suggestion=suggestion,
            )
        )
    return envelopes


def _detect_group_cycle(df: pd.DataFrame) -> ErrorEnvelope | None:
    from app.pipeline.conversion.builder import topological_sort_groups  # local import avoids cycle

    if "name" not in df.columns or "parent" not in df.columns:
        return None
    groups: list[Group] = []
    for _, row in df.iterrows():
        name = row.get("name")
        if name is None or pd.isna(name) or not str(name).strip():
            continue
        parent = row.get("parent")
        parent_str = str(parent) if parent is not None and not pd.isna(parent) else "Primary"
        groups.append(Group(name=str(name), parent=parent_str))
    try:
        topological_sort_groups(groups)
    except ValueError as exc:
        return ErrorEnvelope(
            code=ErrorCode.GROUP_CYCLE,
            severity="error",
            stage=StageName.VALIDATE,
            entity=EntityType.GROUP,
            message="Cyclic group hierarchy detected among the imported groups.",
            raw=str(exc),
        )
    return None


def _validate_vouchers(work: pd.DataFrame, original: pd.DataFrame) -> StageResult[pd.DataFrame]:
    """Voucher validation: group rows into vouchers (line format) and surface every problem,
    including the blocking Dr=Cr balance check (Tally rejects unbalanced vouchers outright)."""
    from app.pipeline.voucher import rows_to_vouchers  # local import avoids a cycle

    vouchers, errors = rows_to_vouchers(work, stage=StageName.VALIDATE)
    error_count = sum(1 for e in errors if e.severity == "error")
    warning_count = sum(1 for e in errors if e.severity == "warning")
    return StageResult(
        ok=error_count == 0,
        stage=StageName.VALIDATE,
        data=original,
        errors=errors,
        stats={
            "rows_in": int(len(original)),
            "vouchers": len(vouchers),
            "error_count": error_count,
            "warning_count": warning_count,
        },
    )


def validate(
    df: pd.DataFrame,
    entity: EntityType,
    *,
    known_groups: Iterable[str] | None = None,
    known_units: Iterable[str] | None = None,
    known_states: Iterable[str] | None = None,
    known_stock_groups: Iterable[str] | None = None,
) -> StageResult[pd.DataFrame]:
    """Validate ``df`` (canonical-keyed) for ``entity`` and return a StageResult with ErrorEnvelopes."""
    work = df.astype("string")  # robust text checks + correct null detection
    # treat whitespace-only cells as blank so required-field checks catch "   " (caught at convert otherwise)
    work = work.apply(lambda col: col.str.strip()).replace("", pd.NA)

    if entity == EntityType.VOUCHER:
        return _validate_vouchers(work, df)
    schema = build_schema(
        entity,
        known_groups=known_groups,
        known_units=known_units,
        known_states=known_states,
        known_stock_groups=known_stock_groups,
    )
    parent_choices = (
        sorted(set(known_groups))
        if entity in (EntityType.LEDGER, EntityType.GROUP) and known_groups
        else (sorted(set(known_stock_groups)) if known_stock_groups else None)
    )
    suggestion_sets: dict[str, list[str] | None] = {
        "parent": parent_choices,
        "base_units": sorted(set(known_units)) if known_units else None,
        "state": sorted(set(known_states)) if known_states else None,
    }

    envelopes: list[ErrorEnvelope] = []
    try:
        schema.validate(work, lazy=True)
    except SchemaErrors as exc:
        envelopes.extend(_failures_to_envelopes(exc.failure_cases, entity, suggestion_sets))

    if entity == EntityType.GROUP:
        cycle = _detect_group_cycle(work)
        if cycle is not None:
            envelopes.append(cycle)

    error_count = sum(1 for e in envelopes if e.severity == "error")
    warning_count = sum(1 for e in envelopes if e.severity == "warning")
    return StageResult(
        ok=error_count == 0,
        stage=StageName.VALIDATE,
        data=df,
        errors=envelopes,
        stats={
            "rows_in": int(len(df)),
            "error_count": error_count,
            "warning_count": warning_count,
        },
    )
