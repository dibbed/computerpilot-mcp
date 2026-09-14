"""MCP registration for lazy, selector-based Playwright operations."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Annotated, Any, Literal
from urllib.parse import urlsplit

from mcp.server import MCPServer
from pydantic import Field

from core.audit import audit_action
from core.config import SETTINGS, ensure_runtime_dirs, resolve_path
from core.tooling import OPEN_WORLD_READ, OPEN_WORLD_WRITE, compact_errors
from tools.browser.manager import MANAGER

SessionArg = Annotated[str, Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9_.-]+$")]
SelectorArg = Annotated[str, Field(min_length=1, max_length=10_000)]


def _safe_url_target(url: str) -> str:
    parts = urlsplit(url)
    if not parts.scheme:
        return "url"
    if not parts.hostname:
        return f"{parts.scheme}://"
    try:
        parsed_port = parts.port
    except ValueError:
        parsed_port = None
    port = f":{parsed_port}" if parsed_port else ""
    return f"{parts.scheme}://{parts.hostname}{port}"


def _url_result(url: str) -> dict[str, Any]:
    return {"url": url, "url_truncated": False}


def _selector_audit(selector: str) -> dict[str, Any]:
    return {
        "selector_chars": len(selector),
        "selector_sha256": hashlib.sha256(selector.encode("utf-8", errors="replace")).hexdigest(),
    }


def register(mcp: MCPServer) -> None:
    @mcp.tool(annotations=OPEN_WORLD_READ, structured_output=True)
    @compact_errors("browser_open_page")
    async def browser_open_page(
        url: Annotated[str, Field(min_length=1, max_length=8_192)],
        session_id: SessionArg = "default",
        browser: Literal["chromium", "firefox", "webkit"] = "chromium",
        headless: bool = True,
        wait_until: Literal["commit", "domcontentloaded", "load"] = "domcontentloaded",
        timeout_sec: Annotated[float, Field(gt=0, le=300)] = 30,
    ) -> dict[str, Any]:
        """Open or reuse a lazy Playwright session and return only page metadata."""

        audit_action(
            "browser_open_page",
            target=_safe_url_target(url),
            details={
                "session_id": session_id,
                "browser": browser,
                "headless": headless,
                "url_chars": len(url),
                "url_sha256": hashlib.sha256(url.encode("utf-8", errors="replace")).hexdigest(),
            },
        )
        return await MANAGER.open(
            session_id,
            url,
            browser_name=browser,
            headless=headless,
            timeout_ms=int(timeout_sec * 1_000),
            wait_until=wait_until,
        )

    @mcp.tool(annotations=OPEN_WORLD_WRITE, structured_output=True)
    @compact_errors("browser_click")
    async def browser_click(
        selector: SelectorArg,
        session_id: SessionArg = "default",
        button: Literal["left", "right", "middle"] = "left",
        click_count: Annotated[int, Field(ge=1, le=5)] = 1,
        timeout_sec: Annotated[float, Field(gt=0, le=300)] = 30,
    ) -> dict[str, Any]:
        """Click the first matching Playwright locator with actionability checks."""
        async with MANAGER.session(session_id):
            page = MANAGER.page(session_id)
            audit_action(
                "browser_click",
                target=_safe_url_target(page.url),
                details={"session_id": session_id, "button": button, **_selector_audit(selector)},
            )
            await page.locator(selector).first.click(button=button, click_count=click_count, timeout=timeout_sec * 1_000)
            return {"ok": True, "session_id": session_id, "clicked": True, "selector_chars": len(selector), **_url_result(page.url)}

    @mcp.tool(annotations=OPEN_WORLD_WRITE, structured_output=True)
    @compact_errors("browser_fill")
    async def browser_fill(
        selector: SelectorArg,
        text: Annotated[str, Field(max_length=100_000)],
        session_id: SessionArg = "default",
        timeout_sec: Annotated[float, Field(gt=0, le=300)] = 30,
    ) -> dict[str, Any]:
        """Fill the first matching editable locator without returning the supplied text."""
        async with MANAGER.session(session_id):
            page = MANAGER.page(session_id)
            audit_action(
                "browser_fill",
                target=_safe_url_target(page.url),
                details={"session_id": session_id, "text_chars": len(text), **_selector_audit(selector)},
            )
            await page.locator(selector).first.fill(text, timeout=timeout_sec * 1_000)
            return {
                "ok": True,
                "session_id": session_id,
                "filled": True,
                "selector_chars": len(selector),
                "text_chars": len(text),
                **_url_result(page.url),
            }

    @mcp.tool(annotations=OPEN_WORLD_WRITE, structured_output=True)
    @compact_errors("browser_screenshot")
    async def browser_screenshot(
        session_id: SessionArg = "default",
        path: Annotated[str | None, Field(max_length=32_767)] = None,
        selector: Annotated[str | None, Field(max_length=10_000)] = None,
        full_page: bool = False,
        image_type: Literal["png", "jpeg", "webp"] = "png",
        quality: Annotated[int | None, Field(ge=1, le=100)] = None,
        timeout_sec: Annotated[float, Field(gt=0, le=300)] = 30,
    ) -> dict[str, Any]:
        """Save one page or element screenshot and return its path and byte size."""
        async with MANAGER.session(session_id):
            ensure_runtime_dirs()
            page = MANAGER.page(session_id)
            if path:
                output = resolve_path(path)
            else:
                stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
                output = SETTINGS.screenshot_dir / f"browser_{session_id}_{stamp}.{image_type}"
            output.parent.mkdir(parents=True, exist_ok=True)
            kwargs: dict[str, Any] = {"path": output, "type": image_type, "timeout": timeout_sec * 1_000, "animations": "disabled"}
            if image_type in {"jpeg", "webp"} and quality is not None:
                kwargs["quality"] = quality
            if selector:
                await page.locator(selector).first.screenshot(**kwargs)
            else:
                kwargs["full_page"] = full_page
                await page.screenshot(**kwargs)
            audit_action("browser_screenshot", target=output, details={"session_id": session_id, "selector": bool(selector)})
            return {"ok": True, "session_id": session_id, "path": str(output), "bytes": output.stat().st_size, **_url_result(page.url)}

    @mcp.tool(annotations=OPEN_WORLD_WRITE, structured_output=True)
    @compact_errors("browser_close")
    async def browser_close(session_id: SessionArg = "default") -> dict[str, Any]:
        """Close one Playwright browser session and release its isolated context."""

        audit_action("browser_close", target=session_id)
        return await MANAGER.close(session_id)
