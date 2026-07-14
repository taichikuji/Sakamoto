import logging
from asyncio import run_coroutine_threadsafe
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from discord import FFmpegPCMAudio, Interaction, Member, VoiceChannel, VoiceClient, VoiceState

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
        self.queues: dict[int, deque[QueueItem]] = {}
        self.currently_playing: dict[int, tuple[str, str, str]] = {}
        self.command_channels: dict[int, object] = {}
        self.ffmpeg_opts = {
            "before_options": "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5 -analyzeduration 10M -probesize 10M",
            "options": "-vn",
        }

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
        vc = self.voice_clients.get(guild_id)
        if vc is None or not vc.is_connected():
            await followup(":x: The bot is not connected to a voice channel.", ephemeral=True)
            return

        self.queues.setdefault(guild_id, deque())
        if vc.is_playing() or vc.is_paused() or self.queues[guild_id]:
            if len(self.queues[guild_id]) >= 25:
                await followup(":x: Queue is full (25 items).", ephemeral=True)
                return
            queued_stream_url = stream_url if duration == "LIVE" else None
            self.queues[guild_id].append(
                QueueItem(source_url, title, duration, queued_stream_url, refresh_stream)
            )
            await followup(queue_message or f":ballot_box_with_check: Added to queue: **{title}** [{duration}]")
            return

        started = await self.play_song(guild_id, source_url, stream_url, title, duration, refresh_stream)
        if started:
            await followup(now_playing_message or f":notes: Now playing: **{title}** [{duration}]")
        else:
            await followup(":x: Failed to start playback.", ephemeral=True)

    async def get_or_connect_voice_client(
        self, guild_id: int, user_voice_channel: VoiceChannel, interaction: Interaction
    ) -> VoiceClient | None:
        vc = self.voice_clients.get(guild_id)
        if vc is None or not vc.is_connected():
            try:
                vc = await user_voice_channel.connect(self_deaf=True)
                self.voice_clients[guild_id] = vc
            except Exception as e:
                await interaction.followup.send(f":x: Failed to connect to the voice channel. Error: {e}", ephemeral=True)
                return None
        elif vc.channel and vc.channel != user_voice_channel:
            await interaction.followup.send(":x: I am already playing in another voice channel.", ephemeral=True)
            return None
        return vc

    async def play_next_track_and_announce(
        self,
        guild_id: int,
        source_url: str,
        title: str,
        duration: str,
        stream_url: str | None,
        refresh_stream: StreamResolver | None,
    ):
        started = await self.play_song(guild_id, source_url, stream_url, title, duration, refresh_stream)
        if not started:
            return
        if guild_id in self.command_channels and (channel := self.command_channels[guild_id]):
            try:
                if duration == "LIVE":
                    await channel.send(f":radio: Playing **{title}** on Radio Garden")
                else:
                    await channel.send(f":notes: Now playing: **{title}** [{duration}]")
            except Exception as e:
                logger.warning("Failed to send now-playing message in guild %s: %s", guild_id, e)

    async def ensure_user_in_same_voice_channel(self, interaction: Interaction, guild_id: int) -> VoiceClient | None:
        if not isinstance(user := interaction.user, Member):
            await interaction.response.send_message(":x: This command can only be used in a server.", ephemeral=True)
            return None
        if not user.voice or not user.voice.channel:
            await interaction.response.send_message(":x: You need to be in a voice channel to use this command.", ephemeral=True)
            return None

        vc = self.voice_clients.get(guild_id)
        if vc is None or not vc.is_connected() or vc.channel is None:
            await interaction.response.send_message(":x: The bot is not connected to a voice channel.", ephemeral=True)
            return None
        if user.voice.channel != vc.channel:
            await interaction.response.send_message(
                ":x: You must be in the same voice channel as the bot to use this command.",
                ephemeral=True,
            )
            return None
        return vc

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
            except Exception as e:
                logger.error("Could not refresh URL for %s: %s", title, e)
                self.play_next(guild_id)
                return False

        if not stream_url:
            logger.error("No playable stream URL found for %s", title)
            self.play_next(guild_id)
            return False

        vc = self.voice_clients.get(guild_id)
        if vc is None or not vc.is_connected():
            logger.warning("Voice client disappeared before playback in guild %s", guild_id)
            return False

        self.currently_playing[guild_id] = (source_url, title, duration)
        source = None
        try:
            source = FFmpegPCMAudio(
                stream_url,
                before_options=self.ffmpeg_opts["before_options"],
                options=self.ffmpeg_opts["options"],
            )
            vc.play(
                source,
                after=lambda e: self.play_next(guild_id, e),
            )
            return True
        except Exception as e:
            if source is not None:
                source.cleanup()
            logger.error("Playback failed to start in guild %s: %s", guild_id, e)
            self.play_next(guild_id, e)
            return False

    async def disconnect_and_cleanup(self, guild_id: int):
        if vc := self.voice_clients.pop(guild_id, None):
            try:
                vc.stop()
                if vc.is_connected():
                    await vc.disconnect()
            except Exception as e:
                logger.error("Error during disconnect for guild %s: %s", guild_id, e)
        self.queues.pop(guild_id, None)
        self.command_channels.pop(guild_id, None)
        self.currently_playing.pop(guild_id, None)

    def play_next(self, guild_id: int, error=None):
        if error:
            logger.error("Player error for guild %s: %s", guild_id, error)
        vc = self.voice_clients.get(guild_id)
        if vc is None or not vc.is_connected():
            run_coroutine_threadsafe(self.disconnect_and_cleanup(guild_id), self.bot.loop)
            return

        queue = self.queues.get(guild_id)
        if queue:
            item = queue.popleft()
            coro = self.play_next_track_and_announce(
                guild_id,
                item.source_url,
                item.title,
                item.duration,
                item.stream_url,
                item.refresh_stream,
            )
            run_coroutine_threadsafe(coro, self.bot.loop)
            return

        self.currently_playing.pop(guild_id, None)
        run_coroutine_threadsafe(self.disconnect_and_cleanup(guild_id), self.bot.loop)

    def unload(self):
        for guild_id in list(self.voice_clients):
            run_coroutine_threadsafe(self.disconnect_and_cleanup(guild_id), self.bot.loop)

    async def handle_voice_state_update(self, member: Member, before: VoiceState, after: VoiceState):
        guild_id = member.guild.id
        vc = self.voice_clients.get(guild_id)
        if vc is None:
            return

        if not vc.is_connected() or not vc.channel:
            await self.disconnect_and_cleanup(guild_id)
            return

        if member.bot:
            if member.id == self.bot.user.id and after.channel and len(after.channel.members) == 1:
                await self.disconnect_and_cleanup(guild_id)
            return

        if before.channel == vc.channel and after.channel != vc.channel:
            if len(vc.channel.members) == 1 and vc.channel.members[0] == self.bot.user:
                await self.disconnect_and_cleanup(guild_id)


def get_audio_engine(bot: "Sakamoto") -> AudioEngine:
    engine = getattr(bot, "_audio_engine", None)
    if engine is None:
        engine = AudioEngine(bot)
        setattr(bot, "_audio_engine", engine)
    return engine
