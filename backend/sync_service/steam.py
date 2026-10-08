import httpx
from fastapi import HTTPException
from pydantic import BaseModel, Field, ValidationError, model_validator
from sqlalchemy.dialects.postgresql import insert
from sqlmodel import Session, select
from app.database import Game, OwnedGame

from app.core.config import STEAM_API_KEY
from app.database import Game


class SteamGame(BaseModel):
    appid: int = Field(strict=True, gt=0)


class OwnedGames(BaseModel):
    game_count: int = Field(strict=True, ge=0)
    games: list[SteamGame] = Field(default_factory=list)

    @model_validator(mode="after")
    def check_count(self):
        # Missing data must not silently become an empty library.
        if self.game_count != len(self.games):
            raise ValueError("Incomplete Steam library")
        return self


def get_owned_games(steam_id: str) -> OwnedGames:
    if not STEAM_API_KEY:
        raise HTTPException(status_code=503, detail="STEAM_API_KEY is not configured")
    # Fetch only the linked account's library with a bounded request timeout.
    try:
        with httpx.Client(timeout=15, follow_redirects=False) as client:
            result = client.get(
                "https://api.steampowered.com/IPlayerService/GetOwnedGames/v0001/",
                params={"key": STEAM_API_KEY, "steamid": steam_id, "include_played_free_games": 1},
            )
            result.raise_for_status()
        return OwnedGames.model_validate(result.json()["response"])
    except httpx.TimeoutException:
        raise HTTPException(status_code=504, detail="Steam library request timed out")
    except (httpx.HTTPError, ValueError, KeyError, TypeError, ValidationError):
        raise HTTPException(status_code=502, detail="Steam library unavailable or invalid")


def insert_missing_games(session: Session, library: OwnedGames) -> int:
    # Sort and deduplicate inserts to keep concurrent batches in a consistent order.
    app_ids = sorted({game.appid for game in library.games})
    if not app_ids:
        return 0
    statement = insert(Game).values([{"steam_app_id": app_id} for app_id in app_ids])
    statement = statement.on_conflict_do_nothing(index_elements=["steam_app_id"]).returning(Game.id)
    return len(session.execute(statement).scalars().all())


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