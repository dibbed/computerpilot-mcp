import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from tools.browser.manager import BrowserManager


def test_sessions_overlap_and_same_session_waits() -> None:
    async def run() -> None:
        manager = BrowserManager()
        entered = asyncio.Event()
        release = asyncio.Event()

        async def owner() -> None:
            async with manager.session("a"):
                entered.set()
                await release.wait()

        task = asyncio.create_task(owner())
        await entered.wait()
        waiter = asyncio.create_task(manager.close("a"))
        await asyncio.sleep(0)
        assert not waiter.done()
        assert (await asyncio.wait_for(manager.close("b"), 1))["closed"] is False
        waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiter
        release.set()
        await task
        assert not manager._session_locks

    asyncio.run(run())


def test_open_independent_navigation_and_partial_failure_cleanup() -> None:
    async def run() -> None:
        manager = BrowserManager()
        count = 0
        both = asyncio.Event()

        async def goto(*args: object, **kwargs: object) -> None:
            nonlocal count
            count += 1
            if count == 2:
                both.set()
            await asyncio.wait_for(both.wait(), 2)

        def browser() -> SimpleNamespace:
            page = SimpleNamespace(set_default_timeout=lambda n: None, goto=goto, title=AsyncMock(return_value="title"), url="about:blank")
            context = SimpleNamespace(new_page=AsyncMock(return_value=page))
            return SimpleNamespace(new_context=AsyncMock(return_value=context), close=AsyncMock())

        runtime = SimpleNamespace(chromium=SimpleNamespace(launch=AsyncMock(side_effect=lambda **k: browser())))
        manager._playwright = runtime
        result = await asyncio.gather(
            manager.open("a", "about:blank", browser_name="chromium", headless=True, timeout_ms=1000, wait_until="load"),
            manager.open("b", "about:blank", browser_name="chromium", headless=True, timeout_ms=1000, wait_until="load"),
        )
        assert len(result) == 2
        failed = SimpleNamespace(new_context=AsyncMock(side_effect=RuntimeError("context failed")), close=AsyncMock())
        runtime.chromium.launch = AsyncMock(return_value=failed)
        with pytest.raises(RuntimeError, match="context failed"):
            await manager.open(
                "failed",
                "about:blank",
                browser_name="chromium",
                headless=True,
                timeout_ms=1000,
                wait_until="load",
            )
        failed.close.assert_awaited_once()
        assert "failed" not in manager._sessions
        assert not manager._session_locks

    asyncio.run(run())


def test_runtime_initialization_singleflight(monkeypatch: pytest.MonkeyPatch) -> None:
    import sys

    async def run() -> None:
        manager = BrowserManager()
        runtime = object()

        async def start() -> object:
            await asyncio.sleep(0.01)
            return runtime

        starter = AsyncMock(side_effect=start)
        monkeypatch.setitem(sys.modules, "playwright.async_api", SimpleNamespace(async_playwright=lambda: SimpleNamespace(start=starter)))
        results = await asyncio.gather(*(manager._runtime() for _ in range(20)))
        assert all(result is runtime for result in results)
        starter.assert_awaited_once()

    asyncio.run(run())


def test_new_session_navigation_failure_closes_browser() -> None:
    async def run() -> None:
        manager = BrowserManager()
        page = SimpleNamespace(set_default_timeout=lambda n: None, goto=AsyncMock(side_effect=RuntimeError("navigation failed")))
        context = SimpleNamespace(new_page=AsyncMock(return_value=page))
        browser = SimpleNamespace(new_context=AsyncMock(return_value=context), close=AsyncMock())
        manager._playwright = SimpleNamespace(chromium=SimpleNamespace(launch=AsyncMock(return_value=browser)))
        with pytest.raises(RuntimeError, match="navigation failed"):
            await manager.open("a", "about:blank", browser_name="chromium", headless=True, timeout_ms=1000, wait_until="load")
        browser.close.assert_awaited_once()
        assert not manager._sessions
        assert not manager._session_locks

    asyncio.run(run())
