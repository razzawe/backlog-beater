from pydantic import BaseModel

class BacklogItem(BaseModel):
    game_id: int
    status: str = "not_started"
    hours_played: float = 0
    progress_percent: int = 0
