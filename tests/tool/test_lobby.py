from pathlib import Path
from types import SimpleNamespace
import sys
from unittest.mock import AsyncMock

import pytest
from aiosqlite import connect
from discord import PermissionOverwrite

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from functions.tool.lobby import LobbyCog, RenameModal, VoiceControlView


def test_set_generator_keeps_optional_channel_description():
    channel_param = LobbyCog.set_generator.parameters[0]
    assert LobbyCog.set_generator.name == "set"
    assert channel_param.name == "channel"
    assert channel_param.description == "The voice channel to use as a lobby generator. Leave empty to clear."
    assert channel_param.required is False


class DummyBot:
    def __init__(self, db_path: Path):
        self.db_path = str(db_path)
        self.color = 0x123456
        self._channels: dict[int, object] = {}

    def get_channel(self, channel_id: int):
        return self._channels.get(channel_id)


class DummyVoiceChannel:
    def __init__(self, overwrites=None):
        self.last_role = None
        self.last_overwrite = None
        self.last_name = None
        self.overwrites = overwrites or {}
        self.set_permissions = AsyncMock(side_effect=self._set_permissions)
        self.edit = AsyncMock(side_effect=self._edit)

    def overwrites_for(self, role):
        return self.overwrites.setdefault(role, PermissionOverwrite())

    async def _set_permissions(self, role, overwrite):
        self.last_role = role
        self.last_overwrite = overwrite
        self.overwrites[role] = overwrite

    async def _edit(self, name):
        self.last_name = name


def _make_interaction(*, user, guild=None, guild_id=None):
    response = SimpleNamespace(
        send_message=AsyncMock(),
        edit_message=AsyncMock(),
        send_modal=AsyncMock(),
        defer=AsyncMock(),
    )
    followup = SimpleNamespace(send=AsyncMock())
    return SimpleNamespace(
        user=user,
        guild=guild,
        guild_id=guild_id,
        response=response,
        followup=followup,
    )


@pytest.mark.asyncio
async def test_voice_control_interaction_check_rejects_non_owner():
    owner = object()
    view = VoiceControlView(DummyVoiceChannel(), owner)
    interaction = _make_interaction(user=object())

    allowed = await view.interaction_check(interaction)

    assert allowed is False
    interaction.response.send_message.assert_awaited_once_with(
        ":x: You don't own this channel!", ephemeral=True
    )


@pytest.mark.asyncio
async def test_voice_control_lock_and_unlock_toggle_permissions_and_buttons():
    owner = object()
    default_role = object()
    allowed_role = object()
    channel = DummyVoiceChannel(
        {
            default_role: PermissionOverwrite(),
            allowed_role: PermissionOverwrite(connect=True),
            owner: PermissionOverwrite(connect=True),
        }
    )
    guild = SimpleNamespace(default_role=default_role)
    view = VoiceControlView(channel, owner)
    interaction = _make_interaction(user=owner, guild=guild)

    lock_button = view.children[0]
    unlock_button = view.children[1]

    await lock_button.callback(interaction)

    assert lock_button.disabled is True
    assert unlock_button.disabled is False
    assert channel.overwrites[default_role].connect is False
    assert channel.overwrites[allowed_role].connect is False
    assert channel.overwrites[owner].connect is True
    interaction.followup.send.assert_awaited_with(":lock: Channel locked.", ephemeral=True)

    await unlock_button.callback(interaction)

    assert lock_button.disabled is False
    assert unlock_button.disabled is True
    assert channel.overwrites[default_role].connect is None
    assert channel.overwrites[allowed_role].connect is True
    assert channel.overwrites[owner].connect is True
    interaction.followup.send.assert_awaited_with(
        ":unlock: Channel unlocked.", ephemeral=True
    )


@pytest.mark.asyncio
async def test_rename_modal_submit_edits_channel_and_sends_confirmation():
    channel = DummyVoiceChannel()
    interaction = _make_interaction(user=object())
    modal = RenameModal(channel)
    modal.name._value = "Focus Room"

    await modal.on_submit(interaction)

    assert channel.last_name == "Focus Room"
    interaction.response.send_message.assert_awaited_once_with(
        ":white_check_mark: Renamed to **Focus Room**", ephemeral=True
    )


@pytest.mark.asyncio
async def test_save_and_remove_generator_updates_memory_and_database(tmp_path):
    bot = DummyBot(tmp_path / "lobby.db")
    cog = LobbyCog(bot)
    await cog._init_db()

    await cog._save_generator(101, 202)
    assert cog.generators[101] == 202

    async with connect(bot.db_path) as db:
        async with db.execute(
            "SELECT channel_id FROM lobby_generator WHERE guild_id = ?", (101,)
        ) as cursor:
            row = await cursor.fetchone()
    assert row == (202,)

    await cog._remove_generator(101)
    assert 101 not in cog.generators

    async with connect(bot.db_path) as db:
        async with db.execute(
            "SELECT channel_id FROM lobby_generator WHERE guild_id = ?", (101,)
        ) as cursor:
            row = await cursor.fetchone()
    assert row is None


@pytest.mark.asyncio
async def test_cleanup_ghost_lobbies_removes_missing_channels(tmp_path):
    bot = DummyBot(tmp_path / "lobby.db")
    cog = LobbyCog(bot)
    await cog._init_db()
    cog.active_channels = {11, 22}
    bot._channels[22] = object()

    async with connect(bot.db_path) as db:
        await db.executemany(
            "INSERT INTO lobby_active (channel_id) VALUES (?)",
            [(11,), (22,)],
        )
        await db.commit()

    await cog._cleanup_ghost_lobbies()

    assert cog.active_channels == {22}
    async with connect(bot.db_path) as db:
        async with db.execute("SELECT channel_id FROM lobby_active ORDER BY channel_id") as cursor:
            rows = await cursor.fetchall()
    assert rows == [(22,)]


@pytest.mark.asyncio
async def test_create_lobby_inherits_overwrites_and_elevates_owner(tmp_path):
    bot = DummyBot(tmp_path / "lobby.db")
    cog = LobbyCog(bot)
    await cog._init_db()

    class DummyMember:
        display_name = "Owner"
        mention = "@Owner"

        def __init__(self, guild):
            self.guild = guild
            self.move_to = AsyncMock()

    default_role = object()
    allowed_role = object()
    new_channel = SimpleNamespace(id=303, send=AsyncMock(), delete=AsyncMock())
    guild = SimpleNamespace(
        default_role=default_role,
        create_voice_channel=AsyncMock(return_value=new_channel),
    )
    member = DummyMember(guild)
    generator = SimpleNamespace(
        category=object(),
        overwrites={
            default_role: PermissionOverwrite(
                view_channel=False, connect=False
            ),
            allowed_role: PermissionOverwrite(
                view_channel=True, connect=True, speak=False
            ),
            member: PermissionOverwrite(speak=False),
        },
    )

    await cog._create_lobby(member, generator)

    overwrites = guild.create_voice_channel.await_args.kwargs["overwrites"]
    assert overwrites[default_role].view_channel is False
    assert overwrites[default_role].connect is False
    assert overwrites[allowed_role].view_channel is True
    assert overwrites[allowed_role].connect is True
    assert overwrites[allowed_role].speak is False
    assert overwrites[member].speak is False
    assert overwrites[member].connect is True
    assert overwrites[member].move_members is True
    assert overwrites[member].manage_channels is True


@pytest.mark.asyncio
async def test_set_generator_set_clear_and_missing_clear_branches(tmp_path):
    bot = DummyBot(tmp_path / "lobby.db")
    cog = LobbyCog(bot)
    await cog._init_db()

    interaction = _make_interaction(user=object(), guild_id=77)
    channel = SimpleNamespace(id=888, name="Generator VC")

    await LobbyCog.set_generator.callback(cog, interaction, channel)
    assert cog.generators[77] == 888
    interaction.followup.send.assert_awaited_with(
        ":white_check_mark: **Generator VC** is now the lobby generator.", ephemeral=True
    )

    await LobbyCog.set_generator.callback(cog, interaction, None)
    assert 77 not in cog.generators
    interaction.followup.send.assert_awaited_with(
        ":white_check_mark: Lobby generator cleared.", ephemeral=True
    )

    await LobbyCog.set_generator.callback(cog, interaction, None)
    interaction.followup.send.assert_awaited_with(
        ":x: No lobby generator is set for this server.", ephemeral=True
    )


@pytest.mark.asyncio
async def test_voice_state_update_routes_to_create_and_delete_paths(tmp_path):
    bot = DummyBot(tmp_path / "lobby.db")
    cog = LobbyCog(bot)
    cog._create_lobby = AsyncMock()
    cog._delete_lobby = AsyncMock()

    guild = SimpleNamespace(id=5)
    before_channel = SimpleNamespace(id=42, members=[])
    after_channel = SimpleNamespace(id=99, guild=guild)
    member = SimpleNamespace(guild=guild)
    before = SimpleNamespace(channel=before_channel)
    after = SimpleNamespace(channel=after_channel)

    cog.active_channels = {42}
    cog.generators = {5: 99}

    await cog.on_voice_state_update(member, before, after)

    cog._delete_lobby.assert_awaited_once_with(before_channel)
    cog._create_lobby.assert_awaited_once_with(member, after_channel)
