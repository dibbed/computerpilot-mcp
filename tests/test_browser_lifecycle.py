import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from tools.browser.manager import BrowserManager


def _page(*, goto: AsyncMock | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        set_default_timeout=lambda n: None,
        goto=goto or AsyncMock(return_value=None),
        title=AsyncMock(return_value="title"),
        url="about:blank",
    )


def test_same_session_same_config_reuses_context() -> None:
    async def run() -> None:
        manager = BrowserManager()
        page = _page()
        context = SimpleNamespace(new_page=AsyncMock(return_value=page), close=AsyncMock())
        browser = SimpleNamespace(new_context=AsyncMock(return_value=context), close=AsyncMock())
        launch = AsyncMock(return_value=browser)
        manager._playwright = SimpleNamespace(chromium=SimpleNamespace(launch=launch))

        await manager.open("same", "https://example.test/one", browser_name="chromium", headless=True, timeout_ms=1000, wait_until="load")
        original = manager._sessions["same"]
        await manager.open("same", "https://example.test/two", browser_name="chromium", headless=True, timeout_ms=1000, wait_until="load")

        assert manager._sessions["same"] is original
        launch.assert_awaited_once_with(headless=True)
        browser.new_context.assert_awaited_once()
        assert page.goto.await_count == 2
        context.close.assert_not_awaited()

    asyncio.run(run())


def test_sessions_keep_context_state_isolated() -> None:
    async def run() -> None:
        manager = BrowserManager()
        contexts: list[SimpleNamespace] = []

        def make_context() -> SimpleNamespace:
            state: dict[str, dict[str, str]] = {"cookies": {}, "local_storage": {}}
            context = SimpleNamespace(
                state=state,
                new_page=AsyncMock(return_value=_page()),
                close=AsyncMock(),
            )
            contexts.append(context)
            return context

        browser = SimpleNamespace(new_context=AsyncMock(side_effect=make_context), close=AsyncMock())
        manager._playwright = SimpleNamespace(chromium=SimpleNamespace(launch=AsyncMock(return_value=browser)))

        await manager.open("a", "about:blank", browser_name="chromium", headless=True, timeout_ms=1000, wait_until="load")
        await manager.open("b", "about:blank", browser_name="chromium", headless=True, timeout_ms=1000, wait_until="load")

        contexts[0].state["cookies"]["auth"] = "A"
        contexts[0].state["local_storage"]["token"] = "alpha"
        contexts[1].state["cookies"]["auth"] = "B"
        contexts[1].state["local_storage"]["token"] = "beta"

        assert contexts[0] is not contexts[1]
        assert contexts[0].state["cookies"] == {"auth": "A"}
        assert contexts[1].state["cookies"] == {"auth": "B"}
        assert contexts[0].state["local_storage"] == {"token": "alpha"}
        assert contexts[1].state["local_storage"] == {"token": "beta"}

    asyncio.run(run())


def test_incompatible_reopen_migrates_after_success() -> None:
    async def run() -> None:
        manager = BrowserManager()
        old_context = SimpleNamespace(new_page=AsyncMock(return_value=_page()), close=AsyncMock())
        new_context = SimpleNamespace(new_page=AsyncMock(return_value=_page()), close=AsyncMock())
        old_browser = SimpleNamespace(new_context=AsyncMock(return_value=old_context), close=AsyncMock())
        new_browser = SimpleNamespace(new_context=AsyncMock(return_value=new_context), close=AsyncMock())
        launch = AsyncMock(side_effect=[old_browser, new_browser])
        manager._playwright = SimpleNamespace(chromium=SimpleNamespace(launch=launch))

        await manager.open("migrate", "about:blank", browser_name="chromium", headless=True, timeout_ms=1000, wait_until="load")
        old_session = manager._sessions["migrate"]
        await manager.open("migrate", "about:blank", browser_name="chromium", headless=False, timeout_ms=1000, wait_until="load")

        current = manager._sessions["migrate"]
        assert current is not old_session
        assert current.context is new_context
        assert current.headless is False
        old_context.close.assert_awaited_once()
        new_context.close.assert_not_awaited()
        old_browser.close.assert_not_awaited()
        new_browser.close.assert_not_awaited()
        assert manager._pools[old_session.pool_key].active_contexts == 0
        assert manager._pools[current.pool_key].active_contexts == 1

    asyncio.run(run())


def test_failed_incompatible_reopen_preserves_existing_session() -> None:
    async def run() -> None:
        manager = BrowserManager()
        old_page = _page()
        failed_page = _page(goto=AsyncMock(side_effect=RuntimeError("replacement navigation failed")))
        old_context = SimpleNamespace(new_page=AsyncMock(return_value=old_page), close=AsyncMock())
        failed_context = SimpleNamespace(new_page=AsyncMock(return_value=failed_page), close=AsyncMock())
        old_browser = SimpleNamespace(new_context=AsyncMock(return_value=old_context), close=AsyncMock())
        replacement_browser = SimpleNamespace(new_context=AsyncMock(return_value=failed_context), close=AsyncMock())
        launch = AsyncMock(side_effect=[old_browser, replacement_browser])
        manager._playwright = SimpleNamespace(chromium=SimpleNamespace(launch=launch))

        await manager.open("stable", "about:blank", browser_name="chromium", headless=True, timeout_ms=1000, wait_until="load")
        original = manager._sessions["stable"]

        with pytest.raises(RuntimeError, match="replacement navigation failed"):
            await manager.open("stable", "about:blank", browser_name="chromium", headless=False, timeout_ms=1000, wait_until="load")

        assert manager._sessions["stable"] is original
        assert manager.page("stable") is old_page
        old_context.close.assert_not_awaited()
        failed_context.close.assert_awaited_once()
        assert manager._pools[original.pool_key].active_contexts == 1
        failed_pool = next(pool for key, pool in manager._pools.items() if key.headless is False)
        assert failed_pool.active_contexts == 0

    asyncio.run(run())


def test_failed_incompatible_context_creation_preserves_existing_session() -> None:
    async def run() -> None:
        manager = BrowserManager()
        old_page = _page()
        old_context = SimpleNamespace(new_page=AsyncMock(return_value=old_page), close=AsyncMock())
        old_browser = SimpleNamespace(new_context=AsyncMock(return_value=old_context), close=AsyncMock())
        replacement_browser = SimpleNamespace(new_context=AsyncMock(side_effect=RuntimeError("context failed")), close=AsyncMock())
        launch = AsyncMock(side_effect=[old_browser, replacement_browser])
        manager._playwright = SimpleNamespace(chromium=SimpleNamespace(launch=launch))

        await manager.open("stable", "about:blank", browser_name="chromium", headless=True, timeout_ms=1000, wait_until="load")
        original = manager._sessions["stable"]

        with pytest.raises(RuntimeError, match="context failed"):
            await manager.open("stable", "about:blank", browser_name="chromium", headless=False, timeout_ms=1000, wait_until="load")

        assert manager._sessions["stable"] is original
        assert manager.page("stable") is old_page
        old_context.close.assert_not_awaited()
        replacement_browser.close.assert_not_awaited()
        assert manager._pools[original.pool_key].active_contexts == 1
        failed_pool = next(pool for key, pool in manager._pools.items() if key.headless is False)
        assert failed_pool.active_contexts == 0

    asyncio.run(run())
