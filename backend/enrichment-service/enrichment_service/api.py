"""Ownership-protected candidate review and personal match selection."""

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlmodel import Session

from app.core.identity import get_gateway_user_id
from app.database import EnrichmentStatus, GameEnrichment, OwnedGame, get_session
from redis.exceptions import RedisError

from .storage import save_cached_override
from .review_cache import get_review
from .review_dispatch import request_refresh

review_router = APIRouter(prefix="/enrichment/games", tags=["enrichment"])


class HLTBSelectionResponse(BaseModel):
    id: int
    hltb_id: int
    main_story_hours: float | None
    main_extra_hours: float | None
    completionist_hours: float | None
    hltb_source: str = "personal"
    hltb_review_needed: bool = False


class HLTBReviewResponse(BaseModel):
    game_id: int
    status: str
    reason: str | None = None
    cache_version: str | None = None
    candidates: list[dict] = Field(default_factory=list)


class HLTBSelectionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    hltb_id: int = Field(strict=True, gt=0)
    cache_version: str = Field(min_length=1, max_length=128)


@review_router.get("/{game_id}/hltb-review", response_model=HLTBReviewResponse)
def get_hltb_review(game_id: int, response: Response,
                    user_id: int = Depends(get_gateway_user_id),
                    session: Session = Depends(get_session)):
    response.headers["Cache-Control"] = "no-store"
    if session.get(OwnedGame, (user_id, game_id)) is None:
        raise HTTPException(404, "Owned game not found")
    record = session.get(GameEnrichment, game_id)
    if record is None:
        raise HTTPException(409, "Game has no ambiguous match to resolve")
    try:
        cached = get_review(game_id)
        if cached and cached.get("status") == "lookup_failed" and record.status == EnrichmentStatus.AMBIGUOUS:
            return HLTBReviewResponse(game_id=game_id, status="lookup_failed",
                                     reason="Suggestions could not be refreshed. Please try again in a minute.")
        if (cached and record.last_success_at is not None
                and cached.get("last_success_at") == record.last_success_at.isoformat()):
            return HLTBReviewResponse(game_id=game_id, status=cached["status"],
                reason=cached.get("reason"), cache_version=cached["version"], candidates=cached["candidates"])
        if record.status != EnrichmentStatus.AMBIGUOUS:
            raise HTTPException(409, "Game has no ambiguous match to resolve")
        # End the read transaction before contacting Redis and the broker.
        session.rollback()
        request_refresh(game_id)
    except RedisError:
        raise HTTPException(503, "Match suggestions are temporarily unavailable; please retry")
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(503, "Suggestions could not be queued for refresh; please retry")
    response.status_code = 202
    response.headers["Retry-After"] = "3"
    return HLTBReviewResponse(game_id=game_id, status="refreshing")


@review_router.put("/{game_id}/hltb-selection", response_model=HLTBSelectionResponse)
def select_hltb_match(game_id: int, body: HLTBSelectionRequest,
                      user_id: int = Depends(get_gateway_user_id),
                      session: Session = Depends(get_session)):
    # Check ownership first so unknown and other users' games behave identically.
    if session.get(OwnedGame, (user_id, game_id)) is None:
        raise HTTPException(404, "Owned game not found")
    try:
        override = save_cached_override(session, user_id, game_id,
                                        hltb_id=body.hltb_id, cache_version=body.cache_version)
        session.commit()
    except RedisError:
        session.rollback()
        raise HTTPException(503, "Match suggestions are temporarily unavailable; please retry")
    except PermissionError as error:
        session.rollback()
        raise HTTPException(404, "Owned game not found") from error
    except ValueError as error:
        session.rollback()
        raise HTTPException(409, str(error)) from error
    return HLTBSelectionResponse(
        id=game_id, hltb_id=override.hltb_id, main_story_hours=override.main_story_hours,
        main_extra_hours=override.main_extra_hours, completionist_hours=override.completionist_hours,
    )
