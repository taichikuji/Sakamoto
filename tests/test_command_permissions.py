import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from discord.ext import commands
from discord.utils import async_all

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from extensions.community.lobby import LobbyCog
from extensions.core.analytics import AnalyticsCog
from extensions.core.loader import LoaderCog
from extensions.core.shutdown import CloseCog
from extensions.core.sync import SyncCog
from extensions.moderation.clear import ClearCog


def test_commands_publish_matching_visibility_defaults_and_keep_runtime_checks():
    lobby = LobbyCog(SimpleNamespace())
    permission_restricted = [
        (ClearCog.clear, "manage_messages"),
        (lobby.__cog_app_commands_group__, "manage_channels"),
    ]
    owner_only = [
        AnalyticsCog.analytics,
        LoaderCog.load,
        LoaderCog.unload,
        LoaderCog.reload,
        CloseCog.shutdown_bot,
        SyncCog.sync.app_command,
    ]

    for command, permission in permission_restricted:
        assert command.default_permissions is not None
        assert getattr(command.default_permissions, permission) is True
    assert all(command.default_permissions is None for command in owner_only)

    assert LoaderCog.load.checks
    assert LoaderCog.unload.checks
    assert LoaderCog.reload.checks
    assert CloseCog.shutdown_bot.checks
    assert SyncCog.sync.checks
    assert ClearCog.clear.checks
    assert lobby.set_generator.checks


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "command",
    [LoaderCog.load, LoaderCog.unload, LoaderCog.reload, CloseCog.shutdown_bot],
)
@pytest.mark.parametrize("is_owner", [True, False])
async def test_global_app_commands_require_application_owner(command, is_owner):
    user = object()
    bot = SimpleNamespace(is_owner=AsyncMock(return_value=is_owner))
    interaction = SimpleNamespace(client=bot, user=user)

    assert await command.checks[0](interaction) is is_owner
    bot.is_owner.assert_awaited_once_with(user)


@pytest.mark.asyncio
@pytest.mark.parametrize("is_owner", [True, False])
async def test_sync_requires_application_owner_for_both_invocation_paths(is_owner):
    user = object()
    bot = SimpleNamespace(is_owner=AsyncMock(return_value=is_owner))

    for interaction in (None, object()):
        ctx = SimpleNamespace(
            author=user, bot=bot, guild=object(), interaction=interaction
        )
        if is_owner:
            assert await async_all(check(ctx) for check in SyncCog.sync.checks)
        else:
            with pytest.raises(commands.NotOwner):
                await async_all(check(ctx) for check in SyncCog.sync.checks)

    assert bot.is_owner.await_count == 2
