// Thin fetch wrapper around the consolidated FastAPI surface
// (src/api/api.py). Same-origin relative paths -- the Vite dev server
// proxies /api to uvicorn (see vite.config.ts), and the production
// build is served BY that same FastAPI process (src/main.py mounts
// frontend/dist/), so no base URL ever needs configuring.

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    headers: init?.body instanceof FormData ? undefined : { "Content-Type": "application/json" },
    ...init,
  });
  if (!res.ok) {
    const detail = await res.text().catch(() => res.statusText);
    throw new Error(`${res.status} ${res.statusText}: ${detail}`);
  }
  return res.json() as Promise<T>;
}

// ---- Demo 1 -- Sovereign Doc-to-Data ----------------------------------

export interface LineItem {
  description?: string | null;
  quantity?: string | null;
  unit_price?: string | null;
  total?: string | null;
  vat_rate?: string | null;
}

export interface Demo1Result {
  doc_id: string;
  status: string;
  file: string;
  classification?: Record<string, unknown>;
  extraction?: Record<string, unknown>;
  validation?: Record<string, unknown>;
  journal_entry?: Record<string, unknown>;
  supplier_hint?: Record<string, unknown> | null;
  error?: string;
}

export function demo1Process(file: File, clientId = "c-001"): Promise<Demo1Result> {
  const form = new FormData();
  form.append("file", file);
  return request(`/api/demo-1/process?client_id=${encodeURIComponent(clientId)}`, {
    method: "POST",
    body: form,
  });
}

export interface SeedSamplesResponse {
  total: number;
  ready_to_post: number;
  needs_review: number;
  errors: number;
  by_group: Record<string, { total: number; ready_to_post: number; needs_review: number; errors: number }>;
}

export function demo1IngestSamples(clientId = "c-001"): Promise<SeedSamplesResponse> {
  return request(`/api/demo-1/ingest-samples?client_id=${encodeURIComponent(clientId)}`, { method: "POST" });
}

// ---- Demo 2 -- Reminder Agent ------------------------------------------

export interface RosterClientResult {
  client_id: string;
  client_name: string;
  period: string;
  missing_count: number;
  reminders_sent: number;
  reminders: Array<{ doc_type: string; follow_up_number: number; message: string }>;
}

export interface RunRosterResponse {
  period: string;
  clients_processed: number;
  total_reminders_sent: number;
  estimated_hours_saved: number;
  clients: RosterClientResult[];
}

export function demo2RunRoster(clientIds: string[], period: string): Promise<RunRosterResponse> {
  return request(`/api/demo-2/run-roster`, {
    method: "POST",
    body: JSON.stringify({ client_ids: clientIds, period }),
  });
}

export interface DashboardResponse {
  period: string;
  clients: Array<{
    client_id: string;
    client_name: string;
    expected: number;
    received: number;
    missing: number;
    reminders_sent: number;
  }>;
}

export function demo2Dashboard(period: string): Promise<DashboardResponse> {
  return request(`/api/demo-2/dashboard/${encodeURIComponent(period)}`);
}

// ---- Demo 3 -- Advisory Report ------------------------------------------

export interface StatementInput {
  revenue?: number;
  cogs?: number;
  operating_expenses?: number;
  net_income?: number;
  current_assets?: number;
  inventory?: number;
  current_liabilities?: number;
  total_debt?: number;
  equity?: number;
  accounts_receivable?: number;
}

export interface RatioSet {
  revenue?: number | null;
  gross_margin_pct?: number | null;
  net_margin_pct?: number | null;
  current_ratio?: number | null;
  quick_ratio?: number | null;
  dso_days?: number | null;
  debt_to_equity?: number | null;
}

export interface Anomaly {
  metric: string;
  severity: string;
  message: string;
  current_value?: number | null;
  reference_value?: number | null;
  reference_type: string;
}

export interface GenerateReportResponse {
  report_id: number;
  client_name: string;
  period: string;
  ratios: RatioSet;
  prior_ratios?: RatioSet | null;
  anomalies: Anomaly[];
  narrative_method: string;
  letter_text: string;
  compared_to_prior: boolean;
  generated_at: string;
}

export function demo3Generate(clientId: string, period: string, statement: StatementInput): Promise<GenerateReportResponse> {
  return request(`/api/demo-3/generate`, {
    method: "POST",
    body: JSON.stringify({ client_id: clientId, period, statement }),
  });
}

// ---- Shared --------------------------------------------------------------

export interface Client {
  id: string;
  name: string;
  vat_number?: string | null;
  email?: string | null;
  phone?: string | null;
  preferred_tone?: string | null;
}

export function listClients(): Promise<Client[]> {
  return request(`/api/clients`);
}

export function upsertClient(client: {
  client_id: string;
  name: string;
  vat_number?: string;
  email?: string;
  phone?: string;
  preferred_tone?: string;
}): Promise<Client> {
  return request(`/api/clients`, { method: "POST", body: JSON.stringify(client) });
}

export interface ReviewQueueItem {
  id: number;
  demo: string;
  ref_type: string;
  ref_id: string;
  reason: string;
  status: string;
  created_at: string;
}

export function reviewQueue(): Promise<ReviewQueueItem[]> {
  return request(`/api/review-queue`);
}

export interface Metrics {
  documents_processed: number;
  accounted_without_review_pct: number | null;
  avg_classification_confidence: number | null;
  review_queue_open: number;
  recurring_suppliers_learned: number;
  data_egress_bytes: number;
  latency_ms: Record<string, { avg: number | null; count: number; last: number | null }>;
}

export function getMetrics(): Promise<Metrics> {
  return request(`/api/metrics`);
}
