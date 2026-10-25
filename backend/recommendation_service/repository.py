"""Read-only, ownership-scoped candidate loading from shared database models."""
from sqlmodel import Session, select

from app.database import (
    EnrichmentStatus, Game, GameEnrichment, GameMetadata, OwnedGame,
    PersonalHLTBOverride, PlayQueueEntry,
)
from .ranking import Candidate
from app.catalog_filters import effective_free_flag, is_test_build


def _genre_ids(genres) -> frozenset[str]:
    # Older/malformed stored metadata must not crash ranking or invent genres.
    if not isinstance(genres, list):
        return frozenset()
    return frozenset(
        entry['id'] for entry in genres
        if isinstance(entry, dict) and isinstance(entry.get('id'), str)
        and entry['id'].isascii() and entry['id'].isdecimal()
        and int(entry['id']) > 0 and str(int(entry['id'])) == entry['id']
    )


def load_candidates(session: Session, user_id: int) -> list[Candidate]:
    """One query, no writes/provider calls or service implementation imports.

    Personal overrides replace the entire estimate set, even if some values are
    missing. Shared HLTB IDs without a lookup row remain usable legacy matches;
    explicit unresolved lookup outcomes cannot become resolved merely from an ID.
    """
    if type(user_id) is not int or user_id <= 0:
        raise ValueError('Expected a positive verified user ID')
    rows = session.exec(
        select(Game, OwnedGame, GameMetadata, GameEnrichment,
               PersonalHLTBOverride, PlayQueueEntry)
        .join(OwnedGame, OwnedGame.game_id == Game.id)
        .outerjoin(GameMetadata, GameMetadata.game_id == Game.id)
        .outerjoin(GameEnrichment, GameEnrichment.game_id == Game.id)
        .outerjoin(PersonalHLTBOverride,
            (PersonalHLTBOverride.game_id == Game.id)
            & (PersonalHLTBOverride.user_id == OwnedGame.user_id))
        .outerjoin(PlayQueueEntry,
            (PlayQueueEntry.game_id == Game.id)
            & (PlayQueueEntry.user_id == OwnedGame.user_id))
        .where(OwnedGame.user_id == user_id)
        .order_by(Game.id)
    ).all()
    candidates = []
    for game, ownership, metadata, enrichment, override, queued in rows:
        estimates = override if override is not None else game
        resolved = estimates.hltb_id is not None and (
            override is not None or enrichment is None
            or enrichment.status == EnrichmentStatus.MATCHED)
        candidates.append(Candidate(
            game_id=game.id, title=game.name, cover_url=game.cover_url,
            cover_s3_key=game.cover_s3_key,
            completion_status=ownership.completion_status.value,
            single_player=metadata.single_player if metadata else None,
            genre_ids=_genre_ids(metadata.genres if metadata else None),
            genre_names=tuple(sorted((entry['id'], entry['name'].strip())
                for entry in (metadata.genres if metadata and isinstance(metadata.genres, list) else [])
                if isinstance(entry, dict) and entry.get('id') in _genre_ids(metadata.genres)
                and isinstance(entry.get('name'), str) and entry['name'].strip())),
            match_resolved=resolved,
            estimate_source=('personal' if override is not None else 'shared')
                if estimates.hltb_id is not None else None,
            main_story_hours=estimates.main_story_hours,
            main_extra_hours=estimates.main_extra_hours,
            completionist_hours=estimates.completionist_hours,
            queue_position=queued.position if queued else None,
            is_free=effective_free_flag(metadata.is_free, metadata.genres) if metadata else None,
            is_test_build=is_test_build(game.name),
            recommendation_hidden=ownership.recommendation_hidden,
            **{field: getattr(metadata, field) if metadata else None
               for field in ("trailer_url", "trailer_thumbnail", "trailer_title", "trailer_format")},
        ))
    return candidates


def supported_genre_ids(session: Session) -> frozenset[str]:
    """Shared catalog taxonomy, with no personal ownership/progress information."""
    genres = session.exec(select(GameMetadata.genres)).all()
    return frozenset(genre for values in genres for genre in _genre_ids(values))
