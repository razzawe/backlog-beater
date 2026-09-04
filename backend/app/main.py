import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.auth.router import router as auth_router
from app.backlog.router import router as backlog_router
from app.integrations.router import router as integrations_router


def create_app() -> FastAPI:
    app = FastAPI(title="Backlog Beater API", version="2.0.0")

    app.add_middleware(
        CORSMiddleware,
        allow_origins=os.getenv("CORS_ORIGINS", "http://localhost:3000").split(","),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(auth_router)
    app.include_router(backlog_router)
    app.include_router(integrations_router)

    @app.get("/health", tags=["system"])
    def health() -> dict[str, str]:
        return {"status": "ok", "architecture": "modular-monolith"}

    return app


app = create_app()
