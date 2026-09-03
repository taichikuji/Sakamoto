from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from extensions.general.help import (
    HelpCog,
    command_usage,
    normalize_command_name,
    parameter_details,
)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("play", "play"),
        ("/PLAY", "play"),
        ("  /radio   search  ", "radio search"),
    ],
)
def test_normalize_command_name(value, expected):
    assert normalize_command_name(value) == expected


def test_command_usage_and_parameter_details_come_from_command_metadata():
    command = SimpleNamespace(
        qualified_name="clear",
        parameters=[
            SimpleNamespace(
                name="amount",
                required=False,
                default=1,
                description="Number of messages to remove.",
            ),
            SimpleNamespace(
                name="user",
                required=False,
                default=None,
                description="Only remove this member's messages.",
            ),
        ],
    )

    assert command_usage(command) == "/clear [amount] [user]"
    assert parameter_details(command) == (
        "`amount` (optional) — Number of messages to remove. Default: `1`.\n"
        "`user` (optional) — Only remove this member's messages."
    )


@pytest.mark.asyncio
async def test_command_name_autocomplete_filters_tree_and_limits_choices():
    commands = [
        SimpleNamespace(qualified_name=f"command-{index}") for index in range(30)
    ]
    commands.append(SimpleNamespace(qualified_name="radio search"))
    bot = SimpleNamespace(tree=SimpleNamespace(walk_commands=lambda: iter(commands)))

    choices = await HelpCog(bot).command_name_autocomplete(SimpleNamespace(), "/RADIO")

    assert [(choice.name, choice.value) for choice in choices] == [
        ("/radio search", "radio search")
    ]
    all_choices = await HelpCog(bot).command_name_autocomplete(SimpleNamespace(), "")
    assert len(all_choices) == 25


@pytest.mark.asyncio
async def test_show_help_reports_unknown_command_without_exposing_other_commands():
    interaction = SimpleNamespace(response=SimpleNamespace(send_message=AsyncMock()))
    bot = SimpleNamespace(tree=SimpleNamespace(get_command=lambda _name: None))

    await HelpCog.show_help.callback(HelpCog(bot), interaction, "/missing command")

    interaction.response.send_message.assert_awaited_once_with(
        ":x: Command `/missing command` not found.", ephemeral=True
    )
