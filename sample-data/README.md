# Sample data (for demos)

Realistic, sizable sample datasets to show MigrationEase working end-to-end. All files are seeded and
reproducible — regenerate with `python sample-data/generate.py`. (Verified: every file runs through
the pipeline with **0 errors**.)

| File | Rows | Import as | Demonstrates |
|---|---:|---|---|
| `ledgers.csv` | 1,000 | **Ledgers** | Masters at scale — customers (Sundry Debtors) + suppliers (Sundry Creditors) with opening balances, GSTINs, states. |
| `stock-items.csv` | 500 | **Stock Items** | Products with **HSN codes** + GST rates + opening stock. |
| `sales-gst.csv` | 2,000 | **Vouchers** | ⭐ The GST engine: place-of-supply **CGST/SGST vs IGST** computed from a rate, **B2B** (with GSTIN) vs **B2C**, and **refunds → Credit Notes**. |
| `marketplace-settlements.csv` | 400 | **Vouchers** + *Settlement mode* | Marketplace settlements → multi-leg journals (Bank + Commission + Fees + TCS = gross). |

## How to demo each

1. Sign in (SSO) → **Import**.
2. **Ledgers:** pick *Ledgers* → upload `ledgers.csv` → the columns auto-map → Validate → set your
   Tally company on Plan → Download XML or Push. (Import ledgers/stock items **before** vouchers if
   you'll push to Tally, so the referenced accounts exist.)
3. **GST sales:** pick *Vouchers* → upload `sales-gst.csv` → map → Validate → on **Plan**, tick
   **B2C daily summary** to consolidate retail sales, or leave it off for per-order invoices →
   Generate. You'll see CGST/SGST on same-state orders, IGST on inter-state, and refunds as Credit Notes.
4. **Settlements:** pick *Vouchers* → upload `marketplace-settlements.csv` → map → on **Plan** tick
   **Marketplace settlement mode** → Generate.

> Numbers are illustrative (random names/amounts) — no real customer data. GSTINs are format-valid
> but fictional. The company's home state in the sales file is **Maharashtra**.
