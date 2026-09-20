"""MCP registration for Language Server Protocol intelligence."""

from __future__ import annotations

import shutil
from collections.abc import Callable
from typing import Annotated, Any

from mcp.server import MCPServer
from pydantic import Field

from core.audit import audit_action
from core.config import resolve_path
from core.tooling import MUTATING, OPEN_WORLD_READ, compact_errors
from tools.language.edits import apply_workspace_edit, prepare_workspace_edit
from tools.language.lsp import LspClient


def _command() -> list[str]:
    """Return the trusted/discovered language-server command exposed by MCP."""

    discovered = shutil.which("pyright-langserver")
    return [discovered or "pyright-langserver", "--stdio"]


def _request(root: str, timeout_sec: float, method: str, params: dict[str, Any]) -> Any:
    return LspClient(_command(), resolve_path(root), timeout_sec=timeout_sec).request(method, params)


def register(mcp: MCPServer) -> None:
    def common(method: str, operation: str) -> Callable[..., dict[str, Any]]:
        @mcp.tool(name=operation, annotations=OPEN_WORLD_READ, structured_output=True)
        @compact_errors(operation)
        def tool(
            root: Annotated[str, Field(min_length=1, max_length=32_767)],
            params: Annotated[dict[str, Any], Field(max_length=100)],
            timeout_sec: Annotated[float, Field(gt=0, le=300)] = 30,
        ) -> dict[str, Any]:
            """Run one bounded request through the trusted discovered language server."""
            return {"ok": True, "method": method, "result": _request(root, timeout_sec, method, params)}

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
    def rename_symbol(
        root: Annotated[str, Field(min_length=1, max_length=32_767)],
        document_uri: Annotated[str, Field(min_length=1, max_length=32_767)],
        line: Annotated[int, Field(ge=0)],
        character: Annotated[int, Field(ge=0)],
        new_name: Annotated[str, Field(min_length=1, max_length=500)],
        expected_sha256: Annotated[dict[str, str] | None, Field(max_length=500)] = None,
        dry_run: bool = False,
        timeout_sec: Annotated[float, Field(gt=0, le=300)] = 30,
    ) -> dict[str, Any]:
        """Rename a symbol through a language server and transactionally apply its workspace edit."""
        workspace = resolve_path(root)
        edit = _request(
            workspace.as_posix(),
            timeout_sec,
            "textDocument/rename",
            {
                "textDocument": {"uri": document_uri},
                "position": {"line": line, "character": character},
                "newName": new_name,
            },
        )
        plan = prepare_workspace_edit(workspace, edit, expected_sha256=expected_sha256)
        audit_action("rename_symbol", target=workspace, details={"file_count": len(plan), "dry_run": dry_run})
        if dry_run:
            return {"ok": True, "dry_run": True, "file_count": len(plan), "files": [str(item.path) for item in plan]}
        return {**apply_workspace_edit(plan), "dry_run": False}

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
