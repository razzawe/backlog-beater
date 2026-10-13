from urllib.parse import urlencode

from fastapi import FastAPI, Request
from fastapi.exception_handlers import http_exception_handler
from fastapi.responses import RedirectResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.lifespan import lifespan
from auth_service.api import api_router
from auth_service.internal import internal_router
from auth_service.steam import steam_router


app = FastAPI(title="Backlog Beater Auth Service", lifespan=lifespan)
app.include_router(api_router, prefix="/api/v1")
app.include_router(steam_router, prefix="/api/v1")
app.include_router(internal_router)


@app.exception_handler(StarletteHTTPException)
async def handle_http_error(request: Request, error: StarletteHTTPException):
    """Return browser callbacks to the UI, preserving API error responses."""
    if request.url.path == "/api/v1/steam/callback" and "text/html" in request.headers.get("accept", ""):
        query = urlencode({"steam": "error", "message": str(error.detail)})
        return RedirectResponse(f"/?{query}", status_code=303, headers={"Cache-Control": "no-store"})
    return await http_exception_handler(request, error)


@app.get("/internal/health", include_in_schema=False)
def health():
    return {"status": "ok"}
