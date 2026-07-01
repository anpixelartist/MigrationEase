# Evaluation Note & Testing Criteria for the Agent

This document outlines the basic requirements, edge cases, and evaluation criteria for AI agents tasked with testing and implementing the Tally Migrator SaaS workflow.

## 1. Basic Import Workflow (The Golden Path)
The fundamental requirement of the project is that a user (or automated system) can upload an e-commerce export file (e.g., an e-commerce platform Sales CSV), and the system will model it and push it into Tally without errors.

**Test Scenario:**
1. Upload a standard e-commerce `Sales.csv`.
2. Select Cutover Date and target Tally Company.
3. System bypasses manual mapping (uses the appropriate platform template).
4. System validates the data (Place of Supply, Zero-Sum, HSN).
5. Bridge chunks and pushes the payload successfully into the local Tally Test Company.

## 2. Critical Edge Cases to Evaluate

When testing or building the data pipeline, the agent must ensure the following edge cases are handled gracefully:

- **Fractional Rounding (Zero-Sum):** If an order calculates CGST/SGST resulting in a 0.01 mismatch against the Gross Total, the pipeline must NOT fail. It must inject a `Round Off` ledger entry to balance the voucher exactly to 0.00.
- **Pre-Cutover Data:** If an order is dated *before* the user's defined Cutover Date, the pipeline must NOT create a voucher. The agent must verify that the total is correctly rolled up into the Party's `<OPENINGBALANCE>` in the Master envelope.
- **Refunds & Credit Notes:** If an order has a negative total or is flagged as a refund, the system must generate a Credit Note voucher (not a Sales voucher) and the original Order ID must be preserved in the Narration.
- **Inter-state vs. Intra-state Taxation:** Given a company based in Maharashtra (MH) and a shipping address in Karnataka (KA), the agent must verify the pipeline maps taxes to `IGST`. If shipping to MH, it must map to `CGST` and `SGST`.
- **Bridge Network Failure:** If the Tally HTTP server crashes mid-chunk, the bridge must halt, report the error via WebSocket, and the backend must allow the user to resume from the last successful chunk (Idempotency check).

## 3. Evaluation Criteria

The AI agent's work will be evaluated against the following strict constraints:
1. **Silent Failure Ban:** No data can be dropped silently. If an order is skipped, it must be logged and surfaced in the UI.
2. **Double Entry Integrity:** No voucher XML can be generated where `debits != credits`.
3. **Data Privacy (DPDP):** Temporary files and `staged_records` must have an explicit TTL or background worker confirming deletion after 30 days.
4. **Tally Protocol Adherence:** XML payloads must strictly adhere to the `All Masters` -> `Vouchers` dependency order, and contain properly escaped characters.

**Agent Task Checklist for Next Steps:**
- [ ] Review implementation strategy.
- [ ] Begin Phase 1 (Database schema for staging records).
- [ ] Implement universal Platform Mapping Template logic.
- [ ] Write integration tests for Tax routing and Zero-Sum assertions.
