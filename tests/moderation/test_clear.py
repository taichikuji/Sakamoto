from pathlib import Path
from types import SimpleNamespace
import sys
from unittest.mock import AsyncMock

import pytest
from discord import app_commands

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from extensions.moderation.clear import ClearCog


class DummyTextChannel:
    def __init__(self, messages):
        self.messages = messages
        self.purge = AsyncMock(side_effect=self._purge)

    async def _purge(self, *, limit, check):
        assert limit == 50
        return [message for message in self.messages[:limit] if check(message)]


def _interaction(channel, user=SimpleNamespace(mention="@moderator")):
    return SimpleNamespace(
        channel=channel,
        user=user,
        response=SimpleNamespace(defer=AsyncMock(), send_message=AsyncMock()),
        followup=SimpleNamespace(send=AsyncMock()),
    )


@pytest.mark.asyncio
async def test_clear_filters_messages_by_member_and_reports_scanned_count(monkeypatch):
    monkeypatch.setattr("extensions.moderation.clear.TextChannel", DummyTextChannel)
    monkeypatch.setattr("extensions.moderation.clear.Thread", type("DummyThread", (), {}))
    member = SimpleNamespace(display_name="Alice")
    other = SimpleNamespace(display_name="Bob")
    channel = DummyTextChannel(
        [SimpleNamespace(author=member), SimpleNamespace(author=other), SimpleNamespace(author=member)]
    )
    interaction = _interaction(channel)

    await ClearCog.clear.callback(ClearCog(SimpleNamespace()), interaction, 50, member)

    interaction.response.defer.assert_awaited_once_with(ephemeral=True)
    interaction.followup.send.assert_awaited_once_with(
        ":wastebasket: Scanned 50 messages and deleted 2 messages from Alice.",
        ephemeral=True,
    )


@pytest.mark.asyncio
async def test_clear_rejects_non_text_channels_without_purging():
    interaction = _interaction(SimpleNamespace())

    await ClearCog.clear.callback(ClearCog(SimpleNamespace()), interaction)

    interaction.followup.send.assert_awaited_once_with(
        ":x: This command can only be used in text channels.", ephemeral=True
    )


@pytest.mark.asyncio
async def test_clear_permission_error_mentions_requester():
    interaction = _interaction(SimpleNamespace(), user=SimpleNamespace(mention="@alice"))
    cog = ClearCog(SimpleNamespace())

    await cog.clear_error(interaction, app_commands.MissingPermissions(["manage_messages"]))

    interaction.response.send_message.assert_awaited_once_with(
        ":x: You don't have permission to use this command, @alice.", ephemeral=True
    )
