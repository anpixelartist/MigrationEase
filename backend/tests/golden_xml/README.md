# Golden Tally XML fixtures

These are the **ground truth** the XML builder is tested against. They are captured, not authored:

1. **From a real TallyPrime** (Milestone 0 Spike A): hand-create one Unit, Group, Ledger (with a
   known **Cr** opening balance) and Stock Item in a licensed TallyPrime, then **export** them and
   save the raw XML here. This is the only authoritative source for the OPENINGBALANCE Dr/Cr sign,
   the `&#4;` list markers, and GST `RATEDETAILS` shape.
2. **From official Tally docs** (help.tallysolutions.com sample XML) for the envelope structure.

Naming: `<entity>_<action>_<case>.request.xml` / `.response.xml`
(e.g. `ledger_create_credit_opening.request.xml`).

CI golden-file tests assert our builder reproduces these byte-for-byte before any builder change
ships. **Do not commit vendor data we may not redistribute** — capture from our own licensed Tally
where possible, and treat official-doc samples as reference only.
