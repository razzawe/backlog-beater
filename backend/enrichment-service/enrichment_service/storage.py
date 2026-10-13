"""Transactional persistence; callers commit or roll back the whole operation."""

from datetime import UTC, datetime

from sqlmodel import Session, select

from app.database import EnrichmentStatus, Game, GameEnrichment, OwnedGame, PersonalHLTBOverride
from .matching import MatchResult, MatchStatus


def _lock_game(session: Session, game_id: int) -> Game:
    # Serialize result updates and override selections, even before a result exists.
    game = session.exec(select(Game).where(Game.id == game_id).with_for_update()
                        .execution_options(populate_existing=True)).first()
    if game is None:
        raise ValueError("Game does not exist")
    return game


def save_match_result(session: Session, game_id: int, result: MatchResult) -> GameEnrichment:
    """Persist a successful lookup and replace its automatic result atomically."""
    if result.status == MatchStatus.MATCHED:
        if result.candidate is None or result.candidates != (result.candidate,):
            raise ValueError("Matched result requires exactly one selected candidate")
    elif result.candidate is not None:
        raise ValueError("Unresolved result cannot have a selected candidate")
    if result.status == MatchStatus.AMBIGUOUS and not result.candidates:
        raise ValueError("Ambiguous result requires review candidates")
    if result.status == MatchStatus.NO_MATCH and result.candidates:
        raise ValueError("No-match result cannot have exact candidates")
    game = _lock_game(session, game_id)
    now = datetime.now(UTC)
    record = session.get(GameEnrichment, game_id, populate_existing=True)
    if record is None:
        record = GameEnrichment(game_id=game_id, status=EnrichmentStatus(result.status.value),
                                reason=result.reason, last_lookup_at=now)
    record.status = EnrichmentStatus(result.status.value)
    record.reason = result.reason
    selected = result.candidate
    game.hltb_id = selected.hltb_id if selected else None
    game.main_story_hours = selected.main_story_hours if selected else None
    game.main_extra_hours = selected.main_extra_hours if selected else None
    game.completionist_hours = selected.completionist_hours if selected else None
    record.last_lookup_at = now
    record.last_success_at = now
    record.last_error = None
    session.add(game)
    session.add(record)
    session.flush()
    return record


def record_lookup_failure(session: Session, game_id: int, *, timed_out: bool = False) -> GameEnrichment:
    """Record failure without replacing a previous successful result.

    A first failure has no successful result (last_success_at is None); its
    reason distinguishes it from a successful no-match lookup. Store fixed
    error codes rather than provider exception text.
    """
    _lock_game(session, game_id)
    now = datetime.now(UTC)
    record = session.get(GameEnrichment, game_id, populate_existing=True)
    if record is None:
        record = GameEnrichment(game_id=game_id, status=EnrichmentStatus.LOOKUP_FAILED,
                                reason="lookup_not_completed", last_lookup_at=now)
    record.last_lookup_at = now
    record.last_error = "lookup_timeout" if timed_out else "lookup_failed"
    session.add(record)
    session.flush()
    return record


def save_cached_override(session: Session, user_id: int, game_id: int, *,
                         hltb_id: int, cache_version: str) -> PersonalHLTBOverride:
    """Validate an owner selection against the current Redis shortlist under a game lock."""
    from .review_cache import get_review

    _lock_game(session, game_id)
    if session.get(OwnedGame, (user_id, game_id)) is None:
        raise PermissionError("Game is not owned by this user")
    shared = session.get(GameEnrichment, game_id, populate_existing=True)
    cached = get_review(game_id)
    if (shared is None or shared.status != EnrichmentStatus.AMBIGUOUS or not cached
            or cached.get("version") != cache_version
            or cached.get("last_success_at") != shared.last_success_at.isoformat()):
        raise ValueError("Suggestions expired or changed; reload before selecting")
    selected = next((candidate for candidate in cached["candidates"] if candidate["hltb_id"] == hltb_id), None)
    if selected is None:
        raise ValueError("Candidate is not in the current suggestions")
    record = session.get(PersonalHLTBOverride, (user_id, game_id))
    if record is None:
        record = PersonalHLTBOverride(user_id=user_id, game_id=game_id,
            hltb_id=hltb_id, candidate=selected, selected_at=datetime.now(UTC))
    record.hltb_id = hltb_id
    for field in ("main_story_hours", "main_extra_hours", "completionist_hours"):
        setattr(record, field, selected[field])
    record.candidate = dict(selected)
    record.selected_at = datetime.now(UTC)
    session.add(record)
    session.flush()
    return record
