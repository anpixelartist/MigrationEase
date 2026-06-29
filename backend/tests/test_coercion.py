"""Tests for the data-coercion layer (the messy-real-world-values edge cases)."""

from decimal import Decimal

import pandas as pd
import pytest

from app.pipeline.coercion import (
    coerce_amount_drcr,
    coerce_bool,
    coerce_decimal,
    is_blank,
    row_to_master,
)
from app.pipeline.entities import EntityType, Ledger


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("1,234.50", Decimal("1234.50")),
        ("(500)", Decimal("-500")),
        ("₹1,00,000", Decimal("100000")),
        ("Rs. 2,500", Decimal("2500")),
        ("-250", Decimal("-250")),
        ("1.5e3", Decimal("1500")),
        ("100-", Decimal("-100")),
        ("+50", Decimal("50")),
        (1000, Decimal("1000")),
        (12.5, Decimal("12.5")),
    ],
)
def test_coerce_decimal_valid(value, expected):
    assert coerce_decimal(value) == expected


@pytest.mark.parametrize("value", ["", "  ", "NA", "n/a", "-", "nil", None])
def test_coerce_decimal_blank_is_none(value):
    assert coerce_decimal(value) is None


@pytest.mark.parametrize("value", ["abc", "12.3.4", "1,2,x", "1.2.3"])
def test_coerce_decimal_invalid_raises(value):
    with pytest.raises(ValueError):
        coerce_decimal(value)


def test_coerce_decimal_only_currency_symbols_is_none():
    # "$$" is just symbols with no number -> treated as blank, not an error
    assert coerce_decimal("$$") is None


@pytest.mark.parametrize(
    ("amount", "drcr", "expected"),
    [
        ("15000", "Cr", (Decimal("15000"), False)),
        ("15000", "Dr", (Decimal("15000"), True)),
        ("-15000", None, (Decimal("15000"), False)),  # negative => credit
        ("15000", None, (Decimal("15000"), True)),  # positive => debit
        ("15000 Cr", None, (Decimal("15000"), False)),  # token in amount string
        ("8,000.50", "credit", (Decimal("8000.50"), False)),
    ],
)
def test_coerce_amount_drcr(amount, drcr, expected):
    assert coerce_amount_drcr(amount, drcr) == expected


def test_coerce_amount_drcr_blank():
    assert coerce_amount_drcr("", None) is None


@pytest.mark.parametrize(
    ("value", "expected"),
    [("Yes", True), ("no", False), ("Y", True), ("N", False), ("1", True), ("0", False),
     ("true", True), ("FALSE", False)],
)
def test_coerce_bool(value, expected):
    assert coerce_bool(value) is expected


def test_coerce_bool_blank_and_invalid():
    assert coerce_bool("") is None
    with pytest.raises(ValueError):
        coerce_bool("maybe")


def test_is_blank_handles_pandas_na():
    assert is_blank(pd.NA) is True
    assert is_blank(float("nan")) is True
    assert is_blank(None) is True
    assert is_blank("x") is False


def test_row_to_master_ledger_credit_opening():
    master, errors = row_to_master(
        EntityType.LEDGER,
        {"name": "Cust", "parent": "Sundry Debtors", "opening_balance": "(15000)"},
        source_row=1,
    )
    assert errors == []
    assert isinstance(master, Ledger)
    assert master.opening_balance == Decimal("15000")
    assert master.opening_is_debit is False  # parenthesized => negative => credit


def test_row_to_master_missing_name_is_error():
    master, errors = row_to_master(EntityType.LEDGER, {"name": "  ", "parent": "X"}, source_row=2)
    assert master is None
    assert errors and errors[0].code == "required_missing"
    assert errors[0].source_row == 2


def test_row_to_master_bad_decimal_captured_not_raised():
    master, errors = row_to_master(
        EntityType.LEDGER,
        {"name": "Cust", "parent": "Sundry Debtors", "credit_limit": "abc"},
        source_row=3,
    )
    # the row still builds; the bad numeric is reported as an error envelope
    assert master is not None
    assert any(e.code == "bad_decimal" and e.source_column == "credit_limit" for e in errors)
