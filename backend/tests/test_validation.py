"""Tests for the Pandera validation stage and its ErrorEnvelope adapter."""

import pandas as pd

from app.pipeline.contracts import ErrorCode
from app.pipeline.entities import EntityType
from app.pipeline.validation import validate


def _codes(result) -> set[str]:
    return {e.code for e in result.errors}


def test_valid_ledgers_pass() -> None:
    df = pd.DataFrame({"name": ["Cust A", "Cust B"], "parent": ["Sundry Debtors", "Sundry Debtors"]})
    result = validate(df, EntityType.LEDGER, known_groups=["Sundry Debtors"])
    assert result.ok is True
    assert result.errors == []


def test_missing_name_is_error() -> None:
    df = pd.DataFrame({"name": ["Cust A", None], "parent": ["Sundry Debtors", "Sundry Debtors"]})
    result = validate(df, EntityType.LEDGER, known_groups=["Sundry Debtors"])
    assert result.ok is False
    assert ErrorCode.REQUIRED_MISSING in _codes(result)


def test_duplicate_name_is_error() -> None:
    df = pd.DataFrame({"name": ["Cust A", "Cust A"], "parent": ["Sundry Debtors", "Sundry Debtors"]})
    result = validate(df, EntityType.LEDGER, known_groups=["Sundry Debtors"])
    assert result.ok is False
    assert ErrorCode.DUPLICATE_IN_FILE in _codes(result)


def test_unknown_group_is_error_with_fuzzy_suggestion() -> None:
    df = pd.DataFrame({"name": ["Cust A"], "parent": ["Sundry Debtrs"]})
    result = validate(df, EntityType.LEDGER, known_groups=["Sundry Debtors", "Bank Accounts"])
    assert result.ok is False
    assert ErrorCode.UNKNOWN_GROUP in _codes(result)
    env = next(e for e in result.errors if e.code == ErrorCode.UNKNOWN_GROUP)
    assert env.source_row == 1
    assert env.suggestion is not None and "Sundry Debtors" in env.suggestion


def test_unit_name_with_space_is_error() -> None:
    # Tally rejects unit symbols with whitespace ("BAD UNIT NAME"); catch it at validation.
    df = pd.DataFrame({"name": ["QA Nos", "Nos", "Kg"]})
    result = validate(df, EntityType.UNIT)
    assert result.ok is False
    assert ErrorCode.BAD_UNIT_NAME in _codes(result)
    env = next(e for e in result.errors if e.code == ErrorCode.BAD_UNIT_NAME)
    assert env.source_row == 1  # only the spaced name


def test_bad_gstin_is_warning_only() -> None:
    df = pd.DataFrame(
        {"name": ["Cust A"], "parent": ["Sundry Debtors"], "gstin": ["NOT-A-GSTIN"]}
    )
    result = validate(df, EntityType.LEDGER, known_groups=["Sundry Debtors"])
    assert ErrorCode.BAD_GSTIN in _codes(result)
    assert result.ok is True  # warnings do not block


def test_decimal_parsing() -> None:
    ok_df = pd.DataFrame(
        {"name": ["Cust A"], "parent": ["Sundry Debtors"], "opening_balance": ["12,000.50"]}
    )
    assert ErrorCode.BAD_DECIMAL not in _codes(
        validate(ok_df, EntityType.LEDGER, known_groups=["Sundry Debtors"])
    )
    bad_df = pd.DataFrame(
        {"name": ["Cust A"], "parent": ["Sundry Debtors"], "opening_balance": ["abc"]}
    )
    bad = validate(bad_df, EntityType.LEDGER, known_groups=["Sundry Debtors"])
    assert ErrorCode.BAD_DECIMAL in _codes(bad)
    assert bad.ok is True  # warning only


def test_stockitem_unknown_unit_is_error() -> None:
    df = pd.DataFrame({"name": ["Shirt"], "base_units": ["Pcs"]})
    result = validate(df, EntityType.STOCK_ITEM, known_units=["Nos", "Kg"])
    assert result.ok is False
    assert ErrorCode.UNKNOWN_UNIT in _codes(result)


def test_group_cycle_detected() -> None:
    df = pd.DataFrame({"name": ["A", "B"], "parent": ["B", "A"]})
    result = validate(df, EntityType.GROUP)
    assert result.ok is False
    assert ErrorCode.GROUP_CYCLE in _codes(result)


def test_no_snapshot_skips_group_membership_check() -> None:
    df = pd.DataFrame({"name": ["Cust A"], "parent": ["Some Unknown Group"]})
    result = validate(df, EntityType.LEDGER)  # no known_groups => degraded create-only mode
    assert ErrorCode.UNKNOWN_GROUP not in _codes(result)
    assert result.ok is True
