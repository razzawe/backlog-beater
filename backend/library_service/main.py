from fastapi import FastAPI

from library_service.games import games_router


app = FastAPI(title="Backlog Beater Library Service")
app.include_router(games_router, prefix="/api/v1")


@app.get("/internal/health", include_in_schema=False)
def health():
    return {"status": "ok"}
