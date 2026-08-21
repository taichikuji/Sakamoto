import logging
from os import environ
from pathlib import Path

from aiohttp import ClientSession
from discord import Activity, ActivityType, Intents
from discord.ext import commands
from discord.utils import setup_logging

setup_logging()
logger = logging.getLogger("Sakamoto")

if not (TOKEN := environ.get("TOKEN")):
    raise OSError("TOKEN environment variable not set")


class Sakamoto(commands.AutoShardedBot):
    """Discord bot with shared application resources."""

    def __init__(self):
        intents = Intents.default()
        intents.message_content = True
        super().__init__(
            description="You thought all I say is meow?",
            command_prefix=commands.when_mentioned,
            case_insensitive=True,
            intents=intents,
        )
        self.session: ClientSession | None = None
        self.color = 0xFF3351
        self.db_path = "data/sakamoto.sqlite"

    async def setup_hook(self):
        self.session = ClientSession()

        for ext in Path("functions").rglob("*.py"):
            if ext.name.startswith("_"):
                continue
            module = ".".join(ext.with_suffix("").parts)
            try:
                await self.load_extension(module)
                logger.info("Loaded %s", module)
            except Exception as e:  # pylint: disable=broad-exception-caught
                # Catches ExtensionFailed (like in steam.py), ImportErrors, etc.
                logger.error("Failed to load %s: %s", module, e)

    async def on_ready(self):
        """Set the bot presence after connecting to Discord."""
        assert self.user is not None, "self.user is None in on_ready!"
        display = Activity(name="Use /help to view all commands!", type=ActivityType.listening)
        await self.change_presence(activity=display)
        logger.info("I am online! - %s %s", self.user.name, self.user.id)

    async def close(self):
        if self.session:
            await self.session.close()
        await super().close()
        logger.info("Session closed!")


if __name__ == "__main__":
    logger.info("Starting Sakamoto...")
    Sakamoto().run(TOKEN, reconnect=True, log_handler=None)
