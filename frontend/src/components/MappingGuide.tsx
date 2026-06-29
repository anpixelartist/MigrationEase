import { type CSSProperties } from "react";
import type { EntityType } from "../api/types";
import { T, card } from "../theme";
import { Button } from "./ui";

type Field = { name: string; req?: boolean; note: string };

const FIELDS: Record<EntityType, Field[]> = {
  ledger: [
    { name: "Ledger Name", req: true, note: "unique, ≤100 chars — e.g. Customer ABC" },
    { name: "Under (Group)", req: true, note: "a Tally group — Sundry Debtors, Bank Accounts, Sales Accounts…" },
    { name: "Opening Balance", note: "a number (₹, commas, brackets all OK)" },
    { name: "Opening Dr/Cr", note: "Dr or Cr — nature of the opening balance" },
    { name: "GSTIN", note: "15-char GSTIN, e.g. 29ABCDE1234F1Z5" },
    { name: "+ optional", note: "PAN, State, Email, Phone, Pincode, Credit Limit, Address…" },
  ],
  group: [
    { name: "Group Name", req: true, note: "e.g. Trade Debtors" },
    { name: "Under (Parent Group)", req: true, note: "the parent group — e.g. Sundry Debtors" },
    { name: "+ optional", note: "Is Revenue?, Debit Nature?, Bill-wise?, Affects Gross Profit?" },
  ],
  unit: [
    { name: "Unit Symbol", req: true, note: "NO SPACES — Nos, Kg, Pcs, Ltr, Box (not 'QA Nos')" },
    { name: "Decimal Places", note: "0 for whole numbers, 2 for fractional" },
  ],
  stock_item: [
    { name: "Item Name", req: true, note: "e.g. Red Striped Shirt" },
    { name: "Base Units", req: true, note: "a Unit that already exists — import Units first" },
    { name: "Under (Stock Group)", note: "blank for top-level — e.g. Apparel" },
    { name: "+ optional", note: "HSN, GST Rate, Opening Qty/Rate/Value" },
  ],
  voucher: [
    { name: "Voucher No.", req: true, note: "groups the rows of one voucher (the doc/invoice no.)" },
    { name: "Date", req: true, note: "2026-04-20 or 20-04-2026 — must be in the company's period" },
    { name: "Voucher Type", req: true, note: "Sales / Purchase / Receipt / Payment / Journal — NOT a group name" },
    { name: "Ledger", req: true, note: "map to a COLUMN — each line posts to a different account" },
    { name: "Amount + Dr/Cr", note: "the line amount + Dr or Cr (or use Debit/Credit columns)" },
    { name: "+ for inventory", note: "Stock Item, Quantity, Rate, Unit (on Sales/Purchase item lines)" },
  ],
};

const DRCR: [string, string, string][] = [
  ["Receipt (money in)", "Bank / Cash", "the Customer"],
  ["Payment (money out)", "the Supplier / Expense", "Bank / Cash"],
  ["Sales", "the Customer", "Sales + GST"],
  ["Purchase", "Purchase + GST", "the Supplier"],
  ["Journal", "one ledger", "another ledger"],
];

const SALES_EXAMPLE: string[][] = [
  ["INV-001", "Sales", "Customer A", "5900", "Dr"],
  ["INV-001", "Sales", "Sales", "5000", "Cr"],
  ["INV-001", "Sales", "Output CGST", "450", "Cr"],
  ["INV-001", "Sales", "Output SGST", "450", "Cr"],
];

const h2: CSSProperties = { fontSize: 13.5, fontWeight: 700, margin: "20px 0 8px", color: T.text };
const small: CSSProperties = { fontSize: 12.5, color: T.muted, lineHeight: 1.55 };

export function MappingGuide({ entity, onClose }: { entity: EntityType; onClose: () => void }) {
  const label = { ledger: "Ledgers", group: "Groups", unit: "Units", stock_item: "Stock Items", voucher: "Vouchers" }[entity];
  const isV = entity === "voucher";
  return (
    <div onClick={onClose} style={{ position: "fixed", inset: 0, background: "rgba(20,20,18,.34)", zIndex: 50, display: "flex", justifyContent: "flex-end" }}>
      <div onClick={(e) => e.stopPropagation()} className="fadeIn" style={{ width: "min(480px,94vw)", height: "100%", background: T.bg || "#f7f7f5", overflowY: "auto", boxShadow: "-8px 0 30px rgba(0,0,0,.14)", padding: "22px 24px 60px" }}>
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 4 }}>
          <h1 style={{ fontSize: 18, fontWeight: 700, margin: 0 }}>Mapping help</h1>
          <Button onClick={onClose}>Close</Button>
        </div>
        <p style={small}>Importing <b>{label}</b>. Full guide: <code style={{ fontFamily: T.mono }}>docs/MAPPING-CHEATSHEET.md</code></p>

        <div style={h2}>The 3 golden rules</div>
        <ol style={{ ...small, paddingLeft: 18, margin: 0 }}>
          <li>Map to a <b>column</b> when the value changes per row; use <b>“Set a fixed value”</b> only when it's the same for every row.</li>
          <li>Import order: <b>Units → Groups → Ledgers → Stock Items → Vouchers</b>. Things must exist before they're referenced.</li>
          <li>The target <b>company must be open</b> in Tally and match the name on the Plan step.</li>
        </ol>

        <div style={h2}>{label} — fields</div>
        <div style={{ ...card, padding: 0, overflow: "hidden" }}>
          {FIELDS[entity].map((f, i) => (
            <div key={f.name} style={{ display: "flex", gap: 10, padding: "9px 13px", borderBottom: i < FIELDS[entity].length - 1 ? "1px solid rgba(0,0,0,.05)" : "none" }}>
              <div style={{ minWidth: 128, fontSize: 12.5, fontWeight: 600 }}>
                {f.name} {f.req && <span style={{ fontSize: 9, fontWeight: 700, color: T.err, background: T.errBg, padding: "1px 4px", borderRadius: 3 }}>REQ</span>}
              </div>
              <div style={{ fontSize: 12, color: T.muted, flex: 1 }}>{f.note}</div>
            </div>
          ))}
        </div>

        {isV && (
          <>
            <div style={h2}>How vouchers work</div>
            <p style={small}>A voucher is <b>several rows sharing one Voucher No.</b> Each row is one posting (one ledger, Dr or Cr). The rows <b>must balance: total Dr = total Cr.</b></p>

            <div style={h2}>Which side is Dr / Cr?</div>
            <div style={{ ...card, padding: 0, overflow: "hidden" }}>
              <div style={{ display: "flex", padding: "7px 13px", background: "#fafafa", fontSize: 11, fontWeight: 700, color: T.faint, borderBottom: `1px solid ${T.line}` }}>
                <div style={{ flex: 1.2 }}>Type</div><div style={{ flex: 1 }}>Debit</div><div style={{ flex: 1 }}>Credit</div>
              </div>
              {DRCR.map(([t, d, c], i) => (
                <div key={t} style={{ display: "flex", padding: "7px 13px", fontSize: 12, borderBottom: i < DRCR.length - 1 ? "1px solid rgba(0,0,0,.05)" : "none" }}>
                  <div style={{ flex: 1.2, fontWeight: 600 }}>{t}</div><div style={{ flex: 1, color: T.muted }}>{d}</div><div style={{ flex: 1, color: T.muted }}>{c}</div>
                </div>
              ))}
            </div>

            <div style={h2}>Example — a Sales invoice as rows</div>
            <div style={{ ...card, padding: 0, overflow: "hidden" }}>
              <div style={{ display: "flex", padding: "6px 12px", background: "#fafafa", fontSize: 10.5, fontWeight: 700, color: T.faint, fontFamily: T.mono, borderBottom: `1px solid ${T.line}` }}>
                <div style={{ width: 66 }}>Vch No</div><div style={{ width: 48 }}>Type</div><div style={{ flex: 1 }}>Ledger</div><div style={{ width: 52, textAlign: "right" }}>Amount</div><div style={{ width: 30, textAlign: "right" }}>D/C</div>
              </div>
              {SALES_EXAMPLE.map((r, i) => (
                <div key={i} style={{ display: "flex", padding: "6px 12px", fontSize: 11.5, fontFamily: T.mono, borderBottom: i < SALES_EXAMPLE.length - 1 ? "1px solid rgba(0,0,0,.05)" : "none" }}>
                  <div style={{ width: 66 }}>{r[0]}</div><div style={{ width: 48 }}>{r[1]}</div><div style={{ flex: 1 }}>{r[2]}</div><div style={{ width: 52, textAlign: "right" }}>{r[3]}</div>
                  <div style={{ width: 30, textAlign: "right", fontWeight: 700, color: r[4] === "Dr" ? T.text : T.blue }}>{r[4]}</div>
                </div>
              ))}
            </div>
            <p style={{ ...small, marginTop: 8 }}>Dr 5900 = Cr (5000+450+450) → <b style={{ color: T.ok }}>balanced</b>. Map Vch No / Date / Ledger / Amount / Dr-Cr to <b>columns</b>; only Voucher Type is a fixed value.</p>
          </>
        )}

        <div style={h2}>Common mistakes</div>
        <ul style={{ ...small, paddingLeft: 18, margin: 0 }}>
          {isV ? (
            <>
              <li><b>Voucher Type = a group</b> (e.g. SUNDRYDEBTORS) → use <b>Sales / Receipt / Payment / Journal</b>.</li>
              <li><b>Ledger = one fixed value</b> → map it to a <b>column</b>, or the voucher can't balance.</li>
              <li>Debits ≠ credits → Tally rejects unbalanced vouchers.</li>
              <li>Naming a ledger that doesn't exist yet → import the <b>Ledgers first</b>.</li>
            </>
          ) : (
            <>
              <li>Unit symbol with a space (<code>QA Nos</code>) → use <code>Nos</code> / <code>Kg</code> (no spaces).</li>
              <li>Stock item before its Unit exists → import <b>Units first</b>.</li>
              <li>A required field has no column → use <b>“Set a fixed value”</b> (e.g. all customers → Under = Sundry Debtors).</li>
            </>
          )}
        </ul>
      </div>
    </div>
  );
}
