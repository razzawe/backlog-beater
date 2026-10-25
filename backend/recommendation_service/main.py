from fastapi import FastAPI
from .api import router

app = FastAPI(title="Backlog Beater Recommendation Service")
app.include_router(router, prefix="/api/v1")


@app.get("/internal/health", include_in_schema=False)
def health():
    return {"status": "ok"}
