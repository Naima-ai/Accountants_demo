import { useEffect, useRef, useState } from "react";
import {
  Client, demo1GetDocument, demo1ListDocuments, demo1RunDocument, demo1UploadDocument,
  Demo1Result, DocumentSummary, listClients,
} from "../api";

function ConfidenceBadge({ value }: { value: number | undefined | null }) {
  if (value === undefined || value === null) return null;
  const cls = value >= 0.85 ? "ok" : value >= 0.6 ? "warn" : "err";
  return <span className={`badge ${cls}`}>{Math.round(value * 100)}% confidence</span>;
}

function StatusBadge({ status }: { status: string }) {
  const cls = status === "accounted" ? "ok" : status === "needs_review" ? "warn" : status === "uploaded" ? "neutral" : "err";
  return <span className={`badge ${cls}`}>{status}</span>;
}

// The Demo 1 "wow": select a client, see the chaotic pile of documents
// already on file for them (or upload a new one), pick one, and watch
// the pipeline run stage by stage -- classify -> extract -> validate
// -> journal entry. Nothing is processed until explicitly Run: uploaded
// and pre-seeded documents are indistinguishable in this table, both
// sit as "uploaded" until picked.
export default function Demo1DocToData() {
  const [clients, setClients] = useState<Client[]>([]);
  const [clientId, setClientId] = useState("");
  const [docs, setDocs] = useState<DocumentSummary[]>([]);
  const [docsError, setDocsError] = useState<string | null>(null);
  const [runningDocId, setRunningDocId] = useState<string | null>(null);
  const [dragging, setDragging] = useState(false);
  const [result, setResult] = useState<Demo1Result | null>(null);
  const [resultDocName, setResultDocName] = useState<string>("");
  const [error, setError] = useState<string | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);

  useEffect(() => {
    listClients()
      .then((cs) => {
        setClients(cs);
        if (cs.length > 0) setClientId(cs[0].id);
      })
      .catch((e) => setDocsError(String(e)));
  }, []);

  const reloadDocs = (id: string) => {
    if (!id) {
      setDocs([]);
      return;
    }
    demo1ListDocuments(id)
      .then(setDocs)
      .catch((e) => setDocsError(String(e)));
  };

  useEffect(() => {
    reloadDocs(clientId);
  }, [clientId]);

  const runDoc = async (doc: DocumentSummary) => {
    setRunningDocId(doc.doc_id);
    setError(null);
    try {
      const r = await demo1RunDocument(doc.doc_id);
      setResult(r);
      setResultDocName(doc.original_filename);
      reloadDocs(clientId);
    } catch (e) {
      setError(String(e));
    } finally {
      setRunningDocId(null);
    }
  };

  const viewDoc = async (doc: DocumentSummary) => {
    setError(null);
    try {
      const r = await demo1GetDocument(doc.doc_id);
      setResult(r);
      setResultDocName(doc.original_filename);
    } catch (e) {
      setError(String(e));
    }
  };

  const uploadFiles = async (files: File[]) => {
    if (files.length === 0 || !clientId) return;
    setError(null);
    for (const file of files) {
      try {
        const summary = await demo1UploadDocument(file, clientId);
        setDocs((d) => [summary, ...d]);
      } catch (e) {
        setError(String(e));
      }
    }
  };

  const onUploadClick = () => fileInput.current?.click();

  const onFilePicked = (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(e.target.files ?? []);
    e.target.value = ""; // allow re-picking the same file(s) later
    uploadFiles(files);
  };

  const onDrop = (e: React.DragEvent) => {
    e.preventDefault();
    setDragging(false);
    uploadFiles(Array.from(e.dataTransfer.files ?? []));
  };

  const extraction = (result?.extraction ?? {}) as Record<string, any>;
  const classification = (result?.classification ?? {}) as Record<string, any>;
  const validation = (result?.validation ?? {}) as Record<string, any>;
  const journal = (result?.journal_entry ?? {}) as Record<string, any>;

  return (
    <div>
      <h2>01 · Sovereign Doc-to-Data</h2>
      <p className="muted">Select a client, pick one of their documents (or upload a new one), and run the pipeline: classify → extract → validate → bookkeep.</p>

      <div className="card">
        <h3>Client</h3>
        <div style={{ display: "flex", gap: 10, alignItems: "center" }}>
          <select value={clientId} onChange={(e) => setClientId(e.target.value)}>
            {clients.length === 0 && <option value="">No clients yet</option>}
            {clients.map((c) => (
              <option key={c.id} value={c.id}>{c.name} ({c.id})</option>
            ))}
          </select>
        </div>
      </div>

      <div
        className={`dropzone ${dragging ? "dragging" : ""}`}
        style={{ marginTop: 14 }}
        onDragOver={(e) => {
          e.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={onDrop}
        onClick={onUploadClick}
      >
        {clientId ? "Drop files here, or click to choose" : "Select a client first"}
        <input ref={fileInput} type="file" multiple hidden onChange={onFilePicked} disabled={!clientId} />
      </div>

      <div style={{ marginTop: 14 }}>
        <button onClick={onUploadClick} disabled={!clientId}>
          Upload files
        </button>
      </div>

      {error && <p className="error-text" style={{ marginTop: 14 }}>{error}</p>}
      {docsError && <p className="error-text" style={{ marginTop: 14 }}>{docsError}</p>}

      <div className="card" style={{ marginTop: 18 }}>
        <h3>Documents ({docs.length})</h3>
        {clientId && docs.length === 0 && !docsError && <p className="muted">No documents for this client yet.</p>}
        {docs.length > 0 && (
          <table>
            <thead>
              <tr>
                <th>File</th>
                <th>Type</th>
                <th>Classification</th>
                <th>Status</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {docs.map((d) => (
                <tr key={d.doc_id}>
                  <td>{d.original_filename}</td>
                  <td className="muted">{d.file_type ?? "—"}</td>
                  <td className="muted">{d.classification ?? "—"}</td>
                  <td><StatusBadge status={d.status} /></td>
                  <td>
                    {d.status === "uploaded" ? (
                      <button disabled={runningDocId === d.doc_id} onClick={() => runDoc(d)}>
                        {runningDocId === d.doc_id ? "Running…" : "Run"}
                      </button>
                    ) : (
                      <button className="secondary" onClick={() => viewDoc(d)}>
                        View
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {result && (
        <div style={{ marginTop: 18 }}>
          <div className="card">
            <h3>Result — {resultDocName}</h3>
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
