import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from discord import app_commands

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from extensions.core.shutdown import CloseCog


def _interaction(close):
    return SimpleNamespace(
        client=SimpleNamespace(user=SimpleNamespace(name="Sakamoto"), close=close),
        response=SimpleNamespace(send_message=AsyncMock()),
    )


@pytest.mark.asyncio
async def test_shutdown_confirms_before_closing_client():
    interaction = _interaction(AsyncMock())

    await CloseCog.shutdown_bot.callback(CloseCog(SimpleNamespace()), interaction)

    interaction.response.send_message.assert_awaited_once_with(
        ":wave: Shutting down Sakamoto...", ephemeral=True
    )
    interaction.client.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_shutdown_logs_close_failure_without_raising(monkeypatch):
    interaction = _interaction(AsyncMock(side_effect=RuntimeError("disconnect failed")))
    logger = MagicMock()
    monkeypatch.setattr("extensions.core.shutdown.logger", logger)

    await CloseCog.shutdown_bot.callback(CloseCog(SimpleNamespace()), interaction)

    logger.error.assert_called_once()


@pytest.mark.asyncio
async def test_shutdown_permission_error_is_ephemeral():
    interaction = _interaction(AsyncMock())
    cog = CloseCog(SimpleNamespace())

    await cog.on_shutdown_error(
        interaction, app_commands.errors.MissingPermissions(["administrator"])
    )

    interaction.response.send_message.assert_awaited_once_with(
        ":x: You need Administrator permissions to shut down the bot.", ephemeral=True
    )
