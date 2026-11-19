from asyncio import Event, gather, wait_for
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from discord import Member, PermissionOverwrite

from extensions.community.lobby import LobbyCog
from extensions.moderation import votekick


@pytest.mark.asyncio
async def test_configured_generator_creates_a_lobby_and_deletes_it_when_empty(tmp_path):
    bot = SimpleNamespace(db_path=str(tmp_path / "sakamoto.sqlite"), color=0)
    first_cog = LobbyCog(bot)
    await first_cog.cog_load()
    lobby = SimpleNamespace(id=20, members=[], send=AsyncMock(), delete=AsyncMock())
    guild = SimpleNamespace(id=1, create_voice_channel=AsyncMock(return_value=lobby))
    generator = SimpleNamespace(
        id=10, name="Generator", guild=guild, category=None, overwrites={}
    )
    settings = SimpleNamespace(
        guild_id=guild.id,
        response=SimpleNamespace(defer=AsyncMock()),
        followup=SimpleNamespace(send=AsyncMock()),
    )
    await first_cog.set_generator.callback(first_cog, settings, generator)
    first_cog.cog_unload()

    cog = LobbyCog(bot)
    await cog.cog_load()
    owner = Mock(guild=guild, bot=False, display_name="Owner", mention="<@2>")
    owner.move_to = AsyncMock()
    disconnected = SimpleNamespace(channel=None)
    try:
        await cog.on_voice_state_update(
            owner, disconnected, SimpleNamespace(channel=generator)
        )

        owner.move_to.assert_awaited_once_with(lobby)
        owner_permissions = guild.create_voice_channel.call_args.kwargs["overwrites"][
            owner
        ]
        assert owner_permissions.manage_channels is True
        lobby.send.assert_awaited_once()

        cog.cog_unload()
        cog = LobbyCog(bot)
        await cog.cog_load()
        lobby.members = [owner]
        await cog.on_voice_state_update(
            Mock(bot=False), SimpleNamespace(channel=lobby), disconnected
        )
        lobby.delete.assert_not_awaited()
        lobby.members.clear()
        await cog.on_voice_state_update(
            owner, SimpleNamespace(channel=lobby), disconnected
        )
        lobby.delete.assert_awaited_once()
    finally:
        cog.cog_unload()

    restarted = LobbyCog(bot)
    await restarted.cog_load()
    assert not restarted.active_channels


@pytest.mark.asyncio
async def test_successful_voice_vote_restores_rejoin_permission_after_restart(
    monkeypatch, tmp_path
):
    deadline = Event()
    monkeypatch.setattr(votekick, "sleep", lambda _delay: deadline.wait())
    channel = SimpleNamespace(id=10, set_permissions=AsyncMock())
    target = Mock(spec=Member, id=2, voice=SimpleNamespace(channel=channel))
    target.move_to = AsyncMock()
    channel.guild = SimpleNamespace(get_member=Mock(return_value=target))
    channel.overwrites_for = Mock(
        side_effect=[
            PermissionOverwrite(connect=True),
            PermissionOverwrite(connect=False),
        ]
    )
    bot = SimpleNamespace(
        db_path=str(tmp_path / "sakamoto.sqlite"),
        get_cog=Mock(),
        wait_until_ready=AsyncMock(),
        fetch_channel=AsyncMock(return_value=channel),
    )
    cog = votekick.ModerationCog(bot)
    bot.get_cog.return_value = cog
    await cog.cog_load()
    view = votekick.VotekickView(bot, required_votes=1, target=target, channel=channel)
    interaction = SimpleNamespace(
        user=Mock(spec=Member, id=3, voice=None),
        response=SimpleNamespace(send_message=AsyncMock()),
    )

    try:
        assert not await view.interaction_check(interaction)
        interaction.user.voice = SimpleNamespace(channel=channel)
        assert await view.interaction_check(interaction)
        await view.yes_button.callback(interaction)

        target.move_to.assert_awaited_once()
        assert target.move_to.call_args.args == (None,)
        assert channel.set_permissions.call_args.kwargs["overwrite"].connect is False
    finally:
        view.stop()
        cog.cog_unload()

    restarted = votekick.ModerationCog(bot)
    try:
        await restarted.cog_load()
        deadline.set()
        await wait_for(gather(*restarted._unban_tasks), timeout=2)
        assert channel.set_permissions.await_count == 2
        assert channel.set_permissions.call_args.kwargs["overwrite"].connect is True
    finally:
        restarted.cog_unload()
