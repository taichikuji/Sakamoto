import importlib
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from discord import HTTPException, app_commands

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.fixture
def main_module(monkeypatch):
    monkeypatch.setenv("TOKEN", "unit-test-token")
    sys.modules.pop("main", None)
    module = importlib.import_module("main")
    yield module
    sys.modules.pop("main", None)


def test_bot_initializes_shared_resources(monkeypatch, main_module):
    monkeypatch.setattr(main_module.time, "monotonic", lambda: 123.0)
    bot = main_module.Sakamoto()

    assert bot.started_at == 123.0
    assert bot.session is None
    assert bot.color == 0xFF3351
    assert bot.db_path == "data/sakamoto.sqlite"
    assert bot.intents.message_content is True
    assert bot.help_command is None
    assert isinstance(bot.tree, main_module.SakamotoCommandTree)
    assert bot.tree.allowed_contexts.guild is True
    assert bot.tree.allowed_contexts.dm_channel is False
    assert bot.tree.allowed_contexts.private_channel is False


@pytest.mark.asyncio
async def test_command_tree_accepts_guilds_and_silently_rejects_dms(main_module):
    tree = main_module.Sakamoto().tree

    assert await tree.interaction_check(SimpleNamespace(guild_id=123)) is True
    assert await tree.interaction_check(SimpleNamespace(guild_id=None)) is False


@pytest.mark.asyncio
async def test_command_tree_dispatches_failures_without_awaiting_analytics(
    monkeypatch, main_module
):
    bot = main_module.Sakamoto()
    command = MagicMock()
    interaction = MagicMock(command=command)
    base_handler = AsyncMock()
    monkeypatch.setattr(main_module.app_commands.CommandTree, "on_error", base_handler)
    bot.dispatch = MagicMock()
    error = app_commands.AppCommandError("boom")

    await bot.tree.on_error(interaction, error)

    bot.dispatch.assert_called_once_with("app_command_failure", interaction, command)
    base_handler.assert_awaited_once_with(interaction, error)


@pytest.mark.asyncio
@pytest.mark.parametrize("deferred", [False, True])
async def test_command_tree_sends_safe_ephemeral_error(main_module, deferred):
    async def broken(interaction):
        pass

    command = app_commands.Command(name="broken", description="Test", callback=broken)
    interaction = SimpleNamespace(
        command=command,
        response=SimpleNamespace(is_done=lambda: deferred, send_message=AsyncMock()),
        followup=SimpleNamespace(send=AsyncMock()),
    )
    bot = main_module.Sakamoto()
    bot.dispatch = MagicMock()
    error = app_commands.CommandInvokeError(command, RuntimeError("private details"))

    await bot.tree.on_error(interaction, error)

    sender = (
        interaction.followup.send if deferred else interaction.response.send_message
    )
    sender.assert_awaited_once_with(
        ":x: Something went wrong running this command. Please try again.",
        ephemeral=True,
    )
    bot.dispatch.assert_called_once_with("app_command_failure", interaction, command)


@pytest.mark.asyncio
async def test_command_tree_leaves_locally_handled_errors_alone(main_module):
    @app_commands.command()
    async def guarded(interaction):
        """Test command."""

    @guarded.error
    async def handled(interaction, error):
        pass

    interaction = SimpleNamespace(
        command=guarded, response=MagicMock(), followup=MagicMock()
    )
    bot = main_module.Sakamoto()
    bot.dispatch = MagicMock()

    await bot.tree.on_error(interaction, app_commands.CheckFailure())

    interaction.response.assert_not_called()
    interaction.response.is_done.assert_not_called()
    interaction.followup.send.assert_not_called()


@pytest.mark.asyncio
async def test_command_tree_tolerates_expired_error_response(main_module):
    interaction = SimpleNamespace(
        command=None,
        response=SimpleNamespace(
            is_done=lambda: False,
            send_message=AsyncMock(
                side_effect=HTTPException(
                    SimpleNamespace(status=404, reason="Not Found"),
                    "Unknown interaction",
                )
            ),
        ),
    )
    await main_module.Sakamoto().tree.on_error(
        interaction, app_commands.AppCommandError("boom")
    )


@pytest.mark.asyncio
async def test_setup_hook_loads_public_extensions_and_continues_after_failure(
    monkeypatch, main_module
):
    bot = main_module.Sakamoto()
    session = MagicMock()
    bot.load_extension = AsyncMock(side_effect=[None, RuntimeError("broken extension")])
    monkeypatch.setattr(main_module, "ClientSession", MagicMock(return_value=session))
    monkeypatch.setattr(
        main_module.Path,
        "rglob",
        lambda _self, _pattern: [
            Path("extensions/general/info.py"),
            Path("extensions/_private.py"),
            Path("extensions/community/redirect.py"),
        ],
    )

    await bot.setup_hook()

    assert bot.session is session
    assert bot.load_extension.await_args_list[0].args == ("extensions.general.info",)
    assert bot.load_extension.await_args_list[1].args == (
        "extensions.community.redirect",
    )


@pytest.mark.asyncio
async def test_close_closes_shared_session_before_base_bot(monkeypatch, main_module):
    bot = main_module.Sakamoto()
    session = MagicMock(close=AsyncMock())
    base_close = AsyncMock()
    bot.session = session
    monkeypatch.setattr(main_module.commands.Bot, "close", base_close)

    await bot.close()

    session.close.assert_awaited_once()
    base_close.assert_awaited_once_with()
