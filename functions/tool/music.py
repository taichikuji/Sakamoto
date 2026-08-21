import logging
from asyncio import gather, get_running_loop
from time import time
from typing import TYPE_CHECKING, Any, cast
from urllib.parse import parse_qs, urlparse

from discord import Embed, Interaction, Member, app_commands
from discord.abc import Messageable
from discord.ext import commands
from yt_dlp import YoutubeDL

from ._audio_engine import QueueItem, get_audio_engine

if TYPE_CHECKING:
    from main import Sakamoto

logger = logging.getLogger(__name__)


class MusicCog(commands.Cog):
    """Cog for music playback and shared audio controls."""

    def __init__(self, bot: "Sakamoto"):
        self.bot = bot
        self.engine = get_audio_engine(bot)
        self.source_cache: dict[str, tuple[float, dict]] = {}

    async def interaction_check(self, interaction: Interaction) -> bool:
        if interaction.guild_id is not None:
            return True
        await interaction.response.send_message(
            ":x: Could not determine guild ID.", ephemeral=True
        )
        return False

    async def play_query_autocomplete(
        self, _interaction: Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        if len(query := current.strip()) < 2:
            return []
        if (session := self.bot.session) is None:
            return []
        try:
            async with session.get(
                "https://suggestqueries.google.com/complete/search",
                params={"client": "firefox", "ds": "yt", "q": query},
            ) as r:
                return [
                    app_commands.Choice(name=s[:100], value=s[:100])
                    for s in (await r.json(content_type=None))[1][:5]
                ]
        except Exception as error:  # pylint: disable=broad-exception-caught
            logger.error("Autocomplete failed for query '%s': %s", query, error)
            return []

    @app_commands.command(
        name="play", description="Play a song or audio. Provide a search term or URL."
    )
    @app_commands.autocomplete(query=play_query_autocomplete)
    async def play(self, interaction: Interaction, query: str):
        if not query:
            await interaction.response.send_message(
                ":x: You must provide a search term or URL.", ephemeral=True
            )
            return

        assert (guild_id := interaction.guild_id) is not None
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

        was_connected = self.engine.is_connected(guild_id)
        # Connecting to Discord and resolving a YouTube stream are independent network waits.
        # Start both now so a new request only waits for the slower one.
        voice_client, info = await gather(
            self.engine.get_or_connect_voice_client(
                guild_id, user.voice.channel, interaction
            ),
            self.resolve_source(query),
            return_exceptions=True,
        )
        if voice_client is None:
            return

        try:
            # Delay source errors until here so the normal cleanup below can disconnect
            # a voice client that was created while the lookup was running.
            if isinstance(info, Exception):
                raise info
            self.engine.set_command_channel(guild_id, cast(Messageable, channel))

            if self.is_url(query) and info.get("_type") in ["playlist", "multi_video"]:
                entries = list(info.get("entries") or [])
                if not entries:
                    raise ValueError("The playlist is empty or private.")

                await self.engine.enqueue_playlist(
                    guild_id=guild_id,
                    items=self.playlist_items(entries),
                    followup=interaction.followup.send,
                )
                return

            await self.engine.enqueue_or_play(
                guild_id,
                self.queue_item(self.first_track(info), requested_url=query),
                followup=interaction.followup.send,
            )

        except Exception as error:  # pylint: disable=broad-exception-caught
            if not was_connected:
                await self.engine.disconnect_and_cleanup(guild_id)
            await interaction.followup.send(
                f":x: Failed to retrieve audio. Error: {error}", ephemeral=True
            )

    def search_source(self, query: str) -> dict:
        """Resolve one complete result, or a flat playlist."""
        is_url = self.is_url(query)
        options: dict[str, Any] = {
            "format": "ba[acodec=opus]/ba[ext=m4a]/bestaudio/best",
            "quiet": True,
            "no_warnings": True,
            "source_address": "0.0.0.0",
            "ignoreerrors": True,
            # Enable only if YouTube playback requires QuickJS; also uncomment Dockerfile support.
            # "js_runtimes": {"quickjs": {}},
            "extract_flat": "in_playlist" if is_url else False,
            "noplaylist": not is_url,
            "playlistend": 50,
        }
        with YoutubeDL(cast(Any, options)) as youtube_dl_client:
            info = cast(
                dict,
                youtube_dl_client.extract_info(
                    query if is_url else f"ytsearch1:{query}", download=False
                ),
            )
        if not info or (not is_url and "entries" in info and not info["entries"]):
            raise ValueError("No results found.")
        return info

    async def resolve_source(self, query: str) -> dict:
        """Reuse yt-dlp results until their signed stream URL expires."""
        key = query.strip() if self.is_url(query) else query.strip().casefold()
        if (cached := self.source_cache.get(key)) and cached[0] > time():
            return cached[1]

        info = await get_running_loop().run_in_executor(None, self.search_source, query)
        is_playlist = self.is_url(query) and info.get("_type") in [
            "playlist",
            "multi_video",
        ]
        expires_at = time() + 15 * 60
        if not is_playlist and (stream_url := self.first_track(info).get("url")):
            expires_at = self.stream_valid_until(stream_url)

        if expires_at > (now := time()):
            self.source_cache = {
                key: value for key, value in self.source_cache.items() if value[0] > now
            }
            self.source_cache[key] = (expires_at, info)
            if not is_playlist:
                track_info = self.first_track(info)
                if source_url := self.source_url(track_info, requested_url=query):
                    self.source_cache[source_url] = (expires_at, track_info)
            while len(self.source_cache) > 256:
                self.source_cache.pop(next(iter(self.source_cache)))
        return info

    async def refresh_stream_url(self, source_url: str) -> str | None:
        info = await self.resolve_source(source_url)
        return self.first_track(info).get("url")

    def stream_valid_until(self, stream_url: str) -> float:
        raw_expiry = (parse_qs(urlparse(stream_url).query).get("expire") or [None])[0]
        try:
            expires_at = float(raw_expiry or "")
        except (TypeError, ValueError):
            expires_at = time() + 30 * 60
        return expires_at - 60

    @staticmethod
    def is_url(query: str) -> bool:
        parsed = urlparse(query.strip())
        return parsed.scheme in {"http", "https"} and bool(parsed.netloc)

    @staticmethod
    def first_track(info: dict) -> dict:
        if "entries" not in info:
            return info
        if not (
            track := next((entry for entry in info.get("entries") or [] if entry), None)
        ):
            raise ValueError("No results found.")
        return track

    @staticmethod
    def source_url(track_info: dict, requested_url: str | None = None) -> str | None:
        if webpage_url := track_info.get("webpage_url"):
            return webpage_url
        if original_url := track_info.get("original_url"):
            return original_url
        if track_info.get("extractor_key") in {
            "Youtube",
            "YoutubeSearch",
        } and track_info.get("id"):
            return f"https://www.youtube.com/watch?v={track_info['id']}"
        if requested_url and MusicCog.is_url(requested_url):
            return requested_url.strip()
        url = track_info.get("url")
        return url if isinstance(url, str) else None

    @staticmethod
    def format_duration(value) -> str:
        if not isinstance(value, (int, float)):
            return "N/A"
        minutes, seconds = divmod(int(value), 60)
        hours, minutes = divmod(minutes, 60)
        return (
            f"{hours}:{minutes:02d}:{seconds:02d}"
            if hours
            else f"{minutes}:{seconds:02d}"
        )

    def queue_item(
        self, track_info: dict, requested_url: str | None = None
    ) -> QueueItem:
        if not (source_url := self.source_url(track_info, requested_url)):
            raise ValueError("No source URL found.")
        return QueueItem(
            source_url=source_url,
            title=track_info.get("title", "Unknown Title"),
            duration=track_info.get("duration_string")
            or self.format_duration(track_info.get("duration")),
            stream_url=track_info.get("url"),
            refresh_stream=self.refresh_stream_url,
        )

    def playlist_items(self, entries: list[dict]) -> list[QueueItem]:
        items = []
        for entry in entries:
            try:
                item = self.queue_item(entry)
                item.stream_url = None
                items.append(item)
            except ValueError:
                continue
        return items

    async def cog_unload(self):
        self.source_cache.clear()

    @app_commands.command(
        name="stop", description="Stop the currently playing audio and disconnect."
    )
    async def stop(self, interaction: Interaction):
        assert (guild_id := interaction.guild_id) is not None
        if (
            await self.engine.ensure_user_in_same_voice_channel(interaction, guild_id)
            is None
        ):
            return
        await self.engine.disconnect_and_cleanup(guild_id)
        await interaction.response.send_message(
            ":stop_button: Stopped and disconnected."
        )

    @app_commands.command(
        name="pause", description="Pause the currently playing audio."
    )
    async def pause(self, interaction: Interaction):
        assert (guild_id := interaction.guild_id) is not None
        if (
            voice_client := await self.engine.ensure_user_in_same_voice_channel(
                interaction, guild_id
            )
        ) is None:
            return

        if voice_client.is_playing():
            voice_client.pause()
            await interaction.response.send_message(":pause_button: Playback paused.")
        else:
            await interaction.response.send_message(
                ":x: Nothing is currently playing.", ephemeral=True
            )

    @app_commands.command(name="resume", description="Resume paused audio.")
    async def resume(self, interaction: Interaction):
        assert (guild_id := interaction.guild_id) is not None
        if (
            voice_client := await self.engine.ensure_user_in_same_voice_channel(
                interaction, guild_id
            )
        ) is None:
            return

        if voice_client.is_paused():
            voice_client.resume()
            await interaction.response.send_message(":arrow_forward: Playback resumed.")
        else:
            await interaction.response.send_message(
                ":x: Playback is not paused.", ephemeral=True
            )

    @app_commands.command(
        name="queue",
        description="Show the current music queue, up to 10 items.",
    )
    async def queue(self, interaction: Interaction):
        assert (guild_id := interaction.guild_id) is not None
        max_display = 10
        queue_items = []

        current, queued = self.engine.queue_snapshot(guild_id)
        if current:
            queue_items.append(f"**Now Playing:** {current.title} [{current.duration}]")

        for i, item in enumerate(queued[:max_display]):
            queue_items.append(f"{i+1}. {item.title} [{item.duration}]")

        if len(queued) > max_display:
            queue_items.append(f"\n...and {len(queued) - max_display} more.")

        if not queue_items:
            await interaction.response.send_message(
                ":x: The music queue is currently empty."
            )
        else:
            embed = Embed(
                title=":notes: Music Queue",
                description="\n".join(queue_items),
                color=self.bot.color,
            )
            await interaction.response.send_message(embed=embed)

    @app_commands.command(
        name="skip",
        description="Skip the current song, or multiple songs if specified.",
    )
    @app_commands.describe(amount="The amount of songs to skip (defaults to 1).")
    async def skip(
        self, interaction: Interaction, amount: app_commands.Range[int, 1] = 1
    ):
        assert (guild_id := interaction.guild_id) is not None
        if (
            voice_client := await self.engine.ensure_user_in_same_voice_channel(
                interaction, guild_id
            )
        ) is None:
            return

        if not voice_client.is_playing() and not voice_client.is_paused():
            await interaction.response.send_message(
                ":x: Nothing is currently playing.", ephemeral=True
            )
            return

        queue_length = self.engine.queued_count(guild_id)

        if amount > queue_length + 1:
            await interaction.response.send_message(
                f":x: You requested {amount} entries, but only {queue_length} are queued. "
                "Please try again.",
                ephemeral=True,
            )
            return
        self.engine.skip_tracks(guild_id, amount)

        message = (
            ":track_next: Skipped."
            if amount == 1
            else f":track_next: Skipped {amount} songs."
        )
        await interaction.response.send_message(message)

    @app_commands.command(
        name="shuffle", description="Shuffle the current music queue."
    )
    async def shuffle(self, interaction: Interaction):
        assert (guild_id := interaction.guild_id) is not None
        if self.engine.shuffle_queue(guild_id):
            await interaction.response.send_message(
                ":twisted_rightwards_arrows: Queue shuffled."
            )
        else:
            await interaction.response.send_message(
                ":x: The music queue is currently empty.", ephemeral=True
            )


async def setup(bot: "Sakamoto"):
    """Add the MusicCog to the bot."""
    await bot.add_cog(MusicCog(bot))
