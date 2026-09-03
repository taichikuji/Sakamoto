import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from discord.ext import commands

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from extensions.core.sync import SyncCog


class DummyHTTPException(Exception):
    def __init__(self, status: int, text: str):
        super().__init__(f"{status} {text}")
        self.status = status
        self.text = text


@pytest.mark.asyncio
async def test_sync_scope_returns_synced_message_for_non_empty_result():
    bot = SimpleNamespace(tree=SimpleNamespace(sync=AsyncMock(return_value=[1, 2, 3])))
    cog = SyncCog(bot)

    message = await cog._sync_scope()

    assert message == "Synced 3 commands globally."


@pytest.mark.asyncio
async def test_sync_scope_returns_no_commands_message_for_empty_result():
    guild = SimpleNamespace(id=77)
    bot = SimpleNamespace(tree=SimpleNamespace(sync=AsyncMock(return_value=[])))
    cog = SyncCog(bot)

    message = await cog._sync_scope(guild)

    assert message == "No commands synced guild 77."


@pytest.mark.asyncio
async def test_sync_scope_formats_http_exception(monkeypatch):
    monkeypatch.setattr("extensions.core.sync.HTTPException", DummyHTTPException)
    bot = SimpleNamespace(
        tree=SimpleNamespace(sync=AsyncMock(side_effect=DummyHTTPException(429, "rate limited")))
    )
    cog = SyncCog(bot)

    message = await cog._sync_scope()

    assert message == "Failed sync globally: 429 rate limited"


@pytest.mark.asyncio
async def test_sync_scope_formats_unexpected_exception():
    bot = SimpleNamespace(tree=SimpleNamespace(sync=AsyncMock(side_effect=RuntimeError("boom"))))
    cog = SyncCog(bot)

    message = await cog._sync_scope()

    assert message == "Error sync globally: boom"


@pytest.mark.asyncio
async def test_sync_command_uses_followup_for_slash_context():
    interaction = SimpleNamespace(followup=SimpleNamespace(send=AsyncMock()))
    ctx = SimpleNamespace(
        interaction=interaction,
        guild=SimpleNamespace(id=5),
        defer=AsyncMock(),
        send=AsyncMock(),
    )
    cog = SyncCog(SimpleNamespace(tree=SimpleNamespace(sync=AsyncMock())))
    cog._sync_scope = AsyncMock(side_effect=["global ok", "guild ok"])

    await SyncCog.sync.callback(cog, ctx)

    ctx.defer.assert_awaited_once_with(ephemeral=True)
    interaction.followup.send.assert_awaited_once_with(
        "global ok\nguild ok\n\n**Note:** Restart Discord client to see changes.",
        ephemeral=True,
    )
    ctx.send.assert_not_awaited()


@pytest.mark.asyncio
async def test_sync_command_uses_ctx_send_for_non_slash_context():
    ctx = SimpleNamespace(
        interaction=None,
        guild=None,
        defer=AsyncMock(),
        send=AsyncMock(),
    )
    cog = SyncCog(SimpleNamespace(tree=SimpleNamespace(sync=AsyncMock())))
    cog._sync_scope = AsyncMock(return_value="global only")

    await SyncCog.sync.callback(cog, ctx)

    cog._sync_scope.assert_awaited_once_with()
    ctx.defer.assert_not_awaited()
    ctx.send.assert_awaited_once_with(
        "global only\nSkipped guild sync (not in server).\n\n**Note:** Restart Discord client to see changes."
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("is_slash", [True, False])
async def test_on_sync_error_sends_missing_permission_message(is_slash):
    interaction = SimpleNamespace(
        response=SimpleNamespace(send_message=AsyncMock()),
    )
    ctx = SimpleNamespace(
        interaction=interaction if is_slash else None,
        send=AsyncMock(),
    )
    cog = SyncCog(SimpleNamespace(tree=SimpleNamespace(sync=AsyncMock())))

    await cog.on_sync_error(ctx, commands.MissingPermissions(["administrator"]))

    if is_slash:
        interaction.response.send_message.assert_awaited_once_with(
            ":x: You need Administrator permissions to run this command.",
            ephemeral=True,
        )
        ctx.send.assert_not_awaited()
    else:
        ctx.send.assert_awaited_once_with(
            ":x: You need Administrator permissions to run this command."
        )


@pytest.mark.asyncio
async def test_on_sync_error_logs_unexpected_errors(monkeypatch):
    ctx = SimpleNamespace(interaction=None, send=AsyncMock())
    cog = SyncCog(SimpleNamespace(tree=SimpleNamespace(sync=AsyncMock())))
    fake_logger = MagicMock()
    monkeypatch.setattr("extensions.core.sync.logger", fake_logger)

    await cog.on_sync_error(ctx, RuntimeError("unexpected"))

    fake_logger.error.assert_called_once()
