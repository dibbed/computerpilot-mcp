import asyncio
from collections.abc import Callable
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest

from tools.browser import registry
from tools.browser.manager import BrowserManager, PoolKey, Session

ToolFunction = Callable[..., Any]


class Capture:
    def __init__(self) -> None:
        self.functions: dict[str, ToolFunction] = {}

    def tool(self, **kwargs: Any) -> Callable[[ToolFunction], ToolFunction]:
        def decorate(fn: ToolFunction) -> ToolFunction:
            self.functions[fn.__name__] = fn
            return fn

        return decorate


def test_browser_snapshot_registers_refs_for_current_generation(monkeypatch: pytest.MonkeyPatch) -> None:
    async def run() -> None:
        page = SimpleNamespace(
            url="https://example.test/",
            title=AsyncMock(return_value="Example"),
            evaluate=AsyncMock(return_value=[{"role": "button", "name": "Save", "text": "Save", "tag": "button"}]),
        )
        manager = BrowserManager()
        manager._sessions["s"] = Session(PoolKey("chromium", True), SimpleNamespace(), page, "chromium", True, generation=3)
        monkeypatch.setattr(registry, "MANAGER", manager)
        monkeypatch.setattr(registry, "audit_action", lambda *a, **k: None)

        capture = Capture()
        registry.register(cast(Any, capture))
        result = await capture.functions["browser_snapshot"]("s", max_nodes=10, max_bytes=10_000)

        assert result["generation"] == 3
        assert result["nodes"][0]["ref"] == "n1"
        assert manager.semantic_ref("s", "n1", 3)["name"] == "Save"

    asyncio.run(run())


def test_browser_query_returns_only_matching_nodes_and_uniqueness(monkeypatch: pytest.MonkeyPatch) -> None:
    async def run() -> None:
        page = SimpleNamespace(
            url="https://example.test/",
            title=AsyncMock(return_value="Example"),
            evaluate=AsyncMock(
                return_value=[
                    {"role": "button", "name": "Save", "text": "Save", "tag": "button"},
                    {"role": "button", "name": "Cancel", "text": "Cancel", "tag": "button", "test_id": "cancel"},
                ]
            ),
        )
        manager = BrowserManager()
        manager._sessions["s"] = Session(PoolKey("chromium", True), SimpleNamespace(), page, "chromium", True, generation=4)
        monkeypatch.setattr(registry, "MANAGER", manager)
        monkeypatch.setattr(registry, "audit_action", lambda *a, **k: None)

        capture = Capture()
        registry.register(cast(Any, capture))
        result = await capture.functions["browser_query"](session_id="s", role="button", name="Cancel", max_results=10)

        assert result["generation"] == 4
        assert result["count"] == 1
        assert result["unique"] is True
        assert result["matches"][0]["name"] == "Cancel"
        assert "nodes" not in result
        assert manager.semantic_ref("s", result["matches"][0]["ref"], 4)["name"] == "Cancel"

    asyncio.run(run())


def test_semantic_click_uses_generation_ref_and_invalidates_snapshot(monkeypatch: pytest.MonkeyPatch) -> None:
    async def run() -> None:
        locator = SimpleNamespace(count=AsyncMock(return_value=1), click=AsyncMock())
        locator.nth = lambda _: locator
        page = SimpleNamespace(url="https://example.test/", get_by_role=lambda role, **kwargs: locator)
        manager = BrowserManager()
        manager._sessions["s"] = Session(PoolKey("chromium", True), SimpleNamespace(), page, "chromium", True, generation=5)
        manager.remember_semantic_refs(
            "s",
            5,
            [{"ref": "n1", "role": "button", "name": "Save", "label": None, "text": "Save", "test_id": None, "tag": "button", "ordinal": 0}],
        )
        monkeypatch.setattr(registry, "MANAGER", manager)
        monkeypatch.setattr(registry, "audit_action", lambda *a, **k: None)
        capture = Capture()
        registry.register(cast(Any, capture))

        result = await capture.functions["browser_click_semantic"](session_id="s", node_ref="n1", generation=5)

        assert result["ok"] is True
        locator.click.assert_awaited_once()
        assert result["generation"] == 6
        assert manager.semantic_generation("s") == 6

    asyncio.run(run())


def test_semantic_click_refuses_ambiguous_direct_target(monkeypatch: pytest.MonkeyPatch) -> None:
    async def run() -> None:
        locator = SimpleNamespace(count=AsyncMock(return_value=2), click=AsyncMock())
        locator.nth = lambda _: locator
        page = SimpleNamespace(url="https://example.test/", get_by_role=lambda role, **kwargs: locator)
        manager = BrowserManager()
        manager._sessions["s"] = Session(PoolKey("chromium", True), SimpleNamespace(), page, "chromium", True, generation=2)
        monkeypatch.setattr(registry, "MANAGER", manager)
        monkeypatch.setattr(registry, "audit_action", lambda *a, **k: None)
        capture = Capture()
        registry.register(cast(Any, capture))

        result = await capture.functions["browser_click_semantic"](session_id="s", role="button", name="Save")

        assert result["ok"] is False
        assert result["error"] == "browser_ambiguous_target"
        locator.click.assert_not_awaited()

    asyncio.run(run())


def test_semantic_fill_uses_label_and_redacts_value_from_result(monkeypatch: pytest.MonkeyPatch) -> None:
    async def run() -> None:
        locator = SimpleNamespace(count=AsyncMock(return_value=1), fill=AsyncMock())
        locator.nth = lambda _: locator
        page = SimpleNamespace(url="https://example.test/", get_by_label=lambda label, **kwargs: locator)
        manager = BrowserManager()
        manager._sessions["s"] = Session(PoolKey("chromium", True), SimpleNamespace(), page, "chromium", True, generation=9)
        monkeypatch.setattr(registry, "MANAGER", manager)
        monkeypatch.setattr(registry, "audit_action", lambda *a, **k: None)
        capture = Capture()
        registry.register(cast(Any, capture))

        result = await capture.functions["browser_fill_semantic"]("secret@example.test", session_id="s", label="Email")

        locator.fill.assert_awaited_once_with("secret@example.test", timeout=30_000.0)
        assert result["ok"] is True
        assert result["value_chars"] == len("secret@example.test")
        assert "secret@example.test" not in str(result)
        assert result["generation"] == 10

    asyncio.run(run())


def test_browser_wait_for_visible_uses_semantic_locator(monkeypatch: pytest.MonkeyPatch) -> None:
    async def run() -> None:
        locator = SimpleNamespace(wait_for=AsyncMock(), count=AsyncMock(return_value=1))
        locator.nth = lambda _: locator
        page = SimpleNamespace(url="https://example.test/", get_by_role=lambda role, **kwargs: locator)
        manager = BrowserManager()
        manager._sessions["s"] = Session(PoolKey("chromium", True), SimpleNamespace(), page, "chromium", True, generation=2)
        monkeypatch.setattr(registry, "MANAGER", manager)
        monkeypatch.setattr(registry, "audit_action", lambda *a, **k: None)
        capture = Capture()
        registry.register(cast(Any, capture))

        result = await capture.functions["browser_wait_for"](
            condition="visible",
            session_id="s",
            role="button",
            name="Continue",
            timeout_sec=2,
        )

        assert result["ok"] is True
        locator.wait_for.assert_awaited_once_with(state="visible", timeout=2000.0)

    asyncio.run(run())


def test_browser_wait_for_generation_change_observes_invalidation(monkeypatch: pytest.MonkeyPatch) -> None:
    async def run() -> None:
        page = SimpleNamespace(url="https://example.test/")
        manager = BrowserManager()
        manager._sessions["s"] = Session(PoolKey("chromium", True), SimpleNamespace(), page, "chromium", True, generation=4)
        monkeypatch.setattr(registry, "MANAGER", manager)
        monkeypatch.setattr(registry, "audit_action", lambda *a, **k: None)
        capture = Capture()
        registry.register(cast(Any, capture))

        async def mutate() -> None:
            await asyncio.sleep(0.02)
            manager.invalidate_semantics("s")

        task = asyncio.create_task(mutate())
        result = await capture.functions["browser_wait_for"](
            condition="generation_change",
            session_id="s",
            after_generation=4,
            timeout_sec=1,
        )
        await task

        assert result["ok"] is True
        assert result["generation"] == 5

    asyncio.run(run())


def test_browser_select_uses_semantic_target_and_invalidates_generation(monkeypatch: pytest.MonkeyPatch) -> None:
    async def run() -> None:
        locator = SimpleNamespace(count=AsyncMock(return_value=1), select_option=AsyncMock())
        locator.nth = lambda _: locator
        page = SimpleNamespace(url="https://example.test/", get_by_role=lambda role, **kwargs: locator)
        manager = BrowserManager()
        manager._sessions["s"] = Session(PoolKey("chromium", True), SimpleNamespace(), page, "chromium", True, generation=3)
        monkeypatch.setattr(registry, "MANAGER", manager)
        monkeypatch.setattr(registry, "audit_action", lambda *a, **k: None)
        capture = Capture()
        registry.register(cast(Any, capture))

        result = await capture.functions["browser_select"](
            session_id="s",
            role="combobox",
            name="Country",
            option_value="de",
        )

        locator.select_option.assert_awaited_once_with(value="de", timeout=30_000.0)
        assert result["ok"] is True
        assert result["generation"] == 4

    asyncio.run(run())


def test_browser_tabs_public_surface_lists_and_activates_stable_page_ids(monkeypatch: pytest.MonkeyPatch) -> None:
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
        manager._sessions["s"] = Session(PoolKey("chromium", True), context, page1, "chromium", True, generation=1)
        monkeypatch.setattr(registry, "MANAGER", manager)
        monkeypatch.setattr(registry, "audit_action", lambda *a, **k: None)
        capture = Capture()
        registry.register(cast(Any, capture))

        listed = await capture.functions["browser_tabs"](operation="list", session_id="s")
        assert [item["page_id"] for item in listed["tabs"]] == ["p1", "p2"]

        activated = await capture.functions["browser_tabs"](operation="activate", session_id="s", page_id="p2")
        assert activated["active_page_id"] == "p2"
        assert activated["generation"] == 2

    asyncio.run(run())
