import logging
from asyncio import Task, create_task, sleep
from os import makedirs, path
from time import time
from typing import TYPE_CHECKING

from aiosqlite import connect
from discord import (
    ButtonStyle,
    Embed,
    Interaction,
    Member,
    Message,
    NotFound,
    app_commands,
)
from discord.ext import commands
from discord.ui import Button, View, button

from extensions.core.analytics import mark_app_command_failed

if TYPE_CHECKING:
    from main import Sakamoto

logger = logging.getLogger(__name__)


class VotekickView(View):
    """Interactive vote controls for a Voice Votekick."""

    def __init__(
        self, bot: Sakamoto, required_votes: int, author: Member, target: Member
    ):
        super().__init__(timeout=60.0)
        self.bot = bot
        self.required_votes = required_votes
        self.author = author
        self.target = target
        self.yes_votes: set[int] = set()
        self.no_votes: set[int] = set()
        self.message: Message | None = None

    def has_voted(self, user_id: int) -> bool:
        """Return whether a member has already voted."""
        return user_id in self.yes_votes or user_id in self.no_votes

    def disable_all_buttons(self):
        """Disable every vote control."""
        for item in self.children:
            if isinstance(item, Button):
                item.disabled = True

    async def on_timeout(self):
        """Mark an unresolved Voice Votekick as timed out."""
        if self.message:
            self.disable_all_buttons()

            embed = self.message.embeds[0]
            embed.title = "Votekick Timed Out"
            embed.description = (
                f":hourglass: The votekick against {self.target.mention} timed out."
            )
            embed.color = 0xFF0000  # Red

            await self.message.edit(embed=embed, view=self)

    async def update_embed(self, interaction: Interaction):
        """Update the displayed vote counts."""
        if self.message:
            embed = self.message.embeds[0]
            embed.set_field_at(
                1,
                name="Votes",
                value=(
                    f":heavy_check_mark: Yes: {len(self.yes_votes)}\n"
                    f":x: No: {len(self.no_votes)}"
                ),
                inline=True,
            )
            await interaction.response.edit_message(embed=embed, view=self)

    @button(label="Yes", style=ButtonStyle.green)
    async def yes_button(self, interaction: Interaction, _button: Button):
        """Record an affirmative vote and complete a successful Votekick."""
        if self.has_voted(interaction.user.id):
            await interaction.response.send_message(
                ":x: You have already voted.", ephemeral=True
            )
            return

        self.yes_votes.add(interaction.user.id)
        await self.update_embed(interaction)

        if len(self.yes_votes) >= self.required_votes:
            self.stop()
            if self.message:
                self.disable_all_buttons()

                embed = self.message.embeds[0]
                embed.title = "Votekick Successful"
                embed.description = (
                    f":heavy_check_mark: {self.target.mention} has been kicked "
                    "from the voice channel."
                )
                embed.color = 0x00FF00  # Green

                await self.message.edit(embed=embed, view=self)

            if self.target.voice and self.target.voice.channel:
                original_channel = self.target.voice.channel
                try:
                    await self.target.move_to(None, reason="Votekick successful.")
                except Exception:
                    logger.error("Failed to move %s during votekick.", self.target)

                if isinstance(cog := self.bot.get_cog("ModerationCog"), ModerationCog):
                    await cog.ban_temporarily(self.target, original_channel)

    @button(label="No", style=ButtonStyle.red)
    async def no_button(self, interaction: Interaction, _button: Button):
        """Record a negative vote."""
        if self.has_voted(interaction.user.id):
            await interaction.response.send_message(
                ":x: You have already voted.", ephemeral=True
            )
            return

        self.no_votes.add(interaction.user.id)
        await self.update_embed(interaction)


class ModerationCog(commands.Cog):
    """Cog for moderation commands."""

    def __init__(self, bot: Sakamoto):
        self.bot = bot
        self.votekicks: dict[int, Message] = {}
        self._unban_tasks: set[Task] = set()

    async def cog_load(self):
        makedirs(path.dirname(self.bot.db_path), exist_ok=True)
        async with connect(self.bot.db_path) as db:
            await db.execute("""
                CREATE TABLE IF NOT EXISTS votekick_bans (
                    channel_id INTEGER NOT NULL,
                    member_id INTEGER NOT NULL,
                    expires_at REAL NOT NULL,
                    previous_connect INTEGER,
                    PRIMARY KEY (channel_id, member_id)
                )
            """)
            await db.commit()
            async with db.execute("SELECT * FROM votekick_bans") as cursor:
                pending_bans = await cursor.fetchall()

        for row in pending_bans:
            self._schedule_unban(*row)

    def cog_unload(self):
        for task in self._unban_tasks:
            task.cancel()
        self._unban_tasks.clear()

    async def ban_temporarily(self, member: Member, channel):
        expires_at = time() + 60
        overwrite = channel.overwrites_for(member)
        previous_connect = overwrite.connect
        async with connect(self.bot.db_path) as db:
            await db.execute(
                "INSERT OR REPLACE INTO votekick_bans VALUES (?, ?, ?, ?)",
                (channel.id, member.id, expires_at, previous_connect),
            )
            await db.commit()
        overwrite.connect = False
        await channel.set_permissions(member, overwrite=overwrite)
        self._schedule_unban(channel.id, member.id, expires_at, previous_connect)

    def _schedule_unban(self, channel_id, member_id, expires_at, previous_connect):
        task = create_task(
            self.unban_after_delay(channel_id, member_id, expires_at, previous_connect)
        )
        self._unban_tasks.add(task)
        task.add_done_callback(self._unban_tasks.discard)

    async def unban_after_delay(
        self, channel_id, member_id, expires_at, previous_connect
    ):
        """Remove a persisted Temporary Rejoin Ban after its deadline."""
        try:
            await sleep(max(0, expires_at - time()))
            await self.bot.wait_until_ready()
            try:
                channel = await self.bot.fetch_channel(channel_id)
                member = channel.guild.get_member(member_id)
                if member is None:
                    member = await self.bot.fetch_user(member_id)
                overwrite = channel.overwrites_for(member)
                if overwrite.connect is False:
                    overwrite.connect = (
                        None if previous_connect is None else bool(previous_connect)
                    )
                    await channel.set_permissions(
                        member,
                        overwrite=None if overwrite.is_empty() else overwrite,
                    )
            except NotFound:
                pass

            async with connect(self.bot.db_path) as db:
                await db.execute(
                    "DELETE FROM votekick_bans WHERE channel_id = ? AND member_id = ?",
                    (channel_id, member_id),
                )
                await db.commit()
        except Exception as error:
            logger.warning("Could not expire ban in %s: %s", channel_id, error)

    @app_commands.command(
        name="votekick",
        description="Start a vote to kick a user from the current voice channel.",
    )
    @app_commands.describe(
        member="Member to vote to remove from the current voice channel."
    )
    # Guard clauses keep each rejected Voice Votekick case self-contained.
    async def votekick(self, interaction: Interaction, member: Member):
        """Start a Voice Votekick for a member in the caller's channel."""
        if not interaction.guild:
            await interaction.response.send_message(
                ":x: This command can only be used in a server.", ephemeral=True
            )
            mark_app_command_failed(interaction)
            return

        if not isinstance(author := interaction.user, Member):
            await interaction.response.send_message(
                ":x: You must be a member of this server to use this command.",
                ephemeral=True,
            )
            mark_app_command_failed(interaction)
            return

        if not (author_voice := author.voice) or not (
            voice_channel := author_voice.channel
        ):
            await interaction.response.send_message(
                ":x: You must be in a voice channel to start a votekick.",
                ephemeral=True,
            )
            mark_app_command_failed(interaction)
            return

        if not (member_voice := member.voice) or member_voice.channel != voice_channel:
            await interaction.response.send_message(
                f":x: {member.mention} is not in your voice channel.", ephemeral=True
            )
            mark_app_command_failed(interaction)
            return

        if member.id == author.id:
            await interaction.response.send_message(
                ":x: You cannot votekick yourself.", ephemeral=True
            )
            mark_app_command_failed(interaction)
            return

        if member.bot:
            await interaction.response.send_message(
                ":x: You cannot votekick a bot.", ephemeral=True
            )
            mark_app_command_failed(interaction)
            return

        if member.id in self.votekicks:
            await interaction.response.send_message(
                f":x: A votekick for {member.mention} is already in progress.",
                ephemeral=True,
            )
            mark_app_command_failed(interaction)
            return

        member_count = sum(
            1
            for channel_member in voice_channel.members
            if not channel_member.bot and channel_member.id != member.id
        )
        required_votes = (member_count * 2 + 2) // 3

        embed = Embed(
            title=f"Votekick for {member.display_name}",
            description=(
                f":information_source: {author.mention} has started a votekick against "
                f"{member.mention}."
            ),
            color=self.bot.color,
        )
        embed.add_field(name="Required Votes", value=str(required_votes), inline=True)
        embed.add_field(
            name="Votes", value=":heavy_check_mark: Yes: 0\n:x: No: 0", inline=True
        )
        embed.set_footer(text="The vote will end in 60 seconds.")

        view = VotekickView(self.bot, required_votes, author, member)

        await interaction.response.send_message(embed=embed, view=view)
        message = await interaction.original_response()
        view.message = message
        self.votekicks[member.id] = message

        await view.wait()
        self.votekicks.pop(member.id, None)


async def setup(bot: Sakamoto):
    """Add the ModerationCog to the bot."""
    await bot.add_cog(ModerationCog(bot))
