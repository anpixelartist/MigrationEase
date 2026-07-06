"""Saved mapping templates: CRUD, org isolation, built-ins, and auto-map overlay."""

from __future__ import annotations

import io

from tests.conftest import make_authed


def _ledger_csv() -> bytes:
    return b"Account Title,Group,Opening\nAcme,Sundry Debtors,100\nBeta,Sundry Debtors,200\n"


def _save(client, name="My Ledgers", entity="ledger", mapping=None, constants=None, cols=None):
    body = {
        "name": name,
        "entity_type": entity,
        "mapping": mapping or {"name": "Account Title", "parent": "Group"},
        "constants": constants or {},
        "source_columns": cols or ["Account Title", "Group", "Opening"],
    }
    return client.post("/templates", json=body)


def test_save_list_delete_template(client):
    r = _save(client)
    assert r.status_code == 201, r.text
    tid = r.json()["id"]
    assert r.json()["mapping"] == {"name": "Account Title", "parent": "Group"}

    lst = client.get("/templates", params={"entity_type": "ledger"}).json()
    names = [t["name"] for t in lst]
    assert "My Ledgers" in names

    d = client.delete(f"/templates/{tid}")
    assert d.status_code == 204
    assert "My Ledgers" not in [t["name"] for t in client.get("/templates", params={"entity_type": "ledger"}).json()]


def test_save_is_upsert_by_name(client):
    _save(client, mapping={"name": "Account Title", "parent": "Group"})
    _save(client, mapping={"name": "Account Title"})  # same name -> overwrite
    lst = [t for t in client.get("/templates", params={"entity_type": "ledger"}).json() if not t["builtin"]]
    assert len(lst) == 1
    assert lst[0]["mapping"] == {"name": "Account Title"}


def test_templates_are_org_isolated(raw_client):
    a = make_authed(raw_client, email="a@example.com")
    b = make_authed(raw_client, email="b@example.com")
    _save(a, name="A only")
    a_list = [t["name"] for t in a.get("/templates", params={"entity_type": "ledger"}).json()]
    b_list = [t["name"] for t in b.get("/templates", params={"entity_type": "ledger"}).json()]
    assert "A only" in a_list
    assert "A only" not in b_list


def test_builtin_shopify_listed_for_vouchers_only(client):
    v = client.get("/templates", params={"entity_type": "voucher"}).json()
    assert any(t["builtin"] and t["name"] == "shopify" for t in v)
    lg = client.get("/templates", params={"entity_type": "ledger"}).json()
    assert not any(t["builtin"] for t in lg)


def test_saved_template_overlays_auto_map(client):
    # Save a template whose source columns match a file we then upload.
    _save(client, mapping={"name": "Account Title", "parent": "Group"})

    job_id = client.post("/jobs", json={"entity_type": "ledger"}).json()["id"]
    client.post(f"/jobs/{job_id}/file", files={"file": ("ledgers.csv", io.BytesIO(_ledger_csv()), "text/csv")})
    proposal = client.get(f"/jobs/{job_id}/mapping/suggestions").json()

    assert proposal["applied_template"] == "My Ledgers"
    by_field = {s["target_field"]: s for s in proposal["suggestions"]}
    assert by_field["name"]["source_column"] == "Account Title"
    assert by_field["name"]["method"] == "template"
    assert by_field["parent"]["source_column"] == "Group"
    # required fields are now satisfied by the template
    assert proposal["unmapped_required"] == []


def test_no_template_no_overlay(client):
    job_id = client.post("/jobs", json={"entity_type": "ledger"}).json()["id"]
    client.post(f"/jobs/{job_id}/file", files={"file": ("ledgers.csv", io.BytesIO(_ledger_csv()), "text/csv")})
    proposal = client.get(f"/jobs/{job_id}/mapping/suggestions").json()
    assert proposal["applied_template"] is None
