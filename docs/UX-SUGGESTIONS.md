# UX & Ease-of-Use Suggestions

This document tracks ideas and improvements for the Ledgerbridge application from the perspective of an accountant or Tally user. These can be implemented over time.

## Terminology & Clarity
- [ ] **Clarify "Under" mapping:** Add a clearer placeholder like `"e.g., Sundry Debtors"` when using a fixed value for "Under (Group/Parent Group)" to guide users.
- [ ] **Clarify "Voucher Type":** Add an inline tooltip or explicit warning during mapping to clarify that Voucher Type must be exactly `Sales`, `Purchase`, `Receipt`, `Payment`, or `Journal`, and is *not* a Group name.

## Error Handling & Feedback
- [ ] **Mapping Help Messages:** If required fields are unmapped (which disables the Validate button), show an automated hint: `"Need help? Usually, 'Under' is a fixed value like 'Sundry Debtors' for all rows."`
- [ ] **Download Error Rows:** In the Validation View, add a "Download error rows as CSV" button so users can easily isolate, fix, and re-upload the problematic rows without losing their place.

## General Flow Improvements
- [ ] **Drag and Drop Uploads:** Update the file upload area to support an explicit drag-and-drop overlay style for a more modern feel.
- [ ] **Persistent Company Name:** Save the target Tally company name to `localStorage` during the "Plan" step and auto-fill it for future imports to save users from re-typing it.
- [ ] **Clickable Stepper (Backwards):** Make the top stepper navigation clickable so users can easily jump back to previous steps (e.g., from Plan back to Map) without clicking "Back" multiple times.

## Architectural Flaws & Backlog
- [ ] **N-to-1 Voucher Grouping:** Introduce an aggregation layer between the mapping UI and the database. Staged records must group by `order-id` to build a unified voucher header with child ledger legs.
- [ ] **Zero-Sum Balance Assertion:** Validate that `Σ(signed AMOUNT) == 0.00` for every voucher. Calculate the party total from the platform, and automatically route fractional discrepancies to a "Round Off" ledger.
- [ ] **Master-Before-Voucher Dependency Emit:** Ensure the XML generator emits a Masters Envelope (`REPORTNAME = All Masters`) before the Vouchers Envelope. Dependency order: Units → Stock Groups → Stock Items → Groups → Ledgers.

## Systems Improvements (Parsing & Integrity)
- [ ] **Hostile File Encodings:** Detect and strip BOMs (`EF BB BF`) immediately upon file read.
- [ ] **XML Escaping:** Implement a strict XML escaper (e.g., converting `&` to `&amp;`) exactly at the XML emission stage.
- [ ] **Deduplication Key Collisions:** Update the deduplication/reference key (`REMOTEID`) to be a composite of `(order-id, amount-type, transaction-type, date)`.
- [ ] **Locale-Aware Date/Number Parsing:** Enforce explicit locale format pickers during upload. Normalize all dates to `YYYYMMDD` (converting to the company's local IST timezone) before XML generation.

## High Priority UX & Business Logic
- [ ] **"Export Guide" Onboarding:** Provide explicit, platform-specific guides detailing exactly which reports to export before the upload screen.
- [ ] **Pre-built Templates:** Eradicate the mapping screen for standard platforms. Auto-map 100% of standard fields based on selected platform.
- [ ] **Bulk Error Resolution:** Group identical validation errors and provide 1-click bulk resolutions (e.g., for missing GSTINs).
- [ ] **Clear Finish Line:** Add a definitive success screen at the end of the journey, providing the downloaded XML and exact steps to import it into Tally.

## High-Level System Architecture & Bridge Strategy
- [ ] **Bridge WebSocket Chunking:** Refactor the bridge flow to prevent pushing one massive file. Slice `<VOUCHER>` tags into chunks (e.g., 200 per request) before pushing to `http://localhost:9000`. Parse Tally's response after each chunk, extract `<LINEERROR>` tags, and update frontend progress.
- [ ] **Bridge Pre-Fetch & Idempotency:** The bridge must query Tally (`<EXPORTDATA>`) for the `LASTVCHID` or reference before pushing. If the voucher exists, emit with `ACTION="Alter"`.
- [ ] **Sandbox Gate:** The Go adapter must enforce routing the first push into a designated Test Company by verifying `SVCURRENTCOMPANY`.
- [ ] **DPDP Data Erasure:** Implement an async worker task enforcing a strict 30-day auto-erasure policy for staging data and uploaded files.

## E-commerce Financial Logic (Universal App)
- [ ] **Cutover Logic & Opening Balances:** Transactions prior to the project Cutover Date must NOT be generated as vouchers. Instead, they must be aggregated into an `<OPENINGBALANCE>` tag within the respective `<LEDGER>` XML node.
- [ ] **B2B vs B2C Split:** Aggregate B2C sales into daily summaries. Generate B2B invoices (with a GSTIN) as individual vouchers for GSTR-1 compliance.
- [ ] **Marketplace Settlements (Multi-leg Journals):** Model settlements dynamically as multi-leg entries: Gross Sales (Cr) against Commission (Dr), Fulfillment Fees (Dr), TCS (Dr), and Bank Deposit (Dr).
- [ ] **Refund Matching (Credit Notes):** Map refunds as Credit Notes and inject the original Platform Order ID into the `<NARRATION>` to link them properly for tax reversal.

## GST & Tax Compliance Engine
- [ ] **Place of Supply (IGST vs CGST/SGST):** Calculate tax splits dynamically based on Shipping State vs Home State. Ensure B2B invoices populate `PARTYGSTIN` properly.
- [ ] **HSN/SAC Code Enrichment:** Introduce a specific mapping stage for HSN code enrichment before generating `<STOCKITEM>` nodes.
- [ ] **Trial Balance Reconciliation (Pre-Flight):** Generate a comparative Trial Balance pre-import. Isolate timing differences from actual accounting discrepancies to present a clear picture to the user.
