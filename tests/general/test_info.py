from pathlib import Path
from types import SimpleNamespace
import sys

import pytest
from psutil import NoSuchProcess

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from extensions.general.info import InfoCog


class FakeProcess:
    def __init__(self, rss=0, *, children=(), error=None):
        self.rss = rss
        self._children = children
        self.error = error

    def memory_info(self):
        if self.error:
            raise self.error
        return SimpleNamespace(rss=self.rss)

    def children(self, recursive=False):
        assert recursive is True
        return self._children


@pytest.mark.asyncio
async def test_get_mem_usage_sums_bot_and_live_children(monkeypatch):
    live_child = FakeProcess(5 * 1024**2)
    exited_child = FakeProcess(error=NoSuchProcess(123))
    bot_process = FakeProcess(10 * 1024**2, children=(live_child, exited_child))
    monkeypatch.setattr("extensions.general.info.Process", lambda _pid: bot_process)

    assert await InfoCog._get_mem_usage() == (
        "Total RSS: 15.00 MiB\n" "Bot: 10.00 MiB\n" "Children (1): 5.00 MiB"
    )
