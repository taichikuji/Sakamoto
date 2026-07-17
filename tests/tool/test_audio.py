from collections import deque
from pathlib import Path
from types import SimpleNamespace
import sys
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from functions.tool._audio_engine import AudioEngine, QueueItem, get_audio_engine
from functions.tool.music import MusicCog
from functions.tool.radio import RadioCog


def _radio_command(name: str):
    return getattr(RadioCog, name)


def test_radio_subcommand_option_contracts():
    search = _radio_command("search")
    balloon = _radio_command("balloon")

    assert [(param.name, param.required, bool(param.autocomplete)) for param in search.parameters] == [
        ("query", True, True),
    ]
    assert balloon.parameters == []


def test_play_option_contracts():
    assert [(param.name, param.required, bool(param.autocomplete)) for param in MusicCog.play.parameters] == [
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
        self.voice = None if voice_channel is None else SimpleNamespace(channel=voice_channel)


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
    return SimpleNamespace(loop=object(), color=0x123456, session=session, add_listener=MagicMock())


def _make_interaction(*, user, guild_id=1):
    return SimpleNamespace(
        guild_id=guild_id,
        user=user,
        channel=SimpleNamespace(send=AsyncMock()),
        response=SimpleNamespace(defer=AsyncMock(), send_message=AsyncMock()),
        followup=SimpleNamespace(send=AsyncMock()),
    )


def test_audio_engine_registers_voice_listener_once():
    bot = _make_bot()

    engine = get_audio_engine(bot)

    assert get_audio_engine(bot) is engine
    bot.add_listener.assert_called_once_with(engine.handle_voice_state_update, "on_voice_state_update")


@pytest.mark.asyncio
async def test_play_song_returns_false_when_refreshed_stream_url_missing(monkeypatch):
    cog = AudioEngine(_make_bot())
    cog.voice_clients[1] = DummyVoiceClient(connected=True)
    cog.play_next = MagicMock()
    refresh_stream = AsyncMock(return_value=None)

    started = await cog.play_song(1, "https://example.test/watch", None, "Track", "3:00", refresh_stream)

    assert started is False
    cog.play_next.assert_called_once_with(1)


@pytest.mark.asyncio
async def test_play_song_starts_playback_and_tracks_current_song(monkeypatch):
    vc = DummyVoiceClient(connected=True)
    cog = AudioEngine(_make_bot())
    cog.voice_clients[1] = vc
    monkeypatch.setattr("functions.tool._audio_engine.FFmpegPCMAudio", lambda stream_url, **_kw: f"audio:{stream_url}")

    started = await cog.play_song(1, "https://example.test/watch", "https://stream.test", "Track", "3:00")

    assert started is True
    assert cog.currently_playing[1] == ("https://example.test/watch", "Track", "3:00")
    assert vc.play.call_args.args[0] == "audio:https://stream.test"


@pytest.mark.asyncio
async def test_play_song_cleans_source_when_playback_is_rejected(monkeypatch):
    source = SimpleNamespace(cleanup=MagicMock())
    vc = DummyVoiceClient(connected=True)
    vc.play.side_effect = RuntimeError("rejected")
    cog = AudioEngine(_make_bot())
    cog.voice_clients[1] = vc
    cog.play_next = MagicMock()
    monkeypatch.setattr(
        "functions.tool._audio_engine.FFmpegPCMAudio",
        lambda *_args, **_kwargs: source,
    )

    started = await cog.play_song(1, "source", "stream", "Track", "3:00")

    assert started is False
    source.cleanup.assert_called_once()


@pytest.mark.asyncio
async def test_ensure_user_in_same_voice_channel_rejects_other_channel(monkeypatch):
    cog = AudioEngine(_make_bot())
    bot_channel = object()
    other_channel = object()
    cog.voice_clients[1] = DummyVoiceClient(connected=True, channel=bot_channel)
    monkeypatch.setattr("functions.tool._audio_engine.Member", DummyMember)
    interaction = _make_interaction(user=DummyMember(42, voice_channel=other_channel), guild_id=1)

    vc = await cog.ensure_user_in_same_voice_channel(interaction, 1)

    assert vc is None
    interaction.response.send_message.assert_awaited_with(
        ":x: You must be in the same voice channel as the bot to use this command.",
        ephemeral=True,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("command", [MusicCog.stop, MusicCog.pause, MusicCog.resume, MusicCog.skip])
async def test_control_commands_return_early_when_same_channel_check_fails(command):
    interaction = _make_interaction(user=object(), guild_id=1)
    cog = MusicCog(_make_bot())
    cog.engine.ensure_user_in_same_voice_channel = AsyncMock(return_value=None)
    cog.engine.disconnect_and_cleanup = AsyncMock()

    await command.callback(cog, interaction)

    cog.engine.ensure_user_in_same_voice_channel.assert_awaited_once_with(interaction, 1)
    interaction.response.send_message.assert_not_awaited()
    cog.engine.disconnect_and_cleanup.assert_not_awaited()


def test_play_next_returns_without_voice_client(monkeypatch):
    cog = AudioEngine(_make_bot())
    monkeypatch.setattr("functions.tool._audio_engine.run_coroutine_threadsafe", lambda coro, _loop: coro.close())
    cog.play_next(123)
    assert cog.currently_playing == {}


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
    session = DummySession([{"data": {"title": "Mataro Radio", "url": "/listen/mataroradio/sFtKSe5I"}}])
    cog = RadioCog(_make_bot(session=session))

    channel_id, title = await cog.resolve_radio_station("sFtKSe5I")

    assert channel_id == "sFtKSe5I"
    assert title == "Mataro Radio"


@pytest.mark.asyncio
async def test_resolve_radio_station_falls_back_to_search():
    session = DummySession(
        [
            {"data": None},
            {
                "hits": {
                    "hits": [
                        {"_source": {"type": "place", "title": "Barcelona", "url": "/map/barcelona"}},
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

    channel_id, title = await cog.resolve_radio_station("FlaixBac")

    assert channel_id == "aaaa1111"
    assert title == "Flaixbac"
    assert session.calls[1][1] == {"q": "FlaixBac"}


@pytest.mark.asyncio
async def test_resolve_radio_station_with_none_query_uses_random_station():
    cog = RadioCog(_make_bot(session=DummySession([])))
    cog.pick_random_station = AsyncMock(return_value=("spain123", "Radio Marca"))

    channel_id, title = await cog.resolve_radio_station(None)

    assert channel_id == "spain123"
    assert title == "Radio Marca"
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

    channel_id, title = await cog.pick_random_station()

    assert channel_id == "sFtKSe5I"
    assert title == "Mataro Radio"


@pytest.mark.asyncio
async def test_resolve_radio_stream_url_prefers_redirect_location():
    session = DummySession([DummyResponse({}, status=302, headers={"Location": "https://stream.test/live"})])
    cog = RadioCog(_make_bot(session=session))

    stream_url = await cog.resolve_radio_stream_url("sFtKSe5I")

    assert stream_url == "https://stream.test/live"


@pytest.mark.asyncio
async def test_resolve_radio_stream_url_returns_api_url_on_success_status():
    session = DummySession([DummyResponse({}, status=200)])
    cog = RadioCog(_make_bot(session=session))

    stream_url = await cog.resolve_radio_stream_url("sFtKSe5I")

    assert stream_url == "https://radio.garden/api/ara/content/listen/sFtKSe5I/channel.mp3"


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
    interaction = _make_interaction(user=DummyMember(42, voice_channel=voice_channel), guild_id=1)
    radio = RadioCog(_make_bot())
    radio.resolve_radio_station = AsyncMock(side_effect=ValueError("No radio station found for that query."))
    radio.resolve_radio_stream_url = AsyncMock()
    radio.engine.get_or_connect_voice_client = AsyncMock()
    monkeypatch.setattr("functions.tool.radio.Member", DummyMember)

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
    interaction = _make_interaction(user=DummyMember(42, voice_channel=voice_channel), guild_id=1)
    radio = RadioCog(_make_bot())
    radio.resolve_radio_station = AsyncMock(return_value=("sFtKSe5I", "Flaixbac"))
    radio.resolve_radio_stream_url = AsyncMock(side_effect=ValueError("Could not resolve a playable radio stream."))
    radio.engine.get_or_connect_voice_client = AsyncMock()
    monkeypatch.setattr("functions.tool.radio.Member", DummyMember)

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
    interaction = _make_interaction(user=DummyMember(42, voice_channel=voice_channel), guild_id=1)
    radio = RadioCog(_make_bot())
    radio.resolve_radio_station = AsyncMock(return_value=("sFtKSe5I", "Flaixbac"))
    radio.resolve_radio_stream_url = AsyncMock(return_value="https://stream.test/live")
    radio.engine.enqueue_or_play = AsyncMock()
    monkeypatch.setattr("functions.tool.radio.Member", DummyMember)

    await _radio_command("balloon").callback(radio, interaction)

    radio.resolve_radio_station.assert_awaited_once_with(None)
    radio.resolve_radio_stream_url.assert_awaited_once_with("sFtKSe5I")
    radio.engine.enqueue_or_play.assert_awaited_once()


@pytest.mark.asyncio
async def test_play_connects_before_enqueue(monkeypatch):
    events = []
    connected_client = DummyVoiceClient(connected=True)
    voice_channel = DummyVoiceChannel(connected_client=connected_client)
    voice_channel.connect.side_effect = lambda **_kwargs: events.append("connect") or connected_client
    interaction = _make_interaction(user=DummyMember(42, voice_channel=voice_channel), guild_id=1)
    cog = MusicCog(_make_bot())
    cog.engine.enqueue_or_play = AsyncMock()
    monkeypatch.setattr("functions.tool.music.Member", DummyMember)

    async def lookup(_executor, _fn, _arg):
        events.append("lookup")
        return {
            "url": "https://stream.test/live",
            "webpage_url": "https://youtube.test/watch?v=abc",
            "title": "Track",
            "duration_string": "3:00",
        }

    monkeypatch.setattr(
        "functions.tool.music.get_running_loop",
        lambda: SimpleNamespace(run_in_executor=lookup),
    )

    await MusicCog.play.callback(cog, interaction, query="track")

    assert events == ["connect", "lookup"]
    voice_channel.connect.assert_awaited_once()
    assert cog.engine.voice_clients[1] is connected_client
    cog.engine.enqueue_or_play.assert_awaited_once()


@pytest.mark.asyncio
async def test_play_cleans_new_connection_when_source_lookup_fails(monkeypatch):
    connected_client = DummyVoiceClient(connected=True)
    voice_channel = DummyVoiceChannel(connected_client=connected_client)
    interaction = _make_interaction(
        user=DummyMember(42, voice_channel=voice_channel), guild_id=1
    )
    cog = MusicCog(_make_bot())
    monkeypatch.setattr("functions.tool.music.Member", DummyMember)
    monkeypatch.setattr("functions.tool.music.get_running_loop", lambda: DummyLoop(None))

    await MusicCog.play.callback(cog, interaction, query="missing")

    voice_channel.connect.assert_awaited_once()
    connected_client.stop.assert_called_once()
    connected_client.disconnect.assert_awaited_once()
    assert cog.engine.voice_clients == {}
    assert cog.engine.command_channels == {}


@pytest.mark.asyncio
async def test_play_keeps_existing_connection_when_source_lookup_fails(monkeypatch):
    connected_client = DummyVoiceClient(connected=True, playing=True)
    voice_channel = DummyVoiceChannel()
    interaction = _make_interaction(
        user=DummyMember(42, voice_channel=voice_channel), guild_id=1
    )
    cog = MusicCog(_make_bot())
    cog.engine.voice_clients[1] = connected_client
    monkeypatch.setattr("functions.tool.music.Member", DummyMember)
    monkeypatch.setattr("functions.tool.music.get_running_loop", lambda: DummyLoop(None))

    await MusicCog.play.callback(cog, interaction, query="missing")

    voice_channel.connect.assert_not_awaited()
    connected_client.stop.assert_not_called()
    connected_client.disconnect.assert_not_awaited()
    assert cog.engine.voice_clients[1] is connected_client


@pytest.mark.asyncio
async def test_play_query_autocomplete_returns_distinct_choices(monkeypatch):
    cog = MusicCog(_make_bot())
    monkeypatch.setattr(
        "functions.tool.music.get_running_loop",
        lambda: DummyLoop(
            {
                "entries": [
                    {"title": "Track", "webpage_url": "https://youtube.test/watch?v=abc"},
                    {"title": "Track", "webpage_url": "https://youtube.test/watch?v=abc"},
                ]
            }
        ),
    )

    choices = await cog.play_query_autocomplete(SimpleNamespace(), "track")

    assert [(choice.name, choice.value) for choice in choices] == [("Track", "https://youtube.test/watch?v=abc")]


@pytest.mark.asyncio
async def test_play_query_autocomplete_expands_video_id_to_url(monkeypatch):
    cog = MusicCog(_make_bot())
    monkeypatch.setattr(
        "functions.tool.music.get_running_loop",
        lambda: DummyLoop({"entries": [{"title": "Track", "url": "abc123"}]}),
    )

    choices = await cog.play_query_autocomplete(SimpleNamespace(), "track")

    assert [(choice.name, choice.value) for choice in choices] == [("Track", "https://www.youtube.com/watch?v=abc123")]


@pytest.mark.asyncio
async def test_play_query_autocomplete_limits_to_five_choices(monkeypatch):
    cog = MusicCog(_make_bot())
    monkeypatch.setattr(
        "functions.tool.music.get_running_loop",
        lambda: DummyLoop(
            {
                "entries": [
                    {"title": f"Track {i}", "webpage_url": f"https://youtube.test/watch?v={i}"}
                    for i in range(6)
                ]
            }
        ),
    )

    choices = await cog.play_query_autocomplete(SimpleNamespace(), "track")

    assert len(choices) == 5


def test_search_source_returns_first_entry(monkeypatch):
    ydl = MagicMock()
    ydl.__enter__.return_value.extract_info.side_effect = [
        {"entries": [{"url": "https://stream.test/live"}]},
        {"entries": []},
    ]
    monkeypatch.setattr("functions.tool.music.YoutubeDL", MagicMock(return_value=ydl))
    cog = MusicCog(_make_bot())

    assert cog.search_source("track") == {"url": "https://stream.test/live"}
    with pytest.raises(ValueError, match="No results found"):
        cog.search_source("missing")


@pytest.mark.asyncio
async def test_search_query_autocomplete_returns_channel_choices():
    session = DummySession(
        [
            {
                "hits": {
                    "hits": [
                        {"_source": {"type": "place", "title": "Barcelona", "url": "/map/barcelona"}},
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

    assert [(choice.name, choice.value) for choice in choices] == [("Flaixbac (Barcelona, Spain)", "sFtKSe5I")]


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
    cog.voice_clients[1] = vc
    followup = AsyncMock()

    await cog.enqueue_or_play(
        1,
        source_url="https://radio.garden/listen/mataroradio/sFtKSe5I",
        title="Mataro Radio",
        duration="LIVE",
        stream_url="https://radio.garden/api/ara/content/listen/sFtKSe5I/channel.mp3",
        followup=followup,
    )

    item = cog.queues[1][0]
    assert item.title == "Mataro Radio"
    assert item.stream_url == "https://radio.garden/api/ara/content/listen/sFtKSe5I/channel.mp3"
    followup.assert_awaited_once()


@pytest.mark.asyncio
async def test_queued_track_refreshes_stream_url_before_playback(monkeypatch):
    vc = DummyVoiceClient(connected=True, playing=True)
    cog = AudioEngine(_make_bot())
    cog.voice_clients[1] = vc
    followup = AsyncMock()
    refresh_stream = AsyncMock(return_value="https://stream.test/fresh")
    monkeypatch.setattr(
        "functions.tool._audio_engine.FFmpegPCMAudio",
        lambda stream_url, **_kw: f"audio:{stream_url}",
    )

    await cog.enqueue_or_play(
        1,
        source_url="https://youtube.test/watch?v=abc",
        title="Track",
        duration="3:00",
        stream_url="https://stream.test/stale",
        followup=followup,
        refresh_stream=refresh_stream,
    )

    item = cog.queues[1].popleft()
    assert item.source_url == "https://youtube.test/watch?v=abc"
    assert item.stream_url is None

    await cog.play_next_track_and_announce(
        1,
        item.source_url,
        item.title,
        item.duration,
        item.stream_url,
        item.refresh_stream,
    )

    refresh_stream.assert_awaited_once_with("https://youtube.test/watch?v=abc")
    assert vc.play.call_args.args[0] == "audio:https://stream.test/fresh"


@pytest.mark.asyncio
async def test_enqueue_or_play_rejects_when_queue_is_full():
    vc = DummyVoiceClient(connected=True, playing=True)
    cog = AudioEngine(_make_bot())
    cog.voice_clients[1] = vc
    cog.queues[1] = deque([QueueItem("u", "t", "d")] * 25)
    followup = AsyncMock()

    await cog.enqueue_or_play(
        1,
        source_url="https://youtube.test/watch?v=abc",
        title="Track",
        duration="3:00",
        stream_url="https://stream.test/live",
        followup=followup,
    )

    followup.assert_awaited_once_with(
        ":x: Queue is full (25 items).",
        ephemeral=True,
    )
    assert len(cog.queues[1]) == 25


@pytest.mark.asyncio
async def test_queue_displays_queued_items():
    interaction = _make_interaction(user=object(), guild_id=1)
    cog = MusicCog(_make_bot())
    cog.engine.queues[1] = deque([QueueItem("url", "Queued Track", "3:00")])

    await MusicCog.queue.callback(cog, interaction)

    embed = interaction.response.send_message.await_args.kwargs["embed"]
    assert embed.description == "1. Queued Track [3:00]"


@pytest.mark.asyncio
async def test_bot_disconnects_when_moved_to_empty_voice_channel():
    bot = _make_bot()
    bot.user = SimpleNamespace(id=99)
    member = SimpleNamespace(id=99, bot=True, guild=SimpleNamespace(id=1))
    new_channel = SimpleNamespace(members=[member])
    cog = AudioEngine(bot)
    cog.voice_clients[1] = DummyVoiceClient(connected=True, channel=new_channel)
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
    cog.voice_clients[1] = vc
    cog.queues[1] = deque([QueueItem("u", "t", "d")])
    cog.currently_playing[1] = ("u", "t", "d")
    cog.command_channels[1] = object()

    await cog.disconnect_and_cleanup(1)

    vc.stop.assert_called_once()
    vc.disconnect.assert_awaited_once()
    assert cog.voice_clients == {}
    assert cog.queues == {}
    assert cog.currently_playing == {}
    assert cog.command_channels == {}


@pytest.mark.asyncio
async def test_disconnect_and_cleanup_stops_disconnected_player():
    vc = DummyVoiceClient(connected=False, playing=True)
    cog = AudioEngine(_make_bot())
    cog.voice_clients[1] = vc

    await cog.disconnect_and_cleanup(1)

    vc.stop.assert_called_once()
    vc.disconnect.assert_not_awaited()


@pytest.mark.asyncio
async def test_play_next_pulls_from_queue(monkeypatch):
    cog = AudioEngine(_make_bot())
    cog.voice_clients[1] = DummyVoiceClient(connected=True)
    cog.queues[1] = deque([QueueItem("url", "title", "3:00")])
    cog.play_next_track_and_announce = AsyncMock()
    scheduled = []

    def fake_run(coro, _loop):
        scheduled.append(coro)

    monkeypatch.setattr("functions.tool._audio_engine.run_coroutine_threadsafe", fake_run)
    cog.play_next(1)
    await scheduled[0]

    cog.play_next_track_and_announce.assert_awaited_once_with(1, "url", "title", "3:00", None, None)
    assert cog.queues[1] == deque()


@pytest.mark.asyncio
async def test_play_next_cleans_state_when_voice_disconnected(monkeypatch):
    cog = AudioEngine(_make_bot())
    cog.voice_clients[1] = DummyVoiceClient(connected=False)
    cog.queues[1] = deque([QueueItem("u", "t", "d")])
    cog.currently_playing[1] = ("u", "t", "d")
    cog.command_channels[1] = object()
    scheduled = []

    def fake_run(coro, _loop):
        scheduled.append(coro)

    monkeypatch.setattr("functions.tool._audio_engine.run_coroutine_threadsafe", fake_run)
    cog.play_next(1)
    await scheduled[0]

    assert cog.voice_clients == {}
    assert cog.queues == {}
    assert cog.currently_playing == {}
    assert cog.command_channels == {}
