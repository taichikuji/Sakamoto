from pathlib import Path
from types import SimpleNamespace
import sys
from unittest.mock import AsyncMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from functions.tool.redirect import ReplaceCog


@pytest.mark.parametrize(
    ("original", "expected"),
    [
        (
            "see https://twitter.com/alice/status/12345",
            "see https://fixupx.com/alice/status/12345",
        ),
        (
            "feed https://bsky.app/profile/a.test/post/b",
            "feed https://fxbsky.app/profile/a.test/post/b",
        ),
        (
            "clip https://www.tiktok.com/@u/video/123",
            "clip https://vm.tnktok.com/@u/video/123",
        ),
        (
            "photo https://instagram.com/p/abc",
            "photo https://kkinstagram.com/p/abc",
        ),
        (
            "art https://www.pixiv.net/en/artworks/123",
            "art https://phixiv.net/en/artworks/123",
        ),
        (
            "short https://youtube.com/shorts/abc123",
            "short https://youtu.be/abc123",
        ),
        (
            "thread https://reddit.com/r/python/comments/xyz",
            "thread https://vxreddit.com/r/python/comments/xyz",
        ),
    ],
)
def test_replace_text_rewrites_supported_domains(original, expected):
    cog = ReplaceCog(SimpleNamespace())
    assert cog.replace_text(original) == expected


@pytest.mark.asyncio
async def test_on_message_ignores_bot_messages_and_empty_content():
    cog = ReplaceCog(SimpleNamespace())
    channel = SimpleNamespace(send=AsyncMock())

    bot_message = SimpleNamespace(
        author=SimpleNamespace(bot=True),
        content="https://twitter.com/a/status/1",
        channel=channel,
    )
    empty_message = SimpleNamespace(
        author=SimpleNamespace(bot=False),
        content="",
        channel=channel,
    )

    await cog.on_message(bot_message)
    await cog.on_message(empty_message)

    channel.send.assert_not_awaited()


@pytest.mark.asyncio
async def test_on_message_ignores_messages_without_urls():
    cog = ReplaceCog(SimpleNamespace())
    channel = SimpleNamespace(send=AsyncMock())
    message = SimpleNamespace(
        author=SimpleNamespace(bot=False),
        content="hello world",
        channel=channel,
    )

    await cog.on_message(message)

    channel.send.assert_not_awaited()


@pytest.mark.asyncio
async def test_on_message_sends_rewritten_text_when_content_changes():
    cog = ReplaceCog(SimpleNamespace())
    channel = SimpleNamespace(send=AsyncMock())
    message = SimpleNamespace(
        author=SimpleNamespace(bot=False),
        content="https://twitter.com/alice/status/12345",
        channel=channel,
    )

    await cog.on_message(message)

    channel.send.assert_awaited_once_with("https://fixupx.com/alice/status/12345")
