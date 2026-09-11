"""Ali Windows Agent MCP entrypoint."""

from __future__ import annotations

import argparse
import asyncio
import json
from collections.abc import Sequence

from core.config import SETTINGS, ensure_runtime_dirs
from core.registry import create_server

mcp = create_server()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Windows Full Access MCP Server")
    parser.add_argument(
        "--transport",
        choices=("stdio", "streamable-http"),
        default="stdio",
        help="stdio for Secure MCP Tunnel; streamable-http for direct local use.",
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--path", default="/mcp")
    parser.add_argument("--check", action="store_true", help="Run startup checks and exit.")
    return parser


async def _check() -> dict[str, object]:
    tools = await mcp.list_tools()
    return {
        "ok": True,
        "server": SETTINGS.server_name,
        "version": SETTINGS.version,
        "tool_count": len(tools),
        "unique_tool_names": len({tool.name for tool in tools}) == len(tools),
    }


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    ensure_runtime_dirs()
    if args.check:
        print(json.dumps(asyncio.run(_check()), separators=(",", ":")))
        return 0
    if args.transport == "stdio":
        mcp.run(transport="stdio")
    else:
        mcp.run(
            transport="streamable-http",
            host=args.host,
            port=args.port,
            streamable_http_path=args.path,
            json_response=True,
            stateless_http=True,
            max_request_body_size=2 * 1024 * 1024,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
