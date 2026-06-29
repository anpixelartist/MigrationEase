"""Tests for the canonical catalog loader and the auto-mapping engine."""

import pandas as pd

from app.pipeline.entities import EntityType
from app.pipeline.mapping import auto_map
from app.pipeline.mapping.catalog import load_catalog


def _mapped(proposal) -> dict[str, str | None]:
    return {s.target_field: s.source_column for s in proposal.suggestions}


# ---- catalog loader ----
def test_all_catalogs_load() -> None:
    for entity in EntityType:
        catalog = load_catalog(entity)
        assert catalog.entity == entity.value
        assert catalog.fields
        # masters identify on "name"; vouchers (line format) identify/group on "voucher_number"
        identity_key = "voucher_number" if entity == EntityType.VOUCHER else "name"
        assert identity_key in [f.key for f in catalog.fields]


def test_ledger_catalog_specifics() -> None:
    catalog = load_catalog(EntityType.LEDGER)
    name = catalog.by_key("name")
    assert name is not None and name.required and name.unique
    parent = catalog.by_key("parent")
    assert parent is not None and parent.required and parent.tally_tag == "PARENT"


# ---- mapping ----
def test_ledger_headers_map_correctly() -> None:
    df = pd.DataFrame(
        {
            "Ledger Name": ["Cust A", "Cust B"],
            "Under": ["Sundry Debtors", "Sundry Debtors"],
            "Opening Bal": ["1000", "-250"],
            "GSTIN": ["29ABCDE1234F1Z5", ""],
            "Random Notes": ["x", "y"],
        }
    )
    result = auto_map(df, EntityType.LEDGER)
    mapped = _mapped(result.data)
    assert mapped["name"] == "Ledger Name"
    assert mapped["parent"] == "Under"
    assert mapped["opening_balance"] == "Opening Bal"
    assert mapped["gstin"] == "GSTIN"
    assert "Random Notes" in result.data.unmapped_sources
    assert result.data.unmapped_required == []
    assert result.ok is True


def test_required_fields_are_never_auto_accept() -> None:
    df = pd.DataFrame({"Ledger Name": ["A"], "Under": ["Sundry Debtors"]})
    result = auto_map(df, EntityType.LEDGER)
    suggestions = {s.target_field: s for s in result.data.suggestions}
    assert suggestions["name"].status == "needs_confirm"
    assert suggestions["name"].confidence >= 0.85  # high confidence, still confirmed
    assert suggestions["parent"].status == "needs_confirm"


def test_one_to_one_no_double_assignment() -> None:
    df = pd.DataFrame(
        {"Ledger Name": ["A"], "Account Name": ["B"], "Under": ["Sundry Debtors"]}
    )
    result = auto_map(df, EntityType.LEDGER)
    used = [s.source_column for s in result.data.suggestions if s.source_column]
    assert len(used) == len(set(used))  # no source mapped to two fields
    name_src = next(s.source_column for s in result.data.suggestions if s.target_field == "name")
    assert name_src in ("Ledger Name", "Account Name")


def test_missing_required_is_flagged() -> None:
    df = pd.DataFrame({"Foobar": ["x"], "Under": ["Sundry Debtors"]})
    result = auto_map(df, EntityType.LEDGER)
    assert "name" in result.data.unmapped_required
    assert result.ok is False


def test_stock_item_mapping() -> None:
    df = pd.DataFrame({"Item": ["Shirt"], "Unit": ["Pcs"], "HSN": ["61052010"]})
    result = auto_map(df, EntityType.STOCK_ITEM)
    mapped = _mapped(result.data)
    assert mapped["name"] == "Item"
    assert mapped["base_units"] == "Unit"
