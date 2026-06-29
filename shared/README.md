# shared/ — the canonical contract

This directory holds the **canonical Tally field catalog**: the single source of truth that the
auto-mapper, the validator, and the XML builder all read. Source columns are mapped *into* these
canonical fields; the `tally_tag` on each field is the output concern.

## `canonical_fields/<entity>.json`

One file per master type (`ledger`, `group`, `stock_item`, `unit`). Each is:

```jsonc
{
  "entity": "ledger",
  "schema_version": 1,
  "tally_element": "LEDGER",          // the <LEDGER> tag in <TALLYMESSAGE>
  "identity": { "scope": "company", "key": ["name"] },  // see note below
  "fields": [
    {
      "key": "parent",                // canonical key (also the pydantic attribute)
      "label": "Group / Parent",      // human label (shown in the mapping UI)
      "tally_tag": "PARENT",          // output XML tag
      "dtype": "group_ref",           // string|decimal|bool|date|enum|unit_ref|group_ref|amount
      "required": true,
      "unique": false,
      "synonyms": ["group", "under", "head", "account group"],
      "value_set_ref": "tally_groups",// late-bound from the user's master snapshot (validation)
      "regex": null,
      "example": "Sundry Debtors"
    }
  ]
}
```

### Identity scope (drives create-vs-update — see plan §11.6)

- **Ledger** identity is **company-global NAME** — `parent` is an *attribute to update*, NOT part
  of the key. Two ledgers cannot share a name in one company.
- **Group / StockItem / Unit** identity is **NAME within their own class**.

Update targeting priority is **GUID > MASTERID > NAME**; `ALTERID` is a concurrency check only.

## Do not put runtime code here

`shared/` is data + types only, consumed by `backend/` (Python) and `frontend/` (TS). Keep it
language-neutral JSON.
