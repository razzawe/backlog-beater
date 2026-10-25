"""One Steam Store lookup; no database, dispatch, pacing, or retry side effects."""
import asyncio
import math
from urllib.parse import urlsplit
from dataclasses import dataclass

import httpx

STORE_URL = "https://store.steampowered.com/api/appdetails"
SINGLE_PLAYER_CATEGORY = 2


class SteamMetadataError(Exception):
    """A failed lookup is not evidence of multiplayer-only support."""


class SteamMetadataUnavailable(SteamMetadataError):
    """Steam explicitly reports this app's Store details unavailable."""


class SteamMetadataTimeout(SteamMetadataError):
    """The lookup exceeded its time budget."""


@dataclass(frozen=True)
class SteamGenre:
    id: str
    name: str


@dataclass(frozen=True)
class SteamMetadata:
    single_player: bool | None
    genres: tuple[SteamGenre, ...]
    header_image: str | None = None
    multiplayer: bool | None = None
    is_free: bool | None = None
    trailer_url: str | None = None
    trailer_thumbnail: str | None = None
    trailer_title: str | None = None
    trailer_format: str | None = None


def _app_id(app_id):
    if type(app_id) is not int or app_id <= 0:
        raise ValueError("Expected a positive Steam app ID")


def parse_metadata(app_id: int, payload: object) -> SteamMetadata:
    """Validate identity and fields; absent categories leave game mode unknown.

    A valid empty category list means no declared single-player support. Missing
    genres are not accepted as a successful empty list. Invalid data raises a
    lookup error so future persistence cannot overwrite verified metadata.
    """
    _app_id(app_id)
    if not isinstance(payload, dict):
        raise SteamMetadataError("invalid_response")
    entry = payload.get(str(app_id))
    if not isinstance(entry, dict) or type(entry.get("success")) is not bool:
        raise SteamMetadataError("invalid_response")
    if not entry["success"]:
        raise SteamMetadataUnavailable("app_unavailable")
    data = entry.get("data")
    if (not isinstance(data, dict) or type(data.get("steam_appid")) is not int
            or data["steam_appid"] != app_id):
        raise SteamMetadataError("invalid_app_identity")
    raw_genres = data.get("genres")
    if not isinstance(raw_genres, list):
        raise SteamMetadataError("invalid_genres")
    genres = {}
    for genre in raw_genres:
        if not isinstance(genre, dict):
            raise SteamMetadataError("invalid_genres")
        genre_id, name = genre.get("id"), genre.get("description")
        if (not isinstance(genre_id, str) or not genre_id.isascii()
                or not genre_id.isdecimal() or int(genre_id) <= 0
                or not isinstance(name, str) or not name.strip()):
            raise SteamMetadataError("invalid_genres")
        genre_id = str(int(genre_id))
        name = " ".join(name.split())
        if genre_id in genres and genres[genre_id] != name:
            raise SteamMetadataError("conflicting_genres")
        genres[genre_id] = name
    single_player = None
    multiplayer = None
    if "categories" in data:
        categories = data["categories"]
        if not isinstance(categories, list) or any(
            not isinstance(item, dict) or type(item.get("id")) is not int
            or item["id"] <= 0 for item in categories
        ):
            raise SteamMetadataError("invalid_categories")
        single_player = any(item["id"] == SINGLE_PLAYER_CATEGORY for item in categories)
        multiplayer = any(item["id"] == 1 for item in categories)
    header_image = validated_header_image(data.get("header_image"))
    trailer = select_trailer(data.get("movies"))
    return SteamMetadata(single_player, tuple(
        SteamGenre(key, genres[key]) for key in sorted(genres, key=int)), header_image, multiplayer, data.get("is_free") if type(data.get("is_free")) is bool else None, **trailer)


async def lookup_metadata(app_id: int, *, timeout_seconds: float = 10,
                          transport: httpx.AsyncBaseTransport | None = None) -> SteamMetadata:
    """Perform one bounded request. Scheduling controls retries and provider pacing."""
    _app_id(app_id)
    if (isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float))
            or not math.isfinite(timeout_seconds) or timeout_seconds <= 0):
        raise ValueError("Expected a finite positive timeout")
    try:
        async with asyncio.timeout(timeout_seconds):
            async with httpx.AsyncClient(timeout=timeout_seconds, follow_redirects=False,
                                         transport=transport) as client:
                response = await client.get(STORE_URL, params={"appids": app_id, "l": "english"})
                response.raise_for_status()
                payload = response.json()
        return parse_metadata(app_id, payload)
    except (TimeoutError, httpx.TimeoutException) as error:
        raise SteamMetadataTimeout("lookup_timeout") from error
    except httpx.HTTPError as error:
        raise SteamMetadataError("request_failed") from error
    except ValueError as error:
        raise SteamMetadataError("invalid_json") from error


def validated_header_image(value):
    """Ignore unusable optional artwork without discarding valid game metadata."""
    if not isinstance(value, str) or not value or any(c.isspace() for c in value):
        return None
    try:
        url = urlsplit(value)
        allowed = {"cdn.akamai.steamstatic.com", "shared.akamai.steamstatic.com",
                   "cdn.cloudflare.steamstatic.com", "shared.cloudflare.steamstatic.com"}
        if (url.scheme != "https" or url.hostname not in allowed or url.username
                or url.password or url.port not in (None, 443) or not url.path
                or url.fragment):
            return None
    except ValueError:
        return None
    return value


def select_trailer(movies):
    """Optional trailers never turn otherwise valid metadata into a failure."""
    if not isinstance(movies, list):
        return {}
    choices = sorted((movie for movie in movies if isinstance(movie, dict)),
                     key=lambda movie: movie.get('highlight') is not True)
    for movie in choices:
        candidates = []
        mp4 = movie.get('mp4')
        if isinstance(mp4, dict):
            candidates.append(('mp4', mp4.get('480') or mp4.get('max')))
        candidates.append(('hls', movie.get('hls_h264')))
        for format, url in candidates:
            if not isinstance(url, str):
                continue
            try:
                parsed = urlsplit(url)
                if (parsed.scheme != 'https' or parsed.hostname not in {
                    'video.akamai.steamstatic.com', 'video.cloudflare.steamstatic.com',
                    'cdn.akamai.steamstatic.com', 'cdn.cloudflare.steamstatic.com'
                } or parsed.username or parsed.password or parsed.port not in (None, 443)
                    or parsed.fragment or any(c.isspace() for c in url)
                    or not parsed.path.lower().endswith('.m3u8' if format == 'hls' else '.mp4')):
                    continue
            except ValueError:
                continue
            name = movie.get('name')
            return {'trailer_url': url, 'trailer_format': format,
                'trailer_thumbnail': validated_header_image(movie.get('thumbnail')),
                'trailer_title': name.strip() if isinstance(name, str) and name.strip() else None}
    return {}
