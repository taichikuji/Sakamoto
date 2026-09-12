import logging
from datetime import UTC, date, datetime, time, timedelta
from os import makedirs, path
from typing import TYPE_CHECKING

from aiosqlite import Connection, connect
from discord import Embed, Interaction, app_commands
from discord.ext import commands, tasks

if TYPE_CHECKING:
    from main import Sakamoto

logger = logging.getLogger(__name__)

RETENTION_DAYS = 90
DEFAULT_REPORT_DAYS = 30

CREATE_TABLE_SQL = """
    CREATE TABLE IF NOT EXISTS command_analytics (
        day TEXT NOT NULL,
        command_name TEXT NOT NULL,
        success_count INTEGER NOT NULL DEFAULT 0,
        failure_count INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (day, command_name)
    )
"""
UPSERT_SQL = """
    INSERT INTO command_analytics (
        day, command_name, success_count, failure_count
    ) VALUES (?, ?, ?, ?)
    ON CONFLICT (day, command_name) DO UPDATE SET
        success_count = success_count + excluded.success_count,
        failure_count = failure_count + excluded.failure_count
"""
DELETE_EXPIRED_SQL = "DELETE FROM command_analytics WHERE day < ?"


def utc_today() -> date:
    return datetime.now(UTC).date()


def mark_app_command_failed(interaction: Interaction) -> None:
    """Record a handled command failure and suppress its completion event."""
    # Handled errors never reach CommandTree.on_error. Set discord.py's flag to
    # suppress its completion event, then dispatch the same aggregate failure path.
    if getattr(interaction, "command_failed", False):
        return
    interaction.command_failed = True
    if (command := getattr(interaction, "command", None)) is not None:
        interaction.client.dispatch("app_command_failure", interaction, command)


async def is_application_owner(interaction: Interaction) -> bool:
    """Allow only the owner of the Discord application."""
    return await interaction.client.is_owner(interaction.user)  # type: ignore[attr-defined]


def _failure_rate(successes: int, failures: int) -> float:
    attempts = successes + failures
    return failures / attempts if attempts else 0.0


def _format_command_row(row: tuple[str, int, int]) -> str:
    name, successes, failures = row
    attempts = successes + failures
    return (
        f"`/{name}` — {attempts} uses; {failures}/{successes} fail/success "
        f"({_failure_rate(successes, failures):.1%} failed)"
    )


def _limited_lines(lines: list[str], *, empty: str, limit: int) -> str:
    if not lines:
        return empty
    visible = lines[:limit]
    if remaining := len(lines) - len(visible):
        visible.append(f"*…and {remaining} more.*")
    return "\n".join(visible)


class AnalyticsCog(commands.Cog):
    """Store privacy-conscious aggregate command usage."""

    def __init__(self, bot: Sakamoto):
        self.bot = bot

    async def cog_load(self) -> None:
        makedirs(path.dirname(self.bot.db_path), exist_ok=True)
        async with connect(self.bot.db_path) as db:
            await db.execute(CREATE_TABLE_SQL)
            await self._delete_expired(db)
            await db.commit()
        self.delete_expired_daily.start()

    def cog_unload(self) -> None:
        self.delete_expired_daily.cancel()

    async def _delete_expired(self, db: Connection) -> None:
        cutoff = utc_today() - timedelta(days=RETENTION_DAYS - 1)
        await db.execute(DELETE_EXPIRED_SQL, (cutoff.isoformat(),))

    async def _record(self, command_name: str, *, succeeded: bool) -> None:
        today = utc_today()
        async with connect(self.bot.db_path) as db:
            await db.execute(
                UPSERT_SQL,
                (today.isoformat(), command_name, int(succeeded), int(not succeeded)),
            )
            await self._delete_expired(db)
            await db.commit()

    async def _record_safely(self, command_name: str, *, succeeded: bool) -> None:
        try:
            await self._record(command_name, succeeded=succeeded)
        except Exception:
            logger.warning(
                "Could not record analytics for /%s.", command_name, exc_info=True
            )

    async def _fetch_stats(self, days: int) -> list[tuple[str, int, int]]:
        cutoff = utc_today() - timedelta(days=days - 1)
        async with (
            connect(self.bot.db_path) as db,
            db.execute(
                """
                SELECT command_name, SUM(success_count), SUM(failure_count)
                FROM command_analytics
                WHERE day >= ?
                GROUP BY command_name
                """,
                (cutoff.isoformat(),),
            ) as cursor,
        ):
            return [
                (name, successes, failures)
                async for name, successes, failures in cursor
            ]

    @commands.Cog.listener()
    async def on_app_command_completion(
        self,
        interaction: Interaction,
        command: app_commands.Command | app_commands.ContextMenu,
    ) -> None:
        if interaction.guild_id is not None:
            await self._record_safely(command.qualified_name, succeeded=True)

    @commands.Cog.listener()
    async def on_app_command_failure(
        self,
        interaction: Interaction,
        command: app_commands.Command | app_commands.ContextMenu,
    ) -> None:
        if interaction.guild_id is not None:
            await self._record_safely(command.qualified_name, succeeded=False)

    @tasks.loop(time=time(hour=0, tzinfo=UTC))
    async def delete_expired_daily(self) -> None:
        try:
            async with connect(self.bot.db_path) as db:
                await self._delete_expired(db)
                await db.commit()
        except Exception:
            logger.warning("Could not delete expired command analytics.", exc_info=True)

    @delete_expired_daily.before_loop
    async def before_delete_expired_daily(self) -> None:
        await self.bot.wait_until_ready()

    @app_commands.command(
        name="analytics", description="Shows aggregate command usage (Bot Owner Only)."
    )
    @app_commands.describe(days="Number of UTC days to report, from 1 to 90.")
    @app_commands.guild_only()
    @app_commands.check(is_application_owner)
    async def analytics(
        self,
        interaction: Interaction,
        days: app_commands.Range[int, 1, RETENTION_DAYS] = DEFAULT_REPORT_DAYS,
    ) -> None:
        """Show aggregate usage without identifying users or servers."""
        rows = await self._fetch_stats(days)
        most_used = sorted(
            rows, key=lambda row: (row[1] + row[2], row[0]), reverse=True
        )
        with_failures = sorted(
            (row for row in rows if row[2]),
            key=lambda row: (_failure_rate(row[1], row[2]), row[2], row[0]),
            reverse=True,
        )
        used_names = {row[0] for row in rows}
        command_names = {
            command.qualified_name
            for command in self.bot.tree.walk_commands()
            if isinstance(command, app_commands.Command)
        }
        unused = sorted(command_names - used_names)

        successes = sum(row[1] for row in rows)
        failures = sum(row[2] for row in rows)
        attempts = successes + failures
        embed = Embed(
            title=f"Command Analytics — Last {days} Days",
            description=(
                "Aggregate guild usage only. Daily data is permanently deleted after "
                f"{RETENTION_DAYS} days."
            ),
            color=self.bot.color,
        )
        embed.add_field(
            name="Overview",
            value=(
                f"Attempts: **{attempts}**\n"
                f"Succeeded: **{successes}**\n"
                f"Failed: **{failures}**\n"
                f"Failure rate: **{_failure_rate(successes, failures):.1%}**"
            ),
            inline=False,
        )
        embed.add_field(
            name="Most Used",
            value=_limited_lines(
                [_format_command_row(row) for row in most_used],
                empty="No command usage recorded.",
                limit=5,
            ),
            inline=False,
        )
        embed.add_field(
            name="Highest Failure Rates",
            value=_limited_lines(
                [_format_command_row(row) for row in with_failures],
                empty="No command failures recorded.",
                limit=5,
            ),
            inline=False,
        )
        embed.add_field(
            name="Unused",
            value=_limited_lines(
                [f"`/{name}`" for name in unused],
                empty="Every current command was used.",
                limit=10,
            ),
            inline=False,
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @analytics.error
    async def on_analytics_error(
        self, interaction: Interaction, error: app_commands.AppCommandError
    ) -> None:
        if isinstance(error, app_commands.CheckFailure):
            await interaction.response.send_message(
                ":x: Only the bot owner can view command analytics.", ephemeral=True
            )
        else:
            logger.error("Unexpected error in analytics command: %s", error)


async def setup(bot: Sakamoto) -> None:
    """Add aggregate command analytics to the bot."""
    await bot.add_cog(AnalyticsCog(bot))
