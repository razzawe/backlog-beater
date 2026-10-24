from fastapi import FastAPI

from library_service.games import games_router
from library_service.queue import queue_router


app = FastAPI(title="Backlog Beater Library Service")
app.include_router(queue_router, prefix="/api/v1")
app.include_router(games_router, prefix="/api/v1")


@app.get("/internal/health", include_in_schema=False)
def health():
    return {"status": "ok"}
