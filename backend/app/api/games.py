from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session, select

from app.api.main import get_current_user
from app.database import CompletionStatus, Game, OwnedGame, User, get_session


# Group endpoints for the authenticated user's stored library.
games_router = APIRouter(prefix="/games", tags=["games"])


class OwnedGameResponse(BaseModel):
    # Expose catalog identifiers without internal ownership records.
    id: int
    steam_app_id: int
    cover_url: str | None
    completion_status: CompletionStatus


@games_router.get("", response_model=list[OwnedGameResponse])
def get_owned_games(
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> list[OwnedGameResponse]:
    """Return the authenticated user's stored game library.
    List catalog identifiers in stable order, or an empty list if none exist."""
    # Require a persisted authenticated account before querying ownership.
    if current_user.id is None:
        raise HTTPException(status_code=500, detail="User has no database ID")

    # Select only catalog games owned by the authenticated user in stable order.
    rows = session.exec(
        select(Game.id, Game.steam_app_id, Game.cover_url, OwnedGame.completion_status)
        .join(OwnedGame, OwnedGame.game_id == Game.id)
        .where(OwnedGame.user_id == current_user.id)
        .order_by(Game.id)
    ).all()

    # Return an empty list when the user has no stored ownership links.
    return [
        OwnedGameResponse(
            id=game_id, steam_app_id=app_id,
            cover_url=cover_url, completion_status=completion_status,
        )
        for game_id, app_id, cover_url, completion_status in rows
    ]
