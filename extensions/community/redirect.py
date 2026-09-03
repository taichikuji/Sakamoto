from typing import TYPE_CHECKING

from discord import Message
from discord.ext import commands

if TYPE_CHECKING:
    from main import Sakamoto


class ReplaceCog(commands.Cog):
    """Cog for replacing social media links with alternative frontends."""

    def __init__(self, bot: "Sakamoto"):
        self.bot = bot
        self.replacements = {
            "x.com": "fixupx.com",
            "twitter.com": "fixupx.com",
            "bsky.social": "fxbsky.app",
            "bsky.app": "fxbsky.app",
            "tiktok.com": "vm.tnktok.com",
            "vm.tiktok.com": "vm.tnktok.com",
            "instagram.com": "instagram7.com",
            "pixiv.net": "phixiv.net",
            "youtube.com/shorts": "youtu.be",
            "reddit.com": "vxreddit.com",
            "facebook.com": "facebed.seria.moe",
            "bilibili.com": "vxbilibili.com",
            "open.spotify.com": "fxspotify.com",
        }

    def replace_text(self, text: str) -> str:
        """Rewrite supported URLs in text."""
        for source, target in self.replacements.items():
            for prefix in ("http://", "http://www.", "https://", "https://www."):
                text = text.replace(f"{prefix}{source}/", f"https://{target}/")
        return text

    @commands.Cog.listener()
    async def on_message(self, message: Message) -> None:
        """Send rewritten text when a non-bot message contains a supported URL."""
        if message.author.bot or not message.content:
            return
        if "http://" not in message.content and "https://" not in message.content:
            return
        if (fixed := self.replace_text(message.content)) != message.content:
            await message.channel.send(fixed)


async def setup(bot: "Sakamoto"):
    """Add the ReplaceCog to the bot."""
    await bot.add_cog(ReplaceCog(bot))
