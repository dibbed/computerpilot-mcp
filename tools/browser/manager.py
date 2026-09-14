"""Long-lived, lazily imported Playwright sessions."""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, Literal

from core.config import SETTINGS
from core.errors import ToolError

BrowserName = Literal["chromium", "firefox", "webkit"]


@dataclass(frozen=True, slots=True)
class PoolKey:
    browser_name: BrowserName
    headless: bool
    launch_config: tuple[tuple[str, str], ...] = ()


@dataclass(slots=True)
class BrowserPool:
    key: PoolKey
    browser: Any
    active_contexts: int = 0
    pending_contexts: int = 0
    last_used: float = 0.0


@dataclass(slots=True)
class Session:
    pool_key: PoolKey
    context: Any
    page: Any
    browser_name: BrowserName
    headless: bool
    last_used: float = 0.0


class BrowserManager:
    def __init__(
        self,
        *,
        session_idle_sec: float | None = None,
        pool_idle_sec: float | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._playwright: Any = None
        self._sessions: dict[str, Session] = {}
        self._pools: dict[PoolKey, BrowserPool] = {}
        self._runtime_lock = asyncio.Lock()
        self._pool_lock = asyncio.Lock()
        self._cleanup_lock = asyncio.Lock()
        self._session_locks: dict[str, tuple[asyncio.Lock, int]] = {}
        self._cleanup_task: asyncio.Task[None] | None = None
        self._session_idle_sec = float(SETTINGS.browser_idle_sec if session_idle_sec is None else session_idle_sec)
        self._pool_idle_sec = float(SETTINGS.browser_pool_idle_sec if pool_idle_sec is None else pool_idle_sec)
        self._cleanup_interval_sec = max(0.01, min(30.0, self._session_idle_sec / 2, self._pool_idle_sec / 2))
        self._clock = clock

    def _touch_session(self, session_id: str) -> None:
        session = self._sessions.get(session_id)
        if session is not None:
            session.last_used = self._clock()

    @asynccontextmanager
    async def session(self, session_id: str, *, touch: bool = True) -> AsyncIterator[None]:
        # Map bookkeeping has no await and is atomic on the owning event loop.
        lock, users = self._session_locks.get(session_id, (asyncio.Lock(), 0))
        self._session_locks[session_id] = (lock, users + 1)
        try:
            async with lock:
                if touch:
                    self._touch_session(session_id)
                try:
                    yield
                finally:
                    if touch:
                        self._touch_session(session_id)
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

    @staticmethod
    def _pool_key(browser_name: BrowserName, headless: bool) -> PoolKey:
        # launch_config is intentionally part of PoolKey so future launch-affecting
        # options cannot accidentally reuse an incompatible browser process.
        return PoolKey(browser_name=browser_name, headless=headless)

    def _ensure_cleanup_task(self) -> None:
        if self._cleanup_task is None or self._cleanup_task.done():
            self._cleanup_task = asyncio.create_task(self._cleanup_loop(), name="browser-idle-cleanup")

    async def _cleanup_loop(self) -> None:
        try:
            while self._pools:
                await asyncio.sleep(self._cleanup_interval_sec)
                await self.cleanup_idle()
        except asyncio.CancelledError:
            raise
        finally:
            current = asyncio.current_task()
            if self._cleanup_task is current:
                self._cleanup_task = None

    async def _acquire_pool_for_context(self, browser_name: BrowserName, headless: bool) -> BrowserPool:
        key = self._pool_key(browser_name, headless)
        runtime = await self._runtime()
        async with self._pool_lock:
            existing = self._pools.get(key)
            if existing is not None:
                existing.pending_contexts += 1
                existing.last_used = self._clock()
                return existing
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
            pool = BrowserPool(
                key=key,
                browser=browser,
                pending_contexts=1,
                last_used=self._clock(),
            )
            self._pools[key] = pool
            self._ensure_cleanup_task()
            return pool

    async def _new_session(
        self,
        *,
        browser_name: BrowserName,
        headless: bool,
    ) -> Session:
        pool = await self._acquire_pool_for_context(browser_name, headless)
        context: Any = None
        try:
            context = await pool.browser.new_context()
            page = await context.new_page()
        except BaseException:
            if context is not None:
                try:
                    await context.close()
                except Exception:
                    pass
            raise
        finally:
            async with self._pool_lock:
                pool.pending_contexts = max(0, pool.pending_contexts - 1)
                pool.last_used = self._clock()
        async with self._pool_lock:
            pool.active_contexts += 1
            pool.last_used = self._clock()
        return Session(
            pool_key=pool.key,
            context=context,
            page=page,
            browser_name=browser_name,
            headless=headless,
            last_used=self._clock(),
        )

    async def _close_session_context(self, session: Session) -> None:
        try:
            await session.context.close()
        finally:
            async with self._pool_lock:
                pool = self._pools.get(session.pool_key)
                if pool is not None:
                    if pool.active_contexts > 0:
                        pool.active_contexts -= 1
                    pool.last_used = self._clock()

    @staticmethod
    def _compatible(session: Session, *, browser_name: BrowserName, headless: bool) -> bool:
        return session.pool_key == BrowserManager._pool_key(browser_name, headless)

    @staticmethod
    async def _navigate(session: Session, url: str, *, timeout_ms: int, wait_until: str) -> dict[str, Any]:
        session.page.set_default_timeout(timeout_ms)
        response = await session.page.goto(url, wait_until=wait_until, timeout=timeout_ms)
        return {
            "ok": True,
            "url": session.page.url,
            "url_truncated": False,
            "title": await session.page.title(),
            "status": response.status if response else None,
            "browser": session.browser_name,
            "headless": session.headless,
        }

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
            if existing is not None and self._compatible(existing, browser_name=browser_name, headless=headless):
                result = await self._navigate(existing, url, timeout_ms=timeout_ms, wait_until=wait_until)
                existing.last_used = self._clock()
                return {"session_id": session_id, **result}

            replacement = await self._new_session(browser_name=browser_name, headless=headless)
            try:
                result = await self._navigate(replacement, url, timeout_ms=timeout_ms, wait_until=wait_until)
            except BaseException:
                await self._close_session_context(replacement)
                raise

            # Publish only after the replacement has navigated successfully. This keeps
            # the existing session usable if launch/context/navigation fails.
            replacement.last_used = self._clock()
            self._sessions[session_id] = replacement
            if existing is not None:
                await self._close_session_context(existing)
            return {"session_id": session_id, **result}

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
            await self._close_session_context(session)
            return {"ok": True, "session_id": session_id, "closed": True}

    async def cleanup_idle(self) -> dict[str, Any]:
        if self._cleanup_lock.locked():
            return {"ok": True, "skipped": True, "reason": "cleanup_in_progress"}

        async with self._cleanup_lock:
            evicted_sessions = 0
            now = self._clock()

            for session_id, candidate in list(self._sessions.items()):
                if now - candidate.last_used < self._session_idle_sec:
                    continue
                async with self.session(session_id, touch=False):
                    current = self._sessions.get(session_id)
                    if current is not candidate:
                        continue
                    if self._clock() - current.last_used < self._session_idle_sec:
                        continue
                    self._sessions.pop(session_id, None)
                    await self._close_session_context(current)
                    evicted_sessions += 1

            pools_to_close: list[BrowserPool] = []
            now = self._clock()
            async with self._pool_lock:
                for key, pool in list(self._pools.items()):
                    if pool.active_contexts != 0 or pool.pending_contexts != 0:
                        continue
                    if now - pool.last_used < self._pool_idle_sec:
                        continue
                    if self._pools.get(key) is pool:
                        self._pools.pop(key)
                        pools_to_close.append(pool)

            closed_pools = 0
            for pool in pools_to_close:
                await pool.browser.close()
                closed_pools += 1

            return {
                "ok": True,
                "skipped": False,
                "evicted_sessions": evicted_sessions,
                "closed_pools": closed_pools,
                "active_sessions": len(self._sessions),
                "active_pools": len(self._pools),
            }


MANAGER = BrowserManager()
