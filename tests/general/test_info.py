import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from extensions.general.info import InfoCog


def test_create_embed_contains_stable_project_info_and_uptime(monkeypatch):
    monkeypatch.setattr("extensions.general.info.time.monotonic", lambda: 3_661)
    monkeypatch.setattr("extensions.general.info.python_version", lambda: "3.14.5")
    monkeypatch.setattr("extensions.general.info.system", lambda: "Linux")
    monkeypatch.setattr("extensions.general.info.machine", lambda: "x86_64")
    cog = InfoCog(SimpleNamespace(color=0xFF3351, started_at=0))
    embed = cog.create_embed()

    assert InfoCog.info.description == "Learn about Sakamoto and check its uptime."
    assert embed.title == ":information_source: Bot's Info"
    assert embed.description == "Here's some information about me and my dependencies!"
    assert embed.color.value == 0xFF3351
    assert [(field.name, field.value, field.inline) for field in embed.fields] == [
        ("Runtime", "**Python**: 3.14.5", True),
        ("OS", "**Linux**: x86_64", True),
        ("Uptime", "1h 1m", True),
        ("Project", "[GitHub](https://github.com/taichikuji/Sakamoto)", True),
    ]


def test_uptime_formats_hours_and_remaining_minutes(monkeypatch):
    monkeypatch.setattr("extensions.general.info.time.monotonic", lambda: 3_661)

    assert InfoCog(SimpleNamespace(started_at=0)).uptime() == "1h 1m"
