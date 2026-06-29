"""Tests for the Tally master XML builder (no live Tally required).

Structural assertions inspect the lxml element tree directly rather than re-parsing the serialized
bytes, because the final bytes intentionally contain ``&#4;`` (Tally's list marker), which is not
well-formed XML 1.0. lxml guarantees structure at build time; the marker is a final byte step.
"""

from decimal import Decimal

import pytest
from lxml import etree

from app.pipeline.conversion.builder import (
    build_and_serialize,
    build_masters_envelope,
    format_opening_balance,
    topological_sort_groups,
)
from app.pipeline.entities import Group, Ledger, StockItem, TallyAction, Unit


def _master_elements(envelope: etree._Element) -> list[etree._Element]:
    reqdata = envelope.find(".//REQUESTDATA")
    assert reqdata is not None
    return [msg[0] for msg in reqdata]  # first child of each <TALLYMESSAGE> is the master


def test_create_order_is_topological() -> None:
    env = build_masters_envelope(
        "ACME",
        units=[Unit(name="Pcs")],
        groups=[
            Group(name="Trade Debtors", parent="Sundry Debtors"),
            Group(name="Sundry Debtors", parent="Primary"),
        ],
        stock_items=[StockItem(name="Shirt", base_units="Pcs", parent="Primary")],
        ledgers=[Ledger(name="Cust A", parent="Sundry Debtors")],
    )
    tags = [etree.QName(m).localname for m in _master_elements(env)]
    assert tags == ["UNIT", "GROUP", "GROUP", "STOCKITEM", "LEDGER"]

    group_names = [
        m.findtext("NAME") for m in _master_elements(env) if etree.QName(m).localname == "GROUP"
    ]
    assert group_names.index("Sundry Debtors") < group_names.index("Trade Debtors")


def test_topological_cycle_raises() -> None:
    with pytest.raises(ValueError):
        topological_sort_groups([Group(name="A", parent="B"), Group(name="B", parent="A")])


def test_opening_balance_credit_is_negative() -> None:
    # ASSUMED convention (verify in Milestone 0 Spike A): Debit positive, Credit negative.
    assert format_opening_balance(Decimal("15000"), is_debit=False) == "-15000.00"
    assert format_opening_balance(Decimal("5000"), is_debit=True) == "5000.00"


def test_ledger_credit_opening_balance_in_xml() -> None:
    env = build_masters_envelope(
        "ACME",
        ledgers=[
            Ledger(
                name="Cust",
                parent="Sundry Debtors",
                opening_balance=Decimal("15000"),
                opening_is_debit=False,
            )
        ],
    )
    ledger = _master_elements(env)[0]
    assert ledger.findtext("OPENINGBALANCE") == "-15000.00"


def test_alter_ledger_skips_opening_balance_and_emits_namelist() -> None:
    env = build_masters_envelope(
        "ACME",
        ledgers=[
            Ledger(
                name="Cust New",
                parent="Sundry Debtors",
                action=TallyAction.ALTER,
                old_name="Cust Old",
                opening_balance=Decimal("100"),
                opening_is_debit=True,
            )
        ],
    )
    ledger = _master_elements(env)[0]
    assert ledger.get("ACTION") == "Alter"
    assert ledger.find("OPENINGBALANCE") is None  # deferred on Alter (plan §11.10)
    name_list = ledger.find("NAME.LIST")
    assert name_list is not None
    assert [n.text for n in name_list.findall("NAME")] == ["Cust New", "Cust Old"]


def test_compound_unit() -> None:
    env = build_masters_envelope(
        "ACME",
        units=[
            Unit(
                name="Box",
                is_simple=False,
                base_units="Pcs",
                additional_units="Box",
                conversion=Decimal("12"),
            )
        ],
    )
    unit = _master_elements(env)[0]
    assert unit.findtext("ISSIMPLEUNIT") == "No"
    assert unit.findtext("BASEUNITS") == "Pcs"
    assert unit.findtext("CONVERSION") == "12"


def test_stock_item_primary_parent_is_omitted() -> None:
    # "Primary" is not a valid STOCK group in Tally (verified Spike A) -> omit PARENT entirely.
    env = build_masters_envelope("C", stock_items=[StockItem(name="X", base_units="Pcs")])
    item = _master_elements(env)[0]
    assert item.find("PARENT") is None
    assert item.findtext("BASEUNITS") == "Pcs"


def test_stock_item_real_parent_is_included() -> None:
    env = build_masters_envelope(
        "C", stock_items=[StockItem(name="X", base_units="Pcs", parent="Apparel")]
    )
    item = _master_elements(env)[0]
    assert item.findtext("PARENT") == "Apparel"


def test_serialized_bytes_have_no_bom_udf_ns_and_list_marker() -> None:
    out = build_and_serialize(
        "ACME Traders",
        stock_items=[StockItem(name="Shirt", base_units="Pcs", gst_applicable=True)],
    )
    assert not out.startswith(b"\xef\xbb\xbf")  # no UTF-8 BOM
    assert b'xmlns:UDF="TallyUDF"' in out
    assert b"<TALLYREQUEST>Import Data</TALLYREQUEST>" in out
    assert b"&#4; Applicable" in out  # list marker substituted at serialization
    assert b"@@TALLY_LIST_MARKER@@" not in out


def test_non_ascii_name_escaped_as_char_ref() -> None:
    out = build_and_serialize("ACME", ledgers=[Ledger(name="Café", parent="Sundry Debtors")])
    assert "Café".encode("utf-8") not in out  # never raw non-ASCII bytes
    assert b"Caf&#233;" in out  # numeric character reference


def test_empty_name_rejected() -> None:
    with pytest.raises(ValueError):
        Ledger(name="   ", parent="Sundry Debtors")
