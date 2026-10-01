import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from tools.browser.manager import BrowserManager, PoolKey, Session


def test_tabs_keep_stable_page_ids_across_discovery() -> None:
    async def run() -> None:
        page1 = SimpleNamespace(url="https://one.test/", title=AsyncMock(return_value="One"), is_closed=lambda: False)
        page2 = SimpleNamespace(url="https://two.test/", title=AsyncMock(return_value="Two"), is_closed=lambda: False)
        context = SimpleNamespace(pages=[page1, page2])
        manager = BrowserManager()
        manager._sessions["s"] = Session(PoolKey("chromium", True), context, page1, "chromium", True)

        first = await manager.tabs("s")
        second = await manager.tabs("s")

        assert [item["page_id"] for item in first] == ["p1", "p2"]
        assert [item["page_id"] for item in second] == ["p1", "p2"]
        assert first[0]["active"] is True
        assert first[1]["active"] is False

    asyncio.run(run())


def test_activate_tab_switches_active_page_and_invalidates_generation() -> None:
    async def run() -> None:
        page1 = SimpleNamespace(
            url="https://one.test/",
            title=AsyncMock(return_value="One"),
            is_closed=lambda: False,
            bring_to_front=AsyncMock(),
        )
        page2 = SimpleNamespace(
            url="https://two.test/",
            title=AsyncMock(return_value="Two"),
            is_closed=lambda: False,
            bring_to_front=AsyncMock(),
        )
        context = SimpleNamespace(pages=[page1, page2])
        manager = BrowserManager()
        manager._sessions["s"] = Session(PoolKey("chromium", True), context, page1, "chromium", True, generation=4)

        tabs = await manager.tabs("s")
        result = await manager.activate_tab("s", tabs[1]["page_id"])

        assert result["active_page_id"] == "p2"
        assert manager.page("s") is page2
        assert manager.semantic_generation("s") == 5
        page2.bring_to_front.assert_awaited_once()

    asyncio.run(run())


def test_close_tab_selects_remaining_page() -> None:
    async def run() -> None:
        page1 = SimpleNamespace(
            url="https://one.test/",
            title=AsyncMock(return_value="One"),
            is_closed=lambda: False,
            close=AsyncMock(),
        )
        page2 = SimpleNamespace(
            url="https://two.test/",
            title=AsyncMock(return_value="Two"),
            is_closed=lambda: False,
            close=AsyncMock(),
        )
        pages = [page1, page2]

        async def close_page2() -> None:
            pages.remove(page2)

        page2.close.side_effect = close_page2
        context = SimpleNamespace(pages=pages)
        manager = BrowserManager()
        manager._sessions["s"] = Session(PoolKey("chromium", True), context, page2, "chromium", True, generation=7)

        tabs = await manager.tabs("s")
        result = await manager.close_tab("s", tabs[1]["page_id"])

        assert result["closed"] is True
        assert result["active_page_id"] == "p1"
        assert manager.page("s") is page1
        assert manager.semantic_generation("s") == 8

    asyncio.run(run())
