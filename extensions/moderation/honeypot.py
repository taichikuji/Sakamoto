"""Catch spam sent to a bait channel and remove the sender's recent messages.

Each server can configure a honeypot and an optional staff log channel. A member
who posts in the honeypot is banned with a request to delete their last hour of
server messages, then immediately unbanned so they can rejoin.
"""

import logging
from os import makedirs, path
from typing import TYPE_CHECKING

from aiosqlite import connect
from discord import (
    AllowedMentions,
    Guild,
    HTTPException,
    Interaction,
    Member,
    Message,
    MessageType,
    NotFound,
    TextChannel,
    app_commands,
)
from discord.ext import commands

from extensions.core.analytics import mark_app_command_failed

if TYPE_CHECKING:
    from main import Sakamoto

logger = logging.getLogger(__name__)


@app_commands.default_permissions(administrator=True)
class HoneypotCog(
    commands.GroupCog,
    group_name="honeypot",
    group_description="Configure a channel that catches server-wide spam.",
):
    """Persist channel settings and moderate messages sent to the honeypot."""

    def __init__(self, bot: Sakamoto):
        self.bot = bot
        # SQLite persists settings; these maps avoid a database read per message.
        self.channels: dict[int, int] = {}
        self.log_channels: dict[int, int] = {}

    async def cog_load(self) -> None:
        makedirs(path.dirname(self.bot.db_path), exist_ok=True)
        async with connect(self.bot.db_path, isolation_level=None) as db:
            await db.execute("""
                CREATE TABLE IF NOT EXISTS honeypot_channels (
                    guild_id INTEGER PRIMARY KEY,
                    channel_id INTEGER NOT NULL
                )
            """)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS honeypot_log_channels (
                    guild_id INTEGER PRIMARY KEY,
                    channel_id INTEGER NOT NULL
                )
            """)
            async with db.execute(
                "SELECT guild_id, channel_id FROM honeypot_channels"
            ) as cursor:
                self.channels = {
                    guild_id: channel_id async for guild_id, channel_id in cursor
                }
            async with db.execute(
                "SELECT guild_id, channel_id FROM honeypot_log_channels"
            ) as cursor:
                self.log_channels = {
                    guild_id: channel_id async for guild_id, channel_id in cursor
                }

    log = app_commands.Group(name="log", description="Configure honeypot logs.")

    @app_commands.command(
        name="set", description="Set or clear the spam honeypot channel."
    )
    @app_commands.describe(channel="Channel to use. Leave empty to clear the honeypot.")
    @app_commands.guild_only()
    @app_commands.checks.has_permissions(administrator=True)
    async def set_honeypot(
        self, interaction: Interaction, channel: TextChannel | None = None
    ) -> None:
        if (guild := interaction.guild) is None:
            return
        if channel is not None:
            if channel.id == self.log_channels.get(guild.id):
                await interaction.response.send_message(
                    ":x: The honeypot and log channels must be different.",
                    ephemeral=True,
                )
                mark_app_command_failed(interaction)
                return
            me = guild.me
            if me is None or not me.guild_permissions.ban_members:
                await interaction.response.send_message(
                    ":x: Sakamoto needs Ban Members permission to run the honeypot.",
                    ephemeral=True,
                )
                mark_app_command_failed(interaction)
                return
            if not channel.permissions_for(me).view_channel:
                await interaction.response.send_message(
                    ":x: Sakamoto needs to see the honeypot channel.", ephemeral=True
                )
                mark_app_command_failed(interaction)
                return
            everyone = channel.permissions_for(guild.default_role)
            # Scam posts may consist only of an image, so attachments must work too.
            if not everyone.send_messages or not everyone.attach_files:
                await interaction.response.send_message(
                    ":x: Everyone needs Send Messages and Attach Files in the honeypot channel.",
                    ephemeral=True,
                )
                mark_app_command_failed(interaction)
                return

        async with connect(self.bot.db_path, isolation_level=None) as db:
            if channel is None:
                await db.execute(
                    "DELETE FROM honeypot_channels WHERE guild_id = ?", (guild.id,)
                )
                response = ":white_check_mark: Honeypot cleared."
            else:
                await db.execute(
                    "INSERT OR REPLACE INTO honeypot_channels (guild_id, channel_id) VALUES (?, ?)",
                    (guild.id, channel.id),
                )
                response = (
                    f":white_check_mark: {channel.mention} is the honeypot. "
                    "Non-admin messages there trigger a softban with a request to delete the author's last hour of server messages."
                )
        if channel is None:
            self.channels.pop(guild.id, None)
        else:
            self.channels[guild.id] = channel.id
        await interaction.response.send_message(response, ephemeral=True)

    @log.command(name="set", description="Set or clear the honeypot log channel.")
    @app_commands.describe(channel="Staff log channel. Leave empty to clear it.")
    @app_commands.guild_only()
    @app_commands.checks.has_permissions(administrator=True)
    async def set_log_channel(
        self, interaction: Interaction, channel: TextChannel | None = None
    ) -> None:
        if (guild := interaction.guild) is None:
            return
        if channel is not None:
            if channel.id == self.channels.get(guild.id):
                await interaction.response.send_message(
                    ":x: The honeypot and log channels must be different.",
                    ephemeral=True,
                )
                mark_app_command_failed(interaction)
                return
            me = guild.me
            permissions = channel.permissions_for(me) if me is not None else None
            if permissions is None or not (
                permissions.view_channel and permissions.send_messages
            ):
                await interaction.response.send_message(
                    ":x: Sakamoto needs View Channel and Send Messages in the log channel.",
                    ephemeral=True,
                )
                mark_app_command_failed(interaction)
                return

        async with connect(self.bot.db_path, isolation_level=None) as db:
            if channel is None:
                await db.execute(
                    "DELETE FROM honeypot_log_channels WHERE guild_id = ?", (guild.id,)
                )
                response = ":white_check_mark: Honeypot logging cleared."
            else:
                await db.execute(
                    "INSERT OR REPLACE INTO honeypot_log_channels (guild_id, channel_id) VALUES (?, ?)",
                    (guild.id, channel.id),
                )
                response = f":white_check_mark: Honeypot events will be logged in {channel.mention}."
        if channel is None:
            self.log_channels.pop(guild.id, None)
        else:
            self.log_channels[guild.id] = channel.id
        await interaction.response.send_message(response, ephemeral=True)

    async def _send_log(self, guild: Guild, member: Member, outcome: str) -> None:
        if (channel_id := self.log_channels.get(guild.id)) is None:
            return
        if (channel := guild.get_channel(channel_id)) is None:
            logger.warning("Honeypot log channel %s is unavailable", channel_id)
            return
        try:
            await channel.send(
                f"🍯 Honeypot: {member.mention} (`{member.id}`) — {outcome}",
                # Show the account to staff without notifying it or any roles.
                allowed_mentions=AllowedMentions.none(),
            )
        except HTTPException:
            logger.exception("Could not post honeypot event in channel %s", channel_id)

    @commands.Cog.listener()
    async def on_message(self, message: Message) -> None:
        guild = message.guild
        member = message.author
        # System events and trusted accounts must never trigger moderation.
        if (
            guild is None
            or self.channels.get(guild.id) != message.channel.id
            or message.type not in (MessageType.default, MessageType.reply)
            or not isinstance(member, Member)
            or member.bot
            or member.guild_permissions.administrator
            or member.id == guild.owner_id
        ):
            return

        outcome = "Softban complete. Discord was asked to remove the member's last hour of messages."
        try:
            # Discord removes recent guild messages during the ban; the immediate
            # unban makes this a softban rather than a permanent removal.
            await guild.ban(
                member,
                delete_message_seconds=3600,
                reason=f"Posted in spam honeypot channel {message.channel.id}",
            )
        except HTTPException:
            logger.exception("Could not ban member %s in guild %s", member.id, guild.id)
            outcome = "Ban failed; recent messages may still be visible."
        else:
            try:
                await guild.unban(
                    member,
                    reason=f"Completed honeypot softban in channel {message.channel.id}",
                )
            except NotFound:
                pass  # Already unbanned.
            except HTTPException:
                logger.exception(
                    "Could not finish softban for member %s in guild %s; manual unban needed",
                    member.id,
                    guild.id,
                )
                outcome = "Ban succeeded, but unban failed. A moderator must unban this member."
        # Delete the bait post separately: ban cleanup may not remove it, and
        # this still works when the ban fails.
        try:
            await message.delete()
        except NotFound:
            pass  # The ban already removed it.
        except HTTPException:
            logger.exception("Could not delete honeypot message %s", message.id)
            outcome += " The triggering message could not be deleted."
        await self._send_log(guild, member, outcome)


async def setup(bot: Sakamoto) -> None:
    await bot.add_cog(HoneypotCog(bot))
