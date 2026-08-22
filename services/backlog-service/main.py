from fastapi import FastAPI, HTTPException, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.middleware.cors import CORSMiddleware
from jose import jwt, JWTError
from typing import List
from models import BacklogItem, BacklogItemResponse, SteamImportRequest
import psycopg2
import psycopg2.extras
from psycopg2.extras import execute_values
from datetime import datetime
import os
app = FastAPI(title="Backlog Service")

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv("CORS_ORIGINS", "http://localhost:3000").split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

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

security = HTTPBearer()

SECRET_KEY = os.getenv("JWT_SECRET", "changethislater")
ALGORITHM = "HS256"
def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)):
    try:
        payload = jwt.decode(credentials.credentials, SECRET_KEY, algorithms=[ALGORITHM])
        user_id = int(payload.get("sub"))
        return user_id
    except (JWTError, TypeError, ValueError):
        raise HTTPException(status_code=401, detail="Invalid token")


@app.get("/health")
def health():
    return {"status": "ok", "service": "backlog-service"}


@app.post("/backlog/import")
def import_steam_library(body: SteamImportRequest, user_id: int = Depends(get_current_user)):
    conn = get_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        values = []
        for game in body.games:
            status = "in_progress" if game.hours_played > 0 else "not_started"
            last_interacted = game.last_interacted_at or datetime.utcnow()
            values.append((
                user_id,
                game.game_id,
                status,
                game.hours_played,
                game.progress_percent,
                last_interacted
            ))

        execute_values(
            cur,
            """
            INSERT INTO backlog_items
            (user_id, game_id, status, hours_played, progress_percent, last_interacted_at)
            VALUES %s
            ON CONFLICT (user_id, game_id) DO UPDATE SET
                hours_played = EXCLUDED.hours_played,
                status = CASE
                    WHEN backlog_items.status = 'completed' THEN backlog_items.status
                    ELSE EXCLUDED.status
                END,
                last_interacted_at = EXCLUDED.last_interacted_at
            """,
            values
        )
        conn.commit()
        return {"message": f"{len(values)} games imported successfully", "imported_count": len(values)}
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cur.close()
        conn.close()

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
        cur.execute("""
            SELECT 
                bi.id,
                bi.user_id,
                bi.game_id,
                bi.status,
                bi.hours_played,
                bi.progress_percent,
                bi.last_interacted_at,
                bi.added_at,
                g.title,
                g.cover_url,
                g.genres,
                g.completion_source,
                g.main_story_hours,
                g.main_plus_extras_hours,
                g.completionist_hours,
                g.completion_match_confidence
            FROM backlog_items bi
            JOIN games g ON bi.game_id = g.id
            WHERE bi.game_id = %s AND bi.user_id = %s
        """, (game_id, user_id))
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
@app.get("/backlog", response_model=List[BacklogItemResponse])
def get_backlog(user_id: int = Depends(get_current_user)):
    conn = get_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    try: 
        cur.execute("""
            SELECT 
                bi.id,
                bi.user_id,
                bi.game_id,
                bi.status,
                bi.hours_played,
                bi.progress_percent,
                bi.last_interacted_at,
                bi.added_at,
                g.title,
                g.cover_url,
                g.genres,
                g.completion_source,
                g.main_story_hours,
                g.main_plus_extras_hours,
                g.completionist_hours,
                g.completion_match_confidence
            FROM backlog_items bi
            JOIN games g ON bi.game_id = g.id
            WHERE bi.user_id = %s ORDER BY last_interacted_at DESC
        """, (user_id,))
        res = cur.fetchall()
        return res
    
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cur.close()
        conn.close()
    
