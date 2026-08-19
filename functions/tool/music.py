import logging
from asyncio import gather, get_running_loop
from collections import deque
from random import shuffle
from time import time
from typing import TYPE_CHECKING, Any, cast
from urllib.parse import parse_qs, urlparse

from discord import Embed, Interaction, Member, app_commands
from discord.abc import Messageable
from discord.ext import commands
from yt_dlp import YoutubeDL

from ._audio_engine import get_audio_engine

if TYPE_CHECKING:
    from main import Sakamoto

logger = logging.getLogger(__name__)


class MusicCog(commands.Cog):
    """Cog for music playback and shared audio controls."""

    def __init__(self, bot: "Sakamoto"):
        self.bot = bot
        self.engine = get_audio_engine(bot)
        self.ydl_opts: dict[str, Any] = {
            "format": "ba[abr>0][vcodec=none]/bestaudio/best",
            "quiet": True,
            "nocheckcertificate": True,
            "no_warnings": True,
            "source_address": "0.0.0.0",
            "ignoreerrors": True,
            "js_runtimes": {"quickjs": {}},
            "extractor_args": {"youtube": {"player_client": ["web_embedded"]}},
        }
        self.source_cache: dict[str, tuple[float, dict]] = {}

    async def interaction_check(self, interaction: Interaction) -> bool:
        if interaction.guild_id is not None:
            return True
        await interaction.response.send_message(":x: Could not determine guild ID.", ephemeral=True)
        return False

    async def play_query_autocomplete(self, _interaction: Interaction, current: str) -> list[app_commands.Choice[str]]:
        if len(query := current.strip()) < 2: return []
        if (session := self.bot.session) is None: return []
        try:
            async with session.get(
                "https://suggestqueries.google.com/complete/search",
                params={"client": "firefox", "ds": "yt", "q": query}
            ) as r:
                return [app_commands.Choice(name=s[:100], value=s[:100]) for s in (await r.json(content_type=None))[1][:5]]
        except Exception as error:
            logger.error("Autocomplete failed for query '%s': %s", query, error)
            return []

    @app_commands.command(name="play", description="Play a song or audio. Provide a search term or URL.")
    @app_commands.autocomplete(query=play_query_autocomplete)
    async def play(self, interaction: Interaction, query: str):
        if not query:
            await interaction.response.send_message(":x: You must provide a search term or URL.", ephemeral=True)
            return

        assert (guild_id := interaction.guild_id) is not None
        if not isinstance(user := interaction.user, Member):
            await interaction.response.send_message(":x: This command can only be used in a server.", ephemeral=True)
            return

        if not user.voice or not user.voice.channel:
            await interaction.response.send_message(":x: You need to be in a voice channel to use this command.", ephemeral=True)
            return

        await interaction.response.defer()
        channel = interaction.channel
        if channel is None or not hasattr(channel, "send"):
            await interaction.followup.send(":x: This command must be used in a text channel.", ephemeral=True)
            return

        existing_vc = self.engine.voice_clients.get(guild_id)
        was_connected = existing_vc is not None and existing_vc.is_connected()
        # Connecting to Discord and resolving a YouTube stream are independent network waits.
        # Start both now so a new request only waits for the slower one.
        voice_client, info = await gather(
            self.engine.get_or_connect_voice_client(guild_id, user.voice.channel, interaction),
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
            self.engine.command_channels[guild_id] = cast(Messageable, channel)

            if self.is_url(query) and info.get("_type") in ["playlist", "multi_video"]:
                entries = list(info.get("entries") or [])
                if not entries:
                    raise ValueError("The playlist is empty or private.")

                await self.engine.enqueue_playlist(
                    guild_id=guild_id,
                    entries=entries,
                    followup=interaction.followup.send,
                    refresh_stream=self.refresh_stream_url,
                )
                return

            track_info = self.first_track(info)

            stream_url = track_info.get("url")
            webpage_url = self.source_url(track_info, requested_url=query)
            if not webpage_url:
                raise ValueError("No source URL found.")

            title = track_info.get("title", "Unknown Title")
            duration = track_info.get("duration_string") or str(track_info.get("duration", "N/A"))

            await self.engine.enqueue_or_play(
                guild_id,
                source_url=webpage_url,
                title=title,
                duration=duration,
                stream_url=stream_url,
                followup=interaction.followup.send,
                refresh_stream=self.refresh_stream_url,
            )

        except Exception as error:
            if not was_connected:
                await self.engine.disconnect_and_cleanup(guild_id)
            await interaction.followup.send(f":x: Failed to retrieve audio. Error: {error}", ephemeral=True)

    def search_source(self, query: str) -> dict:
        """Resolve one complete result, or a flat playlist."""
        is_url = self.is_url(query)
        options = dict(self.ydl_opts)
        options["extract_flat"] = "in_playlist" if is_url else False
        options["noplaylist"] = not is_url
        options["playlistend"] = 50
        extraction_query = query if is_url else f"ytsearch1:{query}"

        with YoutubeDL(cast(Any, options)) as youtube_dl_client:
            info = cast(dict, youtube_dl_client.extract_info(extraction_query, download=False))
        if not info or (not is_url and "entries" in info and not info["entries"]):
            raise ValueError("No results found.")
        return info

    async def resolve_source(self, query: str) -> dict:
        """Reuse yt-dlp results until their signed stream URL expires."""
        key = query.strip() if self.is_url(query) else query.strip().casefold()
        if (cached := self.source_cache.get(key)) and cached[0] > time():
            return cached[1]

        info = await get_running_loop().run_in_executor(None, self.search_source, query)
        is_playlist = self.is_url(query) and info.get("_type") in ["playlist", "multi_video"]
        expires_at = time() + 15 * 60
        if not is_playlist and (stream_url := self.first_track(info).get("url")):
            expires_at = self.stream_valid_until(stream_url)

        if expires_at > (now := time()):
            self.source_cache = {key: value for key, value in self.source_cache.items() if value[0] > now}
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
        entries = [entry for entry in info.get("entries") or [] if entry]
        if not entries:
            raise ValueError("No results found.")
        return entries[0]

    @staticmethod
    def source_url(track_info: dict, requested_url: str | None = None) -> str | None:
        if webpage_url := track_info.get("webpage_url"):
            return webpage_url
        if original_url := track_info.get("original_url"):
            return original_url
        if track_info.get("extractor_key") in {"Youtube", "YoutubeSearch"} and track_info.get("id"):
            return f"https://www.youtube.com/watch?v={track_info['id']}"
        if requested_url and MusicCog.is_url(requested_url):
            return requested_url.strip()
        url = track_info.get("url")
        return url if isinstance(url, str) else None

    async def cog_unload(self):
        self.source_cache.clear()
        self.engine.unload()

    @app_commands.command(name="stop", description="Stop the currently playing audio and disconnect.")
    async def stop(self, interaction: Interaction):
        assert (guild_id := interaction.guild_id) is not None
        if await self.engine.ensure_user_in_same_voice_channel(interaction, guild_id) is None:
            return
        await self.engine.disconnect_and_cleanup(guild_id)
        await interaction.response.send_message(":stop_button: Stopped and disconnected.")

    @app_commands.command(name="pause", description="Pause the currently playing audio.")
    async def pause(self, interaction: Interaction):
        assert (guild_id := interaction.guild_id) is not None
        if (voice_client := await self.engine.ensure_user_in_same_voice_channel(interaction, guild_id)) is None:
            return

        if voice_client.is_playing():
            voice_client.pause()
            await interaction.response.send_message(":pause_button: Playback paused.")
        else:
            await interaction.response.send_message(":x: Nothing is currently playing.", ephemeral=True)

    @app_commands.command(name="resume", description="Resume paused audio.")
    async def resume(self, interaction: Interaction):
        assert (guild_id := interaction.guild_id) is not None
        if (voice_client := await self.engine.ensure_user_in_same_voice_channel(interaction, guild_id)) is None:
            return

        if voice_client.is_paused():
            voice_client.resume()
            await interaction.response.send_message(":arrow_forward: Playback resumed.")
        else:
            await interaction.response.send_message(":x: Playback is not paused.", ephemeral=True)

    @app_commands.command(name="queue", description="Show the current music queue. Displays up to 10 items and indicates if there are more.")
    async def queue(self, interaction: Interaction):
        assert (guild_id := interaction.guild_id) is not None
        max_display = 10
        queue_items = []

        if guild_id in self.engine.currently_playing:
            _, title, duration = self.engine.currently_playing[guild_id]
            queue_items.append(f"**Now Playing:** {title} [{duration}]")

        queue = self.engine.queues.get(guild_id, ())
        for i, item in enumerate(list(queue)[:max_display]):
            queue_items.append(f"{i+1}. {item.title} [{item.duration}]")

        if len(queue) > max_display:
            queue_items.append(f"\n...and {len(queue) - max_display} more.")

        if not queue_items:
            await interaction.response.send_message(":x: The music queue is currently empty.")
        else:
            embed = Embed(
                title=":notes: Music Queue",
                description="\n".join(queue_items),
                color=self.bot.color,
            )
            await interaction.response.send_message(embed=embed)

    @app_commands.command(name="skip", description="Skip the current song, or multiple songs if specified.")
    @app_commands.describe(amount="The amount of songs to skip (defaults to 1).")
    async def skip(self, interaction: Interaction, amount: int = 1):
        assert (guild_id := interaction.guild_id) is not None
        if (voice_client := await self.engine.ensure_user_in_same_voice_channel(interaction, guild_id)) is None:
            return

        if not voice_client.is_playing() and not voice_client.is_paused():
            await interaction.response.send_message(":x: Nothing is currently playing.", ephemeral=True)
            return

        queue = self.engine.queues[guild_id]
        queue_length = len(queue)

        if amount > queue_length + 1:
            await interaction.response.send_message(f":x: The amount of entries you are trying to skip ({amount}) is higher than the amount of entries in the queue ({queue_length}). Please try again.", ephemeral=True)
            return

        if amount > 1:
            for _ in range(amount - 1):
                queue.popleft()

        voice_client.stop()

        if amount == 1:
            await interaction.response.send_message(":track_next: Skipped.")
        else:
            await interaction.response.send_message(f":track_next: Skipped {amount} songs.")

    @app_commands.command(name="shuffle", description="Shuffle the current music queue.")
    async def shuffle(self, interaction: Interaction):
        assert (guild_id := interaction.guild_id) is not None
        if guild_id in self.engine.queues and self.engine.queues[guild_id]:
            shuffled = list(self.engine.queues[guild_id])
            shuffle(shuffled)
            self.engine.queues[guild_id] = deque(shuffled)
            await interaction.response.send_message(":twisted_rightwards_arrows: Queue shuffled.")
        else:
            await interaction.response.send_message(":x: The music queue is currently empty.", ephemeral=True)

async def setup(bot: "Sakamoto"):
    """Add the MusicCog to the bot."""
    await bot.add_cog(MusicCog(bot))
