import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from tools.browser.manager import BrowserManager


class Clock:
    def __init__(self, value: float = 0.0) -> None:
        self.value = value

    def __call__(self) -> float:
        return self.value


def _browser(*, context_close: AsyncMock | None = None) -> tuple[SimpleNamespace, SimpleNamespace]:
    page = SimpleNamespace(
        set_default_timeout=lambda n: None,
        goto=AsyncMock(return_value=None),
        title=AsyncMock(return_value="title"),
        url="about:blank",
    )
    context = SimpleNamespace(
        new_page=AsyncMock(return_value=page),
        close=context_close or AsyncMock(),
    )
    browser = SimpleNamespace(new_context=AsyncMock(return_value=context), close=AsyncMock())
    return browser, context


def test_idle_session_context_is_reclaimed() -> None:
    async def run() -> None:
        clock = Clock()
        manager = BrowserManager(session_idle_sec=10, clock=clock)
        browser, context = _browser()
        manager._playwright = SimpleNamespace(chromium=SimpleNamespace(launch=AsyncMock(return_value=browser)))

        await manager.open("a", "about:blank", browser_name="chromium", headless=True, timeout_ms=1000, wait_until="load")
        pool = next(iter(manager._pools.values()))
        assert pool.active_contexts == 1

        clock.value = 11
        result = await manager.cleanup_idle()
        assert result["evicted_sessions"] == 1
        assert "a" not in manager._sessions
        context.close.assert_awaited_once()
        browser.close.assert_not_awaited()
        assert pool.active_contexts == 0
        assert len(manager._pools) == 1

    asyncio.run(run())


def test_active_session_is_not_evicted_while_operation_finishes() -> None:
    async def run() -> None:
        clock = Clock()
        manager = BrowserManager(session_idle_sec=10, clock=clock)
        browser, context = _browser()
        manager._playwright = SimpleNamespace(chromium=SimpleNamespace(launch=AsyncMock(return_value=browser)))
        await manager.open("a", "about:blank", browser_name="chromium", headless=True, timeout_ms=1000, wait_until="load")

        entered = asyncio.Event()
        release = asyncio.Event()

        async def active_operation() -> None:
            clock.value = 5
            async with manager.session("a"):
                entered.set()
                await release.wait()

        task = asyncio.create_task(active_operation())
        await entered.wait()
        clock.value = 20
        cleanup = asyncio.create_task(manager.cleanup_idle())
        await asyncio.sleep(0)
        assert not cleanup.done()

        release.set()
        await task
        result = await cleanup
        assert result["evicted_sessions"] == 0
        assert "a" in manager._sessions
        assert manager._sessions["a"].last_used == 20
        context.close.assert_not_awaited()

    asyncio.run(run())


def test_cleanup_is_single_flight() -> None:
    async def run() -> None:
        clock = Clock()
        close_entered = asyncio.Event()
        close_release = asyncio.Event()

        async def close_context() -> None:
            close_entered.set()
            await close_release.wait()

        manager = BrowserManager(session_idle_sec=1, clock=clock)
        browser, _ = _browser(context_close=AsyncMock(side_effect=close_context))
        manager._playwright = SimpleNamespace(chromium=SimpleNamespace(launch=AsyncMock(return_value=browser)))
        await manager.open("a", "about:blank", browser_name="chromium", headless=True, timeout_ms=1000, wait_until="load")

        clock.value = 2
        first = asyncio.create_task(manager.cleanup_idle())
        await close_entered.wait()
        second = await manager.cleanup_idle()
        assert second == {"ok": True, "skipped": True, "reason": "cleanup_in_progress"}

        close_release.set()
        first_result = await first
        assert first_result["evicted_sessions"] == 1

    asyncio.run(run())


def test_background_cleanup_reclaims_idle_context() -> None:
    async def run() -> None:
        context_closed = asyncio.Event()

        async def close_context() -> None:
            context_closed.set()

        manager = BrowserManager(session_idle_sec=0.03)
        browser, _ = _browser(context_close=AsyncMock(side_effect=close_context))
        manager._playwright = SimpleNamespace(chromium=SimpleNamespace(launch=AsyncMock(return_value=browser)))

        await manager.open("a", "about:blank", browser_name="chromium", headless=True, timeout_ms=1000, wait_until="load")
        await asyncio.wait_for(context_closed.wait(), 0.5)
        assert not manager._sessions
        assert len(manager._pools) == 1
        browser.close.assert_not_awaited()

    asyncio.run(run())
