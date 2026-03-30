from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime


class BacklogItem(BaseModel):
    game_id: int
    status: str = "not_started"
    hours_played: float = 0
    progress_percent: float = 0


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
    estimated_playtime: Optional[int]
    metacritic_score: Optional[int]


class SteamGameItem(BaseModel):
    game_id: int
    hours_played: float = 0
    progress_percent: float = 0
    last_interacted_at: Optional[datetime] = None


class SteamImportRequest(BaseModel):
    games: List[SteamGameItem]