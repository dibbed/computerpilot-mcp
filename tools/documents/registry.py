"""MCP registration for native document tools."""

from __future__ import annotations

from mcp.server import MCPServer


def register(mcp: MCPServer) -> None:
    """Register native document tools."""
    del mcp
