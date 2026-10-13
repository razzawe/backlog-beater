from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.database import create_db_and_tables


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Create missing tables until a versioned migration workflow is introduced."""
    create_db_and_tables()
    yield
