"""GST engine: place-of-supply routing + exact-rounding tax computation."""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.pipeline.gst import compute_gst, is_intra_state, normalize_state, state_from_gstin


@pytest.mark.parametrize("a,b", [
    ("Maharashtra", "MH"), ("Maharashtra", "27"), ("MH", "27"), ("karnataka", "Karnataka"),
])
def test_state_aliases_compare_equal(a, b):
    assert normalize_state(a) == normalize_state(b)


def test_place_of_supply():
    assert is_intra_state("Maharashtra", "MH") is True          # same state, different spelling
    assert is_intra_state("27", "Maharashtra") is True          # code vs name
    assert is_intra_state("Karnataka", "Maharashtra") is False  # inter-state
    assert is_intra_state("", "Maharashtra") is None            # unknown -> don't guess
    assert is_intra_state("Maharashtra", None) is None


def test_state_from_gstin():
    assert state_from_gstin("27ABCDE1234F1Z5") == "Maharashtra"
    assert state_from_gstin("29ABCDE1234F1Z5") == "Karnataka"
    assert state_from_gstin("bad") == ""


def test_intra_split_even():
    b = compute_gst(Decimal("1000"), Decimal("18"), intra=True)
    assert (b.cgst, b.sgst, b.igst) == (Decimal("90.00"), Decimal("90.00"), Decimal("0"))
    assert b.total_tax == Decimal("180.00")
    assert [n for n, _ in b.components] == ["CGST", "SGST"]


def test_inter_is_igst():
    b = compute_gst(Decimal("1000"), Decimal("18"), intra=False)
    assert b.igst == Decimal("180.00") and b.cgst == 0 and b.sgst == 0
    assert [n for n, _ in b.components] == ["IGST"]


def test_odd_paisa_split_is_exact():
    # total = 100.28 * 5% = 5.014 -> 5.01; half = 2.505 -> 2.51; other leg absorbs -> 2.50
    b = compute_gst(Decimal("100.28"), Decimal("5"), intra=True)
    assert b.total_tax == Decimal("5.01")
    assert b.cgst + b.sgst == Decimal("5.01")   # no paisa lost or created
    assert b.cgst == Decimal("2.51") and b.sgst == Decimal("2.50")


def test_zero_rate_is_valid_zero_tax():
    b = compute_gst(Decimal("500"), Decimal("0"), intra=True)
    assert b.total_tax == 0 and b.components == []


def test_bad_input_returns_none():
    assert compute_gst("abc", "18", intra=True) is None
    assert compute_gst("100", "n/a", intra=False) is None


def test_messy_numeric_strings_parse():
    b = compute_gst("1,000.00", "12", intra=False)
    assert b.igst == Decimal("120.00")
