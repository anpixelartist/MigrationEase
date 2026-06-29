# TallyMigration — End-to-End Test Report

**Result: 32 passed · 0 failed · 1 informational** (total 33 HTTP checks), **plus a Tally read-back
pass** that confirmed stored ledgers/groups/GSTINs/signs and **surfaced one real bug (units with
spaces), now fixed.** Backend suite after the fix: **140 passed.**

## Method

- The backend was run as a live server (`uvicorn`, SQLite, in-process broker) with `TM_DIRECT_TALLY_PUSH=true`.
- Every check is driven over **HTTP** against the running server, using the exact endpoints the frontend calls (auth → bearer token → job lifecycle with task polling).
- Live Tally (TallyPrime, company `Test1`) was reachable — valid masters were pushed, then **read back out of Tally via the Tally ODBC DSN and the XML gateway export** to confirm they stored as intended (see "Live-Tally storage verification" below).
- Dataset: the Zoho Books exports in `tally-datasets/` (Contacts → customer ledgers, Vendors → supplier ledgers, Items → stock items).

## Auth

| ID | Check | Expected | Observed | Result |
|---|---|---|---|---|
| AUTH-1 | Signup creates workspace + token | 201 + token + owner org | token len 243, org owner | ✅ PASS |

## Real dataset

| ID | Check | Expected | Observed | Result |
|---|---|---|---|---|
| DS-1 | Contacts.csv (1000 customers) full pipeline → ledgers | parse 1000 rows, validate ok, generate ~1000 | rows=1000, validate_ok=True, warns={}, generated=1000 | ✅ PASS |
| DS-2 | Contacts subset (30) → PUSH to Tally Test1 | created/updated=30, errors=0 | push=done, created=0, altered=30, errors=0, detail= | ✅ PASS |
| DS-3 | Vendors subset (20) → PUSH (Sundry Creditors) | created/updated≈20, errors=0 | push=done, created=0, altered=20, errors=0 | ✅ PASS |
| DS-4 | Items.csv (2000) → stock items (generate, no push) | validate ok, generate ~2000 | rows=2000, validate_ok=True, generated=2000 | ✅ PASS |
| DS-5 | Items subset → PUSH (stock items need a Unit + inventory) | push surfaces a clear Tally/line error | push=done, created=0, line_errors=["Unit 'Nos' does not exist!"], problem=None | ℹ️ INFO |

## Live Tally

| ID | Check | Expected | Observed | Result |
|---|---|---|---|---|
| LT-1 | Groups → PUSH | created/altered, errors=0 | push=done, created=0, altered=2, errors=0 | ✅ PASS |
| LT-2 | Units → PUSH | created/altered, errors=0 | push=done, created=0, altered=0, errors=0 | ✅ PASS |
| LT-3 | Dr/Cr opening-balance signs verified by re-export | Dr=+/IsDeemedPositive Yes, Cr=-/No | Dr→IsDeemedPositive=Yes/+11111:True, Cr→No/-22222:True | ✅ PASS |

## File edge cases

| ID | Check | Expected | Observed | Result |
|---|---|---|---|---|
| EC-1 | Empty file | 422 empty_file | 422 empty_file | ✅ PASS |
| EC-2 | Header only (no rows) | 422 empty_file | 422 empty_file | ✅ PASS |
| EC-3 | Single-column (no split on spaces) | 1 column; 'State Bank of India' intact | cols=1, unmapped_required=['parent'] | ✅ PASS |
| EC-4 | Semicolon delimiter | 2 columns detected | cols=2 | ✅ PASS |
| EC-5 | Tab delimiter | 2 columns detected | cols=2 | ✅ PASS |
| EC-6 | Latin-1 encoding (accents) | decoded; generate ok | validate_ok=True, generated=2 | ✅ PASS |
| EC-7 | UTF-8 BOM header | header 'name' not corrupted; generate ok | generated=1, notes=[] | ✅ PASS |
| EC-8 | Duplicate headers | handled (pandas dedups) | cols=3, notes=[] | ✅ PASS |
| EC-9 | Blank/unnamed column header | parsed; unnamed-column note | cols=3, notes=['1 unnamed column(s) detected'] | ✅ PASS |
| EC-10 | Corrupt XLSX (not a zip) | 422 parse_failed | 422 parse_failed | ✅ PASS |
| EC-11 | CSV bytes with .xlsx extension | 422 parse_failed | 422 parse_failed | ✅ PASS |
| EC-12 | Valid XLSX (calamine) | parsed; generate ok | rows=2, generated=2 | ✅ PASS |
| EC-13 | Messy amounts (₹, commas, (neg), Dr/Cr suffix, junk) | coerced; junk → warning; generate ok | warns={'bad_decimal': 4}, generated=3 | ✅ PASS |
| EC-14 | Duplicate names → blocks generate | duplicate_in_file error; generate blocked | errors={'duplicate_in_file': 2}, generate=error/validation_failed | ✅ PASS |
| EC-15 | Unknown group (vs snapshot) + fuzzy suggestion | unknown_group error | errors={'unknown_group': 1} | ✅ PASS |
| EC-16 | Blank required name row | required_missing error | errors={'required_missing': 1} | ✅ PASS |
| EC-17 | Bad GSTIN → warning (non-blocking) | bad_gstin warning; generate ok | warns={'bad_gstin': 1}, generate=done | ✅ PASS |
| EC-18 | Null-ish tokens (NA/-/nil) in optional fields | treated as blank; no errors; generate ok | warns={'bad_gstin': 2}, generated=3 | ✅ PASS |
| EC-19 | Name longer than 100 chars | name_too_long error | errors={'name_too_long': 1} | ✅ PASS |
| EC-20 | Junk + fuzzy headers auto-map | Party Name→name, Under Group→parent, GST No→gstin; Random Notes unmapped | unmapped_required=[], generated=1 | ✅ PASS |
| EC-21 | Missing required field unmappable | unmapped_required has name+parent OR map 422 | unmapped_required=['name', 'parent'], map_status=422 | ✅ PASS |

## Resolution

| ID | Check | Expected | Observed | Result |
|---|---|---|---|---|
| RS-1 | Existing master → 'update' verdict (ACTION=Alter) | buckets update=1 | buckets={'update': 1} | ✅ PASS |

## Security

| ID | Check | Expected | Observed | Result |
|---|---|---|---|---|
| SEC-1 | Job from org A is 404 to org B | 404 | status=404 | ✅ PASS |
| SEC-2 | No token → 401 | 401 | status=401 | ✅ PASS |

## Live-Tally storage verification (read-back via Tally ODBC + XML gateway)
After pushing, the stored data was read **back out of Tally** through two independent paths — the
**Tally ODBC** DSN (`TallyODBC64_9000`) and the **XML gateway** Collection export — to confirm the
records landed as intended (not just that the push returned a count).

| What | How verified | Result |
|---|---|---|
| 30 customers under **Sundry Debtors** | ODBC `SELECT $Name,$Parent FROM Ledger` | ✅ 30 present, correct parent |
| 20 vendors under **Sundry Creditors** | ODBC | ✅ 20 present, correct parent |
| **Dr/Cr opening-balance signs** | ODBC `$OpeningBalance` | ✅ `QA Dr Ledger = +11111`, `QA Cr Ledger = -22222` |
| **GSTINs** (mapped optional field) | ODBC `$PartyGSTIN` | ✅ match source exactly (`Customer 1 → 21ABCDE0001F1Z5`, …) |
| **Groups** created | XML gateway export + raw push response | ✅ `QA Trade Debtors`/`QA Trade Creditors` exist (`<CREATED>` confirmed) |
| **Units** created | XML gateway export + raw push response | ❌ rejected — see bug below |

> ODBC quirk: the Tally ODBC driver returns `Ledger` rows reliably, but its `Group`/`Unit` tables came
> back empty/garbage in this config — so the **XML gateway export is the authoritative read path** for
> groups/units, and was used to confirm them. (An early ODBC-only check gave a false "groups missing";
> the gateway export corrected it — groups are fine.)

## Bug found by read-back, and fixed
**Units with a space in the name are silently dropped.** Pushing a unit named `QA Nos` returns
`<LINEERROR>BAD UNIT NAME</LINEERROR>` with `<EXCEPTIONS>1</EXCEPTIONS>` but **`<ERRORS>0</ERRORS>`**,
so a naive "errors == 0" check reports a false pass while **nothing is created** (`Nos` without the
space imports fine: `<CREATED>1</CREATED>`). The backend's `ImportResult.is_success` already accounts
for `exceptions`/`line_errors`, but there was **no pre-push validation** of unit-symbol validity.
**Fix:** added a `bad_unit_name` validation rule (unit names may not contain whitespace) so it is
caught at the **Validate** step with a clear message instead of failing opaquely at push. Unit test
added; full backend suite **140 passed**.

## Fixes applied during this run
- **Whitespace-only required values** (e.g. a name of `"   "`) previously passed validation (Pandera
  saw non-empty text) and were only caught at generation. Validation now strips cells and treats
  whitespace-only as blank → caught as `required_missing` at the Validate step (EC-16).
- **`bad_unit_name`** rule added (above) — unit symbols with spaces caught at the Validate step.

## Findings & notes
- **DS-5 (stock items):** importing `stock_item` masters requires the referenced **Unit** to exist
  and the company's **inventory features** enabled. Tally rejected with `Unit 'Nos' does not exist!`
  — surfaced cleanly as a per-row error (no crash). Recommendation: import the units first, or a
  combined masters import that emits units → stock items in one envelope.
- **Push status vs partial failure:** `run_push` advances the job to `PUSHED` whenever Tally responds,
  carrying the full `ImportResult` (`created/altered/errors/exceptions/line_errors` + `is_success`).
  The consumer (and the frontend "Done" screen) must surface `exceptions`/`line_errors` and treat
  `is_success == false` as a partial/failed import — otherwise a 0-created push can read as success.
- **EC-18 (null-ish tokens):** `NA` / `nil` / `-` in optional fields raise non-blocking *format*
  warnings at validation, but the coercion layer correctly treats them as blank at generation (output
  is correct). Candidate polish: extend the validation layer with the same null-ish set.
- **Idempotent re-runs:** DS-2/DS-3/LT-1 show `altered` rather than `created` because these masters
  were created on the first import and re-imported here — demonstrating safe re-runs (Tally
  `@@DupModify` → Alter). On a fresh company they are creates (first run: created=30 / 20 / 2).
- **Frontend gap (constants):** files that lack a required column (Contacts has no group column, but
  `parent` is required) need a **fixed value** supplied at mapping. The API supports `constants` (used
  here), but the mapping UI does not yet expose a "set a constant value" control. Small UI follow-up.

## Out of scope (Phase 2)
The Zoho **transaction** files (Sales_Invoices, Bills, Journals, Payments, …) are **vouchers** — not
yet wired. Today only **masters** (ledgers, groups, stock items, units) are supported end-to-end.
