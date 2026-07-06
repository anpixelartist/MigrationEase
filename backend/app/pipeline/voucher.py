"""Group a canonical-keyed voucher DataFrame (line format) into balanced :class:`Voucher` objects.

The universal voucher input is the **line format**: one row per ledger entry, with a
``voucher_number`` that groups the lines of one voucher. This represents every voucher type
(Receipt/Payment/Journal/Contra/Sales/Purchase…) and any number of lines.

A line's (magnitude, Dr/Cr) is read from either explicit ``debit``/``credit`` columns or an
``amount`` + ``dr_cr`` pair (see :func:`app.pipeline.coercion.coerce_amount_drcr`). This function is
shared by validation (report the errors) and generation (build the XML), so the two never diverge.
Errors are returned as :class:`ErrorEnvelope` — a bad row never raises into the request path.
"""

from __future__ import annotations

from collections import OrderedDict

import pandas as pd

from app.pipeline.coercion import (
    coerce_amount_drcr,
    coerce_date,
    coerce_decimal,
    coerce_str,
    is_blank,
)
from app.pipeline.contracts import ErrorCode, ErrorEnvelope, StageName
from app.pipeline.entities import EntityType, Voucher, VoucherLine

__all__ = ["rows_to_vouchers"]


def _norm(value: object) -> str:
    return " ".join(str(value or "").strip().casefold().split())


def _err(row: int | None, column: str, code: str, message: str, stage: StageName,
         severity: str = "error") -> ErrorEnvelope:
    return ErrorEnvelope(
        code=code, severity=severity, stage=stage, entity=EntityType.VOUCHER,
        source_row=row, source_column=column, target_field=column, message=message,
    )


def _line_amount(row: dict, src: int, errors: list[ErrorEnvelope], stage: StageName):
    """Return (magnitude>=0, is_debit) for one line, or None (recording an error)."""
    debit_raw, credit_raw = row.get("debit"), row.get("credit")
    has_debit, has_credit = not is_blank(debit_raw), not is_blank(credit_raw)
    if has_debit or has_credit:
        if has_debit and has_credit:
            errors.append(_err(src, "debit", ErrorCode.VALIDATION_ERROR,
                               "A line has both a debit and a credit amount — provide only one.", stage))
            return None
        col = "debit" if has_debit else "credit"
        try:
            mag = coerce_decimal(debit_raw if has_debit else credit_raw)
        except ValueError:
            errors.append(_err(src, col, ErrorCode.BAD_DECIMAL, f"'{row.get(col)}' is not a valid number.", stage))
            return None
        if mag is None or mag == 0:
            errors.append(_err(src, col, ErrorCode.REQUIRED_MISSING, "Line amount is required and cannot be zero.", stage))
            return None
        return abs(mag), has_debit

    try:
        ad = coerce_amount_drcr(row.get("amount"), row.get("dr_cr"))
    except ValueError:
        errors.append(_err(src, "amount", ErrorCode.BAD_DECIMAL, f"'{row.get('amount')}' is not a valid number.", stage))
        return None
    if ad is None or ad[0] == 0:
        errors.append(_err(src, "amount", ErrorCode.REQUIRED_MISSING,
                           "Each line needs an amount (via an Amount+Dr/Cr or Debit/Credit column).", stage))
        return None
    return ad


def _inventory_fields(row: dict, src: int, stock_item: str, amount, errors, stage):
    """Parse + loudly validate the quantity/rate of an inventory line. Returns (qty, rate) or None."""
    from decimal import Decimal

    def _dec(field):
        try:
            return coerce_decimal(row.get(field))
        except ValueError:
            errors.append(_err(src, field, ErrorCode.BAD_DECIMAL, f"'{row.get(field)}' is not a valid number.", stage))
            return "BAD"

    qty = _dec("quantity")
    rate = _dec("rate")
    if qty == "BAD" or rate == "BAD":
        return None
    if qty is None or qty <= 0:
        errors.append(_err(src, "quantity", ErrorCode.INVENTORY_MISMATCH,
                           f"Item '{stock_item}' needs a positive quantity (got {row.get('quantity')!r}).", stage))
        return None
    if rate is not None:  # qty x rate must reconcile with the line amount (loud warning; discounts/rounding ok)
        expected = (abs(qty) * abs(rate)).quantize(Decimal("0.01"))
        if abs(expected - abs(amount)) > Decimal("0.01"):
            errors.append(_err(src, "amount", ErrorCode.INVENTORY_MISMATCH,
                               f"Item '{stock_item}': quantity x rate = {expected} but the line amount is {amount}.",
                               stage, severity="warning"))
    return qty, rate


def rows_to_vouchers(
    df: pd.DataFrame, *, stage: StageName = StageName.CONVERT
) -> tuple[list[Voucher], list[ErrorEnvelope]]:
    """Group ``df`` rows by ``voucher_number`` into balanced :class:`Voucher` objects.

    Returns ``(vouchers, errors)`` where ``vouchers`` are only the well-formed, balanced ones; every
    problem (missing field, bad number/date, unbalanced group) is an :class:`ErrorEnvelope`.
    """
    errors: list[ErrorEnvelope] = []
    groups: "OrderedDict[str, dict]" = OrderedDict()

    for i, row in enumerate(df.to_dict("records"), start=1):
        vnum = coerce_str(row.get("order_id")) or coerce_str(row.get("voucher_number"))
        ledger = coerce_str(row.get("ledger_name"))
        if not vnum:
            errors.append(_err(i, "voucher_number", ErrorCode.REQUIRED_MISSING,
                               "Voucher number or Order ID is required — it groups lines into one voucher.", stage))
            continue
        if not ledger:
            errors.append(_err(i, "ledger_name", ErrorCode.REQUIRED_MISSING,
                               "Ledger is required on every voucher line.", stage))
            continue
        amount = _line_amount(row, i, errors, stage)
        if amount is None:
            continue

        # inventory (Sales/Purchase) item line: a stock_item turns this into an ALLINVENTORYENTRIES entry
        stock_item = coerce_str(row.get("stock_item"))
        qty = rate = None
        if stock_item:
            line_obj = _inventory_fields(row, i, stock_item, amount[0], errors, stage)
            if line_obj is None:
                continue
            qty, rate = line_obj

        g = groups.setdefault(_norm(vnum), {
            "vnum": vnum, "first_row": i, "dates": [], "vtypes": [],
            "parties": [], "narration": None, "lines": [],
        })
        # accumulate header candidates per line so we can detect intra-voucher conflicts (never "first wins" silently)
        draw = row.get("date")
        if not is_blank(draw):
            try:
                parsed = coerce_date(draw)
                if parsed is not None:
                    g["dates"].append(parsed)
            except ValueError:
                errors.append(_err(i, "date", ErrorCode.BAD_DATE, f"'{draw}' is not a valid date.", stage))
        vt = coerce_str(row.get("voucher_type"))
        if vt:
            g["vtypes"].append(vt)
        party = coerce_str(row.get("party_ledger"))
        if party:
            g["parties"].append(party)
        if g["narration"] is None:
            g["narration"] = coerce_str(row.get("narration"))
        unit_val = coerce_str(row.get("unit")) if stock_item else None
        if stock_item and not unit_val:  # Tally rejects an inventory line without a unit (verified live)
            errors.append(_err(i, "unit", ErrorCode.INVENTORY_MISMATCH,
                               f"Item '{stock_item}' needs a Unit (e.g. Nos); Tally rejects an inventory "
                               "line without one.", stage))
        # Place of supply tax routing
        shipping_state = coerce_str(row.get("shipping_state"))
        home_state = coerce_str(row.get("home_state"))
        is_tax = ledger.upper() in ["GST", "TAX", "IGST", "CGST", "SGST"]

        if is_tax and shipping_state and home_state:
            from decimal import Decimal
            if shipping_state.lower() != home_state.lower():
                g["lines"].append(VoucherLine(
                    ledger_name="IGST", is_debit=amount[1], amount=amount[0], source_row=i,
                    stock_item=stock_item, quantity=qty, rate=rate, unit=unit_val,
                ))
            else:
                half = (amount[0] / Decimal("2")).quantize(Decimal("0.01"))
                g["lines"].append(VoucherLine(
                    ledger_name="CGST", is_debit=amount[1], amount=half, source_row=i,
                    stock_item=stock_item, quantity=qty, rate=rate, unit=unit_val,
                ))
                g["lines"].append(VoucherLine(
                    ledger_name="SGST", is_debit=amount[1], amount=amount[0] - half, source_row=i,
                    stock_item=stock_item, quantity=qty, rate=rate, unit=unit_val,
                ))
        else:
            g["lines"].append(VoucherLine(
                ledger_name=ledger, is_debit=amount[1], amount=amount[0], source_row=i,
                stock_item=stock_item, quantity=qty, rate=rate, unit=unit_val,
            ))

    vouchers: list[Voucher] = []
    for g in groups.values():
        src, vnum = g["first_row"], g["vnum"]

        distinct_dates = list(dict.fromkeys(g["dates"]))
        if not distinct_dates:
            errors.append(_err(src, "date", ErrorCode.REQUIRED_MISSING, f"Voucher '{vnum}' has no date.", stage))
            continue
        if len(distinct_dates) > 1:
            errors.append(_err(src, "date", ErrorCode.VOUCHER_CONFLICT,
                               f"Voucher '{vnum}' has lines with conflicting dates "
                               f"({', '.join(d.isoformat() for d in distinct_dates)}).", stage))
            continue
        vdate = distinct_dates[0]

        distinct_vtypes = list(dict.fromkeys(g["vtypes"]))
        if not distinct_vtypes:
            errors.append(_err(src, "voucher_type", ErrorCode.REQUIRED_MISSING,
                               f"Voucher '{vnum}' has no voucher type (e.g. Receipt, Payment, Journal).", stage))
            continue
        if len({_norm(v) for v in distinct_vtypes}) > 1:
            errors.append(_err(src, "voucher_type", ErrorCode.VOUCHER_CONFLICT,
                               f"Voucher '{vnum}' has lines with conflicting voucher types "
                               f"({', '.join(distinct_vtypes)}).", stage))
            continue
        vtype = distinct_vtypes[0]

        party = g["parties"][0] if g["parties"] else None
        if len({_norm(p) for p in g["parties"]}) > 1:
            errors.append(_err(src, "party_ledger", ErrorCode.VOUCHER_CONFLICT,
                               f"Voucher '{vnum}' names multiple party ledgers "
                               f"({', '.join(dict.fromkeys(g['parties']))}); using '{party}'.", stage, severity="warning"))

        voucher = Voucher(
            voucher_type=vtype, date=vdate, lines=g["lines"], reference=vnum,
            narration=g["narration"], party_ledger=party, source_row=src,
        )

        diff = voucher.debit_total - voucher.credit_total
        from decimal import Decimal
        if abs(diff) > Decimal("0") and abs(diff) <= Decimal("0.99"):
            is_debit = diff < 0
            voucher.lines.append(
                VoucherLine(
                    ledger_name="Round Off",
                    is_debit=is_debit,
                    amount=abs(diff),
                    source_row=src
                )
            )

        if not voucher.is_balanced:
            errors.append(_err(src, "amount", ErrorCode.VOUCHER_UNBALANCED,
                               f"Voucher '{vnum}' is unbalanced — debits ({voucher.debit_total}) "
                               f"must equal credits ({voucher.credit_total}).", stage))
            continue
        vouchers.append(voucher)

    return vouchers, errors
