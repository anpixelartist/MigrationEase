import { type CSSProperties } from "react";
import { useAuth } from "../auth/AuthContext";
import { useBridgeStatus } from "../hooks/useBridgeStatus";
import { T, card } from "../theme";

type Level = "live" | "partial" | "planned";

const BADGE: Record<Level, { fg: string; bg: string; label: string }> = {
  live: { fg: T.ok, bg: T.okBg, label: "Live" },
  partial: { fg: T.warn, bg: T.warnBg, label: "Partial" },
  planned: { fg: T.faint, bg: "rgba(0,0,0,.05)", label: "Planned" },
};

type Item = { name: string; level: Level; note: string };
type Section = { title: string; items: Item[] };

// Curated from the codebase audit — an honest map of what the product does today vs the
// aspirational e-commerce PRD in docs/. Kept deliberately conservative: "Live" means a real,
// exercised code path (most verified against live Tally), not a stub.
const SECTIONS: Section[] = [
  {
    title: "Import pipeline",
    items: [
      { name: "Masters import (Ledgers, Groups, Units, Stock Items)", level: "live", note: "Parse → profile → map → validate → generate → push; verified against live Tally + ODBC read-back." },
      { name: "Voucher import (transactions)", level: "live", note: "Rows grouped into balanced vouchers (N-to-1 by order/voucher no.), Dr=Cr enforced at 3 layers." },
      { name: "Fuzzy auto-mapping + confidence", level: "live", note: "rapidfuzz + type-gating + one-to-one assignment; green/amber/unmapped pills." },
      { name: "Fixed values (constants)", level: "live", note: "Set one value for every row when a column is missing (e.g. Under = Sundry Debtors)." },
      { name: "Saved mapping templates", level: "live", note: "Save a manual mapping; it auto-applies to future files with the same columns. Strengthens auto-map over time." },
      { name: "Zero-sum Round-Off on vouchers", level: "live", note: "Injects a Round Off leg for sub-paisa imbalances so a voucher balances exactly." },
      { name: "Cutover → opening balances", level: "live", note: "Pre-cutover rows roll into ledger OPENINGBALANCE instead of vouchers (with counter-party accrual)." },
      { name: "HSN code emission", level: "live", note: "A mapped HSN column is now written as <HSNCODE> on the stock item." },
      { name: "UTF-8 BOM / encoding hardening", level: "live", note: "BOM stripped on read (no more corrupted first header); calamine XXE-safe Excel." },
    ],
  },
  {
    title: "Tally connectivity",
    items: [
      { name: "Direct push (same machine)", level: "live", note: "Backend POSTs XML to the Tally gateway on :9000. Verified live." },
      { name: "Bridge relay (cloud → local Tally)", level: "live", note: "Go agent dials out over WSS; cross-process delivery via Redis pub/sub with exactly-once claim." },
      { name: "Push idempotency", level: "live", note: "Atomic claim → 409 on repeat; final status pushed / pushed_partial / push_failed from Tally's response." },
      { name: "Bridge chunking + sandbox gate", level: "live", note: "Splits into ≤200-message batches and honors the backend-selected company; an opt-in --test-company flag refuses pushes to any other company." },
      { name: "Real-time push progress bar", level: "planned", note: "Bridge emits one terminal result today; per-chunk WebSocket progress is the next increment." },
      { name: "Bridge LASTVCHID pre-fetch idempotency", level: "planned", note: "Backend push-claim idempotency is live; querying Tally for existing vouchers needs export TDLs this Tally build rejects." },
    ],
  },
  {
    title: "Authentication & tenancy",
    items: [
      { name: "Email/password auth + multi-tenant orgs", level: "live", note: "First-party HS256 JWTs; org-scoped everything." },
      { name: "Keycloak / OIDC SSO", level: "live", note: "Authorization Code + PKCE, JIT provisioning, verified-email linking, service accounts (M2M)." },
      { name: "Production hardening", level: "live", note: "Fixed: service-account RLS break, public-client hijack, verified-email JIT gate, proxy-aware rate limiting." },
      { name: "Token storage (BFF/httpOnly)", level: "planned", note: "Tokens are in localStorage today; a cookie/BFF session is the recommended pre-prod hardening." },
    ],
  },
  {
    title: "GST / e-commerce engine",
    items: [
      { name: "Place-of-supply IGST vs CGST/SGST", level: "live", note: "Rate-driven engine: maps a GST Rate + Shipping/Home State (or party GSTIN) to Output CGST/SGST (intra) or IGST (inter), with exact paisa rounding. 12 unit tests." },
      { name: "B2B vs B2C daily summaries", level: "live", note: "B2B invoices (valid GSTIN) stay itemised; B2C sales collapse into one summary voucher per day (GSTR-1 B2C-Others). Opt-in on the Plan step." },
      { name: "Marketplace settlement journals", level: "live", note: "Settlement mode expands each row into a multi-leg journal: Bank(net) + Commission + Fees + TCS = Marketplace(gross). Opt-in on the Plan step." },
      { name: "Refunds → Credit Notes", level: "live", note: "A refund/return row auto-becomes a Credit Note — the sale's legs are reversed and the order id kept in the narration." },
      { name: "HSN/SAC code emission", level: "live", note: "A mapped HSN column is written as <HSNCODE> on stock items and available on voucher lines." },
      { name: "HSN/SAC enrichment lookup", level: "planned", note: "Emission works; a saved SKU→HSN auto-fill map is the next increment." },
    ],
  },
];

const dot = (fg: string): CSSProperties => ({ width: 7, height: 7, borderRadius: "50%", background: fg, flex: "none" });

function Chip({ level }: { level: Level }) {
  const b = BADGE[level];
  return (
    <span style={{ display: "inline-flex", alignItems: "center", gap: 6, padding: "3px 9px", borderRadius: 999, background: b.bg, color: b.fg, fontSize: 11.5, fontWeight: 600, flex: "none" }}>
      <span style={dot(b.fg)} />{b.label}
    </span>
  );
}

export default function Status() {
  const { authConfig } = useAuth();
  const bridge = useBridgeStatus();
  const counts = SECTIONS.flatMap((s) => s.items).reduce(
    (a, i) => ({ ...a, [i.level]: (a[i.level] || 0) + 1 }), {} as Record<Level, number>,
  );

  return (
    <div className="fadeIn" style={{ maxWidth: 940, margin: "0 auto", padding: "28px 28px 100px" }}>
      <h1 style={{ margin: "0 0 6px", fontSize: 22, fontWeight: 600, letterSpacing: "-.02em" }}>Implementation status</h1>
      <p style={{ margin: "0 0 18px", fontSize: 14, color: T.muted, lineHeight: 1.5 }}>
        An honest map of what Ledgerbridge does today versus the roadmap. <b style={{ color: T.ok }}>Live</b> means a real,
        exercised path (most verified against live Tally); <b style={{ color: T.warn }}>Partial</b> is usable but incomplete;
        <b style={{ color: T.faint }}> Planned</b> is not built yet.
      </p>

      {/* live runtime signals */}
      <div style={{ display: "flex", gap: 10, flexWrap: "wrap", marginBottom: 22 }}>
        <span style={{ ...card, padding: "8px 13px", fontSize: 12.5, display: "inline-flex", gap: 8, alignItems: "center" }}>
          <span style={dot(bridge?.online ? "#22c55e" : "#cfcfc8")} />
          Tally bridge: <b>{bridge?.online ? "connected" : "not connected"}</b>
        </span>
        <span style={{ ...card, padding: "8px 13px", fontSize: 12.5, display: "inline-flex", gap: 8, alignItems: "center" }}>
          <span style={dot(authConfig?.mode === "legacy" ? "#cfcfc8" : "#22c55e")} />
          Auth mode: <b>{authConfig?.mode ?? "…"}</b>{authConfig && authConfig.mode !== "legacy" ? " (Keycloak SSO)" : ""}
        </span>
        <span style={{ ...card, padding: "8px 13px", fontSize: 12.5, color: T.muted }}>
          {counts.live ?? 0} live · {counts.partial ?? 0} partial · {counts.planned ?? 0} planned
        </span>
      </div>

      {SECTIONS.map((sec) => (
        <div key={sec.title} style={{ marginBottom: 22 }}>
          <div style={{ fontSize: 13, fontWeight: 700, color: T.faint, textTransform: "uppercase", letterSpacing: ".04em", marginBottom: 10 }}>{sec.title}</div>
          <div style={{ display: "flex", flexDirection: "column", gap: 9 }}>
            {sec.items.map((it) => (
              <div key={it.name} style={{ ...card, padding: "12px 16px", display: "flex", gap: 13, alignItems: "flex-start" }}>
                <div style={{ paddingTop: 1 }}><Chip level={it.level} /></div>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ fontSize: 13.5, fontWeight: 600 }}>{it.name}</div>
                  <div style={{ fontSize: 12.5, color: T.muted, marginTop: 3, lineHeight: 1.5 }}>{it.note}</div>
                </div>
              </div>
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}
