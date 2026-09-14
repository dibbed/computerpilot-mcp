import asyncio
from collections.abc import Callable
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest

from tools.browser import registry
from tools.browser.manager import BrowserManager, PoolKey, Session

ToolFunction = Callable[..., Any]


def test_click_keeps_session_locked_through_action(monkeypatch: pytest.MonkeyPatch) -> None:
    async def run() -> None:
        functions: dict[str, ToolFunction] = {}

        class Capture:
            def tool(self, **kwargs: Any) -> Callable[[ToolFunction], ToolFunction]:
                def decorate(fn: ToolFunction) -> ToolFunction:
                    functions[fn.__name__] = fn
                    return fn

                return decorate

        manager = BrowserManager()
        entered, release = asyncio.Event(), asyncio.Event()

        async def click(**kwargs: object) -> None:
            entered.set()
            await release.wait()

        page = SimpleNamespace(url="about:blank", locator=lambda _: SimpleNamespace(first=SimpleNamespace(click=click)))
        context = SimpleNamespace(close=AsyncMock())
        manager._sessions["a"] = Session(PoolKey("chromium", True), context, page, "chromium", True)
        monkeypatch.setattr(registry, "MANAGER", manager)
        monkeypatch.setattr(registry, "audit_action", lambda *a, **k: None)
        registry.register(cast(Any, Capture()))
        task = asyncio.create_task(functions["browser_click"]("button", session_id="a"))
        await entered.wait()
        close = asyncio.create_task(functions["browser_close"]("a"))
        await asyncio.sleep(0)
        assert not close.done()
        context.close.assert_not_awaited()
        release.set()
        assert (await task)["ok"]
        assert (await close)["closed"]
        context.close.assert_awaited_once()
        assert not manager._session_locks

    asyncio.run(run())
