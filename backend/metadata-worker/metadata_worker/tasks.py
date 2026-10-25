"""Steam metadata task execution and periodic dispatch."""
import asyncio
from datetime import UTC, datetime
from sqlmodel import Session, select
from app.database import Game, GameMetadata, engine
from .worker import app
from . import metadata_jobs
from .steam_metadata import lookup_metadata, SteamMetadataError, SteamMetadataTimeout, SteamMetadataUnavailable
from .steam_pacing import wait_for_slot, SteamPacingError


@app.task(name="catalog.maintain_metadata_jobs")
def maintain_metadata_jobs():
    return metadata_jobs.dispatch(app)


@app.task(name="catalog.collect_metadata", soft_time_limit=25, time_limit=30)
def collect_metadata(game_id: int, attempt_token: str):
    if type(game_id) is not int or game_id <= 0 or not isinstance(attempt_token, str) or not attempt_token:
        raise ValueError("Invalid metadata attempt")
    if not metadata_jobs.claim_running(game_id, attempt_token):
        return "skipped"
    try:
        with Session(engine) as session:
            game = session.get(Game, game_id)
            previous = session.get(GameMetadata, game_id)
            if previous and previous.last_success_at is not None:
                job = metadata_jobs.lock_attempt(session, game_id, attempt_token)
                if job:
                    metadata_jobs.finish(job)
                    session.add(job)
                    session.commit()
                return "skipped"
            app_id = game.steam_app_id
        failure = None
        result = None
        try:
            wait_for_slot()
            result = asyncio.run(lookup_metadata(app_id))
        except (SteamMetadataError, SteamPacingError) as error:
            failure = ("lookup_timeout" if isinstance(error, SteamMetadataTimeout) else
                       "app_unavailable" if isinstance(error, SteamMetadataUnavailable) else
                       "pacing_failed" if isinstance(error, SteamPacingError) else "lookup_failed")
        with Session(engine) as session:
            job = metadata_jobs.lock_attempt(session, game_id, attempt_token)
            if job is None:
                return "stale"
            game = session.exec(select(Game).where(Game.id == game_id).with_for_update()).first()
            if game is None or game.steam_app_id != app_id:
                metadata_jobs.finish(job, failed=True, error="stale")
                session.add(job)
                session.commit()
                return "stale"
            record = session.get(GameMetadata, game_id)
            if record is None:
                record = GameMetadata(game_id=game_id)
            now = datetime.now(UTC)
            if record.last_success_at is None:
                record.last_lookup_at = now
                record.last_error = failure
                if result is not None:
                    record.single_player = result.single_player
                    record.multiplayer = result.multiplayer
                    record.is_free = result.is_free
                    for field in ("trailer_url", "trailer_thumbnail", "trailer_title", "trailer_format"):
                        setattr(record, field, getattr(result, field))
                    record.genres = [{"id": genre.id, "name": genre.name} for genre in result.genres]
                    record.last_success_at = now
                    fallback = f"https://cdn.akamai.steamstatic.com/steam/apps/{app_id}/header.jpg"
                    if result.header_image and (not game.cover_url or game.cover_url == fallback):
                        game.cover_url = result.header_image
                        session.add(game)
                session.add(record)
            metadata_jobs.finish(job, failed=failure is not None, error=failure)
            session.add(job)
            session.commit()
        return "failed" if failure else "completed"
    except Exception:
        metadata_jobs.fail_attempt(game_id, attempt_token, "task_error")
        raise
