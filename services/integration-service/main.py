import token

from fastapi import FastAPI, HTTPException, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import jwt, JWTError
import httpx
import redis
import psycopg2
import psycopg2.extras
import os
import json
from dotenv import load_dotenv

load_dotenv()
app = FastAPI(title="Integration Service")
BACKLOG_SERVICE_URL = os.getenv("BACKLOG_SERVICE_URL", "localhost:8002")

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

# Initialize Redis client
def get_redis():
    return redis.Redis(
        host=os.getenv("REDIS_HOST", "redis"),
        port=int(os.getenv("REDIS_PORT", 6379)),
        decode_responses=True
    )

conn = get_db()
cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
# API keys
STEAM_API_KEY = os.getenv("STEAM_API_KEY")
RAWG_API_KEY = os.getenv("RAWG_API_KEY")

# Helper function to get game details by steam_appid
# def get_game_by_steam_appid(conn, steam_appid: str):
#     with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
#         cur.execute(
#             "SELECT * FROM games WHERE steam_appid = %s",
#             (steam_appid,)
#         )
#         return cur.fetchone()




# Steam API endpoint to get user's game library
@app.get("/steam/library/{steam_id}")
async def get_steam_library(steam_id: str, credentials: HTTPAuthorizationCredentials = Depends(security)):
    if not STEAM_API_KEY:
        raise HTTPException(status_code=500, detail="Steam API key not configured")
    
    url = "http://api.steampowered.com/IPlayerService/GetOwnedGames/v0001/"
   
    params = {
        "key": STEAM_API_KEY,
        "steamid": steam_id,
        "include_appinfo": True,
        "include_played_free_games": True,
        "format": "json"
    }
    async with httpx.AsyncClient() as client:
        response = await client.get(url, params=params)
        if response.status_code != 200:
            raise HTTPException(status_code=response.status_code, detail=f"Steam API error: {response.text}")
        data = response.json()

    games = data.get("response", {}).get("games", [])
    if not games:
        raise HTTPException(status_code=404, detail="No games found or profile is private")




    return {"steam_id": steam_id, "game_count": len(games), "games": games}


#RAWG API endpoint to search for games
@app.get("/games/search")
async def search_game(q: str):
    if not RAWG_API_KEY:
        raise HTTPException(status_code=500, detail="RAWG API key not configured")

    async with httpx.AsyncClient() as client:
        response = await client.get(
            "https://api.rawg.io/api/games",
            params={"key": RAWG_API_KEY, "search": q, "page_size": 5}
        )
        data = response.json()

    results = data.get("results", [])
    return [
        {
            "rawg_id": str(g["id"]),
            "title": g["name"],
            "cover_url": g.get("background_image"),
            "genres": [genre["name"] for genre in g.get("genres", [])],
            "estimated_playtime": g.get("playtime"),
            "metacritic_score": g.get("metacritic")
        }
        for g in results
    ]
