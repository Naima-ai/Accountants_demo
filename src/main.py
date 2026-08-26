from fastapi import FastAPI

from .api.api import router


app = FastAPI(
    title="Accountants Demo API",
    version="0.1.0",
    description="accounting workflow API",
)

app.include_router(router)


@app.get("/health")
async def health_check():
    return {
        "status": "ok",
        "service": "accountants-demo",
    }