import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from extensions.community.redirect import ReplaceCog


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
            "clip https://vm.tiktok.com/abc123/",
            "clip https://vm.tnktok.com/abc123/",
        ),
        (
            "photo https://instagram.com/p/abc",
            "photo https://instagram7.com/p/abc",
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
        guild=object(),
    )
    empty_message = SimpleNamespace(
        author=SimpleNamespace(bot=False),
        content="",
        channel=channel,
        guild=object(),
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
        guild=object(),
    )

    await cog.on_message(message)

    channel.send.assert_not_awaited()


@pytest.mark.asyncio
async def test_on_message_sends_rewritten_text_when_content_changes():
    cog = ReplaceCog(SimpleNamespace())
    channel = SimpleNamespace(send=AsyncMock())
    message = SimpleNamespace(
        author=SimpleNamespace(bot=False),
        content=(
            "https://twitter.com/alice/status/12345 @everyone <@123> <@&456> "
            "**bold** _italic_"
        ),
        channel=channel,
        guild=object(),
    )

    await cog.on_message(message)

    sent = channel.send.await_args
    assert sent.args == (
        r"https://fixupx.com/alice/status/12345 @everyone <@123> <@&456> "
        r"\*\*bold\*\* \_italic\_",
    )
    allowed_mentions = sent.kwargs["allowed_mentions"]
    assert allowed_mentions.everyone is False
    assert allowed_mentions.users is False
    assert allowed_mentions.roles is False


@pytest.mark.asyncio
async def test_on_message_ignores_direct_messages():
    cog = ReplaceCog(SimpleNamespace())
    channel = SimpleNamespace(send=AsyncMock())
    message = SimpleNamespace(
        author=SimpleNamespace(bot=False),
        content="https://twitter.com/alice/status/12345",
        channel=channel,
        guild=None,
    )

    await cog.on_message(message)

    channel.send.assert_not_awaited()
