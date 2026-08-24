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
