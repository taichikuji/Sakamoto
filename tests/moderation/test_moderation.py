import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

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
async def test_votekick_view_duplicate_votes_are_rejected(monkeypatch):
    monkeypatch.setattr("extensions.moderation.votekick.Member", DummyMember)
    channel = object()
    target = DummyMember(2, channel=channel)
    view = VotekickView(SimpleNamespace(), 2, target, channel)
    voter = DummyMember(10, channel=channel)
    interaction = _make_interaction(user=voter, guild=object())

    assert await view.interaction_check(interaction) is True
    await view.children[0].callback(interaction)
    assert await view.interaction_check(interaction) is False

    assert view.yes_votes == {voter.id}
    assert view.no_votes == set()
    interaction.response.send_message.assert_awaited_with(
        ":x: You have already voted.", ephemeral=True
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("button_index", "vote_set"), [(0, "yes_votes"), (1, "no_votes")]
)
async def test_votekick_view_allows_channel_members_to_vote(
    monkeypatch, button_index, vote_set
):
    monkeypatch.setattr("extensions.moderation.votekick.Member", DummyMember)
    channel = object()
    view = VotekickView(SimpleNamespace(), 2, DummyMember(2, channel=channel), channel)
    voter = DummyMember(3, channel=channel)
    interaction = _make_interaction(user=voter, guild=object())

    assert await view.interaction_check(interaction) is True
    await view.children[button_index].callback(interaction)

    assert getattr(view, vote_set) == {voter.id}
    interaction.response.send_message.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case", ["not_member", "outside_channel", "left_channel", "target", "target_left"]
)
async def test_votekick_view_rejects_unauthorized_voters(monkeypatch, case):
    monkeypatch.setattr("extensions.moderation.votekick.Member", DummyMember)
    channel = object()
    target = DummyMember(2, channel=channel)
    view = VotekickView(SimpleNamespace(), 2, target, channel)
    voter = DummyMember(3, channel=channel)

    if case == "not_member":
        voter = SimpleNamespace(id=3, voice=SimpleNamespace(channel=channel))
    elif case == "outside_channel":
        voter = DummyMember(3, channel=object())
    elif case == "left_channel":
        voter = DummyMember(3)
    elif case == "target":
        voter = target
    elif case == "target_left":
        target.voice = None

    interaction = _make_interaction(user=voter, guild=object())

    assert await view.interaction_check(interaction) is False

    assert view.yes_votes == set()
    assert view.no_votes == set()
    interaction.response.send_message.assert_awaited_once()
    assert interaction.response.send_message.await_args.kwargs == {"ephemeral": True}


@pytest.mark.asyncio
async def test_votekick_view_timeout_updates_embed_and_disables_buttons():
    bot = SimpleNamespace(
        loop=SimpleNamespace(create_task=MagicMock()), get_cog=lambda _name: None
    )
    view = VotekickView(
        bot,
        required_votes=2,
        target=DummyMember(2),
        channel=object(),
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
    view = VotekickView(
        bot,
        required_votes=1,
        target=target,
        channel=original_channel,
    )

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
async def test_yes_vote_does_not_kick_target_from_another_channel():
    bot = SimpleNamespace()
    moderation_cog = ModerationCog(bot)
    moderation_cog.ban_temporarily = AsyncMock()
    bot.get_cog = lambda _name: moderation_cog
    original_channel = object()
    target = DummyMember(22, channel=original_channel)
    target.move_to = AsyncMock()
    view = VotekickView(bot, 1, target, original_channel)
    embed = DummyEmbed()
    view.message = SimpleNamespace(embeds=[embed], edit=AsyncMock())

    async def move_target(_interaction):
        target.voice.channel = object()

    view.update_embed = move_target
    await view.children[0].callback(
        _make_interaction(user=DummyMember(99), guild=object())
    )

    target.move_to.assert_not_awaited()
    moderation_cog.ban_temporarily.assert_not_awaited()
    assert embed.title == "Votekick Ended"


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
    assert sent_view.channel is voice_channel
    assert sent_view.message is sent_message
    assert target.id not in cog.votekicks


# Here lies the unit test which caused every commit test to last like 30 minutes rather than 5 minutes as it should have.
