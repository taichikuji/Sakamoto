import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from discord import Member, VoiceClient

from extensions.audio import _audio_engine, music


@pytest.mark.asyncio
async def test_play_queues_the_next_song_and_stop_disconnects(monkeypatch):
    voice = Mock(spec=VoiceClient)
    voice.is_connected.return_value = True
    voice.is_paused.return_value = False
    voice.is_playing.return_value = False
    voice.channel = SimpleNamespace(connect=AsyncMock(return_value=voice))

    next_song_announced = asyncio.Event()
    text_channel = SimpleNamespace(
        send=AsyncMock(side_effect=lambda *_args, **_kwargs: next_song_announced.set())
    )
    interaction = SimpleNamespace(
        guild_id=1,
        user=Mock(spec=Member, voice=SimpleNamespace(channel=voice.channel)),
        channel=text_channel,
        response=SimpleNamespace(defer=AsyncMock(), send_message=AsyncMock()),
        followup=SimpleNamespace(send=AsyncMock()),
        original_response=AsyncMock(),
    )
    bot = SimpleNamespace(loop=asyncio.get_running_loop(), add_listener=Mock(), color=0)
    cog = music.MusicCog(bot)

    tracks = {
        "First song": {
            "title": "First song",
            "webpage_url": "https://example.test/first",
            "url": "https://streams.example.test/first",
            "extractor_key": "Youtube",
            "format_id": "251",
            "acodec": "opus",
        },
        "Second song": {
            "title": "Second song",
            "webpage_url": "https://example.test/second",
            "url": "https://streams.example.test/second",
            "acodec": "aac",
        },
    }
    monkeypatch.setattr(cog, "search_source", tracks.__getitem__)
    source = Mock()
    monkeypatch.setattr(_audio_engine, "FFmpegOpusAudio", source)

    try:
        await cog.play.callback(cog, interaction, "First song")
        voice.channel.connect.assert_awaited_once_with(self_deaf=True)
        voice.play.assert_called_once()
        assert source.call_args.args == (tracks["First song"]["url"],)
        assert source.call_args.kwargs["codec"] == "copy"
        after_first_song = voice.play.call_args.kwargs["after"]

        await cog.play.callback(cog, interaction, "Second song")
        voice.play.assert_called_once()
        voice.channel.connect.assert_awaited_once()

        await cog.queue.callback(cog, interaction)
        queue = interaction.response.send_message.call_args.kwargs["embed"].description
        assert (
            queue.index("First song")
            < queue.index("Up Next")
            < queue.index("Second song")
        )

        after_first_song(None)
        await asyncio.wait_for(next_song_announced.wait(), timeout=2)
        assert voice.play.call_count == 2
        assert source.call_args.args == (tracks["Second song"]["url"],)
        assert source.call_args.kwargs["codec"] is None

        await cog.stop.callback(cog, interaction)
        voice.disconnect.assert_awaited_once()
        assert cog.engine.queue_snapshot(1) == (None, ())
        assert not cog.engine.is_connected(1)
    finally:
        await cog.engine.disconnect_and_cleanup(1)
        cog.cog_unload()


@pytest.mark.asyncio
async def test_play_requires_the_requester_to_be_in_voice(monkeypatch):
    cog = music.MusicCog(SimpleNamespace(add_listener=Mock()))
    lookup = AsyncMock()
    monkeypatch.setattr(cog, "resolve_source", lookup)
    interaction = SimpleNamespace(
        guild_id=1,
        user=Mock(spec=Member, voice=None),
        response=SimpleNamespace(send_message=AsyncMock()),
    )

    await cog.play.callback(cog, interaction, "A song")

    lookup.assert_not_awaited()
    response = interaction.response.send_message.call_args
    assert "voice channel" in response.args[0]
    assert response.kwargs["ephemeral"]
