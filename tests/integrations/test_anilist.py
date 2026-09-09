import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

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
    ]


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
async def test_anilist_403_falls_back_to_tenrai_and_caches_result(monkeypatch):
    fallback = {**MANGA, "_provider": "Tenrai"}
    request = AsyncMock(side_effect=anilist.AniListError("disabled", 403))
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
@pytest.mark.parametrize(
    ("search_type", "status"),
    [("ANIME", 503), ("CHARACTER", 403)],
)
async def test_tenrai_fallback_is_limited_to_media_403(
    monkeypatch, search_type, status
):
    monkeypatch.setattr(
        anilist,
        "_request",
        AsyncMock(side_effect=anilist.AniListError("unavailable", status)),
    )
    tenrai = AsyncMock()
    monkeypatch.setattr(anilist, "search_tenrai_media", tenrai)

    with pytest.raises(anilist.AniListError):
        await anilist._search_results(object(), "Berserk", search_type)
    tenrai.assert_not_awaited()


@pytest.mark.asyncio
async def test_failed_tenrai_fallback_preserves_anilist_error(monkeypatch):
    original = anilist.AniListError("disabled", 403)
    monkeypatch.setattr(anilist, "_request", AsyncMock(side_effect=original))
    monkeypatch.setattr(
        anilist,
        "search_tenrai_media",
        AsyncMock(side_effect=tenrai_fallback.TenraiError("unavailable", 503)),
    )

    with pytest.raises(anilist.AniListError) as raised:
        await anilist._search_results(object(), "Berserk", "MANGA")
    assert raised.value is original


@pytest.mark.asyncio
async def test_public_search_helpers_keep_first_result_contract(monkeypatch):
    search = AsyncMock(side_effect=[[MANGA, {"id": 2}], [CHARACTER, {"id": 3}]])
    monkeypatch.setattr(anilist, "_search_results", search)
    session = object()

    assert await anilist.search_media(session, " Berserk ", "MANGA") is MANGA
    assert await anilist.search_character(session, " Luffy ") is CHARACTER
    assert search.await_args_list == [
        ((session, "Berserk", "MANGA", 1),),
        ((session, "Luffy", "CHARACTER", 1),),
    ]


def test_manga_embed_uses_horizontal_manga_details():
    embed = anilist.media_embed(MANGA, "MANGA", 0x123456)

    assert embed.title == "Berserk"
    assert embed.url == MANGA["siteUrl"]
    assert "<br>" not in embed.description
    assert "<i>" not in embed.description
    assert "Notes:" in embed.description
    assert [(field.name, field.value) for field in embed.fields] == [
        ("⭐ Score", "90/100"),
        ("📚 Ch / Vol", "380 / 42"),
        ("📡 Status", "Finished"),
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
    assert embed.fields[1].name == "📚 Ch / Vol"
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
        ("⚧ Gender", "Male"),
        ("🎂 Age", "19"),
        ("❤️ Favourites", "123,456"),
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
        ("📺 Anime", "321 entries\n4,567 episodes"),
        ("📚 Manga", "89 entries\n12,345 chapters"),
        ("📅 Joined", "<t:1609459200:D>"),
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
