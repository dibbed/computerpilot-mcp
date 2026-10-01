import asyncio
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest

from tests.test_browser_semantic_registry import Capture
from tools.browser import registry
from tools.browser.manager import BrowserManager, PoolKey, Session


def test_browser_extract_text_is_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    async def run() -> None:
        body = SimpleNamespace(inner_text=AsyncMock(return_value="x" * 100))
        page = SimpleNamespace(url="https://example.test/", locator=lambda selector: body)
        manager = BrowserManager()
        manager._sessions["s"] = Session(PoolKey("chromium", True), SimpleNamespace(), page, "chromium", True, generation=2)
        monkeypatch.setattr(registry, "MANAGER", manager)
        monkeypatch.setattr(registry, "audit_action", lambda *a, **k: None)
        capture = Capture()
        registry.register(cast(Any, capture))

        result = await capture.functions["browser_extract"](
            mode="text",
            session_id="s",
            max_chars=20,
        )

        assert result["ok"] is True
        assert result["data"]["text"] == "x" * 20
        assert result["data"]["truncated"] is True
        assert result["generation"] == 2

    asyncio.run(run())


def test_browser_extract_links_returns_bounded_records(monkeypatch: pytest.MonkeyPatch) -> None:
    async def run() -> None:
        page = SimpleNamespace(
            url="https://example.test/",
            evaluate=AsyncMock(
                return_value=[
                    {"text": "One", "href": "https://one.test/"},
                    {"text": "Two", "href": "https://two.test/"},
                    {"text": "Three", "href": "https://three.test/"},
                ]
            ),
        )
        manager = BrowserManager()
        manager._sessions["s"] = Session(PoolKey("chromium", True), SimpleNamespace(), page, "chromium", True, generation=4)
        monkeypatch.setattr(registry, "MANAGER", manager)
        monkeypatch.setattr(registry, "audit_action", lambda *a, **k: None)
        capture = Capture()
        registry.register(cast(Any, capture))

        result = await capture.functions["browser_extract"](
            mode="links",
            session_id="s",
            max_items=2,
            max_chars=1000,
        )

        assert result["data"]["count"] == 2
        assert result["data"]["total_count"] == 3
        assert result["data"]["truncated"] is True
        assert result["data"]["items"][0]["text"] == "One"

    asyncio.run(run())


def test_browser_extract_subtree_uses_generation_scoped_ref(monkeypatch: pytest.MonkeyPatch) -> None:
    async def run() -> None:
        locator = SimpleNamespace(
            count=AsyncMock(return_value=1),
            evaluate=AsyncMock(return_value={"tag": "section", "text": "Details", "role": "region"}),
        )
        locator.nth = lambda _: locator
        page = SimpleNamespace(url="https://example.test/", get_by_role=lambda role, **kwargs: locator)
        manager = BrowserManager()
        manager._sessions["s"] = Session(PoolKey("chromium", True), SimpleNamespace(), page, "chromium", True, generation=8)
        manager.remember_semantic_refs(
            "s",
            8,
            [{"ref": "n1", "role": "region", "name": "Details", "text": "Details", "ordinal": 0}],
        )
        monkeypatch.setattr(registry, "MANAGER", manager)
        monkeypatch.setattr(registry, "audit_action", lambda *a, **k: None)
        capture = Capture()
        registry.register(cast(Any, capture))

        result = await capture.functions["browser_extract"](
            mode="subtree",
            session_id="s",
            node_ref="n1",
            generation=8,
            max_chars=1000,
        )

        assert result["data"]["items"][0]["text"] == "Details"
        locator.evaluate.assert_awaited_once()

    asyncio.run(run())
