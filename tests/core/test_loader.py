import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from discord import app_commands

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from extensions.core.loader import LoaderCog


def _interaction():
    return SimpleNamespace(
        response=SimpleNamespace(send_message=AsyncMock(), defer=AsyncMock()),
        followup=SimpleNamespace(send=AsyncMock()),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("command_name", ["load", "unload", "reload"])
async def test_loader_commands_prefix_extension_and_confirm_success(command_name):
    interaction = _interaction()

    async def extension_operation(_extension):
        interaction.response.defer.assert_awaited_once_with(ephemeral=True)

    bot = SimpleNamespace(
        **{f"{command_name}_extension": AsyncMock(side_effect=extension_operation)}
    )
    cog = LoaderCog(bot)
    command = getattr(LoaderCog, command_name)

    await command.callback(cog, interaction, "audio.music")

    getattr(bot, f"{command_name}_extension").assert_awaited_once_with(
        "extensions.audio.music"
    )
    interaction.followup.send.assert_awaited_once_with(
        f":white_check_mark: {command_name.title()}ed extension 'audio.music' successfully.",
        ephemeral=True,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("command_name", ["load", "unload", "reload"])
async def test_loader_command_reports_unexpected_extension_error(
    monkeypatch, command_name
):
    bot = SimpleNamespace(
        **{f"{command_name}_extension": AsyncMock(side_effect=RuntimeError("boom"))}
    )
    interaction = _interaction()
    logger = MagicMock()
    monkeypatch.setattr("extensions.core.loader.logger", logger)

    await getattr(LoaderCog, command_name).callback(
        LoaderCog(bot), interaction, "missing"
    )

    interaction.followup.send.assert_awaited_once_with(
        f":x: An unexpected error occurred while {command_name}ing the extension.",
        ephemeral=True,
    )
    logger.exception.assert_called_once()


@pytest.mark.asyncio
async def test_loader_owner_error_is_ephemeral():
    interaction = _interaction()
    cog = LoaderCog(SimpleNamespace())

    await cog.on_loader_error(interaction, app_commands.CheckFailure())

    interaction.response.send_message.assert_awaited_once_with(
        ":x: Only the bot owner can run this command.", ephemeral=True
    )
