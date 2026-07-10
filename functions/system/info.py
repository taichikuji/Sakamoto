import time
from os import getpid
from platform import machine, python_version, system
from typing import TYPE_CHECKING

from discord import Embed, Interaction, app_commands
from discord.ext import commands
from psutil import Process

if TYPE_CHECKING:
    from main import Sakamoto


class InfoCog(commands.Cog):
    def __init__(self, bot: "Sakamoto"):
        self.bot = bot

    @app_commands.command(
        name="info",
        description="Show information about the bot, including versions, uptime, and memory usage.",
    )
    async def info(self, interaction: Interaction):
        embed = await self.create_embed()
        await interaction.response.send_message(embed=embed)

    async def create_embed(self):
        em = {
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
        return Embed.from_dict(em)

    @staticmethod
    async def _get_mem_usage():
        process = Process(getpid())
        mem_usage = float(process.memory_info().rss) / 1000000
        child_count = len(process.children(recursive=True))
        return f"{round(mem_usage, 2)} MB\nChild processes: {child_count}"

    async def uptime(self):
        start_time_timestamp = Process(getpid()).create_time()
        current_time_timestamp = time.time()
        uptime_seconds = int(current_time_timestamp - start_time_timestamp)
        uptime_hours = uptime_seconds // 3600
        uptime_minutes = (uptime_seconds % 3600) // 60
        return f"{uptime_hours} hours, {uptime_minutes} minutes"


async def setup(bot: "Sakamoto"):
    await bot.add_cog(InfoCog(bot))
