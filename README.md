# Accountants_demo

Three agentic AI demos for the TeamSystem partnership: on-prem AI accounting workflow
with local SLM inference, modular agents, database memory, and (in an actual pilot)
TeamSystem API integration. See `demo_1/README.md` for the Demo 1 pipeline specifically.

## Shared stack (`src/`)

The database, memory/status tracking, and orchestration layer that ties all three
demos together, plus the FastAPI app exposing them over HTTP.

- `src/config.py` -- central settings (DB, Ollama, review thresholds, per-demo tuning)
- `src/database/database.py` -- SQLAlchemy schema + session handling (local SQLite by default)
- `src/memory/memory.py` -- supplier-learning, client document status, and review-queue memory
- `src/orchestration/demo1_orchestrator.py` -- Demo 1: ingest -> classify -> extract -> validate -> account
- `src/orchestration/demo2_orchestrator.py` -- Demo 2: missing-document detection -> reminders -> dashboard status
- `src/orchestration/demo3_orchestrator.py` -- Demo 3: financial analysis -> advisory report
- `src/api/api.py`, `src/api/reporting_api.py` -- HTTP endpoints for Demo 1 / Demo 3
- `src/main.py` -- FastAPI app entrypoint

### Setup

```bash
pip install -r requirements.txt
```

Same external dependencies as Demo 1 (see `demo_1/README.md`): Ollama running locally
with a pulled model, and Tesseract OCR as a system binary, for anything that touches
`demo_1/`'s ingestion/classification/extraction path.

The database defaults to a local SQLite file at `var/accountants_demo.db`, created
automatically on first run -- no external DB service needed. Override with the
`DATABASE_URL` env var for Postgres/MySQL.

### Running the API

From the repo root:

```bash
uvicorn src.main:app --reload
```

- `POST /api/demo-1/process` -- Demo 1 document processing (currently a mocked response;
  wiring this to `src/orchestration/demo1_orchestrator.py` is in progress)
- `POST /api/demo-3/generate` -- Demo 3 advisory report generation (fully wired)
- `GET /api/demo-3/reports/{client_id}` -- previously generated reports for a client
- Interactive docs at `/docs`

### Testing each orchestrator directly

Every module under `src/database/`, `src/memory/`, `src/orchestration/`, and
`demo_3/report_agent.py` has a self-test at the bottom (`if __name__ == "__main__":`)
that runs against an in-memory SQLite DB with synthetic data -- no server or real
client data needed:

```bash
python src/database/database.py
python src/memory/memory.py
python src/orchestration/demo1_orchestrator.py   # runs the real XML sample end-to-end
python src/orchestration/demo2_orchestrator.py   # simulates a 3-client reminder roster
python src/orchestration/demo3_orchestrator.py   # Q1 -> Q2 report with auto period comparison
python demo_3/report_agent.py                    # ratios + anomaly detection + advisory letter
```

`demo1_orchestrator.py`'s self-test exercises the real Demo 1 pipeline (Meet's
classifier/extractor/validator/accounting_agent + `ingestion.py`) against
`data_set/IT01234567890_FPR01.xml` and checks the entry comes out balanced and
`ready_to_post`, and that the supplier is remembered on a second run.

### Seeding a full demo database

For an actual demo/testing pass (not just self-tests), `src/database/seed_demo_data.py`
runs 10 real documents in **every format `ingestion.py` supports** through the full
Demo 1 pipeline and persists the results into the real database (`var/accountants_demo.db`,
not an in-memory throwaway):

```bash
python data_set/generate_synthetic_samples.py   # 10 each: XML, native PDF, scanned PDF, plain text
python data_set/download_real_samples.py        # 10 real CORD receipts (network + `datasets` package)
python src/database/seed_demo_data.py            # runs all of it through Demo1Orchestrator, seeds the DB
```

- **Synthetic** (`data_set/generate_synthetic_samples.py`): 10 FatturaPA XML e-invoices,
  10 native-text PDFs, 10 image-only "scanned" PDFs (forces the OCR path), 10 plain-text
  invoices -- suppliers/line items are drawn from `chart_of_accounts.json`'s own keyword
  lists so categorization has something real to match against.
- **Real** (`data_set/download_real_samples.py`, bumped to 10 samples): CORD receipts
  download fine. **`darentang/sroie` currently fails** under `datasets>=3.0`
  ("Dataset scripts are no longer supported" -- an upstream Hugging Face incompatibility,
  not something in this repo) -- the 3 SROIE samples already committed in
  `data_set/samples/images/` are what's available until that's resolved (an older
  `datasets` pin, or a different SROIE mirror, would fix it if more are needed).
- The seed script assigns documents round-robin across a small demo client roster,
  runs the full ingest -> classify -> extract -> validate -> account chain, and prints a
  per-format summary (ready_to_post / needs_review / errors), same shape as
  `demo_1/accuracy_scorer.py`.
- **XML documents don't need Ollama** (fully structured, deterministic) and should
  always come back `ready_to_post`. Everything else (PDF/image/text) needs the local
  SLM for classification/extraction -- without Ollama running, those land in
  `needs_review` instead of failing, which is correct graceful-degradation behavior,
  not a bug. Run `ollama pull qwen2.5:3b-instruct` and start Ollama first for full
  accuracy on the non-XML formats.
