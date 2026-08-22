from pydantic import BaseModel, Field
from typing import List, Literal, Optional
from datetime import datetime


class BacklogItem(BaseModel):
    game_id: int
    status: Literal["not_started", "in_progress", "completed"] = "not_started"
    hours_played: float = Field(default=0, ge=0)
    progress_percent: float = Field(default=0, ge=0, le=100)


class BacklogItemResponse(BaseModel):
    id: int
    user_id: int
    game_id: int
    status: str
    hours_played: float
    progress_percent: float
    last_interacted_at: datetime
    added_at: datetime
    title: str
    cover_url: Optional[str]
    genres: Optional[List[str]]
    completion_source: Optional[str]
    main_story_hours: Optional[float]
    main_plus_extras_hours: Optional[float]
    completionist_hours: Optional[float]
    completion_match_confidence: Optional[float]


class SteamGameItem(BaseModel):
    game_id: int
    hours_played: float = Field(default=0, ge=0)
    progress_percent: float = Field(default=0, ge=0, le=100)
    last_interacted_at: Optional[datetime] = None


class SteamImportRequest(BaseModel):
    games: List[SteamGameItem]
