import { useRef, useState } from "react";
import { demo1IngestSamples, demo1Process, Demo1Result, SeedSamplesResponse } from "../api";

function ConfidenceBadge({ value }: { value: number | undefined | null }) {
  if (value === undefined || value === null) return null;
  const cls = value >= 0.85 ? "ok" : value >= 0.6 ? "warn" : "err";
  return <span className={`badge ${cls}`}>{Math.round(value * 100)}% confidence</span>;
}

function StatusBadge({ status }: { status: string }) {
  const cls = status === "accounted" ? "ok" : status === "needs_review" ? "warn" : "err";
  return <span className={`badge ${cls}`}>{status}</span>;
}

// The Demo 1 "wow": drop a chaotic document in, get a validated,
// bookable journal entry out in seconds -- the before/after panel
// shows every pipeline stage the brief calls out (classify -> extract
// -> validate -> journal entry).
export default function Demo1DocToData() {
  const [dragging, setDragging] = useState(false);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<Demo1Result | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [samplesRun, setSamplesRun] = useState<SeedSamplesResponse | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);

  const runFile = async (file: File) => {
    setBusy(true);
    setError(null);
    setResult(null);
    try {
      const r = await demo1Process(file);
      setResult(r);
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  };

  const runAllSamples = async () => {
    setBusy(true);
    setError(null);
    try {
      const r = await demo1IngestSamples();
      setSamplesRun(r);
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  };

  const onDrop = (e: React.DragEvent) => {
    e.preventDefault();
    setDragging(false);
    const file = e.dataTransfer.files?.[0];
    if (file) runFile(file);
  };

  const extraction = (result?.extraction ?? {}) as Record<string, any>;
  const classification = (result?.classification ?? {}) as Record<string, any>;
  const validation = (result?.validation ?? {}) as Record<string, any>;
  const journal = (result?.journal_entry ?? {}) as Record<string, any>;

  return (
    <div>
      <h2>01 · Sovereign Doc-to-Data</h2>
      <p className="muted">Drop an invoice, receipt, or e-invoice — analog and foreign chaos in, bookkeeping-ready entries out.</p>

      <div
        className={`dropzone ${dragging ? "dragging" : ""}`}
        onDragOver={(e) => {
          e.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={onDrop}
        onClick={() => fileInput.current?.click()}
      >
        {busy ? "Processing…" : "Drop a file here, or click to choose one"}
        <input
          ref={fileInput}
          type="file"
          hidden
          onChange={(e) => e.target.files?.[0] && runFile(e.target.files[0])}
        />
      </div>

      <div style={{ marginTop: 14 }}>
        <button className="secondary" onClick={runAllSamples} disabled={busy}>
          Run all data_set/samples/ files
        </button>
      </div>

      {error && <p className="error-text" style={{ marginTop: 14 }}>{error}</p>}

      {samplesRun && (
        <div className="card" style={{ marginTop: 18 }}>
          <h3>Bulk sample run</h3>
          <p>
            {samplesRun.total} documents — {samplesRun.ready_to_post} ready to post, {samplesRun.needs_review} need
            review, {samplesRun.errors} errors.
          </p>
          <table>
            <thead>
              <tr>
                <th>Group</th>
                <th>Total</th>
                <th>Ready</th>
                <th>Needs review</th>
                <th>Errors</th>
              </tr>
            </thead>
            <tbody>
              {Object.entries(samplesRun.by_group).map(([group, stats]) => (
                <tr key={group}>
                  <td>{group}</td>
                  <td>{stats.total}</td>
                  <td>{stats.ready_to_post}</td>
                  <td>{stats.needs_review}</td>
                  <td>{stats.errors}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {result && (
        <div style={{ marginTop: 18 }}>
          <div className="card">
            <h3>Result</h3>
            <p>
              <StatusBadge status={result.status} /> &nbsp; doc_id: {result.doc_id}
            </p>
            {result.error && <p className="error-text">{result.error}</p>}
          </div>

          <div className="stage-grid">
            <div className="card">
              <h3>1 · Classification</h3>
              <p>
                {String(classification.document_type ?? "—")}{" "}
                <ConfidenceBadge value={classification.confidence} />
              </p>
              <p className="muted">method: {String(classification.method ?? "—")}</p>
              {classification.reasoning && <p className="muted">{classification.reasoning}</p>}
            </div>

            <div className="card">
              <h3>2 · Extraction</h3>
              <p>
                <strong>{String(extraction.supplier_name ?? "—")}</strong>{" "}
                <ConfidenceBadge value={extraction.confidence} />
              </p>
              <p className="muted">
                doc #{String(extraction.document_number ?? "—")} · {String(extraction.document_date ?? "—")} ·{" "}
                {String(extraction.total_amount ?? "—")} {String(extraction.currency ?? "")}
              </p>
              <p className="muted">method: {String(extraction.method ?? "—")}</p>
            </div>

            <div className="card">
              <h3>3 · Validation</h3>
              <p>
                {validation.is_valid ? <span className="badge ok">valid</span> : <span className="badge err">invalid</span>}{" "}
                <ConfidenceBadge value={validation.confidence} />
              </p>
              <p className="muted">{(validation.issues ?? []).length} issue(s) flagged</p>
              {(validation.issues ?? []).slice(0, 3).map((iss: any, i: number) => (
                <p key={i} className="muted">
                  [{iss.severity}] {iss.message}
                </p>
              ))}
            </div>

            <div className="card">
              <h3>4 · Journal entry</h3>
              <p>
                {journal.status === "ready_to_post" ? (
                  <span className="badge ok">ready to post</span>
                ) : (
                  <span className="badge warn">pending review</span>
                )}
              </p>
              <table>
                <thead>
                  <tr>
                    <th>Account</th>
                    <th>Dare</th>
                    <th>Avere</th>
                  </tr>
                </thead>
                <tbody>
                  {(journal.lines ?? []).map((line: any, i: number) => (
                    <tr key={i}>
                      <td>{line.account_name}</td>
                      <td>{line.debit ?? ""}</td>
                      <td>{line.credit ?? ""}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>

          {result.supplier_hint && (
            <div className="card">
              <h3>Recognized recurring supplier</h3>
              <p className="muted">
                Seen {String(result.supplier_hint.seen_count)} time(s) before — auto-suggested category:{" "}
                {String(result.supplier_hint.coa_name)}
              </p>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
