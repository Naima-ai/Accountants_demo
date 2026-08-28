import logging
import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from src.api.api import router
from src.database.database import init_db

logger = logging.getLogger("main")
logging.basicConfig(level=logging.INFO)

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_FRONTEND_DIST = os.path.join(_REPO_ROOT, "frontend", "dist")

app = FastAPI(
    title="AI Accounting Demo",
    version="0.1.0",
    description="AI-powered accounting workflow API",
)

# Allows the Vite dev server (npm run dev, default port 5173) to call
# this API during frontend development. In production the built
# frontend/dist/ is served from this same origin (mounted below), so
# CORS isn't exercised there at all.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router)


@app.on_event("startup")
def on_startup() -> None:
    # Creates the SQLite schema (clients, documents, journal entries,
    # reminders, reports, review queue, ...) on first run; a no-op if
    # the tables already exist.
    init_db()

    # Loads the local SLM into memory now, once, so the first real
    # request doesn't pay the (one-time, first-run-only-if-not-cached)
    # model download + load cost. Never blocks startup on failure --
    # classify/extract/validate calls fall back to needs_review=True
    # per-document if the model genuinely can't load.
    from src.llm.slm_client import warm_up
    warm_up()


# Serves the built frontend (npm run build -> frontend/dist/) from the
# same process/port as the API, if present -- the "one shared stack"
# deployment shape for the actual on-prem demo. During development,
# run `npm run dev` in frontend/ instead and hit the Vite dev server directly.
if os.path.isdir(_FRONTEND_DIST):
    app.mount("/", StaticFiles(directory=_FRONTEND_DIST, html=True), name="frontend")
else:
    logger.info(f"No built frontend found at {_FRONTEND_DIST} -- API-only mode. Run `npm run build` in frontend/ to serve it here.")
