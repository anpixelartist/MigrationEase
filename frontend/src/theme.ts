import type { CSSProperties } from "react";

// Neutral, organization-agnostic design tokens.
export const T = {
  accent: "#4f46e5",   // primary — buttons, links, active nav, focus rings
  accentDark: "#3730a3",
  bg: "#f3f3f1",
  surface: "#ffffff",
  text: "#1c1c1a",
  muted: "#6b6b66",
  faint: "#9a9a93",
  line: "rgba(0,0,0,.08)",
  sans: "'Geist', system-ui, sans-serif",
  mono: "'Geist Mono', ui-monospace, monospace",
  ok: "#15803d",
  okBg: "rgba(34,197,94,.12)",
  warn: "#b45309",
  warnBg: "rgba(245,158,11,.14)",
  err: "#b91c1c",
  errBg: "rgba(239,68,68,.1)",
  blue: "#2563eb",
};

export const confColor = (level: string) =>
  level === "high" || level === "auto_accept"
    ? { dot: "#22c55e", fg: T.ok, bg: T.okBg, label: "High" }
    : level === "medium" || level === "needs_confirm"
      ? { dot: "#f59e0b", fg: T.warn, bg: T.warnBg, label: "Review" }
      : { dot: "#ef4444", fg: T.err, bg: T.errBg, label: "Low" };

export const card: CSSProperties = {
  background: T.surface,
  border: `1px solid ${T.line}`,
  borderRadius: 13,
  boxShadow: "0 1px 2px rgba(0,0,0,.04)",
};
