import asyncio
import json
import os
import re
import unicodedata

import httpx
import psycopg2
import psycopg2.extras
import redis
from howlongtobeatpy import HowLongToBeat
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt

load_dotenv()

app = FastAPI(title="Integration Service")
app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv("CORS_ORIGINS", "http://localhost:3000").split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

BACKLOG_SERVICE_URL = os.getenv("BACKLOG_SERVICE_URL", "http://backlog-service:8000")
STEAM_API_KEY = os.getenv("STEAM_API_KEY")
SECRET_KEY = os.getenv("JWT_SECRET", "changethislater")
ALGORITHM = "HS256"
security = HTTPBearer()
completion_provider = HowLongToBeat(0.0)
COMPLETION_CONFIDENCE = float(os.getenv("HLTB_MINIMUM_CONFIDENCE", "0.85"))
COMPLETION_CACHE_TTL = int(os.getenv("HLTB_CACHE_TTL_SECONDS", "604800"))
COMPLETION_MISS_CACHE_TTL = int(os.getenv("HLTB_MISS_CACHE_TTL_SECONDS", "3600"))
COMPLETION_CACHE_NAMESPACE = "hltb:steam:v1"


def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)):
    try:
        payload = jwt.decode(credentials.credentials, SECRET_KEY, algorithms=[ALGORITHM])
        return int(payload.get("sub"))
    except (JWTError, TypeError, ValueError):
        raise HTTPException(status_code=401, detail="Invalid token")


def get_db():
    return psycopg2.connect(
        host=os.getenv("POSTGRES_HOST", "postgres"),
        port=os.getenv("POSTGRES_PORT", "5432"),
        database=os.getenv("POSTGRES_DB", "backlog_db"),
        user=os.getenv("POSTGRES_USER", "backlog_user"),
        password=os.getenv("POSTGRES_PASSWORD", "backlogpassword123"),
    )


def get_redis():
    return redis.Redis(
        host=os.getenv("REDIS_HOST", "redis"),
        port=int(os.getenv("REDIS_PORT", "6379")),
        decode_responses=True,
        socket_connect_timeout=1,
        socket_timeout=1,
    )


def cache_available():
    try:
        return bool(get_redis().ping())
    except redis.RedisError:
        return False


def positive_hours(value):
    return float(value) if value and float(value) > 0 else None


def normalize_provider_title(title):
    normalized = title.translate(str.maketrans({"™": "", "®": "", "©": ""}))
    normalized = unicodedata.normalize("NFKC", normalized)
    normalized = normalized.translate(str.maketrans({
        "’": "'",
        "‘": "'",
        "“": '"',
        "”": '"',
        "–": "-",
        "—": "-",
    }))
    normalized = re.sub(r"[-:]+", " ", normalized)
    normalized = re.sub(r"[^\w\s'&:+-]", " ", normalized)
    return re.sub(r"\s+", " ", normalized).strip()


def provider_search_titles(title):
    normalized = normalize_provider_title(title)
    titles = [normalized]
    without_platform_label = re.sub(
        r"\s+(enhanced|legacy|steam edition)$",
        "",
        normalized,
        flags=re.IGNORECASE,
    ).strip()
    if without_platform_label and without_platform_label.casefold() != normalized.casefold():
        titles.append(without_platform_label)
    return titles


def choose_completion_match(results, search_title):
    exact = next(
        (
            item for item in results or []
            if normalize_provider_title(item.game_name).casefold() == search_title.casefold()
        ),
        None,
    )
    if exact:
        return exact
    subtitle_matches = [
        item for item in results or []
        if normalize_provider_title(item.game_name).casefold().startswith(
            f"{search_title.casefold()} "
        )
    ]
    if subtitle_matches:
        return max(subtitle_matches, key=lambda item: item.similarity)
    best = max(results or [], key=lambda item: item.similarity, default=None)
    return best if best and best.similarity >= COMPLETION_CONFIDENCE else None


async def fetch_completion_for_game(steam_game, semaphore):
    steam_appid = str(steam_game["appid"])
    title = steam_game.get("name", f"Game {steam_appid}")
    search_titles = provider_search_titles(title)
    cache_key = f"{COMPLETION_CACHE_NAMESPACE}:{steam_appid}"
    cache = get_redis()

    try:
        cached = cache.get(cache_key)
        if cached:
            value = json.loads(cached)
            return None if value.get("not_found") else value
    except (redis.RedisError, json.JSONDecodeError):
        pass

    try:
        best = None
        for search_title in search_titles:
            async with semaphore:
                results = await completion_provider.async_search(
                    search_title,
                    similarity_case_sensitive=False,
                )
            best = choose_completion_match(results, search_title)
            if best:
                break
        if best is None:
            value = {"not_found": True}
        else:
            value = {
                "steam_appid": steam_appid,
                "source": "howlongtobeat",
                "source_id": str(best.game_id),
                "cover_url": best.game_image_url,
                "main_story_hours": positive_hours(best.main_story),
                "main_plus_extras_hours": positive_hours(best.main_extra),
                "completionist_hours": positive_hours(best.completionist),
                "match_confidence": float(best.similarity),
            }
    except Exception:
        return None

    try:
        ttl = COMPLETION_MISS_CACHE_TTL if value.get("not_found") else COMPLETION_CACHE_TTL
        cache.setex(cache_key, ttl, json.dumps(value))
    except redis.RedisError:
        pass
    return None if value.get("not_found") else value


async def fetch_completion_metadata(steam_games):
    semaphore = asyncio.Semaphore(2)
    results = await asyncio.gather(
        *(fetch_completion_for_game(game, semaphore) for game in steam_games)
    )
    return {result["steam_appid"]: result for result in results if result}


def upsert_steam_game(steam_appid: str, title: str, completion=None):
    completion = completion or {}
    conn = get_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        cur.execute(
            """
            INSERT INTO games (
                steam_appid, title, cover_url, completion_source, completion_source_id,
                main_story_hours, main_plus_extras_hours, completionist_hours,
                completion_match_confidence, completion_synced_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, CASE WHEN %s IS NULL THEN NULL ELSE NOW() END)
            ON CONFLICT (steam_appid) DO UPDATE SET
                title = EXCLUDED.title,
                cover_url = COALESCE(EXCLUDED.cover_url, games.cover_url),
                completion_source = COALESCE(EXCLUDED.completion_source, games.completion_source),
                completion_source_id = COALESCE(EXCLUDED.completion_source_id, games.completion_source_id),
                main_story_hours = COALESCE(EXCLUDED.main_story_hours, games.main_story_hours),
                main_plus_extras_hours = COALESCE(EXCLUDED.main_plus_extras_hours, games.main_plus_extras_hours),
                completionist_hours = COALESCE(EXCLUDED.completionist_hours, games.completionist_hours),
                completion_match_confidence = COALESCE(EXCLUDED.completion_match_confidence, games.completion_match_confidence),
                completion_synced_at = COALESCE(EXCLUDED.completion_synced_at, games.completion_synced_at),
                last_synced_at = NOW()
            RETURNING id
            """,
            (
                steam_appid, title, completion.get("cover_url"), completion.get("source"),
                completion.get("source_id"), completion.get("main_story_hours"),
                completion.get("main_plus_extras_hours"), completion.get("completionist_hours"),
                completion.get("match_confidence"), completion.get("source"),
            ),
        )
        game_id = cur.fetchone()["id"]
        conn.commit()
        return game_id
    finally:
        cur.close()
        conn.close()


@app.get("/health")
def health():
    return {
        "status": "ok",
        "service": "integration-service",
        "metadata_provider": "howlongtobeatpy",
        "redis_available": cache_available(),
    }


@app.get("/steam/library/{steam_id}")
async def import_steam_library(
    steam_id: str,
    credentials: HTTPAuthorizationCredentials = Depends(security),
    _user_id: int = Depends(get_current_user),
):
    if not STEAM_API_KEY:
        raise HTTPException(status_code=500, detail="Steam API key not configured")

    async with httpx.AsyncClient(timeout=20.0) as client:
        response = await client.get(
            "https://api.steampowered.com/IPlayerService/GetOwnedGames/v0001/",
            params={
                "key": STEAM_API_KEY,
                "steamid": steam_id,
                "include_appinfo": True,
                "include_played_free_games": True,
                "format": "json",
            },
        )
        if response.status_code != 200:
            raise HTTPException(status_code=502, detail="Steam could not return this library")
        steam_games = response.json().get("response", {}).get("games", [])

    if not steam_games:
        raise HTTPException(status_code=404, detail="No games found; the Steam profile may be private")

    completion_by_appid = await fetch_completion_metadata(steam_games)
    imported_games = []
    for steam_game in steam_games:
        steam_appid = str(steam_game["appid"])
        game_id = upsert_steam_game(
            steam_appid,
            steam_game.get("name", f"Game {steam_appid}"),
            completion_by_appid.get(steam_appid),
        )
        imported_games.append({
            "game_id": game_id,
            "hours_played": round(steam_game.get("playtime_forever", 0) / 60, 2),
            "progress_percent": 0,
        })

    async with httpx.AsyncClient(timeout=20.0) as client:
        response = await client.post(
            f"{BACKLOG_SERVICE_URL}/backlog/import",
            json={"games": imported_games},
            headers={"Authorization": f"Bearer {credentials.credentials}"},
        )
        if response.status_code >= 400:
            raise HTTPException(status_code=502, detail="Backlog service could not save the import")

    return {
        "steam_id": steam_id,
        "game_count": len(imported_games),
        "completion_matches": len(completion_by_appid),
    }
