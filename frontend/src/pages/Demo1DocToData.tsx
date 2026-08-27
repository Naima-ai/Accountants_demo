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

interface QueueItem {
  name: string;
  status: "pending" | "done" | "error";
  result?: Demo1Result;
  error?: string;
}

// The Demo 1 "wow": drop a chaotic pile of documents in, get validated,
// bookable journal entries out in seconds -- the before/after panel
// shows every pipeline stage the brief calls out (classify -> extract
// -> validate -> journal entry). Uploads run one at a time server-side
// (the local SLM serves one generate() call at a time), but the UI
// accepts any number of files at once via the Upload button, drag-drop,
// or "run all samples".
export default function Demo1DocToData() {
  const [dragging, setDragging] = useState(false);
  const [busy, setBusy] = useState(false);
  const [queue, setQueue] = useState<QueueItem[]>([]);
  const [selected, setSelected] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [samplesRun, setSamplesRun] = useState<SeedSamplesResponse | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);

  const runFiles = async (files: File[]) => {
    if (files.length === 0) return;
    setBusy(true);
    setError(null);
    const startIndex = queue.length;
    setQueue((q) => [...q, ...files.map((f) => ({ name: f.name, status: "pending" as const }))]);

    for (let i = 0; i < files.length; i++) {
      const index = startIndex + i;
      try {
        const r = await demo1Process(files[i]);
        setQueue((q) => q.map((item, idx) => (idx === index ? { ...item, status: "done", result: r } : item)));
      } catch (e) {
        setQueue((q) => q.map((item, idx) => (idx === index ? { ...item, status: "error", error: String(e) } : item)));
      }
      setSelected(index);
    }
    setBusy(false);
  };

  const onUploadClick = () => fileInput.current?.click();

  const onFilePicked = (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(e.target.files ?? []);
    e.target.value = ""; // allow re-picking the same file(s) later
    runFiles(files);
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
    runFiles(Array.from(e.dataTransfer.files ?? []));
  };

  const current = selected !== null ? queue[selected] : undefined;
  const result = current?.result;
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
        onClick={onUploadClick}
      >
        {busy ? "Processing…" : "Drop files here, or click to choose"}
        <input ref={fileInput} type="file" multiple hidden onChange={onFilePicked} />
      </div>

      <div style={{ marginTop: 14, display: "flex", gap: 10 }}>
        <button onClick={onUploadClick} disabled={busy}>
          Upload files
        </button>
        <button className="secondary" onClick={runAllSamples} disabled={busy}>
          Run all data_set/samples/ files
        </button>
      </div>

      {error && <p className="error-text" style={{ marginTop: 14 }}>{error}</p>}

      {queue.length > 0 && (
        <div className="card" style={{ marginTop: 18 }}>
          <h3>Uploaded files ({queue.length})</h3>
          <table>
            <thead>
              <tr>
                <th>File</th>
                <th>Status</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {queue.map((item, i) => (
                <tr
                  key={i}
                  className={i === selected ? "row-selected" : undefined}
                  style={{ cursor: item.status === "done" ? "pointer" : "default" }}
                  onClick={() => item.status === "done" && setSelected(i)}
                >
                  <td>{item.name}</td>
                  <td>
                    {item.status === "pending" && <span className="badge warn">processing…</span>}
                    {item.status === "done" && <StatusBadge status={item.result?.status ?? "—"} />}
                    {item.status === "error" && <span className="badge err">error</span>}
                  </td>
                  <td className="muted">{item.status === "error" ? item.error : ""}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

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
            <h3>Result — {current?.name}</h3>
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
              <p className="muted">P.IVA {String(extraction.supplier_vat ?? "—")}</p>
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
              <p className="muted">{String(journal.description ?? "")}</p>
              <table>
                <thead>
                  <tr>
                    <th>Account</th>
                    <th>Description</th>
                    <th>Dare</th>
                    <th>Avere</th>
                  </tr>
                </thead>
                <tbody>
                  {(journal.lines ?? []).map((line: any, i: number) => (
                    <tr key={i}>
                      <td>{line.account_name}</td>
                      <td>{line.description ?? ""}</td>
                      <td>{line.debit ?? ""}</td>
                      <td>{line.credit ?? ""}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>

          <div className="card" style={{ marginTop: 14 }}>
            <h3>All extracted fields</h3>
            <table>
              <tbody>
                {[
                  ["Supplier", extraction.supplier_name],
                  ["Supplier P.IVA", extraction.supplier_vat],
                  ["Customer", extraction.customer_name],
                  ["Document number", extraction.document_number],
                  ["Document date", extraction.document_date],
                  ["Due date", extraction.due_date],
                  ["Currency", extraction.currency],
                  ["Subtotal", extraction.subtotal],
                  ["VAT amount", extraction.vat_amount],
                  ["Total amount", extraction.total_amount],
                  ["IBAN", extraction.iban],
                  ["Extraction method", extraction.method],
                  ["Notes", extraction.notes],
                ].map(([label, value]) => (
                  <tr key={label as string}>
                    <th style={{ width: 180 }}>{label}</th>
                    <td>{value === null || value === undefined || value === "" ? "—" : String(value)}</td>
                  </tr>
                ))}
              </tbody>
            </table>

            {(extraction.line_items ?? []).length > 0 && (
              <>
                <h3 style={{ marginTop: 16 }}>Line items</h3>
                <table>
                  <thead>
                    <tr>
                      <th>Description</th>
                      <th>Qty</th>
                      <th>Unit price</th>
                      <th>Total</th>
                      <th>VAT %</th>
                    </tr>
                  </thead>
                  <tbody>
                    {(extraction.line_items as any[]).map((li, i) => (
                      <tr key={i}>
                        <td>{li.description ?? "—"}</td>
                        <td>{li.quantity ?? "—"}</td>
                        <td>{li.unit_price ?? "—"}</td>
                        <td>{li.total ?? "—"}</td>
                        <td>{li.vat_rate ?? "—"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </>
            )}
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
