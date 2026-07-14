import logging
from os import environ, makedirs, path
from re import IGNORECASE, fullmatch, match
from typing import TYPE_CHECKING

from aiohttp import ClientError, ContentTypeError
from aiosqlite import connect
from discord import Embed, Interaction, app_commands
from discord.ext import commands

if TYPE_CHECKING:
    from main import Sakamoto

logger = logging.getLogger(__name__)

try:
    STEAM_TOKEN = environ.get("STEAM_TOKEN")
except Exception as e:
    logger.error("Failed to load STEAM_TOKEN from environment. %s", e)
    STEAM_TOKEN = None

class SteamCog(commands.GroupCog, group_name="steam", group_description="Steam account linking and lobby tools."):
    """Cog for Steam integration, linking accounts and fetching lobby information."""
    def __init__(self, bot: "Sakamoto"):
        self.bot = bot
        self.steam_api_base = "https://api.steampowered.com"

    async def cog_load(self):
        await self._init_db()

    async def _init_db(self):
        makedirs(path.dirname(self.bot.db_path), exist_ok=True)
        async with connect(self.bot.db_path) as db:
            await db.execute("""
                CREATE TABLE IF NOT EXISTS steam_links (
                    discord_id INTEGER PRIMARY KEY,
                    steam_id TEXT NOT NULL
                )
            """)
            await db.commit()

    async def _save_steam_link(self, discord_id: int, steam_id: str):
        async with connect(self.bot.db_path) as db:
            await db.execute(
                "INSERT OR REPLACE INTO steam_links (discord_id, steam_id) VALUES (?, ?)",
                (discord_id, steam_id)
            )
            await db.commit()

    async def _get_steam_link(self, discord_id: int) -> str | None:
        async with connect(self.bot.db_path) as db:
            async with db.execute("SELECT steam_id FROM steam_links WHERE discord_id = ?", (discord_id,)) as cursor:
                if row := await cursor.fetchone():
                    return row[0]
                return None

    def _steam_http_error_message(self, status: int) -> str:
        if status == 400:
            return ":x: Steam rejected the request (400 Bad Request). Please double-check the input and try again."
        if status in {401, 403}:
            return ":x: Steam denied access to the API key (401/403). The bot owner needs to verify `STEAM_TOKEN`."
        if status == 404:
            return ":x: Steam API endpoint was not found (404). This command may need a bot update."
        if status == 405:
            return ":x: Steam rejected the HTTP method (405). This command may need a bot update."
        if status == 429:
            return ":x: Steam rate-limited this request (429). Please wait a moment and try again."
        if status == 500:
            return ":x: Steam had an internal server error (500). Please try again shortly."
        if status == 503:
            return ":x: Steam service is temporarily unavailable (503). Please try again later."
        return f":x: Steam API returned HTTP `{status}`. Please try again later."

    def _steam_id_help_message(self, steam_identifier: str, error: str | None = None) -> str:
        reason = f"Reason: {error.removeprefix(':x:').strip()}\n" if error else ""
        return (
            f":x: Could not resolve `{steam_identifier}` to a valid SteamID64.\n"
            f"{reason}"
            "Use one of these formats:\n"
            "- 17-digit SteamID64 (example: `76561198000000000`)\n"
            "- Full Steam profile URL (`https://steamcommunity.com/id/<vanity>` or `/profiles/<steamid64>`)\n"
            "- Vanity name only (example: `gaben`)"
        )

    async def _resolve_steam_id(self, vanity_url_or_id: str) -> tuple[str | None, str | None]:
        if not self.bot.session:
            logger.error("Bot aiohttp session is not initialized.")
            return None, ":x: The bot's HTTP session is not ready. Please try again later."
        if not STEAM_TOKEN:
            logger.error("STEAM_TOKEN is not set. Cannot resolve Steam ID.")
            return None, ":x: The bot's Steam API key is not configured. Please contact the bot owner."
        
        vanity_name = vanity_url_or_id
        # regex to extract vanity name from Steam URL
        if profile_match := match(r"(?:https?://)?steamcommunity\.com/(?:id/([^/]+)|profiles/(\d{17}))/?", vanity_url_or_id, IGNORECASE):
            # matches id url
            if id_part := profile_match.group(1):
                vanity_name = id_part
            # matches profiles url
            elif profiles_part := profile_match.group(2):
                return profiles_part, None
            
        # confirm if input is indeed SteamID64 after extraction
        if fullmatch(r"\d{17}", vanity_name):
            return vanity_name, None
        
        try:
            # API call to resolve vanity URL to SteamID64
            async with self.bot.session.get(
                f"{self.steam_api_base}/ISteamUser/ResolveVanityURL/v1/",
                params={"key": STEAM_TOKEN, "vanityurl": vanity_name, "url_type": "1"} # url_type 1 for individual profile
            ) as resp:
                if resp.status != 200:
                    logger.error("Steam API error (ResolveVanityURL) - Status %s", resp.status)
                    return None, self._steam_http_error_message(resp.status)

                data = await resp.json()
                response = data.get("response", {})
                if response.get("success") == 1 and response.get("steamid"):
                    return response["steamid"], None

                success_code = response.get("success")
                api_message = response.get("message")
                logger.info(
                    "Could not resolve vanity '%s'. success=%s message=%s",
                    vanity_name,
                    success_code,
                    api_message,
                )

                if success_code == 42:
                    return None, "Steam could not find that vanity profile name (success code 42)."
                if api_message:
                    return None, f"Steam returned: `{api_message}`."
                if success_code is not None:
                    return None, f"Steam could not resolve this profile (success code `{success_code}`)."
                return None, "Steam returned an unexpected response while resolving this profile."
        except ContentTypeError:
            logger.error("ResolveVanityURL returned non-JSON content for '%s'.", vanity_name)
            return None, ":x: Steam returned an unexpected response format. Please try again later."
        except ClientError as e:
            logger.error("Network error during Steam ID resolution for '%s': %s", vanity_name, e)
            return None, ":x: Network error while contacting Steam. Please try again in a moment."
        except Exception as e:
            logger.error("Exception during Steam ID resolution for '%s': %s", vanity_name, e)
            return None, ":x: Unexpected error while resolving your Steam account. Please try again later."

    @app_commands.command(name="link", description="Link your Discord account to a Steam ID or vanity URL.")
    async def link_steam(self, interaction: Interaction, steam_identifier: str):
        await interaction.response.defer(ephemeral=True)
        
        if not STEAM_TOKEN:
            await interaction.followup.send(
                ":x: The bot's Steam API key is not configured. Linking is currently unavailable. Please contact the bot owner."
            )
            return
        if not self.bot.session:
            await interaction.followup.send(
                ":x: The bot's HTTP session is not ready. Please try again later."
            )
            return
        
        steam_id, resolve_error = await self._resolve_steam_id(steam_identifier.strip())
        if not steam_id:
            await interaction.followup.send(self._steam_id_help_message(steam_identifier, resolve_error))
            return
        
        await self._save_steam_link(interaction.user.id, steam_id)
        await interaction.followup.send(
            f":white_check_mark: Your Discord account has been successfully linked to SteamID: `{steam_id}`."
        )

    @app_commands.command(
            name="lobby",
            description="Get a Steam lobby invite link if you're in a joinable game."
        )
    async def get_lobby(self, interaction: Interaction):
        await interaction.response.defer()

        if not STEAM_TOKEN:
                await interaction.followup.send(
                    ":x: The bot's Steam API key is not configured. Lobby fetching is unavailable. Please contact the bot owner."
                )
                return
        if not self.bot.session:
                await interaction.followup.send(
                    ":x: The bot's HTTP session is not ready. Please try again later."
                )
                return
        
        if not (linked_steam_id := await self._get_steam_link(interaction.user.id)):
            await interaction.followup.send(
                ":information_source: Your Steam account is not linked. "
                "Please use the `/steam link <your_steam_id_or_vanity_name>` command first."
            )
            return
        
        try:
            async with self.bot.session.get(
                f"{self.steam_api_base}/ISteamUser/GetPlayerSummaries/v2/",
                params={"key": STEAM_TOKEN, "steamids": linked_steam_id}
            ) as resp:
                if resp.status != 200:
                    logger.error("Steam API error (GetPlayerSummaries) - Status %s", resp.status)
                    await interaction.followup.send(self._steam_http_error_message(resp.status))
                    return
                data = await resp.json()
            
            if not (players := data.get("response", {}).get("players", [])):
                await interaction.followup.send(
                    ":x: Could not retrieve your player summary from Steam. "
                    "Ensure your Steam profile is public and you've linked the correct ID."
                )
                return
            
            player_info = players[0]
            game_name = player_info.get("gameextrainfo", "Unknown Game")

            if not (app_id := player_info.get("gameid")):
                await interaction.followup.send(
                    ":x: You are not currently in a joinable game. "
                    "Please start a game and try again."
                )
                return
            
            if not (lobby_id := player_info.get("lobbysteamid")) or lobby_id == "0":
                await interaction.followup.send(
                    f":information_source: You are currently playing **{game_name}** (AppID: `{app_id}`), "
                    "but you don't seem to be in a joinable lobby, or your lobby details are private."
                )
                return
            
            lobby_url = f"https://taichikuji.github.io/redirector/?url=steam://joinlobby/{app_id}/{lobby_id}/{linked_steam_id}"

            embed = Embed(
                title=f"Steam Lobby Invite for {interaction.user.display_name}",
                description=f"Join {interaction.user.mention}'s lobby for **{game_name}**!",
                color=self.bot.color
            )
            embed.add_field(name="Lobby Link", value=f"<{lobby_url}>", inline=False)
            embed.set_footer(text="Clicking this link requires Steam to be installed and running.")
            if interaction.user.display_avatar:
                embed.set_thumbnail(url=interaction.user.display_avatar.url)
            
            await interaction.followup.send(embed=embed)

        except ContentTypeError:
            logger.error(
                "GetPlayerSummaries returned non-JSON content for user %s (SteamID: %s).",
                interaction.user.id,
                linked_steam_id,
            )
            await interaction.followup.send(
                ":x: Steam returned an unexpected response format while fetching your lobby details."
            )
        except ClientError as e:
            logger.error("Network error in get_lobby for user %s (SteamID: %s): %s", interaction.user.id, linked_steam_id, e)
            await interaction.followup.send(
                ":x: Network error while contacting Steam. Please try again in a moment."
            )
        except Exception as e:
            logger.error("get_lobby command failed for user %s (SteamID: %s): %s", interaction.user.id, linked_steam_id, e)
            await interaction.followup.send(
                ":x: An unexpected error occurred while trying to fetch your lobby information."
            )

async def setup(bot: "Sakamoto"):
    if not STEAM_TOKEN:
        raise commands.ExtensionFailed(name="functions.tool.steam", original=RuntimeError("STEAM_TOKEN environment variable is not set."))
    await bot.add_cog(SteamCog(bot))