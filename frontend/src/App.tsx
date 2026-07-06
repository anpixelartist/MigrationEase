import { Link, Navigate, Route, Routes, useLocation } from "react-router-dom";
import { useAuth } from "./auth/AuthContext";
import { Spinner } from "./components/ui";
import { useBridgeStatus } from "./hooks/useBridgeStatus";
import AuthCallback from "./pages/AuthCallback";
import BridgeSettings from "./pages/BridgeSettings";
import Importer from "./pages/Importer";
import Login from "./pages/Login";
import Status from "./pages/Status";
import { T } from "./theme";

/** Always-visible pill so users know the automatic Tally push facility (local bridge → Tally on port 9000) is live. */
function TallyStatusPill() {
  const status = useBridgeStatus();
  const online = Boolean(status?.online);
  return (
    <Link
      to="/bridge"
      title={online ? "A Tally bridge is connected — you can push imports straight into Tally." : "No Tally bridge connected. Click to set one up."}
      style={{
        display: "inline-flex", alignItems: "center", gap: 7, textDecoration: "none",
        padding: "5px 11px", borderRadius: 999, fontSize: 12, fontWeight: 500,
        background: online ? T.okBg : "rgba(0,0,0,.04)",
        color: online ? T.ok : T.faint,
        border: `1px solid ${online ? "rgba(34,197,94,.28)" : T.line}`,
      }}
    >
      <span style={{ width: 7, height: 7, borderRadius: "50%", background: online ? "#22c55e" : "#cfcfc8", boxShadow: online ? "0 0 0 3px rgba(34,197,94,.18)" : "none" }} />
      {online ? "Tally connected" : "Tally not connected"}
    </Link>
  );
}

function TopBar() {
  const { user, logout } = useAuth();
  const loc = useLocation();
  const linkStyle = (active: boolean) => ({
    fontSize: 13.5,
    fontWeight: 500,
    color: active ? T.text : T.muted,
    textDecoration: "none",
    padding: "6px 4px",
    borderBottom: active ? `2px solid ${T.accent}` : "2px solid transparent",
  });
  return (
    <header style={{ position: "sticky", top: 0, zIndex: 20, background: "rgba(247,247,245,.82)", backdropFilter: "blur(12px)", borderBottom: `1px solid ${T.line}` }}>
      <div style={{ maxWidth: 1100, margin: "0 auto", padding: "0 28px", height: 58, display: "flex", alignItems: "center", gap: 28 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <div style={{ width: 26, height: 26, borderRadius: 7, background: T.accent, color: "#fff", display: "flex", alignItems: "center", justifyContent: "center", fontSize: 15, fontWeight: 700 }}>⟂</div>
          <div style={{ fontWeight: 600, fontSize: 15, letterSpacing: "-.01em" }}>Ledgerbridge</div>
        </div>
        <nav style={{ display: "flex", gap: 18, flex: 1 }}>
          <Link to="/" style={linkStyle(loc.pathname === "/")}>Import</Link>
          <Link to="/bridge" style={linkStyle(loc.pathname === "/bridge")}>Bridge</Link>
          <Link to="/status" style={linkStyle(loc.pathname === "/status")}>Status</Link>
        </nav>
        <div style={{ display: "flex", alignItems: "center", gap: 14 }}>
          <TallyStatusPill />
          <span style={{ fontSize: 12.5, color: T.faint, fontFamily: T.mono }}>{user?.email}</span>
          <span onClick={logout} style={{ fontSize: 12.5, color: T.muted, cursor: "pointer", fontWeight: 500 }}>Sign out</span>
        </div>
      </div>
    </header>
  );
}

export default function App() {
  const { user, ready } = useAuth();

  if (!ready) {
    return (
      <div style={{ height: "100vh", display: "flex", alignItems: "center", justifyContent: "center" }}>
        <Spinner size={26} />
      </div>
    );
  }

  if (!user) {
    return (
      <Routes>
        <Route path="/auth/callback" element={<AuthCallback />} />
        <Route path="*" element={<Login />} />
      </Routes>
    );
  }

  return (
    <div style={{ minHeight: "100vh" }}>
      <TopBar />
      <Routes>
        <Route path="/" element={<Importer />} />
        <Route path="/bridge" element={<BridgeSettings />} />
        <Route path="/status" element={<Status />} />
        <Route path="/auth/callback" element={<Navigate to="/" replace />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </div>
  );
}
