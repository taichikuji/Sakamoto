import sqlite3
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from discord import app_commands

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from extensions.core.analytics import (
    CREATE_TABLE_SQL,
    DELETE_EXPIRED_SQL,
    UPSERT_SQL,
    AnalyticsCog,
    is_application_owner,
    mark_app_command_failed,
)


def _bot(tmp_path=None):
    commands = [
        SimpleNamespace(qualified_name="ping"),
        SimpleNamespace(qualified_name="radio search"),
    ]
    return SimpleNamespace(
        db_path=str((tmp_path or Path("data")) / "analytics.db"),
        color=0x123456,
        tree=SimpleNamespace(walk_commands=lambda: iter(commands)),
    )


def test_schema_and_upsert_store_only_aggregate_command_counts():
    db = sqlite3.connect(":memory:")
    db.execute(CREATE_TABLE_SQL)
    db.execute(UPSERT_SQL, ("2026-09-12", "radio search", 1, 0))
    db.execute(UPSERT_SQL, ("2026-09-12", "radio search", 0, 1))

    columns = [row[1] for row in db.execute("PRAGMA table_info(command_analytics)")]
    row = db.execute("SELECT * FROM command_analytics").fetchone()

    assert columns == ["day", "command_name", "success_count", "failure_count"]
    assert row == ("2026-09-12", "radio search", 1, 1)


def test_retention_query_deletes_expired_daily_rows():
    db = sqlite3.connect(":memory:")
    db.execute(CREATE_TABLE_SQL)
    db.executemany(
        UPSERT_SQL,
        [
            ("2026-06-14", "ping", 1, 0),
            ("2026-06-15", "ping", 1, 0),
        ],
    )

    db.execute(DELETE_EXPIRED_SQL, ("2026-06-15",))

    assert db.execute("SELECT day FROM command_analytics").fetchall() == [
        ("2026-06-15",)
    ]


def test_handled_failure_dispatches_once_and_suppresses_completion():
    interaction = SimpleNamespace(
        command_failed=False,
        command=object(),
        client=SimpleNamespace(dispatch=MagicMock()),
    )

    mark_app_command_failed(interaction)
    mark_app_command_failed(interaction)

    assert interaction.command_failed is True
    interaction.client.dispatch.assert_called_once_with(
        "app_command_failure", interaction, interaction.command
    )


@pytest.mark.asyncio
async def test_completion_and_failure_record_all_guild_command_names(tmp_path):
    cog = AnalyticsCog(_bot(tmp_path))
    cog._record_safely = AsyncMock()
    command = SimpleNamespace(qualified_name="radio search")

    await cog.on_app_command_completion(SimpleNamespace(guild_id=10), command)
    await cog.on_app_command_failure(SimpleNamespace(guild_id=10), command)
    await cog.on_app_command_completion(
        SimpleNamespace(guild_id=10), SimpleNamespace(qualified_name="analytics")
    )
    await cog.on_app_command_failure(
        SimpleNamespace(guild_id=10), SimpleNamespace(qualified_name="ping")
    )
    await cog.on_app_command_completion(SimpleNamespace(guild_id=None), command)

    assert cog._record_safely.await_args_list[0].args == ("radio search",)
    assert cog._record_safely.await_args_list[0].kwargs == {"succeeded": True}
    assert cog._record_safely.await_args_list[1].args == ("radio search",)
    assert cog._record_safely.await_args_list[1].kwargs == {"succeeded": False}
    assert cog._record_safely.await_args_list[2].args == ("analytics",)
    assert cog._record_safely.await_args_list[2].kwargs == {"succeeded": True}
    assert cog._record_safely.await_args_list[3].args == ("ping",)
    assert cog._record_safely.await_args_list[3].kwargs == {"succeeded": False}
    assert cog._record_safely.await_count == 4


@pytest.mark.asyncio
async def test_tracking_failure_is_logged_and_isolated(monkeypatch, tmp_path):
    cog = AnalyticsCog(_bot(tmp_path))
    cog._record = AsyncMock(side_effect=sqlite3.OperationalError("locked"))
    logger = MagicMock()
    monkeypatch.setattr("extensions.core.analytics.logger", logger)

    await cog._record_safely("ping", succeeded=True)

    logger.warning.assert_called_once()


@pytest.mark.asyncio
async def test_analytics_access_check_uses_application_owner():
    interaction = SimpleNamespace(
        client=SimpleNamespace(is_owner=AsyncMock(return_value=False)), user=object()
    )

    assert await is_application_owner(interaction) is False
    interaction.client.is_owner.assert_awaited_once_with(interaction.user)


@pytest.mark.asyncio
async def test_analytics_report_shows_usage_failures_and_unused_commands(tmp_path):
    async def callback(_interaction):
        pass

    bot = _bot(tmp_path)
    bot.tree.walk_commands = lambda: iter(
        [
            app_commands.Command(
                name="analytics", description="Stats", callback=callback
            ),
            app_commands.Command(name="ping", description="Ping", callback=callback),
            app_commands.Command(
                name="unused", description="Unused", callback=callback
            ),
        ]
    )
    cog = AnalyticsCog(bot)
    cog._fetch_stats = AsyncMock(return_value=[("ping", 8, 2), ("analytics", 20, 0)])
    interaction = SimpleNamespace(response=SimpleNamespace(send_message=AsyncMock()))

    await AnalyticsCog.analytics.callback(cog, interaction, 30)

    sent_embed = interaction.response.send_message.await_args.kwargs["embed"]
    fields = {field.name: field.value for field in sent_embed.fields}
    assert sent_embed.title == "Command Analytics — Last 30 Days"
    assert "2/8 fail/success" in fields["Most Used"]
    assert "20.0% failed" in fields["Highest Failure Rates"]
    assert "`/analytics`" in fields["Most Used"]
    assert "`/unused`" in fields["Unused"]
    interaction.response.send_message.assert_awaited_once_with(
        embed=sent_embed, ephemeral=True
    )


def test_analytics_command_is_guild_only_and_bounded_to_retention():
    command = AnalyticsCog.analytics

    assert command.guild_only is True
    assert command.parameters[0].min_value == 1
    assert command.parameters[0].max_value == 90
