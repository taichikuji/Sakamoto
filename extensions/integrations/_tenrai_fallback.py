"""Tenrai-owned media fallback transport and response normalization.

This module contains every Tenrai-specific URL, request rule, error, and
field mapping. It translates Tenrai responses into the AniList GraphQL
response shape consumed by the shared parser.
"""

from typing import Any, Literal

from aiohttp import ClientError, ClientSession, ClientTimeout

TENRAI_URL = "https://api.tenrai.org/v1"
MediaType = Literal["ANIME", "MANGA"]


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


async def search_media(
    session: ClientSession,
    query: str,
    media_type: MediaType,
    limit: int,
) -> dict[str, Any]:
    """Search Tenrai and translate its records into an AniList Page response."""
    resource = media_type.lower()
    try:
        async with session.get(
            f"{TENRAI_URL}/{resource}",
            params={"q": query, "limit": str(limit), "sfw": "true"},
            timeout=ClientTimeout(total=10),
        ) as response:
            if not 200 <= response.status < 300:
                raise TenraiError(
                    f"Tenrai returned HTTP {response.status}.", response.status
                )
            try:
                payload = await response.json(content_type=None)
            except (TypeError, ValueError) as error:
                raise TenraiError("Tenrai returned invalid JSON.") from error
    except (ClientError, TimeoutError) as error:
        raise TenraiError("Could not reach Tenrai.") from error

    data = payload.get("data") if isinstance(payload, dict) else None
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
                "title": {
                    "romaji": item.get("title"),
                    "english": item.get("title_english"),
                    "native": item.get("title_japanese"),
                },
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
