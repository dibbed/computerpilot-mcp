"""MCP registration for Language Server Protocol intelligence."""

from __future__ import annotations

import shutil
from collections.abc import Callable
from typing import Annotated, Any

from mcp.server import MCPServer
from pydantic import Field

from core.config import resolve_path
from core.tooling import OPEN_WORLD_READ, compact_errors
from tools.language.lsp import LspClient


def _command(command: list[str] | None) -> list[str]:
    if command:
        return command
    discovered = shutil.which("pyright-langserver")
    return [discovered or "pyright-langserver", "--stdio"]


def _request(root: str, command: list[str] | None, timeout_sec: float, method: str, params: dict[str, Any]) -> Any:
    return LspClient(_command(command), resolve_path(root), timeout_sec=timeout_sec).request(method, params)


def register(mcp: MCPServer) -> None:
    def common(method: str, operation: str) -> Callable[..., dict[str, Any]]:
        @mcp.tool(name=operation, annotations=OPEN_WORLD_READ, structured_output=True)
        @compact_errors(operation)
        def tool(
            root: Annotated[str, Field(min_length=1, max_length=32_767)],
            params: Annotated[dict[str, Any], Field(max_length=100)],
            command: Annotated[list[str] | None, Field(max_length=20)] = None,
            timeout_sec: Annotated[float, Field(gt=0, le=300)] = 30,
        ) -> dict[str, Any]:
            """Run one bounded language-server intelligence request and return its structured result."""
            return {"ok": True, "method": method, "result": _request(root, command, timeout_sec, method, params)}

        return tool

    common("textDocument/definition", "symbol_definition")
    common("textDocument/references", "symbol_references")
    common("textDocument/documentSymbol", "document_symbols")
    common("workspace/symbol", "workspace_symbols")
    common("textDocument/hover", "symbol_hover")
    common("textDocument/prepareCallHierarchy", "call_hierarchy")
    common("textDocument/diagnostic", "language_diagnostics")
