"""Validated recommendation preferences."""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

CompletionGoal = Literal["main_story", "main_extra", "completionist"]


class RecommendationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    include_free_games: bool = Field(default=False, strict=True)
    offset: int = Field(default=0, strict=True, ge=0, le=1000000)
    weekly_hours: float = Field(gt=0, le=168, allow_inf_nan=False)
    target_weeks: int = Field(strict=True, ge=1, le=52)
    preferred_genres: list[str] = Field(default_factory=list, max_length=20)
    completion_goal: CompletionGoal = "main_story"

    @field_validator("weekly_hours", mode="before")
    @classmethod
    def numeric_hours(cls, value):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("weekly_hours must be a number")
        return value

    @field_validator("preferred_genres")
    @classmethod
    def canonical_genres(cls, values):
        if any(not value.isascii() or not value.isdecimal() or int(value) <= 0
               or str(int(value)) != value for value in values):
            raise ValueError("genre IDs must be canonical positive integer strings")
        if len(set(values)) != len(values):
            raise ValueError("genre IDs must be unique")
        return values

    @property
    def available_hours(self) -> float:
        return self.weekly_hours * self.target_weeks
