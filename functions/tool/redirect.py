from re import Pattern, compile
from typing import TYPE_CHECKING

from discord import Message
from discord.ext import commands

if TYPE_CHECKING:
    from main import Sakamoto


class ReplaceCog(commands.Cog):
    """Cog for replacing social media links with alternative frontends."""
    def __init__(self, bot: "Sakamoto"):
        self.bot = bot
        self.patterns: list[tuple[Pattern[str], str]] = [
            (compile(r'https?://(?:www\.)?(?:x|twitter)\.com/(?P<user>[^/\s]+)/status/(?P<id>\d+)(?:\?[^ \s]*)?'),
             r'https://fixupx.com/\g<user>/status/\g<id>'),
            (compile(r'https?://(?:www\.)?(?:bsky\.social|bsky\.app)/(?P<rest>\S+)'),
             r'https://fxbsky.app/\g<rest>'),
            (compile(r'https?://(?:www\.|vm\.)?tiktok\.com/(?P<rest>\S+)'),
             r'https://vm.tnktok.com/\g<rest>'),
            (compile(r'https?://(?:www\.)?instagram\.com/(?P<rest>\S+)'),
             r'https://kkinstagram.com/\g<rest>'),
            (compile(r'https?://(?:www\.)?pixiv\.net/(?P<rest>\S+)'),
             r'https://phixiv.net/\g<rest>'),
            (compile(r'https?://(?:www\.)?youtube\.com/shorts/(?P<rest>\S+)'),
             r'https://youtu.be/\g<rest>'),
            (compile(r'https?://(?:www\.)?reddit\.com/(?P<rest>\S+)'),
             r'https://vxreddit.com/\g<rest>'),
        ]


    def replace_text(self, text: str) -> str:
        for pattern, repl in self.patterns:
            text = pattern.sub(repl, text)
        return text


    @commands.Cog.listener()
    async def on_message(self, message: Message) -> None:
        if message.author.bot or not message.content:
            return
        if 'http://' not in message.content and 'https://' not in message.content:
            return
        if (fixed := self.replace_text(message.content)) != message.content:
            await message.channel.send(fixed)


async def setup(bot: "Sakamoto"):
    await bot.add_cog(ReplaceCog(bot))
