import logging
from asyncio import CancelledError, Lock, Task, create_task
from collections import deque
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from random import shuffle
from shlex import quote
from typing import TYPE_CHECKING
from urllib.parse import urlparse
from weakref import WeakValueDictionary

from discord import (
    AllowedMentions,
    FFmpegOpusAudio,
    Interaction,
    Member,
    StageChannel,
    VoiceChannel,
    VoiceClient,
    VoiceState,
)
from discord.abc import Messageable
from discord.utils import escape_markdown

if TYPE_CHECKING:
    from main import Sakamoto

logger = logging.getLogger(__name__)

type StreamResolver = Callable[[str], Awaitable[ResolvedStream | None]]


@dataclass(frozen=True)
class ResolvedStream:
    url: str
    codec: str | None = None
    headers: frozendict[str, str] = field(default_factory=frozendict)


@dataclass
class QueueItem:
    source_url: str
    title: str
    duration: str
    stream: ResolvedStream | None = None
    refresh_stream: StreamResolver | None = None


@dataclass
class PlaybackSession:
    """All playback state owned by one guild."""

    voice_client: VoiceClient
    queue: deque[QueueItem] = field(default_factory=deque)
    current: QueueItem | None = None
    command_channel: Messageable | None = None
    playback_id: object | None = None
    requests: int = 0

    @property
    def busy(self) -> bool:
        return (
            self.current is not None
            or self.voice_client.is_playing()
            or self.voice_client.is_paused()
        )


class AudioEngine:
    """Shared voice, queue, and playback behavior."""

    def __init__(self, bot: Sakamoto):
        self.bot = bot
        self.sessions: dict[int, PlaybackSession] = {}
        self.connection_locks: WeakValueDictionary[int, Lock] = WeakValueDictionary()
        self.tasks: set[Task] = set()

    def is_connected(self, guild_id: int) -> bool:
        session = self.sessions.get(guild_id)
        return bool(session and session.voice_client.is_connected())

    def request_error(
        self, guild_id: int, channel: VoiceChannel | StageChannel
    ) -> str | None:
        session = self.sessions.get(guild_id)
        if session is not None:
            voice = session.voice_client
            if voice.is_connected() and voice.channel and voice.channel != channel:
                return ":x: I am already playing in another voice channel."
            if len(session.queue) >= 50:
                return ":x: Queue is full (50 items)."
        return None

    def set_command_channel(
        self, guild_id: int, session: PlaybackSession, channel: Messageable
    ) -> None:
        if self.sessions.get(guild_id) is session:
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
        # Invalidate the old player's callback before stopping it.
        session.playback_id = object()
        session.voice_client.stop()
        self.play_next(guild_id, session, session.playback_id)
        return True

    def shuffle_queue(self, guild_id: int) -> bool:
        if not (session := self.sessions.get(guild_id)) or not session.queue:
            return False
        items = list(session.queue)
        shuffle(items)
        session.queue.clear()
        session.queue.extend(items)
        return True

    def remove_track(self, guild_id: int, position: int) -> QueueItem | None:
        session = self.sessions.get(guild_id)
        if session is None or not 1 <= position <= len(session.queue):
            return None
        item = session.queue[position - 1]
        del session.queue[position - 1]
        return item

    def move_track(self, guild_id: int, position: int, destination: int) -> bool:
        session = self.sessions.get(guild_id)
        if session is None or not (
            1 <= position <= len(session.queue)
            and 1 <= destination <= len(session.queue)
        ):
            return False
        item = session.queue[position - 1]
        del session.queue[position - 1]
        session.queue.insert(destination - 1, item)
        return True

    def clear_queue(self, guild_id: int) -> int:
        session = self.sessions.get(guild_id)
        if session is None:
            return 0
        count = len(session.queue)
        session.queue.clear()
        return count

    async def enqueue_or_play(
        self,
        guild_id: int,
        item: QueueItem,
        followup,
        *,
        now_playing_message: str | None = None,
        queue_message: str | None = None,
    ) -> bool:
        session = self.sessions.get(guild_id)
        if session is None or not session.voice_client.is_connected():
            await followup(
                ":x: The bot is not connected to a voice channel.", ephemeral=True
            )
            return False

        title = escape_markdown(item.title)
        if session.busy or session.queue:
            if len(session.queue) >= 50:
                await followup(":x: Queue is full (50 items).", ephemeral=True)
                return False

            if item.duration != "LIVE":
                item.stream = None
            session.queue.append(item)
            await followup(
                queue_message
                or f":ballot_box_with_check: Added to queue: **{title}** [{item.duration}]",
                allowed_mentions=AllowedMentions.none(),
            )
            return True

        if await self.play_song(guild_id, item):
            await followup(
                now_playing_message
                or f":notes: Now playing: **{title}** [{item.duration}]",
                allowed_mentions=AllowedMentions.none(),
            )
            return True
        else:
            await followup(":x: Failed to start playback.", ephemeral=True)
            return False

    async def enqueue_playlist(
        self,
        guild_id: int,
        items: list[QueueItem],
        followup,
    ) -> bool:
        session = self.sessions.get(guild_id)
        if session is None or not session.voice_client.is_connected():
            await followup(
                ":x: The bot is not connected to a voice channel.", ephemeral=True
            )
            return False

        available_slots = 50 - len(session.queue)
        if available_slots <= 0:
            await followup(":x: Queue is full (50 items).", ephemeral=True)
            return False

        added_items = items[:available_slots]
        if not added_items:
            await followup(
                ":x: The playlist contains no playable tracks.", ephemeral=True
            )
            return False

        session.queue.extend(added_items)
        if not session.busy:
            first = session.queue.popleft()
            if await self.play_song(guild_id, first):
                title = escape_markdown(first.title)
                await followup(
                    f":notes: Started playlist. Now playing: **{title}**\n"
                    f":ballot_box_with_check: Added {len(added_items) - 1} tracks to the queue.",
                    allowed_mentions=AllowedMentions.none(),
                )
                return True
            else:
                await followup(":x: Failed to start playlist playback.", ephemeral=True)
                return False
        else:
            await followup(
                f":ballot_box_with_check: Added **{len(added_items)}** tracks "
                "from the playlist to the queue."
            )
            return True

    async def get_or_connect_voice_client(
        self,
        guild_id: int,
        user_voice_channel: VoiceChannel | StageChannel,
        interaction: Interaction,
    ) -> VoiceClient | None:
        # Weak values keep locks alive while used or awaited, without retaining guilds.
        async with self.connection_locks.setdefault(guild_id, Lock()):
            if error := self.request_error(guild_id, user_voice_channel):
                await interaction.followup.send(error, ephemeral=True)
                return None
            session = self.sessions.get(guild_id)
            voice_client = session.voice_client if session else None
            if voice_client is None or not voice_client.is_connected():
                try:
                    voice_client = await user_voice_channel.connect(self_deaf=True)
                    self.sessions[guild_id] = PlaybackSession(voice_client)
                except CancelledError:
                    if registered := user_voice_channel.guild.voice_client:
                        await registered.disconnect(force=True)
                    raise
                except Exception as error:
                    await interaction.followup.send(
                        f":x: Failed to connect to the voice channel. Error: {error}",
                        ephemeral=True,
                    )
                    return None
            return voice_client

    @asynccontextmanager
    async def request_session(
        self,
        guild_id: int,
        channel: VoiceChannel | StageChannel,
        interaction: Interaction,
    ) -> AsyncIterator[PlaybackSession | None]:
        voice_client = await self.get_or_connect_voice_client(
            guild_id, channel, interaction
        )
        session = self.sessions.get(guild_id)
        if (
            voice_client is None
            or session is None
            or session.voice_client is not voice_client
        ):
            yield None
            return
        session.requests += 1
        try:
            yield session
        finally:
            session.requests -= 1
            await self._close_if_idle(guild_id, session)

    async def _close_if_idle(self, guild_id: int, session: PlaybackSession) -> None:
        async with self.connection_locks.setdefault(guild_id, Lock()):
            if self.sessions.get(guild_id) is session and (
                not session.voice_client.is_connected()
                or (not session.busy and not session.queue and not session.requests)
            ):
                await self._disconnect_session(guild_id, session)

    def _schedule(self, coroutine) -> None:
        task = create_task(coroutine)
        self.tasks.add(task)
        task.add_done_callback(self._transition_done)

    def _transition_done(self, task: Task) -> None:
        self.tasks.discard(task)
        if not task.cancelled() and (error := task.exception()) is not None:
            logger.error("Audio transition failed", exc_info=error)

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

    @staticmethod
    def _reserve(session: PlaybackSession, item: QueueItem) -> object:
        session.current = item
        session.playback_id = object()
        return session.playback_id

    def _owns_playback(
        self, guild_id: int, session: PlaybackSession, playback_id: object | None
    ) -> bool:
        return (
            self.sessions.get(guild_id) is session
            and session.playback_id is playback_id
        )

    async def play_song(self, guild_id: int, item: QueueItem) -> bool:
        session = self.sessions.get(guild_id)
        if session is None:
            return False
        return await self._play_song(
            guild_id, session, item, self._reserve(session, item)
        )

    async def _play_song(
        self,
        guild_id: int,
        session: PlaybackSession,
        item: QueueItem,
        playback_id: object,
        *,
        announce: bool = False,
    ) -> bool:
        if not self._owns_playback(guild_id, session, playback_id):
            return False
        source = None
        try:
            stream = item.stream
            if stream is None and item.refresh_stream is not None:
                stream = await item.refresh_stream(item.source_url)
            if not self._owns_playback(guild_id, session, playback_id):
                return False
            if not session.voice_client.is_connected():
                await self.disconnect_and_cleanup(guild_id)
                return False
            if stream is None:
                raise ValueError(f"No playable stream URL found for {item.title}")
            before_options = (
                "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5 "
                "-analyzeduration 10M -probesize 10M -err_detect ignore_err"
            )
            if stream.headers and urlparse(stream.url).scheme in ("http", "https"):
                headers = "".join(
                    f"{name}: {value}\r\n" for name, value in stream.headers.items()
                )
                before_options += f" -headers {quote(headers)}"
            source = FFmpegOpusAudio(
                stream.url,
                codec=stream.codec,
                before_options=before_options,
                options="-vn",
            )
            session.voice_client.play(
                source,
                after=lambda error: self.bot.loop.call_soon_threadsafe(
                    self.play_next, guild_id, session, playback_id, error
                ),
            )
        except CancelledError:
            self.play_next(guild_id, session, playback_id)
            raise
        except Exception as error:
            if source is not None:
                source.cleanup()
            self.play_next(guild_id, session, playback_id, error)
            return False

        if announce and session.command_channel:
            title = escape_markdown(item.title)
            message = (
                f":radio: Playing **{title}** on Radio Garden"
                if item.duration == "LIVE"
                else f":notes: Now playing: **{title}** [{item.duration}]"
            )
            try:
                await session.command_channel.send(
                    message, allowed_mentions=AllowedMentions.none()
                )
            except Exception as error:
                logger.warning(
                    "Failed to announce playback in guild %s: %s", guild_id, error
                )
        return True

    async def disconnect_and_cleanup(self, guild_id: int) -> None:
        session = self.sessions.get(guild_id)
        if session is not None:
            async with self.connection_locks.setdefault(guild_id, Lock()):
                await self._disconnect_session(guild_id, session)

    async def _disconnect_session(
        self, guild_id: int, session: PlaybackSession
    ) -> None:
        if self.sessions.get(guild_id) is not session:
            return
        del self.sessions[guild_id]
        session.queue.clear()
        session.current = None
        session.playback_id = None
        session.command_channel = None
        try:
            if session.voice_client.is_connected():
                await session.voice_client.disconnect()
            else:
                session.voice_client.stop()
        except Exception as error:
            logger.error("Error during disconnect for guild %s: %s", guild_id, error)

    def play_next(
        self,
        guild_id: int,
        session: PlaybackSession,
        playback_id: object | None,
        error: Exception | None = None,
    ) -> None:
        # All state changes run on the event loop and belong to this exact playback.
        if not self._owns_playback(guild_id, session, playback_id):
            return
        if error:
            logger.error("Player error for guild %s: %s", guild_id, error)
        session.current = None
        session.playback_id = None
        if session.voice_client.is_connected() and session.queue:
            item = session.queue.popleft()
            token = self._reserve(session, item)
            self._schedule(
                self._play_song(guild_id, session, item, token, announce=True)
            )
        else:
            self._schedule(self._close_if_idle(guild_id, session))

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

        if self.bot.user and member.id == self.bot.user.id:
            if after.channel and len(after.channel.members) == 1:
                await self.disconnect_and_cleanup(guild_id)
            return

        if (
            before.channel == voice_client.channel
            and after.channel != voice_client.channel
            and len(voice_client.channel.members) == 1
            and voice_client.channel.members[0] == self.bot.user
        ):
            await self.disconnect_and_cleanup(guild_id)


def get_audio_engine(bot: Sakamoto) -> AudioEngine:
    if (engine := getattr(bot, "_audio_engine", None)) is None:
        engine = AudioEngine(bot)
        bot._audio_engine = engine
        bot.add_listener(engine.handle_voice_state_update, "on_voice_state_update")
    return engine
