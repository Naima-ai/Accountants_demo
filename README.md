# Private Edge Agents for Accountants — Demo Build

On-prem/edge build for the Loop AI Labs × TeamSystem demo brief: three
agentic demos (Sovereign Doc-to-Data, Reminder Agent, Advisory Report)
sharing one stack, with a local SLM doing all inference on-machine — no
data ever leaves the firm.

## Stack

- **Backend**: FastAPI, one consolidated API at `src/api/api.py` (mounted by `src/main.py`)
- **Local SLM**: in-process `llama-cpp-python` (`src/llm/slm_client.py`) — no external daemon
- **Database**: SQLite by default (`src/database/database.py`), swappable via `DATABASE_URL`
- **Frontend**: Vite + React + TypeScript SPA in `frontend/`

## Backend setup

```bash
pip install -r requirements.txt
```

`llama-cpp-python` doesn't ship a PyPI wheel for every platform/Python
combo (confirmed: it fails building from source on Windows/Python 3.12).
If `pip install -r requirements.txt` fails on that package, install it
separately first from the project's prebuilt CPU wheel index, then
re-run the line above for everything else:

```bash
pip install llama-cpp-python --prefer-binary --extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cpu
```

Run the API:

```bash
uvicorn src.main:app --reload
```

The first request (or app startup) downloads the local model once
(~1GB, `Qwen2.5-1.5B-Instruct-GGUF` by default, cached under
`var/models/`) — see `SLM_*` settings in `src/config.py` to point at a
bigger quant or offload to an on-prem GPU (`SLM_N_GPU_LAYERS`).
Swagger UI is at `http://localhost:8000/docs`.

Optional demo data:

```bash
python src/data/generate_synthetic_samples.py   # synthetic Italian invoices
python src/data/download_real_samples.py        # real SROIE/CORD receipt images (network)
python src/database/seed_demo_data.py           # runs everything through the pipeline into the DB
```

## Frontend setup

```bash
cd frontend
npm install
npm run dev
```

Opens on `http://localhost:5173` and proxies `/api` to the backend on
port 8000 (`vite.config.ts`) — run the backend first. For the actual
on-prem demo, build static files and let FastAPI serve them from one
process/port:

```bash
cd frontend && npm run build
uvicorn src.main:app   # now also serves frontend/dist/ at http://localhost:8000
```

## Layout

```
src/
  api/            single FastAPI router — all 3 demos + shared routes
  orchestration/  one orchestrator per demo, chains the pipeline stages
  classifier/, extraction/, validation/, agents/   Demo 1 pipeline stages
  llm/            slm_client.py — in-process local model, shared by all demos
  memory/, database/  shared persistence layer
  config.py       every tunable setting (SLM, thresholds, paths)
frontend/         Vite + React UI (dashboard, and one page per demo)
data_set/         synthetic + public sample documents used by the demos
```

`demo_1/` is a pre-migration legacy folder, superseded by `src/`.
