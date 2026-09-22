from typing import TYPE_CHECKING

from discord import AllowedMentions, Message
from discord.ext import commands
from discord.utils import escape_markdown

if TYPE_CHECKING:
    from main import Sakamoto


class ReplaceCog(commands.Cog):
    """Cog for replacing social media links with alternative frontends."""

    def __init__(self, bot: Sakamoto):
        sources_by_target = {
            "fixupx.com": ("x.com", "twitter.com"),
            "fxbsky.app": ("bsky.social", "bsky.app"),
            "vm.tnktok.com": ("tiktok.com", "vm.tiktok.com"),
            "instagram7.com": ("instagram.com",),
        }
        self.replacements = tuple(
            (f"{prefix}{source}/", f"https://{target}/")
            for target, sources in sources_by_target.items()
            for source in sources
            for prefix in ("http://", "http://www.", "https://", "https://www.")
        )

    def replace_text(self, text: str) -> str:
        """Rewrite supported URLs in text."""
        for source, target in self.replacements:
            text = text.replace(source, target)
        return text

    @commands.Cog.listener()
    async def on_message(self, message: Message) -> None:
        """Send rewritten text when a non-bot message contains a supported URL."""
        if message.guild is None or message.author.bot or not message.content:
            return
        if "http://" not in message.content and "https://" not in message.content:
            return
        if (fixed := self.replace_text(message.content)) != message.content:
            await message.channel.send(
                escape_markdown(fixed, as_needed=True),
                allowed_mentions=AllowedMentions.none(),
            )


async def setup(bot: Sakamoto):
    """Add the ReplaceCog to the bot."""
    await bot.add_cog(ReplaceCog(bot))
