import logging
from asyncio import Lock
from datetime import UTC, datetime, timedelta
from html.parser import HTMLParser
from time import monotonic
from typing import TYPE_CHECKING, Any, Literal

from aiohttp import ClientError, ClientSession, ClientTimeout
from discord import (
    ButtonStyle,
    Embed,
    HTTPException,
    Interaction,
    Message,
    app_commands,
)
from discord.ext import commands
from discord.ui import Button, View, button

# Tenrai owns fallback transport and errors; this module decides when to use it.
from ._tenrai_fallback import TenraiError
from ._tenrai_fallback import search_media as search_tenrai_media
from ._tenrai_fallback import top_media as top_tenrai_media
from ._tenrai_fallback import weekly_schedule as weekly_tenrai_schedule

if TYPE_CHECKING:
    from main import Sakamoto

logger = logging.getLogger(__name__)

ANILIST_URL = "https://graphql.anilist.co"
MediaType = Literal["ANIME", "MANGA"]
SearchType = Literal[MediaType, "CHARACTER", "USER"]

# ANILIST REQUEST POLICY
# AniList is a shared, rate-limited service currently operating with reduced capacity.
# Every new command must reuse cached reads, coalesce equivalent requests, request only
# fields it displays, and avoid retries during outages or rate limits. Pagination must
# cache fetched pages instead of requesting them again when users navigate backwards.
# Prefer slightly stale public catalogue data over avoidable upstream traffic.

# SEARCH WORKFLOW
# Commands and autocomplete share _cached_search, which sends only cache misses through
# _search_results and _request. Each response is cached as a small result list that can
# feed autocomplete, the initial embed, and every pagination button without another
# AniList request. Discord embeds are built last so the cache stays presentation-free.
CACHE_TTL_SECONDS = 7 * 24 * 60 * 60
CACHE_LIMIT = 256
DESCRIPTION_LIMIT = 500
SEARCH_RESULT_LIMIT = 5
TOP_RESULT_LIMIT = 10
AUTOCOMPLETE_MIN_LENGTH = 3
WEEKLY_CACHE_TTL_SECONDS = 60 * 60
WEEKLY_QUERY_PAGE_SIZE = 50
WEEKLY_PAGE_SIZE = 25

MEDIA_SEARCH = """
query ($search: String!, $type: MediaType!, $perPage: Int!) {
  Page(page: 1, perPage: $perPage) {
    media(search: $search, type: $type, isAdult: false) {
      title { romaji english native }
      siteUrl
      description(asHtml: false)
      coverImage { large }
      bannerImage
      format
      status
      episodes
      chapters
      volumes
      averageScore
      genres
    }
  }
}
"""

CHARACTER_SEARCH = """
query ($search: String!, $perPage: Int!) {
  Page(page: 1, perPage: $perPage) {
    characters(search: $search) {
      name { full native }
      siteUrl
      description(asHtml: false)
      image { large }
      gender
      age
      favourites
    }
  }
}
"""

USER_SEARCH = """
query ($search: String!, $perPage: Int!) {
  Page(page: 1, perPage: $perPage) {
    users(search: $search) {
      name
      siteUrl
      about(asHtml: false)
      avatar { large }
      bannerImage
      createdAt
      statistics {
        anime { count episodesWatched }
        manga { count chaptersRead }
      }
    }
  }
}
"""

WEEKLY_SCHEDULE = """
query ($page: Int!, $perPage: Int!, $start: Int!, $end: Int!) {
  Page(page: $page, perPage: $perPage) {
    airingSchedules(
      airingAt_greater: $start
      airingAt_lesser: $end
      sort: TIME
    ) {
      airingAt
      episode
      media {
        title { romaji english native }
        isAdult
      }
    }
  }
}
"""

TOP_MEDIA = """
query (
  $type: MediaType!
  $perPage: Int!
  $yearStart: FuzzyDateInt
  $yearEnd: FuzzyDateInt
  $genre: String
  $season: MediaSeason
  $format: MediaFormat
) {
  Page(page: 1, perPage: $perPage) {
    media(
      type: $type
      startDate_greater: $yearStart
      startDate_lesser: $yearEnd
      genre: $genre
      season: $season
      format: $format
      isAdult: false
      sort: [SCORE_DESC]
    ) {
      title { romaji english native }
      siteUrl
      description(asHtml: false)
      coverImage { large }
      bannerImage
      format
      status
      episodes
      chapters
      volumes
      averageScore
      genres
    }
  }
}
"""


class AniListError(Exception):
    """AniList could not return a usable response."""

    def __init__(self, message: str, status: int | None = None) -> None:
        self.status = status
        super().__init__(message)


class _DescriptionParser(HTMLParser):
    """Turn AniList's lightweight description HTML into Discord-safe text."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "br":
            self.parts.append("\n")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)

    def handle_data(self, data: str) -> None:
        if self.parts and self.parts[-1].endswith("\n") and data.startswith("\n"):
            data = data[1:]
        self.parts.append(data)


def _cut_at_word(description: str, limit: int) -> str:
    shortened = description[:limit].rstrip()
    word_end = max(shortened.rfind(" "), shortened.rfind("\n"))
    return shortened[:word_end].rstrip() if word_end > 0 else shortened


def _clean_description(value: Any, fallback: str = "No synopsis available.") -> str:
    parser = _DescriptionParser()
    parser.feed(str(value or fallback))
    parser.close()
    description = "".join(parser.parts).strip() or fallback
    while "\n\n\n" in description:
        description = description.replace("\n\n\n", "\n\n")
    description = description.replace("~!", "||").replace("!~", "||")
    if len(description) <= DESCRIPTION_LIMIT:
        return description

    shortened = _cut_at_word(description, DESCRIPTION_LIMIT - 1)
    spoiler_close = ""
    if shortened.count("||") % 2:
        shortened = _cut_at_word(description, DESCRIPTION_LIMIT - 3)
        if shortened.count("||") % 2:
            spoiler_close = "||"
    return f"{shortened}…{spoiler_close}"


async def _request(
    session: ClientSession, query: str, variables: dict[str, Any]
) -> dict[str, Any]:
    try:
        async with session.post(
            ANILIST_URL,
            json={"query": query, "variables": variables},
            timeout=ClientTimeout(total=10),
        ) as response:
            if not 200 <= response.status < 300:
                raise AniListError(
                    f"AniList returned HTTP {response.status}.", response.status
                )
            try:
                payload = await response.json(content_type=None)
            except (TypeError, ValueError) as error:
                raise AniListError("AniList returned invalid JSON.") from error
    except (ClientError, TimeoutError) as error:
        raise AniListError("Could not reach AniList.") from error

    if not isinstance(payload, dict) or payload.get("errors"):
        raise AniListError("AniList rejected the search.")
    return payload


def _page_results(payload: dict[str, Any], field: str) -> list[dict[str, Any]]:
    data = payload.get("data")
    page = data.get("Page") if isinstance(data, dict) else None
    results = page.get(field) if isinstance(page, dict) else None
    if not isinstance(results, list) or not all(
        isinstance(result, dict) for result in results
    ):
        raise AniListError("AniList returned an unexpected response.")
    return results


async def _search_results(
    session: ClientSession,
    query: str,
    search_type: SearchType,
    limit: int = SEARCH_RESULT_LIMIT,
) -> list[dict[str, Any]]:
    # AniList permits only one result collection in each Page query. Keep the documents
    # distinct while sharing their transport and response parsing.
    variables: dict[str, Any] = {"search": query, "perPage": limit}
    if search_type == "CHARACTER":
        document, result_field = CHARACTER_SEARCH, "characters"
    elif search_type == "USER":
        document, result_field = USER_SEARCH, "users"
    else:
        document, result_field = MEDIA_SEARCH, "media"
        variables["type"] = search_type
    try:
        payload = await _request(session, document, variables)
    except AniListError as error:
        if error.status != 403 or search_type not in ("ANIME", "MANGA"):
            raise
        # This AniList search boundary owns the media-only, 403-only handoff.
        logger.warning(
            "AniList %s search returned 403; using Tenrai", search_type.lower()
        )
        try:
            payload = await search_tenrai_media(session, query, search_type, limit)
        except TenraiError as fallback_error:
            logger.warning(
                "Tenrai %s fallback failed with status %s",
                search_type.lower(),
                fallback_error.status,
            )
            raise error from fallback_error
    return _page_results(payload, result_field)


async def _weekly_schedule_results(
    session: ClientSession, week_start: int, week_end: int
) -> list[dict[str, Any]]:
    page = 1
    results: list[dict[str, Any]] = []
    try:
        while True:
            payload = await _request(
                session,
                WEEKLY_SCHEDULE,
                {
                    "page": page,
                    "perPage": WEEKLY_QUERY_PAGE_SIZE,
                    # AniList's range filters are exclusive; subtract one second to
                    # include an airing exactly at Monday 00:00 UTC.
                    "start": week_start - 1,
                    "end": week_end,
                },
            )
            page_results = _page_results(payload, "airingSchedules")
            for result in page_results:
                media = result.get("media")
                if isinstance(media, dict) and media.get("isAdult") is not True:
                    results.append(result)
            if len(page_results) < WEEKLY_QUERY_PAGE_SIZE:
                return results
            page += 1
    except AniListError as error:
        if error.status != 403:
            raise
        logger.warning("AniList weekly schedule returned 403; using Tenrai")
        try:
            return await weekly_tenrai_schedule(session, week_start, week_end)
        except TenraiError as fallback_error:
            logger.warning(
                "Tenrai weekly schedule fallback failed with status %s",
                fallback_error.status,
            )
            raise error from fallback_error


async def _top_results(
    session: ClientSession,
    media_type: MediaType,
    *,
    year: int | None = None,
    genre: str | None = None,
    season: str | None = None,
    media_format: str | None = None,
) -> list[dict[str, Any]]:
    variables = {
        "type": media_type,
        "perPage": TOP_RESULT_LIMIT,
        "yearStart": year * 10000 - 1 if year is not None else None,
        "yearEnd": (year + 1) * 10000 if year is not None else None,
        "genre": genre,
        "season": season,
        "format": media_format,
    }
    return _page_results(await _request(session, TOP_MEDIA, variables), "media")


def _week_bounds(now: datetime | None = None) -> tuple[int, int]:
    current = (now or datetime.now(UTC)).astimezone(UTC)
    start = (current - timedelta(days=current.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    return int(start.timestamp()), int((start + timedelta(days=7)).timestamp())


async def search_media(
    session: ClientSession, title: str, media_type: MediaType
) -> dict[str, Any] | None:
    """Return AniList's first safe media match for a title."""
    title = title.strip()
    if not title:
        raise ValueError("An AniList search title is required.")
    results = await _search_results(session, title, media_type, 1)
    return results[0] if results else None


async def search_character(session: ClientSession, name: str) -> dict[str, Any] | None:
    """Return AniList's first character match for a name."""
    name = name.strip()
    if not name:
        raise ValueError("An AniList character name is required.")
    results = await _search_results(session, name, "CHARACTER", 1)
    return results[0] if results else None


def _label(value: Any) -> str:
    if not value:
        return "—"
    return " ".join(
        word if word in {"TV", "OVA", "ONA"} else word.title()
        for word in str(value).split("_")
    )


def _result_names(result: dict[str, Any], search_type: SearchType) -> list[str]:
    if search_type == "USER":
        name = result.get("name")
        return [str(name).strip()] if name else []
    names = result.get("name" if search_type == "CHARACTER" else "title")
    if not isinstance(names, dict):
        return []
    fields = (
        ("full", "native")
        if search_type == "CHARACTER"
        else (
            "romaji",
            "english",
            "native",
        )
    )
    return [str(names[field]).strip() for field in fields if names.get(field)]


def media_embed(
    media: dict[str, Any], media_type: MediaType, color: int, *, cached: bool = False
) -> Embed:
    """Build a compact, linked embed for one anime or manga result."""
    titles = media.get("title")
    if not isinstance(titles, dict):
        titles = {}
    title = (
        titles.get("romaji")
        or titles.get("english")
        or titles.get("native")
        or f"Unknown {media_type.lower()}"
    )
    description = _clean_description(media.get("description"))

    site_url = media.get("siteUrl")
    embed = Embed(
        title=title[:256],
        url=site_url if isinstance(site_url, str) else None,
        description=description,
        color=color,
    )
    score = media.get("averageScore")
    metrics = [("⭐ Score", f"{score}/100" if isinstance(score, int) else "—")]
    if media_type == "ANIME":
        metrics.extend(
            (
                ("🎬 Episodes", str(media.get("episodes") or "—")),
                ("📡 Status", _label(media.get("status"))),
            )
        )
    else:
        metrics.extend(
            (
                (
                    "📚 Ch / Vol",
                    f"{media.get('chapters') or '—'} / {media.get('volumes') or '—'}",
                ),
                ("📡 Status", _label(media.get("status"))),
            )
        )
    for name, value in metrics:
        embed.add_field(name=name, value=value, inline=True)

    footer = [_label(media.get("format"))]
    genres = media.get("genres")
    if isinstance(genres, list) and genres:
        footer.extend(map(str, genres))
    embed.set_footer(text=" • ".join(footer)[:2048])

    cover = media.get("coverImage")
    cover_url = cover.get("large") if isinstance(cover, dict) else None
    if isinstance(cover_url, str):
        embed.set_thumbnail(url=cover_url)
    banner_url = media.get("bannerImage")
    if isinstance(banner_url, str):
        embed.set_image(url=banner_url)
    # The normalized provider marker controls attribution in the shared embed.
    provider = "Tenrai" if media.get("_provider") == "Tenrai" else "AniList"
    author = f"{provider} • Cache Hit" if cached else provider
    embed.set_author(
        name=author,
        url="https://tenrai.org/" if provider == "Tenrai" else "https://anilist.co/",
    )
    return embed


def character_embed(
    character: dict[str, Any], color: int, *, cached: bool = False
) -> Embed:
    """Build a compact, linked embed for one character result."""
    names = character.get("name")
    if not isinstance(names, dict):
        names = {}
    title = names.get("full") or names.get("native") or "Unknown character"
    site_url = character.get("siteUrl")
    embed = Embed(
        title=str(title)[:256],
        url=site_url if isinstance(site_url, str) else None,
        description=_clean_description(character.get("description")),
        color=color,
    )
    favourites = character.get("favourites")
    metrics = (
        ("⚧ Gender", str(character.get("gender") or "—")),
        ("🎂 Age", str(character.get("age") or "—")),
        (
            "❤️ Favourites",
            f"{favourites:,}" if isinstance(favourites, int) else "—",
        ),
    )
    for name, value in metrics:
        embed.add_field(name=name, value=value, inline=True)

    native_name = names.get("native")
    footer = "Character"
    if isinstance(native_name, str) and native_name != title:
        footer = f"{footer} • {native_name}"
    embed.set_footer(text=footer[:2048])

    image = character.get("image")
    image_url = image.get("large") if isinstance(image, dict) else None
    if isinstance(image_url, str):
        embed.set_thumbnail(url=image_url)
    author = "AniList • Cache Hit" if cached else "AniList"
    embed.set_author(name=author, url="https://anilist.co/")
    return embed


def user_embed(user: dict[str, Any], color: int, *, cached: bool = False) -> Embed:
    """Build a linked overview of one AniList user's public profile."""
    name = str(user.get("name") or "Unknown user")
    site_url = user.get("siteUrl")
    embed = Embed(
        title=name[:256],
        url=site_url if isinstance(site_url, str) else None,
        description=_clean_description(user.get("about"), "No profile bio available."),
        color=color,
    )

    statistics = user.get("statistics")
    if not isinstance(statistics, dict):
        statistics = {}
    categories = (
        ("📺 Anime", statistics.get("anime"), "episodesWatched", "episodes"),
        ("📚 Manga", statistics.get("manga"), "chaptersRead", "chapters"),
    )
    for label, values, progress_key, progress_label in categories:
        if not isinstance(values, dict):
            values = {}
        count = values.get("count")
        progress = values.get(progress_key)
        embed.add_field(
            name=label,
            value=(f"{count:,} entries\n" if isinstance(count, int) else "— entries\n")
            + (
                f"{progress:,} {progress_label}"
                if isinstance(progress, int)
                else f"— {progress_label}"
            ),
            inline=True,
        )

    created_at = user.get("createdAt")
    embed.add_field(
        name="📅 Joined",
        value=f"<t:{created_at}:D>" if isinstance(created_at, int) else "—",
        inline=True,
    )
    embed.set_footer(text="AniList user profile")

    avatar = user.get("avatar")
    avatar_url = avatar.get("large") if isinstance(avatar, dict) else None
    if isinstance(avatar_url, str):
        embed.set_thumbnail(url=avatar_url)
    banner_url = user.get("bannerImage")
    if isinstance(banner_url, str):
        embed.set_image(url=banner_url)
    author = "AniList • Cache Hit" if cached else "AniList"
    embed.set_author(name=author, url="https://anilist.co/")
    return embed


def result_embed(
    result: dict[str, Any], search_type: SearchType, color: int, *, cached: bool
) -> Embed:
    if search_type == "USER":
        return user_embed(result, color, cached=cached)
    return (
        character_embed(result, color, cached=cached)
        if search_type == "CHARACTER"
        else media_embed(result, search_type, color, cached=cached)
    )


def _schedule_line(entry: dict[str, Any]) -> str:
    media = entry.get("media")
    if not isinstance(media, dict):
        media = {}
    title = next(iter(_result_names(media, "ANIME")), "Unknown anime")
    display_title = title[:100].replace("\n", " ")

    airing_at = entry.get("airingAt")
    parts = [f"<t:{airing_at}:F>"] if isinstance(airing_at, int) else ["**Time TBA**"]
    episode = entry.get("episode")
    if isinstance(episode, int):
        parts.append(f"Episode {episode}")
    parts.append(display_title)
    return " • ".join(parts)


def weekly_embeds(
    entries: list[dict[str, Any]], color: int, *, cached: bool
) -> list[Embed]:
    """Build bounded chronological pages from one cached weekly schedule."""
    ordered = sorted(
        entries,
        key=lambda entry: (
            not isinstance(entry.get("airingAt"), int),
            entry.get("airingAt") if isinstance(entry.get("airingAt"), int) else 0,
            _schedule_line(entry).casefold(),
        ),
    )
    lines = [_schedule_line(entry) for entry in ordered]
    if not lines:
        return []

    descriptions = [
        "\n".join(lines[index : index + WEEKLY_PAGE_SIZE])
        for index in range(0, len(lines), WEEKLY_PAGE_SIZE)
    ]

    provider = "Tenrai" if entries[0].get("_provider") == "Tenrai" else "AniList"
    author = f"{provider} • Cache Hit" if cached else provider
    author_url = (
        "https://tenrai.org/" if provider == "Tenrai" else "https://anilist.co/"
    )
    pages: list[Embed] = []
    for index, description in enumerate(descriptions, start=1):
        embed = Embed(
            title="Anime Airing This Week",
            description=description,
            color=color,
        )
        embed.set_author(name=author, url=author_url)
        detail = (
            "Tenrai broadcast times • Episode numbers unavailable"
            if provider == "Tenrai"
            else "Times shown in your timezone"
        )
        embed.set_footer(text=f"Page {index}/{len(descriptions)} • {detail}")
        pages.append(embed)
    return pages


class _OwnedPagination(View):
    """Navigate owner-only embed pages and disable controls on timeout."""

    def __init__(self, page_count: int, owner_id: int) -> None:
        super().__init__(timeout=5 * 60)
        self.page_count = page_count
        self.owner_id = owner_id
        self.index = 0
        self.message: Message | None = None
        self._sync_buttons()

    def _sync_buttons(self) -> None:
        previous, following = self.children
        if isinstance(previous, Button):
            previous.disabled = self.index == 0
        if isinstance(following, Button):
            following.disabled = self.index == self.page_count - 1

    async def interaction_check(self, interaction: Interaction) -> bool:
        if interaction.user.id == self.owner_id:
            return True
        await interaction.response.send_message(
            ":x: Only the person who searched can change this result.", ephemeral=True
        )
        return False

    async def on_timeout(self) -> None:
        for item in self.children:
            if isinstance(item, Button):
                item.disabled = True
        if self.message is not None:
            try:
                await self.message.edit(view=self)
            except HTTPException:
                pass

    @button(emoji="⬅️", label="Previous", style=ButtonStyle.secondary)
    async def previous_result(self, interaction: Interaction, _button: Button) -> None:
        self.index -= 1
        self._sync_buttons()
        await interaction.response.edit_message(embed=self.current_embed(), view=self)

    @button(emoji="➡️", label="Next", style=ButtonStyle.secondary)
    async def next_result(self, interaction: Interaction, _button: Button) -> None:
        self.index += 1
        self._sync_buttons()
        await interaction.response.edit_message(embed=self.current_embed(), view=self)


class AniListPagination(_OwnedPagination):
    """Navigate one cached AniList result set without more API requests."""

    def __init__(
        self,
        results: list[dict[str, Any]],
        search_type: SearchType,
        color: int,
        *,
        cached: bool,
        owner_id: int,
        page_label: str = "Page",
    ) -> None:
        self.results = results
        self.search_type = search_type
        self.color = color
        self.cached = cached
        self.page_label = page_label
        super().__init__(len(results), owner_id)

    def current_embed(self) -> Embed:
        embed = result_embed(
            self.results[self.index],
            self.search_type,
            self.color,
            cached=self.cached,
        )
        footer = embed.footer.text or ""
        embed.set_footer(
            text=(f"{self.page_label} {self.index + 1}/{len(self.results)} • {footer}")[
                :2048
            ]
        )
        return embed


class WeeklyPagination(_OwnedPagination):
    """Navigate prebuilt weekly schedule embeds without more API requests."""

    def __init__(self, pages: list[Embed], owner_id: int) -> None:
        self.pages = pages
        super().__init__(len(pages), owner_id)

    def current_embed(self) -> Embed:
        return self.pages[self.index]


class AniListCog(
    commands.GroupCog,
    group_name="anilist",
    group_description="Search AniList's public catalogue.",
):
    """Search AniList's public catalogue."""

    def __init__(self, bot: Sakamoto) -> None:
        self.bot = bot
        self.search_cache: dict[
            tuple[str, str], tuple[float, list[dict[str, Any]]]
        ] = {}
        self.search_lock = Lock()
        self.autocomplete_lock = Lock()
        self.weekly_cache: tuple[int, float, list[dict[str, Any]]] | None = None
        self.weekly_lock = Lock()

    def _store_cache(
        self,
        key: tuple[str, str],
        results: list[dict[str, Any]],
        *,
        expires_at: float | None = None,
    ) -> None:
        now = monotonic()
        self.search_cache = {
            cache_key: value
            for cache_key, value in self.search_cache.items()
            if value[0] > now and cache_key != key
        }
        while len(self.search_cache) >= CACHE_LIMIT:
            self.search_cache.pop(next(iter(self.search_cache)))
        self.search_cache[key] = (
            expires_at if expires_at is not None else now + CACHE_TTL_SECONDS,
            results,
        )

    async def _cached_search(
        self, query: str, search_type: SearchType
    ) -> tuple[list[dict[str, Any]], bool]:
        key = (search_type, " ".join(query.casefold().split()))
        if (cached := self.search_cache.get(key)) and cached[0] > monotonic():
            return cached[1], True

        # Recheck after taking the lock so simultaneous equivalent misses share one
        # upstream request instead of merely running one after another.
        async with self.search_lock:
            if (cached := self.search_cache.get(key)) and cached[0] > monotonic():
                return cached[1], True
            result = await _search_results(self.bot.session, query, search_type)
            self._store_cache(key, result)
            return result, False

    async def _cached_weekly_schedule(
        self, week_start: int, week_end: int
    ) -> tuple[list[dict[str, Any]], bool]:
        cached = self.weekly_cache
        if cached is not None and cached[0] == week_start and cached[1] > monotonic():
            return cached[2], True

        async with self.weekly_lock:
            cached = self.weekly_cache
            if (
                cached is not None
                and cached[0] == week_start
                and cached[1] > monotonic()
            ):
                return cached[2], True
            results = await _weekly_schedule_results(
                self.bot.session, week_start, week_end
            )
            self.weekly_cache = (
                week_start,
                monotonic() + WEEKLY_CACHE_TTL_SECONDS,
                results,
            )
            return results, False

    async def _cached_top(
        self,
        media_type: MediaType,
        *,
        year: int | None = None,
        genre: str | None = None,
        season: str | None = None,
        media_format: str | None = None,
    ) -> tuple[list[dict[str, Any]], bool]:
        genre = " ".join(genre.split()) if genre else None
        if season is not None and year is None:
            year = datetime.now(UTC).year
        filters = (year, genre.casefold() if genre else None, season, media_format)
        key = (f"TOP_{media_type}", repr(filters))
        if (cached := self.search_cache.get(key)) and cached[0] > monotonic():
            return cached[1], True

        async with self.search_lock:
            if (cached := self.search_cache.get(key)) and cached[0] > monotonic():
                return cached[1], True
            fallback_args = {
                "year": year,
                "genre": genre,
                "season": season,
                "media_format": media_format,
            }
            try:
                results = await _top_results(
                    self.bot.session, media_type, **fallback_args
                )
            except AniListError as error:
                if error.status != 403:
                    raise
                logger.warning("AniList top ranking returned 403; using Tenrai")
                try:
                    payload = await top_tenrai_media(
                        self.bot.session,
                        media_type,
                        TOP_RESULT_LIMIT,
                        **fallback_args,
                    )
                    results = _page_results(payload, "media")
                except TenraiError as fallback_error:
                    logger.warning(
                        "Tenrai top ranking fallback failed with status %s",
                        fallback_error.status,
                    )
                    raise error from fallback_error
            self._store_cache(key, results)
            return results, False

    async def search_query_autocomplete(
        self, interaction: Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        query = current.strip()
        command_name = interaction.command.name if interaction.command else ""
        search_type = {
            "anime": "ANIME",
            "manga": "MANGA",
            "character": "CHARACTER",
            "user": "USER",
        }.get(command_name)
        if (
            len(query) < AUTOCOMPLETE_MIN_LENGTH
            or search_type is None
            or self.bot.session is None
        ):
            return []

        normalized = " ".join(query.casefold().split())
        async with self.autocomplete_lock:
            now = monotonic()
            cached_query = ""
            results: list[dict[str, Any]] | None = None
            for (result_type, result_query), (
                expires_at,
                cached,
            ) in self.search_cache.items():
                if (
                    result_type == search_type
                    and expires_at > now
                    and normalized.startswith(result_query)
                    and len(result_query) > len(cached_query)
                ):
                    cached_query = result_query
                    results = cached

            matches = [
                result
                for result in results or []
                if any(
                    normalized in " ".join(name.casefold().split())
                    for name in _result_names(result, search_type)
                )
            ]
            if results is None or (not matches and cached_query != normalized):
                try:
                    matches, _ = await self._cached_search(query, search_type)
                except AniListError as error:
                    logger.warning(
                        "AniList %s autocomplete failed with status %s",
                        search_type.lower(),
                        error.status,
                    )
                    return []

            self._store_cache((search_type, normalized), matches)
            choices: list[app_commands.Choice[str]] = []
            seen: set[str] = set()
            for result in matches:
                names = _result_names(result, search_type)
                if not names:
                    continue
                name = next(
                    (
                        candidate
                        for candidate in names
                        if normalized in " ".join(candidate.casefold().split())
                    ),
                    names[0],
                )[:100]
                if not name or name.casefold() in seen:
                    continue
                seen.add(name.casefold())
                choices.append(app_commands.Choice(name=name, value=name))
                # A selected suggestion should resolve to its exact result from cache,
                # not cause a second search or reopen the broader suggestion set.
                self._store_cache(
                    (search_type, " ".join(name.casefold().split())), [result]
                )
                if len(choices) == SEARCH_RESULT_LIMIT:
                    break
            return choices

    @app_commands.command(name="anime", description="Search AniList for an anime.")
    @app_commands.describe(query="Anime title to search for.")
    @app_commands.autocomplete(query=search_query_autocomplete)
    async def anime(self, interaction: Interaction, query: str) -> None:
        await self._search_command(interaction, query, "ANIME")

    @app_commands.command(name="manga", description="Search AniList for a manga.")
    @app_commands.describe(query="Manga title to search for.")
    @app_commands.autocomplete(query=search_query_autocomplete)
    async def manga(self, interaction: Interaction, query: str) -> None:
        await self._search_command(interaction, query, "MANGA")

    @app_commands.command(
        name="character", description="Search AniList for a character."
    )
    @app_commands.describe(query="Character name to search for.")
    @app_commands.autocomplete(query=search_query_autocomplete)
    async def character(self, interaction: Interaction, query: str) -> None:
        await self._search_command(interaction, query, "CHARACTER")

    @app_commands.command(name="user", description="Search for an AniList user.")
    @app_commands.describe(query="AniList username to search for.")
    @app_commands.autocomplete(query=search_query_autocomplete)
    async def user(self, interaction: Interaction, query: str) -> None:
        await self._search_command(interaction, query, "USER")

    @app_commands.command(
        name="top", description="Browse top-ranked anime or manga on AniList."
    )
    @app_commands.describe(
        media_type="The kind of media to rank (defaults to Anime).",
        year="Only include titles from this year.",
        genre="Only include titles in this genre.",
        season="Only include titles from this anime season.",
        format="Only include titles in this release format.",
    )
    @app_commands.choices(
        media_type=[
            app_commands.Choice(name="Anime", value="ANIME"),
            app_commands.Choice(name="Manga", value="MANGA"),
        ],
        genre=[
            app_commands.Choice(name=value, value=value)
            for value in (
                "Action",
                "Adventure",
                "Comedy",
                "Drama",
                "Ecchi",
                "Fantasy",
                "Horror",
                "Mahou Shoujo",
                "Mecha",
                "Music",
                "Mystery",
                "Psychological",
                "Romance",
                "Sci-Fi",
                "Slice of Life",
                "Sports",
                "Supernatural",
                "Thriller",
            )
        ],
        season=[
            app_commands.Choice(name=value.title(), value=value)
            for value in ("WINTER", "SPRING", "SUMMER", "FALL")
        ],
        format=[
            app_commands.Choice(name=_label(value), value=value)
            for value in (
                "TV",
                "TV_SHORT",
                "MOVIE",
                "SPECIAL",
                "OVA",
                "ONA",
                "MUSIC",
                "MANGA",
                "NOVEL",
                "ONE_SHOT",
            )
        ],
    )
    async def top(
        self,
        interaction: Interaction,
        media_type: str = "ANIME",
        year: app_commands.Range[int, 1900, 2100] | None = None,
        genre: str | None = None,
        season: str | None = None,
        format: str | None = None,
    ) -> None:
        if self.bot.session is None:
            await interaction.response.send_message(
                ":x: The bot's HTTP session is not ready. Please try again later.",
                ephemeral=True,
            )
            return

        selected_type: MediaType = "MANGA" if media_type == "MANGA" else "ANIME"
        await interaction.response.defer()
        try:
            results, cached = await self._cached_top(
                selected_type,
                year=year,
                genre=genre,
                season=season,
                media_format=format,
            )
        except AniListError as error:
            logger.warning("AniList top ranking failed with status %s", error.status)
            message = (
                ":x: AniList has temporarily disabled its API. Please try again later."
                if error.status == 403
                else ":x: AniList is unavailable. Please try again later."
            )
            await interaction.followup.send(message, ephemeral=True)
            return

        if not results:
            await interaction.followup.send(
                f":mag: No {selected_type.lower()} found with those filters.",
                ephemeral=True,
            )
            return
        if len(results) == 1:
            embed = media_embed(
                results[0], selected_type, self.bot.color, cached=cached
            )
            embed.set_footer(text=f"Rank 1/1 • {embed.footer.text or ''}"[:2048])
            await interaction.followup.send(embed=embed)
            return

        view = AniListPagination(
            results,
            selected_type,
            self.bot.color,
            cached=cached,
            owner_id=interaction.user.id,
            page_label="Rank",
        )
        view.message = await interaction.followup.send(
            embed=view.current_embed(), view=view, wait=True
        )

    @app_commands.command(
        name="weekly", description="Show anime airing during the current week."
    )
    async def weekly(self, interaction: Interaction) -> None:
        if self.bot.session is None:
            await interaction.response.send_message(
                ":x: The bot's HTTP session is not ready. Please try again later.",
                ephemeral=True,
            )
            return

        week_start, week_end = _week_bounds()
        await interaction.response.defer()
        try:
            entries, cached = await self._cached_weekly_schedule(week_start, week_end)
        except AniListError as error:
            logger.warning(
                "AniList weekly schedule failed with status %s", error.status
            )
            message = (
                ":x: AniList has temporarily disabled its API. Please try again later."
                if error.status == 403
                else ":x: AniList is unavailable. Please try again later."
            )
            await interaction.followup.send(message, ephemeral=True)
            return

        pages = weekly_embeds(entries, self.bot.color, cached=cached)
        if not pages:
            await interaction.followup.send(
                ":mag: No anime are scheduled to air this week.", ephemeral=True
            )
            return
        if len(pages) == 1:
            await interaction.followup.send(embed=pages[0])
            return

        view = WeeklyPagination(pages, interaction.user.id)
        view.message = await interaction.followup.send(
            embed=view.current_embed(), view=view, wait=True
        )

    async def _search_command(
        self, interaction: Interaction, query: str, search_type: SearchType
    ) -> None:
        label = search_type.lower()
        query = query.strip()
        if not query:
            query_kind = "title" if search_type in ("ANIME", "MANGA") else "name"
            await interaction.response.send_message(
                f":x: Enter a {label} {query_kind} to search for.", ephemeral=True
            )
            return
        if self.bot.session is None:
            await interaction.response.send_message(
                ":x: The bot's HTTP session is not ready. Please try again later.",
                ephemeral=True,
            )
            return

        await interaction.response.defer()
        try:
            result, cached = await self._cached_search(query, search_type)
        except AniListError as error:
            logger.warning(
                "AniList %s search failed with status %s", label, error.status
            )
            message = (
                ":x: AniList has temporarily disabled its API. Please try again later."
                if error.status == 403
                else ":x: AniList is unavailable. Please try again later."
            )
            await interaction.followup.send(message, ephemeral=True)
            return

        if not result:
            await interaction.followup.send(
                f":mag: No {label} found for `{query}`.", ephemeral=True
            )
            return
        if len(result) == 1:
            await interaction.followup.send(
                embed=result_embed(
                    result[0], search_type, self.bot.color, cached=cached
                )
            )
            return

        view = AniListPagination(
            result,
            search_type,
            self.bot.color,
            cached=cached,
            owner_id=interaction.user.id,
        )
        view.message = await interaction.followup.send(
            embed=view.current_embed(), view=view, wait=True
        )


async def setup(bot: Sakamoto) -> None:
    """Add the AniList cog to the bot."""
    await bot.add_cog(AniListCog(bot))
