"""HLTB candidate lookup only; matching and persistence belong to later steps."""

import asyncio
import aiohttp
from email.utils import parsedate_to_datetime
from datetime import UTC, datetime
import math
import unicodedata
from dataclasses import dataclass

from howlongtobeatpy import HowLongToBeat


class HLTBLookupError(Exception):
    """The provider failed or returned unusable data; this is not 'no match'."""


class HLTBTemporaryError(HLTBLookupError):
    def __init__(self, message, *, retry_after=0):
        super().__init__(message)
        self.retry_after = retry_after


class HLTBLookupTimeout(HLTBTemporaryError):
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


def _candidate(entry) -> HLTBCandidate:
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


def normalize_search_title(title: str) -> str:
    """Clean presentation symbols without changing title wording or case."""
    if not isinstance(title, str):
        raise ValueError("A nonempty game title is required")
    title = unicodedata.normalize("NFC", title).translate(str.maketrans("", "", "\u2122\u00ae\u00a9"))
    title = " ".join(title.split())
    if not title:
        raise ValueError("A nonempty game title is required")
    return title


async def lookup_candidates(title: str, *, timeout_seconds: float = 20) -> list[HLTBCandidate]:
    """Return all provider search candidates, or raise on failure.

    An empty list means the search succeeded without results. No candidate is
    selected here. External task cancellation propagates to the caller.
    """
    title = normalize_search_title(title)
    if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
        raise ValueError("Lookup timeout must be positive and finite")
    try:
        async with asyncio.timeout(timeout_seconds):
            # Disable wrapper similarity filtering so our future matcher can
            # inspect every returned candidate. Keep original time categories.
            provider = HowLongToBeat(input_minimum_similarity=0.0, input_auto_filter_times=False)
            entries = await provider.async_search(title.strip(), similarity_case_sensitive=False)
        if entries is None:
            raise HLTBLookupError("HLTB lookup failed")
        if not isinstance(entries, list):
            raise HLTBLookupError("HLTB returned an invalid candidate list")
        return [_candidate(entry) for entry in entries]
    except TimeoutError as error:
        raise HLTBLookupTimeout("HLTB lookup timed out") from error
    except aiohttp.ClientResponseError as error:
        if error.status == 429 or 500 <= error.status <= 599:
            delay = 0
            raw = (error.headers or {}).get("Retry-After")
            if raw:
                try:
                    delay = max(0, float(raw))
                except ValueError:
                    try:
                        delay = max(0, (parsedate_to_datetime(raw) - datetime.now(UTC)).total_seconds())
                    except (ValueError, TypeError, OverflowError):
                        pass
            raise HLTBTemporaryError("HLTB temporarily unavailable", retry_after=delay) from error
        raise HLTBLookupError("HLTB request rejected") from error
    except aiohttp.ClientConnectionError as error:
        raise HLTBTemporaryError("HLTB connection failed") from error
    except HLTBLookupError:
        raise
    except Exception as error:
        raise HLTBLookupError("HLTB lookup failed or returned invalid data") from error
