"""HLTB candidate lookup only; matching and persistence belong to later steps."""

import asyncio
import math
from dataclasses import dataclass

from howlongtobeatpy import HowLongToBeat

from .titles import normalize_search_title


class HLTBLookupError(Exception):
    """The provider failed or returned unusable data; this is not 'no match'."""


class HLTBLookupTimeout(HLTBLookupError):
    """The lookup exceeded its overall time budget."""


@dataclass(frozen=True)
class HLTBCandidate:
    hltb_id: int
    name: str
    alias: str | None
    game_type: str | None
    platforms: tuple[str, ...]
    release_year: int | None
    main_story_hours: float | None
    main_extra_hours: float | None
    completionist_hours: float | None


def _hours(value: object) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("Invalid completion estimate")
    if not math.isfinite(value) or value < 0:
        raise ValueError("Invalid completion estimate")
    # HLTB uses zero for absent estimates. Preserve fractional hours.
    return float(value) if value > 0 else None


def _candidate(entry) -> HLTBCandidate: #validating candidate fields
    if type(entry.game_id) is not int or entry.game_id <= 0:
        raise ValueError("Invalid HLTB ID")
    if not isinstance(entry.game_name, str) or not entry.game_name.strip():
        raise ValueError("Missing HLTB title")
    platforms = entry.profile_platforms or []
    if not isinstance(platforms, list) or any(not isinstance(p, str) for p in platforms):
        raise ValueError("Invalid platforms")
    year = entry.release_world
    if year == 0:
        year = None
    if year is not None and (type(year) is not int or not 1 <= year <= 9999):
        raise ValueError("Invalid release year")
    for value in (entry.game_alias, entry.game_type):
        if value is not None and not isinstance(value, str):
            raise ValueError("Invalid candidate metadata")
    return HLTBCandidate(
        hltb_id=entry.game_id,
        name=entry.game_name.strip(),
        alias=entry.game_alias or None,
        game_type=entry.game_type or None,
        platforms=tuple(platforms),
        release_year=year,
        main_story_hours=_hours(entry.main_story),
        main_extra_hours=_hours(entry.main_extra),
        completionist_hours=_hours(entry.completionist),
    )


async def lookup_candidates(title: str, *, timeout_seconds: float = 20) -> list[HLTBCandidate]:
    """Return all provider search candidates, or raise on failure.

    An empty list means the search succeeded without results. No candidate is
    selected here. External task cancellation propagates to the caller.
    """
    query = normalize_search_title(title)
    if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
        raise ValueError("Lookup timeout must be positive and finite")
    try:
        async with asyncio.timeout(timeout_seconds):
            # Disable wrapper similarity filtering so our future matcher can
            # inspect every returned candidate. Keep original time categories.
            provider = HowLongToBeat(input_minimum_similarity=0.0, input_auto_filter_times=False)
            entries = await provider.async_search(query, similarity_case_sensitive=False)
        if entries is None:
            raise HLTBLookupError("HLTB lookup failed")
        if not isinstance(entries, list):
            raise HLTBLookupError("HLTB returned an invalid candidate list")
        return [_candidate(entry) for entry in entries] # returning all entries that match type of candidate
    except TimeoutError as error:
        raise HLTBLookupTimeout("HLTB lookup timed out") from error
    except HLTBLookupError:
        raise
    except Exception as error:
        raise HLTBLookupError("HLTB lookup failed or returned invalid data") from error
