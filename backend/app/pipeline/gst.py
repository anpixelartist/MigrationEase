"""GST tax engine — place-of-supply routing and CGST/SGST/IGST computation.

Indian GST splits a sale's tax by *place of supply*:
  - intra-state (buyer's state == seller/home state) -> CGST + SGST, each half the rate
  - inter-state (buyer's state != home state)         -> IGST, the full rate

This module is PURE (no I/O) and is the single source of truth for both validation and generation,
so the tax a user sees at Review is exactly what is pushed. All money is :class:`Decimal`; components
are rounded so ``cgst + sgst == total_tax`` and ``igst == total_tax`` EXACTLY (no lost/created paisa).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

_PAISA = Decimal("0.01")

# GST state code (01..38) + common 2-letter abbreviation -> canonical state name. Used so "27",
# "MH" and "Maharashtra" all compare equal when deciding place of supply.
_STATE_CODES: dict[str, str] = {
    "01": "Jammu and Kashmir", "02": "Himachal Pradesh", "03": "Punjab", "04": "Chandigarh",
    "05": "Uttarakhand", "06": "Haryana", "07": "Delhi", "08": "Rajasthan", "09": "Uttar Pradesh",
    "10": "Bihar", "11": "Sikkim", "12": "Arunachal Pradesh", "13": "Nagaland", "14": "Manipur",
    "15": "Mizoram", "16": "Tripura", "17": "Meghalaya", "18": "Assam", "19": "West Bengal",
    "20": "Jharkhand", "21": "Odisha", "22": "Chhattisgarh", "23": "Madhya Pradesh",
    "24": "Gujarat", "25": "Daman and Diu", "26": "Dadra and Nagar Haveli", "27": "Maharashtra",
    "28": "Andhra Pradesh", "29": "Karnataka", "30": "Goa", "31": "Lakshadweep", "32": "Kerala",
    "33": "Tamil Nadu", "34": "Puducherry", "35": "Andaman and Nicobar Islands", "36": "Telangana",
    "37": "Andhra Pradesh", "38": "Ladakh",
}
_ABBREV: dict[str, str] = {
    "jk": "Jammu and Kashmir", "hp": "Himachal Pradesh", "pb": "Punjab", "ch": "Chandigarh",
    "uk": "Uttarakhand", "ua": "Uttarakhand", "hr": "Haryana", "dl": "Delhi", "rj": "Rajasthan",
    "up": "Uttar Pradesh", "br": "Bihar", "sk": "Sikkim", "ar": "Arunachal Pradesh",
    "nl": "Nagaland", "mn": "Manipur", "mz": "Mizoram", "tr": "Tripura", "ml": "Meghalaya",
    "as": "Assam", "wb": "West Bengal", "jh": "Jharkhand", "od": "Odisha", "or": "Odisha",
    "cg": "Chhattisgarh", "mp": "Madhya Pradesh", "gj": "Gujarat", "mh": "Maharashtra",
    "ka": "Karnataka", "ga": "Goa", "kl": "Kerala", "tn": "Tamil Nadu", "py": "Puducherry",
    "tg": "Telangana", "ts": "Telangana", "ap": "Andhra Pradesh", "la": "Ladakh",
}
# GSTIN's first two chars are the state code — used to derive place of supply from a B2B party GSTIN.
GSTIN_LEN = 15


def normalize_state(value: object) -> str:
    """Canonicalize a state name / 2-letter code / GST numeric code for equality comparison.

    Returns "" for blank input, the canonical state name when recognized, else the cleaned string
    (so two spellings of an unlisted place still compare equal).
    """
    s = " ".join(str(value or "").strip().split())
    if not s:
        return ""
    key = s.casefold()
    if s.isdigit():
        return _STATE_CODES.get(s.zfill(2), s)
    if len(key) == 2 and key in _ABBREV:
        return _ABBREV[key]
    # already a full name?
    for name in set(_STATE_CODES.values()) | set(_ABBREV.values()):
        if key == name.casefold():
            return name
    return key


def state_from_gstin(gstin: object) -> str:
    """State encoded in a GSTIN's first two digits, or "" if not a well-formed GSTIN."""
    g = str(gstin or "").strip().upper()
    if len(g) == GSTIN_LEN and g[:2].isdigit():
        return _STATE_CODES.get(g[:2], "")
    return ""


def is_intra_state(buyer_state: object, home_state: object) -> bool | None:
    """True = intra-state (CGST+SGST), False = inter-state (IGST), None = undeterminable.

    None when either state is unknown — the caller must then NOT fabricate a split.
    """
    b, h = normalize_state(buyer_state), normalize_state(home_state)
    if not b or not h:
        return None
    return b == h


def _dec(value: object) -> Decimal | None:
    try:
        d = Decimal(str(value).replace(",", "").strip())
    except (InvalidOperation, ValueError, AttributeError):
        return None
    return d


def _round(d: Decimal) -> Decimal:
    return d.quantize(_PAISA, rounding=ROUND_HALF_UP)


@dataclass(frozen=True)
class GstBreakup:
    """A tax split for one taxable line. Exactly one of (cgst+sgst) or igst is non-zero."""

    taxable: Decimal
    rate: Decimal          # total GST rate %, e.g. 18
    intra: bool
    cgst: Decimal = Decimal("0")
    sgst: Decimal = Decimal("0")
    igst: Decimal = Decimal("0")

    @property
    def total_tax(self) -> Decimal:
        return self.cgst + self.sgst + self.igst

    @property
    def components(self) -> list[tuple[str, Decimal]]:
        """(ledger_suffix, amount) pairs to emit, dropping zero components."""
        out = []
        if self.intra:
            if self.cgst:
                out.append(("CGST", self.cgst))
            if self.sgst:
                out.append(("SGST", self.sgst))
        elif self.igst:
            out.append(("IGST", self.igst))
        return out


def compute_gst(taxable: object, rate_pct: object, intra: bool) -> GstBreakup | None:
    """Compute the CGST/SGST or IGST for a taxable amount at a total GST rate.

    Returns None if the amount or rate can't be parsed. A zero rate yields a zero-tax breakup (valid:
    exempt/0% supplies). Rounding guarantees the components sum EXACTLY to round(taxable*rate/100).
    """
    amt, rate = _dec(taxable), _dec(rate_pct)
    if amt is None or rate is None or rate < 0:
        return None
    amt, rate = abs(amt), rate
    total = _round(amt * rate / Decimal("100"))
    if intra:
        cgst = _round(total / Decimal("2"))
        sgst = total - cgst  # absorbs the odd paisa so cgst+sgst == total exactly
        return GstBreakup(taxable=amt, rate=rate, intra=True, cgst=cgst, sgst=sgst)
    return GstBreakup(taxable=amt, rate=rate, intra=False, igst=total)
