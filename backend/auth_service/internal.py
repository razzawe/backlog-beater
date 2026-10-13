"""Authentication subrequest endpoint for the Nginx gateway."""

from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlmodel import Session

from app.core.security import get_current_user
from app.database import User, get_session


internal_router = APIRouter(prefix="/internal/auth", include_in_schema=False)
gateway_bearer = HTTPBearer(auto_error=False)


def authenticate_gateway_request(
    credentials: HTTPAuthorizationCredentials | None = Depends(gateway_bearer),
    session: Session = Depends(get_session),
) -> User:
    # Return consistent 401 responses for Nginx, including missing credentials.
    if credentials is None:
        raise HTTPException(
            status_code=401, detail="Bearer token required",
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        return get_current_user(credentials=credentials, session=session)
    except HTTPException as error:
        if error.status_code == 401:
            error.headers = {"WWW-Authenticate": "Bearer"}
        raise


@internal_router.get("/check", status_code=204)
def check_authentication(
    user: User = Depends(authenticate_gateway_request),
) -> Response:
    """Return the token's verified database user ID for Nginx to forward."""
    return Response(
        status_code=204,
        headers={"X-User-ID": str(user.id), "Cache-Control": "no-store"},
    )
