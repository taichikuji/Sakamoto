import logging
from datetime import timedelta
from typing import TYPE_CHECKING

from discord import Interaction, Member, Message, TextChannel, Thread, app_commands
from discord.ext import commands
from discord.utils import time_snowflake, utcnow

from extensions.core.analytics import mark_app_command_failed

if TYPE_CHECKING:
    from main import Sakamoto

logger = logging.getLogger(__name__)


async def _delete_messages(
    channel: TextChannel | Thread, messages: list[Message]
) -> None:
    bulk_delete_after = time_snowflake(utcnow() - timedelta(days=14))
    recent = [message for message in messages if message.id >= bulk_delete_after]
    if recent:
        await channel.delete_messages(recent)
    for message in messages:
        if message.id < bulk_delete_after:
            await message.delete()


class ClearCog(commands.Cog):
    """Cog for bulk message removal in text channels."""

    def __init__(self, bot: Sakamoto):
        self.bot = bot

    @app_commands.command(
        name="clear", description="Remove messages in bulk. Defaults to 1 message."
    )
    @app_commands.describe(
        amount="Number of messages to remove (1–100). Defaults to 1.",
        user="Only remove messages authored by this member.",
    )
    @app_commands.default_permissions(manage_messages=True)
    @app_commands.checks.has_permissions(manage_messages=True)
    async def clear(
        self,
        interaction: Interaction,
        amount: app_commands.Range[int, 1, 100] = 1,
        user: Member | None = None,
    ) -> None:
        """Bulk delete messages, optionally filtering by user."""
        await interaction.response.defer(ephemeral=True)

        if isinstance(channel := interaction.channel, (TextChannel, Thread)):
            if user:
                deleted = []
                async for message in channel.history(limit=None):
                    if message.author == user and message.type.is_deletable():
                        deleted.append(message)
                        if len(deleted) == amount:
                            break
                await _delete_messages(channel, deleted)
                msg = f":wastebasket: Deleted {len(deleted)} messages from {user.display_name}."
            else:
                deleted = await channel.purge(limit=amount)
                msg = f":wastebasket: Deleted {len(deleted)} messages."
            await interaction.followup.send(msg, ephemeral=True)
        else:
            await interaction.followup.send(
                ":x: This command can only be used in text channels.", ephemeral=True
            )
            mark_app_command_failed(interaction)
            return

    @clear.error
    async def clear_error(
        self, interaction: Interaction, error: app_commands.AppCommandError
    ) -> None:
        """Handle errors for the clear command."""
        if isinstance(error, app_commands.MissingPermissions):
            await interaction.response.send_message(
                f":x: You don't have permission to use this command, {interaction.user.mention}.",
                ephemeral=True,
            )
        else:
            logger.error("An unexpected error occurred: %s", error)


async def setup(bot: Sakamoto):
    """Add the ClearCog to the bot."""
    await bot.add_cog(ClearCog(bot))
