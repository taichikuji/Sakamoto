from importlib import import_module
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from discord.ext import commands
from discord.utils import async_all

from extensions.core.loader import LoaderCog
from extensions.core.shutdown import CloseCog
from extensions.core.sync import SyncCog


@pytest.mark.asyncio
async def test_bot_starts_without_steam_and_exposes_core_commands(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("TOKEN", "unit-test-token")
    monkeypatch.delenv("STEAM_TOKEN", raising=False)
    bot = import_module("main").Sakamoto()
    bot.db_path = str(tmp_path / "sakamoto.sqlite")

    async with bot:
        await bot.setup_hook()
        session = bot.session
        assert session is not None and not session.closed
        assert "extensions.integrations.steam" not in bot.extensions
        assert await bot.tree.interaction_check(SimpleNamespace(guild_id=1))
        assert not await bot.tree.interaction_check(SimpleNamespace(guild_id=None))

        help_command = bot.tree.get_command("help")
        interaction = SimpleNamespace(
            response=SimpleNamespace(send_message=AsyncMock())
        )
        await help_command.callback(help_command.binding, interaction)
        embed = interaction.response.send_message.call_args.kwargs["embed"]
        fields = {field.name: field.value for field in embed.fields}
        assert {
            "/play",
            "/queue",
            "/stop",
            "/lobby",
            "/votekick",
            "/load",
            "/unload",
            "/reload",
            "/sync",
            "/shutdown",
        } <= fields.keys()
        assert "/honeypot log set" in fields["/honeypot"]

        await help_command.callback(help_command.binding, interaction, "honeypot")
        embed = interaction.response.send_message.call_args.kwargs["embed"]
        assert "/honeypot log set" in embed.fields[0].value

        reload_command = bot.tree.get_command("reload")
        interaction.response.defer = AsyncMock()
        interaction.followup = SimpleNamespace(send=AsyncMock())
        await reload_command.callback(
            reload_command.binding, interaction, "general.help"
        )
        assert bot.tree.get_command("help") is not help_command
        assert bot.tree.get_command("help") is not None

    assert session.closed


@pytest.mark.asyncio
async def test_operational_commands_require_the_application_owner():
    owner = object()
    bot = SimpleNamespace(is_owner=AsyncMock(side_effect=lambda user: user is owner))

    for command in (
        LoaderCog.load,
        LoaderCog.unload,
        LoaderCog.reload,
        CloseCog.shutdown_bot,
    ):
        assert await async_all(
            check(SimpleNamespace(client=bot, user=owner)) for check in command.checks
        )
        assert not await async_all(
            check(SimpleNamespace(client=bot, user=object()))
            for check in command.checks
        ), command.name

    context = SimpleNamespace(bot=bot, author=owner, guild=object())
    assert await async_all(check(context) for check in SyncCog.sync.checks)
    context.author = object()
    with pytest.raises(commands.NotOwner):
        await async_all(check(context) for check in SyncCog.sync.checks)
