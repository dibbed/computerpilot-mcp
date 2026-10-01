"""MCP registration for lazy, selector-based Playwright operations."""

from __future__ import annotations

import asyncio
import hashlib
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Any, Literal
from urllib.parse import urlsplit

from mcp.server import MCPServer
from pydantic import Field

from core.audit import audit_action
from core.config import SETTINGS, ensure_runtime_dirs, resolve_path
from core.errors import ToolError
from core.media import ImageDelivery, image_tool_result
from core.response import bounded_text
from core.tooling import OPEN_WORLD_READ, OPEN_WORLD_WRITE, compact_errors
from tools.browser.manager import MANAGER
from tools.browser.semantics import bound_records, build_snapshot, query_nodes

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


_WINDOWS_RESERVED_NAMES = {
    "con",
    "prn",
    "aux",
    "nul",
    *(f"com{index}" for index in range(1, 10)),
    *(f"lpt{index}" for index in range(1, 10)),
}


def _sanitize_download_filename(value: str) -> str:
    name = Path(value).name.strip()
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip(" .")
    if not name:
        name = "download.bin"
    stem = name.split(".", 1)[0].casefold()
    if stem in _WINDOWS_RESERVED_NAMES:
        name = f"_{name}"
    if len(name) > 180:
        suffix = Path(name).suffix[:20]
        name = f"{Path(name).stem[: max(1, 180 - len(suffix))]}{suffix}"
    return name


def _make_semantic_locator(
    page: Any,
    *,
    node: dict[str, Any] | None = None,
    role: str | None = None,
    name: str | None = None,
    label: str | None = None,
    text: str | None = None,
    test_id: str | None = None,
) -> tuple[Any, dict[str, Any]]:
    target = dict(node or {})
    role = str(target.get("role") or role or "") or None
    name = str(target.get("name") or name or "") or None
    label = str(target.get("label") or label or "") or None
    text = str(target.get("text") or text or "") or None
    test_id = str(target.get("test_id") or test_id or "") or None

    if test_id is not None:
        locator = page.get_by_test_id(test_id)
        kind = "test_id"
        target_value = test_id
    elif role is not None and name is not None:
        locator = page.get_by_role(role, name=name, exact=True)
        kind = "role_name"
        target_value = f"{role}:{name}"
    elif label is not None:
        locator = page.get_by_label(label, exact=True)
        kind = "label"
        target_value = label
    elif text is not None:
        locator = page.get_by_text(text, exact=True)
        kind = "text"
        target_value = text
    elif role is not None:
        locator = page.get_by_role(role)
        kind = "role"
        target_value = role
    else:
        raise ToolError(
            "browser_semantic_target_invalid",
            "No usable semantic target was provided.",
            hint="Use node_ref+generation, role+name, label, text, or test_id.",
        )

    return locator, {
        "target_kind": kind,
        "target_chars": len(target_value),
        "target_sha256": hashlib.sha256(target_value.encode("utf-8", errors="replace")).hexdigest(),
    }


async def _resolve_semantic_locator(
    page: Any,
    *,
    node: dict[str, Any] | None = None,
    role: str | None = None,
    name: str | None = None,
    label: str | None = None,
    text: str | None = None,
    test_id: str | None = None,
    match_index: int | None = None,
) -> tuple[Any, dict[str, Any]]:
    locator, evidence = _make_semantic_locator(
        page,
        node=node,
        role=role,
        name=name,
        label=label,
        text=text,
        test_id=test_id,
    )
    count = int(await locator.count())
    if count < 1:
        code = "browser_stale_ref" if node is not None else "browser_target_not_found"
        raise ToolError(
            code,
            "No element currently matches the semantic target.",
            hint="Take a new browser_snapshot/browser_query and retry.",
        )

    if node is not None:
        index = int(node.get("ordinal", 0))
        if index >= count:
            raise ToolError(
                "browser_stale_ref",
                "The semantic target no longer resolves to the snapshotted occurrence.",
                hint="Take a new browser_snapshot and retry.",
            )
    elif match_index is None:
        if count != 1:
            raise ToolError(
                "browser_ambiguous_target",
                f"Semantic target matched {count} elements.",
                hint="Narrow the semantic target, use browser_query, or provide match_index.",
            )
        index = 0
    else:
        index = match_index
        if index >= count:
            raise ToolError(
                "browser_target_not_found",
                f"match_index {index} is outside the {count} matching elements.",
                hint="Use browser_query to inspect the available matches.",
            )

    return locator.nth(index), {
        **evidence,
        "match_count": count,
        "match_index": index,
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

    @mcp.tool(annotations=OPEN_WORLD_READ, structured_output=True)
    @compact_errors("browser_snapshot")
    async def browser_snapshot(
        session_id: SessionArg = "default",
        max_nodes: Annotated[int, Field(ge=1, le=5_000)] = SETTINGS.browser_semantic_max_nodes,
        max_bytes: Annotated[int, Field(ge=1_024, le=4 * 1_024 * 1_024)] = SETTINGS.browser_semantic_max_bytes,
    ) -> dict[str, Any]:
        """Return a bounded semantic representation of the active browser page."""

        async with MANAGER.session(session_id):
            page = MANAGER.page(session_id)
            generation = MANAGER.semantic_generation(session_id)
            result = await build_snapshot(
                page,
                generation=generation,
                page_id="p1",
                max_nodes=max_nodes,
                max_text_chars=max_bytes,
            )
            MANAGER.remember_semantic_refs(session_id, generation, result["nodes"])
            audit_action(
                "browser_snapshot",
                target=_safe_url_target(page.url),
                details={
                    "session_id": session_id,
                    "generation": generation,
                    "node_count": result["node_count"],
                    "truncated": result["truncated"],
                },
            )
            return result

    @mcp.tool(annotations=OPEN_WORLD_READ, structured_output=True)
    @compact_errors("browser_query")
    async def browser_query(
        session_id: SessionArg = "default",
        role: Annotated[str | None, Field(max_length=100)] = None,
        name: Annotated[str | None, Field(max_length=2_000)] = None,
        label: Annotated[str | None, Field(max_length=2_000)] = None,
        text: Annotated[str | None, Field(max_length=4_000)] = None,
        test_id: Annotated[str | None, Field(max_length=500)] = None,
        max_results: Annotated[int, Field(ge=1, le=500)] = 50,
    ) -> dict[str, Any]:
        """Find semantic elements without returning the entire page snapshot."""

        if all(value is None for value in (role, name, label, text, test_id)):
            raise ToolError(
                "browser_query_invalid",
                "At least one semantic query field is required.",
                hint="Provide role, name, label, text, or test_id.",
            )
        async with MANAGER.session(session_id):
            page = MANAGER.page(session_id)
            generation = MANAGER.semantic_generation(session_id)
            snapshot = await build_snapshot(
                page,
                generation=generation,
                page_id="p1",
                max_nodes=SETTINGS.browser_semantic_max_nodes,
                max_text_chars=SETTINGS.browser_semantic_max_bytes,
            )
            MANAGER.remember_semantic_refs(session_id, generation, snapshot["nodes"])
            result = query_nodes(
                snapshot["nodes"],
                role=role,
                name=name,
                label=label,
                text=text,
                test_id=test_id,
                max_results=max_results,
            )
            audit_action(
                "browser_query",
                target=_safe_url_target(page.url),
                details={
                    "session_id": session_id,
                    "generation": generation,
                    "match_count": result["count"],
                    "unique": result["unique"],
                },
            )
            return {
                "ok": True,
                "session_id": session_id,
                "page_id": snapshot["page_id"],
                "generation": generation,
                **result,
            }

    @mcp.tool(annotations=OPEN_WORLD_WRITE, structured_output=True)
    @compact_errors("browser_click_semantic")
    async def browser_click_semantic(
        session_id: SessionArg = "default",
        node_ref: Annotated[str | None, Field(max_length=100)] = None,
        generation: Annotated[int | None, Field(ge=0)] = None,
        role: Annotated[str | None, Field(max_length=100)] = None,
        name: Annotated[str | None, Field(max_length=2_000)] = None,
        label: Annotated[str | None, Field(max_length=2_000)] = None,
        text: Annotated[str | None, Field(max_length=4_000)] = None,
        test_id: Annotated[str | None, Field(max_length=500)] = None,
        match_index: Annotated[int | None, Field(ge=0, le=10_000)] = None,
        button: Literal["left", "right", "middle"] = "left",
        click_count: Annotated[int, Field(ge=1, le=5)] = 1,
        timeout_sec: Annotated[float, Field(gt=0, le=300)] = 30,
    ) -> dict[str, Any]:
        """Click a semantic browser target and invalidate snapshot-scoped refs."""

        async with MANAGER.session(session_id):
            page = MANAGER.page(session_id)
            node = None
            if node_ref is not None:
                if generation is None:
                    raise ToolError(
                        "browser_generation_required",
                        "generation is required when node_ref is used.",
                        hint="Pass the generation returned by browser_snapshot/browser_query.",
                    )
                node = MANAGER.semantic_ref(session_id, node_ref, generation)
            locator, evidence = await _resolve_semantic_locator(
                page,
                node=node,
                role=role,
                name=name,
                label=label,
                text=text,
                test_id=test_id,
                match_index=match_index,
            )
            audit_action(
                "browser_click_semantic",
                target=_safe_url_target(page.url),
                details={"session_id": session_id, "button": button, **evidence},
            )
            await locator.click(button=button, click_count=click_count, timeout=timeout_sec * 1_000)
            new_generation = MANAGER.invalidate_semantics(session_id)
            return {
                "ok": True,
                "session_id": session_id,
                "clicked": True,
                "generation": new_generation,
                "evidence": evidence,
                **_url_result(page.url),
            }

    @mcp.tool(annotations=OPEN_WORLD_WRITE, structured_output=True)
    @compact_errors("browser_fill_semantic")
    async def browser_fill_semantic(
        value: Annotated[str, Field(max_length=100_000)],
        session_id: SessionArg = "default",
        node_ref: Annotated[str | None, Field(max_length=100)] = None,
        generation: Annotated[int | None, Field(ge=0)] = None,
        role: Annotated[str | None, Field(max_length=100)] = None,
        name: Annotated[str | None, Field(max_length=2_000)] = None,
        label: Annotated[str | None, Field(max_length=2_000)] = None,
        test_id: Annotated[str | None, Field(max_length=500)] = None,
        match_index: Annotated[int | None, Field(ge=0, le=10_000)] = None,
        mode: Literal["replace", "clear"] = "replace",
        timeout_sec: Annotated[float, Field(gt=0, le=300)] = 30,
    ) -> dict[str, Any]:
        """Fill a semantic editable target without exposing the supplied value."""

        async with MANAGER.session(session_id):
            page = MANAGER.page(session_id)
            node = None
            if node_ref is not None:
                if generation is None:
                    raise ToolError(
                        "browser_generation_required",
                        "generation is required when node_ref is used.",
                        hint="Pass the generation returned by browser_snapshot/browser_query.",
                    )
                node = MANAGER.semantic_ref(session_id, node_ref, generation)
            locator, evidence = await _resolve_semantic_locator(
                page,
                node=node,
                role=role,
                name=name,
                label=label,
                test_id=test_id,
                match_index=match_index,
            )
            audit_action(
                "browser_fill_semantic",
                target=_safe_url_target(page.url),
                details={"session_id": session_id, "value_chars": len(value), "mode": mode, **evidence},
            )
            supplied = "" if mode == "clear" else value
            await locator.fill(supplied, timeout=timeout_sec * 1_000)
            new_generation = MANAGER.invalidate_semantics(session_id)
            return {
                "ok": True,
                "session_id": session_id,
                "filled": True,
                "mode": mode,
                "value_chars": len(value),
                "generation": new_generation,
                "evidence": evidence,
                **_url_result(page.url),
            }

    @mcp.tool(annotations=OPEN_WORLD_READ, structured_output=True)
    @compact_errors("browser_extract")
    async def browser_extract(
        mode: Literal["text", "links", "form_fields", "table", "subtree"],
        session_id: SessionArg = "default",
        node_ref: Annotated[str | None, Field(max_length=100)] = None,
        generation: Annotated[int | None, Field(ge=0)] = None,
        role: Annotated[str | None, Field(max_length=100)] = None,
        name: Annotated[str | None, Field(max_length=2_000)] = None,
        label: Annotated[str | None, Field(max_length=2_000)] = None,
        text: Annotated[str | None, Field(max_length=4_000)] = None,
        test_id: Annotated[str | None, Field(max_length=500)] = None,
        match_index: Annotated[int | None, Field(ge=0, le=10_000)] = None,
        max_items: Annotated[int, Field(ge=1, le=5_000)] = 500,
        max_chars: Annotated[int, Field(ge=1, le=4 * 1_024 * 1_024)] = SETTINGS.browser_semantic_max_bytes,
    ) -> dict[str, Any]:
        """Extract bounded structured content from the active semantic browser page."""

        async with MANAGER.session(session_id):
            page = MANAGER.page(session_id)
            current_generation = MANAGER.semantic_generation(session_id)

            if mode == "text":
                raw_text = str(await page.locator("body").inner_text())
                data: dict[str, Any] = bounded_text(raw_text, max_chars, mode="head")
            elif mode == "links":
                records = await page.evaluate(
                    """() => Array.from(document.querySelectorAll('a[href]')).map((a) => ({
                        text: (a.innerText || a.textContent || '').trim(),
                        href: a.href
                    }))"""
                )
                data = bound_records(list(records or []), max_items=max_items, max_text_chars=max_chars)
            elif mode == "table":
                records = await page.evaluate(
                    """() => Array.from(document.querySelectorAll('table')).flatMap((table, tableIndex) =>
                        Array.from(table.rows).map((row, rowIndex) => ({
                            table_index: tableIndex,
                            row_index: rowIndex,
                            cells: Array.from(row.cells).map((cell) => (cell.innerText || cell.textContent || '').trim())
                        }))
                    )"""
                )
                data = bound_records(list(records or []), max_items=max_items, max_text_chars=max_chars)
            elif mode == "form_fields":
                snapshot = await build_snapshot(
                    page,
                    generation=current_generation,
                    page_id="p1",
                    max_nodes=SETTINGS.browser_semantic_max_nodes,
                    max_text_chars=max_chars,
                )
                records = [
                    node
                    for node in snapshot["nodes"]
                    if node.get("tag") in {"input", "textarea", "select"}
                    or node.get("role") in {"textbox", "checkbox", "radio", "combobox", "spinbutton", "slider"}
                ]
                data = bound_records(records, max_items=max_items, max_text_chars=max_chars)
            else:
                node = None
                if node_ref is not None:
                    if generation is None:
                        raise ToolError(
                            "browser_generation_required",
                            "generation is required when node_ref is used.",
                            hint="Pass the generation returned by browser_snapshot/browser_query.",
                        )
                    node = MANAGER.semantic_ref(session_id, node_ref, generation)
                locator, _ = await _resolve_semantic_locator(
                    page,
                    node=node,
                    role=role,
                    name=name,
                    label=label,
                    text=text,
                    test_id=test_id,
                    match_index=match_index,
                )
                record = await locator.evaluate(
                    """(el) => ({
                        tag: el.tagName.toLowerCase(),
                        role: el.getAttribute('role'),
                        name: el.getAttribute('aria-label'),
                        text: (el.innerText || el.textContent || '').trim()
                    })"""
                )
                data = bound_records([record], max_items=1, max_text_chars=max_chars)

            audit_action(
                "browser_extract",
                target=_safe_url_target(page.url),
                details={
                    "session_id": session_id,
                    "mode": mode,
                    "truncated": bool(data.get("truncated", False)),
                },
            )
            return {
                "ok": True,
                "session_id": session_id,
                "mode": mode,
                "generation": current_generation,
                "data": data,
                **_url_result(page.url),
            }

    @mcp.tool(annotations=OPEN_WORLD_WRITE, structured_output=True)
    @compact_errors("browser_select")
    async def browser_select(
        session_id: SessionArg = "default",
        node_ref: Annotated[str | None, Field(max_length=100)] = None,
        generation: Annotated[int | None, Field(ge=0)] = None,
        role: Annotated[str | None, Field(max_length=100)] = None,
        name: Annotated[str | None, Field(max_length=2_000)] = None,
        label: Annotated[str | None, Field(max_length=2_000)] = None,
        test_id: Annotated[str | None, Field(max_length=500)] = None,
        match_index: Annotated[int | None, Field(ge=0, le=10_000)] = None,
        option_value: Annotated[str | None, Field(max_length=10_000)] = None,
        option_label: Annotated[str | None, Field(max_length=10_000)] = None,
        selection_mode: Literal["native", "listbox"] = "native",
        timeout_sec: Annotated[float, Field(gt=0, le=300)] = 30,
    ) -> dict[str, Any]:
        """Select an option through a deterministic semantic control."""

        if (option_value is None) == (option_label is None):
            raise ToolError(
                "browser_select_invalid",
                "Exactly one of option_value or option_label is required.",
                hint="Use option_value for native selects or option_label for a visible semantic option.",
            )
        if selection_mode == "listbox" and option_label is None:
            raise ToolError(
                "browser_select_invalid",
                "listbox mode requires option_label.",
                hint="Provide the visible option label for a semantic listbox.",
            )

        async with MANAGER.session(session_id):
            page = MANAGER.page(session_id)
            node = None
            if node_ref is not None:
                if generation is None:
                    raise ToolError(
                        "browser_generation_required",
                        "generation is required when node_ref is used.",
                        hint="Pass the generation returned by browser_snapshot/browser_query.",
                    )
                node = MANAGER.semantic_ref(session_id, node_ref, generation)
            locator, evidence = await _resolve_semantic_locator(
                page,
                node=node,
                role=role,
                name=name,
                label=label,
                test_id=test_id,
                match_index=match_index,
            )

            if selection_mode == "native":
                if option_value is not None:
                    await locator.select_option(value=option_value, timeout=timeout_sec * 1_000)
                    option_chars = len(option_value)
                else:
                    await locator.select_option(label=option_label, timeout=timeout_sec * 1_000)
                    option_chars = len(option_label or "")
            else:
                await locator.click(timeout=timeout_sec * 1_000)
                option = page.get_by_role("option", name=option_label, exact=True)
                count = int(await option.count())
                if count != 1:
                    raise ToolError(
                        "browser_ambiguous_target",
                        f"Listbox option matched {count} elements.",
                        hint="Use a unique visible option label.",
                    )
                await option.nth(0).click(timeout=timeout_sec * 1_000)
                option_chars = len(option_label or "")

            audit_action(
                "browser_select",
                target=_safe_url_target(page.url),
                details={
                    "session_id": session_id,
                    "selection_mode": selection_mode,
                    "option_chars": option_chars,
                    **evidence,
                },
            )
            new_generation = MANAGER.invalidate_semantics(session_id)
            return {
                "ok": True,
                "session_id": session_id,
                "selected": True,
                "selection_mode": selection_mode,
                "generation": new_generation,
                "evidence": evidence,
                **_url_result(page.url),
            }

    @mcp.tool(annotations=OPEN_WORLD_WRITE, structured_output=True)
    @compact_errors("browser_upload")
    async def browser_upload(
        path: Annotated[str, Field(min_length=1, max_length=32_767)],
        session_id: SessionArg = "default",
        node_ref: Annotated[str | None, Field(max_length=100)] = None,
        generation: Annotated[int | None, Field(ge=0)] = None,
        role: Annotated[str | None, Field(max_length=100)] = None,
        name: Annotated[str | None, Field(max_length=2_000)] = None,
        label: Annotated[str | None, Field(max_length=2_000)] = None,
        test_id: Annotated[str | None, Field(max_length=500)] = None,
        match_index: Annotated[int | None, Field(ge=0, le=10_000)] = None,
        timeout_sec: Annotated[float, Field(gt=0, le=300)] = 30,
    ) -> dict[str, Any]:
        """Attach one explicitly named local file to a semantic file input."""

        source = resolve_path(path)
        if not source.exists():
            raise ToolError(
                "browser_upload_not_found",
                f"Upload source does not exist: {source}",
                hint="Provide an explicit path to an existing regular file.",
            )
        if not source.is_file():
            raise ToolError(
                "browser_upload_not_file",
                f"Upload source is not a regular file: {source}",
                hint="Directories and implicit file discovery are not allowed.",
            )
        size = source.stat().st_size

        async with MANAGER.session(session_id):
            page = MANAGER.page(session_id)
            node = None
            if node_ref is not None:
                if generation is None:
                    raise ToolError(
                        "browser_generation_required",
                        "generation is required when node_ref is used.",
                        hint="Pass the generation returned by browser_snapshot/browser_query.",
                    )
                node = MANAGER.semantic_ref(session_id, node_ref, generation)
            locator, evidence = await _resolve_semantic_locator(
                page,
                node=node,
                role=role,
                name=name,
                label=label,
                test_id=test_id,
                match_index=match_index,
            )
            audit_action(
                "browser_upload",
                target=_safe_url_target(page.url),
                details={
                    "session_id": session_id,
                    "file_bytes": size,
                    "path_chars": len(str(source)),
                    "path_sha256": hashlib.sha256(str(source).encode("utf-8", errors="replace")).hexdigest(),
                    **evidence,
                },
            )
            await locator.set_input_files(str(source), timeout=timeout_sec * 1_000)
            new_generation = MANAGER.invalidate_semantics(session_id)
            return {
                "ok": True,
                "session_id": session_id,
                "uploaded": True,
                "path": str(source),
                "file_name": source.name,
                "bytes": size,
                "generation": new_generation,
                "evidence": evidence,
                **_url_result(page.url),
            }

    @mcp.tool(annotations=OPEN_WORLD_WRITE, structured_output=True)
    @compact_errors("browser_download")
    async def browser_download(
        session_id: SessionArg = "default",
        node_ref: Annotated[str | None, Field(max_length=100)] = None,
        generation: Annotated[int | None, Field(ge=0)] = None,
        role: Annotated[str | None, Field(max_length=100)] = None,
        name: Annotated[str | None, Field(max_length=2_000)] = None,
        label: Annotated[str | None, Field(max_length=2_000)] = None,
        text: Annotated[str | None, Field(max_length=4_000)] = None,
        test_id: Annotated[str | None, Field(max_length=500)] = None,
        match_index: Annotated[int | None, Field(ge=0, le=10_000)] = None,
        timeout_sec: Annotated[float, Field(gt=0, le=300)] = 30,
    ) -> dict[str, Any]:
        """Click a semantic target and save the resulting download under controlled state storage."""

        output: Path | None = None
        async with MANAGER.session(session_id):
            page = MANAGER.page(session_id)
            node = None
            if node_ref is not None:
                if generation is None:
                    raise ToolError(
                        "browser_generation_required",
                        "generation is required when node_ref is used.",
                        hint="Pass the generation returned by browser_snapshot/browser_query.",
                    )
                node = MANAGER.semantic_ref(session_id, node_ref, generation)
            locator, evidence = await _resolve_semantic_locator(
                page,
                node=node,
                role=role,
                name=name,
                label=label,
                text=text,
                test_id=test_id,
                match_index=match_index,
            )
            output_dir = SETTINGS.browser_download_dir
            output_dir.mkdir(parents=True, exist_ok=True)
            try:
                async with page.expect_download(timeout=timeout_sec * 1_000) as download_info:
                    await locator.click(timeout=timeout_sec * 1_000)
                download = await download_info.value
                safe_name = _sanitize_download_filename(str(download.suggested_filename))
                stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
                output = output_dir / f"{stamp}_{safe_name}"
                await download.save_as(str(output))
                size = output.stat().st_size
                if size > SETTINGS.browser_download_max_bytes:
                    output.unlink(missing_ok=True)
                    raise ToolError(
                        "browser_download_too_large",
                        f"Download size {size} exceeds the configured limit {SETTINGS.browser_download_max_bytes}.",
                        hint="Increase MCP_BROWSER_DOWNLOAD_MAX_BYTES only when the larger file is expected.",
                    )
            except BaseException:
                if output is not None:
                    output.unlink(missing_ok=True)
                raise

            audit_action(
                "browser_download",
                target=_safe_url_target(page.url),
                details={
                    "session_id": session_id,
                    "download_bytes": size,
                    "suggested_name_chars": len(str(download.suggested_filename)),
                    **evidence,
                },
            )
            new_generation = MANAGER.invalidate_semantics(session_id)
            return {
                "ok": True,
                "session_id": session_id,
                "downloaded": True,
                "path": str(output),
                "file_name": output.name,
                "suggested_filename": safe_name,
                "bytes": size,
                "generation": new_generation,
                "evidence": evidence,
                **_url_result(page.url),
            }

    @mcp.tool(annotations=OPEN_WORLD_WRITE, structured_output=True)
    @compact_errors("browser_tabs")
    async def browser_tabs(
        operation: Literal["list", "activate", "close"] = "list",
        session_id: SessionArg = "default",
        page_id: Annotated[str | None, Field(max_length=100)] = None,
    ) -> dict[str, Any]:
        """List, activate, or close deterministic page IDs within one browser session."""

        if operation == "list":
            tabs = await MANAGER.tabs(session_id)
            return {"ok": True, "session_id": session_id, "tabs": tabs, "count": len(tabs)}
        if page_id is None:
            raise ToolError(
                "browser_tab_id_required",
                f"page_id is required for browser_tabs operation {operation!r}.",
                hint="Call browser_tabs(operation='list') first.",
            )
        if operation == "activate":
            result = await MANAGER.activate_tab(session_id, page_id)
        else:
            result = await MANAGER.close_tab(session_id, page_id)
        audit_action("browser_tabs", target=session_id, details={"operation": operation, "page_id": page_id})
        return result

    @mcp.tool(annotations=OPEN_WORLD_READ, structured_output=True)
    @compact_errors("browser_wait_for")
    async def browser_wait_for(
        condition: Literal["exists", "visible", "enabled", "hidden", "text", "url", "network_idle", "generation_change"],
        session_id: SessionArg = "default",
        node_ref: Annotated[str | None, Field(max_length=100)] = None,
        generation: Annotated[int | None, Field(ge=0)] = None,
        role: Annotated[str | None, Field(max_length=100)] = None,
        name: Annotated[str | None, Field(max_length=2_000)] = None,
        label: Annotated[str | None, Field(max_length=2_000)] = None,
        text: Annotated[str | None, Field(max_length=4_000)] = None,
        test_id: Annotated[str | None, Field(max_length=500)] = None,
        match_index: Annotated[int | None, Field(ge=0, le=10_000)] = None,
        url_pattern: Annotated[str | None, Field(max_length=8_192)] = None,
        after_generation: Annotated[int | None, Field(ge=0)] = None,
        timeout_sec: Annotated[float, Field(gt=0, le=300)] = 30,
    ) -> dict[str, Any]:
        """Wait for a bounded browser condition instead of sleeping blindly."""

        timeout_ms = timeout_sec * 1_000
        if condition == "generation_change":
            if after_generation is None:
                raise ToolError(
                    "browser_wait_invalid",
                    "after_generation is required for generation_change.",
                    hint="Pass the generation you previously observed.",
                )
            deadline = asyncio.get_running_loop().time() + timeout_sec
            while True:
                current = MANAGER.semantic_generation(session_id)
                if current != after_generation:
                    return {"ok": True, "session_id": session_id, "condition": condition, "generation": current}
                if asyncio.get_running_loop().time() >= deadline:
                    raise ToolError("browser_wait_timeout", "Browser generation did not change before timeout.")
                await asyncio.sleep(min(0.05, max(0.001, timeout_sec / 20)))

        async with MANAGER.session(session_id):
            page = MANAGER.page(session_id)
            if condition == "url":
                if url_pattern is None:
                    raise ToolError("browser_wait_invalid", "url_pattern is required for url waits.")
                await page.wait_for_url(url_pattern, timeout=timeout_ms)
                evidence: dict[str, Any] = {"url_pattern_chars": len(url_pattern)}
            elif condition == "network_idle":
                await page.wait_for_load_state("networkidle", timeout=timeout_ms)
                evidence = {}
            else:
                node = None
                if node_ref is not None:
                    if generation is None:
                        raise ToolError(
                            "browser_generation_required",
                            "generation is required when node_ref is used.",
                            hint="Pass the generation returned by browser_snapshot/browser_query.",
                        )
                    node = MANAGER.semantic_ref(session_id, node_ref, generation)
                locator, target_evidence = _make_semantic_locator(
                    page,
                    node=node,
                    role=role,
                    name=name,
                    label=label,
                    text=text,
                    test_id=test_id,
                )
                if match_index is not None:
                    locator = locator.nth(match_index)
                elif node is not None:
                    locator = locator.nth(int(node.get("ordinal", 0)))
                elif condition not in {"hidden"}:
                    count = int(await locator.count())
                    if count > 1:
                        raise ToolError(
                            "browser_ambiguous_target",
                            f"Semantic target matched {count} elements.",
                            hint="Narrow the target or provide match_index.",
                        )
                if condition == "exists":
                    await locator.wait_for(state="attached", timeout=timeout_ms)
                elif condition == "visible" or condition == "text":
                    await locator.wait_for(state="visible", timeout=timeout_ms)
                elif condition == "hidden":
                    await locator.wait_for(state="hidden", timeout=timeout_ms)
                elif condition == "enabled":
                    deadline = asyncio.get_running_loop().time() + timeout_sec
                    while not await locator.is_enabled():
                        if asyncio.get_running_loop().time() >= deadline:
                            raise ToolError("browser_wait_timeout", "Semantic target did not become enabled before timeout.")
                        await asyncio.sleep(0.05)
                evidence = target_evidence

            current_generation = MANAGER.semantic_generation(session_id)
            audit_action(
                "browser_wait_for",
                target=_safe_url_target(page.url),
                details={"session_id": session_id, "condition": condition, **evidence},
            )
            return {
                "ok": True,
                "session_id": session_id,
                "condition": condition,
                "generation": current_generation,
                **_url_result(page.url),
            }

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
            generation = MANAGER.invalidate_semantics(session_id)
            return {
                "ok": True,
                "session_id": session_id,
                "clicked": True,
                "selector_chars": len(selector),
                "generation": generation,
                **_url_result(page.url),
            }

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
            generation = MANAGER.invalidate_semantics(session_id)
            return {
                "ok": True,
                "session_id": session_id,
                "filled": True,
                "selector_chars": len(selector),
                "text_chars": len(text),
                "generation": generation,
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
        delivery: ImageDelivery = "path",
        timeout_sec: Annotated[float, Field(gt=0, le=300)] = 30,
    ) -> dict[str, Any]:
        """Capture a page/element screenshot; default path mode pairs reliably with view_image."""
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
            metadata = {"ok": True, "session_id": session_id, "path": str(output), "bytes": output.stat().st_size, **_url_result(page.url)}
            return image_tool_result(metadata, output, delivery=delivery)

    @mcp.tool(annotations=OPEN_WORLD_WRITE, structured_output=True)
    @compact_errors("browser_close")
    async def browser_close(session_id: SessionArg = "default") -> dict[str, Any]:
        """Close one Playwright browser session and release its isolated context."""

        audit_action("browser_close", target=session_id)
        return await MANAGER.close(session_id)
