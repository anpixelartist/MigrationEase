"""Build the Tally "Vouchers" import ENVELOPE from canonical Voucher objects (Phase 2).

Load-bearing Tally rules encoded here — all VERIFIED against live TallyPrime (company "Test1",
2026-06-26; see memory phase2-voucher-spike):
  * REPORTNAME is "Vouchers" (not the masters' "All Masters").
  * AMOUNT sign convention is the INVERSE of master OPENINGBALANCE:
      Debit  -> ISDEEMEDPOSITIVE=Yes, AMOUNT negative
      Credit -> ISDEEMEDPOSITIVE=No,  AMOUNT positive
  * The voucher must balance (sum of signed amounts == 0). Tally rejects an unbalanced voucher with
    <EXCEPTIONS>1</EXCEPTIONS>; we fail fast here instead of shipping a doomed envelope.
  * DATE is YYYYMMDD and must fall inside the company's accounting period (caller validates the range).
  * Build with lxml element construction (auto-escaping); never string/Jinja templating.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from lxml import etree

from app.pipeline.conversion.serialize import serialize
from app.pipeline.entities import Voucher, VoucherLine

UDF_NSMAP = {"UDF": "TallyUDF"}
ACCOUNTING_VIEW = "Accounting Voucher View"
INVOICE_VIEW = "Invoice Voucher View"  # inventory (Sales/Purchase) vouchers


def _amount(value: Decimal | int | float) -> str:
    return str(Decimal(value).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def signed_amount(line: VoucherLine) -> str:
    """Encode a line into Tally's signed voucher AMOUNT (Debit negative, Credit positive)."""
    magnitude = abs(Decimal(line.amount))
    return _amount(-magnitude if line.is_debit else magnitude)


def _fmt_date(value: date) -> str:
    return value.strftime("%Y%m%d")


def _num(value: Decimal) -> str:
    """Plain decimal string (no exponent, no forced trailing zeros) for quantities/rates."""
    return format(Decimal(value), "f")


def _sub(parent: etree._Element, tag: str, text: str | None = None,
         attrib: dict[str, str] | None = None) -> etree._Element:
    el = etree.SubElement(parent, tag, attrib or {})
    if text is not None:
        el.text = text
    return el


def _build_voucher(requestdata: etree._Element, voucher: Voucher) -> None:
    if not voucher.is_balanced:
        raise ValueError(
            f"Unbalanced {voucher.voucher_type} voucher (Dr {voucher.debit_total} != Cr {voucher.credit_total}); "
            "Tally would reject it."
        )
    # an inventory voucher must use the Invoice view + LEDGERENTRIES.LIST; an accounting voucher uses
    # the Accounting view + ALLLEDGERENTRIES.LIST (both verified live on test123, 2026-06-26).
    has_inventory = any(line.is_inventory for line in voucher.lines)
    msg = etree.SubElement(requestdata, "TALLYMESSAGE", nsmap=UDF_NSMAP)
    vch = _sub(msg, "VOUCHER", attrib={
        "VCHTYPE": voucher.voucher_type,
        "ACTION": voucher.action.value,
        "OBJVIEW": INVOICE_VIEW if has_inventory else ACCOUNTING_VIEW,
    })
    if voucher.guid:
        _sub(vch, "GUID", voucher.guid)
    _sub(vch, "DATE", _fmt_date(voucher.date))
    _sub(vch, "EFFECTIVEDATE", _fmt_date(voucher.date))
    _sub(vch, "VOUCHERTYPENAME", voucher.voucher_type)
    if voucher.reference:
        _sub(vch, "VOUCHERNUMBER", voucher.reference)
    if voucher.party_ledger:
        _sub(vch, "PARTYLEDGERNAME", voucher.party_ledger)
    if voucher.narration:
        _sub(vch, "NARRATION", voucher.narration)
    ledger_tag = "LEDGERENTRIES.LIST" if has_inventory else "ALLLEDGERENTRIES.LIST"
    for line in voucher.lines:
        if line.is_inventory:
            _build_inventory_entry(vch, line)
        else:
            entry = _sub(vch, ledger_tag)
            _sub(entry, "LEDGERNAME", line.ledger_name)
            _sub(entry, "ISDEEMEDPOSITIVE", "Yes" if line.is_debit else "No")
            _sub(entry, "AMOUNT", signed_amount(line))


def _build_inventory_entry(vch: etree._Element, line: VoucherLine) -> None:
    """Emit an ALLINVENTORYENTRIES.LIST for a stock-item line (Sales/Purchase).

    NOTE: this inventory structure (STOCKITEMNAME / RATE / ACTUALQTY+BILLEDQTY / nested
    ACCOUNTINGALLOCATIONS) follows the documented Tally format but is **pending live-Tally spike
    confirmation** (qty/rate unit-suffix, GST auto-compute) once a company is open. The accounting
    side (the nested allocation amount + sign) is identical to the verified ledger-entry path.
    """
    yn = "Yes" if line.is_debit else "No"
    signed = signed_amount(line)
    # Tally needs the unit IN the qty/rate strings ("100 Nos", "50.00/Nos") — bare numbers are rejected
    # (verified live test123). The unit comes from the item line's unit column (or the item's base unit).
    unit = (line.unit or "").strip()
    inv = _sub(vch, "ALLINVENTORYENTRIES.LIST")
    _sub(inv, "STOCKITEMNAME", line.stock_item)
    _sub(inv, "ISDEEMEDPOSITIVE", yn)
    if line.rate is not None:
        _sub(inv, "RATE", f"{_amount(line.rate)}/{unit}" if unit else _num(line.rate))
    _sub(inv, "AMOUNT", signed)
    if line.quantity is not None:
        # billed quantity is the positive magnitude; in/out direction comes from the voucher type +
        # ISDEEMEDPOSITIVE (verified: a negative qty made Tally reject the inventory entry).
        qty = f"{_num(abs(line.quantity))} {unit}".strip() if unit else _num(abs(line.quantity))
        _sub(inv, "ACTUALQTY", qty)
        _sub(inv, "BILLEDQTY", qty)
    alloc = _sub(inv, "ACCOUNTINGALLOCATIONS.LIST")
    _sub(alloc, "LEDGERNAME", line.ledger_name)
    _sub(alloc, "ISDEEMEDPOSITIVE", yn)
    _sub(alloc, "AMOUNT", signed)


def build_vouchers_envelope(company: str, vouchers: Sequence[Voucher]) -> etree._Element:
    """Assemble the full ``<ENVELOPE>`` for a "Vouchers" import.

    ``company`` selects the target Tally company via SVCURRENTCOMPANY and MUST match the company
    currently open in Tally (the bridge enforces this just-in-time).
    """
    env = etree.Element("ENVELOPE")
    header = _sub(env, "HEADER")
    _sub(header, "TALLYREQUEST", "Import Data")

    body = _sub(env, "BODY")
    importdata = _sub(body, "IMPORTDATA")

    reqdesc = _sub(importdata, "REQUESTDESC")
    _sub(reqdesc, "REPORTNAME", "Vouchers")
    static = _sub(reqdesc, "STATICVARIABLES")
    _sub(static, "SVCURRENTCOMPANY", company)

    reqdata = _sub(importdata, "REQUESTDATA")
    for voucher in vouchers:
        _build_voucher(reqdata, voucher)
    return env


def build_and_serialize_vouchers(company: str, vouchers: Sequence[Voucher]) -> bytes:
    """Convenience: build the voucher envelope and serialize it to Tally-ready bytes."""
    return serialize(build_vouchers_envelope(company, vouchers))
