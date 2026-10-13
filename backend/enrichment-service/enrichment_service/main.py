from fastapi import FastAPI

from .api import review_router


app = FastAPI(title="Backlog Beater Enrichment Service")
app.include_router(review_router, prefix="/api/v1")


@app.get("/internal/health", include_in_schema=False)
def health():
    return {"status": "ok"}
