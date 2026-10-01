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
