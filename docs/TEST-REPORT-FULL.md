# TallyMigration — Full System Test Report

**47 passed · 0 failed · 0 informational** out of 47 blackbox checks · **0 silent failures**

Regression baseline: **170 backend unit/integration tests pass**; frontend type-checks + builds clean.

Fix from this pass: the parser dropped all-empty columns *silently*; it now reports them as a note
(`"N empty column(s) ignored: …"`) so the transformation is visible — keeping with the "nothing silent" rule.

## Method
- Driven over **HTTP** against the running server (the exact endpoints the frontend calls).
- Covers: auth/security, masters (4 types), vouchers (accounting + inventory), parsing/encoding, adversarial inputs, an explicit **silent-failure hunt**, live Tally push + read-back, and **scale**.
- Live Tally (`test123`) was reachable — pushed + read back.
- Scale timings: 5000 ledgers in 0.3s; 2500 vouchers/5000 lines in 3.6s.

> ✅ **No silent failures detected** — every bad input produced a loud error/4xx or a surfaced warning.

## Auth & security

| ID | Check | Expected | Observed | Result | Silent? |
|---|---|---|---|---|---|
| AUTH-1 | Signup returns token + owner org | token+org | token issued | ✅ PASS | — |
| SEC-1 | No token -> 401 | 401 | 401 | ✅ PASS | — |
| SEC-2 | Cross-org job access -> 404 | 404 | 404 | ✅ PASS | — |

## Masters happy

| ID | Check | Expected | Observed | Result | Silent? |
|---|---|---|---|---|---|
| MH-1 | Ledgers parse->validate->generate | generate done, 2 | ok=True gen=2 | ✅ PASS | — |
| MH-2 | Groups generate | generate done, 1 | gen=1 | ✅ PASS | — |
| MH-3 | Units generate | generate done, 1 | gen=1 | ✅ PASS | — |
| MH-4 | Messy amount (₹/commas/Cr) coerced | generate done | gen=1 warns={'bad_decimal': 1} | ✅ PASS | — |

## Masters edge/adversarial

| ID | Check | Expected | Observed | Result | Silent? |
|---|---|---|---|---|---|
| ME-1 | Empty file -> 422 empty_file | 422 empty_file | 422 empty_file | ✅ PASS | — |
| ME-2 | Header only -> 422 empty_file | 422 empty_file | 422 empty_file | ✅ PASS | — |
| ME-3 | Corrupt XLSX -> 422 parse_failed | 422 parse_failed | 422 parse_failed | ✅ PASS | — |
| ME-4 | CSV mislabeled .xlsx -> 422 | 422 parse_failed | 422 parse_failed | ✅ PASS | — |
| ME-5 | Duplicate names -> duplicate_in_file (blocks) | duplicate_in_file | errs={'duplicate_in_file': 2} gen=error | ✅ PASS | — |
| ME-6 | Unknown group vs snapshot -> unknown_group | unknown_group | errs={'unknown_group': 1} | ✅ PASS | — |
| ME-7 | Whitespace-only required name -> required_missing | required_missing | errs={'required_missing': 1} | ✅ PASS | — |
| ME-8 | Name > 100 chars -> name_too_long | name_too_long | errs={'name_too_long': 1} | ✅ PASS | — |
| ME-9 | Unit name with space -> bad_unit_name | bad_unit_name | errs={'bad_unit_name': 1} | ✅ PASS | — |
| ME-10 | Bad GSTIN -> warning, non-blocking | bad_gstin warn + generate | warns={'bad_gstin': 1} gen=done | ✅ PASS | — |
| ME-11 | Constant on unknown field -> 400 (not silently dropped) | 400 bogus | 400 ['bogus'] | ✅ PASS | — |
| ME-12 | Map to non-existent column -> 400 | 400 | 400 bad_request | ✅ PASS | — |

## Parsing/encoding

| ID | Check | Expected | Observed | Result | Silent? |
|---|---|---|---|---|---|
| PE-1 | Semicolon delimiter | handled | cols=2 parse_ok=True | ✅ PASS | — |
| PE-2 | Tab delimiter | handled | cols=2 parse_ok=True | ✅ PASS | — |
| PE-3 | Single column (no space-split) | handled | cols=1 parse_ok=True | ✅ PASS | — |
| PE-4 | Latin-1 accents | handled | cols=2 parse_ok=True | ✅ PASS | — |
| PE-5 | UTF-8 BOM header | handled | cols=2 parse_ok=True | ✅ PASS | — |
| PE-6 | Valid XLSX | handled | cols=2 parse_ok=True | ✅ PASS | — |

## Vouchers happy

| ID | Check | Expected | Observed | Result | Silent? |
|---|---|---|---|---|---|
| VH-1 | Receipt+Journal grouped, previewed, generated | 2 vouchers, balanced | preview=2 balanced=True ok=True gen=2 | ✅ PASS | — |
| VH-2 | Sales invoice with inventory item line | 1 voucher, balanced, generated | preview=1 balanced=True gen=1 | ✅ PASS | — |
| VH-3 | Separate debit/credit columns | 1 voucher balanced | preview=1 gen=1 | ✅ PASS | — |

## Vouchers adversarial

| ID | Check | Expected | Observed | Result | Silent? |
|---|---|---|---|---|---|
| VA-1 | Unbalanced voucher -> blocks | voucher_unbalanced | errs={'voucher_unbalanced': 1} gen=error | ✅ PASS | — |
| VA-2 | Conflicting dates in one voucher | voucher_conflict | errs={'voucher_conflict': 1} | ✅ PASS | — |
| VA-3 | Conflicting voucher types | voucher_conflict | errs={'voucher_conflict': 1} | ✅ PASS | — |
| VA-4 | Both debit+credit on one line | validation_error | errs={'validation_error': 1, 'voucher_unbalanced': 1} | ✅ PASS | — |
| VA-5 | Zero amount line | required_missing | errs={'required_missing': 2} | ✅ PASS | — |
| VA-6 | Bad date | bad_date | errs={'bad_date': 2, 'required_missing': 1} | ✅ PASS | — |
| VA-7 | Row missing voucher number | required_missing | errs={'required_missing': 1} | ✅ PASS | — |
| VA-8 | Inventory qty*rate != amount -> warning (non-block) | inventory_mismatch warn | warns={'inventory_mismatch': 1} gen=done | ✅ PASS | — |
| VA-9 | Inventory negative qty -> error | inventory_mismatch | errs={'inventory_mismatch': 1, 'voucher_unbalanced': 1} | ✅ PASS | — |
| VA-10 | Inventory item without unit -> error | inventory_mismatch | errs={'inventory_mismatch': 1} | ✅ PASS | — |

## Silent-failure hunt

| ID | Check | Expected | Observed | Result | Silent? |
|---|---|---|---|---|---|
| SF-1 | Good+bad vouchers: bad reported, not dropped | validate not ok + unbalanced | ok=False errs={'voucher_unbalanced': 1} | ✅ PASS | — |
| SF-2 | All-bad file: generate refuses (not empty success) | validate not ok / no generate | ok=None gen=None/None | ✅ PASS | — |
| SF-3 | Null-ish tokens (NA/-) -> blank, generate ok | generate done | gen=done generated=2 | ✅ PASS | — |

## Live Tally + read-back

| ID | Check | Expected | Observed | Result | Silent? |
|---|---|---|---|---|---|
| LT-1 | Push 2 ledgers -> Tally | created/altered=2, errors=0 | created=0 altered=2 errors=0 | ✅ PASS | — |
| LT-2 | ODBC/gateway read-back of pushed ledgers | both present | QF Cust 1&2 present=True | ✅ PASS | — |
| LT-3 | Push Receipt voucher -> Tally | created>=1, is_success | created=1 is_success=True line_errors=[] | ✅ PASS | — |
| LT-4 | Push Sales+inventory voucher -> Tally | created>=1 | created=1 is_success=True line_errors=[] | ✅ PASS | — |

## Scale / performance

| ID | Check | Expected | Observed | Result | Silent? |
|---|---|---|---|---|---|
| SP-1 | 5000 ledgers parse->validate->generate | generate 5000 | generated=5000 in 0.3s | ✅ PASS | — |
| SP-2 | 2500 vouchers (5000 lines) validate->preview->generate | generate 2500 | generated=2500 preview=2500 in 3.6s | ✅ PASS | — |
