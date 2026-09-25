from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, call

import pytest
from discord import MessageType

from extensions.moderation.honeypot import HoneypotCog


def test_honeypot_log_set_accepts_an_optional_channel():
    command = HoneypotCog.log.get_command("set")
    assert command is HoneypotCog.set_log_channel
    assert command.parameters[0].required is False


class DummyMember:
    def __init__(self, *, admin=False, bot=False):
        self.id = 42
        self.bot = bot
        self.mention = f"<@{self.id}>"
        self.guild_permissions = SimpleNamespace(administrator=admin)


def _message(
    member, *, channel_id=12, message_type=MessageType.default, log_channel=None
):
    guild = SimpleNamespace(
        id=7,
        owner_id=99,
        ban=AsyncMock(),
        unban=AsyncMock(),
        get_channel=lambda channel_id: log_channel if channel_id == 99 else None,
    )
    return SimpleNamespace(
        guild=guild,
        author=member,
        channel=SimpleNamespace(id=channel_id),
        type=message_type,
        id=123,
        delete=AsyncMock(),
    )


@pytest.mark.asyncio
async def test_honeypot_softbans_and_cleans_recent_server_messages(monkeypatch):
    monkeypatch.setattr("extensions.moderation.honeypot.Member", DummyMember)
    cog = HoneypotCog(SimpleNamespace())
    cog.channels[7] = 12
    message = _message(DummyMember())
    actions = []
    message.guild.ban.side_effect = lambda *args, **kwargs: actions.append("ban")
    message.guild.unban.side_effect = lambda *args, **kwargs: actions.append("unban")

    await cog.on_message(message)

    assert actions == ["ban", "unban"]
    message.guild.ban.assert_awaited_once_with(
        message.author,
        delete_message_seconds=3600,
        reason="Posted in spam honeypot channel 12",
    )
    message.guild.unban.assert_awaited_once_with(
        message.author,
        reason="Completed honeypot softban in channel 12",
    )
    message.delete.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_honeypot_posts_final_softban_outcome_without_pinging(monkeypatch):
    monkeypatch.setattr("extensions.moderation.honeypot.Member", DummyMember)
    log_channel = SimpleNamespace(send=AsyncMock())
    cog = HoneypotCog(SimpleNamespace())
    cog.channels[7] = 12
    cog.log_channels[7] = 99
    message = _message(DummyMember(), log_channel=log_channel)

    await cog.on_message(message)

    log_channel.send.assert_awaited_once()
    args, kwargs = log_channel.send.await_args
    assert "Softban complete" in args[0]
    assert "<@42>" in args[0]
    assert kwargs["allowed_mentions"].users is False


@pytest.mark.asyncio
async def test_log_channel_can_be_set_and_cleared(monkeypatch):
    class FakeDB:
        def __init__(self):
            self.statements = []

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return None

        async def execute(self, sql, params):
            self.statements.append((sql, params))

    db = FakeDB()
    mocked_connect = Mock(return_value=db)
    monkeypatch.setattr("extensions.moderation.honeypot.connect", mocked_connect)
    cog = HoneypotCog(SimpleNamespace(db_path="unused"))
    me = object()
    interaction = SimpleNamespace(
        guild=SimpleNamespace(id=7, me=me),
        response=SimpleNamespace(send_message=AsyncMock()),
    )
    channel = SimpleNamespace(
        id=99,
        mention="#staff-log",
        permissions_for=lambda member: SimpleNamespace(
            view_channel=True, send_messages=True
        ),
    )

    await HoneypotCog.set_log_channel.callback(cog, interaction, channel)
    assert cog.log_channels[7] == 99
    assert "INSERT OR REPLACE" in db.statements[-1][0]

    await HoneypotCog.set_log_channel.callback(cog, interaction, None)
    assert 7 not in cog.log_channels
    assert "DELETE FROM" in db.statements[-1][0]
    assert mocked_connect.call_args_list == [
        call("unused", isolation_level=None),
        call("unused", isolation_level=None),
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("member", [DummyMember(admin=True), DummyMember(bot=True)])
async def test_honeypot_ignores_admins_and_bots(monkeypatch, member):
    monkeypatch.setattr("extensions.moderation.honeypot.Member", DummyMember)
    cog = HoneypotCog(SimpleNamespace())
    cog.channels[7] = 12
    message = _message(member)

    await cog.on_message(message)

    message.guild.ban.assert_not_awaited()
    message.guild.unban.assert_not_awaited()


@pytest.mark.asyncio
async def test_honeypot_ignores_system_messages(monkeypatch):
    monkeypatch.setattr("extensions.moderation.honeypot.Member", DummyMember)
    cog = HoneypotCog(SimpleNamespace())
    cog.channels[7] = 12
    message = _message(DummyMember(), message_type=MessageType.pins_add)

    await cog.on_message(message)

    message.guild.ban.assert_not_awaited()
    message.guild.unban.assert_not_awaited()


@pytest.mark.asyncio
async def test_honeypot_deletes_trigger_message_when_ban_fails(monkeypatch):
    monkeypatch.setattr("extensions.moderation.honeypot.Member", DummyMember)
    monkeypatch.setattr("extensions.moderation.honeypot.HTTPException", RuntimeError)
    cog = HoneypotCog(SimpleNamespace())
    cog.channels[7] = 12
    cog.log_channels[7] = 99
    log_channel = SimpleNamespace(send=AsyncMock())
    message = _message(DummyMember(), log_channel=log_channel)
    message.guild.ban = AsyncMock(side_effect=RuntimeError("ban failed"))

    await cog.on_message(message)

    message.guild.unban.assert_not_awaited()
    message.delete.assert_awaited_once_with()
    assert "Ban failed" in log_channel.send.await_args.args[0]


@pytest.mark.asyncio
async def test_honeypot_logs_unban_failure_and_still_deletes_trigger(
    monkeypatch, caplog
):
    monkeypatch.setattr("extensions.moderation.honeypot.Member", DummyMember)
    monkeypatch.setattr("extensions.moderation.honeypot.HTTPException", RuntimeError)
    cog = HoneypotCog(SimpleNamespace())
    cog.channels[7] = 12
    cog.log_channels[7] = 99
    log_channel = SimpleNamespace(send=AsyncMock())
    message = _message(DummyMember(), log_channel=log_channel)
    message.guild.unban.side_effect = RuntimeError("unban failed")

    await cog.on_message(message)

    message.guild.ban.assert_awaited_once()
    message.delete.assert_awaited_once_with()
    assert "manual unban needed" in caplog.text
    assert "must unban this member" in log_channel.send.await_args.args[0]


@pytest.mark.asyncio
async def test_log_send_failure_does_not_interrupt_softban(monkeypatch, caplog):
    monkeypatch.setattr("extensions.moderation.honeypot.Member", DummyMember)
    monkeypatch.setattr("extensions.moderation.honeypot.HTTPException", RuntimeError)
    log_channel = SimpleNamespace(send=AsyncMock(side_effect=RuntimeError("no access")))
    cog = HoneypotCog(SimpleNamespace())
    cog.channels[7] = 12
    cog.log_channels[7] = 99
    message = _message(DummyMember(), log_channel=log_channel)

    await cog.on_message(message)

    message.guild.unban.assert_awaited_once()
    assert "Could not post honeypot event" in caplog.text
