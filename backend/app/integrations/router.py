"""External integration HTTP routes."""

from fastapi import APIRouter


router = APIRouter(prefix="/integrations", tags=["integrations"])


@router.get("/health")
def integrations_health() -> dict[str, str]:
    return {"status": "ok", "module": "integrations"}
