# Implementation Strategy & Architecture Comparison

## 1. Current State vs. Target Architecture

The current application (as documented in `TEST-REPORT.md` and the existing `app/pipeline/`) implements a generalized CSV-to-Tally migration tool. It follows a direct 1:1 row-to-voucher parsing mechanism with manual column mapping. The new PRD significantly shifts the scope toward a specialized **E-commerce Financial Engine (Universal)** with strict compliance, tax routing, and idempotency guarantees.

### Key Architectural Shifts

| Component / Feature | Current Implementation (Status Quo) | Target Implementation (PRD Requirements) |
| :--- | :--- | :--- |
| **Voucher Grouping** | **Naive 1:1 Mapping:** 1 CSV row = 1 Ledger entry. Grouped strictly by explicit `voucher_number` column. | **N-to-1 Aggregation:** Rows are grouped by `order-id`. E-commerce entries (Sales, Fees, Taxes) are mapped to multi-leg journals. |
| **Mapping UX** | **Manual Column Mapping:** User manually maps CSV columns to Tally fields. | **Pre-Built Templates:** Auto-mapped based on the source platform. Eradicate the mapping step for standard inputs. |
| **Balancing Logic** | **Strict Match:** Asserts Debits == Credits. Fails if unmatched. | **Zero-Sum Routing:** Calculates totals, auto-routes fractional paisa discrepancies to a designated "Round Off" ledger. |
| **Tax & GST** | **Manual/None:** Relies on user to provide explicit GST/IGST ledgers in CSV. | **Dynamic Engine:** Calculates Place of Supply (Shipping vs Home state), splits IGST vs CGST/SGST, and flags B2B vs B2C. |
| **Tally Export Flow** | **Separated/Monolithic:** Masters and Vouchers are separate jobs. Bridge relays single monolithic XML payload. | **Unified & Chunked:** Emits Master envelope *then* Voucher envelope. Bridge slices payloads into chunks (e.g. 200 vouchers) and reports progress via WS. |
| **Cutover & History** | **Vouchers Only:** Every row generates a voucher. | **Date Gateway:** Pre-cutover transactions are rolled up into `<OPENINGBALANCE>` tags; post-cutover become vouchers. |
| **Bridge Safety** | **Blind Push:** Pushes XML blindly. | **Sandbox & Pre-fetch:** Enforces first push to Test Company. Queries `LASTVCHID` for idempotency (`ACTION="Alter"`). |

---

## 2. Implementation Strategy & Phasing

To transform the codebase without breaking the existing pipeline, we must implement an intermediary **E-commerce Aggregation Layer** that sits between the file parser and the XML builder.

### Phase 1: Pipeline Overhaul (The Aggregation Layer)
1. **Database Schema Update:** Introduce a `staged_records` table to hold raw parsed rows securely. Implement the 30-day auto-erasure cron job for DPDP compliance.
2. **Template Engine:** Replace `MappingGuide.tsx` and manual mapping logic with a universal `PlatformTemplate` system. Pre-define expected columns for supported platforms and automatically route Gross Sales, Commissions, and Payouts to standard Tally ledgers.
3. **Aggregation & Balancing:**
   - Group parsed rows by `order-id`.
   - Calculate Place of Supply (tax split).
   - Enforce Zero-Sum logic and inject a `Round Off` leg if a fractional difference exists.
4. **Cutover Router:** Intercept rows during generation. Roll pre-cutover dates into Master Ledgers as opening balances, discarding the voucher generation.

### Phase 2: XML & Bridge Modernization
1. **Unified Envelope Generation:** Refactor `build_and_serialize` to accept both Masters and Vouchers, outputting them in strict dependency order (`All Masters` followed by `Vouchers`).
2. **Bridge Chunking & Status Updates:**
   - Modify the Go bridge (`cmd/bridge/main.go` and `internal/relay/`) to parse the unified XML and extract chunks of `<VOUCHER>` tags.
   - Send `POST` to Tally in loops, capture `<LINEERROR>`, and emit WebSocket messages to the backend.
3. **Idempotency Queries:** Add a pre-fetch step in the Go bridge to query `http://localhost:9000` via XML for existing `VOUCHERNUMBER`s, modifying the XML `ACTION` attribute dynamically before pushing.

### Phase 3: Frontend Evolution
1. **Onboarding Wizards:** Build UI for Platform Selection, Cutover Date configuration, and explicit "Export Guides".
2. **HSN/SAC Mapping Step:** Add a new UI view specifically for mapping SKUs to HSN codes before XML generation.
3. **Trial Balance Pre-Flight:** Implement a comparative Trial Balance dashboard summarizing generated vouchers vs expected platform totals.
4. **Progress UI:** Hook the WebSocket updates from the Bridge chunking into a real-time progress bar.

This strategy ensures that the heavy business logic is centralized in the backend aggregation layer, keeping the frontend fast and the Go bridge strictly focused on local transport and chunking.
