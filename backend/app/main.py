from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.games import games_router
from app.api.main import api_router
from app.api.steam import steam_router
from app.database import create_db_and_tables


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize database tables when the API starts.
    Yield control for the duration of the application lifespan."""
    create_db_and_tables()
    yield


app = FastAPI(title="Backlog Beater API", lifespan=lifespan)

app.include_router(api_router, prefix="/api/v1")
app.include_router(steam_router, prefix="/api/v1")

# Serve the authenticated user's library through the main API.
app.include_router(games_router, prefix="/api/v1")
