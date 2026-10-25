from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, StrictBool
from sqlmodel import Session, select

from app.core.identity import get_gateway_user_id
from app.database import CompletionStatus, EnrichmentStatus, Game, GameEnrichment, OwnedGame, PersonalHLTBOverride, get_session
from app.database import PlayQueueEntry, GameMetadata
from library_service.queue import lock_owner
from app.catalog_filters import effective_free_flag, is_test_build


# Group endpoints for the authenticated user's stored library.
games_router = APIRouter(prefix="/games", tags=["games"])


class OwnedGameResponse(BaseModel):
    # Expose catalog identifiers without internal ownership records.
    id: int
    steam_app_id: int
    name: str | None
    cover_url: str | None
    completion_status: CompletionStatus
    recommendation_hidden: bool = False
    single_player: bool | None = None
    multiplayer: bool | None = None
    is_free: bool | None = None
    is_test_build: bool = False
    hltb_id: int | None = None
    main_story_hours: float | None = None
    main_extra_hours: float | None = None
    completionist_hours: float | None = None
    hltb_source: str | None = None
    hltb_review_needed: bool = False
    enrichment_status: EnrichmentStatus | None = None
    enrichment_last_lookup_at: datetime | None = None


def _response(game: Game, ownership: OwnedGame, override: PersonalHLTBOverride | None,
              enrichment: GameEnrichment | None = None,
              game_metadata: GameMetadata | None = None) -> OwnedGameResponse:
    # Choose the entire estimate set; missing personal values must not fall
    # back to a different shared HLTB entry's estimates.
    metadata = override if override is not None else game
    return OwnedGameResponse(
        recommendation_hidden=ownership.recommendation_hidden,
        id=game.id, steam_app_id=game.steam_app_id, name=game.name,
        single_player=game_metadata.single_player if game_metadata else None,
        multiplayer=game_metadata.multiplayer if game_metadata else None,
        is_free=effective_free_flag(game_metadata.is_free, game_metadata.genres) if game_metadata else None,
        is_test_build=is_test_build(game.name),
        cover_url=game.cover_url, completion_status=ownership.completion_status,
        hltb_id=metadata.hltb_id, main_story_hours=metadata.main_story_hours,
        main_extra_hours=metadata.main_extra_hours,
        completionist_hours=metadata.completionist_hours,
        hltb_source=("personal" if override is not None else "shared") if metadata.hltb_id else None,
        hltb_review_needed=override is None and enrichment is not None and enrichment.status == EnrichmentStatus.AMBIGUOUS,
        enrichment_status=enrichment.status if enrichment else None,
        enrichment_last_lookup_at=enrichment.last_lookup_at if enrichment else None,
    )


class UpdateCompletionStatusRequest(BaseModel):
    completion_status: CompletionStatus


@games_router.patch("/{game_id}", response_model=OwnedGameResponse)
def update_completion_status(
    game_id: int,
    body: UpdateCompletionStatusRequest,
    user_id: int = Depends(get_gateway_user_id),
    session: Session = Depends(get_session),
) -> OwnedGameResponse:
    """Update completion status for a game owned by the authenticated user."""
    lock_owner(session, user_id)
    ownership = session.get(OwnedGame, (user_id, game_id))
    if ownership is None:
        raise HTTPException(status_code=404, detail="Owned game not found")

    game = session.get(Game, game_id)
    ownership.completion_status = body.completion_status
    session.add(ownership)
    if body.completion_status == CompletionStatus.IN_PROGRESS:
        queued = session.get(PlayQueueEntry, (user_id, game_id))
        if queued is not None:
            session.delete(queued)
    session.commit()
    override = session.get(PersonalHLTBOverride, (user_id, game_id))
    return _response(game, ownership, override, session.get(GameEnrichment, game_id), session.get(GameMetadata, game_id))


@games_router.get("", response_model=list[OwnedGameResponse])
def get_owned_games(
    user_id: int = Depends(get_gateway_user_id),
    session: Session = Depends(get_session),
) -> list[OwnedGameResponse]:
    """Return the authenticated user's stored game library.
    List catalog identifiers in stable order, or an empty list if none exist."""
    # Select only catalog games owned by the authenticated user in stable order.
    rows = session.exec(
        select(Game, OwnedGame, PersonalHLTBOverride, GameEnrichment, GameMetadata)
        .join(OwnedGame, OwnedGame.game_id == Game.id)
        .outerjoin(PersonalHLTBOverride, (PersonalHLTBOverride.game_id == Game.id)
                   & (PersonalHLTBOverride.user_id == OwnedGame.user_id))
        .outerjoin(GameEnrichment, GameEnrichment.game_id == Game.id)
        .outerjoin(GameMetadata, GameMetadata.game_id == Game.id)
        .where(OwnedGame.user_id == user_id)
        .order_by(Game.id)
    ).all()

    # Return an empty list when the user has no stored ownership links.
    return [
        _response(game, ownership, override, enrichment, game_metadata)
        for game, ownership, override, enrichment, game_metadata in rows
    ]


class RecommendationPreferenceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    hidden: StrictBool


@games_router.put("/{game_id}/recommendation-preference")
def set_recommendation_preference(game_id: int, body: RecommendationPreferenceRequest,
        user_id: int = Depends(get_gateway_user_id), session: Session = Depends(get_session)):
    lock_owner(session, user_id)
    ownership = session.get(OwnedGame, (user_id, game_id))
    if ownership is None:
        raise HTTPException(404, "Owned game not found")
    ownership.recommendation_hidden = body.hidden
    session.add(ownership)
    session.commit()
    return {"game_id": game_id, "hidden": ownership.recommendation_hidden}
