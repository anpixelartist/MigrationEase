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
from app.pipeline.gst import compute_gst, is_intra_state, state_from_gstin

__all__ = ["rows_to_vouchers", "aggregate_b2c_daily", "settlement_rows_to_vouchers"]

# transaction_type values (case/space-insensitive substring) that mark a sale reversal -> Credit Note.
_REFUND_MARKERS = ("refund", "return", "credit note", "creditnote", "reversal", "chargeback", "cancel")


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
            "gstins": [], "ttypes": [], "gst_mode": False,
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
        gstin = coerce_str(row.get("party_gstin"))
        if gstin:
            g["gstins"].append(gstin)
        ttype = coerce_str(row.get("transaction_type"))
        if ttype:
            g["ttypes"].append(ttype)

        # Always add the row's own ledger line (taxable value for a Sales line).
        g["lines"].append(VoucherLine(
            ledger_name=ledger, is_debit=amount[1], amount=amount[0], source_row=i,
            stock_item=stock_item, quantity=qty, rate=rate, unit=unit_val,
        ))

        # Computed GST: when the row carries a GST rate, synthesize the Output CGST/SGST or IGST legs
        # from the place of supply (buyer state = shipping_state, else the party GSTIN) vs home state.
        rate_raw = row.get("gst_rate")
        if not is_blank(rate_raw):
            buyer_state = coerce_str(row.get("shipping_state")) or state_from_gstin(gstin)
            intra = is_intra_state(buyer_state, coerce_str(row.get("home_state")))
            if intra is None:
                errors.append(_err(i, "gst_rate", ErrorCode.VALIDATION_ERROR,
                                   f"GST rate {rate_raw} given but place of supply is unknown — set "
                                   "Shipping State + Home State (or a party GSTIN). Tax not applied.",
                                   stage, severity="warning"))
            else:
                breakup = compute_gst(amount[0], rate_raw, intra)
                if breakup is None:
                    errors.append(_err(i, "gst_rate", ErrorCode.BAD_DECIMAL,
                                       f"'{rate_raw}' is not a valid GST rate.", stage))
                else:
                    g["gst_mode"] = True
                    for suffix, tax_amt in breakup.components:
                        g["lines"].append(VoucherLine(
                            ledger_name=f"Output {suffix}", is_debit=amount[1], amount=tax_amt, source_row=i,
                        ))

    return _finalize(groups, errors, stage)


def _pos_dec(value: object):
    """A non-negative Decimal, or None (blank/invalid)."""
    if is_blank(value):
        return None
    try:
        d = coerce_decimal(value)
    except ValueError:
        return None
    return abs(d) if d is not None else None


def settlement_rows_to_vouchers(
    df: pd.DataFrame, *, bank_default: str = "Bank", stage: StageName = StageName.CONVERT
) -> tuple[list[Voucher], list[ErrorEnvelope]]:
    """Expand marketplace settlement rows into multi-leg journals (one voucher per settlement).

    Each row is a settlement of a gross sale where the marketplace deducts commission, fees and TCS
    and deposits the net to a bank. The entry clears the marketplace receivable::

        Dr Bank (net)  Dr Commission  Dr Marketplace Fees  Dr TCS Receivable   =   Cr <Marketplace> (gross)

    Columns: ``amount`` = gross, ``commission`` / ``marketplace_fee`` / ``tcs`` = deductions,
    ``ledger_name`` = the bank/deposit ledger (default "Bank"), ``party_ledger`` = the marketplace.
    """
    from decimal import Decimal

    errors: list[ErrorEnvelope] = []
    vouchers: list[Voucher] = []
    for i, row in enumerate(df.to_dict("records"), start=1):
        sid = coerce_str(row.get("order_id")) or coerce_str(row.get("voucher_number"))
        if not sid:
            errors.append(_err(i, "voucher_number", ErrorCode.REQUIRED_MISSING,
                               "Each settlement needs an order/settlement id.", stage))
            continue
        gross = _pos_dec(row.get("amount"))
        if gross is None or gross <= 0:
            errors.append(_err(i, "amount", ErrorCode.REQUIRED_MISSING,
                               f"Settlement '{sid}' needs a positive gross amount.", stage))
            continue
        try:
            vdate = coerce_date(row.get("date"))
        except ValueError:
            vdate = None
        if vdate is None:
            errors.append(_err(i, "date", ErrorCode.REQUIRED_MISSING, f"Settlement '{sid}' has no date.", stage))
            continue

        comm = _pos_dec(row.get("commission")) or Decimal("0")
        fee = _pos_dec(row.get("marketplace_fee")) or Decimal("0")
        tcs = _pos_dec(row.get("tcs")) or Decimal("0")
        net = gross - (comm + fee + tcs)
        if net < 0:
            errors.append(_err(i, "amount", ErrorCode.VALIDATION_ERROR,
                               f"Settlement '{sid}': deductions ({comm + fee + tcs}) exceed gross ({gross}).", stage))
            continue

        party = coerce_str(row.get("party_ledger")) or "Marketplace"
        bank = coerce_str(row.get("ledger_name")) or bank_default
        lines = [VoucherLine(ledger_name=party, is_debit=False, amount=gross, source_row=i)]
        if net > 0:
            lines.append(VoucherLine(ledger_name=bank, is_debit=True, amount=net, source_row=i))
        if comm > 0:
            lines.append(VoucherLine(ledger_name="Commission", is_debit=True, amount=comm, source_row=i))
        if fee > 0:
            lines.append(VoucherLine(ledger_name="Marketplace Fees", is_debit=True, amount=fee, source_row=i))
        if tcs > 0:
            lines.append(VoucherLine(ledger_name="TCS Receivable", is_debit=True, amount=tcs, source_row=i))

        vtype = coerce_str(row.get("voucher_type")) or "Journal"
        voucher = Voucher(voucher_type=vtype, date=vdate, lines=lines, reference=sid,
                          party_ledger=party, narration=f"Marketplace settlement {sid}", source_row=i)
        if not voucher.is_balanced:
            errors.append(_err(i, "amount", ErrorCode.VOUCHER_UNBALANCED,
                               f"Settlement '{sid}' did not balance.", stage))
            continue
        vouchers.append(voucher)
    return vouchers, errors


def aggregate_b2c_daily(vouchers: list[Voucher], b2c_ledger: str = "B2C Sales") -> list[Voucher]:
    """Consolidate B2C sales (no buyer GSTIN) into one summary voucher per day (GSTR-1 B2C-Others).

    B2B invoices (valid ``party_gstin``), Credit Notes, and non-Sales vouchers pass through unchanged.
    Each daily summary sums every non-party leg (Sales income + Output taxes) across that day's B2C
    sales and posts the net to a single ``b2c_ledger`` account, so per-customer detail collapses while
    the tax totals stay exact.
    """
    from collections import defaultdict
    from decimal import Decimal

    passthrough: list[Voucher] = []
    b2c_by_date: "dict" = defaultdict(list)
    for v in vouchers:
        is_sale = _norm(v.voucher_type) == _norm("Sales")
        is_b2c = is_sale and not (v.party_gstin and len(v.party_gstin.strip()) == 15)
        if is_b2c:
            b2c_by_date[v.date].append(v)
        else:
            passthrough.append(v)

    summaries: list[Voucher] = []
    for vdate, day in sorted(b2c_by_date.items()):
        totals: "dict[tuple[str, bool], Decimal]" = defaultdict(lambda: Decimal("0"))
        for v in day:
            for ln in v.lines:
                if v.party_ledger and ln.ledger_name == v.party_ledger:
                    continue  # drop per-customer receivable; replaced by the single B2C leg
                totals[(ln.ledger_name, ln.is_debit)] += ln.amount
        lines = [VoucherLine(ledger_name=n, is_debit=dr, amount=amt)
                 for (n, dr), amt in totals.items() if amt > 0]
        dr = sum((a for (n, d), a in totals.items() if d), Decimal("0"))
        cr = sum((a for (n, d), a in totals.items() if not d), Decimal("0"))
        bal = cr - dr
        if abs(bal) > 0:
            lines.append(VoucherLine(ledger_name=b2c_ledger, is_debit=bal > 0, amount=abs(bal)))
        summaries.append(Voucher(
            voucher_type="Sales", date=vdate, lines=lines, party_ledger=b2c_ledger,
            reference=f"B2C-{vdate.isoformat()}",
            narration=f"B2C daily summary — {len(day)} order(s)",
        ))
    return passthrough + summaries


def _finalize(groups, errors, stage):
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
        from decimal import Decimal

        vtype = distinct_vtypes[0]
        # A refund/return row becomes a Credit Note (sale reversal), keeping the order id for linkage.
        is_refund = any(any(m in _norm(t) for m in _REFUND_MARKERS) for t in g["ttypes"])
        narration = g["narration"]
        if is_refund:
            vtype = "Credit Note"
            tag = f"Refund of order {vnum}"
            narration = f"{tag}. {narration}" if narration else tag

        party = g["parties"][0] if g["parties"] else None
        if len({_norm(p) for p in g["parties"]}) > 1:
            errors.append(_err(src, "party_ledger", ErrorCode.VOUCHER_CONFLICT,
                               f"Voucher '{vnum}' names multiple party ledgers "
                               f"({', '.join(dict.fromkeys(g['parties']))}); using '{party}'.", stage, severity="warning"))

        lines = list(g["lines"])

        def _dr_cr(ls: list[VoucherLine]) -> tuple[Decimal, Decimal]:
            return (sum((x.amount for x in ls if x.is_debit), Decimal("0")),
                    sum((x.amount for x in ls if not x.is_debit), Decimal("0")))

        # Computed-tax mode: synthesize the party balancing leg (Customer Dr = taxable + tax on a sale).
        if g["gst_mode"] and party:
            dr, cr = _dr_cr(lines)
            bal = cr - dr  # > 0 -> the party owes (Debit); < 0 -> party is Credit
            if abs(bal) > Decimal("0.99"):
                lines.append(VoucherLine(ledger_name=party, is_debit=bal > 0, amount=abs(bal), source_row=src))

        # Residual sub-rupee imbalance -> Round Off leg.
        dr, cr = _dr_cr(lines)
        diff = dr - cr
        if Decimal("0") < abs(diff) <= Decimal("0.99"):
            lines.append(VoucherLine(ledger_name="Round Off", is_debit=diff < 0, amount=abs(diff), source_row=src))

        # A Credit Note reverses the synthesized sale: flip each leg's side (balance is preserved).
        if is_refund and g["gst_mode"]:
            lines = [ln.model_copy(update={"is_debit": not ln.is_debit}) for ln in lines]

        gstin = next((x for x in g["gstins"] if len(x.strip()) == 15), None)
        voucher = Voucher(
            voucher_type=vtype, date=vdate, lines=lines, reference=vnum,
            narration=narration, party_ledger=party, party_gstin=gstin, source_row=src,
        )

        if not voucher.is_balanced:
            errors.append(_err(src, "amount", ErrorCode.VOUCHER_UNBALANCED,
                               f"Voucher '{vnum}' is unbalanced — debits ({voucher.debit_total}) "
                               f"must equal credits ({voucher.credit_total}).", stage))
            continue
        vouchers.append(voucher)

    return vouchers, errors
