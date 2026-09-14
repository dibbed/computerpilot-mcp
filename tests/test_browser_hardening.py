import asyncio
from collections.abc import Callable
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from core.errors import ToolError
from tools.browser.manager import BrowserManager


def _context() -> SimpleNamespace:
    page = SimpleNamespace(
        set_default_timeout=lambda n: None,
        goto=AsyncMock(return_value=None),
        title=AsyncMock(return_value="title"),
        url="about:blank",
    )
    return SimpleNamespace(new_page=AsyncMock(return_value=page), close=AsyncMock())


def _closable_context() -> tuple[SimpleNamespace, SimpleNamespace, dict[str, bool]]:
    state = {"closed": False}
    page = SimpleNamespace(
        set_default_timeout=lambda n: None,
        goto=AsyncMock(return_value=None),
        title=AsyncMock(return_value="title"),
        url="about:blank",
        is_closed=lambda: state["closed"],
    )

    async def close() -> None:
        state["closed"] = True

    context = SimpleNamespace(new_page=AsyncMock(return_value=page), close=AsyncMock(side_effect=close))
    return context, page, state


class FakeBrowser:
    def __init__(self, contexts: list[SimpleNamespace] | None = None) -> None:
        self._connected = True
        self._disconnect_handlers: list[Callable[..., Any]] = []
        self._contexts = list(contexts or [])
        self.new_context = AsyncMock(side_effect=self._new_context)
        self.close = AsyncMock(side_effect=self._close)

    async def _new_context(self) -> SimpleNamespace:
        if not self._contexts:
            raise RuntimeError("no context fixture")
        return self._contexts.pop(0)

    async def _close(self) -> None:
        self.disconnect()

    def is_connected(self) -> bool:
        return self._connected

    def on(self, event: str, callback: Callable[..., Any]) -> None:
        if event == "disconnected":
            self._disconnect_handlers.append(callback)

    def disconnect(self) -> None:
        if not self._connected:
            return
        self._connected = False
        for callback in list(self._disconnect_handlers):
            callback(self)


def test_closed_page_reopens_session_in_existing_pool() -> None:
    async def run() -> None:
        old_context, _, old_state = _closable_context()
        new_context, new_page, _ = _closable_context()
        browser = FakeBrowser([old_context, new_context])
        launch = AsyncMock(return_value=browser)
        manager = BrowserManager(session_idle_sec=60, pool_idle_sec=60)
        manager._playwright = SimpleNamespace(chromium=SimpleNamespace(launch=launch))

        await manager.open("a", "about:blank", browser_name="chromium", headless=True, timeout_ms=1000, wait_until="load")
        pool = next(iter(manager._pools.values()))
        old_state["closed"] = True

        result = await manager.open("a", "about:blank", browser_name="chromium", headless=True, timeout_ms=1000, wait_until="load")

        assert result["ok"] is True
        launch.assert_awaited_once()
        assert browser.new_context.await_count == 2
        assert manager._sessions["a"].page is new_page
        assert manager._sessions["a"].pool is pool
        assert pool.active_contexts == 1
        old_context.close.assert_awaited_once()

    asyncio.run(run())


def test_closed_context_reopens_session_in_existing_pool() -> None:
    async def run() -> None:
        old_context, _, _ = _closable_context()
        new_context, new_page, _ = _closable_context()
        browser = FakeBrowser([old_context, new_context])
        launch = AsyncMock(return_value=browser)
        manager = BrowserManager(session_idle_sec=60, pool_idle_sec=60)
        manager._playwright = SimpleNamespace(chromium=SimpleNamespace(launch=launch))

        await manager.open("a", "about:blank", browser_name="chromium", headless=True, timeout_ms=1000, wait_until="load")
        pool = next(iter(manager._pools.values()))
        await old_context.close()

        result = await manager.open("a", "about:blank", browser_name="chromium", headless=True, timeout_ms=1000, wait_until="load")

        assert result["ok"] is True
        launch.assert_awaited_once()
        assert browser.new_context.await_count == 2
        assert manager._sessions["a"].page is new_page
        assert manager._sessions["a"].pool is pool
        assert pool.active_contexts == 1
        assert old_context.close.await_count == 2

    asyncio.run(run())


def test_closed_page_is_reported_stale_to_non_open_operations() -> None:
    async def run() -> None:
        context, _, state = _closable_context()
        browser = FakeBrowser([context])
        manager = BrowserManager(session_idle_sec=60, pool_idle_sec=60)
        manager._playwright = SimpleNamespace(chromium=SimpleNamespace(launch=AsyncMock(return_value=browser)))

        await manager.open("a", "about:blank", browser_name="chromium", headless=True, timeout_ms=1000, wait_until="load")
        state["closed"] = True

        with pytest.raises(ToolError) as exc_info:
            manager.page("a")
        assert exc_info.value.code == "browser_session_stale"
        assert manager._sessions["a"].stale is True

    asyncio.run(run())


def test_page_closed_during_navigation_returns_stale_session_error() -> None:
    async def run() -> None:
        context, page, state = _closable_context()
        browser = FakeBrowser([context])
        manager = BrowserManager(session_idle_sec=60, pool_idle_sec=60)
        manager._playwright = SimpleNamespace(chromium=SimpleNamespace(launch=AsyncMock(return_value=browser)))

        await manager.open("a", "about:blank", browser_name="chromium", headless=True, timeout_ms=1000, wait_until="load")

        async def close_during_navigation(*args: object, **kwargs: object) -> None:
            state["closed"] = True
            raise RuntimeError("target closed")

        page.goto = AsyncMock(side_effect=close_during_navigation)
        with pytest.raises(ToolError) as exc_info:
            await manager.open("a", "about:blank", browser_name="chromium", headless=True, timeout_ms=1000, wait_until="load")
        assert exc_info.value.code == "browser_session_stale"
        assert manager._sessions["a"].stale is True
        assert browser.is_connected() is True

    asyncio.run(run())


def test_launch_failure_does_not_poison_future_pool_creation() -> None:
    async def run() -> None:
        browser = FakeBrowser([_context()])
        launch = AsyncMock(side_effect=[RuntimeError("launch failed"), browser])
        manager = BrowserManager(session_idle_sec=60, pool_idle_sec=60)
        manager._playwright = SimpleNamespace(chromium=SimpleNamespace(launch=launch))

        with pytest.raises(RuntimeError, match="launch failed"):
            await manager.open("a", "about:blank", browser_name="chromium", headless=True, timeout_ms=1000, wait_until="load")
        assert not manager._pools
        assert "a" not in manager._sessions

        result = await manager.open("a", "about:blank", browser_name="chromium", headless=True, timeout_ms=1000, wait_until="load")
        assert result["ok"] is True
        assert launch.await_count == 2
        assert len(manager._pools) == 1

    asyncio.run(run())


def test_disconnect_invalidates_pool_and_marks_session_stale() -> None:
    async def run() -> None:
        context = _context()
        browser = FakeBrowser([context])
        manager = BrowserManager(session_idle_sec=60, pool_idle_sec=60)
        manager._playwright = SimpleNamespace(chromium=SimpleNamespace(launch=AsyncMock(return_value=browser)))

        await manager.open("a", "about:blank", browser_name="chromium", headless=True, timeout_ms=1000, wait_until="load")
        assert len(manager._pools) == 1

        browser.disconnect()
        assert not manager._pools
        assert manager._sessions["a"].stale is True

        with pytest.raises(ToolError) as exc_info:
            manager.page("a")
        assert exc_info.value.code == "browser_session_stale"

    asyncio.run(run())


def test_open_recreates_disconnected_pool_for_same_session() -> None:
    async def run() -> None:
        old_context = _context()
        new_context = _context()
        old_browser = FakeBrowser([old_context])
        new_browser = FakeBrowser([new_context])
        launch = AsyncMock(side_effect=[old_browser, new_browser])
        manager = BrowserManager(session_idle_sec=60, pool_idle_sec=60)
        manager._playwright = SimpleNamespace(chromium=SimpleNamespace(launch=launch))

        await manager.open("a", "about:blank", browser_name="chromium", headless=True, timeout_ms=1000, wait_until="load")
        old_browser.disconnect()
        result = await manager.open("a", "about:blank", browser_name="chromium", headless=True, timeout_ms=1000, wait_until="load")

        assert result["ok"] is True
        assert launch.await_count == 2
        assert len(manager._pools) == 1
        assert manager._sessions["a"].pool is next(iter(manager._pools.values()))
        assert manager._sessions["a"].pool is not None
        assert manager._sessions["a"].pool.browser is new_browser
        old_context.close.assert_awaited_once()

    asyncio.run(run())


def test_concurrent_reopen_after_disconnect_publishes_one_new_pool() -> None:
    async def run() -> None:
        old_browser = FakeBrowser([_context(), _context()])
        new_browser = FakeBrowser([_context(), _context()])
        launch = AsyncMock(side_effect=[old_browser, new_browser])
        manager = BrowserManager(session_idle_sec=60, pool_idle_sec=60)
        manager._playwright = SimpleNamespace(chromium=SimpleNamespace(launch=launch))

        await asyncio.gather(
            manager.open("a", "about:blank", browser_name="chromium", headless=True, timeout_ms=1000, wait_until="load"),
            manager.open("b", "about:blank", browser_name="chromium", headless=True, timeout_ms=1000, wait_until="load"),
        )
        old_browser.disconnect()

        await asyncio.gather(
            manager.open("a", "about:blank", browser_name="chromium", headless=True, timeout_ms=1000, wait_until="load"),
            manager.open("b", "about:blank", browser_name="chromium", headless=True, timeout_ms=1000, wait_until="load"),
        )

        assert launch.await_count == 2
        assert len(manager._pools) == 1
        pool = next(iter(manager._pools.values()))
        assert pool.browser is new_browser
        assert pool.active_contexts == 2
        assert manager._sessions["a"].pool is pool
        assert manager._sessions["b"].pool is pool

    asyncio.run(run())


def test_disconnect_during_context_attach_does_not_publish_session() -> None:
    async def run() -> None:
        entered = asyncio.Event()
        release = asyncio.Event()
        context = _context()
        browser = FakeBrowser()

        async def new_context() -> SimpleNamespace:
            entered.set()
            await release.wait()
            return context

        browser.new_context = AsyncMock(side_effect=new_context)
        manager = BrowserManager(session_idle_sec=60, pool_idle_sec=60)
        manager._playwright = SimpleNamespace(chromium=SimpleNamespace(launch=AsyncMock(return_value=browser)))

        opening = asyncio.create_task(
            manager.open("a", "about:blank", browser_name="chromium", headless=True, timeout_ms=1000, wait_until="load")
        )
        await entered.wait()
        browser.disconnect()
        release.set()

        with pytest.raises(ToolError) as exc_info:
            await opening
        assert exc_info.value.code == "browser_disconnected"
        context.close.assert_awaited_once()
        assert not manager._sessions
        assert not manager._pools

    asyncio.run(run())
