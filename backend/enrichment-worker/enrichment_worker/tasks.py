"""Catalog import scheduling and one-game enrichment tasks."""

import asyncio
import logging

from sqlmodel import Session, select

from app.database import EnrichmentStatus, Game, GameEnrichment, engine
from enrichment_service.hltb import HLTBLookupError, HLTBLookupTimeout, lookup_candidates
from enrichment_service.matching import review_match_candidates, MatchResult, MatchStatus
from enrichment_service import review_cache
from enrichment_service.storage import record_lookup_failure, save_match_result
from .worker import app


def eligible(game, record):
    return bool(game and game.name and game.name.strip()) and game.hltb_id is None and (
        record is None or record.status == EnrichmentStatus.LOOKUP_FAILED
    )


@app.task(name="enrichment.enrich_game", soft_time_limit=30, time_limit=35)
def enrich_game(game_id: int):
    """Look up one eligible catalog game; cache uncertain suggestions for 24 hours."""
    return _lookup_game(game_id)


@app.task(name="enrichment.refresh_review", soft_time_limit=30, time_limit=35)
def refresh_review(game_id: int, token: str):
    """Rebuild expired suggestions, retaining every owner's personal selection."""
    return _lookup_game(game_id, refresh=True, token=token)


def _lookup_game(game_id: int, *, refresh: bool = False, token: str | None = None):
    if type(game_id) is not int or game_id <= 0:
        raise ValueError("Expected a positive catalog game ID")
    if token is not None and (not isinstance(token, str) or not token):
        raise ValueError("Invalid refresh token")
    # Capture the database state, then close the connection before external I/O.
    with Session(engine) as session:
        game = session.get(Game, game_id)
        record = session.get(GameEnrichment, game_id)
        allowed = (bool(game and game.name and game.name.strip()) and record is not None
                   and record.status == EnrichmentStatus.AMBIGUOUS) if refresh else eligible(game, record)
        if not allowed:
            if token:
                review_cache.release_refresh(game_id, token)
            return "skipped"
        if refresh:
            cached = review_cache.get_review(game_id)
            if (cached and record.last_success_at is not None
                    and cached.get("last_success_at") == record.last_success_at.isoformat()):
                review_cache.release_refresh(game_id, token)
                return "skipped"
        title = game.name
        snapshot = record.last_lookup_at if record else None
    token = token or review_cache.claim_refresh(game_id)
    if not token or not review_cache.begin_refresh(game_id, token):
        return "skipped"
    try:
        failure = None
        try:
            candidates = asyncio.run(lookup_candidates(title, timeout_seconds=20))
        except HLTBLookupError as error:
            failure = error
        with Session(engine) as session:
            # Serialize cache publication and selections with shared result updates.
            game = session.exec(select(Game).where(Game.id == game_id).with_for_update()).first()
            record = session.get(GameEnrichment, game_id)
            allowed = (bool(game and record) and record.status == EnrichmentStatus.AMBIGUOUS) if refresh else eligible(game, record)
            if (not allowed or game.name != title
                    or (record.last_lookup_at if record else None) != snapshot):
                return "stale"
            if failure is not None:
                record_lookup_failure(session, game_id, timed_out=isinstance(failure, HLTBLookupTimeout))
                if refresh:
                    review_cache.put_failure(game_id, token)
                outcome = "failed"
            else:
                result = review_match_candidates(title, candidates)
                # Review refreshes always let the owner confirm a plausible choice.
                if refresh and result.status == MatchStatus.MATCHED:
                    result = MatchResult(MatchStatus.AMBIGUOUS, None, "review_refreshed", result.candidates)
                record = save_match_result(session, game_id, result)
                review_cache.put_review(game_id, token, result=result, last_success_at=record.last_success_at)
                outcome = result.status.value
            session.commit()
        return outcome
    finally:
        review_cache.release_refresh(game_id, token)


@app.task(name="catalog.games_imported")
def games_imported(*, schema_version: int, steam_app_ids: list[int]):
    """Resolve a v1 import event to eligible catalog IDs; no HLTB search here.

    The producer supplies Steam IDs, not enrichment decisions. Publication errors
    fail this task visibly; automatic retries and durable dispatch are pending.
    """
    if type(schema_version) is not int or schema_version != 1:
        raise ValueError("Unsupported catalog import event version")
    if (not isinstance(steam_app_ids, list)
            or any(type(app_id) is not int or app_id <= 0 for app_id in steam_app_ids)):
        raise ValueError("Expected a list of positive Steam app IDs")
    if not steam_app_ids:
        return 0

    # Release the read session before publishing individual game tasks.
    with Session(engine) as session:
        rows = session.exec(select(Game, GameEnrichment)
            .outerjoin(GameEnrichment, GameEnrichment.game_id == Game.id)
            .where(Game.steam_app_id.in_(set(steam_app_ids)))).all()
        ids = [game.id for game, record in rows if eligible(game, record)]
    if not ids:
        return 0
    with app.connection_for_write(connect_timeout=2) as connection:
        connection.ensure_connection(max_retries=0)
        with app.amqp.Producer(connection) as producer:
            for game_id in ids:
                app.send_task("enrichment.enrich_game", args=[game_id],
                              queue="enrichment", producer=producer, retry=False)
    return len(ids)


@app.task(name="enrichment.healthcheck")
def healthcheck():
    """Verify queued task execution without contacting HLTB or changing data."""
    logging.getLogger(__name__).info("Enrichment queue healthcheck completed")
