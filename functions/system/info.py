import time
from os import getpid
from platform import machine, python_version, system
from typing import TYPE_CHECKING

from discord import Embed, Interaction, app_commands
from discord.ext import commands
from psutil import Error, Process

if TYPE_CHECKING:
    from main import Sakamoto


class InfoCog(commands.Cog):
    """Cog that reports runtime information."""

    def __init__(self, bot: "Sakamoto"):
        self.bot = bot

    @app_commands.command(
        name="info",
        description="Show information about the bot, including versions, uptime, and memory usage.",
    )
    async def info(self, interaction: Interaction):
        """Send the bot information embed."""
        embed = await self.create_embed()
        await interaction.response.send_message(embed=embed)

    async def create_embed(self):
        """Build the bot information embed."""
        embed_data = {
            "title": ":information_source: Bot's Info",
            "description": "Here's some information about me and my dependencies!",
            "color": self.bot.color,
            "fields": [
                {
                    "name": "Bot version",
                    "value": f"**Python**: {python_version()}\n**Sakamoto**: v3.0.0",
                    "inline": True,
                },
                {"name": "OS", "value": f"**{system()}**: {machine()}", "inline": True},
                {"name": "Uptime", "value": await self.uptime(), "inline": True},
                {
                    "name": "Memory",
                    "value": await self._get_mem_usage(),
                    "inline": True,
                },
            ],
        }
        return Embed.from_dict(embed_data)

    @staticmethod
    async def _get_mem_usage():
        """Return RSS for the bot and its live child processes."""
        process = Process(getpid())
        bot_rss = process.memory_info().rss
        child_rss = 0
        child_count = 0
        for child in process.children(recursive=True):
            try:
                child_rss += child.memory_info().rss
                child_count += 1
            except Error:
                continue

        mib = 1024**2
        return (
            f"Total RSS: {(bot_rss + child_rss) / mib:.2f} MiB\n"
            f"Bot: {bot_rss / mib:.2f} MiB\n"
            f"Children ({child_count}): {child_rss / mib:.2f} MiB"
        )

    async def uptime(self):
        """Return the current process uptime."""
        start_time_timestamp = Process(getpid()).create_time()
        current_time_timestamp = time.time()
        uptime_seconds = int(current_time_timestamp - start_time_timestamp)
        uptime_hours = uptime_seconds // 3600
        uptime_minutes = (uptime_seconds % 3600) // 60
        return f"{uptime_hours} hours, {uptime_minutes} minutes"


async def setup(bot: "Sakamoto"):
    """Add the InfoCog to the bot."""
    await bot.add_cog(InfoCog(bot))
