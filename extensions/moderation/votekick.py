import logging
from asyncio import sleep
from typing import TYPE_CHECKING, Optional

from discord import (
    ButtonStyle,
    Embed,
    Interaction,
    Member,
    Message,
    PermissionOverwrite,
    app_commands,
)
from discord.ext import commands
from discord.ui import Button, View, button

if TYPE_CHECKING:
    from main import Sakamoto

logger = logging.getLogger(__name__)


class VotekickView(View):
    """Interactive vote controls for a Voice Votekick."""

    def __init__(self, bot: "Sakamoto", required_votes: int, author: Member, target: Member):
        super().__init__(timeout=60.0)
        self.bot = bot
        self.required_votes = required_votes
        self.author = author
        self.target = target
        self.yes_votes: set[int] = set()
        self.no_votes: set[int] = set()
        self.message: Optional[Message] = None

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
            embed.description = f":hourglass: The votekick against {self.target.mention} timed out."
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
            await interaction.response.send_message(":x: You have already voted.", ephemeral=True)
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
                except Exception:  # pylint: disable=broad-exception-caught
                    logger.error("Failed to move %s during votekick.", self.target)

                overwrite = PermissionOverwrite(connect=False)
                await original_channel.set_permissions(self.target, overwrite=overwrite)

                if isinstance(cog := self.bot.get_cog("ModerationCog"), ModerationCog):
                    self.bot.loop.create_task(
                        cog.unban_after_delay(self.target, original_channel, 60)
                    )

    @button(label="No", style=ButtonStyle.red)
    async def no_button(self, interaction: Interaction, _button: Button):
        """Record a negative vote."""
        if self.has_voted(interaction.user.id):
            await interaction.response.send_message(":x: You have already voted.", ephemeral=True)
            return

        self.no_votes.add(interaction.user.id)
        await self.update_embed(interaction)


class ModerationCog(commands.Cog):
    """Cog for moderation commands."""

    def __init__(self, bot: "Sakamoto"):
        self.bot = bot
        self.votekicks: dict[int, Message] = {}

    async def unban_after_delay(self, member: Member, channel, delay: int):
        """Remove a Temporary Rejoin Ban after its delay."""
        await sleep(delay)
        try:
            await channel.set_permissions(member, overwrite=None)
        except Exception as error:  # pylint: disable=broad-exception-caught
            logger.error(
                "Failed to remove votekick ban for %s in channel %s: %s", member, channel.id, error
            )

    @app_commands.command(
        name="votekick", description="Start a vote to kick a user from the current voice channel."
    )
    @app_commands.describe(member="Member to vote to remove from the current voice channel.")
    # Guard clauses keep each rejected Voice Votekick case self-contained.
    # pylint: disable=too-many-return-statements
    async def votekick(self, interaction: Interaction, member: Member):
        """Start a Voice Votekick for a member in the caller's channel."""
        if not interaction.guild:
            await interaction.response.send_message(
                ":x: This command can only be used in a server.", ephemeral=True
            )
            return

        if not isinstance(author := interaction.user, Member):
            await interaction.response.send_message(
                ":x: You must be a member of this server to use this command.", ephemeral=True
            )
            return

        if not (author_voice := author.voice) or not (voice_channel := author_voice.channel):
            await interaction.response.send_message(
                ":x: You must be in a voice channel to start a votekick.", ephemeral=True
            )
            return

        if not (member_voice := member.voice) or member_voice.channel != voice_channel:
            await interaction.response.send_message(
                f":x: {member.mention} is not in your voice channel.", ephemeral=True
            )
            return

        if member.id == author.id:
            await interaction.response.send_message(
                ":x: You cannot votekick yourself.", ephemeral=True
            )
            return

        if member.bot:
            await interaction.response.send_message(
                ":x: You cannot votekick a bot.", ephemeral=True
            )
            return

        if member.id in self.votekicks:
            await interaction.response.send_message(
                f":x: A votekick for {member.mention} is already in progress.", ephemeral=True
            )
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
        embed.add_field(name="Votes", value=":heavy_check_mark: Yes: 0\n:x: No: 0", inline=True)
        embed.set_footer(text="The vote will end in 60 seconds.")

        view = VotekickView(self.bot, required_votes, author, member)

        await interaction.response.send_message(embed=embed, view=view)
        message = await interaction.original_response()
        view.message = message
        self.votekicks[member.id] = message

        await view.wait()
        self.votekicks.pop(member.id, None)


async def setup(bot: "Sakamoto"):
    """Add the ModerationCog to the bot."""
    await bot.add_cog(ModerationCog(bot))
