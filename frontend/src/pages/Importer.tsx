import { useEffect, useMemo, useRef, useState, type CSSProperties } from "react";
import { ApiError, api, downloadArtifact, pollTask } from "../api/client";
import type {
  BridgeStatus,
  EntityType,
  GenerateSummary,
  MappingProposal,
  ProfileSignals,
  PushResult,
  ValidationResult,
  VoucherPreview,
} from "../api/types";
import { Button, Spinner, useToast } from "../components/ui";
import { MappingGuide } from "../components/MappingGuide";
import { T, card, confColor } from "../theme";

type Step = "upload" | "preview" | "map" | "validate" | "plan" | "import" | "done";
const STEPS: { key: Step; label: string }[] = [
  { key: "upload", label: "Upload" },
  { key: "preview", label: "Preview" },
  { key: "map", label: "Map" },
  { key: "validate", label: "Validate" },
  { key: "plan", label: "Plan" },
  { key: "import", label: "Import" },
];
const ENTITIES: { key: EntityType; label: string }[] = [
  { key: "ledger", label: "Ledgers" },
  { key: "group", label: "Groups" },
  { key: "stock_item", label: "Stock Items" },
  { key: "unit", label: "Units" },
  { key: "voucher", label: "Vouchers" },
];

const input: CSSProperties = {
  width: "100%",
  background: "#fff",
  border: "1px solid rgba(0,0,0,.14)",
  borderRadius: 8,
  padding: "9px 12px",
  fontSize: 13.5,
  outline: "none",
  fontFamily: T.sans,
  color: T.text,
};

export default function Importer() {
  const toast = useToast();
  const fileInput = useRef<HTMLInputElement>(null);

  const [step, setStep] = useState<Step>("upload");
  const [entity, setEntity] = useState<EntityType>("ledger");
  const [file, setFile] = useState<File | null>(null);
  const [jobId, setJobId] = useState<string | null>(null);
  const [profile, setProfile] = useState<ProfileSignals | null>(null);
  const [proposal, setProposal] = useState<MappingProposal | null>(null);
  const [mapping, setMapping] = useState<Record<string, string | null>>({});
  const [constants, setConstants] = useState<Record<string, string>>({});
  const [template, setTemplate] = useState<string>("");
  const [cutoverDate, setCutoverDate] = useState<string>(() => localStorage.getItem("tm_cutover") || "");
  const [validation, setValidation] = useState<ValidationResult | null>(null);
  const [vpreview, setVpreview] = useState<VoucherPreview | null>(null);
  const [company, setCompany] = useState(() => localStorage.getItem("tm_company") || "");
  const [gen, setGen] = useState<GenerateSummary | null>(null);
  const [push, setPush] = useState<PushResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [showGuide, setShowGuide] = useState(false);
  const [bridgeStatus, setBridgeStatus] = useState<BridgeStatus | null>(null);

  useEffect(() => {
    localStorage.setItem("tm_company", company);
  }, [company]);

  useEffect(() => {
    localStorage.setItem("tm_cutover", cutoverDate);
  }, [cutoverDate]);

  useEffect(() => {
    const load = () => api.bridgeStatus().then(setBridgeStatus).catch(() => {});
    load();
    const iv = setInterval(load, 5000);
    return () => clearInterval(iv);
  }, []);

  const curIdx = STEPS.findIndex((s) => s.key === step);
  const fail = (e: unknown) => {
    if (e instanceof ApiError) {
      const p = e.problem;
      if (p.status === 404)
        setErr("The backend didn't recognize that request (404). If you just updated the app, restart the backend server.");
      else setErr(p.detail || p.title || `Request failed (${p.status}).`);
    } else setErr("Unexpected error — please retry.");
  };

  const reset = () => {
    setStep("upload"); setEntity("ledger"); setFile(null); setJobId(null); setProfile(null);
    setProposal(null); setMapping({}); setConstants({}); setValidation(null); setVpreview(null); setCompany(""); setGen(null); setPush(null); setErr(null);
  };

  // ---- actions ----
  const doUpload = async () => {
    if (!file) return;
    setBusy(true); setErr(null);
    try {
      const job = await api.createJob(entity);
      setJobId(job.id);
      await api.uploadFile(job.id, file);
      setProfile(await api.getProfile(job.id));
      setStep("preview");
    } catch (e) { fail(e); } finally { setBusy(false); }
  };

  const goMap = async () => {
    if (!jobId) return;
    setBusy(true); setErr(null);
    try {
      const p = await api.getSuggestions(jobId);
      setProposal(p);
      const m: Record<string, string | null> = {};
      p.suggestions.forEach((s) => { m[s.target_field] = s.source_column; });
      setMapping(m);
      setStep("map");
    } catch (e) { fail(e); } finally { setBusy(false); }
  };

  const runValidate = async () => {
    if (!jobId) return;
    setBusy(true); setErr(null);
    try {
      const cleanConsts = Object.fromEntries(
        Object.entries(constants).filter(([, v]) => v.trim() !== ""),
      );
      await api.postMapping(jobId, mapping, cleanConsts, template || undefined);
      const t = await api.enqueueValidate(jobId);
      const r = await pollTask<ValidationResult>(jobId, t.task_id);
      if (r.state === "error") { fail(new ApiError(r.problem!)); return; }
      setValidation(r.result!);
      if (entity === "voucher") {
        // the multi-line preview is optional — never let it block validation (e.g. older backend)
        try { setVpreview(await api.getVoucherPreview(jobId)); } catch { setVpreview(null); }
      }
      setStep("validate");
    } catch (e) { fail(e); } finally { setBusy(false); }
  };

  const runGenerate = async () => {
    if (!jobId) return;
    setBusy(true); setErr(null);
    try {
      const t = await api.enqueueGenerate(jobId, company || undefined, cutoverDate || undefined);
      const r = await pollTask<GenerateSummary>(jobId, t.task_id);
      if (r.state === "error") { fail(new ApiError(r.problem!)); return; }
      setGen(r.result!);
      toast.show(`Generated ${r.result!.generated} master(s)`);
    } catch (e) { fail(e); } finally { setBusy(false); }
  };

  const runPush = async () => {
    if (!jobId) return;
    setBusy(true); setErr(null); setStep("import");
    try {
      const t = await api.enqueuePush(jobId);
      const r = await pollTask<PushResult>(jobId, t.task_id);
      if (r.state === "error") {
        setErr(r.problem!.detail || r.problem!.title);
        toast.show("Push unavailable — download the file instead");
        setStep("plan");
        return;
      }
      setPush(r.result!);
      setStep("done");
    } catch (e) { fail(e); setStep("plan"); } finally { setBusy(false); }
  };

  const download = async () => {
    if (!jobId) return;
    try { await downloadArtifact(jobId, `${entity}_import.xml`); toast.show("Downloaded XML"); } catch (e) { fail(e); }
  };

  const mapAttention = useMemo(
    () =>
      proposal
        ? proposal.suggestions.filter(
            (s) => s.required && !mapping[s.target_field] && !(constants[s.target_field] ?? "").trim(),
          ).length
        : 0,
    [proposal, mapping, constants],
  );

  return (
    <div className="fadeIn" style={{ maxWidth: 1100, margin: "0 auto", padding: "26px 28px 120px" }}>
      <Stepper curIdx={curIdx} step={step} />

      {err && (
        <div style={{ background: T.errBg, color: T.err, fontSize: 13, padding: "11px 14px", borderRadius: 9, margin: "0 0 18px" }}>
          {err}
        </div>
      )}

      {/* UPLOAD */}
      {step === "upload" && (
        <div className="fadeIn">
          <h1 style={h1}>Import to Tally</h1>
          <p style={sub}>Pick what you're importing, then drop a CSV or Excel file. We profile it, map the columns, and flag anything off before it touches your books.</p>

          <div style={{ display: "flex", gap: 8, marginBottom: 18, flexWrap: "wrap" }}>
            {ENTITIES.map((e) => (
              <div key={e.key} onClick={() => setEntity(e.key)} style={{
                padding: "8px 16px", borderRadius: 9, fontSize: 13, fontWeight: 500, cursor: "pointer",
                background: entity === e.key ? "#1c1c1a" : "#fff",
                color: entity === e.key ? "#fff" : T.muted,
                border: entity === e.key ? "1px solid #1c1c1a" : "1px solid rgba(0,0,0,.1)",
              }}>{e.label}</div>
            ))}
          </div>

          <div style={{ display: "flex", gap: 14, marginBottom: 22 }}>
            <div style={{ flex: 1 }}>
              <label style={{ fontSize: 12.5, fontWeight: 600, color: T.muted, display: "block", marginBottom: 6 }}>Pre-built Template</label>
              <select value={template} onChange={(e) => setTemplate(e.target.value)} style={input}>
                <option value="">None (Manual Mapping)</option>
                <option value="shopify">Shopify</option>
              </select>
            </div>
            <div style={{ flex: 1 }}>
              <label style={{ fontSize: 12.5, fontWeight: 600, color: T.muted, display: "block", marginBottom: 6 }}>Cutover Date (Optional)</label>
              <input type="date" value={cutoverDate} onChange={(e) => setCutoverDate(e.target.value)} style={input} />
            </div>
          </div>

          <div onClick={() => fileInput.current?.click()} style={{ ...card, border: `1.5px dashed rgba(79,70,229,.32)`, padding: "44px 32px", textAlign: "center", cursor: "pointer" }}>
            <input ref={fileInput} type="file" accept=".csv,.tsv,.xlsx,.xls" style={{ display: "none" }}
              onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
            <div style={{ width: 52, height: 52, borderRadius: 14, background: "rgba(79,70,229,.09)", display: "inline-flex", alignItems: "center", justifyContent: "center", marginBottom: 14, fontSize: 22, color: T.accent }}>↑</div>
            <div style={{ fontSize: 15.5, fontWeight: 500, marginBottom: 5 }}>{file ? file.name : "Click to choose a file"}</div>
            <div style={{ fontSize: 13, color: T.faint }}>.csv · .xlsx · .xls — up to 25 MB</div>
          </div>

          <div style={{ marginTop: 22, display: "flex", justifyContent: "flex-end" }}>
            <Button variant="primary" disabled={!file} loading={busy} onClick={doUpload}>Profile & continue →</Button>
          </div>
        </div>
      )}

      {/* PREVIEW */}
      {step === "preview" && profile && (
        <div className="fadeIn">
          <h1 style={h1}>Preview your data</h1>
          <p style={sub}>{profile.row_count.toLocaleString()} rows · {profile.column_count} columns detected.</p>
          <div style={{ ...card, overflow: "hidden" }}>
            <div style={{ overflowX: "auto" }}>
              <table style={{ width: "100%", borderCollapse: "collapse", minWidth: 720 }}>
                <thead>
                  <tr style={{ background: "#fafafa", borderBottom: `1px solid ${T.line}` }}>
                    {profile.columns.map((c) => (
                      <th key={c.name} style={{ padding: "11px 14px", textAlign: "left", verticalAlign: "bottom" }}>
                        <div style={{ fontSize: 13, fontWeight: 600, fontFamily: T.mono }}>{c.name}</div>
                        <div style={{ fontSize: 10.5, fontFamily: T.mono, marginTop: 3, color: c.inferred_type === "numeric" ? T.accent : T.faint }}>
                          {c.inferred_type}{c.null_pct > 0 ? ` · ${Math.round(c.null_pct * 100)}% empty` : ""}
                        </div>
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {[0, 1, 2].map((ri) => (
                    <tr key={ri} style={{ borderBottom: "1px solid rgba(0,0,0,.04)" }}>
                      {profile.columns.map((c) => (
                        <td key={c.name} style={{ padding: "9px 14px", fontSize: 12.5, color: "#3a3a36", fontFamily: c.inferred_type === "numeric" ? T.mono : T.sans, whiteSpace: "nowrap" }}>
                          {c.samples[ri] ?? ""}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
          <Footer onBack={() => setStep("upload")} primaryLabel="Continue to mapping" onPrimary={goMap} busy={busy} />
        </div>
      )}

      {showGuide && <MappingGuide entity={entity} onClose={() => setShowGuide(false)} />}

      {/* MAP */}
      {step === "map" && proposal && profile && (
        <div className="fadeIn">
          <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", gap: 12 }}>
            <h1 style={h1}>Map columns to Tally fields</h1>
            <Button onClick={() => setShowGuide(true)}>❓ Mapping help</Button>
          </div>
          <p style={sub}>
            {proposal.suggestions.filter((s) => s.status === "auto_accept").length} auto-mapped · {mapAttention} required field(s) need your attention.
            <br />No column for a required field (e.g. a group/parent)? Choose <b>“Set a fixed value”</b> to apply one value to every row.
          </p>
          <div style={{ display: "flex", flexDirection: "column", gap: 9 }}>
            {proposal.suggestions.map((s) => {
              const field = s.target_field;
              const isConst = field in constants;
              const colVal = mapping[field] ?? "";
              const constVal = constants[field] ?? "";
              const satisfied = isConst ? constVal.trim() !== "" : colVal !== "";
              const flag = s.required && !satisfied;
              const onSelect = (v: string) => {
                if (v === "__const__") {
                  setConstants((c) => ({ ...c, [field]: c[field] ?? "" }));
                  setMapping((m) => ({ ...m, [field]: null }));
                } else {
                  setConstants((c) => { const n = { ...c }; delete n[field]; return n; });
                  setMapping((m) => ({ ...m, [field]: v || null }));
                }
              };
              return (
                <div key={field} style={{ ...card, border: flag ? `1px solid rgba(239,68,68,.34)` : `1px solid ${T.line}`, padding: "13px 16px", display: "grid", gridTemplateColumns: "230px 18px 1fr 130px", gap: 14, alignItems: "center" }}>
                  <div style={{ display: "flex", alignItems: "center", gap: 8, minWidth: 0 }}>
                    <span style={{ fontSize: 13.5, fontWeight: 500 }}>{s.label}</span>
                    {s.required && <span style={{ fontSize: 9.5, fontWeight: 600, color: T.err, background: T.errBg, padding: "2px 5px", borderRadius: 4 }}>REQ</span>}
                  </div>
                  <div style={{ textAlign: "center", color: "#cfcfc8" }}>→</div>
                  <div style={{ minWidth: 0, display: "flex", flexDirection: "column", gap: 7 }}>
                    <select value={isConst ? "__const__" : colVal} onChange={(e) => onSelect(e.target.value)} style={{ ...input, fontFamily: T.mono }}>
                      <option value="">— Don't import —</option>
                      <option value="__const__">✎ Set a fixed value…</option>
                      {profile.columns.map((col) => (<option key={col.name} value={col.name}>{col.name}</option>))}
                    </select>
                    {isConst && (
                      <input autoFocus value={constVal} onChange={(e) => setConstants((c) => ({ ...c, [field]: e.target.value }))}
                        placeholder={`Same value for every row${field === "parent" ? " — e.g. Sundry Debtors" : ""}`} style={input} />
                    )}
                  </div>
                  <div style={{ display: "flex", justifyContent: "flex-end" }}>
                    {isConst ? (
                      <span style={{ display: "inline-flex", alignItems: "center", gap: 6, padding: "4px 10px", borderRadius: 999, background: "rgba(37,99,235,.10)", color: T.blue, fontSize: 12, fontWeight: 500 }}>
                        <span style={{ width: 7, height: 7, borderRadius: "50%", background: T.blue }} />Fixed value
                      </span>
                    ) : (() => {
                      const level = colVal === "" ? "unmapped" : colVal === s.source_column ? s.status : "needs_confirm";
                      const c = confColor(level);
                      return (
                        <span style={{ display: "inline-flex", alignItems: "center", gap: 6, padding: "4px 10px", borderRadius: 999, background: c.bg, color: c.fg, fontSize: 12, fontWeight: 500 }}>
                          <span style={{ width: 7, height: 7, borderRadius: "50%", background: c.dot }} />{c.label}
                          {colVal !== "" && <span style={{ opacity: 0.6 }}>{Math.round(s.confidence * 100)}%</span>}
                        </span>
                      );
                    })()}
                  </div>
                </div>
              );
            })}
          </div>
          <Footer onBack={() => setStep("preview")} primaryLabel="Validate →" onPrimary={runValidate} busy={busy}
            disabled={mapAttention > 0} hint={mapAttention > 0 ? `${mapAttention} required field(s) unmapped` : undefined} />
        </div>
      )}

      {/* VALIDATE */}
      {step === "validate" && validation && (
        <ValidateView validation={validation} entity={entity} voucherPreview={vpreview}
          onBack={() => setStep("map")} onContinue={() => setStep("plan")} />
      )}

      {/* PLAN */}
      {step === "plan" && (
        <div className="fadeIn">
          <h1 style={h1}>Import plan</h1>
          <p style={sub}>Nothing is written to Tally until you push. You can download a reviewable XML file first.</p>
          <div style={{ ...card, padding: 20, marginBottom: 16 }}>
            <label style={{ fontSize: 12.5, fontWeight: 600, color: T.muted, display: "block", marginBottom: 6 }}>Target Tally company</label>
            <div style={{ display: "flex", gap: 10 }}>
              <input style={{ ...input, maxWidth: 320 }} value={company} onChange={(e) => setCompany(e.target.value)} placeholder="e.g. Acme Traders Pvt Ltd" />
              <Button variant={gen ? "secondary" : "primary"} loading={busy} disabled={!company} onClick={runGenerate}>
                {gen ? "Regenerate" : "Generate Tally XML"}
              </Button>
            </div>
          </div>

          {gen && (
            <>
              <div style={{ display: "grid", gridTemplateColumns: "repeat(3,1fr)", gap: 12, marginBottom: 18 }}>
                <Stat n={gen.generated} label={entity === "voucher" ? "Vouchers to import" : "Masters to import"} color={T.ok} />
                <Stat n={gen.held_conflicts} label="Held (conflicts)" color={T.warn} />
                <Stat n={gen.skipped_rows} label="Skipped rows" color={T.faint} />
              </div>
              <div style={{ ...card, padding: 20, display: "flex", alignItems: "center", justifyContent: "space-between", gap: 16, flexWrap: "wrap" }}>
                <div style={{ fontSize: 13.5, color: T.muted, maxWidth: 460 }}>
                  Download a Tally-ready XML to import manually, or push it straight into <b>{company}</b> via your connected bridge.
                  {bridgeStatus && !bridgeStatus.online && (
                     <div style={{ color: T.err, marginTop: 4, fontWeight: 600 }}>⚠️ Bridge is currently offline. Connect it via the Bridge tab.</div>
                  )}
                </div>
                <div style={{ display: "flex", gap: 11 }}>
                  <Button onClick={download}>Download XML</Button>
                  <Button variant="primary" loading={busy} disabled={!bridgeStatus?.online} onClick={runPush}>Push to Tally</Button>
                </div>
              </div>
            </>
          )}
          <Footer onBack={() => setStep("validate")} />
        </div>
      )}

      {/* IMPORT */}
      {step === "import" && (
        <div className="fadeIn" style={{ maxWidth: 460, margin: "60px auto 0", textAlign: "center" }}>
          <div style={{ marginBottom: 18 }}><Spinner size={34} /></div>
          <h1 style={{ ...h1, fontSize: 20 }}>Pushing to Tally…</h1>
          <p style={sub}>Relaying the import to your Tally company.</p>
        </div>
      )}

      {/* DONE */}
      {step === "done" && push && (() => {
        const ok = push.is_success;
        const rejected = push.errors + push.exceptions;
        const problems = push.line_errors.length || rejected;
        return (
          <div className="fadeIn" style={{ maxWidth: 560, margin: "40px auto 0", textAlign: "center" }}>
            <div style={{ width: 58, height: 58, borderRadius: "50%", background: ok ? T.okBg : T.errBg, color: ok ? T.ok : T.err, display: "inline-flex", alignItems: "center", justifyContent: "center", fontSize: 30, marginBottom: 16 }}>{ok ? "✓" : "!"}</div>
            <h1 style={h1}>{ok ? "Import complete" : "Imported with problems"}</h1>
            <p style={sub}>
              {ok
                ? `Records were written to ${company || "your Tally company"}.`
                : `Tally rejected ${problems} record(s) — nothing was forced. Fix the data below and re-import.`}
            </p>
            <div style={{ display: "grid", gridTemplateColumns: "repeat(3,1fr)", gap: 12, margin: "20px 0 24px", textAlign: "left" }}>
              <Stat n={push.created} label="Created" color={T.text} />
              <Stat n={push.altered} label="Updated" color={T.blue} />
              <Stat n={push.ignored + rejected} label="Ignored / rejected" color={rejected ? T.err : T.faint} />
            </div>
            {(push.line_errors.length > 0 || push.exceptions > 0) && (
              <div style={{ ...card, padding: 14, textAlign: "left", marginBottom: 18 }}>
                <div style={{ fontSize: 12, fontWeight: 600, color: T.err, marginBottom: 6 }}>
                  Tally reported {push.line_errors.length || push.exceptions} problem(s)
                  {push.exceptions > 0 ? ` · ${push.exceptions} exception(s)` : ""}
                </div>
                {push.line_errors.slice(0, 8).map((e, i) => (<div key={i} style={{ fontSize: 12, color: T.muted, fontFamily: T.mono }}>{e}</div>))}
                {push.line_errors.length === 0 && push.exceptions > 0 && (
                  <div style={{ fontSize: 12, color: T.muted }}>Some masters were rejected by Tally (e.g. an invalid name or unit symbol). Check those rows and re-import.</div>
                )}
              </div>
            )}
            <div style={{ display: "flex", gap: 10, justifyContent: "center" }}>
              {!ok && <Button onClick={download}>Download XML</Button>}
              <Button variant="primary" onClick={reset}>Import another file</Button>
            </div>
          </div>
        );
      })()}
    </div>
  );
}

// ---- small pieces ----
const h1: CSSProperties = { margin: "0 0 6px", fontSize: 22, fontWeight: 600, letterSpacing: "-.02em" };
const sub: CSSProperties = { margin: "0 0 18px", fontSize: 14, color: T.muted, lineHeight: 1.5 };

function Stat({ n, label, color }: { n: number; label: string; color: string }) {
  return (
    <div style={{ ...card, padding: "15px 17px" }}>
      <div style={{ fontSize: 24, fontWeight: 600, fontFamily: T.mono, letterSpacing: "-.02em", color }}>{n.toLocaleString()}</div>
      <div style={{ fontSize: 12.5, color: T.faint, marginTop: 2 }}>{label}</div>
    </div>
  );
}

function Stepper({ curIdx, step }: { curIdx: number; step: Step }) {
  const done = (i: number) => i < curIdx || (step === "done" && i <= curIdx);
  return (
    <nav style={{ display: "flex", alignItems: "center", justifyContent: "center", gap: 0, marginBottom: 26 }}>
      {STEPS.map((s, i) => {
        const isDone = done(i);
        const isCur = i === curIdx && !isDone;
        let circle: CSSProperties = { width: 25, height: 25, borderRadius: "50%", display: "flex", alignItems: "center", justifyContent: "center", fontSize: 11.5, fontWeight: 600, flex: "none" };
        if (isDone) circle = { ...circle, background: T.accent, color: "#fff" };
        else if (isCur) circle = { ...circle, background: "#fff", color: T.accent, border: `1.5px solid ${T.accent}`, boxShadow: `0 0 0 4px ${T.accent}1f` };
        else circle = { ...circle, background: "#fff", color: "#b4b4ad", border: "1px solid rgba(0,0,0,.12)" };
        return (
          <div key={s.key} style={{ display: "flex", alignItems: "center" }}>
            <div style={{ display: "flex", alignItems: "center", gap: 8, padding: "4px 6px" }}>
              <div style={circle}>{isDone ? "✓" : i + 1}</div>
              <span style={{ fontSize: 12.5, fontWeight: isCur ? 600 : 500, color: isCur ? T.text : isDone ? "#4a4a44" : "#b4b4ad" }}>{s.label}</span>
            </div>
            {i < STEPS.length - 1 && <div style={{ width: 30, height: 1.5, margin: "0 4px", background: i < curIdx ? T.accent : "rgba(0,0,0,.12)" }} />}
          </div>
        );
      })}
    </nav>
  );
}

function Footer({ onBack, primaryLabel, onPrimary, busy, disabled, hint }: {
  onBack?: () => void; primaryLabel?: string; onPrimary?: () => void; busy?: boolean; disabled?: boolean; hint?: string;
}) {
  return (
    <div style={{ position: "fixed", bottom: 0, left: 0, right: 0, background: "rgba(247,247,245,.9)", backdropFilter: "blur(12px)", borderTop: `1px solid ${T.line}` }}>
      <div style={{ maxWidth: 1100, margin: "0 auto", padding: "13px 28px", display: "flex", alignItems: "center", justifyContent: "space-between", gap: 16 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
          {onBack && <Button onClick={onBack}>Back</Button>}
          {hint && <span style={{ fontSize: 13, color: T.muted }}>{hint}</span>}
        </div>
        {primaryLabel && onPrimary && (
          <Button variant="primary" loading={busy} disabled={disabled} onClick={onPrimary}>{primaryLabel}</Button>
        )}
      </div>
    </div>
  );
}

function ValidateView({ validation, entity, voucherPreview, onBack, onContinue }: {
  validation: ValidationResult; entity: EntityType; voucherPreview: VoucherPreview | null;
  onBack: () => void; onContinue: () => void;
}) {
  const isVoucher = entity === "voucher";
  const vCount = validation.stats.vouchers ?? voucherPreview?.count ?? 0;
  const groups = useMemo(() => {
    const map = new Map<string, { code: string; severity: string; message: string; count: number; suggestion: string | null }>();
    validation.errors.forEach((e) => {
      const g = map.get(e.code);
      if (g) g.count++;
      else map.set(e.code, { code: e.code, severity: e.severity, message: e.message, count: 1, suggestion: e.suggestion });
    });
    return [...map.values()].sort((a, b) => (a.severity === "error" ? -1 : 1) - (b.severity === "error" ? -1 : 1));
  }, [validation]);
  const errCount = validation.stats.error_count ?? 0;
  const warnCount = validation.stats.warning_count ?? 0;
  const rows = validation.stats.rows_in ?? 0;

  return (
    <div className="fadeIn">
      <h1 style={h1}>Review &amp; fix</h1>
      <p style={sub}>{validation.ok
        ? (isVoucher ? `${vCount.toLocaleString()} balanced voucher(s) ready to import.` : "No blocking errors — you're ready to plan the import.")
        : `${errCount} error(s) must be resolved before importing.`}</p>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(3,1fr)", gap: 14, marginBottom: 20 }}>
        <Stat n={errCount} label="Errors (block import)" color={errCount ? T.err : T.faint} />
        <Stat n={warnCount} label="Warnings" color={warnCount ? T.warn : T.faint} />
        {isVoucher
          ? <Stat n={vCount} label="Vouchers (balanced)" color={T.ok} />
          : <Stat n={Math.max(0, rows - errCount - warnCount)} label="Clean rows" color={T.ok} />}
      </div>
      {groups.length === 0 ? (
        <div style={{ ...card, padding: 28, textAlign: "center", color: T.ok }}>✓ Everything looks clean.</div>
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
          {groups.map((g) => {
            const sev = g.severity === "error" ? { fg: T.err, bg: T.errBg } : g.severity === "warning" ? { fg: T.warn, bg: T.warnBg } : { fg: T.ok, bg: T.okBg };
            return (
              <div key={g.code} style={{ ...card, padding: "14px 17px", display: "flex", gap: 13, alignItems: "flex-start" }}>
                <div style={{ width: 24, height: 24, borderRadius: 7, background: sev.bg, color: sev.fg, display: "flex", alignItems: "center", justifyContent: "center", fontWeight: 700, flex: "none" }}>!</div>
                <div style={{ flex: 1 }}>
                  <div style={{ display: "flex", alignItems: "center", gap: 9 }}>
                    <span style={{ fontSize: 14, fontWeight: 600 }}>{g.message}</span>
                    <span style={{ fontSize: 11, fontWeight: 500, fontFamily: T.mono, padding: "2px 8px", borderRadius: 999, color: sev.fg, background: sev.bg }}>{g.count} row(s)</span>
                  </div>
                  {g.suggestion && <div style={{ fontSize: 12.5, color: T.muted, marginTop: 5 }}>{g.suggestion}</div>}
                </div>
              </div>
            );
          })}
        </div>
      )}
      {isVoucher && voucherPreview && voucherPreview.vouchers.length > 0 && <VoucherReview preview={voucherPreview} />}
      <Footer onBack={onBack} primaryLabel="Continue to plan" onPrimary={onContinue} disabled={!validation.ok}
        hint={validation.ok ? undefined : "Fix the errors in your file, re-upload, or adjust the mapping"} />
    </div>
  );
}

function VoucherReview({ preview }: { preview: VoucherPreview }) {
  return (
    <div style={{ marginTop: 24 }}>
      <h2 style={{ fontSize: 15.5, fontWeight: 600, margin: "0 0 4px" }}>Voucher review</h2>
      <p style={{ ...sub, marginBottom: 14 }}>
        {preview.count.toLocaleString()} voucher(s) grouped from your rows
        {preview.vouchers.length < preview.count ? ` — showing the first ${preview.vouchers.length}` : ""}.
      </p>
      <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
        {preview.vouchers.map((v, i) => (
          <div key={i} style={{ ...card, padding: 0, overflow: "hidden" }}>
            <div style={{ display: "flex", alignItems: "center", gap: 10, padding: "11px 16px", borderBottom: `1px solid ${T.line}`, flexWrap: "wrap" }}>
              <span style={{ fontSize: 11, fontWeight: 600, padding: "2px 9px", borderRadius: 999, background: "rgba(79,70,229,.10)", color: T.accent }}>{v.voucher_type}</span>
              <span style={{ fontSize: 13.5, fontWeight: 600, fontFamily: T.mono }}>{v.voucher_number || "(no number)"}</span>
              <span style={{ fontSize: 12.5, color: T.faint }}>{v.date}</span>
              {v.party_ledger && <span style={{ fontSize: 12.5, color: T.muted }}>· {v.party_ledger}</span>}
              <span style={{ marginLeft: "auto", fontSize: 11.5, fontWeight: 600, padding: "3px 10px", borderRadius: 999, background: v.balanced ? T.okBg : T.errBg, color: v.balanced ? T.ok : T.err }}>
                {v.balanced ? "✓ Balanced" : "✗ Unbalanced"}
              </span>
            </div>
            <table style={{ width: "100%", borderCollapse: "collapse" }}>
              <tbody>
                {v.lines.map((ln, j) => (
                  <tr key={j} style={{ borderBottom: j < v.lines.length - 1 ? "1px solid rgba(0,0,0,.045)" : "none" }}>
                    <td style={{ padding: "7px 16px", fontSize: 12.5 }}>
                      {ln.ledger_name}
                      {ln.stock_item && (
                        <span style={{ color: T.faint }}> · {ln.stock_item}{ln.quantity ? ` (${ln.quantity}${ln.unit ? " " + ln.unit : ""})` : ""}</span>
                      )}
                    </td>
                    <td style={{ padding: "7px 8px", fontSize: 11.5, fontWeight: 600, width: 34, color: ln.dr_cr === "Dr" ? T.text : T.blue }}>{ln.dr_cr}</td>
                    <td style={{ padding: "7px 16px", fontSize: 12.5, fontFamily: T.mono, textAlign: "right", width: 130 }}>{Number(ln.amount).toLocaleString()}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ))}
      </div>
    </div>
  );
}
