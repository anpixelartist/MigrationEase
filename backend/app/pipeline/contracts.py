"""Shared pipeline contracts — the typed shapes that flow between stages and to the API.

These are the backbone every stage produces/consumes (plan §4). The error model is deliberately
ONE shape (`ErrorEnvelope`) so that Pandera validation failures and Tally push ``LINEERROR``
responses normalize into a single user-facing "error explanation center", and so the React side
renders fix-it cards keyed on a stable `ErrorCode`.

Pure pydantic/enums — no third-party runtime deps beyond pydantic.
"""

from __future__ import annotations

from typing import Any, Generic, Literal, TypeVar

from pydantic import BaseModel, Field

from app.pipeline.entities import EntityType

T = TypeVar("T")

Severity = Literal["error", "warning", "info"]
MappingMethod = Literal["schema_match", "embedding", "fuzzy", "template", "manual"]


class StageName(str):
    """Pipeline stage identifiers (kept as plain str constants for cheap interop/logging)."""

    PARSE = "parse"
    PROFILE = "profile"
    MAP = "map"
    VALIDATE = "validate"
    RESOLVE = "resolve"
    CONVERT = "convert"
    PUSH = "push"


class ErrorCode(str):
    """Stable codes the UI renders fix-it cards for. String subclass for ergonomic comparison."""

    # parsing / ingest
    EMPTY_FILE = "empty_file"
    FILE_TOO_LARGE = "file_too_large"
    UNSUPPORTED_FORMAT = "unsupported_format"
    PARSE_FAILED = "parse_failed"
    # validation (Pandera)
    REQUIRED_MISSING = "required_missing"
    NAME_TOO_LONG = "name_too_long"
    BAD_UNIT_NAME = "bad_unit_name"
    DUPLICATE_IN_FILE = "duplicate_in_file"
    UNKNOWN_GROUP = "unknown_group"
    UNKNOWN_UNIT = "unknown_unit"
    BAD_DECIMAL = "bad_decimal"
    BAD_GSTIN = "bad_gstin"
    BAD_HSN = "bad_hsn"
    BAD_PAN = "bad_pan"
    GROUP_CYCLE = "group_cycle"
    # vouchers (Phase 2)
    BAD_DATE = "bad_date"
    VOUCHER_UNBALANCED = "voucher_unbalanced"
    UNKNOWN_VOUCHER_TYPE = "unknown_voucher_type"
    VOUCHER_CONFLICT = "voucher_conflict"  # rows of one voucher disagree on date/type/party
    INVENTORY_MISMATCH = "inventory_mismatch"  # qty x rate != line amount, or bad quantity
    # resolution
    DUP_CONFLICT = "dup_conflict"
    # conversion / referential integrity
    UNRESOLVED_REFERENCE = "unresolved_reference"
    # push / Tally + bridge
    TALLY_LINEERROR = "tally_lineerror"
    BRIDGE_OFFLINE = "bridge_offline"
    WRONG_COMPANY = "wrong_company"
    TALLY_UNREACHABLE = "tally_unreachable"
    # generic / structural
    VALUE_NOT_ALLOWED = "value_not_allowed"
    MISSING_COLUMN = "missing_column"
    VALIDATION_ERROR = "validation_error"


class ErrorEnvelope(BaseModel):
    """One normalized error/warning, row- or column- or table-scoped."""

    code: str
    severity: Severity = "error"
    stage: str
    entity: EntityType | None = None
    source_row: int | None = None  # 1-based row in the original uploaded file
    source_column: str | None = None
    target_field: str | None = None  # canonical field key
    message: str
    raw: str | None = None  # original Pandera failure / Tally <LINEERROR> text
    suggestion: str | None = None  # e.g. "Did you mean 'Sundry Debtors'?"


class ColumnMapping(BaseModel):
    """A single source-column -> canonical-field assignment with provenance."""

    source_column: str
    target_field: str | None = None  # None => unmapped / ignored
    confidence: float = 0.0  # 0..1 (schema/embedding * fuzzy boost, gated by type compatibility)
    method: MappingMethod = "manual"


class MappingPlan(BaseModel):
    """The user-confirmed mapping for one upload of one entity type."""

    entity: EntityType
    mappings: list[ColumnMapping] = Field(default_factory=list)
    constants: dict[str, str] = Field(default_factory=dict)  # user-pinned constants (e.g. fixed Parent)
    template_id: str | None = None


class StageResult(BaseModel, Generic[T]):
    """Uniform stage output: a payload plus accumulated errors and lightweight stats."""

    ok: bool
    stage: str
    data: T | None = None
    errors: list[ErrorEnvelope] = Field(default_factory=list)
    stats: dict[str, Any] = Field(default_factory=dict)  # rows_in, rows_out, duration_ms, ...
