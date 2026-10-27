"""MCP registration for Language Server Protocol intelligence."""

from __future__ import annotations

import asyncio
import shutil
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Annotated, Any, Literal, cast

import anyio
from mcp.server import MCPServer
from pydantic import BaseModel, Field

from core.audit import audit_action
from core.config import SETTINGS, resolve_path
from core.errors import ToolError
from core.recovery import bind_operation_recovery
from core.response_models import validate_tool_output
from core.tooling import MUTATING, OPEN_WORLD_READ, compact_errors
from tools.language.edits import apply_workspace_edit, prepare_workspace_edit
from tools.language.lsp import LspClient, uri_to_path
from tools.language.requests import (
    DiagnosticRequest,
    DiagnosticsResult,
    DocumentRequest,
    PositionRequest,
    ReferencesRequest,
    WorkspaceSymbolRequest,
    language_for_uri,
    sanitize_diagnostic_report,
    select_request,
    validate_request,
)
from tools.language.sessions import LspSessionManager

LanguageId = Literal["python", "typescript", "typescriptreact", "javascript", "javascriptreact", "php"]


def _command() -> list[str]:
    """Return the trusted/discovered language-server command exposed by MCP."""

    discovered = shutil.which("pyright-langserver")
    return [discovered or "pyright-langserver", "--stdio"]


async def _run_sync(call: Callable[[], Any]) -> Any:
    call = bind_operation_recovery(call)
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


def _resolve_language(method: str, params: Mapping[str, object], language_id: str | None) -> str:
    if method.startswith("textDocument/"):
        document = params.get("textDocument")
        uri = document.get("uri") if isinstance(document, Mapping) else None
        if isinstance(uri, str):
            suffix_language = language_for_uri(uri)
            if suffix_language is not None:
                if language_id is not None and language_id != suffix_language:
                    raise ToolError("lsp_language_mismatch", "The language_id does not match the document suffix.")
                return language_id or suffix_language
    return language_id or "python"


async def _request(
    root: str, timeout_sec: float, method: str, params: dict[str, Any], *,
    manager: LspSessionManager | None = None, language_id: str | None = None,
) -> Any:
    language = _resolve_language(method, params, language_id)
    deadline = time.monotonic() + timeout_sec
    if manager is not None:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ToolError("lsp_timeout", "Language request deadline expired before dispatch.")
        return await manager.arequest(Path(root), language, method, params, timeout_sec=remaining)

    def legacy_request() -> Any:
        workspace = resolve_path(root)
        validated = validate_request(workspace, method, params)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ToolError("lsp_timeout", "Language request deadline expired before dispatch.")
        return LspClient(_command(), workspace, timeout_sec=remaining).request(method, validated)

    return await _run_sync(legacy_request)


async def _dispatch_read(
    root: str,
    params: dict[str, Any] | None,
    request: BaseModel | None,
    timeout_sec: float,
    method: str,
    *,
    manager: LspSessionManager | None,
    language_id: str | None,
) -> dict[str, Any]:
    selected = select_request(params, request)
    result = await _request(root, timeout_sec, method, selected, manager=manager, language_id=language_id)
    return {"ok": True, "method": method, "result": result}


def register(mcp: MCPServer, *, manager: LspSessionManager | None = None) -> None:
    if SETTINGS.persistent_enabled and manager is None:
        raise ToolError("lsp_configuration_invalid", "Persistent language tools require a server-owned session manager.")

    @mcp.tool(name="symbol_definition", annotations=OPEN_WORLD_READ, structured_output=True)
    @compact_errors("symbol_definition")
    async def symbol_definition(
        root: Annotated[str, Field(min_length=1, max_length=32_767)],
        params: Annotated[dict[str, Any] | None, Field(max_length=100)] = None,
        timeout_sec: Annotated[float, Field(gt=0, le=300)] = 30,
        request: PositionRequest | None = None,
        language_id: LanguageId | None = None,
    ) -> dict[str, Any]:
        """Return definitions for a position from the trusted language server."""
        return await _dispatch_read(
            root, params, request, timeout_sec, "textDocument/definition",
            manager=manager, language_id=language_id,
        )

    @mcp.tool(name="symbol_references", annotations=OPEN_WORLD_READ, structured_output=True)
    @compact_errors("symbol_references")
    async def symbol_references(
        root: Annotated[str, Field(min_length=1, max_length=32_767)],
        params: Annotated[dict[str, Any] | None, Field(max_length=100)] = None,
        timeout_sec: Annotated[float, Field(gt=0, le=300)] = 30,
        request: ReferencesRequest | None = None,
        language_id: LanguageId | None = None,
    ) -> dict[str, Any]:
        """Return references for a position from the trusted language server."""
        return await _dispatch_read(
            root, params, request, timeout_sec, "textDocument/references",
            manager=manager, language_id=language_id,
        )

    @mcp.tool(name="document_symbols", annotations=OPEN_WORLD_READ, structured_output=True)
    @compact_errors("document_symbols")
    async def document_symbols(
        root: Annotated[str, Field(min_length=1, max_length=32_767)],
        params: Annotated[dict[str, Any] | None, Field(max_length=100)] = None,
        timeout_sec: Annotated[float, Field(gt=0, le=300)] = 30,
        request: DocumentRequest | None = None,
        language_id: LanguageId | None = None,
    ) -> dict[str, Any]:
        """Return symbols declared in one document."""
        return await _dispatch_read(
            root, params, request, timeout_sec, "textDocument/documentSymbol",
            manager=manager, language_id=language_id,
        )

    @mcp.tool(name="workspace_symbols", annotations=OPEN_WORLD_READ, structured_output=True)
    @compact_errors("workspace_symbols")
    async def workspace_symbols(
        root: Annotated[str, Field(min_length=1, max_length=32_767)],
        params: Annotated[dict[str, Any] | None, Field(max_length=100)] = None,
        timeout_sec: Annotated[float, Field(gt=0, le=300)] = 30,
        request: WorkspaceSymbolRequest | None = None,
        language_id: LanguageId | None = None,
    ) -> dict[str, Any]:
        """Search workspace symbols across the trusted language server."""
        return await _dispatch_read(
            root, params, request, timeout_sec, "workspace/symbol",
            manager=manager, language_id=language_id,
        )

    @mcp.tool(name="symbol_hover", annotations=OPEN_WORLD_READ, structured_output=True)
    @compact_errors("symbol_hover")
    async def symbol_hover(
        root: Annotated[str, Field(min_length=1, max_length=32_767)],
        params: Annotated[dict[str, Any] | None, Field(max_length=100)] = None,
        timeout_sec: Annotated[float, Field(gt=0, le=300)] = 30,
        request: PositionRequest | None = None,
        language_id: LanguageId | None = None,
    ) -> dict[str, Any]:
        """Return hover information for a position."""
        return await _dispatch_read(
            root, params, request, timeout_sec, "textDocument/hover",
            manager=manager, language_id=language_id,
        )

    @mcp.tool(name="call_hierarchy", annotations=OPEN_WORLD_READ, structured_output=True)
    @compact_errors("call_hierarchy")
    async def call_hierarchy(
        root: Annotated[str, Field(min_length=1, max_length=32_767)],
        params: Annotated[dict[str, Any] | None, Field(max_length=100)] = None,
        timeout_sec: Annotated[float, Field(gt=0, le=300)] = 30,
        request: PositionRequest | None = None,
        language_id: LanguageId | None = None,
    ) -> dict[str, Any]:
        """Prepare call hierarchy items for a position."""
        return await _dispatch_read(
            root, params, request, timeout_sec, "textDocument/prepareCallHierarchy",
            manager=manager, language_id=language_id,
        )

    @mcp.tool(name="language_diagnostics", annotations=OPEN_WORLD_READ, structured_output=True)
    @compact_errors("language_diagnostics")
    async def language_diagnostics(
        root: Annotated[str, Field(min_length=1, max_length=32_767)],
        params: Annotated[dict[str, Any] | None, Field(max_length=100)] = None,
        timeout_sec: Annotated[float, Field(gt=0, le=300)] = 30,
        request: DiagnosticRequest | None = None,
        language_id: LanguageId | None = None,
    ) -> DiagnosticsResult:
        """Return language diagnostics for one document."""
        result = await _dispatch_read(
            root, params, request, timeout_sec, "textDocument/diagnostic",
            manager=manager, language_id=language_id,
        )
        result["result"] = sanitize_diagnostic_report(result["result"])
        return cast(DiagnosticsResult, validate_tool_output(DiagnosticsResult, result, operation="language_diagnostics"))

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
        document_versions = getattr(edit, "snapshot_version", None)
        source_sha256 = getattr(edit, "source_sha256", None)

        def apply_rename() -> dict[str, Any]:
            if manager is not None or document_versions is not None or source_sha256 is not None:
                if not isinstance(document_versions, Mapping) or not isinstance(source_sha256, Mapping):
                    raise ToolError("lsp_result_stale", "The language-server rename snapshot is incomplete.")
                snapshot_versions: dict[Path, int] = {}
                for uri, version in document_versions.items():
                    if not isinstance(uri, str) or type(version) is not int or version < 0:
                        raise ToolError("lsp_result_stale", "The language-server rename snapshot is invalid.")
                    source = uri_to_path(uri, workspace)
                    previous = snapshot_versions.get(source)
                    if previous is not None and previous != version:
                        raise ToolError("lsp_document_version_conflict", "The language-server rename snapshot has conflicting versions.")
                    snapshot_versions[source] = version
                snapshot_hashes: dict[Path, str] = {}
                for uri, digest in source_sha256.items():
                    if not isinstance(uri, str) or not isinstance(digest, str):
                        raise ToolError("lsp_result_stale", "The language-server rename snapshot is invalid.")
                    source = uri_to_path(uri, workspace)
                    previous_hash = snapshot_hashes.get(source)
                    if previous_hash is not None and previous_hash.casefold() != digest.casefold():
                        raise ToolError("lsp_result_stale", "The language-server rename snapshot has conflicting hashes.")
                    snapshot_hashes[source] = digest
                touched: set[Path] = set()
                if isinstance(edit, dict):
                    raw_changes = edit.get("changes")
                    if isinstance(raw_changes, dict):
                        for uri in raw_changes:
                            if isinstance(uri, str):
                                touched.add(uri_to_path(uri, workspace))
                    operations = edit.get("documentChanges")
                    if isinstance(operations, list):
                        for operation in operations:
                            document = operation.get("textDocument") if isinstance(operation, dict) else None
                            uri = document.get("uri") if isinstance(document, dict) else None
                            if isinstance(uri, str):
                                touched.add(uri_to_path(uri, workspace))
                for source in touched:
                    relative = source.relative_to(workspace).as_posix()
                    if source not in snapshot_versions or source not in snapshot_hashes:
                        raise ToolError(
                            "lsp_result_stale",
                            f"Rename edit targets {relative} without a server snapshot.",
                        )
                    wanted = expected_sha256.get(relative)
                    digest = snapshot_hashes[source]
                    if wanted is not None and wanted.casefold() != digest.casefold():
                        raise ToolError("lsp_result_stale", f"The language-server rename snapshot is stale for {relative}.")
            plan = prepare_workspace_edit(
                workspace,
                edit,
                expected_sha256=expected_sha256,
                require_expected_sha256=True,
                document_versions=document_versions,
            )
            audit_action("rename_symbol", target=workspace, details={"file_count": len(plan), "dry_run": dry_run})
            if dry_run:
                return {"ok": True, "dry_run": True, "file_count": len(plan), "files": [str(item.path) for item in plan]}
            result = apply_workspace_edit(plan)
            if manager is not None:
                manager.close_workspace(workspace)
            return {**result, "dry_run": False}

        return await _run_sync(apply_rename)

    @mcp.tool(annotations=MUTATING, structured_output=True)
    @compact_errors("apply_code_action")
    def apply_code_action(
        root: Annotated[str, Field(min_length=1, max_length=32_767)],
        action: Annotated[dict[str, Any], Field(max_length=200)],
        expected_sha256: Annotated[dict[str, str] | None, Field(max_length=500)] = None,
        dry_run: bool = False,
        document_versions: Annotated[dict[str, int] | None, Field(max_length=500)] = None,
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
            document_versions=document_versions,
        )
        if dry_run:
            return {"ok": True, "dry_run": True, "file_count": len(plan), "command_ignored": bool(action.get("command"))}
        result = apply_workspace_edit(plan)
        if manager is not None:
            manager.close_workspace(workspace)
        return {**result, "dry_run": False, "command_ignored": bool(action.get("command"))}
