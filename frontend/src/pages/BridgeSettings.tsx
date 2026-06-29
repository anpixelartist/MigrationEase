import { useEffect, useState, type CSSProperties } from "react";
import { ApiError, api } from "../api/client";
import type { BridgeResponse, BridgeStatus } from "../api/types";
import { Button, useToast } from "../components/ui";
import { T, card } from "../theme";

const input: CSSProperties = { width: "100%", maxWidth: 320, background: "#fff", border: "1px solid rgba(0,0,0,.14)", borderRadius: 8, padding: "9px 12px", fontSize: 13.5, outline: "none", fontFamily: T.sans, color: T.text };

export default function BridgeSettings() {
  const toast = useToast();
  const [status, setStatus] = useState<BridgeStatus | null>(null);
  const [created, setCreated] = useState<BridgeResponse | null>(null);
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    const load = () => api.bridgeStatus().then(setStatus).catch(() => {});
    load();
    const iv = setInterval(load, 5000);
    return () => clearInterval(iv);
  }, []);

  const pair = async () => {
    setBusy(true); setErr(null);
    try {
      setCreated(await api.createBridge(name || undefined));
      toast.show("Bridge paired — copy the key below");
    } catch (e) {
      setErr(e instanceof ApiError ? e.problem.detail || e.problem.title : "Error");
    } finally { setBusy(false); }
  };

  const online = status?.online;
  const runCmd = created
    ? `bridge run --relay ws://127.0.0.1:8000/bridge/ws --key ${created.api_key} --tally http://127.0.0.1:9000 --company "Your Company"`
    : "";

  return (
    <div className="fadeIn" style={{ maxWidth: 760, margin: "0 auto", padding: "28px 28px 80px" }}>
      <h1 style={{ margin: "0 0 6px", fontSize: 22, fontWeight: 600, letterSpacing: "-.02em" }}>Connect your Tally</h1>
      <p style={{ margin: "0 0 22px", fontSize: 14, color: T.muted, lineHeight: 1.5 }}>
        Install the bridge agent on the machine running Tally. It dials out to the cloud (no inbound ports) and relays imports to your local Tally gateway on port 9000.
      </p>

      <div style={{ ...card, padding: 18, display: "flex", alignItems: "center", gap: 12, marginBottom: 20 }}>
        <span style={{ width: 10, height: 10, borderRadius: "50%", background: online ? "#22c55e" : "#cfcfc8", boxShadow: online ? "0 0 0 4px rgba(34,197,94,.18)" : "none" }} />
        <div style={{ fontSize: 14, fontWeight: 500 }}>{online ? "Bridge connected" : "No bridge connected"}</div>
        {status?.company_guid && <div style={{ fontSize: 12.5, color: T.faint, fontFamily: T.mono }}>company {status.company_guid}</div>}
      </div>

      <div style={{ ...card, padding: 20 }}>
        <div style={{ fontSize: 14, fontWeight: 600, marginBottom: 12 }}>Pair a new machine</div>
        <div style={{ display: "flex", gap: 10, marginBottom: 14 }}>
          <input style={input} value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. Reception PC" />
          <Button variant="primary" loading={busy} onClick={pair}>Generate key</Button>
        </div>
        {err && <div style={{ background: T.errBg, color: T.err, fontSize: 12.5, padding: "9px 12px", borderRadius: 8 }}>{err}</div>}

        {created && (
          <div style={{ marginTop: 8 }}>
            <div style={{ fontSize: 12, fontWeight: 600, color: T.muted, marginBottom: 6 }}>
              Run this on the Tally machine (the key is shown only once):
            </div>
            <div style={{ position: "relative" }}>
              <pre style={{ margin: 0, background: "#1c1c1a", color: "#e7e7e2", fontFamily: T.mono, fontSize: 12, padding: "13px 15px", borderRadius: 9, overflowX: "auto", whiteSpace: "pre-wrap", wordBreak: "break-all" }}>{runCmd}</pre>
              <Button style={{ position: "absolute", top: 8, right: 8, padding: "5px 10px", fontSize: 11.5 }}
                onClick={() => { navigator.clipboard.writeText(runCmd); toast.show("Copied"); }}>Copy</Button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
