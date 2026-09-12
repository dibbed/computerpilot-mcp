from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor

from scripts.supervisor import PanelStatusCache


def test_panel_status_cache_uses_independent_ttls_and_copies() -> None:
    now = [0.0]
    cache = PanelStatusCache(clock=lambda: now[0])
    calls = {"fast": 0, "slow": 0}

    def fast() -> dict[str, object]:
        calls["fast"] += 1
        return {"items": [{"value": calls["fast"]}]}

    def slow() -> dict[str, int]:
        calls["slow"] += 1
        return {"bytes": calls["slow"]}

    first = cache.get("fast", 2.0, fast)
    first["items"][0]["value"] = 999
    assert cache.get("fast", 2.0, fast)["items"][0]["value"] == 1
    assert cache.get("slow", 20.0, slow)["bytes"] == 1

    now[0] = 3.0
    assert cache.get("fast", 2.0, fast)["items"][0]["value"] == 2
    assert cache.get("slow", 20.0, slow)["bytes"] == 1
    assert calls == {"fast": 2, "slow": 1}


def test_panel_status_cache_coalesces_concurrent_refresh() -> None:
    cache = PanelStatusCache()
    started = threading.Event()
    release = threading.Event()
    calls: list[int] = []

    def loader() -> dict[str, int]:
        calls.append(1)
        started.set()
        assert release.wait(5)
        return {"value": 7}

    with ThreadPoolExecutor(max_workers=12) as pool:
        owner = pool.submit(cache.get, "jobs", 2.0, loader)
        assert started.wait(5)
        waiters = [pool.submit(cache.get, "jobs", 2.0, loader) for _ in range(11)]
        time.sleep(0.02)
        release.set()
        results = [owner.result(timeout=5), *(future.result(timeout=5) for future in waiters)]

    assert results == [{"value": 7}] * 12
    assert len(calls) == 1
