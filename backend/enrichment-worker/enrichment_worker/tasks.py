"""Catalog import scheduling and one-game enrichment tasks."""

import asyncio
import logging

from sqlmodel import Session, select

from app.database import EnrichmentStatus, Game, GameEnrichment, engine
from enrichment_service.hltb import HLTBLookupError, HLTBLookupTimeout, HLTBTemporaryError, lookup_candidates
from enrichment_service.matching import review_match_candidates, MatchResult, MatchStatus
from enrichment_service import review_cache
from enrichment_service.storage import record_lookup_failure, save_match_result
from .worker import app
from . import jobs, hltb_pacing
from metadata_worker import metadata_jobs
import random


def eligible(game, record):
    return bool(game and game.name and game.name.strip()) and game.hltb_id is None and (
        record is None or record.status == EnrichmentStatus.LOOKUP_FAILED
    )


@app.task(bind=True, max_retries=None, name="enrichment.enrich_game", soft_time_limit=30, time_limit=35)
def enrich_game(self, game_id: int, attempt_token: str):
    """Look up one eligible catalog game; cache uncertain suggestions for 24 hours."""
    if type(game_id) is not int or game_id <= 0 or not isinstance(attempt_token, str) or not attempt_token:
        raise ValueError("Invalid job attempt")
    wait = hltb_pacing.wait_for_slot()
    if wait:
        raise self.retry(countdown=wait + random.uniform(0.1, 0.5))
    try:
        if not jobs.claim_running(game_id, attempt_token):
            return "skipped"
        outcome = _lookup_game(game_id, attempt_token=attempt_token)
        if outcome in {"skipped", "stale"}:
            jobs.fail_attempt(game_id, attempt_token, outcome)
        return outcome
    except Exception:
        jobs.fail_attempt(game_id, attempt_token, "task_error")
        raise


@app.task(bind=True, max_retries=None, name="enrichment.refresh_review", soft_time_limit=30, time_limit=35)
def refresh_review(self, game_id: int, token: str, failure_count: int = 0):
    """Rebuild expired suggestions, retaining every owner's personal selection."""
    wait = hltb_pacing.wait_for_slot()
    if wait:
        raise self.retry(countdown=wait + random.uniform(0.1, 0.5))
    try:
        return _lookup_game(game_id, refresh=True, token=token, refresh_failures=failure_count)
    except HLTBTemporaryError as error:
        if failure_count >= 2:
            return "failed"
        new_token = review_cache.claim_refresh(game_id)
        if not new_token:
            return "skipped"
        delay = max(60 if failure_count == 0 else 300, error.retry_after)
        raise self.retry(args=[game_id, new_token, failure_count + 1], countdown=delay)


def _lookup_game(game_id: int, *, refresh: bool = False, token: str | None = None, attempt_token: str | None = None, refresh_failures: int = 0):
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
            if isinstance(error, HLTBTemporaryError):
                hltb_pacing.cooldown(max(60, error.retry_after))
        with Session(engine) as session:
            job = jobs.lock_attempt(session, game_id, attempt_token) if attempt_token else None
            if attempt_token and job is None:
                return "stale"
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
            if job is not None:
                jobs.finish(job, failed=failure is not None,
                            error="lookup_failed" if failure else None)
                if isinstance(failure, HLTBTemporaryError):
                    jobs.schedule_retry(job, retry_after=failure.retry_after)
                session.add(job)
            session.commit()
        if refresh and isinstance(failure, HLTBTemporaryError) and refresh_failures < 2:
            raise failure
        return outcome
    finally:
        review_cache.release_refresh(game_id, token)


@app.task(name="catalog.games_imported")
def games_imported(*, schema_version: int, steam_app_ids: list[int]):
    """Resolve a v1 import event to eligible catalog IDs; no HLTB search here.

    The producer supplies Steam IDs, not enrichment decisions. Jobs are committed
    before publication; maintenance recovers pending dispatch after broker failures.
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
    with Session(engine) as session:
        jobs.create_ready(session, ids)
        metadata_ids = [game.id for game, _ in rows]
        metadata_jobs.create_ready(session, metadata_ids)
        session.commit()
    count = jobs.dispatch(app, ids)
    metadata_jobs.dispatch(app, metadata_ids)
    return count


@app.task(name="enrichment.maintain_jobs")
def maintain_jobs():
    return jobs.dispatch(app)



@app.task(name="enrichment.healthcheck")
def healthcheck():
    """Verify queued task execution without contacting HLTB or changing data."""
    logging.getLogger(__name__).info("Enrichment queue healthcheck completed")
