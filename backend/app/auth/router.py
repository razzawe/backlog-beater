import os
from datetime import datetime, timedelta, timezone


import bcrypt
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from psycopg2.errors import UniqueViolation
from psycopg2.extras import RealDictCursor

from app.auth.schemas import RegisterRequest, TokenResponse, UserResponse, LoginRequest
from app.db import get_connection

router = APIRouter(prefix="/auth", tags=["auth"])
bearer_scheme = HTTPBearer()

JWT_SECRET = os.getenv("JWT_SECRET", "changethislater")
JWT_ALGORITHM = "HS256"

# JWT utility function to create access token
def create_access_token(user_id: int) -> str:
    payload = {
        "sub": str(user_id),
        "exp": datetime.now(timezone.utc) + timedelta(days=7),
    }

    return jwt.encode(
        payload,
        JWT_SECRET,
        algorithm=JWT_ALGORITHM,
    )


def get_current_user_id(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
) -> int:
    try:
        payload = jwt.decode(
            credentials.credentials,
            JWT_SECRET,
            algorithms=[JWT_ALGORITHM],
        )
        return int(payload["sub"])
    except (JWTError, KeyError, TypeError, ValueError):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired access token",
        )

# Password hashing utility function (bcrypt adds a salt to each password)
def hash_password(password: str) -> str:
    password_bytes = password.encode("utf-8")
    hashed_bytes = bcrypt.hashpw(password_bytes, bcrypt.gensalt())
    return hashed_bytes.decode("utf-8")

def verify_password(password: str, password_hash: str) -> bool:
    return bcrypt.checkpw(
        password.encode("utf-8"),
        password_hash.encode("utf-8"),
    )

@router.post(
    "/register",
    response_model=UserResponse,
    status_code=status.HTTP_201_CREATED,
)
def register(body: RegisterRequest) -> UserResponse:
    
    connection = get_connection()
    cursor = connection.cursor(cursor_factory=RealDictCursor)

    try:
        cursor.execute(
            """
            INSERT INTO users (email, password_hash)
            VALUES (%s, %s)
            RETURNING id, email
            """,
            (str(body.email).lower(), hash_password(body.password)),
        )
        user = cursor.fetchone()
        connection.commit()
        return UserResponse(**user) # converts dict of user to arguments for UserResponse
    except UniqueViolation:
        connection.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Email already registered",
        )
    finally:
        cursor.close()
        connection.close()

@router.post("/login", response_model=TokenResponse)
def login(body: LoginRequest) -> TokenResponse:
    connection = get_connection()
    cursor = connection.cursor(cursor_factory=RealDictCursor)

    try:
        cursor.execute(
            """
            SELECT id, password_hash
            FROM users
            WHERE email = %s
            """,
            (str(body.email).lower(),),
        )

        user = cursor.fetchone()

        if not user or not verify_password(
            body.password,
            user["password_hash"],
        ):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid email or password",
            )

        token = create_access_token(user["id"])

        return TokenResponse(access_token=token)

    finally:
        cursor.close()
        connection.close()


@router.get("/me", response_model=UserResponse)
def get_me(user_id: int = Depends(get_current_user_id)) -> UserResponse:
    connection = get_connection()
    cursor = connection.cursor(cursor_factory=RealDictCursor)

    try:
        cursor.execute(
            "SELECT id, email FROM users WHERE id = %s",
            (user_id,),
        )
        user = cursor.fetchone()

        if not user:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User not found",
            )

        return UserResponse(**user)
    finally:
        cursor.close()
        connection.close()


@router.get("/health")
def auth_health() -> dict[str, str]:
    return {"status": "ok", "module": "auth"}
