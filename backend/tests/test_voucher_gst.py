"""Computed GST + refund→credit-note in the voucher builder (place of supply, party leg synthesis)."""

from __future__ import annotations

from decimal import Decimal

import pandas as pd

from app.pipeline.voucher import (
    aggregate_b2c_daily,
    rows_to_vouchers,
    settlement_rows_to_vouchers,
)


def _legs(v):
    return {ln.ledger_name: ("Dr" if ln.is_debit else "Cr", ln.amount) for ln in v.lines}


def _sale_row(**over):
    row = {
        "order_id": "1001", "date": "2026-04-01", "voucher_type": "Sales",
        "ledger_name": "Sales", "amount": "1000", "dr_cr": "Cr",
        "party_ledger": "Customer A", "gst_rate": "18",
        "shipping_state": "MH", "home_state": "MH",
    }
    row.update(over)
    return row


def test_intra_state_sale_builds_cgst_sgst_and_party_leg():
    v, errs = rows_to_vouchers(pd.DataFrame([_sale_row()]))
    assert len(v) == 1 and not [e for e in errs if e.severity == "error"]
    legs = _legs(v[0])
    assert legs["Sales"] == ("Cr", Decimal("1000"))
    assert legs["Output CGST"] == ("Cr", Decimal("90.00"))
    assert legs["Output SGST"] == ("Cr", Decimal("90.00"))
    assert legs["Customer A"] == ("Dr", Decimal("1180.00"))  # taxable + tax
    assert v[0].is_balanced and v[0].voucher_type == "Sales"


def test_inter_state_sale_builds_igst():
    v, _ = rows_to_vouchers(pd.DataFrame([_sale_row(shipping_state="Karnataka")]))
    legs = _legs(v[0])
    assert legs["Output IGST"] == ("Cr", Decimal("180.00"))
    assert "Output CGST" not in legs
    assert legs["Customer A"] == ("Dr", Decimal("1180.00"))


def test_place_of_supply_from_party_gstin_when_no_shipping_state():
    # No shipping_state, but a Karnataka GSTIN (29...) vs home MH -> inter-state IGST.
    row = _sale_row(shipping_state="", party_gstin="29ABCDE1234F1Z5")
    v, _ = rows_to_vouchers(pd.DataFrame([row]))
    assert _legs(v[0]).get("Output IGST") == ("Cr", Decimal("180.00"))


def test_refund_becomes_flipped_credit_note():
    v, _ = rows_to_vouchers(pd.DataFrame([_sale_row(transaction_type="refund")]))
    assert v[0].voucher_type == "Credit Note"
    legs = _legs(v[0])
    # every leg reversed vs a sale
    assert legs["Sales"] == ("Dr", Decimal("1000"))
    assert legs["Output CGST"] == ("Dr", Decimal("90.00"))
    assert legs["Customer A"] == ("Cr", Decimal("1180.00"))
    assert v[0].is_balanced
    assert "Refund of order 1001" in (v[0].narration or "")


def test_multi_lineitem_order_sums_into_one_voucher():
    rows = [_sale_row(ledger_name="Sales", amount="1000"),
            _sale_row(ledger_name="Sales", amount="500")]  # same order_id 1001
    v, _ = rows_to_vouchers(pd.DataFrame(rows))
    assert len(v) == 1
    legs = v[0].lines
    # 2 sales legs + 2 tax legs each row + 1 party leg
    party = [ln for ln in legs if ln.ledger_name == "Customer A"]
    assert len(party) == 1 and party[0].amount == Decimal("1770.00")  # (1000+500)*1.18


def test_gst_rate_without_place_of_supply_warns_and_skips_tax():
    v, errs = rows_to_vouchers(pd.DataFrame([_sale_row(shipping_state="", home_state="", party_gstin="")]))
    legs = _legs(v[0]) if v else {}
    assert "Output CGST" not in legs and "Output IGST" not in legs
    assert any(e.severity == "warning" and "place of supply" in e.message for e in errs)


def test_b2c_daily_summary_aggregates_and_keeps_b2b_separate():
    rows = [
        _sale_row(order_id="B1", party_ledger="Cust1", amount="1000"),                       # B2C, day1
        _sale_row(order_id="B2", party_ledger="Cust2", amount="2000"),                       # B2C, day1
        _sale_row(order_id="B3", party_ledger="Big Co", amount="5000",
                  party_gstin="27ABCDE1234F1Z5"),                                             # B2B, day1
        _sale_row(order_id="B4", party_ledger="Cust3", amount="500", date="2026-04-02"),      # B2C, day2
    ]
    vouchers, _ = rows_to_vouchers(pd.DataFrame(rows))
    out = aggregate_b2c_daily(vouchers)

    b2b = [v for v in out if v.party_gstin]
    summaries = [v for v in out if v.party_ledger == "B2C Sales"]
    assert len(b2b) == 1                              # the B2B invoice passed through untouched
    assert len(summaries) == 2                        # one per day
    day1 = next(v for v in summaries if v.reference == "B2C-2026-04-01")
    sales = sum(ln.amount for ln in day1.lines if ln.ledger_name == "Sales")
    assert sales == Decimal("3000")                  # 1000 + 2000 merged
    assert day1.is_balanced
    assert not any(ln.ledger_name in ("Cust1", "Cust2") for ln in day1.lines)  # per-customer detail dropped


def test_marketplace_settlement_multi_leg():
    row = {
        "order_id": "S1", "date": "2026-04-05", "amount": "1000",
        "commission": "120", "marketplace_fee": "40", "tcs": "10",
        "party_ledger": "Amazon", "ledger_name": "HDFC Bank",
    }
    v, errs = settlement_rows_to_vouchers(pd.DataFrame([row]))
    assert len(v) == 1 and not [e for e in errs if e.severity == "error"]
    legs = _legs(v[0])
    assert legs["Amazon"] == ("Cr", Decimal("1000"))          # marketplace receivable cleared
    assert legs["HDFC Bank"] == ("Dr", Decimal("830"))        # net deposit (1000 - 170)
    assert legs["Commission"] == ("Dr", Decimal("120"))
    assert legs["Marketplace Fees"] == ("Dr", Decimal("40"))
    assert legs["TCS Receivable"] == ("Dr", Decimal("10"))
    assert v[0].is_balanced


def test_settlement_rejects_deductions_over_gross():
    row = {"order_id": "S2", "date": "2026-04-05", "amount": "100", "commission": "200",
           "party_ledger": "Amazon", "ledger_name": "Bank"}
    v, errs = settlement_rows_to_vouchers(pd.DataFrame([row]))
    assert not v and any("exceed gross" in e.message for e in errs)
