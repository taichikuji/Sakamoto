import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from aiosqlite import connect
from discord import PermissionOverwrite

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from extensions.community.lobby import LobbyCog, RenameModal, VoiceControlView


async def _init_test_db(cog):
    if sys.version_info >= (3, 14):
        pytest.skip("aiosqlite.connect() blocks under Python 3.14 in this environment")
    await cog._init_db()


def test_set_generator_keeps_optional_channel_description():
    channel_param = LobbyCog.set_generator.parameters[0]
    assert LobbyCog.set_generator.name == "set"
    assert channel_param.name == "channel"
    assert (
        channel_param.description
        == "The voice channel to use as a lobby generator. Leave empty to clear."
    )
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
    interaction.followup.send.assert_awaited_with(
        ":lock: Channel locked.", ephemeral=True
    )

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
    await _init_test_db(cog)

    await cog._save_generator(101, 202)
    assert cog.generators[101] == 202

    async with (
        connect(bot.db_path) as db,
        db.execute(
            "SELECT channel_id FROM lobby_generator WHERE guild_id = ?", (101,)
        ) as cursor,
    ):
        row = await cursor.fetchone()
    assert row == (202,)

    await cog._remove_generator(101)
    assert 101 not in cog.generators

    async with (
        connect(bot.db_path) as db,
        db.execute(
            "SELECT channel_id FROM lobby_generator WHERE guild_id = ?", (101,)
        ) as cursor,
    ):
        row = await cursor.fetchone()
    assert row is None


@pytest.mark.asyncio
async def test_cleanup_ghost_lobbies_removes_missing_channels(tmp_path):
    bot = DummyBot(tmp_path / "lobby.db")
    cog = LobbyCog(bot)
    await _init_test_db(cog)
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
    async with (
        connect(bot.db_path) as db,
        db.execute("SELECT channel_id FROM lobby_active ORDER BY channel_id") as cursor,
    ):
        rows = await cursor.fetchall()
    assert rows == [(22,)]


@pytest.mark.asyncio
async def test_create_lobby_inherits_overwrites_and_elevates_owner(tmp_path):
    bot = DummyBot(tmp_path / "lobby.db")
    cog = LobbyCog(bot)
    await _init_test_db(cog)

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
            default_role: PermissionOverwrite(view_channel=False, connect=False),
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
    view = cog.control_views[new_channel.id]

    await cog._delete_lobby(new_channel)

    assert view.is_finished()
    assert cog.control_views == {}


@pytest.mark.asyncio
async def test_set_generator_set_clear_and_missing_clear_branches(tmp_path):
    bot = DummyBot(tmp_path / "lobby.db")
    cog = LobbyCog(bot)
    await _init_test_db(cog)

    interaction = _make_interaction(user=object(), guild_id=77)
    channel = SimpleNamespace(id=888, name="Generator VC")

    await LobbyCog.set_generator.callback(cog, interaction, channel)
    assert cog.generators[77] == 888
    interaction.response.defer.assert_awaited_once_with(ephemeral=True)
    interaction.followup.send.assert_awaited_with(
        ":white_check_mark: **Generator VC** is now the lobby generator.",
        ephemeral=True,
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
    member = SimpleNamespace(guild=guild, bot=False)
    before = SimpleNamespace(channel=before_channel)
    after = SimpleNamespace(channel=after_channel)

    cog.active_channels = {42}
    cog.generators = {5: 99}

    await cog.on_voice_state_update(member, before, after)

    cog._delete_lobby.assert_awaited_once_with(before_channel)
    cog._create_lobby.assert_awaited_once_with(member, after_channel)


@pytest.mark.asyncio
async def test_voice_state_update_does_not_create_lobby_for_bot(tmp_path):
    cog = LobbyCog(DummyBot(tmp_path / "lobby.db"))
    cog._create_lobby = AsyncMock()
    guild = SimpleNamespace(id=5)
    generator = SimpleNamespace(id=99, guild=guild)
    member = SimpleNamespace(guild=guild, bot=True)
    cog.generators = {5: 99}

    await cog.on_voice_state_update(
        member,
        SimpleNamespace(channel=None),
        SimpleNamespace(channel=generator),
    )

    cog._create_lobby.assert_not_awaited()


@pytest.mark.asyncio
async def test_cog_load_restores_generators_and_active_lobbies(tmp_path):
    bot = DummyBot(tmp_path / "lobby.db")
    first_cog = LobbyCog(bot)
    await _init_test_db(first_cog)
    await first_cog._save_generator(11, 22)
    async with connect(bot.db_path) as db:
        await db.execute("INSERT INTO lobby_active (channel_id) VALUES (?)", (33,))
        await db.commit()

    restored_cog = LobbyCog(bot)
    await restored_cog.cog_load()

    assert restored_cog.generators == {11: 22}
    assert restored_cog.active_channels == {33}


@pytest.mark.asyncio
async def test_on_ready_cleans_ghost_lobbies_only_once(tmp_path):
    cog = LobbyCog(DummyBot(tmp_path / "lobby.db"))
    cog._cleanup_ghost_lobbies = AsyncMock()

    await cog.on_ready()
    await cog.on_ready()

    cog._cleanup_ghost_lobbies.assert_awaited_once()


def test_cog_unload_stops_and_forgets_all_control_views(tmp_path):
    cog = LobbyCog(DummyBot(tmp_path / "lobby.db"))
    first_view = SimpleNamespace(stop=Mock())
    second_view = SimpleNamespace(stop=Mock())
    cog.control_views = {1: first_view, 2: second_view}

    cog.cog_unload()

    first_view.stop.assert_called_once_with()
    second_view.stop.assert_called_once_with()
    assert cog.control_views == {}


@pytest.mark.asyncio
async def test_voice_state_update_ignores_state_changes_in_same_channel(tmp_path):
    cog = LobbyCog(DummyBot(tmp_path / "lobby.db"))
    cog._create_lobby = AsyncMock()
    cog._delete_lobby = AsyncMock()
    channel = SimpleNamespace(id=42, members=[])
    member = SimpleNamespace(bot=False)

    await cog.on_voice_state_update(
        member,
        SimpleNamespace(channel=channel),
        SimpleNamespace(channel=channel),
    )

    cog._create_lobby.assert_not_awaited()
    cog._delete_lobby.assert_not_awaited()


@pytest.mark.asyncio
async def test_voice_state_update_keeps_nonempty_lobby(tmp_path):
    cog = LobbyCog(DummyBot(tmp_path / "lobby.db"))
    cog._delete_lobby = AsyncMock()
    channel = SimpleNamespace(id=42, members=[object()])
    cog.active_channels = {42}
    member = SimpleNamespace(bot=False)

    await cog.on_voice_state_update(
        member,
        SimpleNamespace(channel=channel),
        SimpleNamespace(channel=None),
    )

    cog._delete_lobby.assert_not_awaited()


@pytest.mark.asyncio
async def test_create_lobby_cleans_up_when_member_move_fails(tmp_path):
    bot = DummyBot(tmp_path / "lobby.db")
    cog = LobbyCog(bot)
    await _init_test_db(cog)
    new_channel = SimpleNamespace(id=303, delete=AsyncMock(), send=AsyncMock())
    guild = SimpleNamespace(create_voice_channel=AsyncMock(return_value=new_channel))
    member = SimpleNamespace(
        guild=guild,
        display_name="Owner",
        mention="@Owner",
        move_to=AsyncMock(side_effect=RuntimeError("voice disconnected")),
    )
    generator = SimpleNamespace(category=None, overwrites={})

    await cog._create_lobby(member, generator)

    new_channel.delete.assert_awaited_once()
    assert cog.active_channels == set()
    assert cog.control_views == {}
    async with (
        connect(bot.db_path) as db,
        db.execute("SELECT channel_id FROM lobby_active") as cursor,
    ):
        assert await cursor.fetchall() == []


@pytest.mark.asyncio
async def test_delete_lobby_removes_tracking_when_discord_delete_fails(tmp_path):
    bot = DummyBot(tmp_path / "lobby.db")
    cog = LobbyCog(bot)
    await _init_test_db(cog)
    channel = SimpleNamespace(
        id=303, delete=AsyncMock(side_effect=RuntimeError("forbidden"))
    )
    cog.active_channels.add(channel.id)
    view = VoiceControlView(DummyVoiceChannel(), object())
    cog.control_views[channel.id] = view
    async with connect(bot.db_path) as db:
        await db.execute(
            "INSERT INTO lobby_active (channel_id) VALUES (?)", (channel.id,)
        )
        await db.commit()

    await cog._delete_lobby(channel)

    assert view.is_finished()
    assert cog.active_channels == set()
    assert cog.control_views == {}
    async with (
        connect(bot.db_path) as db,
        db.execute("SELECT channel_id FROM lobby_active") as cursor,
    ):
        assert await cursor.fetchall() == []
