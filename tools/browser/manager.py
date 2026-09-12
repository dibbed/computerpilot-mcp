"""Long-lived, lazily imported Playwright sessions."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, Literal

from core.errors import ToolError

BrowserName = Literal["chromium", "firefox", "webkit"]


@dataclass(slots=True)
class Session:
    browser: Any
    context: Any
    page: Any
    browser_name: str
    headless: bool


class BrowserManager:
    def __init__(self) -> None:
        self._playwright: Any = None
        self._sessions: dict[str, Session] = {}
        self._runtime_lock = asyncio.Lock()
        self._session_locks: dict[str, tuple[asyncio.Lock, int]] = {}

    @asynccontextmanager
    async def session(self, session_id: str) -> AsyncIterator[None]:
        # Map bookkeeping has no await and is atomic on the owning event loop.
        lock, users = self._session_locks.get(session_id, (asyncio.Lock(), 0))
        self._session_locks[session_id] = (lock, users + 1)
        try:
            async with lock:
                yield
        finally:
            users = self._session_locks[session_id][1] - 1
            if users:
                self._session_locks[session_id] = (lock, users)
            else:
                del self._session_locks[session_id]

    async def _runtime(self) -> Any:
        async with self._runtime_lock:
            if self._playwright is not None:
                return self._playwright
            try:
                from playwright.async_api import async_playwright
            except ImportError as exc:
                raise ToolError(
                    "playwright_not_installed",
                    "Optional Playwright support is not installed.",
                    hint=(
                        "Run .venv\\Scripts\\python.exe -m pip install -r requirements-browser.txt, "
                        "then python -m playwright install chromium."
                    ),
                ) from exc
            self._playwright = await async_playwright().start()
            return self._playwright

    async def open(
        self,
        session_id: str,
        url: str,
        *,
        browser_name: BrowserName,
        headless: bool,
        timeout_ms: int,
        wait_until: str,
    ) -> dict[str, Any]:
        async with self.session(session_id):
            existing = self._sessions.get(session_id)
            created = False
            if existing is None or existing.browser_name != browser_name or existing.headless != headless:
                if existing is not None:
                    self._sessions.pop(session_id)
                    await existing.browser.close()
                runtime = await self._runtime()
                try:
                    browser = await getattr(runtime, browser_name).launch(headless=headless)
                except Exception as exc:
                    message = str(exc)
                    if "Executable doesn't exist" in message or "playwright install" in message:
                        raise ToolError(
                            "browser_runtime_missing",
                            f"{browser_name} runtime is not installed.",
                            hint=f"Run .venv\\Scripts\\python.exe -m playwright install {browser_name}.",
                        ) from exc
                    raise
                try:
                    context = await browser.new_context()
                    page = await context.new_page()
                except BaseException:
                    await browser.close()
                    raise
                existing = Session(browser=browser, context=context, page=page, browser_name=browser_name, headless=headless)
                self._sessions[session_id] = existing
                created = True
            try:
                existing.page.set_default_timeout(timeout_ms)
                response = await existing.page.goto(url, wait_until=wait_until, timeout=timeout_ms)
                return {
                    "ok": True,
                    "session_id": session_id,
                    "url": existing.page.url,
                    "url_truncated": False,
                    "title": await existing.page.title(),
                    "status": response.status if response else None,
                    "browser": browser_name,
                    "headless": headless,
                }
            except BaseException:
                if created:
                    self._sessions.pop(session_id, None)
                    await existing.browser.close()
                raise

    def page(self, session_id: str) -> Any:
        session = self._sessions.get(session_id)
        if session is None:
            raise ToolError(
                "browser_session_not_found",
                f"Browser session {session_id!r} is not open.",
                hint="Call browser_open_page first.",
            )
        return session.page

    async def close(self, session_id: str) -> dict[str, Any]:
        async with self.session(session_id):
            session = self._sessions.pop(session_id, None)
            if session is None:
                return {"ok": True, "session_id": session_id, "closed": False, "reason": "not_found"}
            await session.browser.close()
            return {"ok": True, "session_id": session_id, "closed": True}


MANAGER = BrowserManager()
