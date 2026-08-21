import logging
from typing import TYPE_CHECKING

from discord import Interaction, app_commands
from discord.ext import commands

if TYPE_CHECKING:
    from main import Sakamoto

logger = logging.getLogger(__name__)


class CloseCog(commands.Cog):
    """Cog for shutting down the bot gracefully."""

    def __init__(self, bot: "Sakamoto"):
        self.bot = bot

    @app_commands.command(name="shutdown", description="Shuts down the bot gracefully.")
    @app_commands.checks.has_permissions(administrator=True)
    async def shutdown_bot(self, interaction: Interaction) -> None:
        """Shutdown command for administrators."""
        assert (
            interaction.client.user is not None
        ), "interaction.client.user is None in shutdown_bot!"
        await interaction.response.send_message(
            f":wave: Shutting down {interaction.client.user.name}...", ephemeral=True
        )
        try:
            await interaction.client.close()
        except Exception as e:  # pylint: disable=broad-exception-caught
            logger.error("Failed to shut down the bot: %s", e)

    @shutdown_bot.error
    async def on_shutdown_error(
        self, interaction: Interaction, error: app_commands.AppCommandError
    ) -> None:
        """Handle errors for the shutdown command."""
        if isinstance(error, app_commands.errors.MissingPermissions):
            await interaction.response.send_message(
                ":x: You need Administrator permissions to shut down the bot.", ephemeral=True
            )
        else:
            logger.error("Unexpected error in shutdown command: %s", error)


async def setup(bot: "Sakamoto"):
    """Add the CloseCog to the bot."""
    await bot.add_cog(CloseCog(bot))
