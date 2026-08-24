from fastapi import FastAPI

from src.api.api import router
from src.api.reporting_api import router as reporting_router
from src.api.test_api import router as test_router
from src.database.database import init_db


app = FastAPI(
    title="AI Accounting Demo",
    version="0.1.0",
    description="AI-powered accounting workflow API",
)

app.include_router(router)
app.include_router(reporting_router)
app.include_router(test_router)


@app.on_event("startup")
def on_startup() -> None:
    # Creates the SQLite schema (clients, documents, journal entries,
    # reminders, reports, review queue, ...) on first run; a no-op if
    # the tables already exist.
    init_db()