import logging
from asyncio import run_coroutine_threadsafe
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field, replace
from random import shuffle
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


@dataclass
class PlaybackSession:
    """All playback state owned by one guild."""

    voice_client: VoiceClient
    queue: deque[QueueItem] = field(default_factory=deque)
    current: QueueItem | None = None
    command_channel: Messageable | None = None


class AudioEngine:
    """Shared voice, queue, and playback behavior."""

    def __init__(self, bot: "Sakamoto"):
        self.bot = bot
        self.sessions: dict[int, PlaybackSession] = {}

    def is_connected(self, guild_id: int) -> bool:
        session = self.sessions.get(guild_id)
        return bool(session and session.voice_client.is_connected())

    def set_command_channel(self, guild_id: int, channel: Messageable) -> None:
        if session := self.sessions.get(guild_id):
            session.command_channel = channel

    def queue_snapshot(
        self, guild_id: int
    ) -> tuple[QueueItem | None, tuple[QueueItem, ...]]:
        if not (session := self.sessions.get(guild_id)):
            return None, ()
        return session.current, tuple(session.queue)

    def queued_count(self, guild_id: int) -> int:
        session = self.sessions.get(guild_id)
        return len(session.queue) if session else 0

    def skip_tracks(self, guild_id: int, amount: int) -> bool:
        session = self.sessions.get(guild_id)
        if not session or amount < 1 or amount > len(session.queue) + 1:
            return False
        for _ in range(amount - 1):
            session.queue.popleft()
        session.voice_client.stop()
        return True

    def shuffle_queue(self, guild_id: int) -> bool:
        if not (session := self.sessions.get(guild_id)) or not session.queue:
            return False
        items = list(session.queue)
        shuffle(items)
        session.queue.clear()
        session.queue.extend(items)
        return True

    async def enqueue_or_play(
        self,
        guild_id: int,
        item: QueueItem,
        followup,
        *,
        now_playing_message: str | None = None,
        queue_message: str | None = None,
    ) -> None:
        session = self.sessions.get(guild_id)
        if session is None or not session.voice_client.is_connected():
            await followup(
                ":x: The bot is not connected to a voice channel.", ephemeral=True
            )
            return

        voice_client = session.voice_client
        if voice_client.is_playing() or voice_client.is_paused() or session.queue:
            if len(session.queue) >= 50:
                await followup(":x: Queue is full (50 items).", ephemeral=True)
                return

            if item.duration != "LIVE":
                item = replace(item, stream_url=None)
            session.queue.append(item)
            await followup(
                queue_message
                or f":ballot_box_with_check: Added to queue: **{item.title}** [{item.duration}]"
            )
            return

        if await self.play_song(guild_id, item):
            await followup(
                now_playing_message
                or f":notes: Now playing: **{item.title}** [{item.duration}]"
            )
        else:
            await followup(":x: Failed to start playback.", ephemeral=True)

    async def enqueue_playlist(
        self,
        guild_id: int,
        items: list[QueueItem],
        followup,
    ) -> None:
        session = self.sessions.get(guild_id)
        if session is None or not session.voice_client.is_connected():
            await followup(
                ":x: The bot is not connected to a voice channel.", ephemeral=True
            )
            return

        available_slots = 50 - len(session.queue)
        if available_slots <= 0:
            await followup(":x: Queue is full (50 items).", ephemeral=True)
            return

        added_items = items[:available_slots]
        if not added_items:
            await followup(
                ":x: The playlist contains no playable tracks.", ephemeral=True
            )
            return

        session.queue.extend(added_items)
        voice_client = session.voice_client
        if not voice_client.is_playing() and not voice_client.is_paused():
            first = session.queue.popleft()
            if await self.play_song(guild_id, first):
                await followup(
                    f":notes: Started playlist. Now playing: **{first.title}**\n"
                    f":ballot_box_with_check: Added {len(added_items) - 1} tracks to the queue."
                )
            else:
                await followup(":x: Failed to start playlist playback.", ephemeral=True)
        else:
            await followup(
                f":ballot_box_with_check: Added **{len(added_items)}** tracks "
                "from the playlist to the queue."
            )

    async def get_or_connect_voice_client(
        self,
        guild_id: int,
        user_voice_channel: VoiceChannel | StageChannel,
        interaction: Interaction,
    ) -> VoiceClient | None:
        session = self.sessions.get(guild_id)
        voice_client = session.voice_client if session else None
        if voice_client is None or not voice_client.is_connected():
            try:
                voice_client = await user_voice_channel.connect(self_deaf=True)
                self.sessions[guild_id] = PlaybackSession(voice_client)
            except Exception as error:  # pylint: disable=broad-exception-caught
                await interaction.followup.send(
                    f":x: Failed to connect to the voice channel. Error: {error}",
                    ephemeral=True,
                )
                return None
        elif voice_client.channel and voice_client.channel != user_voice_channel:
            await interaction.followup.send(
                ":x: I am already playing in another voice channel.", ephemeral=True
            )
            return None

        return voice_client

    async def play_next_track_and_announce(
        self, guild_id: int, item: QueueItem
    ) -> None:
        if not await self.play_song(guild_id, item):
            return

        session = self.sessions.get(guild_id)
        if session and session.command_channel:
            try:
                if item.duration == "LIVE":
                    await session.command_channel.send(
                        f":radio: Playing **{item.title}** on Radio Garden"
                    )
                else:
                    await session.command_channel.send(
                        f":notes: Now playing: **{item.title}** [{item.duration}]"
                    )
            except Exception as error:  # pylint: disable=broad-exception-caught
                logger.warning(
                    "Failed to send now-playing message in guild %s: %s",
                    guild_id,
                    error,
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
                ":x: You need to be in a voice channel to use this command.",
                ephemeral=True,
            )
            return None

        session = self.sessions.get(guild_id)
        voice_client = session.voice_client if session else None
        if (
            voice_client is None
            or not voice_client.is_connected()
            or voice_client.channel is None
        ):
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

    async def play_song(self, guild_id: int, item: QueueItem) -> bool:
        stream_url = item.stream_url
        if not stream_url and item.refresh_stream is not None:
            try:
                stream_url = await item.refresh_stream(item.source_url)
            except Exception as error:  # pylint: disable=broad-exception-caught
                logger.error("Could not refresh URL for %s: %s", item.title, error)
                self.play_next(guild_id)
                return False

        if not stream_url:
            logger.error("No playable stream URL found for %s", item.title)
            self.play_next(guild_id)
            return False

        session = self.sessions.get(guild_id)
        if session is None or not session.voice_client.is_connected():
            logger.warning(
                "Voice client disappeared before playback in guild %s", guild_id
            )
            await self.disconnect_and_cleanup(guild_id)
            return False

        session.current = item
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
            session.voice_client.play(
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

    async def disconnect_and_cleanup(self, guild_id: int) -> None:
        if session := self.sessions.pop(guild_id, None):
            try:
                session.voice_client.stop()
                if session.voice_client.is_connected():
                    await session.voice_client.disconnect()
            except Exception as error:  # pylint: disable=broad-exception-caught
                logger.error(
                    "Error during disconnect for guild %s: %s", guild_id, error
                )

    def play_next(self, guild_id: int, error=None) -> None:
        if error:
            logger.error("Player error for guild %s: %s", guild_id, error)

        session = self.sessions.get(guild_id)
        if session is None or not session.voice_client.is_connected():
            run_coroutine_threadsafe(
                self.disconnect_and_cleanup(guild_id), self.bot.loop
            )
            return

        if session.queue:
            item = session.queue.popleft()
            run_coroutine_threadsafe(
                self.play_next_track_and_announce(guild_id, item), self.bot.loop
            )
            return

        session.current = None
        run_coroutine_threadsafe(self.disconnect_and_cleanup(guild_id), self.bot.loop)

    async def handle_voice_state_update(
        self, member: Member, before: VoiceState, after: VoiceState
    ) -> None:
        guild_id = member.guild.id
        session = self.sessions.get(guild_id)
        if session is None:
            return

        voice_client = session.voice_client
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

        if (
            before.channel == voice_client.channel
            and after.channel != voice_client.channel
        ):
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
