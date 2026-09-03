import logging
from typing import TYPE_CHECKING

from discord import Guild, HTTPException
from discord.ext import commands

if TYPE_CHECKING:
    from main import Sakamoto

logger = logging.getLogger(__name__)


class SyncCog(commands.Cog):
    """Cog for syncing application commands."""

    def __init__(self, bot: Sakamoto):
        self.bot = bot

    async def _sync_scope(self, guild: Guild | None = None) -> str:
        """Sync commands for specific scope and return result message."""
        scope_name = f"guild {guild.id}" if guild else "globally"
        try:
            if count := len(await self.bot.tree.sync(guild=guild)):
                return f"Synced {count} commands {scope_name}."
            return f"No commands synced {scope_name}."
        except HTTPException as e:
            return f"Failed sync {scope_name}: {e.status} {getattr(e, 'text', '')}"
        except Exception as e:
            return f"Error sync {scope_name}: {e}"

    @commands.hybrid_command(
        name="sync",
        description="Sync application commands globally and to current guild (Admin Only).",
    )
    @commands.has_permissions(administrator=True)
    async def sync(self, ctx: commands.Context) -> None:
        """Sync commands globally and guild-specific."""
        if is_slash := ctx.interaction is not None:
            await ctx.defer(ephemeral=True)

        global_msg = await self._sync_scope()

        if ctx.guild:
            guild_msg = await self._sync_scope(ctx.guild)
        else:
            guild_msg = "Skipped guild sync (not in server)."

        final_msg = f"{global_msg}\n{guild_msg}\n\n**Note:** Restart Discord client to see changes."

        if is_slash and ctx.interaction:
            await ctx.interaction.followup.send(final_msg, ephemeral=True)
        else:
            await ctx.send(final_msg)

    @sync.error
    async def on_sync_error(
        self, ctx: commands.Context, error: commands.CommandError
    ) -> None:
        """Handle errors for the sync command."""
        if isinstance(error, commands.MissingPermissions):
            msg = ":x: You need Administrator permissions to run this command."
            if ctx.interaction:
                await ctx.interaction.response.send_message(msg, ephemeral=True)
            else:
                await ctx.send(msg)
        else:
            logger.error("Unexpected error in sync command: %s", error)


async def setup(bot: Sakamoto):
    """Add the SyncCog to the bot."""
    await bot.add_cog(SyncCog(bot))
