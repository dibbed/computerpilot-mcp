import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from core.errors import ToolError
from tools.browser.manager import BrowserManager, PoolKey, Session
from tools.browser.semantics import build_snapshot, query_nodes


def test_snapshot_is_bounded_and_marks_truncation() -> None:
    async def run() -> None:
        page = SimpleNamespace(
            url="https://example.test/",
            title=AsyncMock(return_value="Example"),
            evaluate=AsyncMock(
                return_value=[
                    {"role": "button", "name": "Download report", "text": "Download report", "tag": "button"},
                    {"role": "textbox", "name": "Email", "label": "Email", "tag": "input"},
                    {"role": "link", "name": "More information", "text": "More information", "tag": "a"},
                ]
            ),
        )
        result = await build_snapshot(page, generation=7, page_id="p1", max_nodes=2, max_text_chars=200)
        assert result["generation"] == 7
        assert result["page_id"] == "p1"
        assert result["truncated"] is True
        assert result["node_count"] == 2
        assert [node["ref"] for node in result["nodes"]] == ["n1", "n2"]
        assert result["nodes"][0]["role"] == "button"
        assert result["nodes"][0]["name"] == "Download report"

    asyncio.run(run())


def test_query_nodes_matches_semantic_fields_without_returning_full_tree() -> None:
    nodes = [
        {"ref": "n1", "role": "button", "name": "Save", "label": None, "text": "Save", "test_id": None},
        {"ref": "n2", "role": "button", "name": "Cancel", "label": None, "text": "Cancel", "test_id": "cancel"},
        {"ref": "n3", "role": "textbox", "name": "Email", "label": "Email", "text": "", "test_id": None},
    ]
    result = query_nodes(nodes, role="button", name="Cancel", max_results=10)
    assert result["count"] == 1
    assert result["unique"] is True
    assert result["matches"][0]["ref"] == "n2"


def test_generation_scoped_refs_fail_closed_after_invalidation() -> None:
    manager = BrowserManager()
    page = SimpleNamespace(url="about:blank")
    session = Session(PoolKey("chromium", True), SimpleNamespace(), page, "chromium", True)
    manager._sessions["s"] = session

    generation = manager.semantic_generation("s")
    manager.remember_semantic_refs("s", generation, [{"ref": "n1", "role": "button", "name": "Save", "ordinal": 0}])
    assert manager.semantic_ref("s", "n1", generation)["name"] == "Save"

    manager.invalidate_semantics("s")
    with pytest.raises(ToolError) as exc_info:
        manager.semantic_ref("s", "n1", generation)
    assert exc_info.value.code == "browser_stale_ref"


def test_navigation_increments_generation_and_clears_refs() -> None:
    async def run() -> None:
        page = SimpleNamespace(
            set_default_timeout=lambda _: None,
            goto=AsyncMock(return_value=None),
            title=AsyncMock(return_value="title"),
            url="https://example.test/",
        )
        context = SimpleNamespace(new_page=AsyncMock(return_value=page), close=AsyncMock())
        browser = SimpleNamespace(new_context=AsyncMock(return_value=context), close=AsyncMock())
        manager = BrowserManager()
        manager._playwright = SimpleNamespace(chromium=SimpleNamespace(launch=AsyncMock(return_value=browser)))

        await manager.open("s", "https://example.test/", browser_name="chromium", headless=True, timeout_ms=1000, wait_until="load")
        first_generation = manager.semantic_generation("s")
        manager.remember_semantic_refs("s", first_generation, [{"ref": "n1", "role": "button", "name": "Save", "ordinal": 0}])

        await manager.open("s", "https://example.test/next", browser_name="chromium", headless=True, timeout_ms=1000, wait_until="load")
        assert manager.semantic_generation("s") > first_generation
        with pytest.raises(ToolError) as exc_info:
            manager.semantic_ref("s", "n1", first_generation)
        assert exc_info.value.code == "browser_stale_ref"

    asyncio.run(run())
