# Ledgerbridge — Import & Mapping Cheatsheet

Keep this open while you fill in the **Map** step. It tells you what each field means, which are
**required**, what a valid value looks like, and when to map a **column** vs a **fixed value**.

---

## 0. The 3 golden rules

1. **Map to a *column*** when the value changes row-to-row (a name, an amount, a ledger).
   **Use "✎ Set a fixed value"** only when the value is the **same for every row in the file**
   (e.g. the whole file is Sales vouchers → `Voucher Type = Sales`).
2. **Masters must exist before they're used.** Import in this order:
   **Units → Groups → Ledgers & Stock Items → Vouchers.** A voucher that names a ledger which
   doesn't exist in Tally yet will be rejected.
3. **The target company must be open in Tally** (Gateway → *Select Company*) and its name typed on
   the **Plan** step must match.

---

## 1. Which import type do I pick? (the Upload step)

| Pick | When your file is… | Tally calls these |
|---|---|---|
| **Ledgers** | a list of customers / suppliers / bank / income / expense accounts | accounting heads where balances live |
| **Groups** | categories that organize ledgers | the chart-of-accounts tree |
| **Units** | units of measure (Nos, Kg…) | for stock |
| **Stock Items** | products you buy/sell | inventory |
| **Vouchers** | **transactions** — sales, purchases, payments, receipts, journals | the actual entries |

---

## 2. LEDGERS

| Field | Required? | Notes / valid values | Example |
|---|---|---|---|
| **Ledger Name** | ✅ | unique; ≤ 100 chars | `Customer ABC` |
| **Under (Group)** | ✅ | a Tally group — see common groups below | `Sundry Debtors` |
| Opening Balance | – | a number (₹, commas, brackets all OK) | `15000` |
| Opening Dr/Cr | – | `Dr` or `Cr` (debit/credit nature of the opening balance) | `Cr` |
| GSTIN | – | 15-char GSTIN format | `29ABCDE1234F1Z5` |
| GST Reg. Type | – | `Regular` / `Composition` / `Unregistered` / `Consumer` | `Regular` |
| PAN, State, Email, Phone, Pincode, Credit Limit, Mailing Name, Address | – | optional details | |

**Common groups** to put ledgers `Under`: `Sundry Debtors` (customers), `Sundry Creditors`
(suppliers), `Bank Accounts`, `Cash-in-Hand`, `Sales Accounts`, `Purchase Accounts`,
`Direct Expenses`, `Indirect Expenses`, `Duties & Taxes` (GST ledgers), `Current Assets`,
`Current Liabilities`, `Capital Account`.

> **No group column in your file?** Set **Under** = a fixed value (e.g. a customer list → all
> `Sundry Debtors`).

---

## 3. GROUPS

| Field | Required? | Notes | Example |
|---|---|---|---|
| **Group Name** | ✅ | | `Trade Debtors` |
| **Under (Parent Group)** | ✅ | the parent group (or a built-in one) | `Sundry Debtors` |
| Is Revenue / Debit Nature / Bill-wise / Affects Gross Profit | – | `Yes`/`No` | |

---

## 4. UNITS

| Field | Required? | Notes | Example |
|---|---|---|---|
| **Unit Symbol** | ✅ | **NO SPACES** — Tally rejects `QA Nos`. Use `Nos`, `Kg`, `Pcs`, `Ltr`, `Box`, `Mtr` | `Nos` |
| Decimal Places | – | `0` for whole numbers, `2` for fractional | `0` |

---

## 5. STOCK ITEMS

| Field | Required? | Notes | Example |
|---|---|---|---|
| **Item Name** | ✅ | | `Red Striped Shirt` |
| **Base Units** | ✅ | a Unit that **already exists** (import Units first) | `Pcs` |
| Under (Stock Group) | – | leave blank for top-level | `Apparel` |
| HSN Code | – | | `61052010` |
| GST Rate (%) | – | | `5` |
| Opening Qty / Rate / Value | – | numbers | `25` / `490` / `12250` |

> Stock items need **Inventory enabled** in the company (Tally **F11 → Inventory**).

---

## 6. VOUCHERS (transactions) — the important one

### The shape: **one row per ledger line**, grouped by **Voucher No.**

A voucher (e.g. one invoice) is **several rows** that share the same **Voucher No.** Each row is
one posting (one ledger, one amount, Dr or Cr). The rows of a voucher **must balance: total Dr =
total Cr.**

| Field | Required? | Notes | Example |
|---|---|---|---|
| **Voucher No.** | ✅ | groups the rows of one voucher (the invoice/doc number) | `INV-001` |
| **Date** | ✅ | `2026-04-20` or `20-04-2026`; **must be inside the company's period** | `2026-04-20` |
| **Voucher Type** | ✅ | **`Sales` / `Purchase` / `Receipt` / `Payment` / `Journal` / `Contra` / `Credit Note` / `Debit Note`** — **NOT** a group name | `Sales` |
| **Ledger** | ✅ | the account this line posts to — **map to a COLUMN** (it differs per line) | `Customer A` |
| Amount | –* | the line amount (magnitude) | `5000` |
| Dr/Cr | –* | `Dr` or `Cr` for this line | `Dr` |
| Debit / Credit | –* | *instead* of Amount+Dr/Cr, two separate columns | |
| Party Ledger | – | the customer/supplier (for invoices) | `Customer A` |
| Narration | – | free text | `Being goods sold` |
| Stock Item / Quantity / Rate / Unit | – | only for **Sales/Purchase with inventory** | `Widget` / `100` / `50` / `Nos` |

`–*` Each line needs an amount via **either** `Amount` + `Dr/Cr` **or** `Debit`/`Credit` columns.

### Which side is Dr / which is Cr? (per voucher type)

| Voucher Type | Debit (Dr) | Credit (Cr) |
|---|---|---|
| **Receipt** (money in) | Bank / Cash | the Customer (party) |
| **Payment** (money out) | the Supplier / Expense | Bank / Cash |
| **Sales** | the Customer (party) | Sales income **+** GST |
| **Purchase** | Purchase **+** GST | the Supplier (party) |
| **Journal** (adjustment) | one ledger | another ledger |
| **Contra** (bank↔cash) | the account receiving | the account giving |

### Example — a Sales invoice as rows

| Voucher No. | Date | Voucher Type | Ledger | Amount | Dr/Cr |
|---|---|---|---|---|---|
| INV-001 | 2026-04-20 | Sales | Customer A | 5900 | Dr |
| INV-001 | 2026-04-20 | Sales | Sales | 5000 | Cr |
| INV-001 | 2026-04-20 | Sales | Output CGST | 450 | Cr |
| INV-001 | 2026-04-20 | Sales | Output SGST | 450 | Cr |

Dr 5900 = Cr (5000 + 450 + 450) → **balanced.** Mapping: `Voucher Type` = fixed `Sales`;
`Voucher No / Date / Ledger / Amount / Dr-Cr` = **columns**.

### Example — a Receipt (customer pays)

| Voucher No. | Date | Voucher Type | Ledger | Amount | Dr/Cr |
|---|---|---|---|---|---|
| RCPT-1 | 2026-04-21 | Receipt | HDFC Bank | 5900 | Dr |
| RCPT-1 | 2026-04-21 | Receipt | Customer A | 5900 | Cr |

### Sales **with inventory** (stock items)

Add `Stock Item`, `Quantity`, `Rate`, `Unit` on the **income line** (the one posting to your Sales
ledger). The item's amount = qty × rate.

| Voucher No. | Date | Type | Ledger | Amount | Dr/Cr | Stock Item | Quantity | Rate | Unit |
|---|---|---|---|---|---|---|---|---|---|
| INV-002 | 2026-04-20 | Sales | Customer A | 5900 | Dr | | | | |
| INV-002 | 2026-04-20 | Sales | Output CGST | 450 | Cr | | | | |
| INV-002 | 2026-04-20 | Sales | Output SGST | 450 | Cr | | | | |
| INV-002 | 2026-04-20 | Sales | Sales | 5000 | Cr | Widget | 100 | 50 | Nos |

> Needs **Inventory enabled** in the company. **Unit is required on item lines** (Tally rejects an
> item with no unit). Quantity must be positive.

---

## 7. Common mistakes (and the fix)

| Mistake | Why it fails | Fix |
|---|---|---|
| **Voucher Type = `SUNDRYDEBTORS`** | that's a group, not a transaction type | use `Sales` / `Receipt` / `Payment` / `Journal` / … |
| **Ledger = a single fixed value** | every line posts to the same account → can't balance | map **Ledger to a column** |
| Unit symbol with a space (`QA Nos`) | Tally rejects it | use `Nos`, `Kg`, `Pcs` (no spaces) |
| Voucher debits ≠ credits | Tally rejects unbalanced vouchers | make each voucher's Dr total = Cr total |
| Date outside the company period | "date is Out of Range" | use a date within the company's books period |
| Voucher names a ledger that doesn't exist | Tally can't find it | import the **Ledgers first**, then the vouchers |
| A column you needed shows as "ignored" | the app drops **all-empty** columns | put data in it, or it's just an empty column you don't need |

---

## 8. What the confidence pills mean (Map step)

- **green / high %** — auto-mapped confidently, usually leave it.
- **amber "Review"** — a guess; check it's the right column.
- **"Fixed value"** (blue) — you set a constant for every row.
- **red REQ outline** — a required field still needs a column or a fixed value.

You can't continue past **Validate** until every red error is resolved — but **warnings** (amber)
don't block you.
