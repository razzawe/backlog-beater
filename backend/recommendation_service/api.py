"""Authenticated read-only recommendation API."""
from dataclasses import asdict
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session
from app.core.identity import get_gateway_user_id
from app.database import get_session
from .schemas import RecommendationRequest
from .repository import load_candidates, supported_genre_ids
from .ranking import eligible_estimate, rank_candidates

router = APIRouter(prefix="/recommendations", tags=["recommendations"])


class RecommendationResponse(BaseModel):
    game_id: int
    title: str | None
    cover_url: str | None
    estimated_hours: float
    estimated_weeks: float
    estimate_source: Literal["personal", "shared"] | None
    matched_genres: list[str]
    queue_position: int | None
    reasons: list[str]
    history_genres: list[str] = []
    history_game_count: int = 0
    trailer_url: str | None = None
    trailer_thumbnail: str | None = None
    trailer_title: str | None = None
    trailer_format: str | None = None


class CoverageResponse(BaseModel):
    owned_games: int
    verified_single_player_games: int
    eligible_games_before_budget: int
    games_within_budget: int


class PaginationResponse(BaseModel):
    offset: int
    limit: int = 3
    total: int
    has_more: bool


class RecommendationsResponse(BaseModel):
    available_hours: float
    recommendations: list[RecommendationResponse]
    empty_reason: Literal["empty_library", "no_eligible_games", "no_games_within_budget"] | None
    coverage: CoverageResponse
    pagination: PaginationResponse


@router.post("", response_model=RecommendationsResponse)
def recommend(preferences: RecommendationRequest,
              user_id: int = Depends(get_gateway_user_id),
              session: Session = Depends(get_session)) -> RecommendationsResponse:
    if preferences.preferred_genres:
        unsupported = set(preferences.preferred_genres) - supported_genre_ids(session)
        if unsupported:
            raise HTTPException(422, detail={"message": "Unsupported genre IDs",
                                            "genre_ids": sorted(unsupported, key=int)})
    candidates = load_candidates(session, user_id)
    recommendations = rank_candidates(candidates, preferences)
    # Coverage uses the exact ranking eligibility helper, without the budget.
    estimates = [hours for candidate in candidates
                 if (hours := eligible_estimate(candidate, preferences.completion_goal, include_free_games=preferences.include_free_games)) is not None]
    within = sum(hours <= preferences.available_hours for hours in estimates)
    reason = None
    if not recommendations and within == 0:
        reason = ('empty_library' if not candidates else
                  'no_eligible_games' if not estimates else 'no_games_within_budget')
    return RecommendationsResponse(
        available_hours=preferences.available_hours,
        pagination=PaginationResponse(offset=preferences.offset, total=within,
            has_more=preferences.offset + 3 < within),
        recommendations=[RecommendationResponse(**asdict(item)) for item in recommendations],
        empty_reason=reason,
        coverage=CoverageResponse(owned_games=len(candidates),
            verified_single_player_games=sum(item.single_player is True for item in candidates),
            eligible_games_before_budget=len(estimates), games_within_budget=within))


@router.get("")
def preference_choices(user_id: int = Depends(get_gateway_user_id),
                       session: Session = Depends(get_session)):
    """Stored shared genre choices; no provider requests or personal information."""
    from app.database import GameMetadata
    from sqlmodel import select
    from .repository import _genre_ids
    names = {}
    for genres in session.exec(select(GameMetadata.genres)).all():
        valid = _genre_ids(genres)
        for genre in genres if isinstance(genres, list) else []:
            if (isinstance(genre, dict) and genre.get('id') in valid
                    and isinstance(genre.get('name'), str) and genre['name'].strip()):
                genre_id = genre['id']
                name = genre['name'].strip()
                names[genre_id] = min(names.get(genre_id, name), name)
    return {"genres": [{"id": key, "name": value}
                        for key, value in sorted(names.items(), key=lambda item: (item[1].casefold(), int(item[0])))]}
