"""Backlog HTTP routes."""

from fastapi import APIRouter


router = APIRouter(prefix="/backlog", tags=["backlog"])


@router.get("/health")
def backlog_health() -> dict[str, str]:
    return {"status": "ok", "module": "backlog"}
