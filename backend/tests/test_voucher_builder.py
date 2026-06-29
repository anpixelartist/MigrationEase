"""Golden tests for the Phase-2 voucher XML builder.

These pin the exact structure + sign convention VERIFIED against live TallyPrime (Test1, 2026-06-26):
a Debit line -> ISDEEMEDPOSITIVE=Yes + NEGATIVE amount; a Credit line -> No + POSITIVE amount; the
voucher must balance. See memory phase2-voucher-spike. Runs in CI without a live Tally.
"""

from datetime import date
from decimal import Decimal

import pytest
from lxml import etree

from app.pipeline.conversion.voucher_builder import (
    build_and_serialize_vouchers,
    build_vouchers_envelope,
    signed_amount,
)
from app.pipeline.entities import TallyAction, Voucher, VoucherLine


def _receipt() -> Voucher:
    # money received: Debit Cash, Credit the customer (mirrors the verified spike)
    return Voucher(
        voucher_type="Receipt",
        date=date(2026, 4, 1),
        narration="Spike receipt",
        lines=[
            VoucherLine(ledger_name="Cash", is_debit=True, amount=Decimal("5000")),
            VoucherLine(ledger_name="Customer 1", is_debit=False, amount=Decimal("5000")),
        ],
    )


def test_sign_convention_debit_negative_credit_positive() -> None:
    dr = VoucherLine(ledger_name="Cash", is_debit=True, amount=Decimal("5000"))
    cr = VoucherLine(ledger_name="Customer 1", is_debit=False, amount=Decimal("5000"))
    assert signed_amount(dr) == "-5000.00"  # Debit -> negative
    assert signed_amount(cr) == "5000.00"  # Credit -> positive


def test_receipt_envelope_structure_matches_verified_format() -> None:
    xml = build_and_serialize_vouchers("Test1", [_receipt()])
    root = etree.fromstring(xml)

    assert root.findtext(".//REPORTNAME") == "Vouchers"
    assert root.findtext(".//SVCURRENTCOMPANY") == "Test1"

    vch = root.find(".//VOUCHER")
    assert vch.get("VCHTYPE") == "Receipt"
    assert vch.get("ACTION") == "Create"
    assert vch.get("OBJVIEW") == "Accounting Voucher View"
    assert vch.findtext("DATE") == "20260401"
    assert vch.findtext("EFFECTIVEDATE") == "20260401"
    assert vch.findtext("VOUCHERTYPENAME") == "Receipt"

    entries = vch.findall("ALLLEDGERENTRIES.LIST")
    assert len(entries) == 2
    cash, cust = entries
    assert cash.findtext("LEDGERNAME") == "Cash"
    assert cash.findtext("ISDEEMEDPOSITIVE") == "Yes"
    assert cash.findtext("AMOUNT") == "-5000.00"
    assert cust.findtext("LEDGERNAME") == "Customer 1"
    assert cust.findtext("ISDEEMEDPOSITIVE") == "No"
    assert cust.findtext("AMOUNT") == "5000.00"


def test_optional_fields_emitted_only_when_set() -> None:
    v = _receipt()
    v.reference = "RV-001"
    v.party_ledger = "Customer 1"
    root = etree.fromstring(build_and_serialize_vouchers("Test1", [v]))
    vch = root.find(".//VOUCHER")
    assert vch.findtext("VOUCHERNUMBER") == "RV-001"
    assert vch.findtext("PARTYLEDGERNAME") == "Customer 1"
    # a bare receipt omits them entirely
    bare = etree.fromstring(build_and_serialize_vouchers("Test1", [_receipt()])).find(".//VOUCHER")
    assert bare.find("VOUCHERNUMBER") is None
    assert bare.find("PARTYLEDGERNAME") is None


def test_no_bom_and_balanced_amounts_sum_to_zero() -> None:
    xml = build_and_serialize_vouchers("Test1", [_receipt()])
    assert not xml.startswith(b"\xef\xbb\xbf")  # Tally chokes on a UTF-8 BOM
    amounts = [Decimal(e.text) for e in etree.fromstring(xml).iter("AMOUNT")]
    assert sum(amounts) == Decimal("0.00")


def test_unbalanced_voucher_is_rejected() -> None:
    bad = Voucher(
        voucher_type="Journal",
        date=date(2026, 4, 1),
        lines=[
            VoucherLine(ledger_name="Customer 1", is_debit=True, amount=Decimal("100")),
            VoucherLine(ledger_name="Vendor 1", is_debit=False, amount=Decimal("200")),
        ],
    )
    assert bad.is_balanced is False
    with pytest.raises(ValueError, match="Unbalanced"):
        build_vouchers_envelope("Test1", [bad])


def test_balance_helpers() -> None:
    v = _receipt()
    assert v.debit_total == Decimal("5000")
    assert v.credit_total == Decimal("5000")
    assert v.is_balanced is True
    # a single-sided "voucher" is not balanced
    one = Voucher(voucher_type="Journal", date=date(2026, 4, 1),
                  lines=[VoucherLine(ledger_name="X", is_debit=True, amount=Decimal("10"))])
    assert one.is_balanced is False


def test_multi_line_journal_three_entries() -> None:
    v = Voucher(
        voucher_type="Journal",
        date=date(2026, 5, 15),
        action=TallyAction.CREATE,
        lines=[
            VoucherLine(ledger_name="Expense A", is_debit=True, amount=Decimal("600")),
            VoucherLine(ledger_name="Expense B", is_debit=True, amount=Decimal("400")),
            VoucherLine(ledger_name="Cash", is_debit=False, amount=Decimal("1000")),
        ],
    )
    assert v.is_balanced
    entries = etree.fromstring(build_and_serialize_vouchers("Test1", [v])).findall(".//ALLLEDGERENTRIES.LIST")
    assert len(entries) == 3
    assert [e.findtext("AMOUNT") for e in entries] == ["-600.00", "-400.00", "1000.00"]
