from pathlib import Path
from types import SimpleNamespace
import sys
from unittest.mock import AsyncMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from extensions.general.ping import PingCog


@pytest.mark.asyncio
async def test_ping_truncates_latency_to_milliseconds():
    interaction = SimpleNamespace(response=SimpleNamespace(send_message=AsyncMock()))
    cog = PingCog(SimpleNamespace(latency=0.1239))

    await PingCog.ping.callback(cog, interaction)

    interaction.response.send_message.assert_awaited_once_with(
        ":ping_pong: Pong! Latency: 123ms"
    )
