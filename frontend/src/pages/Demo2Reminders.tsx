import { useEffect, useState } from "react";
import { Client, demo2RunRoster, listClients, RunRosterResponse } from "../api";

// The Demo 2 "wow": "run" on the whole roster and watch the agent
// decide who/how to chase, on its own -- no per-client manual work.
export default function Demo2Reminders() {
  const [clients, setClients] = useState<Client[]>([]);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [period, setPeriod] = useState(() => new Date().toISOString().slice(0, 7));
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<RunRosterResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    listClients()
      .then((cs) => {
        setClients(cs);
        setSelected(new Set(cs.map((c) => c.id)));
      })
      .catch((e) => setError(String(e)));
  }, []);

  const toggle = (id: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });
  };

  const run = async () => {
    setBusy(true);
    setError(null);
    setResult(null);
    try {
      const r = await demo2RunRoster(Array.from(selected), period);
      setResult(r);
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div>
      <h2>02 · Reminder Agent &amp; Document Collection</h2>
      <p className="muted">
        Run on the roster: the agent cross-checks each client's checklist, drafts a personalized reminder for
        anything missing, and follows up on its own.
      </p>

      <div className="card">
        <h3>Roster ({selected.size} of {clients.length} selected)</h3>
        <div style={{ display: "flex", gap: 10, alignItems: "center", marginBottom: 12 }}>
          <label className="muted" htmlFor="period">Period</label>
          <input id="period" value={period} onChange={(e) => setPeriod(e.target.value)} placeholder="2026-07" />
          <button onClick={run} disabled={busy || selected.size === 0}>
            {busy ? "Running…" : `Run on ${selected.size} client(s)`}
          </button>
        </div>
        {clients.length === 0 && (
          <p className="muted">
            No clients yet. Seed some via <code>POST /api/demo-2/seed</code> or{" "}
            <code>python src/database/seed_demo_data.py</code>.
          </p>
        )}
        <table>
          <tbody>
            {clients.map((c) => (
              <tr key={c.id}>
                <td style={{ width: 24 }}>
                  <input type="checkbox" checked={selected.has(c.id)} onChange={() => toggle(c.id)} />
                </td>
                <td>{c.name}</td>
                <td className="muted">{c.preferred_tone}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {error && <p className="error-text">{error}</p>}

      {result && (
        <>
          <div className="stat-grid" style={{ marginBottom: 18 }}>
            <div className="stat-tile">
              <div className="value">{result.clients_processed}</div>
              <div className="label">Clients processed</div>
            </div>
            <div className="stat-tile">
              <div className="value">{result.total_reminders_sent}</div>
              <div className="label">Reminders sent</div>
            </div>
            <div className="stat-tile">
              <div className="value">{result.estimated_hours_saved}h</div>
              <div className="label">Estimated hours saved</div>
            </div>
          </div>

          {result.clients.map((c) => (
            <div key={c.client_id} className="card">
              <h3>
                {c.client_name} — {c.missing_count} missing, {c.reminders_sent} reminder(s) sent
              </h3>
              {c.reminders.map((r, i) => (
                <p key={i} className="muted">
                  [{r.doc_type}, follow-up #{r.follow_up_number}] {r.message}
                </p>
              ))}
              {c.reminders.length === 0 && <p className="muted">Fully up to date — nothing to chase.</p>}
            </div>
          ))}
        </>
      )}
    </div>
  );
}
