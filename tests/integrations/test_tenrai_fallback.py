import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from aiohttp import ClientError

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from extensions.integrations import _tenrai_fallback as tenrai


class DummyResponse:
    def __init__(self, payload=None, *, status=200, json_error=None):
        self.payload = payload
        self.status = status
        self.json_error = json_error

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def json(self, **_kwargs):
        if self.json_error is not None:
            raise self.json_error
        return self.payload


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("media_type", "resource", "counts"),
    [
        ("ANIME", "anime", {"episodes": 26}),
        ("MANGA", "manga", {"chapters": 380, "volumes": 42}),
    ],
)
async def test_search_media_normalizes_tenrai_records(media_type, resource, counts):
    item = {
        "mal_id": 1,
        "url": f"https://myanimelist.net/{resource}/1/Cowboy_Bebop",
        "title": "Cowboy Bebop",
        "title_english": "Cowboy Bebop",
        "title_japanese": "カウボーイビバップ",
        "images": {
            "jpg": {
                "image_url": "https://example.test/poster.jpg",
                "large_image_url": "https://example.test/poster-large.jpg",
            }
        },
        "type": "TV" if media_type == "ANIME" else "Manga",
        "status": "Finished Airing" if media_type == "ANIME" else "Finished",
        "score": 8.23,
        "synopsis": "Space bounty hunters.",
        "genres": [
            {"name": "Action"},
            {"name": "Sci-Fi"},
            {"name": ""},
            {"id": 1},
        ],
        **counts,
    }
    session = SimpleNamespace(
        get=MagicMock(return_value=DummyResponse({"data": [item]}))
    )

    payload = await tenrai.search_media(session, "Cowboy Bebop", media_type, 5)
    [result] = payload["data"]["Page"]["media"]

    assert result == {
        "_provider": "Tenrai",
        "title": {
            "romaji": "Cowboy Bebop",
            "english": "Cowboy Bebop",
            "native": "カウボーイビバップ",
        },
        "siteUrl": f"https://myanimelist.net/{resource}/1/Cowboy_Bebop",
        "description": "Space bounty hunters.",
        "coverImage": {"large": "https://example.test/poster-large.jpg"},
        "bannerImage": None,
        "format": "TV" if media_type == "ANIME" else "Manga",
        "status": "Finished Airing" if media_type == "ANIME" else "Finished",
        "episodes": counts.get("episodes"),
        "chapters": counts.get("chapters"),
        "volumes": counts.get("volumes"),
        "averageScore": 82,
        "genres": ["Action", "Sci-Fi"],
    }
    request = session.get.call_args
    assert request.args == (f"{tenrai.TENRAI_URL}/{resource}",)
    assert request.kwargs["params"] == {
        "q": "Cowboy Bebop",
        "limit": "5",
        "sfw": "true",
    }
    assert request.kwargs["timeout"].total == 10


@pytest.mark.asyncio
@pytest.mark.parametrize("score", [0, None, "unknown", 11, "inf"])
async def test_search_media_tolerates_missing_optional_data(score):
    session = SimpleNamespace(
        get=MagicMock(
            return_value=DummyResponse(
                {"data": [{"images": {}, "genres": None, "score": score}]}
            )
        )
    )

    payload = await tenrai.search_media(session, "Berserk", "MANGA", 5)
    [result] = payload["data"]["Page"]["media"]

    assert result["averageScore"] is None
    assert result["coverImage"] is None
    assert result["genres"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [{"data": {}}, {"data": [None]}, None])
async def test_search_media_rejects_malformed_response(payload):
    session = SimpleNamespace(get=MagicMock(return_value=DummyResponse(payload)))

    with pytest.raises(tenrai.TenraiError, match="unexpected response"):
        await tenrai.search_media(session, "Berserk", "MANGA", 5)


@pytest.mark.asyncio
async def test_search_media_preserves_http_status():
    session = SimpleNamespace(get=MagicMock(return_value=DummyResponse(status=429)))

    with pytest.raises(tenrai.TenraiError, match="HTTP 429") as raised:
        await tenrai.search_media(session, "Berserk", "MANGA", 5)
    assert raised.value.status == 429


@pytest.mark.asyncio
async def test_search_media_rejects_invalid_json():
    session = SimpleNamespace(
        get=MagicMock(return_value=DummyResponse(json_error=ValueError()))
    )

    with pytest.raises(tenrai.TenraiError, match="invalid JSON"):
        await tenrai.search_media(session, "Berserk", "MANGA", 5)


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [ClientError(), TimeoutError()])
async def test_search_media_wraps_transport_errors(error):
    session = SimpleNamespace(get=MagicMock(side_effect=error))

    with pytest.raises(tenrai.TenraiError, match="Could not reach Tenrai"):
        await tenrai.search_media(session, "Berserk", "MANGA", 5)
