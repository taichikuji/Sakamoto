import sys
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiohttp import ClientError

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from extensions.integrations import _tenrai_fallback as tenrai


class DummyResponse:
    def __init__(self, payload=None, *, status=200, headers=None, json_error=None):
        self.payload = payload
        self.status = status
        self.headers = headers or {}
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
async def test_weekly_schedule_paginates_and_normalizes_broadcast_times():
    monday = {
        "title": "Weekly Anime",
        "title_english": "Weekly Anime",
        "title_japanese": "週間アニメ",
        "broadcast": {
            "day": "Mondays",
            "time": "22:00",
            "timezone": "Asia/Tokyo",
        },
    }
    unknown = {
        "title": "TBA Anime",
        "broadcast": {"day": None, "time": None, "timezone": None},
    }
    session = SimpleNamespace(
        get=MagicMock(
            side_effect=[
                DummyResponse(
                    {
                        "pagination": {"has_next_page": True},
                        "data": [monday],
                    }
                ),
                DummyResponse(
                    {
                        "pagination": {"has_next_page": False},
                        "data": [unknown],
                    }
                ),
            ]
        )
    )
    week_start = int(datetime(2026, 9, 7, tzinfo=UTC).timestamp())
    week_end = int(datetime(2026, 9, 14, tzinfo=UTC).timestamp())
    results = await tenrai.weekly_schedule(session, week_start, week_end)

    assert [result["airingAt"] for result in results] == [
        int(datetime(2026, 9, 7, 13, tzinfo=UTC).timestamp()),
        None,
    ]
    assert results[0]["media"]["title"] == {
        "romaji": "Weekly Anime",
        "english": "Weekly Anime",
        "native": "週間アニメ",
    }
    assert results[1]["media"]["title"]["romaji"] == "TBA Anime"
    assert all(result["_provider"] == "Tenrai" for result in results)
    assert all(result["episode"] is None for result in results)
    assert [call.kwargs["params"] for call in session.get.call_args_list] == [
        {"page": "1", "limit": "50", "sfw": "true"},
        {"page": "2", "limit": "50", "sfw": "true"},
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        {"pagination": {}, "data": {}},
        {"pagination": {}, "data": [None]},
        {"data": []},
    ],
)
async def test_weekly_schedule_rejects_malformed_response(payload):
    session = SimpleNamespace(get=MagicMock(return_value=DummyResponse(payload)))

    with pytest.raises(tenrai.TenraiError, match="unexpected response"):
        await tenrai.weekly_schedule(session, 0, 604800)


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
async def test_search_characters_normalizes_tenrai_records():
    item = {
        "mal_id": 40,
        "url": "https://myanimelist.net/character/40/Luffy_Monkey_D_",
        "images": {
            "jpg": {"image_url": "https://example.test/luffy.jpg"},
        },
        "name": "Monkey D., Luffy",
        "name_kanji": "モンキー・D・ルフィ",
        "favorites": 149815,
        "about": "Captain of the Straw Hats.\n\n[Spoiler]Secret.[/Spoiler]",
    }
    session = SimpleNamespace(
        get=MagicMock(return_value=DummyResponse({"data": [item]}))
    )

    payload = await tenrai.search_characters(session, "Luffy", 5)

    assert payload == {
        "data": {
            "Page": {
                "characters": [
                    {
                        "_provider": "Tenrai",
                        "name": {
                            "full": "Monkey D., Luffy",
                            "native": "モンキー・D・ルフィ",
                        },
                        "siteUrl": (
                            "https://myanimelist.net/character/40/Luffy_Monkey_D_"
                        ),
                        "description": "Captain of the Straw Hats.\n\n~!Secret.!~",
                        "image": {"large": "https://example.test/luffy.jpg"},
                        "gender": None,
                        "age": None,
                        "favourites": 149815,
                    }
                ]
            }
        }
    }
    request = session.get.call_args
    assert request.args == (f"{tenrai.TENRAI_URL}/characters",)
    assert request.kwargs["params"] == {"q": "Luffy", "limit": "5"}
    assert request.kwargs["timeout"].total == 10


@pytest.mark.asyncio
async def test_search_characters_tolerates_missing_optional_data():
    session = SimpleNamespace(get=MagicMock(return_value=DummyResponse({"data": [{}]})))

    payload = await tenrai.search_characters(session, "Unknown", 5)
    [result] = payload["data"]["Page"]["characters"]

    assert result["image"] is None
    assert result["description"] is None
    assert result["favourites"] is None
    assert result["gender"] is None
    assert result["age"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [{"data": {}}, {"data": [None]}, None])
async def test_search_characters_rejects_malformed_response(payload):
    session = SimpleNamespace(get=MagicMock(return_value=DummyResponse(payload)))

    with pytest.raises(tenrai.TenraiError, match="unexpected response"):
        await tenrai.search_characters(session, "Luffy", 5)


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
async def test_search_media_retries_one_rate_limit(monkeypatch):
    session = SimpleNamespace(
        get=MagicMock(
            side_effect=[
                DummyResponse(status=429, headers={"Retry-After": "2"}),
                DummyResponse({"data": []}),
            ]
        )
    )
    wait = AsyncMock()
    monkeypatch.setattr(tenrai, "sleep", wait)

    assert await tenrai.search_media(session, "Berserk", "MANGA", 5) == {
        "data": {"Page": {"media": []}}
    }
    wait.assert_awaited_once_with(2)


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
