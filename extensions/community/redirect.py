from typing import TYPE_CHECKING

from discord import Message
from discord.ext import commands

if TYPE_CHECKING:
    from main import Sakamoto


class ReplaceCog(commands.Cog):
    """Cog for replacing social media links with alternative frontends."""

    def __init__(self, bot: Sakamoto):
        self.bot = bot
        self.replacements = {
            "fixupx.com": ("x.com", "twitter.com"),
            "fxbsky.app": ("bsky.social", "bsky.app"),
            "vm.tnktok.com": ("tiktok.com", "vm.tiktok.com"),
            "instagram7.com": ("instagram.com",),
        }

    def replace_text(self, text: str) -> str:
        """Rewrite supported URLs in text."""
        for target, sources in self.replacements.items():
            for source in sources:
                for prefix in ("http://", "http://www.", "https://", "https://www."):
                    text = text.replace(f"{prefix}{source}/", f"https://{target}/")
        return text

    @commands.Cog.listener()
    async def on_message(self, message: Message) -> None:
        """Send rewritten text when a non-bot message contains a supported URL."""
        if message.guild is None or message.author.bot or not message.content:
            return
        if "http://" not in message.content and "https://" not in message.content:
            return
        if (fixed := self.replace_text(message.content)) != message.content:
            await message.channel.send(fixed)


async def setup(bot: Sakamoto):
    """Add the ReplaceCog to the bot."""
    await bot.add_cog(ReplaceCog(bot))
