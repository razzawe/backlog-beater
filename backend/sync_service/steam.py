import asyncio

import httpx
from fastapi import HTTPException
from pydantic import BaseModel, Field, ValidationError, model_validator
from sqlalchemy import func
from sqlalchemy.dialects.postgresql import insert
from sqlmodel import Session, select

from app.core.config import STEAM_API_KEY
from app.database import Game, OwnedGame


class SteamGame(BaseModel):
    appid: int = Field(strict=True, gt=0)
    name: str | None = Field(default=None, strict=True)


class OwnedGames(BaseModel):
    game_count: int = Field(strict=True, ge=0)
    games: list[SteamGame] = Field(default_factory=list)

    @model_validator(mode="after")
    def check_count(self):
        # Missing data must not silently become an empty library.
        if self.game_count != len(self.games):
            raise ValueError("Incomplete Steam library")
        return self


async def get_owned_games(steam_id: str) -> OwnedGames:
    if not STEAM_API_KEY:
        raise HTTPException(status_code=503, detail="STEAM_API_KEY is not configured")
    # Four attempts share a deadline, leaving time for DB writes before Nginx's
    # 60-second timeout. HTTPX's timeout alone only bounds individual I/O phases.
    try:
        async with asyncio.timeout(45):
            async with httpx.AsyncClient(timeout=10, follow_redirects=False) as client: #10 second tries
                for attempt in range(4):
                    try:
                        result = await client.get(
                            "https://api.steampowered.com/IPlayerService/GetOwnedGames/v0001/",
                            params={"key": STEAM_API_KEY, "steamid": steam_id,
                                    "include_played_free_games": 1, "include_appinfo": 1},
                        )
                        result.raise_for_status()
                    except (httpx.TransportError, httpx.HTTPStatusError) as error:
                        transient = isinstance(error, httpx.TransportError) or (
                            error.response.status_code in {408, 429, 500, 502, 503, 504}
                        )
                        if not transient or attempt == 3: # fourth attempt times out library request
                            if isinstance(error, httpx.TimeoutException): # last attempt elapsed >= 10 seconds and nothing happened
                                raise HTTPException(504, "Steam library request timed out after 4 attempts") from error
                            raise HTTPException(502, "Steam library unavailable; try syncing again later") from error
                        await asyncio.sleep(2 ** attempt) #1 second, then 2 second, then 4 seconds
                        continue
                    # Invalid/incomplete data is not a transient transport failure.
                    return OwnedGames.model_validate(result.json()["response"])
    except TimeoutError as error:
        raise HTTPException(504, "Steam library request exceeded its time limit; try syncing again later") from error
    except (httpx.HTTPError, ValueError, KeyError, TypeError, ValidationError) as error:
        raise HTTPException(502, "Steam library unavailable or invalid") from error


def insert_missing_games(session: Session, library: OwnedGames) -> int:
    # Stable order and deduplication protect concurrent imports.
    games = {}
    for game in library.games:
        if game.appid not in games or game.name:
            games[game.appid] = game
    rows = [
        {"steam_app_id": appid, "name": games[appid].name.strip() or None if games[appid].name else None,
         "cover_url": f"https://cdn.akamai.steamstatic.com/steam/apps/{appid}/header.jpg"}
        for appid in sorted(games)
    ]
    if not rows:
        return 0
    statement = insert(Game).values(rows)
    inserted = len(session.execute(statement.on_conflict_do_nothing(
        index_elements=["steam_app_id"],
    ).returning(Game.id)).scalars().all())
    # Refresh existing catalog metadata without touching ownership or progress.
    session.execute(statement.on_conflict_do_update(
        index_elements=["steam_app_id"],
        set_={"name": func.coalesce(statement.excluded.name, Game.name),
              "cover_url": func.coalesce(Game.cover_url, statement.excluded.cover_url)},
    ))
    return inserted


def insert_owned_games(
    session: Session,
    user_id: int,
    library: OwnedGames,
) -> int:
    app_ids = sorted({game.appid for game in library.games})
    if not app_ids:
        return 0

    # Include existing catalog entries as well as newly inserted games.
    game_ids = session.exec(
        select(Game.id)
        .where(Game.steam_app_id.in_(app_ids))
        .order_by(Game.id)
    ).all()

    statement = (
        insert(OwnedGame)
        .values([
            {"user_id": user_id, "game_id": game_id}
            for game_id in game_ids
        ])
        .on_conflict_do_nothing(index_elements=["user_id", "game_id"])
        .returning(OwnedGame.game_id)
    )
    return len(session.execute(statement).scalars().all())

def sync_owned_games(
    session: Session,
    user_id: int,
    library: OwnedGames,
) -> tuple[int, int]:
    """Insert catalog games and ownership links in the caller's transaction."""
    inserted_count = insert_missing_games(session, library)
    ownerships_inserted_count = insert_owned_games(session, user_id, library)
    return inserted_count, ownerships_inserted_count
