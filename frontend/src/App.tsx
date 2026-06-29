import { Link, Navigate, Route, Routes, useLocation } from "react-router-dom";
import { useAuth } from "./auth/AuthContext";
import { Spinner } from "./components/ui";
import BridgeSettings from "./pages/BridgeSettings";
import Importer from "./pages/Importer";
import Login from "./pages/Login";
import { T } from "./theme";

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
        </nav>
        <div style={{ display: "flex", alignItems: "center", gap: 14 }}>
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
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </div>
  );
}
