from fastapi import FastAPI, HTTPException, Depends
from jose import jwt, JWTError
import psycopg2
import os
app = FastAPI(title="Backlog Service")
@app.get("/backlog")


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

def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)):
    try:
        payload = jwt.decode(credentials.credentials, SECRET_KEY, algorithms=[ALGORITHM])
        user_id = int(payload.get("sub"))
        return user_id
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid token")
    
    