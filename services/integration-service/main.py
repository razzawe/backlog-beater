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
BACKLOG_SERVICE_URL = os.getenv("BACKLOG_SERVICE_URL", "http://backlog-backlog-service:8000")

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


# API keys
STEAM_API_KEY = os.getenv("STEAM_API_KEY")
RAWG_API_KEY = os.getenv("RAWG_API_KEY")

# Helper function to get game metadata from RAWG and cache it
async def get_or_populate_game(steam_appid: str, game_name: str):
    # STEP 1: check cache first
    r = get_redis()
    cached = r.get(f"game:{steam_appid}")
    if cached:
        return json.loads(cached)
    
    # STEP 2: if not in cache, get games table first
    conn = get_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        cur.execute("SELECT * FROM games WHERE steam_appid = %s", (steam_appid,))
        game = cur.fetchone()
        if game:
            r.setex(f"game:{steam_appid}", 3600, json.dumps(dict(game), default=str))
            return dict(game)
    finally:
        cur.close()
        conn.close()

    # STEP 3: if not in cache, call RAWG API:
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.get(
            "https://api.rawg.io/api/games",
            params={"key": RAWG_API_KEY, "search": game_name, "page_size": 1}
        )
        data = response.json()
    results = data.get("results", [])
    rawg_game = results[0] if results else {}
    genres = [genre["name"] for genre in rawg_game.get("genres", [])]

    # STEP 4: save to DB and cache:
    game_data = {
        "steam_appid": steam_appid,
        "title": game_name,
        "cover_url": rawg_game.get("background_image"),
        "genres": genres,
        "estimated_playtime": rawg_game.get("playtime"),
        "metacritic_score": rawg_game.get("metacritic")
    }
    conn = get_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    # Insert or update game in DB
    try:
        cur.execute("""
            INSERT INTO games (steam_appid, title, cover_url, genres, estimated_playtime, metacritic_score)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (steam_appid) DO UPDATE SET
                last_synced_at = NOW()
            RETURNING *
            """, (
            game_data["steam_appid"],
            game_data["title"],
            game_data["cover_url"],
            game_data["genres"],
            game_data["estimated_playtime"],
            game_data["metacritic_score"]
        ))
        game = cur.fetchone()
        conn.commit()

        # Cache the game data for 24 hours
        r.setex(f"game:{steam_appid}", 86400, json.dumps(dict(game), default=str))

        return dict(game)
    finally:
        cur.close()
        conn.close()

# Steam API endpoint to get user's game library
@app.get("/steam/library/{steam_id}")
async def get_steam_library(steam_id: str, credentials: HTTPAuthorizationCredentials = Depends(security)):
    if not STEAM_API_KEY:
        raise HTTPException(status_code=500, detail="Steam API key not configured")
    
    url = "http://api.steampowered.com/IPlayerService/GetOwnedGames/v0001/"


    #Params for STEAM API call
    params = { 
        "key": STEAM_API_KEY,
        "steamid": steam_id,
        "include_appinfo": True,
        "include_played_free_games": True,
        "format": "json"
    }

    #Get library data from Steam API
    async with httpx.AsyncClient() as client:
        response = await client.get(url, params=params)
        if response.status_code != 200:
            raise HTTPException(status_code=response.status_code, detail=f"Steam API error: {response.text}")
        data = response.json()

    #Extract games from response
    games = data.get("response", {}).get("games", [])
    if not games:
        raise HTTPException(status_code=404, detail="No games found or profile is private")

    # Enrich games with metadata and prepare for backlog import
    enriched_games = []

    for steam_game in games:
        steam_appid = str(steam_game["appid"]) 
        game_name = steam_game.get("name", f"Game {steam_appid}")
        hours_played = steam_game.get("playtime_forever", 0) / 60

        game = await get_or_populate_game(steam_appid, game_name)
        print(f"DEBUG: {game}")

        enriched_games.append({
            "game_id": game["id"],
            "hours_played": round(hours_played, 2),
            "genres": game.get("genres", []),
            "progress_percent": int(game.get("estimated_playtime", 0) and min(100, (hours_played / game["estimated_playtime"]) * 100)),
            "estimated_playtime": game.get("estimated_playtime"),
            "cover_url": game.get("cover_url")
        })
        print(f"DEBUG: Estimated playtime for {game['title']}: {int(game.get('estimated_playtime', 0))}")
        print(f"DEBUG: Progress percent for {game['title']}: {int(game.get('estimated_playtime', 0) and min(100, (hours_played / game['estimated_playtime']) * 100))}")

    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{BACKLOG_SERVICE_URL}/backlog/import",
            json={"games": enriched_games},
            headers={"Authorization": f"Bearer {credentials.credentials}"}
        )
        if response.status_code >= 400:
            raise HTTPException(status_code=502, detail=f"Backlog service error: {response.text}")

    # outside the loop
    return {
        "steam_id": steam_id,
        "game_count": len(enriched_games),
        "games": enriched_games
    }


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
