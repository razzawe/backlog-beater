import os
from datetime import UTC, datetime, timedelta
import jwt
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt.exceptions import InvalidTokenError
from pwdlib import PasswordHash
from pydantic import BaseModel, Field
from sqlmodel import Session, select

from app.database import User, get_session


SECRET_KEY = os.getenv("SECRET_KEY", "change-this-secret-before-production")
ALGORITHM = "HS256"

api_router = APIRouter(prefix="/auth", tags=["auth"])
bearer_scheme = HTTPBearer()
password_hash = PasswordHash.recommended()

class RegisterRequest(BaseModel):
    username: str = Field(min_length=3, max_length=50)
    password: str = Field(min_length=8, max_length=128)


class LoginRequest(BaseModel):
    username: str
    password: str


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"


class UserPublic(BaseModel):
    id: int
    username: str
    steam_id: str | None = None  # Null until a Steam account is linked.


def create_access_token(user_id: int) -> str:
    """Create a signed JWT identifying the given user.
    Set the token to expire one hour after it is issued."""
    expires_at = datetime.now(UTC) + timedelta(hours=1)
    return jwt.encode(
        {"sub": str(user_id), "exp": expires_at},
        SECRET_KEY,
        algorithm=ALGORITHM,
    )


def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
    session: Session = Depends(get_session),
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
        )
        user_id = int(payload["sub"])
    except (InvalidTokenError, KeyError, ValueError):
        raise credentials_error

    user = session.get(User, user_id)
    if user is None:
        raise credentials_error

    return user


@api_router.post("/register", response_model=UserPublic, status_code=201)
def register(
    data: RegisterRequest,
    session: Session = Depends(get_session),
) -> User:
    """Register a user with a securely hashed password.
    Reject duplicate usernames and return the persisted account."""
    existing_user = session.exec(
        select(User).where(User.username == data.username)
    ).first()
    if existing_user:
        raise HTTPException(status_code=409, detail="Username already exists")

    user = User(
        username=data.username,
        hashed_password=password_hash.hash(data.password),
    )
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


@api_router.post("/login", response_model=Token)
def login(
    data: LoginRequest,
    session: Session = Depends(get_session),
) -> Token:
    """Authenticate a username and password and issue an access token.
    Reject unknown users or incorrect passwords with an authentication error."""
    user = session.exec(
        select(User).where(User.username == data.username)
    ).first()

    if user is None or not password_hash.verify(
        data.password,
        user.hashed_password,
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
        )

    if user.id is None:
        raise HTTPException(status_code=500, detail="User has no database ID")

    return Token(access_token=create_access_token(user.id))


@api_router.get("/me", response_model=UserPublic)
def read_current_user(
    current_user: User = Depends(get_current_user),
) -> User:
    """Return the account associated with the authenticated request.
    Expose public user fields through the endpoint response model."""
    return current_user
