"""Tests for the deterministic entity-resolution rule layer."""

from app.pipeline.resolution import resolve

EXISTING = [
    {"name": "HDFC Bank", "parent": "Bank Accounts", "guid": "g-hdfc"},
    {"name": "Cash", "parent": "Cash-in-Hand", "guid": "g-cash"},
]


def test_exact_match_updates() -> None:
    verdict = resolve([{"name": "HDFC Bank", "parent": "Bank Accounts", "source_row": 1}], EXISTING)[0]
    assert verdict.decision == "update"
    assert verdict.action == "Alter"
    assert verdict.matched_guid == "g-hdfc"
    assert verdict.source_row == 1


def test_exact_name_different_parent_still_updates() -> None:
    # Ledger identity is company-global NAME; a differing parent is just an attribute to update.
    verdict = resolve([{"name": "HDFC Bank", "parent": "Sundry Debtors"}], EXISTING)[0]
    assert verdict.decision == "update"


def test_case_and_space_insensitive_exact_match() -> None:
    verdict = resolve([{"name": "  hdfc   bank "}], EXISTING)[0]
    assert verdict.decision == "update"
    assert verdict.matched_name == "HDFC Bank"


def test_new_master_creates() -> None:
    verdict = resolve([{"name": "Reliance Industries", "parent": "Sundry Debtors"}], EXISTING)[0]
    assert verdict.decision == "create"
    assert verdict.action == "Create"


def test_close_typo_is_conflict() -> None:
    verdict = resolve([{"name": "HDFC Bnk", "parent": "Bank Accounts"}], EXISTING)[0]
    assert verdict.decision == "conflict"
    assert verdict.action is None
    assert verdict.matched_name == "HDFC Bank"


def test_empty_snapshot_creates_everything() -> None:
    verdicts = resolve([{"name": "HDFC Bank"}, {"name": "Cash"}], [])
    assert all(v.decision == "create" for v in verdicts)
