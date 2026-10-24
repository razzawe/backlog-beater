from fastapi import Depends, FastAPI, HTTPException
from starlette.concurrency import run_in_threadpool
from pydantic import BaseModel
from sqlmodel import Session

from app.core.identity import get_gateway_user
from app.database import User, engine
from sync_service.steam import OwnedGames, get_owned_games, sync_owned_games

from sync_service.events import ImportEventSubmission, publish_games_imported


# Separate application for Steam library syncing.
app = FastAPI(title="Backlog Beater Sync Service")


class SyncResult(BaseModel):
    # Steam's total library size includes games already stored by earlier syncs.
    game_count: int
    # Catalog entries are shared by all users; ownership links belong to this user.
    inserted_count: int
    ownerships_inserted_count: int
    enrichment: ImportEventSubmission


@app.post("/api/v1/steam/sync", response_model=SyncResult)
async def sync_games(
    user: User = Depends(get_gateway_user),
) -> SyncResult:
    """Fetch and store the authenticated user's linked Steam library.
    Commit catalog and ownership inserts atomically and return their counts."""
    # Resolve the Steam ID from the authenticated account, never request input.
    if user.steam_id is None:
        raise HTTPException(status_code=409, detail="Connect Steam before syncing")
    # Ownership records require the internal database ID, not the Steam ID.
    # Fetch and validate Steam's response before starting any library writes.
    library = await get_owned_games(user.steam_id)
    # The synchronous database work runs in a worker thread to avoid blocking
    # the event loop while other requests are being handled.

    # Save the library before publishing any background work.
    inserted, ownerships_inserted = await run_in_threadpool(
        save_library, user.id, library,
    )
    # Submission failures are reported without undoing the committed import.

    # Send Steam app IDs to Redis Queue 
    enrichment = await run_in_threadpool(
        publish_games_imported, sorted({game.appid for game in library.games}),
    )
    return SyncResult(
        game_count=library.game_count,
        inserted_count=inserted,
        ownerships_inserted_count=ownerships_inserted,
        enrichment=enrichment,
    )


def save_library(user_id: int, library: OwnedGames) -> tuple[int, int]:
    """Save the fetched library in a single database transaction.
    Return insertion counts, rolling back all writes if any operation fails."""
    # Create and close the session in the worker thread that performs the writes.
    with Session(engine) as session:
        # Commit catalog entries and ownership links in one transaction.
        try:
            inserted, ownerships_inserted = sync_owned_games(session, user_id, library)
            session.commit()
        except Exception:
            # Undo both kinds of inserts so a failed sync cannot leave a partial library.
            session.rollback()
            raise
        return inserted, ownerships_inserted


@app.get("/internal/health", include_in_schema=False)
def health():
    return {"status": "ok"}
