from fastapi import FastAPI

from src.api.api import router


app = FastAPI(
    title="AI Accounting Demo",
    version="0.1.0",
    description="AI-powered accounting workflow API",
)

app.include_router(router)