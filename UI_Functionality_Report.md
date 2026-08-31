# UI Functionality Report

There are two front-ends over the same backend:
- **Streamlit console** (`src/ui/ui.py`)
- **React/Vite SPA** (`frontend/`) — a lighter, componentized version of the same three workflows, talking to the FastAPI router (`src/api/api.py`). (Not fully developed)
---

## Shared / cross-cutting functionality

These appear on every page, top of the app:

- **Live status banner** — four live tiles: *Edge Inference: Active*, *Data Egress: 0 Bytes*, *Clients Onboarded* (count), *Awaiting Review* (open review-queue count).
- **Client management (sidebar)** — scrollable list of all onboarded clients with tone/id metadata, an inline form to add or update a client (id, name, tone, email), and a one-click 
- **Navigation** — Dashboard / Doc‑to‑Data / Client Reminders / Advisory Reports.
- **Shared human review queue** — every demo, regardless of which one flags something, writes into one `ReviewQueueItem` table. Surfaced on the Dashboard with demo/type/ref/reason and a **Resolve** button.
- **Shared client roster & persistence** — one SQLite (swappable) database (`src/database/database.py`) backs all three demos: `clients`, `documents`, `journal_entries`, `expected_documents`, `reminder_logs`, `financial_statements`, `analysis_reports`, `review_queue`.
- **Latency tracking** — every pipeline run's wall-clock time is recorded in-process and shown on the Dashboard ("on-board inference latency, observed by this server") 
---

## Dashboard (Live Metrics)

The default landing page. Everything here is computed live from the actual DB, nothing simulated:

| Metric | What it shows |
|---|---|
| Documents processed | Total documents run through Demo 1 |
| Accounted without review | % that reached `ready_to_post` with zero human intervention |
| Avg. classification confidence | Mean confidence across all classified documents |
| Recurring suppliers learned | Suppliers seen ≥2 times per client (proof the system gets smarter per client) |
| On-board inference latency | Avg/last/sample-count per call type (`demo-1-process`, `demo-2-run`, `demo-3-generate`) |
| Open review-queue items | Full table (demo, type, ref id, reason) with inline **Resolve** action |

---

## Demo 1 — Sovereign Doc‑to‑Data

Drop in *any* accounting document — XML e‑invoice, native-text PDF, scanned/OCR'd PDF, plain text, or a photographed receipt — and get back a validated, bookable double-entry journal entry, entirely on-device.

### Document intake
- **Client selector** — every action is scoped to a chosen client.
- **File upload** — accepts PDF, PNG/JPG, XML, TXT via drag/drop-style uploader.
- **"Try a sample" buttons** — one-click buttons for each format the pipeline supports (XML e‑invoice, plain-text invoice, native PDF, scanned PDF/OCR path, plus a real photographed receipt if SROIE/CORD samples are downloaded) 
- **Existing documents table (per client)** — lists every document already registered for the selected client (pre-seeded via the roster generator or uploaded earlier), with file name, detected type, classification, and status badge (`uploaded` / `accounted` / `needs_review` / `failed`). Each row has either a **Run** button (unprocessed) or a **View** button (already processed, opens a read-only before/after view).

### The live pipeline (BEFORE / AFTER split view)
Once a document is run, the screen splits into two columns:

**BEFORE (left):**
- Rendered preview of the original file — actual image for photos, rasterized first page for PDFs, syntax-highlighted raw XML, or plain text.
- Raw OCR/extracted text (first ~4000 chars), scrollable.
- Any ingestion-time warnings (e.g. low OCR confidence per page).

**AFTER (right), stage by stage:**
1. **Classification** — document type badge (invoice / receipt / bank statement / financial statement / payroll / chart of accounts / other), confidence %, and method used (`heuristic` for XML/CSV — instant, free, no model call — or `model` for anything visually ambiguous).
2. **Extraction** — supplier, VAT/tax ID, date, currency, total, and full line-item table. XML e‑invoices are parsed directly and deterministically (no LLM call, ~0.98 confidence); everything else goes through the local SLM with grammar‑constrained JSON decoding (guarantees valid JSON every time) and a worked example in-prompt to stop it inventing placeholder values.
3. **Recognized recurring supplier badge** — if this client has seen this supplier before, shows "recognized supplier — seen Nx → [learned category]". This is the **auto-learning demo beat**: process the same supplier's second invoice and watch confidence/auto-categorization visibly improve.
4. **Validation** — pass/fail pill (✅ balanced & valid / ⚠ needs review / ✗ issues found) plus every individual issue (field, severity, human-readable message) — covers required-field checks, Italian VAT number format, VAT‑rate legality, subtotal+VAT=total reconciliation, line-item sum reconciliation, implausible-amount detection, and date-format sanity, all deterministic Python, not model-dependent.
5. **Journal entry (Dare / Avere)** — full double-entry table: one line per extracted line item (booked to its real Chart-of-Accounts category), a VAT-credit line, and a payables line to the supplier — plus Balanced/Not‑balanced and Ready‑to‑post/Pending‑review pills. Uncategorized items are never silently dropped — they're booked to a clearly labeled placeholder account and force review.

### Human‑in‑the‑loop review gate
- If the entry isn't clean, an **inline edit form** lets you correct supplier name, VAT, date, currency, total right in the UI, then **Recalculate with edits** re-runs validation/journal-building live.
- **Approve & Push** persists the document, journal entry, resolves any matching review-queue item, and — critically — **reinforces the supplier→account mapping** for next time (the learning loop closes here, not just on clean auto-processed documents).

### Chart of Accounts
- Backed by a synthetic-but-defensible Italian Chart of Accounts (`chart_of_accounts.json`), built on the real Civil Code statutory structure (Art. 2424/2425) with a granular keyword-driven categorization layer (CNDCEC/OIC-consistent) for line-item matching — first via free keyword match, only falling back to the local model when nothing matches.

---

## Demo 2 — Client Reminders & Document Collection

The agent knows what each client owes, spots the gaps automatically, drafts a personalized reminder in the client's own tone, sends it, and logs it — across an entire roster in one click.

Three tabs:

### Checklist tab
- Pick a client + a period (frequency selector: Monthly/Quarterly/Yearly, generating real period options rather than free text).
- Multi-select which document types are expected that period (bank statement, sales invoices, purchase invoices, payroll).
- **Seed checklist** button writes the expectation to memory.
- Live checklist view per client/period: each item shows **✓ Received** or **Missing**, with a **Mark received** button for manual overrides.

### Run reminders tab
- **Run for this client** — cross-checks that one client's checklist, and for anything missing, drafts + sends a reminder, then logs it. Result shows missing-document count, reminders-sent count, and every drafted message with its doc type and follow-up number.
- **Run for entire roster** — runs every client in one call and reports clients processed, total reminders sent, and **estimated hours saved** (calculated from a configurable manual-minutes-per-document assumption), plus a per-client breakdown table.
- **Tone-aware drafting** — each client has a `preferred_tone` (formal/friendly); the reminder text is generated in that voice, and escalates in wording on follow-up #2 vs #1.
- **Follow-up ceiling** — after a configurable max number of follow-ups with no response, the orchestrator stops sending and instead auto-escalates the item to the shared human review queue.
- Falls back gracefully to a template-based agent if no dedicated SLM-drafted `ReminderAgent` is registered yet — so the orchestration/memory path is always demoable even before that piece is finished.

### Dashboard & history tab
- **Collection dashboard** — per-client rollup table: expected / received / missing / reminders sent, for the selected period.
- **Reminder history** — full chronological log of every reminder ever sent to the selected client, with channel, follow-up number, status, and the actual message text.

---

## Demo 3 — Advisory Report + Alerts

A raw financial statement in, a plain-language, client-ready advisory letter with flagged anomalies out, in under a minute.

Three ways to feed it data:

### Existing Client mode
- Loads a client's already-stored fiscal years/periods as a table.
- Pick a period → **Generate advisory report** — automatically pulls the *prior* period from memory for period-over-period comparison (no need to hand it in manually).

### Upload Statements mode
- Upload a CSV (one row per fiscal year, a `period` column + any statement field as columns) → generates one report per row, in order, each correctly chained to the previous row as its "prior period."

### Manual Input mode
- Direct form entry of a single period's raw figures (revenue, COGS, opex, net income, current assets, inventory, current liabilities, total debt, equity, receivables) for any client/period combination.

### What every report contains
- **Ratio set** — gross margin %, net margin %, current ratio, quick ratio, DSO (days sales outstanding), debt/equity — shown as metric tiles with a delta vs. the prior period where available. All computed deterministically, not by the model.
- **Anomaly detection** (also deterministic) — flags:
  - Cash strain (current ratio below threshold) → **alert** severity
  - Declining gross/net margin vs. prior period beyond a configurable point-drop threshold → **warning**
  - Worsening DSO (collections slowing) → **warning**
  - Below-industry-benchmark margins (only if not already flagged vs. prior period, to avoid double-counting)
  - Reports with any alert-severity anomaly are auto-flagged into the shared review queue.
- **Adjustable industry benchmarks** — an expandable panel lets you override the default benchmark ratios per report.
- **Advisory letter** — 150–250 word, plain-language letter (no unexplained jargon), written by the local SLM by default, referencing only the real computed numbers and detected issues, ending in 1–3 concrete recommendations — with a deterministic template fallback if the model call fails, so this never blocks the demo. Downloadable as a `.txt` file per report.
- **Report history** — every previously generated report for the selected client, expandable, with alert/warning counts and the full stored letter text.

---

## Data & seeding tooling (behind the scenes)

- `generate_synthetic_samples.py` — generates 10 synthetic Italian invoices in every format the pipeline handles (XML, native PDF, scanned PDF, plain text), drawn from real Chart-of-Accounts keywords so categorization actually has something to match.
- `download_real_samples.py` — pulls real, messy, photographed receipts from public datasets (SROIE, CORD) to stress-test OCR/classification against real-world noise (angles, folds, lighting) that synthetic data can't fake.
- `generate_client_roster.py` — generates a full 50-client synthetic roster (Faker, `it_IT` locale) in well under a minute: 4 unprocessed documents per client (one of each format), a partially-received Demo 2 checklist, and 2–3 years of internally consistent, trajectory-driven (growing/flat/declining) financial statements per client
- `seed_demo_data.py` — quick 3-client seed that *does* run the full Demo 1 pipeline immediately, useful for a fast local smoke test.
- `accuracy_scorer.py` — measures real field-level extraction accuracy against SROIE ground-truth labels (supplier name, total, date) — the source of the honestly-reported ~60–75% accuracy figure on a hard, non-Italian benchmark, used to justify why the review-gate/validation layer exists at all.

---

## Architecture summary (for Q&A)

| Layer | Tech | Notes |
|---|---|---|
| Ingestion | PyMuPDF, pdfplumber, lxml, Tesseract OCR (via pytesseract), pandas | Per-page OCR fallback for mixed native/scanned PDFs |
| Classification | Heuristic first (XML/CSV structural signals), local SLM fallback | Confidence-gated review threshold |
| Extraction | Direct structured lookup for XML, local SLM (grammar-constrained JSON) otherwise | OCR VAT-prefix confusable correction, placeholder-value sanitization |
| Validation | Pure Python rule engine | VAT format, math reconciliation, plausibility, date parsing |
| Categorization | Keyword match first, local SLM fallback | Against synthetic-but-defensible Italian CoA |
| Accounting | Deterministic double-entry builder | Never drops a line item, always balances or flags |
| Memory/status | SQLAlchemy ORM over SQLite (swappable DATABASE_URL) | One schema, three demo "heads," one shared review queue |
| Local inference | `llama-cpp-python`, Qwen2.5‑1.5B‑Instruct GGUF, in-process | No daemon, no external calls, GPU-offloadable via config only |
| APIs | FastAPI, one consolidated router | Powers the React frontend; Streamlit calls orchestrators directly |
| UI | Streamlit (this doc) + React/Vite/TS SPA | Same orchestration layer underneath both |

