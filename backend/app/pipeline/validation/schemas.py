"""Pandera DataFrame schemas per Tally master type.

The input DataFrame has already been renamed to canonical field keys (post-mapping) and is coerced
to string dtype by the caller, so checks operate on text values. Value-set checks (valid groups /
units / states) are **late-bound** from the user's own Tally master snapshot — passed in here — so
"is PARENT a real group" is checked against *that company's* chart of accounts (plan §6).

We use the pandas backend explicitly (``import pandera.pandas as pa``) per pandera 0.20+.
"""

from __future__ import annotations

from collections.abc import Iterable
from decimal import Decimal, InvalidOperation

import pandera.pandas as pa

from app.pipeline.entities import EntityType

# Regexes (mirror shared/canonical_fields/*.json).
GSTIN = r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z]{1}[1-9A-Z]{1}Z[0-9A-Z]{1}$"
PAN = r"^[A-Z]{5}[0-9]{4}[A-Z]$"
HSN = r"^[0-9]{4,8}$"
PINCODE = r"^[0-9]{6}$"
EMAIL = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"

MAX_NAME_LEN = 100


def _is_decimal(value: object) -> bool:
    try:
        Decimal(str(value).replace(",", "").strip())
    except (InvalidOperation, ValueError, AttributeError):
        return False
    return True


def _decimal_check() -> pa.Check:
    # element-wise; ignore_na so blanks pass (optional numeric fields)
    return pa.Check(_is_decimal, element_wise=True, ignore_na=True, name="decimal_parseable")


def _isin_or_empty(values: Iterable[str] | None) -> list[pa.Check]:
    """Return an ``isin`` check only when a value-set is actually available (snapshot present)."""
    if not values:
        return []
    return [pa.Check.isin(sorted(set(values)))]


def _name_column(extra_checks: list[pa.Check] | None = None) -> pa.Column:
    return pa.Column(
        nullable=False,
        unique=True,
        required=True,
        checks=[pa.Check.str_length(1, MAX_NAME_LEN), *(extra_checks or [])],
    )


def _unit_symbol_check() -> pa.Check:
    # Tally rejects unit symbols containing whitespace with "<LINEERROR>BAD UNIT NAME</LINEERROR>"
    # (verified against live TallyPrime 2026-06-26). Catch it at validation, not silently at push.
    return pa.Check(
        lambda v: not any(ch.isspace() for ch in str(v)),
        element_wise=True,
        ignore_na=True,
        name="unit_symbol",
    )


def build_schema(
    entity: EntityType,
    *,
    known_groups: Iterable[str] | None = None,
    known_units: Iterable[str] | None = None,
    known_states: Iterable[str] | None = None,
    known_stock_groups: Iterable[str] | None = None,
) -> pa.DataFrameSchema:
    if entity == EntityType.LEDGER:
        columns = {
            "name": _name_column(),
            "parent": pa.Column(nullable=False, required=True, checks=_isin_or_empty(known_groups)),
            "opening_balance": pa.Column(nullable=True, required=False, checks=[_decimal_check()]),
            "opening_balance_drcr": pa.Column(
                nullable=True,
                required=False,
                checks=[pa.Check.isin(["Dr", "Cr", "DR", "CR", "dr", "cr", ""])],
            ),
            "gstin": pa.Column(nullable=True, required=False, checks=[pa.Check.str_matches(GSTIN)]),
            "pan": pa.Column(nullable=True, required=False, checks=[pa.Check.str_matches(PAN)]),
            "state": pa.Column(nullable=True, required=False, checks=_isin_or_empty(known_states)),
            "email": pa.Column(nullable=True, required=False, checks=[pa.Check.str_matches(EMAIL)]),
            "pincode": pa.Column(nullable=True, required=False, checks=[pa.Check.str_matches(PINCODE)]),
            "credit_limit": pa.Column(nullable=True, required=False, checks=[_decimal_check()]),
        }
        return pa.DataFrameSchema(columns, strict=False, name="ledger")

    if entity == EntityType.GROUP:
        columns = {
            "name": _name_column(),
            # parent may be Primary, an existing group, or another group created in the same file;
            # the acyclic check is done separately (table-level) in validate().
            "parent": pa.Column(nullable=False, required=True),
        }
        return pa.DataFrameSchema(columns, strict=False, name="group")

    if entity == EntityType.STOCK_ITEM:
        columns = {
            "name": _name_column(),
            "base_units": pa.Column(nullable=False, required=True, checks=_isin_or_empty(known_units)),
            "parent": pa.Column(nullable=True, required=False, checks=_isin_or_empty(known_stock_groups)),
            "hsn": pa.Column(nullable=True, required=False, checks=[pa.Check.str_matches(HSN)]),
            "opening_qty": pa.Column(nullable=True, required=False, checks=[_decimal_check()]),
            "opening_rate": pa.Column(nullable=True, required=False, checks=[_decimal_check()]),
            "opening_value": pa.Column(nullable=True, required=False, checks=[_decimal_check()]),
            "gst_rate": pa.Column(nullable=True, required=False, checks=[_decimal_check()]),
        }
        return pa.DataFrameSchema(columns, strict=False, name="stock_item")

    if entity == EntityType.UNIT:
        columns = {
            "name": _name_column(extra_checks=[_unit_symbol_check()]),
            "decimal_places": pa.Column(nullable=True, required=False, checks=[_decimal_check()]),
            "conversion": pa.Column(nullable=True, required=False, checks=[_decimal_check()]),
        }
        return pa.DataFrameSchema(columns, strict=False, name="unit")

    raise ValueError(f"Unknown entity type: {entity}")
