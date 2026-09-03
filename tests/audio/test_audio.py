import asyncio
import sys
from collections import deque
from pathlib import Path
from time import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from extensions.audio._audio_engine import (
    AudioEngine,
    PlaybackSession,
    QueueItem,
    get_audio_engine,
)
from extensions.audio.music import MusicCog, MusicControls
from extensions.audio.radio import RadioCog, RadioStation


def _radio_command(name: str):
    return getattr(RadioCog, name)


def test_radio_subcommand_option_contracts():
    search = _radio_command("search")
    balloon = _radio_command("balloon")

    assert [
        (param.name, param.required, bool(param.autocomplete))
        for param in search.parameters
    ] == [
        ("query", True, True),
    ]
    assert balloon.parameters == []


def test_play_option_contracts():
    assert [
        (param.name, param.required, bool(param.autocomplete))
        for param in MusicCog.play.parameters
    ] == [
        ("query", True, True),
    ]


@pytest.mark.asyncio
async def test_music_commands_require_guild():
    interaction = _make_interaction(user=object(), guild_id=None)

    assert await MusicCog(_make_bot()).interaction_check(interaction) is False
    interaction.response.send_message.assert_awaited_once_with(
        ":x: Could not determine guild ID.", ephemeral=True
    )


class DummyMember:
    def __init__(self, user_id: int, *, voice_channel=None):
        self.id = user_id
        self.voice = (
            None if voice_channel is None else SimpleNamespace(channel=voice_channel)
        )


class DummyVoiceChannel:
    def __init__(self, connected_client=None):
        self.connect = AsyncMock(return_value=connected_client)


class DummyVoiceClient:
    def __init__(self, *, connected=True, playing=False, paused=False, channel=None):
        self._connected = connected
        self._playing = playing
        self._paused = paused
        self.channel = channel
        self.play = MagicMock()
        self.pause = MagicMock()
        self.resume = MagicMock()
        self.stop = MagicMock()
        self.disconnect = AsyncMock()

    def is_connected(self):
        return self._connected

    def is_playing(self):
        return self._playing

    def is_paused(self):
        return self._paused


class DummyLoop:
    def __init__(self, result):
        self.result = result

    async def run_in_executor(self, _executor, _fn, _arg):
        return self.result


class ImmediateLoop:
    async def run_in_executor(self, _executor, function, argument):
        return function(argument)


class DummyResponse:
    def __init__(self, payload, status=200, headers=None):
        self.payload = payload
        self.status = status
        self.headers = headers or {}

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def json(self, content_type=None):
        return self.payload


class DummySession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls: list[tuple[str, dict | None]] = []

    def get(self, url, params=None, timeout=10, **kwargs):
        self.calls.append((url, params))
        payload = self.responses.pop(0)
        if isinstance(payload, DummyResponse):
            return payload
        return DummyResponse(payload)


def _make_bot(*, session=None):
    return SimpleNamespace(
        loop=object(), color=0x123456, session=session, add_listener=MagicMock()
    )


def _make_interaction(*, user, guild_id=1):
    message = SimpleNamespace(edit=AsyncMock())
    return SimpleNamespace(
        guild_id=guild_id,
        user=user,
        channel=SimpleNamespace(send=AsyncMock()),
        response=SimpleNamespace(defer=AsyncMock(), send_message=AsyncMock()),
        followup=SimpleNamespace(send=AsyncMock()),
        message=message,
        original_response=AsyncMock(return_value=message),
    )


async def _wait_for_event(event: asyncio.Event) -> None:
    await asyncio.wait_for(event.wait(), timeout=1)


def _add_session(
    engine,
    voice_client,
    *,
    guild_id=1,
    queue=(),
    current=None,
    command_channel=None,
):
    session = PlaybackSession(
        voice_client,
        deque(queue),
        current,
        command_channel,
    )
    engine.sessions[guild_id] = session
    return session


def test_audio_engine_registers_voice_listener_once():
    bot = _make_bot()

    engine = get_audio_engine(bot)

    assert get_audio_engine(bot) is engine
    bot.add_listener.assert_called_once_with(
        engine.handle_voice_state_update, "on_voice_state_update"
    )


def test_audio_engine_sessions_are_isolated_by_guild():
    engine = AudioEngine(_make_bot())
    first = _add_session(
        engine,
        DummyVoiceClient(),
        queue=[QueueItem("one", "First", "1:00")],
    )
    second = _add_session(
        engine,
        DummyVoiceClient(),
        guild_id=2,
        queue=[QueueItem("two", "Second", "2:00")],
    )

    assert engine.queue_snapshot(1) == (None, tuple(first.queue))
    assert engine.queue_snapshot(2) == (None, tuple(second.queue))


def test_queue_operations_validate_skip_and_shuffle(monkeypatch):
    engine = AudioEngine(_make_bot())
    voice_client = DummyVoiceClient(playing=True)
    _add_session(
        engine,
        voice_client,
        queue=[QueueItem("one", "First", "1:00"), QueueItem("two", "Second", "2:00")],
    )
    monkeypatch.setattr(
        "extensions.audio._audio_engine.shuffle", lambda items: items.reverse()
    )

    assert engine.skip_tracks(1, 0) is False
    assert engine.shuffle_queue(1) is True
    assert [item.title for item in engine.queue_snapshot(1)[1]] == ["Second", "First"]
    assert engine.skip_tracks(1, 2) is True
    assert engine.queued_count(1) == 1
    voice_client.stop.assert_called_once()


@pytest.mark.asyncio
async def test_music_unload_preserves_shared_radio_session():
    bot = _make_bot()
    music = MusicCog(bot)
    radio = RadioCog(bot)
    _add_session(music.engine, DummyVoiceClient())

    await music.cog_unload()

    assert radio.engine is music.engine
    assert radio.engine.is_connected(1)


def test_skip_amount_has_minimum_of_one():
    amount = MusicCog.skip.parameters[0]

    assert amount.name == "amount"
    assert amount.min_value == 1


@pytest.mark.asyncio
async def test_play_song_returns_false_when_refreshed_stream_url_missing(monkeypatch):
    cog = AudioEngine(_make_bot())
    _add_session(cog, DummyVoiceClient(connected=True))
    cog.play_next = MagicMock()
    refresh_stream = AsyncMock(return_value=None)

    started = await cog.play_song(
        1,
        QueueItem("https://example.test/watch", "Track", "3:00", None, refresh_stream),
    )

    assert started is False
    cog.play_next.assert_called_once_with(1)


@pytest.mark.asyncio
async def test_play_song_starts_audio_engine_and_tracks_current_song(monkeypatch):
    vc = DummyVoiceClient(connected=True)
    cog = AudioEngine(_make_bot())
    _add_session(cog, vc)
    monkeypatch.setattr(
        "extensions.audio._audio_engine.FFmpegOpusAudio",
        lambda stream_url, **_kw: f"audio:{stream_url}",
    )

    item = QueueItem(
        "https://example.test/watch", "Track", "3:00", "https://stream.test"
    )
    started = await cog.play_song(1, item)

    assert started is True
    assert cog.queue_snapshot(1)[0] is item
    assert vc.play.call_args.args[0] == "audio:https://stream.test"


@pytest.mark.asyncio
async def test_play_song_cleans_source_when_audio_engine_is_rejected(monkeypatch):
    source = SimpleNamespace(cleanup=MagicMock())
    vc = DummyVoiceClient(connected=True)
    vc.play.side_effect = RuntimeError("rejected")
    cog = AudioEngine(_make_bot())
    _add_session(cog, vc)
    cog.play_next = MagicMock()
    monkeypatch.setattr(
        "extensions.audio._audio_engine.FFmpegOpusAudio",
        lambda *_args, **_kwargs: source,
    )

    started = await cog.play_song(1, QueueItem("source", "Track", "3:00", "stream"))

    assert started is False
    source.cleanup.assert_called_once()


@pytest.mark.asyncio
async def test_play_song_cleans_state_when_voice_client_disappears():
    cog = AudioEngine(_make_bot())
    _add_session(
        cog,
        DummyVoiceClient(connected=False),
        queue=[QueueItem("url", "Queued", "3:00")],
        current=QueueItem("url", "Playing", "3:00"),
        command_channel=object(),
    )

    assert await cog.play_song(1, QueueItem("url", "Track", "3:00", "stream")) is False
    assert cog.sessions == {}


@pytest.mark.asyncio
async def test_ensure_user_in_same_voice_channel_rejects_other_channel(monkeypatch):
    cog = AudioEngine(_make_bot())
    bot_channel = object()
    other_channel = object()
    _add_session(cog, DummyVoiceClient(connected=True, channel=bot_channel))
    monkeypatch.setattr("extensions.audio._audio_engine.Member", DummyMember)
    interaction = _make_interaction(
        user=DummyMember(42, voice_channel=other_channel), guild_id=1
    )

    vc = await cog.ensure_user_in_same_voice_channel(interaction, 1)

    assert vc is None
    interaction.response.send_message.assert_awaited_with(
        ":x: You must be in the same voice channel as the bot to use this command.",
        ephemeral=True,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "command",
    [MusicCog.stop, MusicCog.skip],
)
async def test_control_commands_return_early_when_same_channel_check_fails(command):
    interaction = _make_interaction(user=object(), guild_id=1)
    cog = MusicCog(_make_bot())
    cog.engine.ensure_user_in_same_voice_channel = AsyncMock(return_value=None)
    cog.engine.disconnect_and_cleanup = AsyncMock()

    await command.callback(cog, interaction)

    cog.engine.ensure_user_in_same_voice_channel.assert_awaited_once_with(
        interaction, 1
    )
    interaction.response.send_message.assert_not_awaited()
    cog.engine.disconnect_and_cleanup.assert_not_awaited()


def test_play_next_returns_without_voice_client(monkeypatch):
    cog = AudioEngine(_make_bot())
    monkeypatch.setattr(
        "extensions.audio._audio_engine.run_coroutine_threadsafe",
        lambda coro, _loop: coro.close(),
    )
    cog.play_next(123)
    assert cog.sessions == {}


@pytest.mark.asyncio
async def test_playlist_prefers_webpage_url_over_flat_url():
    music = MusicCog(_make_bot())
    items = music.playlist_items(
        [
            {
                "id": "abc",
                "url": "abc",
                "webpage_url": "https://www.youtube.com/watch?v=abc",
            },
            {
                "id": "station",
                "url": "https://example.test/live",
                "extractor_key": "Generic",
            },
        ]
    )

    assert items[0].source_url == "https://www.youtube.com/watch?v=abc"
    assert items[1].source_url == "https://example.test/live"


@pytest.mark.asyncio
async def test_playlist_rejects_entries_without_urls():
    cog = AudioEngine(_make_bot())
    _add_session(cog, DummyVoiceClient(connected=True))
    followup = AsyncMock()

    items = MusicCog(_make_bot()).playlist_items([{"title": "Unavailable"}])
    await cog.enqueue_playlist(1, items, followup)

    assert cog.queue_snapshot(1)[1] == ()
    followup.assert_awaited_once_with(
        ":x: The playlist contains no playable tracks.", ephemeral=True
    )


@pytest.mark.parametrize(
    "value, expected",
    [
        ("https://radio.garden/listen/mataroradio/sFtKSe5I", "sFtKSe5I"),
        ("http://radio.garden/listen/esplugues-fm/cKnK9OEm", "cKnK9OEm"),
        ("sFtKSe5I", "sFtKSe5I"),
        ("flaixbac", "flaixbac"),
        ("https://other.site/listen/foo/sFtKSe5I", None),
    ],
)
def test_extract_channel_id(value, expected):
    assert RadioCog.extract_channel_id(value) == expected


@pytest.mark.parametrize(
    "href, expected",
    [
        ("/listen/kutx-98-9/vbFsCngB", "vbFsCngB"),
        ("/listen/foo/sFtKSe5I", "sFtKSe5I"),
        ("/something/else/", None),
    ],
)
def test_channel_id_from_href(href, expected):
    assert RadioCog.channel_id_from_href(href) == expected


@pytest.mark.asyncio
async def test_resolve_radio_station_with_channel_id():
    session = DummySession(
        [{"data": {"title": "Mataro Radio", "url": "/listen/mataroradio/sFtKSe5I"}}]
    )
    cog = RadioCog(_make_bot(session=session))

    station = await cog.resolve_radio_station("sFtKSe5I")

    assert station == RadioStation("sFtKSe5I", "Mataro Radio")


@pytest.mark.asyncio
async def test_resolve_radio_station_falls_back_to_search():
    session = DummySession(
        [
            {"data": None},
            {
                "hits": {
                    "hits": [
                        {
                            "_source": {
                                "type": "place",
                                "title": "Barcelona",
                                "url": "/map/barcelona",
                            }
                        },
                        {
                            "_source": {
                                "type": "channel",
                                "page": {
                                    "type": "channel",
                                    "title": "Flaixbac",
                                    "subtitle": "Madrid, Spain",
                                    "url": "/listen/flaixbac/aaaa1111",
                                    "place": {"title": "Madrid"},
                                    "country": {"title": "Spain"},
                                },
                            }
                        },
                        {
                            "_source": {
                                "type": "channel",
                                "page": {
                                    "type": "channel",
                                    "title": "Flaixbac",
                                    "subtitle": "Barcelona, Spain",
                                    "url": "/listen/flaixbac/sFtKSe5I",
                                    "place": {"title": "Barcelona"},
                                    "country": {"title": "Spain"},
                                },
                            }
                        },
                    ]
                }
            },
        ]
    )
    cog = RadioCog(_make_bot(session=session))

    station = await cog.resolve_radio_station("FlaixBac")

    assert station == RadioStation("aaaa1111", "Flaixbac")
    assert session.calls[1][1] == {"q": "FlaixBac"}


@pytest.mark.asyncio
async def test_resolve_radio_station_with_none_query_uses_random_station():
    cog = RadioCog(_make_bot(session=DummySession([])))
    expected = RadioStation("spain123", "Radio Marca")
    cog.pick_random_station = AsyncMock(return_value=expected)

    station = await cog.resolve_radio_station(None)

    assert station == expected
    cog.pick_random_station.assert_awaited_once()


@pytest.mark.asyncio
async def test_resolve_radio_station_raises_when_search_empty():
    session = DummySession([{"hits": {"hits": []}}])
    cog = RadioCog(_make_bot(session=session))

    with pytest.raises(ValueError):
        await cog.resolve_radio_station("does-not-exist")


@pytest.mark.asyncio
async def test_resolve_radio_station_raises_when_search_has_no_channel_hits():
    session = DummySession(
        [
            {
                "hits": {
                    "hits": [
                        {
                            "_source": {
                                "type": "place",
                                "title": "Barcelona",
                                "url": "/map/barcelona",
                            }
                        },
                    ]
                }
            }
        ]
    )
    cog = RadioCog(_make_bot(session=session))

    with pytest.raises(ValueError):
        await cog.resolve_radio_station("FlaixBac")


@pytest.mark.asyncio
async def test_pick_random_station_returns_channel_from_random_place():
    session = DummySession(
        [
            {"data": {"list": [{"id": "place1"}]}},
            {
                "data": {
                    "content": [
                        {
                            "items": [
                                {
                                    "page": {
                                        "type": "channel",
                                        "title": "Mataro Radio",
                                        "url": "/listen/mataroradio/sFtKSe5I",
                                    }
                                }
                            ]
                        }
                    ]
                }
            },
        ]
    )
    cog = RadioCog(_make_bot(session=session))

    station = await cog.pick_random_station()

    assert station == RadioStation("sFtKSe5I", "Mataro Radio")


@pytest.mark.asyncio
async def test_resolve_radio_stream_url_prefers_redirect_location():
    session = DummySession(
        [
            DummyResponse(
                {}, status=302, headers={"Location": "https://stream.test/live"}
            )
        ]
    )
    cog = RadioCog(_make_bot(session=session))

    stream_url = await cog.resolve_radio_stream_url("sFtKSe5I")

    assert stream_url == "https://stream.test/live"


@pytest.mark.asyncio
async def test_resolve_radio_stream_url_returns_api_url_on_success_status():
    session = DummySession([DummyResponse({}, status=200)])
    cog = RadioCog(_make_bot(session=session))

    stream_url = await cog.resolve_radio_stream_url("sFtKSe5I")

    assert (
        stream_url == "https://radio.garden/api/ara/content/listen/sFtKSe5I/channel.mp3"
    )


@pytest.mark.asyncio
async def test_resolve_radio_stream_url_raises_when_unplayable_status():
    session = DummySession([DummyResponse({}, status=403)])
    cog = RadioCog(_make_bot(session=session))

    with pytest.raises(ValueError):
        await cog.resolve_radio_stream_url("sFtKSe5I")


@pytest.mark.asyncio
async def test_radio_does_not_join_voice_when_station_resolution_fails(monkeypatch):
    connected_client = DummyVoiceClient(connected=True)
    voice_channel = DummyVoiceChannel(connected_client=connected_client)
    interaction = _make_interaction(
        user=DummyMember(42, voice_channel=voice_channel), guild_id=1
    )
    radio = RadioCog(_make_bot())
    radio.resolve_radio_station = AsyncMock(
        side_effect=ValueError("No radio station found for that query.")
    )
    radio.resolve_radio_stream_url = AsyncMock()
    radio.engine.get_or_connect_voice_client = AsyncMock()
    monkeypatch.setattr("extensions.audio.radio.Member", DummyMember)

    await _radio_command("search").callback(radio, interaction, query="missing")

    radio.resolve_radio_station.assert_awaited_once_with("missing")
    radio.resolve_radio_stream_url.assert_not_awaited()
    radio.engine.get_or_connect_voice_client.assert_not_awaited()
    voice_channel.connect.assert_not_awaited()
    interaction.followup.send.assert_awaited_once_with(
        ":x: No radio station found for that query.",
        ephemeral=True,
    )


@pytest.mark.asyncio
async def test_radio_does_not_join_voice_when_stream_resolution_fails(monkeypatch):
    connected_client = DummyVoiceClient(connected=True)
    voice_channel = DummyVoiceChannel(connected_client=connected_client)
    interaction = _make_interaction(
        user=DummyMember(42, voice_channel=voice_channel), guild_id=1
    )
    radio = RadioCog(_make_bot())
    radio.resolve_radio_station = AsyncMock(
        return_value=RadioStation("sFtKSe5I", "Flaixbac")
    )
    radio.resolve_radio_stream_url = AsyncMock(
        side_effect=ValueError("Could not resolve a playable radio stream.")
    )
    radio.engine.get_or_connect_voice_client = AsyncMock()
    monkeypatch.setattr("extensions.audio.radio.Member", DummyMember)

    await _radio_command("search").callback(radio, interaction, query="flaixbac")

    radio.resolve_radio_station.assert_awaited_once_with("flaixbac")
    radio.resolve_radio_stream_url.assert_awaited_once_with("sFtKSe5I")
    radio.engine.get_or_connect_voice_client.assert_not_awaited()
    voice_channel.connect.assert_not_awaited()
    interaction.followup.send.assert_awaited_once_with(
        ":x: Could not resolve a playable radio stream.",
        ephemeral=True,
    )


@pytest.mark.asyncio
async def test_radio_balloon_uses_random_station_path(monkeypatch):
    connected_client = DummyVoiceClient(connected=True)
    voice_channel = DummyVoiceChannel(connected_client=connected_client)
    interaction = _make_interaction(
        user=DummyMember(42, voice_channel=voice_channel), guild_id=1
    )
    radio = RadioCog(_make_bot())
    radio.resolve_radio_station = AsyncMock(
        return_value=RadioStation("sFtKSe5I", "Flaixbac")
    )
    radio.resolve_radio_stream_url = AsyncMock(return_value="https://stream.test/live")
    radio.engine.enqueue_or_play = AsyncMock()
    monkeypatch.setattr("extensions.audio.radio.Member", DummyMember)

    await _radio_command("balloon").callback(radio, interaction)

    radio.resolve_radio_station.assert_awaited_once_with(None)
    radio.resolve_radio_stream_url.assert_awaited_once_with("sFtKSe5I")
    radio.engine.enqueue_or_play.assert_awaited_once()


@pytest.mark.asyncio
async def test_play_connects_before_enqueue(monkeypatch):
    events = []
    connected_client = DummyVoiceClient(connected=True)
    voice_channel = DummyVoiceChannel(connected_client=connected_client)
    voice_channel.connect.side_effect = lambda **_kwargs: (
        events.append("connect") or connected_client
    )
    interaction = _make_interaction(
        user=DummyMember(42, voice_channel=voice_channel), guild_id=1
    )
    cog = MusicCog(_make_bot())
    cog.engine.enqueue_or_play = AsyncMock()
    monkeypatch.setattr("extensions.audio.music.Member", DummyMember)

    async def lookup(_executor, _fn, _arg):
        events.append("lookup")
        return {
            "url": "https://stream.test/live",
            "webpage_url": "https://youtube.test/watch?v=abc",
            "title": "Track",
            "duration_string": "3:00",
        }

    monkeypatch.setattr(
        "extensions.audio.music.get_running_loop",
        lambda: SimpleNamespace(run_in_executor=lookup),
    )

    await MusicCog.play.callback(cog, interaction, query="track")

    assert events == ["connect", "lookup"]
    voice_channel.connect.assert_awaited_once()
    assert cog.engine.is_connected(1)
    cog.engine.enqueue_or_play.assert_awaited_once()


@pytest.mark.asyncio
async def test_play_resolves_source_while_connecting_to_voice(monkeypatch):
    connected_client = DummyVoiceClient(connected=True)
    voice_channel = DummyVoiceChannel(connected_client=connected_client)
    interaction = _make_interaction(
        user=DummyMember(42, voice_channel=voice_channel), guild_id=1
    )
    cog = MusicCog(_make_bot())
    cog.engine.enqueue_or_play = AsyncMock()
    connection_started = asyncio.Event()
    lookup_started = asyncio.Event()
    monkeypatch.setattr("extensions.audio.music.Member", DummyMember)

    async def connect(**_kwargs):
        connection_started.set()
        await _wait_for_event(lookup_started)
        return connected_client

    async def lookup(_executor, _fn, _arg):
        await _wait_for_event(connection_started)
        lookup_started.set()
        return {
            "url": "https://stream.test/live",
            "webpage_url": "https://youtube.test/watch?v=abc",
            "title": "Track",
            "duration_string": "3:00",
        }

    voice_channel.connect.side_effect = connect
    monkeypatch.setattr(
        "extensions.audio.music.get_running_loop",
        lambda: SimpleNamespace(run_in_executor=lookup),
    )

    await asyncio.wait_for(
        MusicCog.play.callback(cog, interaction, query="track"), timeout=1
    )

    cog.engine.enqueue_or_play.assert_awaited_once()


@pytest.mark.asyncio
async def test_play_reports_voice_connection_exception(monkeypatch):
    voice_channel = DummyVoiceChannel()
    interaction = _make_interaction(
        user=DummyMember(42, voice_channel=voice_channel), guild_id=1
    )
    cog = MusicCog(_make_bot())
    cog.engine.get_or_connect_voice_client = AsyncMock(
        side_effect=RuntimeError("connection failed")
    )
    cog.resolve_source = AsyncMock(return_value={"url": "https://stream.test/live"})
    cog.engine.enqueue_or_play = AsyncMock()
    monkeypatch.setattr("extensions.audio.music.Member", DummyMember)

    await MusicCog.play.callback(cog, interaction, query="track")

    cog.engine.enqueue_or_play.assert_not_awaited()
    interaction.followup.send.assert_awaited_once_with(
        ":x: Failed to retrieve audio. Error: connection failed", ephemeral=True
    )


@pytest.mark.asyncio
async def test_play_cleans_new_connection_when_source_lookup_fails(monkeypatch):
    connected_client = DummyVoiceClient(connected=True)
    voice_channel = DummyVoiceChannel(connected_client=connected_client)
    interaction = _make_interaction(
        user=DummyMember(42, voice_channel=voice_channel), guild_id=1
    )
    cog = MusicCog(_make_bot())
    monkeypatch.setattr("extensions.audio.music.Member", DummyMember)
    monkeypatch.setattr(
        "extensions.audio.music.get_running_loop", lambda: DummyLoop(None)
    )

    await MusicCog.play.callback(cog, interaction, query="missing")

    voice_channel.connect.assert_awaited_once()
    connected_client.stop.assert_called_once()
    connected_client.disconnect.assert_awaited_once()
    assert cog.engine.sessions == {}


@pytest.mark.asyncio
async def test_play_keeps_existing_connection_when_source_lookup_fails(monkeypatch):
    connected_client = DummyVoiceClient(connected=True, playing=True)
    voice_channel = DummyVoiceChannel()
    interaction = _make_interaction(
        user=DummyMember(42, voice_channel=voice_channel), guild_id=1
    )
    cog = MusicCog(_make_bot())
    _add_session(cog.engine, connected_client)
    monkeypatch.setattr("extensions.audio.music.Member", DummyMember)
    monkeypatch.setattr(
        "extensions.audio.music.get_running_loop", lambda: DummyLoop(None)
    )

    await MusicCog.play.callback(cog, interaction, query="missing")

    voice_channel.connect.assert_not_awaited()
    connected_client.stop.assert_not_called()
    connected_client.disconnect.assert_not_awaited()
    assert cog.engine.is_connected(1)


@pytest.mark.asyncio
async def test_play_query_autocomplete_fetches_from_google(monkeypatch):
    bot = _make_bot()
    session = DummySession(
        [
            DummyResponse(
                [
                    "query",
                    ["track 1", "track 2", "track 3", "track 4", "track 5", "track 6"],
                ]
            )
        ]
    )
    bot.session = session
    cog = MusicCog(bot)

    choices = await cog.play_query_autocomplete(SimpleNamespace(), "track")

    assert len(choices) == 5
    assert choices[0].name == "track 1"
    assert choices[0].value == "track 1"


def test_search_source_resolves_one_full_search_result(monkeypatch):
    ydl = MagicMock()
    ydl.__enter__.return_value.extract_info.side_effect = [
        {"entries": [{"url": "https://stream.test/live"}]},
        {"entries": []},
    ]
    youtube_dl = MagicMock(return_value=ydl)
    monkeypatch.setattr("extensions.audio.music.YoutubeDL", youtube_dl)
    cog = MusicCog(_make_bot())

    assert cog.search_source("track") == {
        "entries": [{"url": "https://stream.test/live"}]
    }
    options = youtube_dl.call_args.args[0]
    assert options["extract_flat"] is False
    assert options["noplaylist"] is True
    assert "js_runtimes" not in options
    assert "extractor_args" not in options
    ydl.__enter__.return_value.extract_info.assert_any_call(
        "ytsearch1:track", download=False
    )
    with pytest.raises(ValueError, match="No results found"):
        cog.search_source("missing")


@pytest.mark.asyncio
async def test_resolve_source_reuses_cached_query_and_stream_url(monkeypatch):
    cog = MusicCog(_make_bot())
    cog.source_cache["expired"] = (time() - 1, {"unused": True})
    source_url = "https://www.youtube.com/watch?v=abc"
    stream_url = f"https://stream.test/audio?expire={int(time()) + 3600}"
    info = {
        "entries": [
            {
                "extractor_key": "Youtube",
                "id": "abc",
                "webpage_url": source_url,
                "url": stream_url,
            }
        ]
    }
    cog.search_source = MagicMock(return_value=info)
    monkeypatch.setattr("extensions.audio.music.get_running_loop", ImmediateLoop)

    assert await cog.resolve_source("Track") is info
    assert await cog.resolve_source("track") is info
    assert await cog.refresh_stream_url(source_url) == stream_url
    assert "expired" not in cog.source_cache
    cog.search_source.assert_called_once_with("Track")


@pytest.mark.asyncio
async def test_resolve_source_reuses_in_flight_query(monkeypatch):
    cog = MusicCog(_make_bot())
    started = asyncio.Event()
    release = asyncio.Event()
    info = {"url": f"https://stream.test/audio?expire={int(time()) + 3600}"}

    class LookupLoop:
        calls = 0

        def run_in_executor(self, _executor, _function, _argument):
            self.calls += 1

            async def lookup():
                started.set()
                await _wait_for_event(release)
                return info

            return asyncio.create_task(lookup())

    loop = LookupLoop()
    monkeypatch.setattr("extensions.audio.music.get_running_loop", lambda: loop)

    first = asyncio.create_task(cog.resolve_source("Track"))
    await _wait_for_event(started)
    second = asyncio.create_task(cog.resolve_source(" track "))
    await asyncio.sleep(0)
    release.set()

    assert await asyncio.gather(first, second) == [info, info]
    assert loop.calls == 1
    assert cog.source_lookups == {}


@pytest.mark.asyncio
async def test_resolve_source_clears_failed_in_flight_lookup(monkeypatch):
    cog = MusicCog(_make_bot())
    cog.search_source = MagicMock(side_effect=RuntimeError("lookup failed"))
    monkeypatch.setattr("extensions.audio.music.get_running_loop", ImmediateLoop)

    with pytest.raises(RuntimeError, match="lookup failed"):
        await cog.resolve_source("Track")

    assert cog.source_lookups == {}


@pytest.mark.asyncio
async def test_source_cache_stops_at_256_keys(monkeypatch):
    cog = MusicCog(_make_bot())
    expires_at = int(time()) + 3600
    cog.search_source = MagicMock(
        side_effect=lambda query: {
            "url": f"https://stream.test/{query}?expire={expires_at}"
        }
    )
    monkeypatch.setattr("extensions.audio.music.get_running_loop", ImmediateLoop)

    for index in range(130):
        await cog.resolve_source(f"Track {index}")

    assert len(cog.source_cache) == 256
    assert "track 0" not in cog.source_cache
    assert "track 129" in cog.source_cache


@pytest.mark.asyncio
async def test_resolve_source_refreshes_an_expired_stream_url(monkeypatch):
    cog = MusicCog(_make_bot())
    expired = {"url": f"https://stream.test/audio?expire={int(time()) - 1}"}
    fresh = {"url": f"https://stream.test/audio?expire={int(time()) + 3600}"}
    cog.search_source = MagicMock(side_effect=[expired, fresh])
    monkeypatch.setattr("extensions.audio.music.get_running_loop", ImmediateLoop)

    assert await cog.resolve_source("https://example.test/track") is expired
    assert await cog.resolve_source("https://example.test/track") is fresh
    assert cog.search_source.call_count == 2


@pytest.mark.asyncio
async def test_search_query_autocomplete_returns_channel_choices():
    session = DummySession(
        [
            {
                "hits": {
                    "hits": [
                        {
                            "_source": {
                                "type": "place",
                                "title": "Barcelona",
                                "url": "/map/barcelona",
                            }
                        },
                        {
                            "_source": {
                                "type": "channel",
                                "page": {
                                    "type": "channel",
                                    "title": "Flaixbac",
                                    "subtitle": "Barcelona, Spain",
                                    "url": "/listen/flaixbac/sFtKSe5I",
                                },
                            }
                        },
                    ]
                }
            }
        ]
    )
    cog = RadioCog(_make_bot(session=session))

    choices = await cog.search_query_autocomplete(SimpleNamespace(), "flaix")

    assert [(choice.name, choice.value) for choice in choices] == [
        ("Flaixbac (Barcelona, Spain)", "sFtKSe5I")
    ]


@pytest.mark.asyncio
async def test_search_query_autocomplete_limits_to_five_choices():
    session = DummySession(
        [
            {
                "hits": {
                    "hits": [
                        {
                            "_source": {
                                "type": "channel",
                                "page": {
                                    "type": "channel",
                                    "title": f"Station {i}",
                                    "subtitle": "Spain",
                                    "url": f"/listen/station-{i}/id{i}",
                                },
                            }
                        }
                        for i in range(6)
                    ]
                }
            }
        ]
    )
    cog = RadioCog(_make_bot(session=session))

    choices = await cog.search_query_autocomplete(SimpleNamespace(), "station")

    assert len(choices) == 5


@pytest.mark.asyncio
async def test_pick_random_station_raises_when_no_places():
    session = DummySession([{"data": {"list": []}}])
    cog = RadioCog(_make_bot(session=session))

    with pytest.raises(ValueError):
        await cog.pick_random_station()


@pytest.mark.asyncio
async def test_enqueue_or_play_queues_when_playing():
    vc = DummyVoiceClient(connected=True, playing=True)
    cog = AudioEngine(_make_bot())
    _add_session(cog, vc)
    followup = AsyncMock()

    await cog.enqueue_or_play(
        1,
        QueueItem(
            "https://radio.garden/listen/mataroradio/sFtKSe5I",
            "Mataro Radio",
            "LIVE",
            "https://radio.garden/api/ara/content/listen/sFtKSe5I/channel.mp3",
        ),
        followup=followup,
    )

    item = cog.queue_snapshot(1)[1][0]
    assert item.title == "Mataro Radio"
    assert (
        item.stream_url
        == "https://radio.garden/api/ara/content/listen/sFtKSe5I/channel.mp3"
    )
    followup.assert_awaited_once()


@pytest.mark.asyncio
async def test_queued_track_refreshes_stream_url_before_audio_engine(monkeypatch):
    vc = DummyVoiceClient(connected=True, playing=True)
    cog = AudioEngine(_make_bot())
    session = _add_session(cog, vc)
    followup = AsyncMock()
    refresh_stream = AsyncMock(return_value="https://stream.test/fresh")
    monkeypatch.setattr(
        "extensions.audio._audio_engine.FFmpegOpusAudio",
        lambda stream_url, **_kw: f"audio:{stream_url}",
    )

    await cog.enqueue_or_play(
        1,
        QueueItem(
            "https://youtube.test/watch?v=abc",
            "Track",
            "3:00",
            "https://stream.test/stale",
            refresh_stream,
        ),
        followup=followup,
    )

    item = session.queue.popleft()
    assert item.source_url == "https://youtube.test/watch?v=abc"
    assert item.stream_url is None

    await cog.play_next_track_and_announce(1, item)

    refresh_stream.assert_awaited_once_with("https://youtube.test/watch?v=abc")
    assert vc.play.call_args.args[0] == "audio:https://stream.test/fresh"


@pytest.mark.asyncio
async def test_enqueue_or_play_rejects_when_queue_is_full():
    vc = DummyVoiceClient(connected=True, playing=True)
    cog = AudioEngine(_make_bot())
    _add_session(cog, vc, queue=[QueueItem("u", "t", "d")] * 50)
    followup = AsyncMock()

    await cog.enqueue_or_play(
        1,
        QueueItem(
            "https://youtube.test/watch?v=abc",
            "Track",
            "3:00",
            "https://stream.test/live",
        ),
        followup=followup,
    )

    followup.assert_awaited_once_with(
        ":x: Queue is full (50 items).",
        ephemeral=True,
    )
    assert cog.queued_count(1) == 50


@pytest.mark.asyncio
async def test_queue_displays_playback_state():
    interaction = _make_interaction(user=object(), guild_id=1)
    cog = MusicCog(_make_bot())
    current = QueueItem("https://example.test/current", "Current *Track*", "2:00")
    _add_session(
        cog.engine,
        DummyVoiceClient(),
        current=current,
        queue=[
            QueueItem("url", f"Queued Track {number}", "3:00")
            for number in range(1, 12)
        ],
    )

    await MusicCog.queue.callback(cog, interaction)

    embed = interaction.response.send_message.await_args.kwargs["embed"]
    view = interaction.response.send_message.await_args.kwargs["view"]
    assert embed.title == "🎵 Music Queue"
    assert embed.description.startswith(
        "**Now Playing**\n"
        "[Current \\*Track*](https://example.test/current) [`2:00`]\n\n"
        "**Up Next**"
    )
    assert "`10.` Queued Track 10 [`3:00`]" in embed.description
    assert "Queued Track 11" not in embed.description
    assert embed.fields == []
    assert embed.footer.text == "11 tracks queued • 1 not shown"
    assert isinstance(view, MusicControls)
    assert [str(item.emoji) for item in view.children] == ["⏯️", "⏹️", "⏭️", "🔀"]


@pytest.mark.asyncio
async def test_queue_controls_control_playback():
    interaction = _make_interaction(user=object(), guild_id=1)
    cog = MusicCog(_make_bot())
    voice_client = DummyVoiceClient(playing=True)
    _add_session(cog.engine, voice_client)
    cog.engine.ensure_user_in_same_voice_channel = AsyncMock(return_value=voice_client)
    view = MusicControls(cog, 1)

    await view.children[0].callback(interaction)
    voice_client._playing = False
    voice_client._paused = True
    await view.children[0].callback(interaction)
    voice_client._playing = True
    voice_client._paused = False
    await view.children[2].callback(interaction)
    cog.engine.shuffle_queue = MagicMock(return_value=True)
    await view.children[3].callback(interaction)

    voice_client.pause.assert_called_once()
    voice_client.resume.assert_called_once()
    voice_client.stop.assert_called_once()
    cog.engine.shuffle_queue.assert_called_once_with(1)

    cog.engine.disconnect_and_cleanup = AsyncMock(
        side_effect=lambda guild_id: cog.engine.sessions.pop(guild_id, None)
    )
    await view.children[1].callback(interaction)

    cog.engine.disconnect_and_cleanup.assert_awaited_once_with(1)
    assert all(item.disabled for item in view.children)


@pytest.mark.asyncio
async def test_empty_queue_display_does_not_create_guild_state():
    interaction = _make_interaction(user=object(), guild_id=1)
    cog = MusicCog(_make_bot())

    await MusicCog.queue.callback(cog, interaction)

    assert 1 not in cog.engine.sessions


@pytest.mark.asyncio
async def test_bot_disconnects_when_moved_to_empty_voice_channel():
    bot = _make_bot()
    bot.user = SimpleNamespace(id=99)
    member = SimpleNamespace(id=99, bot=True, guild=SimpleNamespace(id=1))
    new_channel = SimpleNamespace(members=[member])
    cog = AudioEngine(bot)
    _add_session(cog, DummyVoiceClient(connected=True, channel=new_channel))
    cog.disconnect_and_cleanup = AsyncMock()

    await cog.handle_voice_state_update(
        member,
        SimpleNamespace(channel=object()),
        SimpleNamespace(channel=new_channel),
    )

    cog.disconnect_and_cleanup.assert_awaited_once_with(1)


@pytest.mark.asyncio
async def test_disconnect_and_cleanup_clears_all_state():
    vc = DummyVoiceClient(connected=True, playing=True)
    cog = AudioEngine(_make_bot())
    _add_session(
        cog,
        vc,
        queue=[QueueItem("u", "t", "d")],
        current=QueueItem("u", "t", "d"),
        command_channel=object(),
    )

    await cog.disconnect_and_cleanup(1)

    vc.stop.assert_called_once()
    vc.disconnect.assert_awaited_once()
    assert cog.sessions == {}


@pytest.mark.asyncio
async def test_disconnect_and_cleanup_stops_disconnected_player():
    vc = DummyVoiceClient(connected=False, playing=True)
    cog = AudioEngine(_make_bot())
    _add_session(cog, vc)

    await cog.disconnect_and_cleanup(1)

    vc.stop.assert_called_once()
    vc.disconnect.assert_not_awaited()


@pytest.mark.asyncio
async def test_play_next_pulls_from_queue(monkeypatch):
    cog = AudioEngine(_make_bot())
    item = QueueItem("url", "title", "3:00")
    _add_session(cog, DummyVoiceClient(connected=True), queue=[item])
    cog.play_next_track_and_announce = AsyncMock()
    scheduled = []

    def fake_run(coro, _loop):
        scheduled.append(coro)

    monkeypatch.setattr(
        "extensions.audio._audio_engine.run_coroutine_threadsafe", fake_run
    )
    cog.play_next(1)
    await scheduled[0]

    cog.play_next_track_and_announce.assert_awaited_once_with(1, item)
    assert cog.queue_snapshot(1)[1] == ()


@pytest.mark.asyncio
async def test_play_next_cleans_state_when_voice_disconnected(monkeypatch):
    cog = AudioEngine(_make_bot())
    _add_session(
        cog,
        DummyVoiceClient(connected=False),
        queue=[QueueItem("u", "t", "d")],
        current=QueueItem("u", "t", "d"),
        command_channel=object(),
    )
    scheduled = []

    def fake_run(coro, _loop):
        scheduled.append(coro)

    monkeypatch.setattr(
        "extensions.audio._audio_engine.run_coroutine_threadsafe", fake_run
    )
    cog.play_next(1)
    await scheduled[0]

    assert cog.sessions == {}
