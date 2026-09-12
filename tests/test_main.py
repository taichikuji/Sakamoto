import importlib
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from discord import app_commands

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
    assert isinstance(bot.tree, main_module.SakamotoCommandTree)


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
            Path("extensions/general/ping.py"),
            Path("extensions/_private.py"),
            Path("extensions/community/redirect.py"),
        ],
    )

    await bot.setup_hook()

    assert bot.session is session
    assert bot.load_extension.await_args_list[0].args == ("extensions.general.ping",)
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
