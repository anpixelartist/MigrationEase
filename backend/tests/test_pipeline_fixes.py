"""Regression tests for two audit-surfaced pipeline gaps: UTF-8 BOM on read, and HSN emission."""

from __future__ import annotations

from app.pipeline.conversion import build_and_serialize
from app.pipeline.entities import StockItem, TallyAction
from app.pipeline.parsing import parse_file


def test_utf8_bom_does_not_corrupt_first_header():
    # A UTF-8 BOM (EF BB BF) prefixing the header used to leak into the first column name.
    content = "﻿Ledger Name,Under\nAcme,Sundry Debtors\n".encode("utf-8")
    result = parse_file(content, "ledgers.csv")
    assert result.ok, result.errors
    assert list(result.data.columns)[0] == "Ledger Name"  # not "﻿Ledger Name"


def test_hsn_code_is_emitted_into_stock_item_xml():
    item = StockItem(name="Widget", base_units="Nos", hsn_code="61052010", action=TallyAction.CREATE)
    xml = build_and_serialize("TestCo", stock_items=[item]).decode()
    assert "<HSNCODE>61052010</HSNCODE>" in xml


def test_no_hsn_tag_when_absent():
    item = StockItem(name="Widget", base_units="Nos", action=TallyAction.CREATE)
    xml = build_and_serialize("TestCo", stock_items=[item]).decode()
    assert "HSNCODE" not in xml
