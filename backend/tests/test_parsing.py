"""Tests for the parse stage (CSV encoding/delimiter detection + XLSX via calamine)."""

import io

import pandas as pd

from app.pipeline.contracts import ErrorCode
from app.pipeline.parsing import parse_file


def test_empty_named_column_dropped_but_reported() -> None:
    # an all-empty named column is dropped (convenience) but surfaced in stats — never silent
    res = parse_file(b"name,unit,parent\nA,,Sundry Debtors\nB,,Sundry Debtors\n", "x.csv")
    assert res.ok
    assert "unit" not in res.data.columns
    assert "unit" in res.stats["dropped_columns"]


def test_parse_comma_csv() -> None:
    content = b"Ledger Name,Under\nCust A,Sundry Debtors\nCust B,Sundry Debtors\n"
    result = parse_file(content, "ledgers.csv")
    assert result.ok is True
    assert list(result.data.columns) == ["Ledger Name", "Under"]
    assert result.data.shape == (2, 2)
    assert result.data.iloc[0]["Ledger Name"] == "Cust A"


def test_parse_semicolon_delimiter_is_detected() -> None:
    content = b"name;parent\nA;Sundry Debtors\nB;Bank Accounts\n"
    result = parse_file(content, "x.csv")
    assert result.ok is True
    assert list(result.data.columns) == ["name", "parent"]
    assert result.data.shape == (2, 2)


def test_parse_single_column_with_spaces_not_split() -> None:
    # Regression: sep=None used to sniff a space and shatter "HDFC Bank" into two columns.
    content = b"Ledger Name\nCash\nHDFC Bank\n"
    result = parse_file(content, "names.csv")
    assert result.ok is True
    assert list(result.data.columns) == ["Ledger Name"]
    assert result.data.shape == (2, 1)
    assert result.data.iloc[1]["Ledger Name"] == "HDFC Bank"


def test_parse_tab_delimited() -> None:
    content = b"name\tparent\nCash\tCash-in-Hand\n"
    result = parse_file(content, "x.csv")
    assert result.ok is True
    assert list(result.data.columns) == ["name", "parent"]
    assert result.data.iloc[0]["parent"] == "Cash-in-Hand"


def test_parse_non_utf8_encoding() -> None:
    content = "name,city\nCafé,Señor\n".encode("latin-1")
    result = parse_file(content, "x.csv")
    assert result.ok is True
    assert result.data.iloc[0]["name"] == "Café"


def test_parse_header_whitespace_is_stripped() -> None:
    content = b"  Ledger Name ,  Under \nA,Sundry Debtors\n"
    result = parse_file(content, "x.csv")
    assert list(result.data.columns) == ["Ledger Name", "Under"]


def test_parse_xlsx_via_calamine() -> None:
    df = pd.DataFrame({"Item": ["Shirt", "Trouser"], "Unit": ["Pcs", "Pcs"]})
    buffer = io.BytesIO()
    df.to_excel(buffer, index=False)  # openpyxl writer
    result = parse_file(buffer.getvalue(), "items.xlsx")
    assert result.ok is True
    assert list(result.data.columns) == ["Item", "Unit"]
    assert result.data.shape == (2, 2)
    assert result.stats["format"] == "excel"


def test_parse_empty_file() -> None:
    result = parse_file(b"", "x.csv")
    assert result.ok is False
    assert result.errors[0].code == ErrorCode.EMPTY_FILE


def test_parse_row_cap_exceeded() -> None:
    content = b"name,parent\n" + b"\n".join(f"L{i},G".encode() for i in range(5)) + b"\n"
    result = parse_file(content, "x.csv", max_rows=2)
    assert result.ok is False
    assert result.errors[0].code == ErrorCode.FILE_TOO_LARGE


def test_parse_values_are_strings() -> None:
    content = b"name,opening\nCash,1000\n"
    result = parse_file(content, "x.csv")
    assert result.data.iloc[0]["opening"] == "1000"
    assert isinstance(result.data.iloc[0]["opening"], str)
