from __future__ import annotations

import asyncio

from mcp.server import MCPServer

from tools.language.registry import register


def test_language_tool_schemas_do_not_expose_arbitrary_command() -> None:
    server = MCPServer("language-schema")
    register(server)
    tools = {tool.name: tool for tool in asyncio.run(server.list_tools())}
    for name in (
        "symbol_definition",
        "symbol_references",
        "document_symbols",
        "workspace_symbols",
        "symbol_hover",
        "call_hierarchy",
        "language_diagnostics",
        "rename_symbol",
    ):
        assert "command" not in tools[name].input_schema["properties"]

    assert "expected_sha256" in tools["rename_symbol"].input_schema["required"]
