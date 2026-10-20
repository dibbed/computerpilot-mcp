from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, cast

import pytest
from mcp import Client
from mcp.server import MCPServer
from pydantic import ValidationError

from core.errors import ToolError
from tools.language import registry
from tools.language.registry import register
from tools.language.requests import (
    DiagnosticRequest,
    PositionRequest,
    WorkspaceSymbolRequest,
    select_request,
)

_READ_MODELS = {
    "symbol_definition": "PositionRequest",
    "symbol_references": "ReferencesRequest",
    "document_symbols": "DocumentRequest",
    "workspace_symbols": "WorkspaceSymbolRequest",
    "symbol_hover": "PositionRequest",
    "call_hierarchy": "PositionRequest",
    "language_diagnostics": "DiagnosticRequest",
}
_LANGUAGES = ["python", "typescript", "typescriptreact", "javascript", "javascriptreact", "php"]


def _server() -> MCPServer:
    server = MCPServer("language-schema")
    register(server)
    return server


def _tools() -> dict[str, Any]:
    return {tool.name: tool for tool in asyncio.run(_server().list_tools())}


def _structured(result: Any) -> dict[str, Any]:
    assert isinstance(result.structured_content, dict)
    return result.structured_content


def _enum_values(prop: dict[str, Any]) -> list[object]:
    direct = prop.get("enum")
    if isinstance(direct, list):
        return direct
    for variant in prop.get("anyOf", []):
        if isinstance(variant, dict) and isinstance(variant.get("enum"), list):
            return variant["enum"]
    raise AssertionError(f"No enum in schema property: {prop!r}")


def _object_variant(prop: dict[str, Any]) -> dict[str, Any]:
    if prop.get("type") == "object":
        return prop
    for variant in prop.get("anyOf", []):
        if isinstance(variant, dict) and variant.get("type") == "object":
            return variant
    raise AssertionError(f"No object schema in property: {prop!r}")


def test_language_tool_schemas_do_not_expose_arbitrary_command() -> None:
    tools = _tools()
    for name in (
        *_READ_MODELS,
        "rename_symbol",
    ):
        assert "command" not in tools[name].input_schema["properties"]

    assert "expected_sha256" in tools["rename_symbol"].input_schema["required"]


def test_all_seven_read_schemas_are_bounded_and_typed() -> None:
    tools = _tools()
    total_bytes = 0
    for name, model in _READ_MODELS.items():
        schema = tools[name].input_schema
        properties = schema["properties"]
        serialized = json.dumps(schema, sort_keys=True, separators=(",", ":")).encode("utf-8")
        total_bytes += len(serialized)

        assert properties["root"]["maxLength"] == 32_767
        assert _object_variant(properties["params"])["maxProperties"] == 100
        assert properties["timeout_sec"]["exclusiveMinimum"] == 0
        assert properties["timeout_sec"]["maximum"] == 300
        assert set(_enum_values(properties["language_id"])) == set(_LANGUAGES)
        assert "request" in properties
        assert model in serialized.decode("utf-8")
        assert schema["required"] == ["root"]
        assert len(serialized) <= 12_000

    assert total_bytes <= 60_000


def test_exactly_one_request_form_is_required() -> None:
    with pytest.raises(ToolError) as missing:
        select_request(None, None)
    assert missing.value.code == "lsp_request_shape"

    with pytest.raises(ToolError) as both:
        select_request({"query": "Service"}, WorkspaceSymbolRequest(query="Service"))
    assert both.value.code == "lsp_request_shape"

    assert select_request({"query": "Service"}, None) == {"query": "Service"}
    assert select_request(None, WorkspaceSymbolRequest(query="Service")) == {"query": "Service"}


def test_typed_request_excludes_absent_optional_fields() -> None:
    request = DiagnosticRequest.model_validate({"textDocument": {"uri": "file:///workspace/a.py"}})
    assert select_request(None, request) == {"textDocument": {"uri": "file:///workspace/a.py"}}


def test_typed_request_models_reject_unknown_and_invalid_values() -> None:
    with pytest.raises(ValidationError):
        PositionRequest.model_validate(
            {
                "textDocument": {"uri": "file:///workspace/a.py"},
                "position": {"line": 0, "character": -1},
            }
        )
    with pytest.raises(ValidationError):
        PositionRequest.model_validate(
            {
                "textDocument": {"uri": "file:///workspace/a.py"},
                "position": {"line": 0, "character": 0},
                "executeCommand": "canary",
            }
        )


def test_read_tool_requires_one_form_before_dispatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def deny_dispatch(*args: Any, **kwargs: Any) -> Any:
        pytest.fail("invalid request reached LSP dispatch")

    monkeypatch.setattr(registry.LspClient, "request", deny_dispatch)

    async def scenario() -> None:
        async with Client(_server(), raise_exceptions=True) as client:
            missing = _structured(await client.call_tool("symbol_definition", {"root": str(tmp_path)}))
            assert missing["ok"] is False
            assert missing["error"] == "lsp_request_shape"

            both = _structured(
                await client.call_tool(
                    "symbol_definition",
                    {
                        "root": str(tmp_path),
                        "params": {
                            "textDocument": {"uri": (tmp_path / "a.py").as_uri()},
                            "position": {"line": 0, "character": 0},
                        },
                        "request": {
                            "textDocument": {"uri": (tmp_path / "a.py").as_uri()},
                            "position": {"line": 0, "character": 0},
                        },
                    },
                )
            )
            assert both["ok"] is False
            assert both["error"] == "lsp_request_shape"

    asyncio.run(scenario())


def test_typed_and_legacy_lsp_inputs_have_same_semantics(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "a.py"
    target.write_text("value = 1\n", encoding="utf-8")
    payload = {
        "textDocument": {"uri": target.as_uri()},
        "position": {"line": 0, "character": 0},
    }
    calls: list[tuple[str, dict[str, Any]]] = []

    def request(self: Any, method: str, params: dict[str, Any]) -> Any:
        calls.append((method, params))
        return {"contents": "same"}

    monkeypatch.setattr(registry.LspClient, "request", request)

    async def scenario() -> None:
        async with Client(_server(), raise_exceptions=True) as client:
            legacy = _structured(
                await client.call_tool("symbol_hover", {"root": str(tmp_path), "params": payload})
            )
            typed = _structured(
                await client.call_tool("symbol_hover", {"root": str(tmp_path), "request": payload})
            )
            expected = {"ok": True, "method": "textDocument/hover", "result": {"contents": "same"}}
            assert legacy == expected
            assert typed == expected

    asyncio.run(scenario())
    assert len(calls) == 2
    assert calls[0][0] == calls[1][0] == "textDocument/hover"
    assert calls[0][1] == calls[1][1]


class _ManagerSpy:
    def __init__(self) -> None:
        self.calls: list[tuple[Path, str, str, dict[str, object]]] = []

    async def arequest(
        self,
        root: Path,
        language_id: str,
        method: str,
        params: dict[str, object],
        *,
        timeout_sec: float,
    ) -> object:
        self.calls.append((root, language_id, method, params))
        return {"ok": True}


def test_document_language_is_inferred_from_suffix(tmp_path: Path) -> None:
    manager = _ManagerSpy()
    target = tmp_path / "a.py"
    target.write_text("value = 1\n", encoding="utf-8")
    params = {
        "textDocument": {"uri": target.as_uri()},
        "position": {"line": 0, "character": 0},
    }

    asyncio.run(
        registry._request(
            str(tmp_path), 5, "textDocument/hover", params, manager=cast(Any, manager), language_id=None
        )
    )
    assert manager.calls == [(tmp_path, "python", "textDocument/hover", params)]

    tsx = tmp_path / "a.tsx"
    tsx.write_text("export const value = 1;\n", encoding="utf-8")
    asyncio.run(
        registry._request(
            str(tmp_path),
            5,
            "textDocument/hover",
            {"textDocument": {"uri": tsx.as_uri()}, "position": {"line": 0, "character": 0}},
            manager=cast(Any, manager),
            language_id=None,
        )
    )
    assert manager.calls[-1][1] == "typescriptreact"

    php = tmp_path / "a.php"
    php.write_text("<?php $value = 1;\n", encoding="utf-8")
    asyncio.run(
        registry._request(
            str(tmp_path),
            5,
            "textDocument/hover",
            {"textDocument": {"uri": php.as_uri()}, "position": {"line": 0, "character": 0}},
            manager=cast(Any, manager),
            language_id="php",
        )
    )
    assert manager.calls[-1][1] == "php"


def test_document_language_mismatch_never_dispatches(tmp_path: Path) -> None:
    manager = _ManagerSpy()
    target = tmp_path / "a.py"
    target.write_text("value = 1\n", encoding="utf-8")
    params = {
        "textDocument": {"uri": target.as_uri()},
        "position": {"line": 0, "character": 0},
    }

    with pytest.raises(ToolError) as failure:
        asyncio.run(
            registry._request(
                str(tmp_path),
                5,
                "textDocument/hover",
                params,
                manager=cast(Any, manager),
                language_id="typescript",
            )
        )
    assert failure.value.code == "lsp_language_mismatch"
    assert manager.calls == []


def test_workspace_symbol_defaults_to_python(tmp_path: Path) -> None:
    manager = _ManagerSpy()
    asyncio.run(
        registry._request(
            str(tmp_path),
            5,
            "workspace/symbol",
            {"query": "Service"},
            manager=cast(Any, manager),
            language_id=None,
        )
    )
    assert manager.calls[0][1] == "python"
