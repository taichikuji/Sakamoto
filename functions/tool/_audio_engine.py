import logging
from asyncio import run_coroutine_threadsafe
from collections import deque, defaultdict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from discord import (
    FFmpegOpusAudio,
    Interaction,
    Member,
    StageChannel,
    VoiceChannel,
    VoiceClient,
    VoiceState,
)
from discord.abc import Messageable

if TYPE_CHECKING:
    from main import Sakamoto

logger = logging.getLogger(__name__)

StreamResolver = Callable[[str], Awaitable[str | None]]


@dataclass
class QueueItem:
    source_url: str
    title: str
    duration: str
    stream_url: str | None = None
    refresh_stream: StreamResolver | None = None


class AudioEngine:
    """Shared playback state and voice/queue helpers."""

    def __init__(self, bot: "Sakamoto"):
        self.bot = bot
        self.voice_clients: dict[int, VoiceClient] = {}
        self.queues: defaultdict[int, deque[QueueItem]] = defaultdict(deque)
        self.currently_playing: dict[int, tuple[str, str]] = {}
        self.command_channels: dict[int, Messageable] = {}

    async def enqueue_or_play(
        self,
        guild_id: int,
        *,
        source_url: str,
        title: str,
        duration: str,
        stream_url: str | None,
        followup,
        now_playing_message: str | None = None,
        queue_message: str | None = None,
        refresh_stream: StreamResolver | None = None,
    ) -> None:
        voice_client = self.voice_clients.get(guild_id)
        if voice_client is None or not voice_client.is_connected():
            await followup(":x: The bot is not connected to a voice channel.", ephemeral=True)
            return

        queue = self.queues[guild_id]
        if voice_client.is_playing() or voice_client.is_paused() or queue:
            if len(queue) >= 50:
                await followup(":x: Queue is full (50 items).", ephemeral=True)
                return

            queued_stream_url = stream_url if duration == "LIVE" else None
            queue.append(QueueItem(source_url, title, duration, queued_stream_url, refresh_stream))
            await followup(
                queue_message or f":ballot_box_with_check: Added to queue: **{title}** [{duration}]"
            )
            return

        started = await self.play_song(
            guild_id, source_url, stream_url, title, duration, refresh_stream
        )
        if started:
            await followup(now_playing_message or f":notes: Now playing: **{title}** [{duration}]")
        else:
            await followup(":x: Failed to start playback.", ephemeral=True)

    async def enqueue_playlist(
        self,
        guild_id: int,
        entries: list[dict],
        followup,
        refresh_stream: StreamResolver | None = None,
    ) -> None:
        voice_client = self.voice_clients.get(guild_id)
        if voice_client is None or not voice_client.is_connected():
            await followup(":x: The bot is not connected to a voice channel.", ephemeral=True)
            return

        queue = self.queues[guild_id]
        available_slots = 50 - len(queue)

        if available_slots <= 0:
            await followup(":x: Queue is full (50 items).", ephemeral=True)
            return

        queued = len(queue)

        def format_duration(duration_value):
            if not isinstance(duration_value, (int, float)):
                return "N/A"
            minutes, seconds = divmod(int(duration_value), 60)
            hours, minutes = divmod(minutes, 60)
            return f"{hours}:{minutes:02d}:{seconds:02d}" if hours else f"{minutes}:{seconds:02d}"

        for entry in entries[:available_slots]:
            is_youtube = entry.get("extractor_key") in {"Youtube", "YoutubeSearch"}
            url = entry.get("webpage_url") or (
                f"https://www.youtube.com/watch?v={entry['id']}"
                if is_youtube and entry.get("id")
                else entry.get("url")
            )
            if not url:
                continue

            queue.append(
                QueueItem(
                    source_url=url,
                    title=entry.get("title", "Unknown Title"),
                    duration=entry.get("duration_string") or format_duration(entry.get("duration")),
                    stream_url=None,
                    refresh_stream=refresh_stream,
                )
            )

        added = len(queue) - queued
        if not added:
            await followup(":x: The playlist contains no playable tracks.", ephemeral=True)
            return

        if not voice_client.is_playing() and not voice_client.is_paused():
            first = queue.popleft()
            if await self.play_song(
                guild_id, first.source_url, None, first.title, first.duration, refresh_stream
            ):
                await followup(
                    f":notes: Started playlist. Now playing: **{first.title}**\n"
                    f":ballot_box_with_check: Added {added - 1} tracks to the queue."
                )
            else:
                await followup(":x: Failed to start playlist playback.", ephemeral=True)
        else:
            await followup(
                f":ballot_box_with_check: Added **{added}** tracks from the playlist to the queue."
            )

    async def get_or_connect_voice_client(
        self,
        guild_id: int,
        user_voice_channel: VoiceChannel | StageChannel,
        interaction: Interaction,
    ) -> VoiceClient | None:
        voice_client = self.voice_clients.get(guild_id)
        if voice_client is None or not voice_client.is_connected():
            try:
                voice_client = await user_voice_channel.connect(self_deaf=True)
                self.voice_clients[guild_id] = voice_client
            except Exception as error:  # pylint: disable=broad-exception-caught
                await interaction.followup.send(
                    f":x: Failed to connect to the voice channel. Error: {error}", ephemeral=True
                )
                return None
        elif voice_client.channel and voice_client.channel != user_voice_channel:
            await interaction.followup.send(
                ":x: I am already playing in another voice channel.", ephemeral=True
            )
            return None

        return voice_client

    async def play_next_track_and_announce(
        self,
        guild_id: int,
        item: QueueItem,
    ):
        started = await self.play_song(
            guild_id,
            item.source_url,
            item.stream_url,
            item.title,
            item.duration,
            item.refresh_stream,
        )
        if not started:
            return

        if channel := self.command_channels.get(guild_id):
            try:
                if item.duration == "LIVE":
                    await channel.send(f":radio: Playing **{item.title}** on Radio Garden")
                else:
                    await channel.send(f":notes: Now playing: **{item.title}** [{item.duration}]")
            except Exception as error:  # pylint: disable=broad-exception-caught
                logger.warning(
                    "Failed to send now-playing message in guild %s: %s", guild_id, error
                )

    async def ensure_user_in_same_voice_channel(
        self, interaction: Interaction, guild_id: int
    ) -> VoiceClient | None:
        if not isinstance(user := interaction.user, Member):
            await interaction.response.send_message(
                ":x: This command can only be used in a server.", ephemeral=True
            )
            return None

        if not user.voice or not user.voice.channel:
            await interaction.response.send_message(
                ":x: You need to be in a voice channel to use this command.", ephemeral=True
            )
            return None

        voice_client = self.voice_clients.get(guild_id)
        if voice_client is None or not voice_client.is_connected() or voice_client.channel is None:
            await interaction.response.send_message(
                ":x: The bot is not connected to a voice channel.", ephemeral=True
            )
            return None

        if user.voice.channel != voice_client.channel:
            await interaction.response.send_message(
                ":x: You must be in the same voice channel as the bot to use this command.",
                ephemeral=True,
            )
            return None

        return voice_client

    async def play_song(
        self,
        guild_id: int,
        source_url: str,
        stream_url: str | None,
        title: str,
        duration: str,
        refresh_stream: StreamResolver | None = None,
    ) -> bool:
        if not stream_url and refresh_stream is not None:
            try:
                stream_url = await refresh_stream(source_url)
            except Exception as error:  # pylint: disable=broad-exception-caught
                logger.error("Could not refresh URL for %s: %s", title, error)
                self.play_next(guild_id)
                return False

        if not stream_url:
            logger.error("No playable stream URL found for %s", title)
            self.play_next(guild_id)
            return False

        voice_client = self.voice_clients.get(guild_id)
        if voice_client is None or not voice_client.is_connected():
            logger.warning("Voice client disappeared before playback in guild %s", guild_id)
            await self.disconnect_and_cleanup(guild_id)
            return False

        self.currently_playing[guild_id] = (title, duration)
        source = None
        try:
            source = FFmpegOpusAudio(
                stream_url,
                before_options=(
                    "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5 "
                    "-analyzeduration 10M -probesize 10M -err_detect ignore_err"
                ),
                options="-vn",
            )
            voice_client.play(
                source,
                after=lambda error: self.play_next(guild_id, error),
            )
            return True
        except Exception as error:  # pylint: disable=broad-exception-caught
            if source is not None:
                source.cleanup()
            logger.error("Playback failed to start in guild %s: %s", guild_id, error)
            self.play_next(guild_id, error)
            return False

    async def disconnect_and_cleanup(self, guild_id: int):
        if voice_client := self.voice_clients.pop(guild_id, None):
            try:
                voice_client.stop()
                if voice_client.is_connected():
                    await voice_client.disconnect()
            except Exception as error:  # pylint: disable=broad-exception-caught
                logger.error("Error during disconnect for guild %s: %s", guild_id, error)

        self.queues.pop(guild_id, None)
        self.command_channels.pop(guild_id, None)
        self.currently_playing.pop(guild_id, None)

    def play_next(self, guild_id: int, error=None):
        if error:
            logger.error("Player error for guild %s: %s", guild_id, error)

        voice_client = self.voice_clients.get(guild_id)
        if voice_client is None or not voice_client.is_connected():
            run_coroutine_threadsafe(self.disconnect_and_cleanup(guild_id), self.bot.loop)
            return

        queue = self.queues.get(guild_id)
        if queue:
            item = queue.popleft()
            coro = self.play_next_track_and_announce(guild_id, item)
            run_coroutine_threadsafe(coro, self.bot.loop)
            return

        self.currently_playing.pop(guild_id, None)
        run_coroutine_threadsafe(self.disconnect_and_cleanup(guild_id), self.bot.loop)

    def unload(self):
        for guild_id in list(self.voice_clients):
            run_coroutine_threadsafe(self.disconnect_and_cleanup(guild_id), self.bot.loop)

    async def handle_voice_state_update(
        self, member: Member, before: VoiceState, after: VoiceState
    ):
        guild_id = member.guild.id
        voice_client = self.voice_clients.get(guild_id)
        if voice_client is None:
            return

        if not voice_client.is_connected() or not voice_client.channel:
            await self.disconnect_and_cleanup(guild_id)
            return

        if member.bot:
            if (
                self.bot.user
                and member.id == self.bot.user.id
                and after.channel
                and len(after.channel.members) == 1
            ):
                await self.disconnect_and_cleanup(guild_id)
            return

        if before.channel == voice_client.channel and after.channel != voice_client.channel:
            if (
                len(voice_client.channel.members) == 1
                and voice_client.channel.members[0] == self.bot.user
            ):
                await self.disconnect_and_cleanup(guild_id)


def get_audio_engine(bot: "Sakamoto") -> AudioEngine:
    if (engine := getattr(bot, "_audio_engine", None)) is None:
        engine = AudioEngine(bot)
        setattr(bot, "_audio_engine", engine)
        bot.add_listener(engine.handle_voice_state_update, "on_voice_state_update")
    return engine
