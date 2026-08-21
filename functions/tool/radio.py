import logging
from dataclasses import dataclass
from random import choice, sample
from typing import TYPE_CHECKING
from urllib.parse import urljoin, urlparse

from discord import Interaction, Member, app_commands
from discord.ext import commands

from ._audio_engine import QueueItem, get_audio_engine

if TYPE_CHECKING:
    from main import Sakamoto

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RadioStation:
    channel_id: str
    title: str


class RadioCog(
    commands.GroupCog, group_name="radio", group_description="Play radio stations."
):
    """Groupped radio based commands."""

    RADIO_ENDPOINT = "https://radio.garden/api"

    def __init__(self, bot: "Sakamoto"):
        self.bot = bot
        self.engine = get_audio_engine(bot)

    async def search_query_autocomplete(
        self, _interaction: Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        query = current.strip()
        if len(query) < 2:
            return []

        payload = await self.fetch_json(
            f"{self.RADIO_ENDPOINT}/search", params={"q": query}
        )
        hits = (payload or {}).get("hits", {}).get("hits") or []

        choices: list[app_commands.Choice[str]] = []
        seen_values: set[str] = set()

        for hit in hits:
            source = hit.get("_source") or {}
            channel = self.radio_station_page(source)
            if not channel:
                continue

            value = self.channel_id_from_href(channel.get("url") or channel.get("href"))
            if not value or value in seen_values:
                continue

            seen_values.add(value)
            title = str(channel.get("title") or "Unknown Station").strip()
            subtitle = str(channel.get("subtitle") or "").strip()
            label = f"{title} ({subtitle})" if subtitle else title

            choices.append(
                app_commands.Choice(
                    name=label[:100] or "Unknown Station", value=value[:100]
                )
            )

            if len(choices) == 5:
                break
        return choices

    @app_commands.command(
        name="search", description="Play a radio station by search, URL, or channel ID."
    )
    @app_commands.autocomplete(query=search_query_autocomplete)
    @app_commands.describe(query="A station query, radio URL, or channel ID.")
    async def search(self, interaction: Interaction, query: str):
        if not query or not query.strip():
            await interaction.response.send_message(
                ":x: You must provide a station query, URL, or channel ID.",
                ephemeral=True,
            )
            return
        await self.play_resolved_radio_station(interaction, query)

    @app_commands.command(name="balloon", description="Play a random radio station.")
    async def balloon(self, interaction: Interaction):
        await self.play_resolved_radio_station(interaction, None)

    async def play_resolved_radio_station(
        self, interaction: Interaction, query: str | None
    ) -> None:
        if (guild_id := interaction.guild_id) is None:
            await interaction.response.send_message(
                ":x: Could not determine guild ID.", ephemeral=True
            )
            return

        if not isinstance(user := interaction.user, Member):
            await interaction.response.send_message(
                ":x: This command can only be used in a server.", ephemeral=True
            )
            return

        if not user.voice or not user.voice.channel:
            await interaction.response.send_message(
                ":x: You need to be in a voice channel to use this command.",
                ephemeral=True,
            )
            return

        await interaction.response.defer()
        channel = interaction.channel
        if channel is None or not hasattr(channel, "send"):
            await interaction.followup.send(
                ":x: This command must be used in a text channel.", ephemeral=True
            )
            return

        try:
            station = await self.resolve_radio_station(query)
            stream_url = await self.resolve_radio_stream_url(station.channel_id)
        except ValueError as error:
            await interaction.followup.send(f":x: {error}", ephemeral=True)
            return
        except Exception as error:  # pylint: disable=broad-exception-caught
            logger.error("radio resolution failed: %s", error)
            await interaction.followup.send(
                ":x: Failed to reach radio source. Try again later.", ephemeral=True
            )
            return

        if (
            await self.engine.get_or_connect_voice_client(
                guild_id, user.voice.channel, interaction
            )
            is None
        ):
            return

        self.engine.set_command_channel(guild_id, channel)

        await self.engine.enqueue_or_play(
            guild_id,
            QueueItem(
                source_url=self.radio_stream_url(station.channel_id),
                title=station.title,
                duration="LIVE",
                stream_url=stream_url,
            ),
            followup=interaction.followup.send,
            now_playing_message=(
                f":radio: Now playing: **{station.title}** using radio source"
            ),
            queue_message=(
                f":ballot_box_with_check: Added to queue: :radio: **{station.title}** [LIVE]"
            ),
        )

    async def resolve_radio_station(self, query: str | None) -> RadioStation:
        if query is None:
            return await self.pick_random_station()

        raw = query.strip()
        if not raw:
            raise ValueError("You must provide a station query, URL, or channel ID.")

        if channel_id := self.extract_channel_id(raw):
            if channel := (
                await self.fetch_json(
                    f"{self.RADIO_ENDPOINT}/ara/content/channel/{channel_id}"
                )
                or {}
            ).get("data"):
                title = channel.get("title") or "Unknown Station"
                return RadioStation(channel_id, title)

        channel = await self.search_radio_channel(raw)
        if channel is None:
            raise ValueError("No radio station found for that query.")

        if not (channel_id := self.channel_id_from_href(channel.get("url"))):
            raise ValueError(
                "Could not resolve a station stream ID from search results."
            )

        title = channel.get("title") or "Unknown Station"
        return RadioStation(channel_id, title)

    @staticmethod
    def extract_channel_id(value: str) -> str | None:
        raw = value.strip()
        if not raw:
            return None

        parsed = urlparse(value)
        if parsed.scheme and parsed.netloc:
            if parsed.netloc.lower() not in {"radio.garden", "www.radio.garden"}:
                return None
            return RadioCog.channel_id_from_href(parsed.path)

        return raw

    async def pick_random_station(self) -> RadioStation:
        places_payload = await self.fetch_json(
            f"{self.RADIO_ENDPOINT}/ara/content/places"
        )
        places = (places_payload or {}).get("data", {}).get("list") or []
        if not places:
            raise ValueError("No radio places available.")

        for place in sample(places, k=min(len(places), 5)):
            place_id = place.get("id")
            if not place_id:
                continue

            channels = await self.fetch_place_channels(place_id)
            if not channels:
                continue

            station = choice(channels)
            if not (
                channel_id := self.channel_id_from_href(
                    station.get("href") or station.get("url")
                )
            ):
                continue

            title = station.get("title") or "Unknown Station"
            return RadioStation(channel_id, title)

        raise ValueError("Could not find a random radio station. Try again.")

    async def fetch_place_channels(self, place_id: str) -> list[dict]:
        payload = await self.fetch_json(
            f"{self.RADIO_ENDPOINT}/ara/content/page/{place_id}/channels"
        )
        content = (payload or {}).get("data", {}).get("content") or []
        if not content:
            return []

        channels = []
        for item in content[0].get("items") or []:
            if channel := self.radio_station_page(item):
                channels.append(channel)
        return channels

    async def resolve_radio_stream_url(self, channel_id: str) -> str:
        stream_api_url = self.radio_stream_url(channel_id)
        if self.bot.session is None:
            raise RuntimeError("HTTP session is not available.")

        try:
            async with self.bot.session.get(
                stream_api_url, allow_redirects=False, timeout=10
            ) as response:
                redirect_statuses = {301, 302, 303, 307, 308}
                if response.status in redirect_statuses:
                    location = response.headers.get("Location")
                    if location:
                        return urljoin(stream_api_url, location)

                if response.status == 200:
                    return stream_api_url

        except Exception as error:  # pylint: disable=broad-exception-caught
            logger.error("HTTP request failed for %s: %s", stream_api_url, error)
            raise ValueError("Could not resolve a playable radio stream.") from error

        raise ValueError("Could not resolve a playable radio stream.")

    @classmethod
    def radio_stream_url(cls, channel_id: str) -> str:
        return f"{cls.RADIO_ENDPOINT}/ara/content/listen/{channel_id}/channel.mp3"

    async def search_radio_channel(self, query: str) -> dict | None:
        payload = await self.fetch_json(
            f"{self.RADIO_ENDPOINT}/search", params={"q": query.strip()}
        )
        hits = (payload or {}).get("hits", {}).get("hits") or []

        for hit in hits:
            source = hit.get("_source") or {}
            channel = self.radio_station_page(source)
            if not channel:
                continue

            if self.channel_id_from_href(channel.get("url")):
                return channel

        return None

    @staticmethod
    def radio_station_page(source: dict) -> dict | None:
        page = source.get("page")
        if isinstance(page, dict):
            return page if page.get("type") == "channel" else None
        return source if source.get("type") == "channel" else None

    async def fetch_json(self, url: str, *, params: dict | None = None) -> dict | None:
        if self.bot.session is None:
            raise RuntimeError("HTTP session is not available.")
        try:
            async with self.bot.session.get(url, params=params, timeout=10) as response:
                if response.status != 200:
                    return None
                return await response.json(content_type=None)
        except Exception as error:  # pylint: disable=broad-exception-caught
            logger.error("HTTP request failed for %s: %s", url, error)
            return None

    @staticmethod
    def channel_id_from_href(href: str | None) -> str | None:
        if not href:
            return None
        segments = [segment for segment in urlparse(href).path.split("/") if segment]
        if len(segments) >= 3 and segments[-3] == "listen":
            return segments[-1]
        return None


async def setup(bot: "Sakamoto"):
    """Add the RadioCog to the bot."""
    await bot.add_cog(RadioCog(bot))
