import logging
from glob import iglob
from os import environ, sep

from aiohttp import ClientSession
from discord import Activity, ActivityType, Intents
from discord.ext import commands
from discord.utils import setup_logging

setup_logging()
logger = logging.getLogger("Sakamoto")

if (TOKEN := environ.get("TOKEN")) is None:
    raise OSError("TOKEN environment variable not set")


class Sakamoto(commands.AutoShardedBot):
    def __init__(self):
        intents = Intents.default()
        intents.message_content = True
        super().__init__(
            description="You thought all I say is meow?",
            command_prefix=commands.when_mentioned,
            case_insensitive=True,
            intents=intents,
        )
        self._bot_token = TOKEN
        self.session: ClientSession | None = None
        self.color = 0xFF3351
        self.db_path = "data/sakamoto.sqlite"

    async def setup_hook(self):
        self.session = ClientSession()
        for functions in iglob("functions/**/*.py", recursive=True):
            filename = functions.split(sep)[-1]
            if filename.startswith("_"):
                continue
            module = functions.replace(".py", "").replace(sep, ".")
            try:
                await self.load_extension(module)
                logger.info("Loaded %s", module)
            except ImportError:
                logger.error("Extension import failure! [%s]", module)
            except commands.errors.ExtensionFailed:
                logger.error("Extension failed! [%s]", module)
            except Exception:
                logger.error("Unexpected exception! [%s]", module)

    async def on_ready(self):
        assert self.user is not None, "self.user is None in on_ready!"
        display = Activity(
            name="Use /help to view all commands!", type=ActivityType.listening
        )
        await self.change_presence(activity=display)
        logger.info("I am online! - %s %s", self.user.name, self.user.id)

    async def close(self):
        try:
            if self.session is not None:
                await self.session.close()
            await super().close()
            logger.info("Session closed!")
        except Exception as e:
            logger.error("Failed to close aiohttp session - %s", e)
            raise

    def run(self, *args, **kwargs):
        try:
            super().run(str(self._bot_token), reconnect=True, log_handler=None, *args, **kwargs)
        except TypeError:
            logger.error("An unexpected keyword argument was passed!")
        except Exception as e:
            logger.error("An exception occurred: %s", e)


if __name__ == "__main__":
    logger.info("Starting Sakamoto...")
    Sakamoto().run()
