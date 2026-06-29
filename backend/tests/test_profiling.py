"""Tests for the profile stage (per-column signals)."""

import pandas as pd

from app.pipeline.profiling import profile


def _df() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "name": ["Cust A", "Cust B", "Cust C"],
            "amount": ["1000", "-250", "30.5"],
            "flag": ["Yes", "No", "Yes"],
            "note": ["x", "", "y"],
            "blank": ["", "", ""],
        }
    )


def test_profile_basic_shape() -> None:
    result = profile(_df())
    assert result.ok is True
    assert result.data.row_count == 3
    assert result.data.column_count == 5


def test_inferred_types() -> None:
    cols = {c.name: c for c in profile(_df()).data.columns}
    assert cols["amount"].inferred_type == "numeric"
    assert cols["flag"].inferred_type == "boolean"
    assert cols["name"].inferred_type == "string"
    assert cols["blank"].inferred_type == "empty"


def test_null_pct_and_count() -> None:
    cols = {c.name: c for c in profile(_df()).data.columns}
    note = cols["note"]
    assert note.count == 2  # one blank excluded
    assert round(note.null_pct, 2) == 0.33


def test_candidate_key_detection() -> None:
    cols = {c.name: c for c in profile(_df()).data.columns}
    assert cols["name"].is_candidate_key is True  # all distinct, no nulls
    assert cols["flag"].is_candidate_key is False  # repeated values


def test_samples_are_distinct_originals() -> None:
    cols = {c.name: c for c in profile(_df()).data.columns}
    assert cols["name"].samples == ["Cust A", "Cust B", "Cust C"]
