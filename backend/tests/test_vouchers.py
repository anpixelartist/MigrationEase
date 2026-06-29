"""Phase-2 voucher pipeline: row-grouping, validation (Dr=Cr + dates), and an HTTP end-to-end."""

import pandas as pd
from lxml import etree

from app.pipeline.contracts import ErrorCode
from app.pipeline.conversion.voucher_builder import build_and_serialize_vouchers
from app.pipeline.entities import EntityType
from app.pipeline.validation import validate
from app.pipeline.voucher import rows_to_vouchers


def _df(rows: dict) -> pd.DataFrame:
    return pd.DataFrame(rows)


def test_groups_multiline_by_voucher_number() -> None:
    df = _df({
        "voucher_number": ["RV1", "RV1", "PV1", "PV1"],
        "date": ["2026-04-01", "2026-04-01", "01-04-2026", "01-04-2026"],
        "voucher_type": ["Receipt", "Receipt", "Payment", "Payment"],
        "ledger_name": ["Cash", "Customer 1", "Vendor 1", "Cash"],
        "amount": ["5000", "5000", "3000", "3000"],
        "dr_cr": ["Dr", "Cr", "Dr", "Cr"],
    })
    vouchers, errors = rows_to_vouchers(df)
    assert errors == []
    assert len(vouchers) == 2
    rv = vouchers[0]
    assert rv.voucher_type == "Receipt" and rv.reference == "RV1"
    assert len(rv.lines) == 2 and rv.is_balanced
    assert (rv.lines[0].ledger_name, rv.lines[0].is_debit) == ("Cash", True)
    assert rv.lines[1].is_debit is False


def test_separate_debit_credit_columns() -> None:
    df = _df({
        "voucher_number": ["J1", "J1", "J1"],
        "date": ["2026-05-15"] * 3,
        "voucher_type": ["Journal"] * 3,
        "ledger_name": ["Expense A", "Expense B", "Cash"],
        "debit": ["600", "400", ""],
        "credit": ["", "", "1000"],
    })
    vouchers, errors = rows_to_vouchers(df)
    assert errors == []
    assert len(vouchers) == 1
    v = vouchers[0]
    assert [(ln.is_debit, str(ln.amount)) for ln in v.lines] == [(True, "600"), (True, "400"), (False, "1000")]
    assert v.is_balanced


def test_unbalanced_voucher_is_flagged_and_excluded() -> None:
    df = _df({
        "voucher_number": ["X", "X"],
        "date": ["2026-04-01", "2026-04-01"],
        "voucher_type": ["Journal", "Journal"],
        "ledger_name": ["A", "B"],
        "amount": ["100", "250"],
        "dr_cr": ["Dr", "Cr"],
    })
    vouchers, errors = rows_to_vouchers(df)
    assert vouchers == []
    assert any(e.code == ErrorCode.VOUCHER_UNBALANCED for e in errors)


def test_missing_fields_and_bad_date() -> None:
    df = _df({
        "voucher_number": ["A", "A", "", "C"],
        "date": ["nonsense", "nonsense", "2026-04-01", "2026-04-01"],
        "voucher_type": ["Journal", "Journal", "Journal", "Journal"],
        "ledger_name": ["L1", "L2", "L3", ""],
        "amount": ["10", "10", "10", "10"],
        "dr_cr": ["Dr", "Cr", "Dr", "Cr"],
    })
    _vouchers, errors = rows_to_vouchers(df)
    codes = {e.code for e in errors}
    assert ErrorCode.BAD_DATE in codes          # voucher A has an unparseable date
    assert ErrorCode.REQUIRED_MISSING in codes  # row 3 has no voucher_number, row 4 no ledger


def test_validate_voucher_ok_and_unbalanced() -> None:
    clean = _df({
        "voucher_number": ["RV1", "RV1"],
        "date": ["2026-04-01", "2026-04-01"],
        "voucher_type": ["Receipt", "Receipt"],
        "ledger_name": ["Cash", "Customer 1"],
        "amount": ["5000", "5000"],
        "dr_cr": ["Dr", "Cr"],
    })
    ok = validate(clean, EntityType.VOUCHER)
    assert ok.ok is True
    assert ok.stats["vouchers"] == 1

    bad = clean.copy()
    bad.loc[1, "amount"] = "4000"  # break the balance
    res = validate(bad, EntityType.VOUCHER)
    assert res.ok is False
    assert any(e.code == ErrorCode.VOUCHER_UNBALANCED for e in res.errors)


def test_sales_invoice_with_inventory_lines() -> None:
    # party Dr 5900 == CGST 450 + SGST 450 + Sales 5000 (the sales income posts via the item line)
    df = _df({
        "voucher_number": ["INV1"] * 4,
        "date": ["2026-04-01"] * 4,
        "voucher_type": ["Sales"] * 4,
        "party_ledger": ["Customer 1"] * 4,
        "ledger_name": ["Customer 1", "CGST", "SGST", "Sales"],
        "amount": ["5900", "450", "450", "5000"],
        "dr_cr": ["Dr", "Cr", "Cr", "Cr"],
        "stock_item": ["", "", "", "Widget-1"],
        "quantity": ["", "", "", "100"],
        "rate": ["", "", "", "50"],
        "unit": ["", "", "", "Nos"],
    })
    vouchers, errors = rows_to_vouchers(df)
    assert errors == []  # qty*rate (100*50=5000) matches the line amount; voucher balances
    assert len(vouchers) == 1 and vouchers[0].is_balanced
    inv = [ln for ln in vouchers[0].lines if ln.is_inventory]
    assert len(inv) == 1 and inv[0].stock_item == "Widget-1"

    # structure VERIFIED against live Tally: Invoice view + LEDGERENTRIES.LIST (not ALLLEDGERENTRIES) +
    # unit-suffixed qty/rate + nested ACCOUNTINGALLOCATIONS to the income ledger.
    root = etree.fromstring(build_and_serialize_vouchers("Test1", [vouchers[0]]))
    assert root.find(".//VOUCHER").get("OBJVIEW") == "Invoice Voucher View"
    assert len(root.findall(".//LEDGERENTRIES.LIST")) == 3   # party + 2 GST (invoice-mode tag)
    assert root.findall(".//ALLLEDGERENTRIES.LIST") == []     # NOT the accounting-mode tag
    inv_entries = root.findall(".//ALLINVENTORYENTRIES.LIST")
    assert len(inv_entries) == 1
    assert inv_entries[0].findtext("STOCKITEMNAME") == "Widget-1"
    assert inv_entries[0].findtext("ACTUALQTY") == "100 Nos"
    assert inv_entries[0].findtext("RATE") == "50.00/Nos"
    assert inv_entries[0].findtext("ACCOUNTINGALLOCATIONS.LIST/LEDGERNAME") == "Sales"
    assert inv_entries[0].findtext("AMOUNT") == "5000.00"  # Sales = credit -> positive


def _poll(client, jid, tid):
    for _ in range(20):
        body = client.get(f"/jobs/{jid}/tasks/{tid}").json()
        if body["state"] != "pending":
            return body
    return {"state": "timeout"}


def test_voucher_preview_groups_lines(client) -> None:
    jid = client.post("/jobs", json={"entity_type": "voucher"}).json()["id"]
    csv = (b"vno,date,vtype,ledger,amt,drcr\n"
           b"RV1,2026-04-01,Receipt,Cash,5000,Dr\n"
           b"RV1,2026-04-01,Receipt,Customer 1,5000,Cr\n")
    client.post(f"/jobs/{jid}/file", files={"file": ("v.csv", csv, "text/csv")})
    client.post(f"/jobs/{jid}/mapping", json={"mapping": {
        "voucher_number": "vno", "date": "date", "voucher_type": "vtype",
        "ledger_name": "ledger", "amount": "amt", "dr_cr": "drcr"}, "constants": {}})
    body = client.get(f"/jobs/{jid}/vouchers/preview").json()
    assert body["count"] == 1
    v = body["vouchers"][0]
    assert v["voucher_type"] == "Receipt" and v["voucher_number"] == "RV1" and v["balanced"] is True
    assert len(v["lines"]) == 2
    assert {ln["dr_cr"] for ln in v["lines"]} == {"Dr", "Cr"}


def test_voucher_http_end_to_end(client) -> None:
    jid = client.post("/jobs", json={"entity_type": "voucher"}).json()["id"]
    csv = (
        b"vno,date,vtype,ledger,amt,drcr\n"
        b"RV1,2026-04-01,Receipt,Cash,5000,Dr\n"
        b"RV1,2026-04-01,Receipt,Customer 1,5000,Cr\n"
        b"JR1,2026-04-02,Journal,Rent,1200,Dr\n"
        b"JR1,2026-04-02,Journal,Cash,1200,Cr\n"
    )
    assert client.post(f"/jobs/{jid}/file", files={"file": ("v.csv", csv, "text/csv")}).status_code == 200
    mapping = {
        "voucher_number": "vno", "date": "date", "voucher_type": "vtype",
        "ledger_name": "ledger", "amount": "amt", "dr_cr": "drcr",
    }
    assert client.post(f"/jobs/{jid}/mapping", json={"mapping": mapping, "constants": {}}).status_code == 200

    t = client.post(f"/jobs/{jid}/validate", json={"known_groups": None}).json()["task_id"]
    v = _poll(client, jid, t)
    assert v["state"] == "done" and v["result"]["ok"] is True
    assert v["result"]["stats"]["vouchers"] == 2

    t = client.post(f"/jobs/{jid}/generate", json={"company": "Test1"}).json()["task_id"]
    g = _poll(client, jid, t)
    assert g["state"] == "done" and g["result"]["generated"] == 2

    xml = client.get(f"/jobs/{jid}/artifact").text
    assert "<REPORTNAME>Vouchers</REPORTNAME>" in xml
    assert xml.count("<VOUCHER ") == 2
    assert "<AMOUNT>-5000.00</AMOUNT>" in xml  # Cash debit -> negative
