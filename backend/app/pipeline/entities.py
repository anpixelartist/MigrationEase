"""Canonical master models — the typed contract between validation/resolution and XML generation.

These mirror Tally master tag names closely but stay transport-neutral: they carry only the MVP
fields plus identity metadata used for create-vs-update targeting and error correlation. Tag
names, envelope structure, and formatting live in ``conversion/``, not here.

Identity scope (drives create-vs-update — plan §11.6):
  * Ledger identity = company-global normalized NAME (``parent`` is an attribute to update, not a key).
  * Group / StockItem / Unit identity = NAME within their own class.
Update targeting priority: GUID > MASTERID > NAME(+old/new). ALTERID is a concurrency check only.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from enum import Enum

from pydantic import BaseModel, Field, field_validator


class EntityType(str, Enum):
    UNIT = "unit"
    GROUP = "group"
    STOCK_ITEM = "stock_item"
    LEDGER = "ledger"
    VOUCHER = "voucher"  # Phase 2 transactions (line format: one row per ledger entry)


class TallyAction(str, Enum):
    CREATE = "Create"
    ALTER = "Alter"  # update an existing master
    # DELETE is deliberately excluded from MVP.


class MasterBase(BaseModel):
    """Fields shared by every master. ``source_row``/identity fields are NOT serialized to Tally."""

    # --- source correlation (kept for mapping Tally's row-agnostic errors back to a file row) ---
    source_row: int | None = None

    # --- Tally identity ---
    name: str
    action: TallyAction = TallyAction.CREATE

    # --- reliable update targeting (populated only when matched against an existing master) ---
    guid: str | None = None
    master_id: str | None = None
    alter_id: str | None = None  # concurrency token only; never an identity key
    old_name: str | None = None  # set on Alter when the matched master's Tally name differs (rename)

    @field_validator("name")
    @classmethod
    def _strip_name(cls, value: str) -> str:
        value = (value or "").strip()
        if not value:
            raise ValueError("Tally master NAME cannot be empty")
        return value


class Unit(MasterBase):
    is_simple: bool = True
    decimal_places: int = 0
    # compound unit (e.g. 1 Box = 12 Pcs); Tally derives NAME from base+additional+conversion
    base_units: str | None = None
    additional_units: str | None = None
    conversion: Decimal | None = None


class Group(MasterBase):
    parent: str = "Primary"  # "Primary" for a top-level group; must pre-exist for sub-groups
    is_revenue: bool | None = None
    is_deemed_positive: bool | None = None
    is_billwise: bool | None = None
    affects_gross_profit: bool | None = None


class Ledger(MasterBase):
    parent: str  # the Group; mandatory in Tally
    # opening balance is a magnitude (>= 0) plus a Dr/Cr direction; the serializer encodes the sign
    opening_balance: Decimal = Decimal("0")
    opening_is_debit: bool = True
    gst_registration_type: str | None = None
    gstin: str | None = None
    state: str | None = None
    country: str | None = "India"
    is_billwise: bool | None = None
    mailing_name: str | None = None
    address_lines: list[str] = Field(default_factory=list)
    pincode: str | None = None
    email: str | None = None
    phone: str | None = None
    credit_limit: Decimal | None = None


class StockItem(MasterBase):
    parent: str = "Primary"  # stock group
    base_units: str  # mandatory; the referenced Unit must pre-exist
    category: str | None = None
    description: str | None = None
    opening_qty: Decimal | None = None
    opening_rate: Decimal | None = None
    opening_value: Decimal | None = None
    hsn_code: str | None = None  # emitted as <HSNCODE> when the user maps an HSN column
    gst_applicable: bool = False  # full GST RATEDETAILS deferred past the masters spine (plan §11.10)


# ---------------------------------------------------------------------------
# Phase 2 — Vouchers (transactions). Multi-line; the debits must equal the credits.
# AMOUNT sign convention is VERIFIED live (see memory phase2-voucher-spike) and is the INVERSE of the
# master opening-balance rule: a Debit line serializes to a NEGATIVE amount, a Credit line to POSITIVE.
# ---------------------------------------------------------------------------
class VoucherLine(BaseModel):
    """One entry of a voucher. ``amount`` is a magnitude (>= 0); the builder applies the sign.

    A line always carries an accounting allocation (``ledger_name`` + ``is_debit`` + ``amount``) so the
    voucher balances uniformly. If ``stock_item`` is set the line is an INVENTORY entry (Sales/Purchase):
    the builder emits ``ALLINVENTORYENTRIES.LIST`` with the stock qty/rate and a nested
    ``ACCOUNTINGALLOCATIONS.LIST`` to ``ledger_name``; otherwise a plain ``ALLLEDGERENTRIES.LIST``.
    """

    ledger_name: str
    is_debit: bool
    amount: Decimal  # magnitude; the referenced Ledger must pre-exist in the company
    source_row: int | None = None
    # inventory (Sales/Purchase) — present only on item lines
    stock_item: str | None = None
    quantity: Decimal | None = None
    rate: Decimal | None = None
    unit: str | None = None  # Tally requires the unit in qty/rate ("100 Nos", "50.00/Nos")

    @property
    def is_inventory(self) -> bool:
        return bool(self.stock_item)

    @field_validator("ledger_name")
    @classmethod
    def _strip_ledger(cls, value: str) -> str:
        value = (value or "").strip()
        if not value:
            raise ValueError("Voucher line LEDGERNAME cannot be empty")
        return value

    @field_validator("amount")
    @classmethod
    def _abs_amount(cls, value: Decimal) -> Decimal:
        return abs(Decimal(value))


class Voucher(BaseModel):
    """A transaction: header + N balanced ledger entries (+ inventory entries for Sales/Purchase later)."""

    voucher_type: str  # VCHTYPE + VOUCHERTYPENAME, e.g. "Receipt", "Payment", "Journal", "Sales"
    date: date
    lines: list[VoucherLine] = Field(default_factory=list)
    action: TallyAction = TallyAction.CREATE
    narration: str | None = None
    reference: str | None = None  # document / voucher number (e.g. an invoice no.)
    party_ledger: str | None = None  # PARTYLEDGERNAME for invoice-style vouchers
    guid: str | None = None
    master_id: str | None = None
    source_row: int | None = None  # the header (first) row this voucher came from

    @property
    def debit_total(self) -> Decimal:
        return sum((ln.amount for ln in self.lines if ln.is_debit), Decimal("0"))

    @property
    def credit_total(self) -> Decimal:
        return sum((ln.amount for ln in self.lines if not ln.is_debit), Decimal("0"))

    @property
    def is_balanced(self) -> bool:
        """True when debits == credits and there is a non-zero, at-least-two-sided entry."""
        return (
            len(self.lines) >= 2
            and self.debit_total > 0
            and self.debit_total == self.credit_total
        )
