import os

import jwt
from fastapi import HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials
from jwt.exceptions import InvalidTokenError
from sqlmodel import Session

from app.database import User


SECRET_KEY = os.getenv("SECRET_KEY", "")
if not SECRET_KEY or SECRET_KEY == "change-this-secret-before-production":
    raise RuntimeError("Set SECRET_KEY for the auth service; the development default is not allowed")
ALGORITHM = "HS256"


def get_current_user(
    credentials: HTTPAuthorizationCredentials,
    session: Session,
) -> User:
    """Resolve the authenticated user from a bearer JWT.
    Reject invalid or expired tokens and users that no longer exist."""
    credentials_error = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or expired token",
    )

    try:
        payload = jwt.decode(
            credentials.credentials,
            SECRET_KEY,
            algorithms=[ALGORITHM],
            options={"require": ["sub", "exp"]},
        )
        user_id = int(payload["sub"])
        if not 0 < user_id <= 2147483647:
            raise ValueError("Invalid user ID")
    except (InvalidTokenError, KeyError, ValueError, TypeError):
        raise credentials_error

    user = session.get(User, user_id)
    if user is None:
        raise credentials_error

    return user
