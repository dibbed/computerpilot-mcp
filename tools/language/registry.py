"""MCP registration for Language Server Protocol intelligence."""

from __future__ import annotations

import asyncio
import shutil
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Annotated, Any

import anyio
from mcp.server import MCPServer
from pydantic import Field

from core.audit import audit_action
from core.config import SETTINGS, resolve_path
from core.errors import ToolError
from core.tooling import MUTATING, OPEN_WORLD_READ, compact_errors
from tools.language.edits import apply_workspace_edit, prepare_workspace_edit
from tools.language.lsp import LspClient
from tools.language.requests import validate_request
from tools.language.sessions import LspSessionManager


def _command() -> list[str]:
    """Return the trusted/discovered language-server command exposed by MCP."""

    discovered = shutil.which("pyright-langserver")
    return [discovered or "pyright-langserver", "--stdio"]


async def _run_sync(call: Callable[[], Any]) -> Any:
    cancelled = False
    try:
        with anyio.CancelScope(shield=True):
            worker = asyncio.create_task(anyio.to_thread.run_sync(call, abandon_on_cancel=False))
            while True:
                try:
                    return await asyncio.shield(worker)
                except asyncio.CancelledError:
                    if worker.cancelled():
                        raise
                    cancelled = True
    finally:
        if cancelled:
            raise asyncio.CancelledError


async def _request(
    root: str, timeout_sec: float, method: str, params: dict[str, Any], *,
    manager: LspSessionManager | None = None, language_id: str | None = None,
) -> Any:
    deadline = time.monotonic() + timeout_sec
    if manager is not None:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ToolError("lsp_timeout", "Language request deadline expired before dispatch.")
        return await manager.arequest(Path(root), language_id or "python", method, params, timeout_sec=remaining)

    def legacy_request() -> Any:
        workspace = resolve_path(root)
        validated = validate_request(workspace, method, params)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ToolError("lsp_timeout", "Language request deadline expired before dispatch.")
        return LspClient(_command(), workspace, timeout_sec=remaining).request(method, validated)

    return await _run_sync(legacy_request)


def register(mcp: MCPServer, *, manager: LspSessionManager | None = None) -> None:
    if SETTINGS.persistent_enabled and manager is None:
        raise ToolError("lsp_configuration_invalid", "Persistent language tools require a server-owned session manager.")

    def common(method: str, operation: str) -> Callable[..., Awaitable[dict[str, Any]]]:
        @mcp.tool(name=operation, annotations=OPEN_WORLD_READ, structured_output=True)
        @compact_errors(operation)
        async def tool(
            root: Annotated[str, Field(min_length=1, max_length=32_767)],
            params: Annotated[dict[str, Any], Field(max_length=100)],
            timeout_sec: Annotated[float, Field(gt=0, le=300)] = 30,
        ) -> dict[str, Any]:
            """Run one bounded request through the trusted discovered language server."""
            result = await _request(root, timeout_sec, method, params, manager=manager)
            return {"ok": True, "method": method, "result": result}

        return tool

    common("textDocument/definition", "symbol_definition")
    common("textDocument/references", "symbol_references")
    common("textDocument/documentSymbol", "document_symbols")
    common("workspace/symbol", "workspace_symbols")
    common("textDocument/hover", "symbol_hover")
    common("textDocument/prepareCallHierarchy", "call_hierarchy")
    common("textDocument/diagnostic", "language_diagnostics")

    @mcp.tool(annotations=MUTATING, structured_output=True)
    @compact_errors("rename_symbol")
    async def rename_symbol(
        root: Annotated[str, Field(min_length=1, max_length=32_767)],
        document_uri: Annotated[str, Field(min_length=1, max_length=32_767)],
        line: Annotated[int, Field(ge=0)],
        character: Annotated[int, Field(ge=0)],
        new_name: Annotated[str, Field(min_length=1, max_length=500)],
        expected_sha256: Annotated[dict[str, str], Field(max_length=500)],
        dry_run: bool = False,
        timeout_sec: Annotated[float, Field(gt=0, le=300)] = 30,
    ) -> dict[str, Any]:
        """Rename a symbol with required per-file SHA-256 guards and transactionally apply its workspace edit."""
        workspace = await _run_sync(lambda: resolve_path(root))
        edit = await _request(
            workspace.as_posix(),
            timeout_sec,
            "textDocument/rename",
            {
                "textDocument": {"uri": document_uri},
                "position": {"line": line, "character": character},
                "newName": new_name,
            },
            manager=manager,
        )
        def apply_rename() -> dict[str, Any]:
            plan = prepare_workspace_edit(
                workspace,
                edit,
                expected_sha256=expected_sha256,
                require_expected_sha256=True,
            )
            audit_action("rename_symbol", target=workspace, details={"file_count": len(plan), "dry_run": dry_run})
            if dry_run:
                return {"ok": True, "dry_run": True, "file_count": len(plan), "files": [str(item.path) for item in plan]}
            return {**apply_workspace_edit(plan), "dry_run": False}

        return await _run_sync(apply_rename)

    @mcp.tool(annotations=MUTATING, structured_output=True)
    @compact_errors("apply_code_action")
    def apply_code_action(
        root: Annotated[str, Field(min_length=1, max_length=32_767)],
        action: Annotated[dict[str, Any], Field(max_length=200)],
        expected_sha256: Annotated[dict[str, str] | None, Field(max_length=500)] = None,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """Apply only the workspace edit from an LSP code action; returned commands are never executed."""
        workspace = resolve_path(root)
        edit = action.get("edit")
        if not isinstance(edit, dict):
            return {"ok": False, "applied": False, "command_ignored": bool(action.get("command")), "reason": "no_workspace_edit"}
        plan = prepare_workspace_edit(
            workspace,
            edit,
            expected_sha256=expected_sha256,
            require_expected_sha256=True,
        )
        if dry_run:
            return {"ok": True, "dry_run": True, "file_count": len(plan), "command_ignored": bool(action.get("command"))}
        return {**apply_workspace_edit(plan), "dry_run": False, "command_ignored": bool(action.get("command"))}
