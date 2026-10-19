from __future__ import annotations

import asyncio
import hashlib
import os
import subprocess
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from mcp import Client
from mcp.server import MCPServer

from core import audit
from core.errors import ToolError
from tools.language import registry


@pytest.fixture(autouse=True)
def isolated_audit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(audit, "SETTINGS", replace(audit.SETTINGS, state_dir=tmp_path / "state"))


def create_server() -> MCPServer:
    server = MCPServer("language-validation")
    registry.register(server)
    return server


def _structured(result: Any) -> dict[str, Any]:
    assert isinstance(result.structured_content, dict)
    return result.structured_content


def test_apply_code_action_requires_complete_source_hash_guard(tmp_path: Path) -> None:
    target = tmp_path / "a.py"
    target.write_text("old\n", encoding="utf-8")
    action = {
        "edit": {
            "changes": {
                target.as_uri(): [
                    {
                        "range": {
                            "start": {"line": 0, "character": 0},
                            "end": {"line": 0, "character": 3},
                        },
                        "newText": "new",
                    }
                ]
            }
        }
    }

    async def scenario() -> None:
        async with Client(create_server(), raise_exceptions=True) as client:
            missing = _structured(
                await client.call_tool(
                    "apply_code_action",
                    {"root": str(tmp_path), "action": action, "dry_run": True},
                )
            )
            assert missing["ok"] is False
            assert missing["error"] == "lsp_edit_precondition_required"

            digest = hashlib.sha256(target.read_bytes()).hexdigest()
            guarded = _structured(
                await client.call_tool(
                    "apply_code_action",
                    {
                        "root": str(tmp_path),
                        "action": action,
                        "expected_sha256": {"a.py": digest},
                        "dry_run": True,
                    },
                )
            )
            assert guarded["ok"] is True
            assert guarded["dry_run"] is True
            assert guarded["file_count"] == 1

    asyncio.run(scenario())
    assert target.read_text(encoding="utf-8") == "old\n"


@pytest.mark.parametrize("case", [
    "negative", "bool", "overflow", "missing", "extra", "http", "outside", "large",
    "nul", "escape", "surrogate", "split_utf16", "depth", "token", "unknown_method",
])
def test_language_invalid_request_never_spawns(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: str
) -> None:
    target = tmp_path / "a.py"
    target.write_text("a😀b\n", encoding="utf-8")
    params: dict[str, Any] = {"textDocument": {"uri": target.as_uri()}, "position": {"line": 0, "character": 3}}
    method = "textDocument/hover"
    if case in {"negative", "bool", "overflow", "split_utf16"}:
        params["position"]["character"] = {"negative": -1, "bool": True, "overflow": 2**31, "split_utf16": 2}[case]
    elif case == "missing":
        params.pop("textDocument")
    elif case == "extra":
        params["executeCommand"] = "canary-private-command"
    elif case in {"http", "outside", "nul", "escape", "surrogate"}:
        params["textDocument"]["uri"] = {
            "http": "https://example.invalid/a.py", "outside": (tmp_path.parent / "outside.py").as_uri(),
            "nul": target.as_uri() + "%00", "escape": target.as_uri() + "%zz", "surrogate": target.as_uri() + "%ff",
        }[case]
    elif case == "large":
        params["extra"] = "canary-private-source" * 10_000
    elif case == "depth":
        node: dict[str, Any] = {}
        params["extra"] = node
        for _ in range(20):
            node["child"] = {}
            node = node["child"]
    elif case == "token":
        params["partialResultToken"] = "private-token"
    elif case == "unknown_method":
        method = "workspace/executeCommand"

    def deny_dispatch(*args: Any, **kwargs: Any) -> Any:
        pytest.fail("invalid request reached LSP dispatch")

    monkeypatch.setattr(registry.LspClient, "request", deny_dispatch)
    monkeypatch.setattr("tools.language.transport.spawn_owned_process", deny_dispatch)
    with pytest.raises(ToolError) as failure:
        registry._request(str(tmp_path), 5, method, params)
    assert "canary-private" not in str(failure.value)
    assert failure.value.code in {
        "lsp_params_invalid", "lsp_method_unsupported", "lsp_uri_unsupported",
        "lsp_path_outside_workspace", "lsp_position_invalid",
    }

    if case != "unknown_method":
        async def scenario() -> None:
            async with Client(create_server(), raise_exceptions=True) as client:
                result = _structured(await client.call_tool("symbol_hover", {"root": str(tmp_path), "params": params}))
                assert result["ok"] is False
                assert "canary-private" not in str(result)
        asyncio.run(scenario())


def test_language_valid_utf16_request_dispatches_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "a.py"
    target.write_text("a😀b\n", encoding="utf-8")
    calls: list[Any] = []

    def request(self: Any, method: str, params: Any) -> Any:
        calls.append((method, params))
        return {"contents": "valid"}

    monkeypatch.setattr(registry.LspClient, "request", request)
    params = {"textDocument": {"uri": target.as_uri()}, "position": {"line": 0, "character": 3}}
    assert registry._request(str(tmp_path), 5, "textDocument/hover", params) == {"contents": "valid"}
    assert calls == [("textDocument/hover", params)]


def test_language_workspace_query_does_not_read_document(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tools.language.requests import validate_request

    def no_read(*args: Any, **kwargs: Any) -> Any:
        pytest.fail("workspace query must not read source")

    monkeypatch.setattr(Path, "open", no_read)
    assert validate_request(tmp_path, "workspace/symbol", {"query": "Service"}) == {"query": "Service"}


def test_language_document_position_read_is_bounded(tmp_path: Path) -> None:
    from tools.language.requests import validate_request

    target = tmp_path / "large.py"
    target.write_bytes(b"x" * (2 * 1024 * 1024 + 1))
    with pytest.raises(ToolError) as failure:
        validate_request(tmp_path, "textDocument/hover", {
            "textDocument": {"uri": target.as_uri()}, "position": {"line": 0, "character": 0},
        })
    assert failure.value.code == "lsp_document_too_large"


def test_language_symlink_or_junction_escape_never_dispatches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "workspace"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    (outside / "secret.py").write_text("private = 1\n", encoding="utf-8")
    link = root / "linked"
    if os.name == "nt":
        subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(outside)], capture_output=True, check=True)
    else:
        link.symlink_to(outside, target_is_directory=True)

    def deny_dispatch(*args: Any, **kwargs: Any) -> Any:
        pytest.fail("escaped path reached LSP")

    monkeypatch.setattr(registry.LspClient, "request", deny_dispatch)
    try:
        assert link.resolve() == outside.resolve()
        with pytest.raises(ToolError) as failure:
            registry._request(str(root), 5, "textDocument/hover", {
                "textDocument": {"uri": (link / "secret.py").as_uri()}, "position": {"line": 0, "character": 0},
            })
        assert failure.value.code == "lsp_path_outside_workspace"
    finally:
        link.rmdir() if os.name == "nt" else link.unlink()


@pytest.mark.parametrize("method,fields", [
    ("textDocument/definition", {"position": {"line": 0, "character": 0}}),
    ("textDocument/references", {"position": {"line": 0, "character": 0}, "context": {"includeDeclaration": False}}),
    ("textDocument/documentSymbol", {}),
    ("textDocument/prepareCallHierarchy", {"position": {"line": 0, "character": 0}}),
    ("textDocument/diagnostic", {"identifier": "provider", "previousResultId": "prior"}),
    ("textDocument/rename", {"position": {"line": 0, "character": 0}, "newName": "new_name"}),
])
def test_language_standard_methods_remain_valid(tmp_path: Path, method: str, fields: dict[str, Any]) -> None:
    from tools.language.requests import validate_request

    target = tmp_path / "a.py"
    target.write_text("old_name = 1\n", encoding="utf-8")
    params = {"textDocument": {"uri": target.as_uri()}, **fields}
    assert validate_request(tmp_path, method, params) == params
