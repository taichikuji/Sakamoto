import asyncio
import sys
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiohttp import ClientError

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from extensions.integrations import _tenrai_fallback as tenrai_fallback
from extensions.integrations import anilist

MANGA = {
    "title": {"romaji": "Berserk", "english": "Berserk"},
    "siteUrl": "https://anilist.co/manga/30002",
    "description": (
        "A lone swordsman seeks revenge.<br><br>(Source: Dark Horse)"
        "<br><br><i>Notes:</i><br>Still publishing."
    ),
    "coverImage": {"large": "https://example.test/berserk.jpg"},
    "bannerImage": "https://example.test/berserk-banner.jpg",
    "format": "MANGA",
    "status": "FINISHED",
    "chapters": 380,
    "volumes": 42,
    "averageScore": 90,
    "genres": ["Action", "Drama", "Fantasy"],
}

ANIME = {
    **MANGA,
    "title": {"romaji": "Cowboy Bebop", "english": "Cowboy Bebop"},
    "siteUrl": "https://anilist.co/anime/1",
    "format": "TV",
    "episodes": 26,
}

TENRAI_ANIME = {
    "title": "Cowboy Bebop",
    "title_english": "Cowboy Bebop",
    "title_japanese": "カウボーイビバップ",
    "url": "https://myanimelist.net/anime/1/Cowboy_Bebop",
    "synopsis": "Bounty hunters travel through space.",
    "images": {"jpg": {"large_image_url": "https://example.test/bebop.jpg"}},
    "type": "TV",
    "status": "Finished Airing",
    "episodes": 26,
    "score": 8.9,
    "genres": [{"name": "Action"}, {"name": "Sci-Fi"}],
}

CHARACTER = {
    "name": {"full": "Monkey D. Luffy", "native": "モンキー・D・ルフィ"},
    "siteUrl": "https://anilist.co/character/40",
    "description": "Captain of the Straw Hat Pirates.<br><br><i>Dream:</i> King.",
    "image": {"large": "https://example.test/luffy.jpg"},
    "gender": "Male",
    "age": "19",
    "favourites": 123456,
}

USER = {
    "name": "Taiga",
    "siteUrl": "https://anilist.co/user/Taiga",
    "about": "Anime and manga fan.<br><br><i>Hello!</i>",
    "avatar": {"large": "https://example.test/taiga-avatar.jpg"},
    "bannerImage": "https://example.test/taiga-banner.jpg",
    "createdAt": 1609459200,
    "statistics": {
        "anime": {"count": 321, "episodesWatched": 4567},
        "manga": {"count": 89, "chaptersRead": 12345},
    },
}

SCHEDULE_ENTRY = {
    "airingAt": 1788800400,
    "episode": 12,
    "media": {
        "title": {"romaji": "Weekly Anime", "english": "Weekly Anime"},
        "isAdult": False,
    },
}


def _make_cog():
    return anilist.AniListCog(SimpleNamespace(session=object(), color=0x123456))


def _make_interaction():
    return SimpleNamespace(
        user=SimpleNamespace(id=123),
        response=SimpleNamespace(send_message=AsyncMock(), defer=AsyncMock()),
        followup=SimpleNamespace(send=AsyncMock()),
    )


def test_commands_are_grouped_under_anilist():
    group = _make_cog().app_command

    assert group.name == "anilist"
    assert [(command.name, command.qualified_name) for command in group.commands] == [
        ("anime", "anilist anime"),
        ("manga", "anilist manga"),
        ("character", "anilist character"),
        ("user", "anilist user"),
        ("top", "anilist top"),
        ("weekly", "anilist weekly"),
    ]


@pytest.mark.asyncio
async def test_top_results_uses_anilist_filters_and_score_order(monkeypatch):
    request = AsyncMock(return_value={"data": {"Page": {"media": [ANIME]}}})
    monkeypatch.setattr(anilist, "_request", request)
    session = object()

    assert await anilist._top_results(
        session,
        "ANIME",
        year=1998,
        genre="Action",
        season="SPRING",
        media_format="TV",
    ) == [ANIME]
    request.assert_awaited_once_with(
        session,
        anilist.TOP_MEDIA,
        {
            "type": "ANIME",
            "perPage": anilist.TOP_RESULT_LIMIT,
            "yearStart": 19980000,
            "yearEnd": 19990000,
            "genre": "Action",
            "season": "SPRING",
            "format": "TV",
        },
    )
    assert "isAdult: false" in anilist.TOP_MEDIA
    assert "sort: [SCORE_DESC]" in anilist.TOP_MEDIA


@pytest.mark.asyncio
async def test_top_results_omits_unset_filters(monkeypatch):
    request = AsyncMock(return_value={"data": {"Page": {"media": [ANIME]}}})
    monkeypatch.setattr(anilist, "_request", request)
    session = object()

    assert await anilist._top_results(session, "ANIME") == [ANIME]
    request.assert_awaited_once_with(
        session,
        anilist.TOP_MEDIA,
        {"type": "ANIME", "perPage": anilist.TOP_RESULT_LIMIT},
    )


@pytest.mark.asyncio
async def test_tenrai_top_translates_filters_and_media_in_one_request(monkeypatch):
    request = AsyncMock(return_value={"data": [TENRAI_ANIME]})
    monkeypatch.setattr(tenrai_fallback, "_request", request)
    session = object()

    payload = await tenrai_fallback.top_media(
        session,
        "ANIME",
        10,
        year=1998,
        genre="Thriller",
        season="SPRING",
        media_format="TV",
    )

    request.assert_awaited_once_with(
        session,
        "anime",
        {
            "limit": "10",
            "sfw": "true",
            "order_by": "score",
            "sort": "desc",
            "genres": "41",
            "type": "tv",
            "start_date": "1998-04-01",
            "end_date": "1998-06-30",
        },
    )
    [result] = payload["data"]["Page"]["media"]
    assert result["_provider"] == "Tenrai"
    assert result["title"] == {
        "romaji": "Cowboy Bebop",
        "english": "Cowboy Bebop",
        "native": "カウボーイビバップ",
    }
    assert result["averageScore"] == 89
    assert result["genres"] == ["Action", "Sci-Fi"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "transport"),
    [(403, False), (429, False), (503, False), (None, True)],
)
async def test_top_unavailability_uses_one_tenrai_request_per_uncached_filter_set(
    monkeypatch, status, transport
):
    fallback = {**ANIME, "_provider": "Tenrai"}
    request = AsyncMock(
        side_effect=anilist.AniListError("unavailable", status, unavailable=transport)
    )
    tenrai = AsyncMock(return_value={"data": {"Page": {"media": [fallback]}}})
    monkeypatch.setattr(anilist, "_request", request)
    monkeypatch.setattr(anilist, "top_tenrai_media", tenrai)
    cog = _make_cog()

    assert await cog._cached_top("ANIME") == ([fallback], False)
    assert await cog._cached_top("ANIME") == ([fallback], True)
    assert await cog._cached_top("ANIME", year=1998) == ([fallback], False)

    assert request.await_count == 2
    assert tenrai.await_count == 2
    assert (
        anilist.media_embed(fallback, "ANIME", 0x123456, cached=True).author.name
        == "Tenrai • Cache Hit"
    )


@pytest.mark.asyncio
async def test_top_403_preserves_anilist_error_when_tenrai_fails(monkeypatch):
    original = anilist.AniListError("disabled", 403)
    monkeypatch.setattr(anilist, "_request", AsyncMock(side_effect=original))
    monkeypatch.setattr(
        anilist,
        "top_tenrai_media",
        AsyncMock(side_effect=tenrai_fallback.TenraiError("unavailable", 503)),
    )

    with pytest.raises(anilist.AniListError) as raised:
        await _make_cog()._cached_top("ANIME")
    assert raised.value is original


@pytest.mark.asyncio
async def test_top_400_does_not_fall_back_to_tenrai(monkeypatch):
    original = anilist.AniListError("bad request", 400)
    monkeypatch.setattr(anilist, "_request", AsyncMock(side_effect=original))
    tenrai = AsyncMock()
    monkeypatch.setattr(anilist, "top_tenrai_media", tenrai)

    with pytest.raises(anilist.AniListError) as raised:
        await _make_cog()._cached_top("ANIME")
    assert raised.value is original
    tenrai.assert_not_awaited()


@pytest.mark.asyncio
async def test_top_season_without_year_uses_current_year(monkeypatch):
    ranking = AsyncMock(return_value=[ANIME])
    monkeypatch.setattr(anilist, "_top_results", ranking)
    cog = _make_cog()

    await cog._cached_top("ANIME", season="FALL")

    ranking.assert_awaited_once_with(
        cog.bot.session,
        "ANIME",
        year=datetime.now(UTC).year,
        genre=None,
        season="FALL",
        media_format=None,
    )


@pytest.mark.asyncio
async def test_top_cache_normalizes_equivalent_genres(monkeypatch):
    ranking = AsyncMock(return_value=[ANIME])
    monkeypatch.setattr(anilist, "_top_results", ranking)
    cog = _make_cog()

    assert await cog._cached_top("ANIME", genre="Action") == ([ANIME], False)
    assert await cog._cached_top("ANIME", genre="  action  ") == ([ANIME], True)
    ranking.assert_awaited_once_with(
        cog.bot.session,
        "ANIME",
        year=None,
        genre="Action",
        season=None,
        media_format=None,
    )


@pytest.mark.asyncio
async def test_top_command_defaults_to_anime_and_paginates_cached_results():
    second = {
        **ANIME,
        "title": {"romaji": "Frieren: Beyond Journey's End"},
        "siteUrl": "https://anilist.co/anime/154587",
    }
    cog = _make_cog()
    cog._cached_top = AsyncMock(return_value=([ANIME, second], True))
    interaction = _make_interaction()
    message = SimpleNamespace(edit=AsyncMock())
    interaction.followup.send.return_value = message

    await anilist.AniListCog.top.callback(cog, interaction)

    cog._cached_top.assert_awaited_once_with(
        "ANIME",
        year=None,
        genre=None,
        season=None,
        media_format=None,
    )
    sent = interaction.followup.send.await_args.kwargs
    view = sent["view"]
    assert isinstance(view, anilist.AniListPagination)
    assert sent["embed"].title == "Cowboy Bebop"
    assert sent["embed"].footer.text.startswith("Rank 1/2 • ")
    assert sent["embed"].author.name == "AniList • Cache Hit"

    navigation = SimpleNamespace(response=SimpleNamespace(edit_message=AsyncMock()))
    await view.children[1].callback(navigation)

    assert navigation.response.edit_message.await_args.kwargs[
        "embed"
    ].footer.text.startswith("Rank 2/2 • ")
    cog._cached_top.assert_awaited_once()


@pytest.mark.asyncio
async def test_top_command_passes_selected_filters():
    cog = _make_cog()
    cog._cached_top = AsyncMock(return_value=([MANGA], False))
    interaction = _make_interaction()

    await anilist.AniListCog.top.callback(
        cog,
        interaction,
        media_type="MANGA",
        year=1989,
        genre="Action",
        season=None,
        format="MANGA",
    )

    cog._cached_top.assert_awaited_once_with(
        "MANGA",
        year=1989,
        genre="Action",
        season=None,
        media_format="MANGA",
    )
    embed = interaction.followup.send.await_args.kwargs["embed"]
    assert embed.title == "Berserk"
    assert embed.footer.text.startswith("Rank 1/1 • ")


def test_week_bounds_cover_monday_through_sunday_utc():
    assert anilist._week_bounds(datetime(2026, 9, 9, 12, 30, tzinfo=UTC)) == (
        1788739200,
        1789344000,
    )


@pytest.mark.asyncio
async def test_weekly_schedule_fetches_all_pages_and_filters_adult_media(
    monkeypatch,
):
    adult = {
        **SCHEDULE_ENTRY,
        "media": {**SCHEDULE_ENTRY["media"], "isAdult": True},
    }
    later = {**SCHEDULE_ENTRY, "airingAt": 1788886800}
    request = AsyncMock(
        side_effect=[
            {"data": {"Page": {"airingSchedules": [SCHEDULE_ENTRY] * 49 + [adult]}}},
            {"data": {"Page": {"airingSchedules": [later]}}},
        ]
    )
    monkeypatch.setattr(anilist, "_request", request)

    results = await anilist._weekly_schedule_results(object(), 1788739200, 1789344000)

    assert results == [SCHEDULE_ENTRY] * 49 + [later]
    assert [call.args[2] for call in request.await_args_list] == [
        {
            "page": 1,
            "perPage": 50,
            "start": 1788739199,
            "end": 1789344000,
        },
        {
            "page": 2,
            "perPage": 50,
            "start": 1788739199,
            "end": 1789344000,
        },
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "transport"),
    [(403, False), (429, False), (503, False), (None, True)],
)
async def test_weekly_schedule_unavailability_falls_back_to_tenrai(
    monkeypatch, status, transport
):
    fallback = {**SCHEDULE_ENTRY, "_provider": "Tenrai", "episode": None}
    session = object()
    monkeypatch.setattr(
        anilist,
        "_request",
        AsyncMock(
            side_effect=anilist.AniListError(
                "unavailable", status, unavailable=transport
            )
        ),
    )
    tenrai = AsyncMock(return_value=[fallback])
    monkeypatch.setattr(anilist, "weekly_tenrai_schedule", tenrai)

    assert await anilist._weekly_schedule_results(session, 1788739200, 1789344000) == [
        fallback
    ]
    tenrai.assert_awaited_once_with(session, 1788739200, 1789344000)


@pytest.mark.asyncio
async def test_weekly_schedule_400_does_not_fallback(monkeypatch):
    monkeypatch.setattr(
        anilist,
        "_request",
        AsyncMock(side_effect=anilist.AniListError("bad request", 400)),
    )
    tenrai = AsyncMock()
    monkeypatch.setattr(anilist, "weekly_tenrai_schedule", tenrai)

    with pytest.raises(anilist.AniListError):
        await anilist._weekly_schedule_results(object(), 1788739200, 1789344000)
    tenrai.assert_not_awaited()


def test_weekly_embeds_show_local_discord_times_and_degrade_to_tba():
    tba = {**SCHEDULE_ENTRY, "airingAt": None, "episode": None}

    [embed] = anilist.weekly_embeds([tba, SCHEDULE_ENTRY], 0x123456, cached=True)

    assert embed.title == "Anime Airing This Week"
    assert embed.description is not None
    assert "<t:1788800400:F> • Episode 12" in embed.description
    assert "**Time TBA**" in embed.description
    assert embed.description.index("<t:1788800400:F>") < embed.description.index(
        "**Time TBA**"
    )
    assert embed.author.name == "AniList • Cache Hit"
    assert embed.footer.text == "Page 1/1 • Times shown in your timezone"


def test_weekly_tenrai_embed_marks_broadcast_only_data():
    fallback = {
        **SCHEDULE_ENTRY,
        "_provider": "Tenrai",
        "episode": None,
    }

    [embed] = anilist.weekly_embeds([fallback], 0x123456, cached=False)

    assert embed.author.name == "Tenrai"
    assert embed.author.url == "https://tenrai.org/"
    assert "Episode" not in (embed.description or "")
    assert embed.footer.text == (
        "Page 1/1 • Tenrai broadcast times • Episode numbers unavailable"
    )


@pytest.mark.asyncio
async def test_weekly_command_uses_cached_schedule(monkeypatch):
    cog = _make_cog()
    cog._cached_weekly_schedule = AsyncMock(return_value=([SCHEDULE_ENTRY], False))
    monkeypatch.setattr(anilist, "_week_bounds", lambda: (1788739200, 1789344000))
    interaction = _make_interaction()

    await anilist.AniListCog.weekly.callback(cog, interaction)

    interaction.response.defer.assert_awaited_once_with()
    cog._cached_weekly_schedule.assert_awaited_once_with(1788739200, 1789344000)
    embed = interaction.followup.send.await_args.kwargs["embed"]
    assert "<t:1788800400:F>" in embed.description


@pytest.mark.asyncio
async def test_weekly_cache_reuses_results_within_the_same_week(monkeypatch):
    now = [0.0]
    schedule = AsyncMock(return_value=[SCHEDULE_ENTRY])
    monkeypatch.setattr(anilist, "monotonic", lambda: now[0])
    monkeypatch.setattr(anilist, "_weekly_schedule_results", schedule)
    cog = _make_cog()

    assert await cog._cached_weekly_schedule(100, 200) == (
        [SCHEDULE_ENTRY],
        False,
    )
    assert await cog._cached_weekly_schedule(100, 200) == (
        [SCHEDULE_ENTRY],
        True,
    )
    schedule.assert_awaited_once_with(cog.bot.session, 100, 200)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("search_type", "document", "result_field", "variables", "result"),
    [
        (
            "ANIME",
            anilist.MEDIA_SEARCH,
            "media",
            {"search": "Berserk", "perPage": 3, "type": "ANIME"},
            MANGA,
        ),
        (
            "MANGA",
            anilist.MEDIA_SEARCH,
            "media",
            {"search": "Berserk", "perPage": 3, "type": "MANGA"},
            MANGA,
        ),
        (
            "CHARACTER",
            anilist.CHARACTER_SEARCH,
            "characters",
            {"search": "Berserk", "perPage": 3},
            CHARACTER,
        ),
        (
            "USER",
            anilist.USER_SEARCH,
            "users",
            {"search": "Berserk", "perPage": 3},
            USER,
        ),
    ],
)
async def test_search_results_selects_document_variables_and_collection(
    monkeypatch, search_type, document, result_field, variables, result
):
    request = AsyncMock(return_value={"data": {"Page": {result_field: [result]}}})
    monkeypatch.setattr(anilist, "_request", request)
    session = object()

    assert await anilist._search_results(session, "Berserk", search_type, 3) == [result]
    request.assert_awaited_once_with(session, document, variables)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "transport"),
    [(403, False), (429, False), (503, False), (None, True)],
)
async def test_anilist_unavailability_falls_back_to_tenrai_and_caches_result(
    monkeypatch, status, transport
):
    fallback = {**MANGA, "_provider": "Tenrai"}
    request = AsyncMock(
        side_effect=anilist.AniListError("unavailable", status, unavailable=transport)
    )
    tenrai = AsyncMock(return_value={"data": {"Page": {"media": [fallback]}}})
    monkeypatch.setattr(anilist, "_request", request)
    monkeypatch.setattr(anilist, "search_tenrai_media", tenrai)
    cog = _make_cog()

    assert await cog._cached_search("Berserk", "MANGA") == ([fallback], False)
    assert await cog._cached_search(" berserk ", "MANGA") == ([fallback], True)
    request.assert_awaited_once()
    tenrai.assert_awaited_once_with(cog.bot.session, "Berserk", "MANGA", 5)
    embed = anilist.media_embed(fallback, "MANGA", 0x123456, cached=True)
    assert embed.author.name == "Tenrai • Cache Hit"
    assert embed.author.url == "https://tenrai.org/"


@pytest.mark.asyncio
async def test_character_403_uses_tenrai_for_autocomplete_and_cache(monkeypatch):
    fallback = {
        **CHARACTER,
        "_provider": "Tenrai",
        "siteUrl": "https://myanimelist.net/character/40/Luffy_Monkey_D_",
        "gender": None,
        "age": None,
    }
    request = AsyncMock(side_effect=anilist.AniListError("disabled", 403))
    tenrai = AsyncMock(return_value={"data": {"Page": {"characters": [fallback]}}})
    monkeypatch.setattr(anilist, "_request", request)
    monkeypatch.setattr(anilist, "search_tenrai_characters", tenrai)
    cog = _make_cog()
    interaction = SimpleNamespace(command=SimpleNamespace(name="character"))

    choices = await cog.search_query_autocomplete(interaction, "Luffy")
    result, cached = await cog._cached_search(choices[0].value, "CHARACTER")

    assert [(choice.name, choice.value) for choice in choices] == [
        ("Monkey D. Luffy", "Monkey D. Luffy")
    ]
    assert (result, cached) == ([fallback], True)
    request.assert_awaited_once()
    tenrai.assert_awaited_once_with(cog.bot.session, "Luffy", 5)
    embed = anilist.character_embed(fallback, 0x123456, cached=True)
    assert [(field.name, field.value) for field in embed.fields[:2]] == [
        (":transgender_symbol: Gender", "—"),
        (":birthday: Age", "—"),
    ]
    assert embed.author.name == "Tenrai • Cache Hit"
    assert embed.author.url == "https://tenrai.org/"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("search_type", "status"),
    [("ANIME", 400), ("USER", 403), ("USER", 429), ("USER", 503)],
)
async def test_tenrai_fallback_excludes_bad_requests_and_user_lookup(
    monkeypatch, search_type, status
):
    monkeypatch.setattr(
        anilist,
        "_request",
        AsyncMock(side_effect=anilist.AniListError("unavailable", status)),
    )
    tenrai_media = AsyncMock()
    tenrai_characters = AsyncMock()
    monkeypatch.setattr(anilist, "search_tenrai_media", tenrai_media)
    monkeypatch.setattr(anilist, "search_tenrai_characters", tenrai_characters)

    with pytest.raises(anilist.AniListError):
        await anilist._search_results(object(), "Berserk", search_type)
    tenrai_media.assert_not_awaited()
    tenrai_characters.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("transport_error", [ClientError("dns"), TimeoutError()])
async def test_request_marks_transport_failures_unavailable(transport_error):
    session = SimpleNamespace(post=MagicMock(side_effect=transport_error))

    with pytest.raises(anilist.AniListError) as raised:
        await anilist._request(session, "query", {})

    assert raised.value.status is None
    assert raised.value.unavailable is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("search_type", "fallback_name"),
    [("MANGA", "search_tenrai_media"), ("CHARACTER", "search_tenrai_characters")],
)
async def test_failed_tenrai_fallback_preserves_anilist_error(
    monkeypatch, search_type, fallback_name
):
    original = anilist.AniListError("disabled", 403)
    monkeypatch.setattr(anilist, "_request", AsyncMock(side_effect=original))
    monkeypatch.setattr(
        anilist,
        fallback_name,
        AsyncMock(side_effect=tenrai_fallback.TenraiError("unavailable", 503)),
    )

    with pytest.raises(anilist.AniListError) as raised:
        await anilist._search_results(object(), "Berserk", search_type)
    assert raised.value is original


def test_manga_embed_uses_horizontal_manga_details():
    embed = anilist.media_embed(MANGA, "MANGA", 0x123456)

    assert embed.title == "Berserk"
    assert embed.url == MANGA["siteUrl"]
    assert "<br>" not in embed.description
    assert "<i>" not in embed.description
    assert "Notes:" in embed.description
    assert [(field.name, field.value) for field in embed.fields] == [
        (":star: Score", "90/100"),
        (":books: Ch / Vol", "380 / 42"),
        (":satellite: Status", "Finished"),
    ]
    assert embed.footer.text == "Manga • Action • Drama • Fantasy"
    assert embed.thumbnail.url == MANGA["coverImage"]["large"]
    assert embed.image.url == MANGA["bannerImage"]
    assert embed.author.name == "AniList"


@pytest.mark.asyncio
async def test_manga_command_uses_shared_search_path():
    cog = _make_cog()
    cog._cached_search = AsyncMock(return_value=([MANGA], True))
    interaction = _make_interaction()

    await anilist.AniListCog.manga.callback(cog, interaction, " Berserk ")

    interaction.response.defer.assert_awaited_once_with()
    cog._cached_search.assert_awaited_once_with("Berserk", "MANGA")
    embed = interaction.followup.send.await_args.kwargs["embed"]
    assert embed.title == "Berserk"
    assert embed.fields[1].name == ":books: Ch / Vol"
    assert embed.author.name == "AniList • Cache Hit"


def test_media_embed_truncates_description_at_word_boundary():
    media = {**MANGA, "description": "word " * 200}

    description = anilist.media_embed(media, "MANGA", 0x123456).description

    assert description is not None
    assert len(description) <= anilist.DESCRIPTION_LIMIT
    assert description.endswith("…")
    assert description.removesuffix("…").split()[-1] == "word"


def test_description_converts_anilist_spoilers_for_discord():
    description = anilist._clean_description(
        "Family: ~!Minato Namikaze (father), Kushina Uzumaki (mother)!~"
    )

    assert description == (
        "Family: ||Minato Namikaze (father), Kushina Uzumaki (mother)||"
    )


def test_description_closes_spoiler_when_truncated():
    description = anilist._clean_description("Family: ~!" + "secret " * 100 + "!~")

    assert len(description) <= anilist.DESCRIPTION_LIMIT
    assert description.startswith("Family: ||")
    assert description.endswith("…||")
    assert description.count("||") == 2


def test_character_embed_uses_character_details():
    embed = anilist.character_embed(CHARACTER, 0x123456, cached=True)

    assert embed.title == "Monkey D. Luffy"
    assert embed.url == CHARACTER["siteUrl"]
    assert embed.description == "Captain of the Straw Hat Pirates.\n\nDream: King."
    assert [(field.name, field.value) for field in embed.fields] == [
        (":transgender_symbol: Gender", "Male"),
        (":birthday: Age", "19"),
        (":heart: Favourites", "123,456"),
    ]
    assert embed.footer.text == "Character • モンキー・D・ルフィ"
    assert embed.thumbnail.url == CHARACTER["image"]["large"]
    assert embed.author.name == "AniList • Cache Hit"


def test_user_embed_uses_public_profile_details():
    embed = anilist.user_embed(USER, 0x123456, cached=True)

    assert embed.title == "Taiga"
    assert embed.url == USER["siteUrl"]
    assert embed.description == "Anime and manga fan.\n\nHello!"
    assert [(field.name, field.value) for field in embed.fields] == [
        (":tv: Anime", "321 entries\n4,567 episodes"),
        (":books: Manga", "89 entries\n12,345 chapters"),
        (":date: Joined", "<t:1609459200:D>"),
    ]
    assert embed.thumbnail.url == USER["avatar"]["large"]
    assert embed.image.url == USER["bannerImage"]
    assert embed.author.name == "AniList • Cache Hit"


@pytest.mark.asyncio
async def test_user_command_uses_shared_search_path():
    cog = _make_cog()
    cog._cached_search = AsyncMock(return_value=([USER], False))
    interaction = _make_interaction()

    await anilist.AniListCog.user.callback(cog, interaction, " Taiga ")

    interaction.response.defer.assert_awaited_once_with()
    cog._cached_search.assert_awaited_once_with("Taiga", "USER")
    embed = interaction.followup.send.await_args.kwargs["embed"]
    assert embed.title == "Taiga"
    assert embed.author.name == "AniList"


@pytest.mark.asyncio
async def test_character_command_uses_cached_search_path():
    cog = _make_cog()
    cog._cached_search = AsyncMock(return_value=([CHARACTER], False))
    interaction = _make_interaction()

    await anilist.AniListCog.character.callback(cog, interaction, " Luffy ")

    interaction.response.defer.assert_awaited_once_with()
    cog._cached_search.assert_awaited_once_with("Luffy", "CHARACTER")
    embed = interaction.followup.send.await_args.kwargs["embed"]
    assert embed.title == "Monkey D. Luffy"
    assert embed.author.name == "AniList"


@pytest.mark.asyncio
async def test_character_command_rejects_empty_name():
    cog = _make_cog()
    interaction = _make_interaction()

    await anilist.AniListCog.character.callback(cog, interaction, "   ")

    interaction.response.send_message.assert_awaited_once_with(
        ":x: Enter a character name to search for.", ephemeral=True
    )
    interaction.response.defer.assert_not_awaited()
    assert interaction.command_failed is True


@pytest.mark.asyncio
async def test_command_paginates_cached_results_without_more_searches():
    second = {
        **MANGA,
        "title": {"romaji": "Berserk: The Prototype"},
        "siteUrl": "https://anilist.co/manga/30621",
    }
    cog = _make_cog()
    cog._cached_search = AsyncMock(return_value=([MANGA, second], False))
    interaction = _make_interaction()
    message = SimpleNamespace(edit=AsyncMock())
    interaction.followup.send.return_value = message

    await anilist.AniListCog.manga.callback(cog, interaction, "Berserk")

    sent = interaction.followup.send.await_args.kwargs
    view = sent["view"]
    assert isinstance(view, anilist.AniListPagination)
    assert sent["embed"].title == "Berserk"
    assert sent["embed"].footer.text.startswith("Page 1/2 • ")
    assert sent["wait"] is True
    assert view.message is message

    navigation = SimpleNamespace(response=SimpleNamespace(edit_message=AsyncMock()))
    await view.children[1].callback(navigation)

    edited = navigation.response.edit_message.await_args.kwargs
    assert edited["embed"].title == "Berserk: The Prototype"
    assert edited["embed"].footer.text.startswith("Page 2/2 • ")
    cog._cached_search.assert_awaited_once_with("Berserk", "MANGA")


@pytest.mark.asyncio
async def test_pagination_rejects_other_users():
    view = anilist.AniListPagination(
        [MANGA, MANGA], "MANGA", 0x123456, cached=False, owner_id=123
    )
    interaction = SimpleNamespace(
        user=SimpleNamespace(id=456),
        response=SimpleNamespace(send_message=AsyncMock()),
    )

    assert await view.interaction_check(interaction) is False
    interaction.response.send_message.assert_awaited_once_with(
        ":x: Only the person who searched can change this result.", ephemeral=True
    )


@pytest.mark.asyncio
async def test_autocomplete_reuses_prefix_and_seeds_selected_result_cache(monkeypatch):
    second = {
        **MANGA,
        "title": {
            "romaji": "Berserk: Ougon Jidai-hen",
            "english": "Berserk: The Golden Age Arc",
        },
    }
    cog = _make_cog()
    search = AsyncMock(return_value=[MANGA, second])
    monkeypatch.setattr(anilist, "_search_results", search)
    interaction = SimpleNamespace(command=SimpleNamespace(name="manga"))

    choices = await cog.search_query_autocomplete(interaction, "ber")
    narrowed = await cog.search_query_autocomplete(interaction, "bers")

    assert [choice.name for choice in choices] == [
        "Berserk",
        "Berserk: Ougon Jidai-hen",
    ]
    assert [choice.name for choice in narrowed] == [
        "Berserk",
        "Berserk: Ougon Jidai-hen",
    ]
    result, cached = await cog._cached_search(choices[0].value, "MANGA")
    assert (result, cached) == ([MANGA], True)
    search.assert_awaited_once_with(cog.bot.session, "ber", "MANGA")


@pytest.mark.asyncio
async def test_autocomplete_skips_short_queries_and_missing_session():
    cog = _make_cog()
    cog._cached_search = AsyncMock()
    interaction = SimpleNamespace(command=SimpleNamespace(name="anime"))

    assert await cog.search_query_autocomplete(interaction, "ab") == []
    cog.bot.session = None
    assert await cog.search_query_autocomplete(interaction, "Cowboy Bebop") == []
    cog._cached_search.assert_not_awaited()


@pytest.mark.asyncio
async def test_user_autocomplete_returns_profile_names(monkeypatch):
    cog = _make_cog()
    search = AsyncMock(return_value=[USER])
    monkeypatch.setattr(anilist, "_search_results", search)
    interaction = SimpleNamespace(command=SimpleNamespace(name="user"))

    choices = await cog.search_query_autocomplete(interaction, "Tai")

    assert [(choice.name, choice.value) for choice in choices] == [("Taiga", "Taiga")]
    search.assert_awaited_once_with(cog.bot.session, "Tai", "USER")


@pytest.mark.asyncio
async def test_character_autocomplete_fails_gracefully():
    cog = _make_cog()
    cog._cached_search = AsyncMock(side_effect=anilist.AniListError("unavailable", 503))
    interaction = SimpleNamespace(command=SimpleNamespace(name="character"))

    assert await cog.search_query_autocomplete(interaction, "Luffy") == []


@pytest.mark.asyncio
async def test_character_search_reuses_shared_cache(monkeypatch):
    search = AsyncMock(return_value=[CHARACTER])
    monkeypatch.setattr(anilist, "_search_results", search)
    cog = _make_cog()

    assert await cog._cached_search("Monkey D. Luffy", "CHARACTER") == (
        [CHARACTER],
        False,
    )
    assert await cog._cached_search(" monkey d. luffy ", "CHARACTER") == (
        [CHARACTER],
        True,
    )
    search.assert_awaited_once_with(cog.bot.session, "Monkey D. Luffy", "CHARACTER")


@pytest.mark.asyncio
async def test_cache_normalizes_queries_and_keeps_empty_results(monkeypatch):
    now = [0.0]
    calls = []

    async def search(_session, title, media_type):
        calls.append((title, media_type))
        return [] if len(calls) == 1 else [{"id": 1}]

    monkeypatch.setattr(anilist, "monotonic", lambda: now[0])
    monkeypatch.setattr(anilist, "_search_results", search)
    cog = _make_cog()

    assert await cog._cached_search("Cowboy  Bebop", "ANIME") == ([], False)
    assert await cog._cached_search(" cowboy bebop ", "ANIME") == ([], True)
    assert len(calls) == 1

    now[0] = anilist.CACHE_TTL_SECONDS + 1
    assert await cog._cached_search("cowboy bebop", "ANIME") == ([{"id": 1}], False)
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_cache_coalesces_concurrent_equivalent_searches(monkeypatch):
    started = asyncio.Event()
    release = asyncio.Event()
    calls = 0
    result = [{"id": 1}]

    async def search(_session, _title, _media_type):
        nonlocal calls
        calls += 1
        started.set()
        await release.wait()
        return result

    monkeypatch.setattr(anilist, "_search_results", search)
    cog = _make_cog()

    first = asyncio.create_task(cog._cached_search("Frieren", "ANIME"))
    await started.wait()
    second = asyncio.create_task(cog._cached_search(" frieren ", "ANIME"))
    await asyncio.sleep(0)
    release.set()

    assert await asyncio.gather(first, second) == [
        (result, False),
        (result, True),
    ]
    assert calls == 1


@pytest.mark.asyncio
async def test_cache_does_not_store_failures(monkeypatch):
    calls = 0
    result = [{"id": 1}]

    async def search(_session, _title, _media_type):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise anilist.AniListError("unavailable", 503)
        return result

    monkeypatch.setattr(anilist, "_search_results", search)
    cog = _make_cog()

    with pytest.raises(anilist.AniListError):
        await cog._cached_search("Frieren", "ANIME")
    assert await cog._cached_search("Frieren", "ANIME") == (result, False)
    assert calls == 2


@pytest.mark.asyncio
async def test_cache_stays_bounded(monkeypatch):
    monkeypatch.setattr(anilist, "monotonic", lambda: 0.0)
    monkeypatch.setattr(
        anilist,
        "_search_results",
        lambda *_args: asyncio.sleep(0, result=[{"id": 999}]),
    )
    cog = _make_cog()
    cog.search_cache = {
        ("ANIME", f"title {index}"): (100.0, [{"id": index}])
        for index in range(anilist.CACHE_LIMIT)
    }

    await cog._cached_search("New title", "ANIME")

    assert len(cog.search_cache) == anilist.CACHE_LIMIT
    assert ("ANIME", "title 0") not in cog.search_cache
    assert ("ANIME", "new title") in cog.search_cache
