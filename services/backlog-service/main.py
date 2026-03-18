from fastapi import FastAPI, HTTPException, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import jwt, JWTError
from typing import List
from models import BacklogItem, BacklogItemResponse
import psycopg2
import psycopg2.extras
import os
app = FastAPI(title="Backlog Service")

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

# Authentication (TEMP)
security = HTTPBearer()

SECRET_KEY = os.getenv("JWT_SECRET", "changethislater")
ALGORITHM = "HS256"
def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)):
    try:
        payload = jwt.decode(credentials.credentials, SECRET_KEY, algorithms=[ALGORITHM])
        user_id = int(payload.get("sub"))
        return user_id
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid token")
    

# Single Item Backlog Insert:
@app.post("/backlog") 
def post_backlog(backlog_item: BacklogItem, user_id: int = Depends(get_current_user)):
    conn = get_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        # check game exists 
        cur.execute("SELECT id FROM games WHERE id = %s", (backlog_item.game_id,))
        if not cur.fetchone():
            raise HTTPException(status_code=404, detail="Game not found")
        
        # insert backlog item from user
        cur.execute(
            """ INSERT INTO backlog_items (user_id, game_id, status, hours_played, progress_percent)
                VALUES (%s, %s, %s, %s, %s)
                RETURNING *
            """,
            (user_id, backlog_item.game_id, backlog_item.status, backlog_item.hours_played, backlog_item.progress_percent)
            )
        res = cur.fetchone()
        conn.commit()
        return res
    except HTTPException:
        raise
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cur.close()
        conn.close()
    

# Single Item Backlog Get:
@app.get("/backlog/{game_id}", response_model=BacklogItemResponse)
def get_backlog_item(game_id: int, user_id: int = Depends(get_current_user)):
    conn = get_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    try: 
        cur.execute("SELECT * FROM backlog_items WHERE game_id = %s AND user_id = %s", (game_id, user_id))
        res = cur.fetchone()
        if not res: #check if game exists in backlog
            raise HTTPException(status_code=404, detail="Game not found in backlog")
        return res
    
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cur.close()
        conn.close()
    

# All Item Backlog Get:
@app.get("/backlog/", response_model=List[BacklogItemResponse])
def get_backlog(user_id: int = Depends(get_current_user)):
    conn = get_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    try: 
        cur.execute("SELECT * FROM backlog_items WHERE user_id = %s ORDER BY last_interacted_at DESC", (user_id,))
        res = cur.fetchall()
        return res
    
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cur.close()
        conn.close()
    
