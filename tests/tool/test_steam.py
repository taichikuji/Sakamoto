from pathlib import Path
from types import SimpleNamespace
import sys
from unittest.mock import AsyncMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from functions.tool.steam import SteamCog


class DummyResponse:
    def __init__(self, status: int, payload: dict):
        self.status = status
        self._payload = payload

    async def json(self):
        return self._payload


class DummyRequestContext:
    def __init__(self, response: DummyResponse):
        self.response = response

    async def __aenter__(self):
        return self.response

    async def __aexit__(self, *_exc):
        return False


class DummySession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, url, params=None):
        self.calls.append((url, params))
        response_or_exc = self.responses.pop(0)
        if isinstance(response_or_exc, Exception):
            raise response_or_exc
        return DummyRequestContext(response_or_exc)


class DummyBot:
    def __init__(self, *, db_path: Path, session):
        self.db_path = str(db_path)
        self.session = session
        self.color = 0x123456


def _make_interaction(user_id: int = 1):
    user = SimpleNamespace(
        id=user_id,
        mention=f"<@{user_id}>",
        display_name=f"user-{user_id}",
        display_avatar=SimpleNamespace(url="https://example.test/avatar.png"),
    )
    return SimpleNamespace(
        user=user,
        response=SimpleNamespace(defer=AsyncMock()),
        followup=SimpleNamespace(send=AsyncMock()),
    )


@pytest.mark.asyncio
async def test_save_and_get_steam_link_roundtrip(tmp_path):
    bot = DummyBot(db_path=tmp_path / "steam.db", session=None)
    cog = SteamCog(bot)
    await cog._init_db()

    await cog._save_steam_link(10, "76561198000000010")
    linked = await cog._get_steam_link(10)

    assert linked == "76561198000000010"


@pytest.mark.parametrize(
    ("status", "detail"),
    [
        (400, "400 Bad Request"),
        (401, "401/403"),
        (403, "401/403"),
        (404, "404"),
        (405, "405"),
        (429, "429"),
        (500, "500"),
        (503, "503"),
        (418, "HTTP `418`"),
    ],
)
def test_steam_http_error_messages_are_status_specific(tmp_path, status, detail):
    cog = SteamCog(DummyBot(db_path=tmp_path / "steam.db", session=None))

    assert detail in cog._steam_http_error_message(status)


@pytest.mark.asyncio
async def test_resolve_steam_id_returns_direct_profile_id(monkeypatch, tmp_path):
    monkeypatch.setattr("functions.tool.steam.STEAM_TOKEN", "token")
    bot = DummyBot(db_path=tmp_path / "steam.db", session=DummySession([]))
    cog = SteamCog(bot)

    result = await cog._resolve_steam_id("76561198000000042")

    assert result == ("76561198000000042", None)


@pytest.mark.asyncio
async def test_resolve_steam_id_extracts_profiles_url(monkeypatch, tmp_path):
    monkeypatch.setattr("functions.tool.steam.STEAM_TOKEN", "token")
    bot = DummyBot(db_path=tmp_path / "steam.db", session=DummySession([]))
    cog = SteamCog(bot)

    result = await cog._resolve_steam_id("https://steamcommunity.com/profiles/76561198000000042/")

    assert result == ("76561198000000042", None)


@pytest.mark.asyncio
async def test_resolve_steam_id_resolves_vanity_via_api(monkeypatch, tmp_path):
    monkeypatch.setattr("functions.tool.steam.STEAM_TOKEN", "token")
    session = DummySession(
        [
            DummyResponse(
                200,
                {"response": {"success": 1, "steamid": "76561198000000123"}},
            )
        ]
    )
    bot = DummyBot(db_path=tmp_path / "steam.db", session=session)
    cog = SteamCog(bot)

    result = await cog._resolve_steam_id("my-vanity-name")

    assert result == ("76561198000000123", None)
    assert session.calls[0][1]["vanityurl"] == "my-vanity-name"


@pytest.mark.asyncio
async def test_resolve_steam_id_failures(monkeypatch, tmp_path):
    monkeypatch.setattr("functions.tool.steam.STEAM_TOKEN", "token")
    no_session_bot = DummyBot(db_path=tmp_path / "a.db", session=None)
    no_session_cog = SteamCog(no_session_bot)
    assert await no_session_cog._resolve_steam_id("alice") == (
        None,
        ":x: The bot's HTTP session is not ready. Please try again later.",
    )

    monkeypatch.setattr("functions.tool.steam.STEAM_TOKEN", None)
    tokenless_bot = DummyBot(db_path=tmp_path / "b.db", session=DummySession([]))
    tokenless_cog = SteamCog(tokenless_bot)
    assert await tokenless_cog._resolve_steam_id("alice") == (
        None,
        ":x: The bot's Steam API key is not configured. Please contact the bot owner.",
    )

    monkeypatch.setattr("functions.tool.steam.STEAM_TOKEN", "token")
    non_200_bot = DummyBot(
        db_path=tmp_path / "c.db",
        session=DummySession([DummyResponse(500, {"response": {}})]),
    )
    non_200_cog = SteamCog(non_200_bot)
    assert await non_200_cog._resolve_steam_id("alice") == (
        None,
        ":x: Steam had an internal server error (500). Please try again shortly.",
    )

    unresolved_bot = DummyBot(
        db_path=tmp_path / "d.db",
        session=DummySession([DummyResponse(200, {"response": {"success": 42}})]),
    )
    unresolved_cog = SteamCog(unresolved_bot)
    assert await unresolved_cog._resolve_steam_id("alice") == (
        None,
        "Steam could not find that vanity profile name (success code 42).",
    )

    error_bot = DummyBot(
        db_path=tmp_path / "e.db",
        session=DummySession([RuntimeError("network down")]),
    )
    error_cog = SteamCog(error_bot)
    assert await error_cog._resolve_steam_id("alice") == (
        None,
        ":x: Unexpected error while resolving your Steam account. Please try again later.",
    )


@pytest.mark.asyncio
async def test_link_steam_guardrails_and_success(monkeypatch, tmp_path):
    interaction = _make_interaction()

    monkeypatch.setattr("functions.tool.steam.STEAM_TOKEN", None)
    cog = SteamCog(DummyBot(db_path=tmp_path / "steam.db", session=DummySession([])))
    await SteamCog.link_steam.callback(cog, interaction, "alice")
    interaction.followup.send.assert_awaited_with(
        ":x: The bot's Steam API key is not configured. Linking is currently unavailable. Please contact the bot owner."
    )

    monkeypatch.setattr("functions.tool.steam.STEAM_TOKEN", "token")
    interaction = _make_interaction()
    cog = SteamCog(DummyBot(db_path=tmp_path / "steam2.db", session=None))
    await SteamCog.link_steam.callback(cog, interaction, "alice")
    interaction.followup.send.assert_awaited_with(
        ":x: The bot's HTTP session is not ready. Please try again later."
    )

    interaction = _make_interaction()
    cog = SteamCog(DummyBot(db_path=tmp_path / "steam3.db", session=DummySession([])))
    cog._resolve_steam_id = AsyncMock(
        return_value=(None, "Steam could not find that vanity profile name (success code 42).")
    )
    await SteamCog.link_steam.callback(cog, interaction, "alice")
    assert "Reason: Steam could not find that vanity profile name (success code 42)." in (
        interaction.followup.send.await_args.args[0]
    )

    interaction = _make_interaction(user_id=7)
    cog = SteamCog(DummyBot(db_path=tmp_path / "steam4.db", session=DummySession([])))
    cog._resolve_steam_id = AsyncMock(return_value=("76561198000000007", None))
    cog._save_steam_link = AsyncMock()
    await SteamCog.link_steam.callback(cog, interaction, "alice")
    cog._save_steam_link.assert_awaited_once_with(7, "76561198000000007")
    interaction.followup.send.assert_awaited_with(
        ":white_check_mark: Your Discord account has been successfully linked to SteamID: `76561198000000007`."
    )


@pytest.mark.asyncio
async def test_get_lobby_guardrails(monkeypatch, tmp_path):
    interaction = _make_interaction()

    monkeypatch.setattr("functions.tool.steam.STEAM_TOKEN", None)
    cog = SteamCog(DummyBot(db_path=tmp_path / "steam.db", session=DummySession([])))
    await SteamCog.get_lobby.callback(cog, interaction)
    interaction.followup.send.assert_awaited_with(
        ":x: The bot's Steam API key is not configured. Lobby fetching is unavailable. Please contact the bot owner."
    )

    monkeypatch.setattr("functions.tool.steam.STEAM_TOKEN", "token")
    interaction = _make_interaction()
    cog = SteamCog(DummyBot(db_path=tmp_path / "steam2.db", session=None))
    await SteamCog.get_lobby.callback(cog, interaction)
    interaction.followup.send.assert_awaited_with(
        ":x: The bot's HTTP session is not ready. Please try again later."
    )

    interaction = _make_interaction()
    cog = SteamCog(DummyBot(db_path=tmp_path / "steam3.db", session=DummySession([])))
    cog._get_steam_link = AsyncMock(return_value=None)
    await SteamCog.get_lobby.callback(cog, interaction)
    interaction.followup.send.assert_awaited_with(
        ":information_source: Your Steam account is not linked. "
        "Please use the `/steam link <your_steam_id_or_vanity_name>` command first."
    )


@pytest.mark.asyncio
async def test_get_lobby_handles_player_summary_failures(monkeypatch, tmp_path):
    monkeypatch.setattr("functions.tool.steam.STEAM_TOKEN", "token")
    interaction = _make_interaction()

    # HTTP failure
    session = DummySession([DummyResponse(500, {"response": {}})])
    cog = SteamCog(DummyBot(db_path=tmp_path / "a.db", session=session))
    cog._get_steam_link = AsyncMock(return_value="76561198000000001")
    await SteamCog.get_lobby.callback(cog, interaction)
    interaction.followup.send.assert_awaited()

    # Missing players
    interaction = _make_interaction()
    session = DummySession([DummyResponse(200, {"response": {"players": []}})])
    cog = SteamCog(DummyBot(db_path=tmp_path / "b.db", session=session))
    cog._get_steam_link = AsyncMock(return_value="76561198000000001")
    await SteamCog.get_lobby.callback(cog, interaction)
    interaction.followup.send.assert_awaited_with(
        ":x: Could not retrieve your player summary from Steam. "
        "Ensure your Steam profile is public and you've linked the correct ID."
    )

    # Not in game
    interaction = _make_interaction()
    session = DummySession(
        [DummyResponse(200, {"response": {"players": [{"personaname": "x"}]}})]
    )
    cog = SteamCog(DummyBot(db_path=tmp_path / "c.db", session=session))
    cog._get_steam_link = AsyncMock(return_value="76561198000000001")
    await SteamCog.get_lobby.callback(cog, interaction)
    interaction.followup.send.assert_awaited_with(
        ":x: You are not currently in a joinable game. "
        "Please start a game and try again."
    )

    # Missing lobby
    interaction = _make_interaction()
    session = DummySession(
        [
            DummyResponse(
                200,
                {
                    "response": {
                        "players": [{"gameid": "570", "gameextrainfo": "Dota 2", "lobbysteamid": "0"}]
                    }
                },
            )
        ]
    )
    cog = SteamCog(DummyBot(db_path=tmp_path / "d.db", session=session))
    cog._get_steam_link = AsyncMock(return_value="76561198000000001")
    await SteamCog.get_lobby.callback(cog, interaction)
    interaction.followup.send.assert_awaited()


@pytest.mark.asyncio
async def test_get_lobby_success_sends_embed_with_redirect_url(monkeypatch, tmp_path):
    monkeypatch.setattr("functions.tool.steam.STEAM_TOKEN", "token")
    interaction = _make_interaction(user_id=44)
    session = DummySession(
        [
            DummyResponse(
                200,
                {
                    "response": {
                        "players": [
                            {
                                "gameid": "570",
                                "gameextrainfo": "Dota 2",
                                "lobbysteamid": "1234567890",
                            }
                        ]
                    }
                },
            )
        ]
    )
    cog = SteamCog(DummyBot(db_path=tmp_path / "steam.db", session=session))
    cog._get_steam_link = AsyncMock(return_value="76561198000000044")

    await SteamCog.get_lobby.callback(cog, interaction)

    interaction.followup.send.assert_awaited_once()
    embed = interaction.followup.send.await_args.kwargs["embed"]
    assert "Steam Lobby Invite for user-44" == embed.title
    assert "steam://joinlobby/570/1234567890/76561198000000044" in embed.fields[0].value
