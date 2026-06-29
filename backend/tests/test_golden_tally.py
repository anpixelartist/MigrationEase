"""Golden tests grounded in real TallyPrime output (Milestone 0 Spike A, company "Test1").

The request fixture is byte-identical to what TallyPrime ACCEPTED (CREATED=2, ERRORS=0); the response
and export fixtures are what Tally actually returned. These pin the builder/parser to verified Tally
behaviour — any change that drifts from them fails CI and must be re-verified against a live Tally.
"""

from decimal import Decimal
from pathlib import Path

from app.pipeline.conversion.builder import build_and_serialize, format_opening_balance
from app.pipeline.entities import Ledger
from app.pipeline.push.response_parser import parse_import_response

GOLDEN = Path(__file__).parent / "golden_xml"

_SPIKE_LEDGERS = [
    Ledger(name="AA TM Debit Test", parent="Sundry Debtors",
           opening_balance=Decimal("12345"), opening_is_debit=True),
    Ledger(name="AA TM Credit Test", parent="Sundry Creditors",
           opening_balance=Decimal("54321"), opening_is_debit=False),
]


def test_builder_output_is_byte_identical_to_tally_accepted_request() -> None:
    produced = build_and_serialize("Test1", ledgers=_SPIKE_LEDGERS)
    expected = (GOLDEN / "ledgers_create_drcr.request.xml").read_bytes()
    assert produced == expected


def test_parser_reads_real_tally_created_response() -> None:
    result = parse_import_response((GOLDEN / "import_response_created.xml").read_bytes())
    assert result.created == 2
    assert result.errors == 0
    assert result.cancelled == 0
    assert result.is_success is True


def test_sign_convention_matches_tally_export() -> None:
    reference = (GOLDEN / "ledger_export_drcr.reference.xml").read_text(encoding="utf-8")
    # Debit ledger (sent +12345) stored by Tally as ISDEEMEDPOSITIVE=Yes with a positive amount;
    # Credit ledger (sent -54321) stored as ISDEEMEDPOSITIVE=No with a negative amount.
    assert '<ISDEEMEDPOSITIVE TYPE="Logical">Yes</ISDEEMEDPOSITIVE>' in reference
    assert '<OPENINGBALANCE TYPE="Amount">12345.00</OPENINGBALANCE>' in reference
    assert '<ISDEEMEDPOSITIVE TYPE="Logical">No</ISDEEMEDPOSITIVE>' in reference
    assert '<OPENINGBALANCE TYPE="Amount">-54321.00</OPENINGBALANCE>' in reference
    # our serializer reproduces those exact signed magnitudes
    assert format_opening_balance(Decimal("12345"), is_debit=True) == "12345.00"
    assert format_opening_balance(Decimal("54321"), is_debit=False) == "-54321.00"
