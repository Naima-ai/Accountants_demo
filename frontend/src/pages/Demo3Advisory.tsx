import { useEffect, useRef, useState } from "react";
import {
  Client, demo3Generate, demo3ListStatements, demo3UploadStatement, GenerateReportResponse,
  listClients, StatementInput, StoredStatement,
} from "../api";

const FIELDS: Array<{ key: keyof StatementInput; label: string }> = [
  { key: "revenue", label: "Revenue" },
  { key: "cogs", label: "COGS" },
  { key: "operating_expenses", label: "Operating expenses" },
  { key: "net_income", label: "Net income" },
  { key: "current_assets", label: "Current assets" },
  { key: "inventory", label: "Inventory" },
  { key: "current_liabilities", label: "Current liabilities" },
  { key: "total_debt", label: "Total debt" },
  { key: "equity", label: "Equity" },
  { key: "accounts_receivable", label: "Accounts receivable" },
];

type Mode = "existing" | "upload" | "manual";

function ReportView({ result }: { result: GenerateReportResponse }) {
  return (
    <>
      <div className="card">
        <h3>{result.period} — Ratios ({result.compared_to_prior ? "vs. prior period" : "no prior period yet"})</h3>
        <table>
          <thead>
            <tr>
              <th>Metric</th>
              <th>Current</th>
              {result.prior_ratios && <th>Prior</th>}
            </tr>
          </thead>
          <tbody>
            {(Object.keys(result.ratios) as Array<keyof typeof result.ratios>).map((k) => (
              <tr key={k}>
                <td>{k}</td>
                <td>{result.ratios[k] ?? "—"}</td>
                {result.prior_ratios && <td>{result.prior_ratios[k] ?? "—"}</td>}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="card">
        <h3>Anomalies ({result.anomalies.length})</h3>
        {result.anomalies.length === 0 && <p className="muted">No significant anomalies detected.</p>}
        {result.anomalies.map((a, i) => (
          <p key={i}>
            <span className={`badge ${a.severity === "alert" ? "err" : "warn"}`}>{a.severity}</span> {a.message}
          </p>
        ))}
      </div>

      <div className="card">
        <h3>Advisory letter ({result.narrative_method})</h3>
        <pre>{result.letter_text}</pre>
      </div>
    </>
  );
}

// Demo 3's "wow": a raw financial statement in, a plain-language
// advisory letter with alerts + recommendations out in ~1 minute --
// three ways in: an existing client's stored years, an uploaded CSV,
// or a manually filled-in form.
export default function Demo3Advisory() {
  const [mode, setMode] = useState<Mode>("existing");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [results, setResults] = useState<GenerateReportResponse[]>([]);

  // Shared client roster (Existing Client + Upload Statements modes).
  const [clients, setClients] = useState<Client[]>([]);
  const [clientId, setClientId] = useState("");

  useEffect(() => {
    listClients()
      .then((cs) => {
        setClients(cs);
        if (cs.length > 0) setClientId(cs[0].id);
      })
      .catch((e) => setError(String(e)));
  }, []);

  // ---- Mode A: Existing Client -----------------------------------------
  const [statements, setStatements] = useState<StoredStatement[]>([]);
  const [statementsError, setStatementsError] = useState<string | null>(null);

  useEffect(() => {
    if (mode !== "existing" || !clientId) return;
    demo3ListStatements(clientId)
      .then(setStatements)
      .catch((e) => setStatementsError(String(e)));
  }, [mode, clientId]);

  const generateFromExisting = async () => {
    if (statements.length === 0) return;
    const latest = statements[statements.length - 1];
    setBusy(true);
    setError(null);
    setResults([]);
    try {
      const r = await demo3Generate(clientId, latest.period, latest.data);
      setResults([r]);
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  };

  // ---- Mode B: Upload Statements ----------------------------------------
  const fileInput = useRef<HTMLInputElement>(null);
  const [uploadFile, setUploadFile] = useState<File | null>(null);

  const uploadAndGenerate = async () => {
    if (!uploadFile || !clientId) return;
    setBusy(true);
    setError(null);
    setResults([]);
    try {
      const r = await demo3UploadStatement(uploadFile, clientId);
      setResults(r);
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  };

  // ---- Mode C: Manual Input -----------------------------------------------
  const [manualClientId, setManualClientId] = useState("c-001");
  const [manualPeriod, setManualPeriod] = useState("2026-Q2");
  const [statement, setStatement] = useState<StatementInput>({});

  const setField = (key: keyof StatementInput, value: string) => {
    setStatement((prev) => ({ ...prev, [key]: value === "" ? undefined : Number(value) }));
  };

  const generateFromManual = async () => {
    setBusy(true);
    setError(null);
    setResults([]);
    try {
      const r = await demo3Generate(manualClientId, manualPeriod, statement);
      setResults([r]);
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div>
      <h2>03 · Advisory Report + Alerts</h2>
      <p className="muted">
        The agent computes ratios, flags anomalies vs. the prior period and benchmarks, and writes a client-ready
        letter — from an existing client's records, an uploaded statement, or manual entry.
      </p>

      <div style={{ display: "flex", gap: 10, marginBottom: 18 }}>
        <button className={mode === "existing" ? "" : "secondary"} onClick={() => { setMode("existing"); setResults([]); }}>
          Existing Client
        </button>
        <button className={mode === "upload" ? "" : "secondary"} onClick={() => { setMode("upload"); setResults([]); }}>
          Upload Statements
        </button>
        <button className={mode === "manual" ? "" : "secondary"} onClick={() => { setMode("manual"); setResults([]); }}>
          Generate from Manual Input
        </button>
      </div>

      {mode === "existing" && (
        <div className="card">
          <h3>Existing Client</h3>
          <div style={{ display: "flex", gap: 10, alignItems: "center", marginBottom: 12 }}>
            <select value={clientId} onChange={(e) => setClientId(e.target.value)}>
              {clients.length === 0 && <option value="">No clients yet</option>}
              {clients.map((c) => (
                <option key={c.id} value={c.id}>{c.name} ({c.id})</option>
              ))}
            </select>
          </div>
          {statementsError && <p className="error-text">{statementsError}</p>}
          {clientId && statements.length === 0 && !statementsError && (
            <p className="muted">No stored financial statements for this client yet.</p>
          )}
          {statements.length > 0 && (
            <>
              <table>
                <thead>
                  <tr>
                    <th>Period</th>
                    {FIELDS.map((f) => <th key={f.key}>{f.label}</th>)}
                  </tr>
                </thead>
                <tbody>
                  {statements.map((s) => (
                    <tr key={s.period}>
                      <td>{s.period}</td>
                      {FIELDS.map((f) => <td key={f.key}>{s.data[f.key] ?? "—"}</td>)}
                    </tr>
                  ))}
                </tbody>
              </table>
              <div style={{ marginTop: 14 }}>
                <button onClick={generateFromExisting} disabled={busy}>
                  {busy ? "Generating…" : `Generate advisory report (${statements[statements.length - 1].period})`}
                </button>
              </div>
            </>
          )}
        </div>
      )}

      {mode === "upload" && (
        <div className="card">
          <h3>Upload Statements</h3>
          <p className="muted">
            CSV with one row per fiscal year: a <code>period</code> column plus any of{" "}
            {FIELDS.map((f) => f.key).join(", ")} as columns.
          </p>
          <div style={{ display: "flex", gap: 10, alignItems: "center", marginBottom: 12 }}>
            <select value={clientId} onChange={(e) => setClientId(e.target.value)}>
              {clients.length === 0 && <option value="">No clients yet</option>}
              {clients.map((c) => (
                <option key={c.id} value={c.id}>{c.name} ({c.id})</option>
              ))}
            </select>
            <button className="secondary" onClick={() => fileInput.current?.click()}>
              {uploadFile ? uploadFile.name : "Choose CSV"}
            </button>
            <input
              ref={fileInput} type="file" accept=".csv" hidden
              onChange={(e) => setUploadFile(e.target.files?.[0] ?? null)}
            />
          </div>
          <button onClick={uploadAndGenerate} disabled={busy || !uploadFile || !clientId}>
            {busy ? "Processing…" : "Upload & generate report(s)"}
          </button>
        </div>
      )}

      {mode === "manual" && (
        <div className="card">
          <h3>Manual Input</h3>
          <div style={{ display: "flex", gap: 10, marginBottom: 14 }}>
            <div>
              <div className="muted">Client ID</div>
              <input value={manualClientId} onChange={(e) => setManualClientId(e.target.value)} />
            </div>
            <div>
              <div className="muted">Period</div>
              <input value={manualPeriod} onChange={(e) => setManualPeriod(e.target.value)} placeholder="2026-Q2" />
            </div>
          </div>
          <div className="stage-grid">
            {FIELDS.map((f) => (
              <div key={f.key}>
                <div className="muted">{f.label}</div>
                <input
                  type="number"
                  value={statement[f.key] ?? ""}
                  onChange={(e) => setField(f.key, e.target.value)}
                  style={{ width: "100%" }}
                />
              </div>
            ))}
          </div>
          <div style={{ marginTop: 14 }}>
            <button onClick={generateFromManual} disabled={busy}>
              {busy ? "Generating…" : "Generate from Manual Input"}
            </button>
          </div>
        </div>
      )}

      {error && <p className="error-text">{error}</p>}

      {results.map((r, i) => <ReportView key={i} result={r} />)}
    </div>
  );
}
