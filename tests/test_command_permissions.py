import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from extensions.community.lobby import LobbyCog
from extensions.core.loader import LoaderCog
from extensions.core.shutdown import CloseCog
from extensions.core.sync import SyncCog
from extensions.moderation.clear import ClearCog


def test_restricted_commands_publish_defaults_and_keep_runtime_checks():
    lobby = LobbyCog(SimpleNamespace())
    restricted = [
        (LoaderCog.load, "administrator"),
        (LoaderCog.unload, "administrator"),
        (LoaderCog.reload, "administrator"),
        (CloseCog.shutdown_bot, "administrator"),
        (SyncCog.sync.app_command, "administrator"),
        (ClearCog.clear, "manage_messages"),
        (lobby.__cog_app_commands_group__, "manage_channels"),
    ]

    for command, permission in restricted:
        assert command.default_permissions is not None
        assert getattr(command.default_permissions, permission) is True

    assert LoaderCog.load.checks
    assert LoaderCog.unload.checks
    assert LoaderCog.reload.checks
    assert CloseCog.shutdown_bot.checks
    assert SyncCog.sync.checks
    assert ClearCog.clear.checks
    assert lobby.set_generator.checks
