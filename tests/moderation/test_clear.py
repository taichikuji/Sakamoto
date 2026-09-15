import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from discord import app_commands

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from extensions.moderation.clear import ClearCog


class DummyTextChannel:
    def __init__(self, messages):
        self.messages = messages
        self.history_yielded = 0
        self.delete_messages = AsyncMock()
        self.purge = AsyncMock()

    def history(self, *, limit):
        assert limit is None

        async def iterator():
            for message in self.messages:
                self.history_yielded += 1
                yield message

        return iterator()


def _message(author, message_id=2**63, *, deletable=True):
    return SimpleNamespace(
        author=author,
        id=message_id,
        type=SimpleNamespace(is_deletable=MagicMock(return_value=deletable)),
        delete=AsyncMock(),
    )


def _interaction(channel, user=SimpleNamespace(mention="@moderator")):
    return SimpleNamespace(
        channel=channel,
        user=user,
        response=SimpleNamespace(defer=AsyncMock(), send_message=AsyncMock()),
        followup=SimpleNamespace(send=AsyncMock()),
    )


def test_clear_amount_is_limited_to_one_bulk_delete():
    amount = next(
        parameter
        for parameter in ClearCog.clear.parameters
        if parameter.name == "amount"
    )

    assert (amount.min_value, amount.max_value) == (1, 100)


@pytest.mark.asyncio
async def test_clear_deletes_requested_number_of_messages_from_member(monkeypatch):
    monkeypatch.setattr("extensions.moderation.clear.TextChannel", DummyTextChannel)
    monkeypatch.setattr(
        "extensions.moderation.clear.Thread", type("DummyThread", (), {})
    )
    member = SimpleNamespace(display_name="Alice")
    other = SimpleNamespace(display_name="Bob")
    first = _message(member)
    second = _message(member)
    channel = DummyTextChannel(
        [
            first,
            _message(other),
            _message(member, deletable=False),
            _message(other),
            second,
            _message(member),
        ]
    )
    interaction = _interaction(channel)

    await ClearCog.clear.callback(ClearCog(SimpleNamespace()), interaction, 2, member)

    interaction.response.defer.assert_awaited_once_with(ephemeral=True)
    channel.delete_messages.assert_awaited_once_with([first, second])
    assert channel.history_yielded == 5
    interaction.followup.send.assert_awaited_once_with(
        ":wastebasket: Deleted 2 messages from Alice.",
        ephemeral=True,
    )


@pytest.mark.asyncio
async def test_clear_without_member_uses_native_batched_purge(monkeypatch):
    monkeypatch.setattr("extensions.moderation.clear.TextChannel", DummyTextChannel)
    monkeypatch.setattr(
        "extensions.moderation.clear.Thread", type("DummyThread", (), {})
    )
    channel = DummyTextChannel([])
    channel.purge.return_value = [object()] * 50
    interaction = _interaction(channel)

    await ClearCog.clear.callback(ClearCog(SimpleNamespace()), interaction, 50)

    channel.purge.assert_awaited_once_with(limit=50)
    channel.delete_messages.assert_not_awaited()
    interaction.followup.send.assert_awaited_once_with(
        ":wastebasket: Deleted 50 messages.", ephemeral=True
    )


@pytest.mark.asyncio
async def test_clear_deletes_old_member_messages_individually(monkeypatch):
    monkeypatch.setattr("extensions.moderation.clear.TextChannel", DummyTextChannel)
    monkeypatch.setattr(
        "extensions.moderation.clear.Thread", type("DummyThread", (), {})
    )
    member = SimpleNamespace(display_name="Alice")
    recent = _message(member)
    old = _message(member, message_id=1)
    channel = DummyTextChannel([recent, old])

    await ClearCog.clear.callback(
        ClearCog(SimpleNamespace()), _interaction(channel), 2, member
    )

    channel.delete_messages.assert_awaited_once_with([recent])
    old.delete.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_clear_rejects_non_text_channels_without_purging():
    interaction = _interaction(SimpleNamespace())

    await ClearCog.clear.callback(ClearCog(SimpleNamespace()), interaction)

    interaction.followup.send.assert_awaited_once_with(
        ":x: This command can only be used in text channels.", ephemeral=True
    )
    assert interaction.command_failed is True


@pytest.mark.asyncio
async def test_clear_permission_error_mentions_requester():
    interaction = _interaction(
        SimpleNamespace(), user=SimpleNamespace(mention="@alice")
    )
    cog = ClearCog(SimpleNamespace())

    await cog.clear_error(
        interaction, app_commands.MissingPermissions(["manage_messages"])
    )

    interaction.response.send_message.assert_awaited_once_with(
        ":x: You don't have permission to use this command, @alice.", ephemeral=True
    )
