"""Pure recommendation selection; no database or provider calls."""
from collections import Counter
from dataclasses import dataclass
import math
from typing import Iterable

from .schemas import RecommendationRequest


@dataclass(frozen=True)
class Candidate:
    """Repository supplies ownership-scoped, effective personal/shared estimates."""
    game_id: int
    title: str | None
    cover_url: str | None
    completion_status: str
    single_player: bool | None
    genre_ids: frozenset[str]
    match_resolved: bool
    estimate_source: str | None
    main_story_hours: float | None = None
    main_extra_hours: float | None = None
    completionist_hours: float | None = None
    queue_position: int | None = None
    is_free: bool | None = None
    recommendation_hidden: bool = False
    is_test_build: bool = False
    trailer_url: str | None = None
    trailer_thumbnail: str | None = None
    trailer_title: str | None = None
    trailer_format: str | None = None
    genre_names: tuple[tuple[str, str], ...] = ()



@dataclass(frozen=True)
class Recommendation:
    game_id: int
    title: str | None
    cover_url: str | None
    estimated_hours: float
    estimated_weeks: float
    estimate_source: str | None
    matched_genres: tuple[str, ...]
    queue_position: int | None
    reasons: tuple[str, ...]
    history_genres: tuple[str, ...] = ()
    history_game_count: int = 0
    trailer_url: str | None = None
    trailer_thumbnail: str | None = None
    trailer_title: str | None = None
    trailer_format: str | None = None


def rank_candidates(candidates: Iterable[Candidate],
                    preferences: RecommendationRequest) -> list[Recommendation]:
    """Return up to three distinct games; explicit genres, history, then time fit.

    Queue membership is informational, never an exclusion or scoring signal.
    Estimates must already use whole-set personal-override precedence. Never
    substitute a different completion category or round before budget checks.
    Duplicate identical inputs collapse; conflicting records fail explicitly.
    """
    preferred = frozenset(preferences.preferred_genres)
    budget = preferences.available_hours
    ranked = []
    seen = {}
    for candidate in candidates:
        if candidate.game_id in seen:
            if seen[candidate.game_id] != candidate:
                raise ValueError('Conflicting candidates for the same game')
            continue
        seen[candidate.game_id] = candidate
    # Reversible history experiment: derived only from this user's owned games.
    completed = [candidate for candidate in seen.values()
                 if candidate.completion_status == 'finished' and candidate.genre_ids
                 and not candidate.recommendation_hidden and not candidate.is_test_build]
    frequencies = Counter(genre for candidate in completed for genre in candidate.genre_ids)
    names = {}
    for game in seen.values():
        for genre_id, name in game.genre_names:
            names[genre_id] = min(names.get(genre_id, name), name)
    for candidate in seen.values():
        hours = eligible_estimate(candidate, preferences.completion_goal, include_free_games=preferences.include_free_games)
        if hours is None or hours > budget:
            continue
        weeks = hours / preferences.weekly_hours
        if not math.isfinite(weeks):
            continue
        matched = tuple(sorted(candidate.genre_ids & preferred, key=int))
        genre_fit = len(matched) / len(preferred) if preferred else 0
        history_fit = (sum(frequencies[genre] for genre in candidate.genre_ids)
                       / (len(completed) * len(candidate.genre_ids))) if completed and candidate.genre_ids else 0
        related_count = sum(bool(candidate.genre_ids & game.genre_ids) for game in completed)
        time_fit = hours / budget
        reasons = []
        if preferred:
            reasons.append(f'Matches {len(matched)} of your {len(preferred)} preferred genres.')
        if related_count:
            aligned = ', '.join(names.get(genre, f'Genre {genre}') for genre in sorted(candidate.genre_ids & frequencies.keys(), key=int))
            reasons.append(f'{aligned}: shares genres with {related_count} game{"s" if related_count != 1 else ""} you’ve finished.')
        reasons.append(f'Fits your {budget:g}-hour budget.')
        reasons.append('Weeks assume your full weekly budget is dedicated to this game.')
        recommendation = Recommendation(
            game_id=candidate.game_id, title=candidate.title, cover_url=candidate.cover_url,
            estimated_hours=hours, estimated_weeks=weeks,
            estimate_source=candidate.estimate_source, matched_genres=matched,
            queue_position=candidate.queue_position, reasons=tuple(reasons),
            history_genres=tuple(names.get(genre, f'Genre {genre}') for genre in sorted(candidate.genre_ids & frequencies.keys(), key=int)),
            history_game_count=related_count,
            **{field: getattr(candidate, field) for field in ("trailer_url", "trailer_thumbnail", "trailer_title", "trailer_format")})
        ranked.append(((-genre_fit, -history_fit, -time_fit, candidate.game_id), recommendation))
    ranked.sort(key=lambda item: item[0])
    return [recommendation for _, recommendation in ranked[preferences.offset:preferences.offset + 3]]


def eligible_estimate(candidate: Candidate, completion_goal: str, *, include_free_games: bool = False) -> float | None:
    """Shared eligibility definition for ranking and coverage before budgeting."""
    if candidate.is_test_build or candidate.recommendation_hidden or (candidate.is_free is True and not include_free_games):
        return None
    if (candidate.completion_status != 'not started' or candidate.single_player is not True
            or candidate.match_resolved is not True):
        return None
    hours = getattr(candidate, completion_goal + '_hours')
    if (isinstance(hours, bool) or not isinstance(hours, (int, float))
            or not math.isfinite(hours) or hours <= 0):
        return None
    return hours
