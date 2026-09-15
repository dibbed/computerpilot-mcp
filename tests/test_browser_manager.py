import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from core.errors import ToolError
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


def test_same_config_shares_browser_with_isolated_contexts() -> None:
    async def run() -> None:
        manager = BrowserManager()
        count = 0
        both = asyncio.Event()
        contexts: list[SimpleNamespace] = []

        async def goto(*args: object, **kwargs: object) -> None:
            nonlocal count
            count += 1
            if count == 2:
                both.set()
            await asyncio.wait_for(both.wait(), 2)

        def make_context() -> SimpleNamespace:
            page = SimpleNamespace(
                set_default_timeout=lambda n: None,
                goto=goto,
                title=AsyncMock(return_value="title"),
                url="about:blank",
            )
            context = SimpleNamespace(new_page=AsyncMock(return_value=page), close=AsyncMock())
            contexts.append(context)
            return context

        browser = SimpleNamespace(new_context=AsyncMock(side_effect=make_context), close=AsyncMock())
        launch = AsyncMock(return_value=browser)
        manager._playwright = SimpleNamespace(chromium=SimpleNamespace(launch=launch))

        result = await asyncio.gather(
            manager.open("a", "about:blank", browser_name="chromium", headless=True, timeout_ms=1000, wait_until="load"),
            manager.open("b", "about:blank", browser_name="chromium", headless=True, timeout_ms=1000, wait_until="load"),
        )

        assert len(result) == 2
        launch.assert_awaited_once_with(headless=True)
        assert browser.new_context.await_count == 2
        assert len(contexts) == 2
        assert manager._sessions["a"].context is not manager._sessions["b"].context
        assert len(manager._pools) == 1
        pool = next(iter(manager._pools.values()))
        assert pool.browser is browser
        assert pool.active_contexts == 2

        assert (await manager.close("a"))["closed"] is True
        contexts[0].close.assert_awaited_once()
        browser.close.assert_not_awaited()
        assert pool.active_contexts == 1
        assert "b" in manager._sessions

    asyncio.run(run())


def test_different_launch_configs_use_separate_pools() -> None:
    async def run() -> None:
        manager = BrowserManager()

        def make_browser() -> SimpleNamespace:
            page = SimpleNamespace(
                set_default_timeout=lambda n: None,
                goto=AsyncMock(return_value=None),
                title=AsyncMock(return_value="title"),
                url="about:blank",
            )
            context = SimpleNamespace(new_page=AsyncMock(return_value=page), close=AsyncMock())
            return SimpleNamespace(new_context=AsyncMock(return_value=context), close=AsyncMock())

        browsers = [make_browser(), make_browser()]
        launch = AsyncMock(side_effect=browsers)
        manager._playwright = SimpleNamespace(chromium=SimpleNamespace(launch=launch))

        await manager.open("headless", "about:blank", browser_name="chromium", headless=True, timeout_ms=1000, wait_until="load")
        await manager.open("headed", "about:blank", browser_name="chromium", headless=False, timeout_ms=1000, wait_until="load")

        assert launch.await_count == 2
        assert len(manager._pools) == 2
        assert {key.headless for key in manager._pools} == {True, False}

    asyncio.run(run())


def test_context_creation_failure_does_not_kill_shared_pool() -> None:
    async def run() -> None:
        manager = BrowserManager()
        page = SimpleNamespace(
            set_default_timeout=lambda n: None,
            goto=AsyncMock(return_value=None),
            title=AsyncMock(return_value="title"),
            url="about:blank",
        )
        first_context = SimpleNamespace(new_page=AsyncMock(return_value=page), close=AsyncMock())
        browser = SimpleNamespace(
            new_context=AsyncMock(side_effect=[first_context, RuntimeError("context failed")]),
            close=AsyncMock(),
        )
        launch = AsyncMock(return_value=browser)
        manager._playwright = SimpleNamespace(chromium=SimpleNamespace(launch=launch))

        await manager.open("a", "about:blank", browser_name="chromium", headless=True, timeout_ms=1000, wait_until="load")
        with pytest.raises(RuntimeError, match="context failed"):
            await manager.open("failed", "about:blank", browser_name="chromium", headless=True, timeout_ms=1000, wait_until="load")

        launch.assert_awaited_once()
        browser.close.assert_not_awaited()
        assert "a" in manager._sessions
        assert "failed" not in manager._sessions
        assert next(iter(manager._pools.values())).active_contexts == 1
        assert not manager._session_locks

    asyncio.run(run())


def test_browser_session_budget_rejects_new_logical_session() -> None:
    async def run() -> None:
        manager = BrowserManager(max_sessions=1)
        page = SimpleNamespace(
            set_default_timeout=lambda n: None,
            goto=AsyncMock(return_value=None),
            title=AsyncMock(return_value="title"),
            url="about:blank",
        )
        context = SimpleNamespace(new_page=AsyncMock(return_value=page), close=AsyncMock())
        browser = SimpleNamespace(new_context=AsyncMock(return_value=context), close=AsyncMock())
        manager._playwright = SimpleNamespace(chromium=SimpleNamespace(launch=AsyncMock(return_value=browser)))

        await manager.open("one", "about:blank", browser_name="chromium", headless=True, timeout_ms=1000, wait_until="load")
        with pytest.raises(ToolError) as exc_info:
            await manager.open("two", "about:blank", browser_name="chromium", headless=True, timeout_ms=1000, wait_until="load")
        assert exc_info.value.code == "browser_session_limit"
        stats = await manager.stats()
        assert stats["active_sessions"] == 1
        assert stats["max_sessions"] == 1
        assert stats["pending_sessions"] == 0

    asyncio.run(run())


def test_browser_pool_budget_rejects_incompatible_new_pool() -> None:
    async def run() -> None:
        manager = BrowserManager(max_pools=1)

        def make_browser() -> SimpleNamespace:
            page = SimpleNamespace(
                set_default_timeout=lambda n: None,
                goto=AsyncMock(return_value=None),
                title=AsyncMock(return_value="title"),
                url="about:blank",
            )
            context = SimpleNamespace(new_page=AsyncMock(return_value=page), close=AsyncMock())
            return SimpleNamespace(new_context=AsyncMock(return_value=context), close=AsyncMock())

        launch = AsyncMock(side_effect=[make_browser(), make_browser()])
        manager._playwright = SimpleNamespace(chromium=SimpleNamespace(launch=launch))
        await manager.open("one", "about:blank", browser_name="chromium", headless=True, timeout_ms=1000, wait_until="load")
        with pytest.raises(ToolError) as exc_info:
            await manager.open("two", "about:blank", browser_name="chromium", headless=False, timeout_ms=1000, wait_until="load")
        assert exc_info.value.code == "browser_pool_limit"
        stats = await manager.stats()
        assert stats["active_pools"] == 1
        assert stats["max_pools"] == 1
        assert stats["pending_sessions"] == 0

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


def test_new_session_navigation_failure_closes_context_not_pool() -> None:
    async def run() -> None:
        manager = BrowserManager()
        page = SimpleNamespace(set_default_timeout=lambda n: None, goto=AsyncMock(side_effect=RuntimeError("navigation failed")))
        context = SimpleNamespace(new_page=AsyncMock(return_value=page), close=AsyncMock())
        browser = SimpleNamespace(new_context=AsyncMock(return_value=context), close=AsyncMock())
        manager._playwright = SimpleNamespace(chromium=SimpleNamespace(launch=AsyncMock(return_value=browser)))

        with pytest.raises(RuntimeError, match="navigation failed"):
            await manager.open("a", "about:blank", browser_name="chromium", headless=True, timeout_ms=1000, wait_until="load")

        context.close.assert_awaited_once()
        browser.close.assert_not_awaited()
        assert not manager._sessions
        assert len(manager._pools) == 1
        assert next(iter(manager._pools.values())).active_contexts == 0
        assert not manager._session_locks

    asyncio.run(run())
