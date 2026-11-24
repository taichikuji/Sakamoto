from importlib import import_module
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from discord import Message
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
        original_message = Mock(spec=Message)
        button_message = SimpleNamespace(edit=AsyncMock())
        interaction = SimpleNamespace(
            message=button_message,
            user=SimpleNamespace(id=1),
            response=SimpleNamespace(
                send_message=AsyncMock(
                    return_value=SimpleNamespace(resource=original_message)
                ),
                edit_message=AsyncMock(),
            ),
        )
        await help_command.callback(help_command.binding, interaction)
        idle_view = interaction.response.send_message.await_args.kwargs["view"]
        await idle_view.on_timeout()
        assert all(button.disabled for button in idle_view.children)
        original_message.edit.assert_awaited_once_with(view=idle_view)

        await help_command.callback(help_command.binding, interaction)
        first = interaction.response.send_message.call_args.kwargs
        embed, view = first["embed"], first["view"]
        assert await view.interaction_check(interaction)
        other_user = SimpleNamespace(
            user=SimpleNamespace(id=2),
            response=SimpleNamespace(send_message=AsyncMock()),
        )
        assert not await view.interaction_check(other_user)
        assert other_user.response.send_message.await_args.kwargs["ephemeral"]
        previous, next_page = view.children
        assert previous.disabled
        fields = {}
        pages = []
        expected_page_count = len(range(0, len(bot.tree.get_commands()), 5))
        for page_number in range(1, expected_page_count + 1):
            if page_number > 1:
                assert not next_page.disabled
                await next_page.callback(interaction)
                edit = interaction.response.edit_message.await_args.kwargs
                assert edit["view"] is view
                embed = edit["embed"]
            assert embed.footer.text == f"Page {page_number} of {expected_page_count}"
            assert len(embed.fields) <= 5
            assert len(embed) <= 6000
            pages.append(embed)
            for field in embed.fields:
                assert len(field.value) <= 1024
                assert field.name not in fields
                fields[field.name] = field.value
        assert next_page.disabled
        for page in reversed(pages[:-1]):
            assert not previous.disabled
            await previous.callback(interaction)
            assert interaction.response.edit_message.await_args.kwargs["embed"] is page
            assert not next_page.disabled
        assert previous.disabled
        assert fields.keys() == {
            f"/{command.name}" for command in bot.tree.get_commands()
        }
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

        await view.on_timeout()
        assert previous.disabled and next_page.disabled
        button_message.edit.assert_awaited_once_with(view=view)

        await help_command.callback(help_command.binding, interaction, "honeypot")
        detail = interaction.response.send_message.await_args.kwargs
        assert detail["ephemeral"]
        assert "view" not in detail
        assert "/honeypot log set" in detail["embed"].fields[0].value

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
