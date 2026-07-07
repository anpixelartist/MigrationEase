import { useState, type CSSProperties, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { ApiError } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import { Button } from "../components/ui";
import { T, card } from "../theme";

const input: CSSProperties = {
  width: "100%",
  background: "#fff",
  border: "1px solid rgba(0,0,0,.14)",
  borderRadius: 9,
  padding: "10px 12px",
  fontSize: 14,
  outline: "none",
  fontFamily: T.sans,
  color: T.text,
};
const label: CSSProperties = { fontSize: 12.5, fontWeight: 600, color: T.muted, marginBottom: 6, display: "block" };

export default function Login() {
  const { login, signup, loginSso, authConfig } = useAuth();
  const nav = useNavigate();
  const [mode, setMode] = useState<"login" | "signup">("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [orgName, setOrgName] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const ssoEnabled = authConfig?.mode === "hybrid" || authConfig?.mode === "keycloak";
  const passwordEnabled = authConfig?.mode !== "keycloak"; // hidden when the IdP owns credentials

  const sso = async () => {
    setErr(null);
    setBusy(true);
    try {
      await loginSso(); // redirects away on success
    } catch (ex) {
      setErr(ex instanceof Error ? ex.message : "Something went wrong");
      setBusy(false);
    }
  };

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setErr(null);
    setBusy(true);
    try {
      if (mode === "login") await login(email, password);
      else await signup(email, password, undefined, orgName || undefined);
      nav("/");
    } catch (ex) {
      setErr(ex instanceof ApiError ? ex.problem.detail || ex.problem.title : "Something went wrong");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div style={{ minHeight: "100vh", display: "flex", alignItems: "center", justifyContent: "center", padding: 24 }}>
      <div className="fadeIn" style={{ width: 380, maxWidth: "100%" }}>
        <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 22, justifyContent: "center" }}>
          <div style={{ width: 30, height: 30, borderRadius: 8, background: T.accent, color: "#fff", display: "flex", alignItems: "center", justifyContent: "center", fontSize: 17, fontWeight: 700 }}>⟂</div>
          <div style={{ fontWeight: 600, fontSize: 17, letterSpacing: "-.01em" }}>Ledgerbridge</div>
        </div>

        <form onSubmit={submit} style={{ ...card, padding: 26 }}>
          <h1 style={{ margin: "0 0 4px", fontSize: 19, fontWeight: 600, letterSpacing: "-.02em" }}>
            {mode === "login" ? "Welcome back" : "Create your workspace"}
          </h1>
          <p style={{ margin: "0 0 20px", fontSize: 13.5, color: T.muted }}>
            {mode === "login" ? "Sign in to import data into Tally." : "Start importing CSV/Excel into Tally."}
          </p>

          {ssoEnabled && (
            <>
              <Button variant="primary" type="button" loading={busy} onClick={sso} style={{ width: "100%", padding: "11px 18px" }}>
                Continue with SSO
              </Button>
              {!passwordEnabled && err && (
                <div style={{ background: T.errBg, color: T.err, fontSize: 12.5, padding: "9px 12px", borderRadius: 8, marginTop: 14 }}>{err}</div>
              )}
              {!passwordEnabled && (
                <p style={{ margin: "14px 0 0", fontSize: 12.5, color: T.muted, textAlign: "center" }}>
                  Sign-in and registration are managed by your identity provider.
                </p>
              )}
              {passwordEnabled && (
                <div style={{ display: "flex", alignItems: "center", gap: 10, margin: "16px 0" }}>
                  <div style={{ flex: 1, height: 1, background: T.line }} />
                  <span style={{ fontSize: 11.5, color: T.faint }}>or with email</span>
                  <div style={{ flex: 1, height: 1, background: T.line }} />
                </div>
              )}
            </>
          )}

          {passwordEnabled && mode === "signup" && (
            <div style={{ marginBottom: 14 }}>
              <label style={label}>Workspace name</label>
              <input style={input} value={orgName} onChange={(e) => setOrgName(e.target.value)} placeholder="Acme Books" />
            </div>
          )}
          {passwordEnabled && (
            <>
              <div style={{ marginBottom: 14 }}>
                <label style={label}>Email</label>
                <input style={input} type="email" required value={email} onChange={(e) => setEmail(e.target.value)} placeholder="you@company.com" />
              </div>
              <div style={{ marginBottom: 18 }}>
                <label style={label}>Password</label>
                <input style={input} type="password" required value={password} onChange={(e) => setPassword(e.target.value)} placeholder="At least 8 characters" />
              </div>

              {err && (
                <div style={{ background: T.errBg, color: T.err, fontSize: 12.5, padding: "9px 12px", borderRadius: 8, marginBottom: 14 }}>{err}</div>
              )}

              <Button variant="primary" type="submit" loading={busy} style={{ width: "100%", padding: "11px 18px" }}>
                {mode === "login" ? "Sign in" : "Create workspace"}
              </Button>

              <div style={{ marginTop: 16, fontSize: 13, color: T.muted, textAlign: "center" }}>
                {mode === "login" ? "New here? " : "Already have an account? "}
                <span
                  onClick={() => { setMode(mode === "login" ? "signup" : "login"); setErr(null); }}
                  style={{ color: T.accent, fontWeight: 500, cursor: "pointer" }}
                >
                  {mode === "login" ? "Create a workspace" : "Sign in"}
                </span>
              </div>
            </>
          )}
        </form>

        {/* Vendor-neutral hint; only shown in password-only deployments. */}
        {authConfig && !ssoEnabled && (
          <p style={{ margin: "16px 4px 0", fontSize: 12, color: T.faint, textAlign: "center", lineHeight: 1.55 }}>
            🔒 Single sign-on (SSO) is available for organizations.<br />
            Contact your administrator to enable it.
          </p>
        )}
      </div>
    </div>
  );
}
