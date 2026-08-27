import { useEffect, useState } from "react";
import { getMetrics, Metrics } from "../api";

// The brief's "wow effect, measured" slide: accuracy, on-board latency,
// 0-bytes egress, and the per-client learning curve, live from
// GET /api/metrics (aggregated from real processed documents +
// this server's own observed call latencies, not fabricated numbers).
export default function Dashboard() {
  const [metrics, setMetrics] = useState<Metrics | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = () => {
    getMetrics()
      .then(setMetrics)
      .catch((e) => setError(String(e)));
  };

  useEffect(() => {
    load();
    const id = setInterval(load, 5000);
    return () => clearInterval(id);
  }, []);

  return (
    <div>
      <h2>Live metrics</h2>
      <p className="muted">Refreshes every 5s. Everything below is computed from documents actually processed by this server — nothing simulated.</p>

      {error && <p className="error-text">{error}</p>}

      {metrics && (
        <>
          <div className="stat-grid">
            <div className="stat-tile">
              <div className="value">{metrics.documents_processed}</div>
              <div className="label">Documents processed</div>
            </div>
            <div className="stat-tile">
              <div className="value">
                {metrics.accounted_without_review_pct !== null ? `${metrics.accounted_without_review_pct}%` : "—"}
              </div>
              <div className="label">Accounted without review</div>
            </div>
            <div className="stat-tile">
              <div className="value">
                {metrics.avg_classification_confidence !== null ? metrics.avg_classification_confidence.toFixed(2) : "—"}
              </div>
              <div className="label">Avg classification confidence</div>
            </div>
            <div className="stat-tile">
              <div className="value">{metrics.recurring_suppliers_learned}</div>
              <div className="label">Suppliers recognized (2nd+ sighting)</div>
            </div>
            <div className="stat-tile">
              <div className="value">{metrics.review_queue_open}</div>
              <div className="label">Open review-queue items</div>
            </div>
            <div className="stat-tile">
              <div className="value">{metrics.data_egress_bytes} bytes</div>
              <div className="label">Data egress (on-prem, always 0)</div>
            </div>
          </div>

          <div className="card" style={{ marginTop: 18 }}>
            <h3>On-board inference latency (ms, observed by this server)</h3>
            <table>
              <thead>
                <tr>
                  <th>Call</th>
                  <th>Avg</th>
                  <th>Last</th>
                  <th>Sample size</th>
                </tr>
              </thead>
              <tbody>
                {Object.entries(metrics.latency_ms).map(([key, v]) => (
                  <tr key={key}>
                    <td>{key}</td>
                    <td>{v.avg !== null ? `${v.avg.toFixed(0)} ms` : "—"}</td>
                    <td>{v.last !== null ? `${v.last.toFixed(0)} ms` : "—"}</td>
                    <td>{v.count}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </div>
  );
}
