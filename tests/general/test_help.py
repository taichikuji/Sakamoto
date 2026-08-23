from types import SimpleNamespace

import pytest

from extensions.general.help import command_usage, normalize_command_name, parameter_details


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
        parameters={
            "amount": SimpleNamespace(
                name="amount",
                required=False,
                default=1,
                description="Number of messages to remove.",
            ),
            "user": SimpleNamespace(
                name="user",
                required=False,
                default=None,
                description="Only remove this member's messages.",
            ),
        },
    )

    assert command_usage(command) == "/clear [amount] [user]"
    assert parameter_details(command) == (
        "`amount` (optional) — Number of messages to remove. Default: `1`.\n"
        "`user` (optional) — Only remove this member's messages."
    )
