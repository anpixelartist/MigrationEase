"""Robust value coercion + row -> canonical-master conversion (the data edge-case layer).

Real Tally/accounting exports are messy: amounts arrive as ``"₹1,23,456.00"``, ``"(500)"``,
``"1000 Cr"``, ``"-250"``, ``"1.5e3"``; booleans as ``Yes/No/Y/1/true``; blanks as ``""``, ``"NA"``,
``"-"``. These helpers normalize all of that and NEVER raise into the request path — failures are
returned as :class:`ErrorEnvelope` so a single bad cell doesn't sink a whole import.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

import pandas as pd

from app.pipeline.contracts import ErrorCode, ErrorEnvelope, StageName
from app.pipeline.entities import (
    EntityType,
    Group,
    Ledger,
    MasterBase,
    StockItem,
    TallyAction,
    Unit,
)

_NULLISH = {"", "na", "n/a", "nan", "none", "null", "nil", "-", "--"}
_TRUE = {"yes", "y", "true", "t", "1", "on"}
_FALSE = {"no", "n", "false", "f", "0", "off"}
# currency symbols / codes and the Indian "Rs"/"INR" prefixes
_CURRENCY = re.compile(r"[₹$€£¥]|(?i:\b(?:rs|inr|usd|eur)\b)\.?")
_DRCR_SUFFIX = re.compile(r"(?i)\b(dr|cr|debit|credit)\b\.?")
_ISO_DATE = re.compile(r"^\d{4}-\d{1,2}-\d{1,2}")


def is_blank(value: Any) -> bool:
    if value is None:
        return True
    try:
        if bool(pd.isna(value)):  # pd.NA, NaN, NaT (from StringDtype/object columns)
            return True
    except (TypeError, ValueError):  # pd.isna on array-likes
        pass
    return str(value).strip().lower() in _NULLISH


def coerce_str(value: Any) -> str | None:
    if is_blank(value):
        return None
    return str(value).strip()


def coerce_bool(value: Any) -> bool | None:
    if is_blank(value):
        return None
    token = str(value).strip().lower()
    if token in _TRUE:
        return True
    if token in _FALSE:
        return False
    raise ValueError(f"not a yes/no value: {value!r}")


def coerce_decimal(value: Any) -> Decimal | None:
    """Parse a messy numeric string into a Decimal (or None if blank). Raises ValueError if invalid."""
    if is_blank(value):
        return None
    if isinstance(value, (int, float, Decimal)):
        try:
            return Decimal(str(value))
        except InvalidOperation as exc:
            raise ValueError(f"not a number: {value!r}") from exc

    text = str(value).strip()
    negative = False

    # accounting parentheses negative: (500) -> -500
    if text.startswith("(") and text.endswith(")"):
        negative = True
        text = text[1:-1]

    text = _CURRENCY.sub("", text)
    text = _DRCR_SUFFIX.sub("", text)  # a stray Dr/Cr token; sign handled by caller for balances
    text = text.replace(",", "").replace("_", "").replace(" ", "").strip()

    # leading/trailing sign variants
    if text.endswith("-"):
        negative = not negative
        text = text[:-1]
    if text.startswith("+"):
        text = text[1:]
    if text.startswith("-"):
        negative = not negative
        text = text[1:]

    if text == "" or text == ".":
        return None
    try:
        magnitude = Decimal(text)
    except InvalidOperation as exc:
        raise ValueError(f"not a number: {value!r}") from exc
    return -magnitude if negative else magnitude


def coerce_date(value: Any) -> date | None:
    """Parse a messy date cell into a ``date`` (or None if blank). Raises ValueError if unparseable.

    Day-first (dd/mm/yyyy, dd-mm-yyyy) is assumed for the Indian context; ISO (yyyy-mm-dd) and Excel
    datetime values still parse unambiguously.
    """
    if is_blank(value):
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    dayfirst = not _ISO_DATE.match(text)  # ISO yyyy-mm-dd is unambiguous; day-first for dd/mm/yyyy
    try:
        ts = pd.to_datetime(text, dayfirst=dayfirst, errors="raise")
    except (ValueError, TypeError, pd.errors.ParserError) as exc:
        raise ValueError(f"not a date: {value!r}") from exc
    if pd.isna(ts):
        raise ValueError(f"not a date: {value!r}")
    return ts.date()


def coerce_amount_drcr(amount: Any, drcr: Any) -> tuple[Decimal, bool] | None:
    """Return (magnitude>=0, is_debit) from an amount cell + optional Dr/Cr cell.

    Precedence: an explicit Dr/Cr column wins; else a Dr/Cr token inside the amount string; else the
    sign of the amount (positive => Debit, negative => Credit), matching Tally's convention.
    """
    parsed = coerce_decimal(amount)
    if parsed is None:
        return None

    is_debit: bool | None = None
    drcr_token = coerce_str(drcr)
    if drcr_token:
        low = drcr_token.lower()
        if low in {"dr", "debit", "d"}:
            is_debit = True
        elif low in {"cr", "credit", "c"}:
            is_debit = False
    if is_debit is None and isinstance(amount, str):
        match = _DRCR_SUFFIX.search(amount)
        if match:
            is_debit = match.group(1).lower() in {"dr", "debit"}

    if is_debit is None:
        is_debit = parsed >= 0
    return abs(parsed), is_debit


# --------------------------------------------------------------------------------------------------
# row -> canonical master
# --------------------------------------------------------------------------------------------------
def _err(entity: EntityType, row: int | None, column: str, code: str, message: str) -> ErrorEnvelope:
    return ErrorEnvelope(
        code=code,
        severity="error",
        stage=StageName.CONVERT,
        entity=entity,
        source_row=row,
        source_column=column,
        target_field=column,
        message=message,
    )


def _action(row: dict[str, Any]) -> TallyAction:
    decision = coerce_str(row.get("_action"))
    return TallyAction.ALTER if decision and decision.lower() == "alter" else TallyAction.CREATE


def row_to_master(
    entity: EntityType, row: dict[str, Any], *, source_row: int | None = None
) -> tuple[MasterBase | None, list[ErrorEnvelope]]:
    """Convert one mapped row (canonical keys) into a canonical master, capturing per-field errors."""
    errors: list[ErrorEnvelope] = []

    name = coerce_str(row.get("name"))
    if not name:
        errors.append(_err(entity, source_row, "name", ErrorCode.REQUIRED_MISSING, "Name is required."))
        return None, errors

    common = dict(
        name=name,
        source_row=source_row,
        action=_action(row),
        guid=coerce_str(row.get("guid")),
        old_name=coerce_str(row.get("old_name")),
    )

    def num(field: str) -> Decimal | None:
        try:
            return coerce_decimal(row.get(field))
        except ValueError:
            errors.append(_err(entity, source_row, field, ErrorCode.BAD_DECIMAL,
                              f"'{row.get(field)}' is not a valid number."))
            return None

    def flag(field: str) -> bool | None:
        try:
            return coerce_bool(row.get(field))
        except ValueError:
            errors.append(_err(entity, source_row, field, ErrorCode.VALUE_NOT_ALLOWED,
                              f"'{row.get(field)}' is not a yes/no value."))
            return None

    try:
        if entity == EntityType.UNIT:
            dp = num("decimal_places")
            master: MasterBase = Unit(
                **common,
                decimal_places=int(dp) if dp is not None else 0,
                conversion=num("conversion"),
                base_units=coerce_str(row.get("base_units")),
                additional_units=coerce_str(row.get("additional_units")),
                is_simple=coerce_str(row.get("base_units")) is None,
            )
        elif entity == EntityType.GROUP:
            master = Group(
                **common,
                parent=coerce_str(row.get("parent")) or "Primary",
                is_revenue=flag("is_revenue"),
                is_deemed_positive=flag("is_deemed_positive"),
                is_billwise=flag("is_billwise"),
            )
        elif entity == EntityType.LEDGER:
            parent = coerce_str(row.get("parent"))
            if not parent:
                errors.append(_err(entity, source_row, "parent", ErrorCode.REQUIRED_MISSING,
                                  "Ledger parent (group) is required."))
                return None, errors
            ob = coerce_amount_drcr(row.get("opening_balance"), row.get("opening_balance_drcr"))
            master = Ledger(
                **common,
                parent=parent,
                opening_balance=ob[0] if ob else Decimal("0"),
                opening_is_debit=ob[1] if ob else True,
                gstin=coerce_str(row.get("gstin")),
                gst_registration_type=coerce_str(row.get("gst_registration_type")),
                state=coerce_str(row.get("state")),
                is_billwise=flag("is_billwise"),
                mailing_name=coerce_str(row.get("mailing_name")),
                pincode=coerce_str(row.get("pincode")),
                email=coerce_str(row.get("email")),
                phone=coerce_str(row.get("phone")),
                credit_limit=num("credit_limit"),
            )
        elif entity == EntityType.STOCK_ITEM:
            base_units = coerce_str(row.get("base_units"))
            if not base_units:
                errors.append(_err(entity, source_row, "base_units", ErrorCode.REQUIRED_MISSING,
                                  "Stock item base units are required."))
                return None, errors
            master = StockItem(
                **common,
                parent=coerce_str(row.get("parent")) or "Primary",
                base_units=base_units,
                opening_qty=num("opening_qty"),
                opening_rate=num("opening_rate"),
                opening_value=num("opening_value"),
                hsn_code=coerce_str(row.get("hsn")) or None,
                gst_applicable=bool(flag("gst_applicable")),
            )
        else:  # pragma: no cover - guarded earlier
            raise ValueError(f"unknown entity {entity}")
    except ValueError as exc:
        # pydantic/entity-level validation failure (e.g. empty name after strip)
        errors.append(_err(entity, source_row, "name", ErrorCode.VALIDATION_ERROR, str(exc)))
        return None, errors

    return master, errors
