from pydantic import BaseModel
from datetime import datetime

class BacklogItem(BaseModel):
    game_id: int
    status: str = "not_started"
    hours_played: float = 0
    progress_percent: int = 0


class BacklogItemResponse(BaseModel):
    id: int
    user_id: int
    game_id: int
    status: str
    hours_played: float
    progress_percent: float
    last_interacted_at: datetime
    added_at: datetime