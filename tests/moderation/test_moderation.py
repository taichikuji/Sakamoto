import sys
from asyncio import gather
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiosqlite import connect
from discord import PermissionOverwrite

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from extensions.moderation.votekick import ModerationCog, VotekickView


class DummyEmbed:
    def __init__(self):
        self.title = "Votekick for User"
        self.description = "desc"
        self.color = 0x000000
        self.fields = [("Required Votes", "2", True), ("Votes", "Yes: 0 / No: 0", True)]

    def set_field_at(self, index, name, value, inline):
        self.fields[index] = (name, value, inline)


class DummyMember:
    def __init__(self, user_id: int, *, bot: bool = False, channel=None):
        self.id = user_id
        self.bot = bot
        self.display_name = f"user-{user_id}"
        self.mention = f"<@{user_id}>"
        self.voice = None if channel is None else SimpleNamespace(channel=channel)


def _make_interaction(*, user, guild):
    response = SimpleNamespace(
        send_message=AsyncMock(),
        edit_message=AsyncMock(),
    )
    return SimpleNamespace(
        user=user,
        guild=guild,
        response=response,
        original_response=AsyncMock(),
    )


@pytest.mark.asyncio
async def test_votekick_view_duplicate_votes_are_rejected():
    bot = SimpleNamespace(
        loop=SimpleNamespace(create_task=MagicMock()), get_cog=lambda _name: None
    )
    author = DummyMember(1)
    target = DummyMember(2)
    view = VotekickView(bot, required_votes=2, author=author, target=target)
    voter = DummyMember(10)
    interaction = _make_interaction(user=voter, guild=object())

    await view.children[0].callback(interaction)
    await view.children[0].callback(interaction)
    await view.children[1].callback(interaction)

    assert view.has_voted(voter.id) is True
    interaction.response.send_message.assert_awaited_with(
        ":x: You have already voted.", ephemeral=True
    )


@pytest.mark.asyncio
async def test_votekick_view_timeout_updates_embed_and_disables_buttons():
    bot = SimpleNamespace(
        loop=SimpleNamespace(create_task=MagicMock()), get_cog=lambda _name: None
    )
    view = VotekickView(
        bot, required_votes=2, author=DummyMember(1), target=DummyMember(2)
    )
    embed = DummyEmbed()
    message = SimpleNamespace(embeds=[embed], edit=AsyncMock())
    view.message = message

    await view.on_timeout()

    assert all(button.disabled for button in view.children)
    assert embed.title == "Votekick Timed Out"
    message.edit.assert_awaited_once_with(embed=embed, view=view)


@pytest.mark.asyncio
async def test_yes_button_successful_vote_kicks_target_and_records_ban(monkeypatch):
    bot = SimpleNamespace()
    bot.loop = SimpleNamespace(
        create_task=MagicMock(side_effect=lambda coro: coro.close())
    )
    bot.get_cog = lambda _name: None
    moderation_cog = ModerationCog(bot)
    moderation_cog.ban_temporarily = AsyncMock()
    bot.get_cog = lambda _name: moderation_cog

    original_channel = SimpleNamespace(set_permissions=AsyncMock())
    target = DummyMember(22, channel=original_channel)
    target.move_to = AsyncMock()
    author = DummyMember(11)
    view = VotekickView(bot, required_votes=1, author=author, target=target)

    embed = DummyEmbed()
    message = SimpleNamespace(embeds=[embed], edit=AsyncMock())
    view.message = message

    voter = DummyMember(99)
    interaction = _make_interaction(user=voter, guild=object())

    monkeypatch.setattr("extensions.moderation.votekick.Member", DummyMember)

    await view.children[0].callback(interaction)

    assert all(button.disabled for button in view.children)
    assert embed.title == "Votekick Successful"
    target.move_to.assert_awaited_once_with(None, reason="Votekick successful.")
    moderation_cog.ban_temporarily.assert_awaited_once_with(target, original_channel)


@pytest.mark.asyncio
async def test_votekick_command_guardrails(monkeypatch):
    monkeypatch.setattr("extensions.moderation.votekick.Member", DummyMember)
    cog = ModerationCog(SimpleNamespace(color=0x123456))

    # Not in a guild
    interaction = _make_interaction(user=DummyMember(1), guild=None)
    await ModerationCog.votekick.callback(cog, interaction, DummyMember(2))
    interaction.response.send_message.assert_awaited_with(
        ":x: This command can only be used in a server.", ephemeral=True
    )
    assert interaction.command_failed is True

    # Author not in voice
    interaction = _make_interaction(user=DummyMember(1), guild=object())
    await ModerationCog.votekick.callback(cog, interaction, DummyMember(2))
    interaction.response.send_message.assert_awaited_with(
        ":x: You must be in a voice channel to start a votekick.", ephemeral=True
    )
    assert interaction.command_failed is True

    # Target not in same voice channel
    author_channel = SimpleNamespace(id=1, members=[])
    target_channel = SimpleNamespace(id=2, members=[])
    interaction = _make_interaction(
        user=DummyMember(1, channel=author_channel), guild=object()
    )
    await ModerationCog.votekick.callback(
        cog, interaction, DummyMember(2, channel=target_channel)
    )
    interaction.response.send_message.assert_awaited_with(
        ":x: <@2> is not in your voice channel.", ephemeral=True
    )
    assert interaction.command_failed is True

    # Self-votekick
    interaction = _make_interaction(
        user=DummyMember(3, channel=author_channel), guild=object()
    )
    await ModerationCog.votekick.callback(
        cog, interaction, DummyMember(3, channel=author_channel)
    )
    interaction.response.send_message.assert_awaited_with(
        ":x: You cannot votekick yourself.", ephemeral=True
    )
    assert interaction.command_failed is True

    # Bot target
    interaction = _make_interaction(
        user=DummyMember(10, channel=author_channel), guild=object()
    )
    await ModerationCog.votekick.callback(
        cog, interaction, DummyMember(50, bot=True, channel=author_channel)
    )
    interaction.response.send_message.assert_awaited_with(
        ":x: You cannot votekick a bot.", ephemeral=True
    )
    assert interaction.command_failed is True


@pytest.mark.asyncio
async def test_votekick_command_happy_path_tracks_and_clears_state(monkeypatch):
    monkeypatch.setattr("extensions.moderation.votekick.Member", DummyMember)
    monkeypatch.setattr("extensions.moderation.votekick.VotekickView.wait", AsyncMock())

    bot = SimpleNamespace(color=0xABCDEF)
    cog = ModerationCog(bot)

    voice_channel = SimpleNamespace(members=[])
    author = DummyMember(1, channel=voice_channel)
    target = DummyMember(2, channel=voice_channel)
    other = DummyMember(3, channel=voice_channel)
    voice_channel.members = [author, target, other]

    interaction = _make_interaction(user=author, guild=object())
    sent_message = SimpleNamespace(embeds=[DummyEmbed()], edit=AsyncMock())
    interaction.original_response = AsyncMock(return_value=sent_message)

    await ModerationCog.votekick.callback(cog, interaction, target)

    interaction.response.send_message.assert_awaited_once()
    sent_view = interaction.response.send_message.await_args.kwargs["view"]
    assert isinstance(sent_view, VotekickView)
    assert sent_view.required_votes == 2
    assert sent_view.message is sent_message
    assert target.id not in cog.votekicks


@pytest.mark.asyncio
async def test_pending_ban_resumes_after_restart(tmp_path, monkeypatch):
    now = 1000
    monkeypatch.setattr("extensions.moderation.votekick.time", lambda: now)
    member = DummyMember(22)
    channel = SimpleNamespace(
        id=33,
        guild=SimpleNamespace(get_member=lambda _: member),
        overwrites_for=MagicMock(
            return_value=PermissionOverwrite(connect=None, speak=False)
        ),
        set_permissions=AsyncMock(),
    )
    bot = SimpleNamespace(
        db_path=str(tmp_path / "bans.db"),
        wait_until_ready=AsyncMock(),
        fetch_channel=AsyncMock(return_value=channel),
    )

    async def rows():
        async with (
            connect(bot.db_path) as db,
            db.execute("SELECT * FROM votekick_bans") as cursor,
        ):
            return await cursor.fetchall()

    first = ModerationCog(bot)
    await first.cog_load()
    await first.ban_temporarily(member, channel)
    assert await rows() == [(33, 22, 1060, None)]

    tasks = list(first._unban_tasks)
    first.cog_unload()
    await gather(*tasks, return_exceptions=True)
    assert first._unban_tasks == set()

    now = 1060
    wait_for_expiry = AsyncMock()
    monkeypatch.setattr("extensions.moderation.votekick.sleep", wait_for_expiry)
    channel.overwrites_for.return_value = PermissionOverwrite(
        connect=False, speak=False
    )
    restored = ModerationCog(bot)
    await restored.cog_load()
    await gather(*list(restored._unban_tasks))

    wait_for_expiry.assert_awaited_once_with(0)
    overwrite = channel.set_permissions.await_args.kwargs["overwrite"]
    assert overwrite.connect is None
    assert overwrite.speak is False
    assert await rows() == []
