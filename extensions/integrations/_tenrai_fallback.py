"""Tenrai-owned media fallback transport and response normalization.

This module contains every Tenrai-specific URL, request rule, error, and
field mapping. It translates Tenrai responses into the AniList GraphQL
response shape consumed by the shared parser.
"""

from asyncio import sleep
from datetime import UTC, datetime, time, timedelta
from typing import Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from aiohttp import ClientError, ClientSession, ClientTimeout

TENRAI_URL = "https://api.tenrai.org/v1"
MediaType = Literal["ANIME", "MANGA"]
_WEEKDAYS = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}


class TenraiError(Exception):
    """Tenrai could not return a usable fallback response."""

    def __init__(self, message: str, status: int | None = None) -> None:
        self.status = status
        super().__init__(message)


def _cover_image(item: dict[str, Any]) -> dict[str, str] | None:
    images = item.get("images")
    jpg = images.get("jpg") if isinstance(images, dict) else None
    if not isinstance(jpg, dict):
        return None
    url = jpg.get("large_image_url") or jpg.get("image_url")
    return {"large": url} if isinstance(url, str) and url else None


def _genre_names(item: dict[str, Any]) -> list[str]:
    genres = item.get("genres")
    if not isinstance(genres, list):
        return []
    return [
        name
        for genre in genres
        if isinstance(genre, dict)
        and isinstance(name := genre.get("name"), str)
        and name
    ]


def _titles(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "romaji": item.get("title"),
        "english": item.get("title_english"),
        "native": item.get("title_japanese"),
    }


async def _request(
    session: ClientSession, resource: str, params: dict[str, str]
) -> dict[str, Any]:
    retried = False
    while True:
        try:
            async with session.get(
                f"{TENRAI_URL}/{resource}",
                params=params,
                timeout=ClientTimeout(total=10),
            ) as response:
                if response.status == 429 and not retried:
                    retry_after = response.headers.get("Retry-After", "1")
                else:
                    if not 200 <= response.status < 300:
                        raise TenraiError(
                            f"Tenrai returned HTTP {response.status}.",
                            response.status,
                        )
                    try:
                        payload = await response.json(content_type=None)
                    except (TypeError, ValueError) as error:
                        raise TenraiError("Tenrai returned invalid JSON.") from error
                    if not isinstance(payload, dict):
                        raise TenraiError("Tenrai returned an unexpected response.")
                    return payload
        except (ClientError, TimeoutError) as error:
            raise TenraiError("Could not reach Tenrai.") from error

        retried = True
        try:
            delay = max(float(retry_after), 0)
        except TypeError, ValueError:
            delay = 1
        await sleep(delay)


def _broadcast_timestamp(broadcast: Any, week_start: int, week_end: int) -> int | None:
    """Resolve a recurring Tenrai broadcast into the requested UTC week."""
    if not isinstance(broadcast, dict):
        return None
    day = broadcast.get("day")
    broadcast_time = broadcast.get("time")
    timezone_name = broadcast.get("timezone")
    if not all(
        isinstance(value, str) for value in (day, broadcast_time, timezone_name)
    ):
        return None

    weekday = _WEEKDAYS.get(day.casefold().removesuffix("s"))
    if weekday is None:
        return None
    try:
        parsed_time = time.fromisoformat(broadcast_time)
        timezone = ZoneInfo(timezone_name)
    except ValueError, ZoneInfoNotFoundError:
        return None

    local_start = datetime.fromtimestamp(week_start, UTC).astimezone(timezone)
    broadcast_date = local_start.date() + timedelta(
        days=(weekday - local_start.weekday()) % 7
    )
    candidate = datetime.combine(broadcast_date, parsed_time, timezone)
    if candidate.timestamp() < week_start:
        candidate += timedelta(days=7)
    timestamp = int(candidate.timestamp())
    return timestamp if timestamp < week_end else None


async def weekly_schedule(
    session: ClientSession, week_start: int, week_end: int
) -> list[dict[str, Any]]:
    """Return Tenrai's weekly broadcasts in the shared schedule shape."""
    page = 1
    results: list[dict[str, Any]] = []
    while True:
        payload = await _request(
            session,
            "schedules",
            {"page": str(page), "limit": "50", "sfw": "true"},
        )
        data = payload.get("data")
        pagination = payload.get("pagination")
        if not isinstance(data, list) or not isinstance(pagination, dict):
            raise TenraiError("Tenrai returned an unexpected response.")

        for item in data:
            if not isinstance(item, dict):
                raise TenraiError("Tenrai returned an unexpected response.")
            results.append(
                {
                    "_provider": "Tenrai",
                    "airingAt": _broadcast_timestamp(
                        item.get("broadcast"), week_start, week_end
                    ),
                    "episode": None,
                    "media": {"title": _titles(item)},
                }
            )

        if pagination.get("has_next_page") is not True:
            return results
        page += 1


async def search_media(
    session: ClientSession,
    query: str,
    media_type: MediaType,
    limit: int,
) -> dict[str, Any]:
    """Search Tenrai and translate its records into an AniList Page response."""
    resource = media_type.lower()
    payload = await _request(
        session, resource, {"q": query, "limit": str(limit), "sfw": "true"}
    )

    data = payload.get("data")
    if not isinstance(data, list):
        raise TenraiError("Tenrai returned an unexpected response.")

    results: list[dict[str, Any]] = []
    for item in data:
        if not isinstance(item, dict):
            raise TenraiError("Tenrai returned an unexpected response.")
        try:
            raw_score = float(item["score"])
            score = round(raw_score * 10) if 0 < raw_score <= 10 else None
        except KeyError, TypeError, ValueError:
            score = None
        results.append(
            {
                "_provider": "Tenrai",
                "title": _titles(item),
                "siteUrl": item.get("url"),
                "description": item.get("synopsis"),
                "coverImage": _cover_image(item),
                "bannerImage": None,
                "format": item.get("type"),
                "status": item.get("status"),
                "episodes": item.get("episodes"),
                "chapters": item.get("chapters"),
                "volumes": item.get("volumes"),
                "averageScore": score,
                "genres": _genre_names(item),
            }
        )
    return {"data": {"Page": {"media": results}}}
