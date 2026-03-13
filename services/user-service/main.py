from fastapi import FastAPI, HTTPException, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel, EmailStr
from jose import jwt, JWTError
import psycopg2
import psycopg2.extras
import os
import bcrypt
from dotenv import load_dotenv
from datetime import datetime, timedelta

load_dotenv()

app = FastAPI(title="User Service")

# Security
security = HTTPBearer()

# DB connection


def get_db():
    conn = psycopg2.connect(
        host=os.getenv("POSTGRES_HOST", "postgres"),
        port=os.getenv("POSTGRES_PORT", "5432"),
        database=os.getenv("POSTGRES_DB", "backlog_db"),
        user=os.getenv("POSTGRES_USER", "backlog_user"),
        password=os.getenv("POSTGRES_PASSWORD", "backlogpassword123")
    )
    return conn

# JWT


SECRET_KEY = os.getenv("JWT_SECRET", "changethislater")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRY_TIME = 60 * 24 * 7  # 7 days
def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()

def verify_password(password: str, hashed: str) -> bool:
    return bcrypt.checkpw(password.encode(), hashed.encode())

def create_token(user_id: int):
    payload = {
        "sub": str(user_id),
        "exp": datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRY_TIME)
    }
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)):
    try:
        payload = jwt.decode(credentials.credentials, SECRET_KEY, algorithms=[ALGORITHM])
        user_id = int(payload.get("sub"))
        return user_id
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid token")

# Models


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class SteamLinkRequest(BaseModel):
    steam_id: str

# Routes


@app.get("/health")
def health():
    return {"status": "ok", "service": "user-service"}


@app.post("/register")
def register(body: RegisterRequest):
    conn = get_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        cur.execute("SELECT id FROM users WHERE email = %s", (body.email,))
        if cur.fetchone():
            raise HTTPException(status_code=400, detail="Email already registered")
        
        password_hash = hash_password(body.password)
        cur.execute(
            "INSERT INTO users (email, password_hash) VALUES (%s, %s) RETURNING id",
            (body.email, password_hash)
        )
        user_id = cur.fetchone()["id"]
        conn.commit()
        token = create_token(user_id)
        return {"token": token, "user_id": user_id}
    finally:
        cur.close()
        conn.close()


@app.post("/login")
def login(body: LoginRequest):
    conn = get_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        cur.execute("SELECT id, password_hash FROM users WHERE email = %s", (body.email,))
        user = cur.fetchone()
        if not user or not verify_password(body.password, user["password_hash"]):
            raise HTTPException(status_code=401, detail="Invalid credentials")
        
        token = create_token(user["id"])
        return {"token": token, "user_id": user["id"]}
    finally:
        cur.close()
        conn.close()


@app.post("/link-steam")
def link_steam(body: SteamLinkRequest, user_id: int = Depends(get_current_user)):
    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute(
            "UPDATE users SET steam_id = %s WHERE id = %s",
            (body.steam_id, user_id)
        )
        conn.commit()
        return {"message": "Steam account linked successfully"}
    finally:
        cur.close()
        conn.close()


@app.get("/me")
def get_me(user_id: int = Depends(get_current_user)):
    conn = get_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        cur.execute("SELECT id, email, steam_id, created_at FROM users WHERE id = %s", (user_id,))
        user = cur.fetchone()
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        return user
    finally:
        cur.close()
        conn.close()