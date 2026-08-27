import { useState } from "react";
import { demo3Generate, GenerateReportResponse, StatementInput } from "../api";

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

// Demo 3's "wow": a raw financial statement in, a plain-language
// advisory letter with alerts + recommendations out in ~1 minute.
export default function Demo3Advisory() {
  const [clientId, setClientId] = useState("c-001");
  const [period, setPeriod] = useState("2026-Q2");
  const [statement, setStatement] = useState<StatementInput>({});
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<GenerateReportResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  const setField = (key: keyof StatementInput, value: string) => {
    setStatement((prev) => ({ ...prev, [key]: value === "" ? undefined : Number(value) }));
  };

  const generate = async () => {
    setBusy(true);
    setError(null);
    setResult(null);
    try {
      const r = await demo3Generate(clientId, period, statement);
      setResult(r);
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
        Enter a client's financial statement — the agent computes ratios, flags anomalies vs. the prior period and
        benchmarks, and writes the client-ready letter.
      </p>

      <div className="card">
        <h3>Statement</h3>
        <div style={{ display: "flex", gap: 10, marginBottom: 14 }}>
          <div>
            <div className="muted">Client ID</div>
            <input value={clientId} onChange={(e) => setClientId(e.target.value)} />
          </div>
          <div>
            <div className="muted">Period</div>
            <input value={period} onChange={(e) => setPeriod(e.target.value)} placeholder="2026-Q2" />
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
          <button onClick={generate} disabled={busy}>
            {busy ? "Generating…" : "Generate report"}
          </button>
        </div>
      </div>

      {error && <p className="error-text">{error}</p>}

      {result && (
        <>
          <div className="card">
            <h3>Ratios ({result.compared_to_prior ? "vs. prior period" : "no prior period yet"})</h3>
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
      )}
    </div>
  );
}
