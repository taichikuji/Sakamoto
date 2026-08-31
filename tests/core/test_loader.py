from pathlib import Path
from types import SimpleNamespace
import sys
from unittest.mock import AsyncMock, MagicMock

import pytest
from discord import app_commands

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from extensions.core.loader import LoaderCog


def _interaction():
    return SimpleNamespace(response=SimpleNamespace(send_message=AsyncMock()))


@pytest.mark.asyncio
@pytest.mark.parametrize("command_name", ["load", "unload", "reload"])
async def test_loader_commands_prefix_extension_and_confirm_success(command_name):
    bot = SimpleNamespace(**{f"{command_name}_extension": AsyncMock()})
    cog = LoaderCog(bot)
    interaction = _interaction()
    command = getattr(LoaderCog, command_name)

    await command.callback(cog, interaction, "audio.music")

    getattr(bot, f"{command_name}_extension").assert_awaited_once_with(
        "extensions.audio.music"
    )
    interaction.response.send_message.assert_awaited_once_with(
        f":white_check_mark: {command_name.title()}ed extension 'audio.music' successfully.",
        ephemeral=True,
    )


@pytest.mark.asyncio
async def test_loader_command_reports_unexpected_extension_error(monkeypatch):
    bot = SimpleNamespace(load_extension=AsyncMock(side_effect=RuntimeError("boom")))
    interaction = _interaction()
    logger = MagicMock()
    monkeypatch.setattr("extensions.core.loader.logger", logger)

    await LoaderCog.load.callback(LoaderCog(bot), interaction, "missing")

    interaction.response.send_message.assert_awaited_once_with(
        ":x: An unexpected error occurred: boom", ephemeral=True
    )
    logger.error.assert_called_once()


@pytest.mark.asyncio
async def test_loader_permission_error_is_ephemeral():
    interaction = _interaction()
    cog = LoaderCog(SimpleNamespace())

    await cog.on_loader_error(
        interaction, app_commands.errors.MissingPermissions(["administrator"])
    )

    interaction.response.send_message.assert_awaited_once_with(
        ":x: You need Administrator permissions to run this command.", ephemeral=True
    )
