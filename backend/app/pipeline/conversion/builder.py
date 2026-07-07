"""Build the Tally "All Masters" import ENVELOPE from canonical master objects.

Load-bearing Tally rules encoded here (plan §5):
  * Create-order is topological within ONE ordered envelope: Units -> Groups(parent-before-child)
    -> Stock Items -> Ledgers. A master whose PARENT/BASEUNITS does not yet exist is rejected by Tally.
  * Create vs update is decided upstream (resolution) and emitted as ACTION="Create"|"Alter".
  * Build with lxml element-tree construction (auto-escaping). Never string/Jinja templating.
  * OPENINGBALANCE sign and the exact encoding are confirmed empirically in Milestone 0 Spike A.
"""

from __future__ import annotations

from collections import defaultdict, deque
from collections.abc import Sequence
from decimal import ROUND_HALF_UP, Decimal

from lxml import etree

from app.pipeline.conversion.serialize import list_marker_value, serialize
from app.pipeline.conversion.voucher_builder import _build_voucher
from app.pipeline.entities import Group, Ledger, StockItem, TallyAction, Unit, Voucher

# ---------------------------------------------------------------------------
# Tally conventions
# ---------------------------------------------------------------------------
# OPENINGBALANCE sign convention: Debit = positive, Credit = negative.
# VERIFIED against live TallyPrime (company "Test1", 2026-06-26, Milestone 0 Spike A): a ledger
# imported with OPENINGBALANCE=+12345 exported back as ISDEEMEDPOSITIVE=Yes (Debit); one imported
# with OPENINGBALANCE=-54321 exported back as ISDEEMEDPOSITIVE=No (Credit). See the golden fixtures
# in tests/golden_xml/. Do not change without re-running the spike.
OPENING_BALANCE_CREDIT_IS_NEGATIVE = True

UDF_NSMAP = {"UDF": "TallyUDF"}
PRIMARY_GROUP = "Primary"
DEFAULT_IMPORT_DUPS = "@@DupModify"  # deliberate safety net; we still resolve ACTION explicitly


# ---------------------------------------------------------------------------
# small formatting helpers
# ---------------------------------------------------------------------------
def _yn(value: bool) -> str:
    return "Yes" if value else "No"


def _amount(value: Decimal | int | float) -> str:
    """Fixed 2-decimal-place amount string."""
    return str(Decimal(value).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def _num(value: Decimal | int | float) -> str:
    """Plain decimal string with no exponent and no forced trailing zeros (for quantities)."""
    return format(Decimal(value), "f")


def _norm(name: str) -> str:
    return " ".join((name or "").strip().casefold().split())


def _sub(parent: etree._Element, tag: str, text: str | None = None,
         attrib: dict[str, str] | None = None) -> etree._Element:
    el = etree.SubElement(parent, tag, attrib or {})
    if text is not None:
        el.text = text
    return el


def _tallymessage(requestdata: etree._Element) -> etree._Element:
    # xmlns:UDF is required when any user-defined field is present and harmless otherwise.
    return etree.SubElement(requestdata, "TALLYMESSAGE", nsmap=UDF_NSMAP)


def _add_identity(master_el: etree._Element, *, name: str, guid: str | None,
                  old_name: str | None) -> None:
    """Emit GUID (if known) and the NAME / NAME.LIST(new, old) targeting structure."""
    if guid:
        _sub(master_el, "GUID", guid)
    old = (old_name or "").strip()
    if old and _norm(old) != _norm(name):
        name_list = _sub(master_el, "NAME.LIST", attrib={"TYPE": "String"})
        _sub(name_list, "NAME", name)  # new name
        _sub(name_list, "NAME", old)   # old name = the match key
    else:
        _sub(master_el, "NAME", name)


# ---------------------------------------------------------------------------
# opening balance
# ---------------------------------------------------------------------------
def format_opening_balance(amount: Decimal, is_debit: bool) -> str:
    """Encode a magnitude + Dr/Cr direction into Tally's signed OPENINGBALANCE string."""
    magnitude = abs(Decimal(amount))
    if not is_debit and OPENING_BALANCE_CREDIT_IS_NEGATIVE:
        return _amount(-magnitude)
    if is_debit and not OPENING_BALANCE_CREDIT_IS_NEGATIVE:
        return _amount(-magnitude)
    return _amount(magnitude)


# ---------------------------------------------------------------------------
# topological ordering of groups (parent before child)
# ---------------------------------------------------------------------------
def topological_sort_groups(groups: Sequence[Group]) -> list[Group]:
    """Order groups so a parent precedes its children (Kahn's algorithm over in-batch parent edges).

    Groups whose parent is not present in the batch (e.g. "Primary" or a pre-existing Tally group)
    are treated as roots. Raises ``ValueError`` on a cyclic hierarchy.
    """
    by_name: dict[str, Group] = {_norm(g.name): g for g in groups}
    children: dict[str, list[str]] = defaultdict(list)
    indegree: dict[str, int] = {_norm(g.name): 0 for g in groups}

    for g in groups:
        parent_key = _norm(g.parent)
        self_key = _norm(g.name)
        if parent_key in by_name and parent_key != self_key:
            children[parent_key].append(self_key)
            indegree[self_key] += 1

    # sort for deterministic output
    queue: deque[str] = deque(sorted(k for k, d in indegree.items() if d == 0))
    ordered: list[Group] = []
    while queue:
        key = queue.popleft()
        ordered.append(by_name[key])
        for child in sorted(children[key]):
            indegree[child] -= 1
            if indegree[child] == 0:
                queue.append(child)

    if len(ordered) != len(groups):
        cyclic = sorted(k for k, d in indegree.items() if d > 0)
        raise ValueError(f"Cyclic group hierarchy detected among: {cyclic}")
    return ordered


# ---------------------------------------------------------------------------
# per-master builders (each appends one <TALLYMESSAGE> to REQUESTDATA)
# ---------------------------------------------------------------------------
def _build_unit(requestdata: etree._Element, unit: Unit) -> None:
    msg = _tallymessage(requestdata)
    el = _sub(msg, "UNIT", attrib={"NAME": unit.name, "ACTION": unit.action.value})
    _add_identity(el, name=unit.name, guid=unit.guid, old_name=unit.old_name)
    if unit.is_simple:
        _sub(el, "ISSIMPLEUNIT", "Yes")
        _sub(el, "DECIMALPLACES", str(unit.decimal_places))
    else:
        # Tally derives the NAME of a compound unit from base + additional + conversion.
        _sub(el, "ISSIMPLEUNIT", "No")
        if unit.base_units:
            _sub(el, "BASEUNITS", unit.base_units)
        if unit.additional_units:
            _sub(el, "ADDITIONALUNITS", unit.additional_units)
        if unit.conversion is not None:
            _sub(el, "CONVERSION", _num(unit.conversion))


def _build_group(requestdata: etree._Element, group: Group) -> None:
    msg = _tallymessage(requestdata)
    el = _sub(msg, "GROUP", attrib={"NAME": group.name, "ACTION": group.action.value})
    _add_identity(el, name=group.name, guid=group.guid, old_name=group.old_name)
    _sub(el, "PARENT", group.parent or PRIMARY_GROUP)
    if group.is_revenue is not None:
        _sub(el, "ISREVENUE", _yn(group.is_revenue))
    if group.is_deemed_positive is not None:
        _sub(el, "ISDEEMEDPOSITIVE", _yn(group.is_deemed_positive))
    if group.is_billwise is not None:
        _sub(el, "ISBILLWISEON", _yn(group.is_billwise))
    if group.affects_gross_profit is not None:
        _sub(el, "AFFECTSGROSSPROFIT", _yn(group.affects_gross_profit))


def _build_ledger(requestdata: etree._Element, ledger: Ledger) -> None:
    msg = _tallymessage(requestdata)
    el = _sub(msg, "LEDGER", attrib={"NAME": ledger.name, "ACTION": ledger.action.value})
    _add_identity(el, name=ledger.name, guid=ledger.guid, old_name=ledger.old_name)
    _sub(el, "PARENT", ledger.parent)
    if ledger.is_billwise is not None:
        _sub(el, "ISBILLWISEON", _yn(ledger.is_billwise))
    if ledger.gst_registration_type:
        _sub(el, "GSTREGISTRATIONTYPE", ledger.gst_registration_type)
    if ledger.gstin:
        _sub(el, "PARTYGSTIN", ledger.gstin)
    if ledger.state:
        _sub(el, "LEDSTATENAME", ledger.state)
    if ledger.country:
        _sub(el, "COUNTRYNAME", ledger.country)
    # Opening balance on Create only; emitting it on Alter risks re-run corruption (plan §11.10).
    if ledger.action == TallyAction.CREATE:
        _sub(el, "OPENINGBALANCE", format_opening_balance(ledger.opening_balance, ledger.opening_is_debit))
    if ledger.mailing_name:
        ml = _sub(el, "MAILINGNAME.LIST", attrib={"TYPE": "String"})
        _sub(ml, "MAILINGNAME", ledger.mailing_name)
    if ledger.address_lines:
        al = _sub(el, "ADDRESS.LIST", attrib={"TYPE": "String"})
        for line in ledger.address_lines:
            _sub(al, "ADDRESS", line)
    if ledger.pincode:
        _sub(el, "PINCODE", ledger.pincode)
    if ledger.email:
        _sub(el, "EMAIL", ledger.email)
    if ledger.phone:
        _sub(el, "LEDGERPHONE", ledger.phone)
    if ledger.credit_limit is not None:
        _sub(el, "CREDITLIMIT", _amount(ledger.credit_limit))


def _build_stock_item(requestdata: etree._Element, item: StockItem) -> None:
    msg = _tallymessage(requestdata)
    el = _sub(msg, "STOCKITEM", attrib={"NAME": item.name, "ACTION": item.action.value})
    _add_identity(el, name=item.name, guid=item.guid, old_name=item.old_name)
    # "Primary" is NOT a valid STOCK group in Tally (verified Spike A 2026-06-26 — it raises
    # "Stock Group 'Primary' does not exist"). Omit PARENT so Tally uses the primary stock group.
    if item.parent and item.parent.strip().casefold() != PRIMARY_GROUP.casefold():
        _sub(el, "PARENT", item.parent)
    _sub(el, "BASEUNITS", item.base_units)  # the referenced Unit must pre-exist
    if item.hsn_code:
        _sub(el, "HSNCODE", item.hsn_code)  # canonical field 'hsn' -> Tally <HSNCODE>
    if item.category:
        _sub(el, "CATEGORY", item.category)
    if item.description:
        _sub(el, "DESCRIPTION", item.description)
    if item.gst_applicable:
        _sub(el, "GSTAPPLICABLE", list_marker_value("Applicable"))
        _sub(el, "GSTTYPEOFSUPPLY", "Goods")
    else:
        _sub(el, "GSTAPPLICABLE", list_marker_value("Not Applicable"))
    # Opening stock on Create only.
    if item.action == TallyAction.CREATE and item.opening_qty is not None:
        _sub(el, "OPENINGBALANCE", f"{_num(item.opening_qty)} {item.base_units}")
        if item.opening_rate is not None:
            _sub(el, "OPENINGRATE", f"{_amount(item.opening_rate)}/{item.base_units}")
        if item.opening_value is not None:
            _sub(el, "OPENINGVALUE", _amount(item.opening_value))


# ---------------------------------------------------------------------------
# envelope assembly
# ---------------------------------------------------------------------------
def build_masters_envelope(
    company: str,
    *,
    units: Sequence[Unit] = (),
    groups: Sequence[Group] = (),
    stock_items: Sequence[StockItem] = (),
    ledgers: Sequence[Ledger] = (),
    import_dups: str | None = DEFAULT_IMPORT_DUPS,
) -> etree._Element:
    """Assemble the full ``<ENVELOPE>`` for an "All Masters" import, in dependency order.

    ``company`` selects the target Tally company via SVCURRENTCOMPANY and MUST match the company
    currently open in Tally (the bridge enforces this just-in-time — plan §7).
    """
    env = etree.Element("ENVELOPE")
    header = _sub(env, "HEADER")
    _sub(header, "TALLYREQUEST", "Import Data")

    body = _sub(env, "BODY")
    importdata = _sub(body, "IMPORTDATA")

    reqdesc = _sub(importdata, "REQUESTDESC")
    _sub(reqdesc, "REPORTNAME", "All Masters")
    static = _sub(reqdesc, "STATICVARIABLES")
    _sub(static, "SVCURRENTCOMPANY", company)
    if import_dups:
        _sub(static, "IMPORTDUPS", import_dups)

    reqdata = _sub(importdata, "REQUESTDATA")
    for unit in units:
        _build_unit(reqdata, unit)
    for group in topological_sort_groups(list(groups)):
        _build_group(reqdata, group)
    for item in stock_items:
        _build_stock_item(reqdata, item)
    for ledger in ledgers:
        _build_ledger(reqdata, ledger)
    return env


def build_unified_envelope(
    company: str,
    *,
    units: Sequence[Unit] = (),
    groups: Sequence[Group] = (),
    stock_items: Sequence[StockItem] = (),
    ledgers: Sequence[Ledger] = (),
    vouchers: Sequence[Voucher] = (),
    import_dups: str | None = DEFAULT_IMPORT_DUPS,
) -> etree._Element:
    """Assemble a single unified `<ENVELOPE>` containing both 'All Masters' and 'Vouchers'."""
    env = etree.Element("ENVELOPE")
    header = _sub(env, "HEADER")
    _sub(header, "TALLYREQUEST", "Import Data")

    body = _sub(env, "BODY")

    if units or groups or stock_items or ledgers:
        importdata = _sub(body, "IMPORTDATA")
        reqdesc = _sub(importdata, "REQUESTDESC")
        _sub(reqdesc, "REPORTNAME", "All Masters")
        static = _sub(reqdesc, "STATICVARIABLES")
        _sub(static, "SVCURRENTCOMPANY", company)
        if import_dups:
            _sub(static, "IMPORTDUPS", import_dups)

        reqdata = _sub(importdata, "REQUESTDATA")
        for unit in units:
            _build_unit(reqdata, unit)
        for group in topological_sort_groups(list(groups)):
            _build_group(reqdata, group)
        for item in stock_items:
            _build_stock_item(reqdata, item)
        for ledger in ledgers:
            _build_ledger(reqdata, ledger)

    if vouchers:
        importdata_v = _sub(body, "IMPORTDATA")
        reqdesc_v = _sub(importdata_v, "REQUESTDESC")
        _sub(reqdesc_v, "REPORTNAME", "Vouchers")
        static_v = _sub(reqdesc_v, "STATICVARIABLES")
        _sub(static_v, "SVCURRENTCOMPANY", company)

        reqdata_v = _sub(importdata_v, "REQUESTDATA")
        for voucher in vouchers:
            _build_voucher(reqdata_v, voucher)

    return env

def build_and_serialize(
    company: str,
    *,
    units: Sequence[Unit] = (),
    groups: Sequence[Group] = (),
    stock_items: Sequence[StockItem] = (),
    ledgers: Sequence[Ledger] = (),
    vouchers: Sequence[Voucher] = (),
    import_dups: str | None = DEFAULT_IMPORT_DUPS,
) -> bytes:
    """Convenience: build the envelope and serialize it to Tally-ready bytes."""
    envelope = build_unified_envelope(
        company,
        units=units,
        groups=groups,
        stock_items=stock_items,
        ledgers=ledgers,
        vouchers=vouchers,
        import_dups=import_dups,
    )
    return serialize(envelope)
