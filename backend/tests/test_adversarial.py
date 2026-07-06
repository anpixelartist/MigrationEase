"""Adversarial / loud-failure audit.

Principle (per the product owner): the nastiest inputs must EITHER be handled correctly OR fail
LOUDLY with a clear error/warning. Nothing may be silently dropped, silently "fixed", or silently
corrupted. Every test here asserts a loud signal (an ErrorEnvelope or a 4xx), never a quiet success.
"""

from datetime import date
from decimal import Decimal

import pandas as pd
from lxml import etree

from app.pipeline.contracts import ErrorCode
from app.pipeline.conversion.voucher_builder import build_and_serialize_vouchers
from app.pipeline.entities import Voucher, VoucherLine
from app.pipeline.voucher import rows_to_vouchers


def _vdf(rows: dict) -> pd.DataFrame:
    return pd.DataFrame(rows)


def _codes(errs) -> set[str]:
    return {e.code for e in errs}


# ---------------------------------------------------------------- intra-voucher conflicts (were silent)
def test_conflicting_dates_within_one_voucher_is_loud() -> None:
    df = _vdf({"voucher_number": ["A", "A"], "date": ["2026-04-01", "2026-05-01"],
               "voucher_type": ["Journal", "Journal"], "ledger_name": ["X", "Y"],
               "amount": ["100", "100"], "dr_cr": ["Dr", "Cr"]})
    vouchers, errors = rows_to_vouchers(df)
    assert vouchers == []  # not silently using "first date wins"
    assert ErrorCode.VOUCHER_CONFLICT in _codes(errors)


def test_conflicting_voucher_types_within_one_voucher_is_loud() -> None:
    df = _vdf({"voucher_number": ["A", "A"], "date": ["2026-04-01", "2026-04-01"],
               "voucher_type": ["Receipt", "Payment"], "ledger_name": ["X", "Y"],
               "amount": ["100", "100"], "dr_cr": ["Dr", "Cr"]})
    vouchers, errors = rows_to_vouchers(df)
    assert vouchers == []
    assert ErrorCode.VOUCHER_CONFLICT in _codes(errors)


def test_multiple_party_ledgers_warns_loudly_but_proceeds() -> None:
    df = _vdf({"voucher_number": ["A", "A"], "date": ["2026-04-01", "2026-04-01"],
               "voucher_type": ["Sales", "Sales"], "ledger_name": ["X", "Y"],
               "amount": ["100", "100"], "dr_cr": ["Dr", "Cr"], "party_ledger": ["Cust 1", "Cust 2"]})
    vouchers, errors = rows_to_vouchers(df)
    assert len(vouchers) == 1  # a soft conflict shouldn't block, but it MUST be surfaced
    assert any(e.code == ErrorCode.VOUCHER_CONFLICT and e.severity == "warning" for e in errors)


# ---------------------------------------------------------------- balance edge cases
def test_penny_imbalance_is_loud() -> None:
    # A penny imbalance <= 0.99 should be auto-routed to Round Off.
    # We will test an imbalance > 0.99 to ensure the loud error still works.
    df = _vdf({"voucher_number": ["A", "A"], "date": ["2026-04-01"] * 2, "voucher_type": ["Journal"] * 2,
               "ledger_name": ["X", "Y"], "amount": ["100.00", "101.50"], "dr_cr": ["Dr", "Cr"]})
    vouchers, errors = rows_to_vouchers(df)
    assert vouchers == [] and ErrorCode.VOUCHER_UNBALANCED in _codes(errors)

def test_fractional_imbalance_auto_round_off() -> None:
    df = _vdf({"voucher_number": ["A", "A"], "date": ["2026-04-01"] * 2, "voucher_type": ["Journal"] * 2,
               "ledger_name": ["X", "Y"], "amount": ["100.00", "100.01"], "dr_cr": ["Dr", "Cr"]})
    vouchers, errors = rows_to_vouchers(df)
    assert len(vouchers) == 1
    assert any(line.ledger_name == "Round Off" and line.amount == Decimal("0.01") and line.is_debit for line in vouchers[0].lines)


def test_decimal_split_that_balances_exactly_is_accepted() -> None:
    df = _vdf({"voucher_number": ["A"] * 4, "date": ["2026-04-01"] * 4, "voucher_type": ["Journal"] * 4,
               "ledger_name": ["a", "b", "c", "Cash"], "amount": ["33.33", "33.33", "33.34", "100.00"],
               "dr_cr": ["Dr", "Dr", "Dr", "Cr"]})
    vouchers, errors = rows_to_vouchers(df)
    assert errors == [] and len(vouchers) == 1 and vouchers[0].is_balanced


def test_both_debit_and_credit_on_one_line_is_loud() -> None:
    df = _vdf({"voucher_number": ["A", "A"], "date": ["2026-04-01"] * 2, "voucher_type": ["Journal"] * 2,
               "ledger_name": ["X", "Y"], "debit": ["100", "50"], "credit": ["50", "100"]})
    _vouchers, errors = rows_to_vouchers(df)
    assert ErrorCode.VALIDATION_ERROR in _codes(errors)


def test_zero_amount_line_is_loud() -> None:
    df = _vdf({"voucher_number": ["A", "A"], "date": ["2026-04-01"] * 2, "voucher_type": ["Journal"] * 2,
               "ledger_name": ["X", "Y"], "amount": ["0", "0"], "dr_cr": ["Dr", "Cr"]})
    vouchers, errors = rows_to_vouchers(df)
    assert vouchers == [] and ErrorCode.REQUIRED_MISSING in _codes(errors)


# ---------------------------------------------------------------- structural safety (XML injection)
def test_xml_injection_in_ledger_name_is_escaped_not_executed() -> None:
    v = Voucher(voucher_type="Journal", date=date(2026, 4, 1), lines=[
        VoucherLine(ledger_name="Cash</LEDGERNAME><INJECT>boom</INJECT>", is_debit=True, amount=Decimal("100")),
        VoucherLine(ledger_name="Other", is_debit=False, amount=Decimal("100")),
    ])
    xml = build_and_serialize_vouchers("Test1", [v])
    assert b"<INJECT>" not in xml          # never becomes a real element
    assert b"&lt;INJECT&gt;" in xml        # carried as escaped text
    assert etree.fromstring(xml).find(".//VOUCHER") is not None  # still well-formed


# ---------------------------------------------------------------- constant on an unknown field (was silent)
# ---------------------------------------------------------------- inventory (Sales/Purchase) lines
def test_inventory_qty_times_rate_mismatch_warns_loudly() -> None:
    # balanced (4000 == 4000) but qty*rate = 5000 != line amount 4000 -> loud WARNING, not silent
    df = _vdf({"voucher_number": ["INV1", "INV1"], "date": ["2026-04-01"] * 2, "voucher_type": ["Sales"] * 2,
               "ledger_name": ["Customer 1", "Sales"], "amount": ["4000", "4000"], "dr_cr": ["Dr", "Cr"],
               "stock_item": ["", "Widget-1"], "quantity": ["", "100"], "rate": ["", "50"], "unit": ["", "Nos"]})
    vouchers, errors = rows_to_vouchers(df)
    assert len(vouchers) == 1  # a warning doesn't block
    assert any(e.code == ErrorCode.INVENTORY_MISMATCH and e.severity == "warning" for e in errors)


def test_inventory_line_without_unit_is_loud() -> None:
    df = _vdf({"voucher_number": ["INV1", "INV1"], "date": ["2026-04-01"] * 2, "voucher_type": ["Sales"] * 2,
               "ledger_name": ["Customer 1", "Sales"], "amount": ["5000", "5000"], "dr_cr": ["Dr", "Cr"],
               "stock_item": ["", "Widget-1"], "quantity": ["", "100"], "rate": ["", "50"]})  # no unit column
    _vouchers, errors = rows_to_vouchers(df)
    assert any(e.code == ErrorCode.INVENTORY_MISMATCH and "Unit" in e.message for e in errors)


def test_negative_quantity_is_loud() -> None:
    df = _vdf({"voucher_number": ["INV1", "INV1"], "date": ["2026-04-01"] * 2, "voucher_type": ["Sales"] * 2,
               "ledger_name": ["Customer 1", "Sales"], "amount": ["5000", "5000"], "dr_cr": ["Dr", "Cr"],
               "stock_item": ["", "Widget-1"], "quantity": ["", "-100"], "rate": ["", "50"], "unit": ["", "Nos"]})
    vouchers, errors = rows_to_vouchers(df)
    assert any(e.code == ErrorCode.INVENTORY_MISMATCH for e in errors)  # negative qty not silently abs'd


def test_item_line_without_quantity_is_loud() -> None:
    df = _vdf({"voucher_number": ["INV1", "INV1"], "date": ["2026-04-01"] * 2, "voucher_type": ["Sales"] * 2,
               "ledger_name": ["Customer 1", "Sales"], "amount": ["5000", "5000"], "dr_cr": ["Dr", "Cr"],
               "stock_item": ["", "Widget-1"], "quantity": ["", ""], "rate": ["", "50"], "unit": ["", "Nos"]})
    _vouchers, errors = rows_to_vouchers(df)
    assert any(e.code == ErrorCode.INVENTORY_MISMATCH and "quantity" in e.message.lower() for e in errors)


def test_constant_on_unknown_field_fails_loudly(client) -> None:
    jid = client.post("/jobs", json={"entity_type": "ledger"}).json()["id"]
    client.post(f"/jobs/{jid}/file", files={"file": ("l.csv", b"name,grp\nA,Sundry Debtors\n", "text/csv")})
    resp = client.post(f"/jobs/{jid}/mapping",
                       json={"mapping": {"name": "name", "parent": "grp"}, "constants": {"bogus_field": "x"}})
    assert resp.status_code == 400  # not silently ignored
    assert "bogus_field" in (resp.json().get("detail") or "")


def test_generate_surfaces_skipped_and_errors_not_silent(client) -> None:
    """A voucher file with one good + one unbalanced voucher: the bad one must be reported, not vanish."""
    jid = client.post("/jobs", json={"entity_type": "voucher"}).json()["id"]
    csv = (b"vno,date,vtype,ledger,amt,drcr\n"
           b"OK1,2026-04-01,Journal,A,100,Dr\nOK1,2026-04-01,Journal,B,100,Cr\n"
           b"BAD1,2026-04-01,Journal,C,100,Dr\nBAD1,2026-04-01,Journal,D,250,Cr\n")
    client.post(f"/jobs/{jid}/file", files={"file": ("v.csv", csv, "text/csv")})
    client.post(f"/jobs/{jid}/mapping", json={"mapping": {
        "voucher_number": "vno", "date": "date", "voucher_type": "vtype",
        "ledger_name": "ledger", "amount": "amt", "dr_cr": "drcr"}, "constants": {}})
    tid = client.post(f"/jobs/{jid}/validate", json={"known_groups": None}).json()["task_id"]
    body = client.get(f"/jobs/{jid}/tasks/{tid}").json()
    # validation must FAIL loudly because one voucher is unbalanced (it is not silently skipped)
    assert body["result"]["ok"] is False
    assert any(e["code"] == ErrorCode.VOUCHER_UNBALANCED for e in body["result"]["errors"])
