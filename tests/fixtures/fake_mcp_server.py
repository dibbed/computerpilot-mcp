"""Deterministic local test MCP; stdout is exclusively protocol data."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import anyio
import psutil
from mcp import types
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server

# SDK diverts fd1 during serving; retain a duplicate of the original protocol pipe.
PROTOCOL_STDOUT = os.fdopen(os.dup(sys.stdout.fileno()), "wb", buffering=0)


def record_effect(workspace: Path, arguments: dict[str, Any]) -> int:
    counter = workspace / "counter.json"
    count = int(counter.read_text(encoding="utf-8")) if counter.exists() else 0
    count += 1
    with (workspace / "effects.jsonl").open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps({"dispatch_count": count, "arguments": arguments}) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    with counter.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(str(count))
        handle.flush()
        os.fsync(handle.fileno())
    return count


def tools(mode: str = "readonly", epoch: int = 0) -> list[types.Tool]:
    catalog = [
        types.Tool(
            name="increment_value", description="Create one durable synthetic test effect.",
            input_schema={"type": "object", "properties": {"amount": {"type": "integer", "minimum": 1, "maximum": 10}},
                          "required": ["amount"], "additionalProperties": False},
            annotations=types.ToolAnnotations(read_only_hint=False, destructive_hint=False),
        ),
        types.Tool(
            name="read_value", description="Return the exact disposable test arguments.",
            input_schema={"type": "object", "properties": {"value": {"type": "string"}, "file.name": {"type": "string"}},
                          "additionalProperties": False},
            annotations=types.ToolAnnotations(read_only_hint=True, destructive_hint=False),
        ),
    ]
    read = catalog[1]
    if mode == "duplicate":
        catalog.append(read)
    elif mode == "schema_remote_ref":
        read.input_schema["properties"]["value"] = {"$ref": "https://example.invalid/no-fetch"}
    elif mode == "schema_regex":
        read.input_schema["properties"]["value"] = {"type": "string", "pattern": "(a+)+$"}
    elif mode == "recursive_schema":
        read.input_schema["$defs"] = {"Loop": {"$ref": "#/$defs/Loop"}}
        read.input_schema["properties"]["value"] = {"$ref": "#/$defs/Loop"}
    elif mode == "dangerous_annotation":
        catalog[0].annotations = types.ToolAnnotations(read_only_hint=True, idempotent_hint=True)
    elif mode == "catalog_change" and epoch == 1:
        read.input_schema["properties"]["value"] = {"type": "integer"}
    elif mode == "remove" or (mode == "catalog_change" and epoch >= 2):
        catalog.remove(read)
    elif mode == "unknown":
        catalog.append(types.Tool(name="unclassified_tool", input_schema={"type": "object"}))
    return catalog


def marker(workspace: Path, name: str, value: str = "ready") -> None:
    with (workspace / name).open("w", encoding="utf-8") as handle:
        handle.write(value)
        handle.flush()
        os.fsync(handle.fileno())


async def wait_control(workspace: Path, name: str) -> None:
    deadline = time.monotonic() + 60
    while not (workspace / name).exists():
        if time.monotonic() >= deadline:
            raise TimeoutError("fixture control deadline")
        await asyncio.sleep(0.01)


async def raw_result(workspace: Path, mode: str, request_id: Any, data: dict[str, Any]) -> None:
    if mode == "split_utf8":
        payload = (json.dumps({"jsonrpc": "2.0", "id": request_id, "result": {
            "content": [{"type": "text", "text": "پاسخ"}], "structuredContent": data,
        }}, ensure_ascii=False) + "\n").encode("utf-8")
        split = payload.index("پ".encode()) + 1
        PROTOCOL_STDOUT.write(payload[:split])
        PROTOCOL_STDOUT.flush()
        marker(workspace, "split_ready", str(split))
        await wait_control(workspace, "release")
        PROTOCOL_STDOUT.write(payload[split:])
        PROTOCOL_STDOUT.flush()
        await wait_control(workspace, "close")
    else:
        payload = {
            "malformed": b"not-json\n",
            "oversize": b"x" * (2 * 1024 * 1024 + 1) + b"\n",
            "no_newline": b"x" * (2 * 1024 * 1024 + 1),
            "nesting": b"[" * 33 + b"]" * 33 + b"\n",
        }[mode]
        PROTOCOL_STDOUT.write(payload)
        PROTOCOL_STDOUT.flush()
    os._exit(3)


async def run(workspace: Path, page_size: int, mode: str) -> None:
    async def list_tools(context: Any, params: types.PaginatedRequestParams | None) -> types.ListToolsResult:
        cursor = int(params.cursor) if params and params.cursor else 0
        epoch_file = workspace / "catalog_epoch"
        epoch = int(epoch_file.read_text(encoding="utf-8")) if epoch_file.exists() else 0
        catalog = tools(mode, epoch)
        end = cursor + page_size
        next_cursor = "0" if mode == "cursor_loop" else str(end) if end < len(catalog) else None
        return types.ListToolsResult(tools=catalog[cursor:end], next_cursor=next_cursor)

    async def call_tool(context: Any, params: types.CallToolRequestParams) -> types.CallToolResult:
        marker(workspace, "call_received")
        arguments = params.arguments or {}
        data: dict[str, Any]
        if params.name == "read_value" and set(arguments) <= {"value", "file.name"}:
            data = {"value": arguments.get("value", "fixture"), "arguments": arguments}
        elif params.name == "increment_value" and set(arguments) == {"amount"} and type(arguments["amount"]) is int:
            if not 1 <= arguments["amount"] <= 10:
                return types.CallToolResult(content=[types.TextContent(type="text", text="invalid_fake_args")], is_error=True)
            data = {"dispatch_count": record_effect(workspace, arguments), "arguments": arguments}
            marker(workspace, "effect_committed")
        else:
            return types.CallToolResult(content=[types.TextContent(type="text", text="invalid_fake_args")], is_error=True)
        if mode in {"uncertain_mutation", "crash"}:
            os._exit(5)
        elif mode == "root_exit_child":
            spawn_tree_member(workspace, "tree_child")
            await wait_control(workspace, "child_ready")
            os._exit(6)
        elif mode in {"delayed", "call_timeout", "ignore_cancel"}:
            try:
                await wait_control(workspace, "close")
            except asyncio.CancelledError:
                if mode != "ignore_cancel":
                    raise
                marker(workspace, "cancel_ignored")
                with anyio.CancelScope(shield=True):
                    await wait_control(workspace, "close")
        elif mode in {"malformed", "oversize", "no_newline", "nesting", "split_utf8"}:
            await raw_result(workspace, mode, context.request_id, data)
        elif mode == "notification_flood":
            for _ in range(128):
                await context.session.send_tool_list_changed()
        elif mode == "stderr_flood":
            chunk = ("fixture stderr " + os.getenv("FIXTURE_CANARY", "synthetic-canary") + "\n").encode("utf-8")
            for _ in range(128):
                sys.stderr.buffer.write(chunk)
            sys.stderr.buffer.flush()
        return types.CallToolResult(
            content=[types.TextContent(type="text", text=json.dumps(data, ensure_ascii=False))], structured_content=data,
        )

    server: Server[Any] = Server(
        "computerpilot-local-fixture", version="1", on_list_tools=list_tools, on_call_tool=call_tool,
    )
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


def spawn_tree_member(workspace: Path, mode: str) -> subprocess.Popen[bytes]:
    names = ("SYSTEMROOT", "WINDIR", "COMSPEC", "TEMP", "TMP", "LANG", "LC_ALL", "LC_CTYPE", "TMPDIR", "PYTHONUNBUFFERED")
    env = {name: os.environ[name] for name in names if name in os.environ}
    env["MCP_EXTENSIONS_ENABLED"] = "0"
    return subprocess.Popen(
        [sys.executable, str(Path(__file__).resolve()), "--workspace", str(workspace), "--mode", mode],
        cwd=workspace, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )


async def special_mode(workspace: Path, mode: str) -> None:
    if mode in {"tree_child", "tree_grandchild"}:
        child = spawn_tree_member(workspace, "tree_grandchild") if mode == "tree_child" else None
        try:
            if child:
                await wait_control(workspace, "grandchild_ready")
            current = psutil.Process()
            name = "child_ready" if child else "grandchild_ready"
            marker(workspace, name, json.dumps({"pid": current.pid, "created": current.create_time()}))
            await wait_control(workspace, "close")
        finally:
            if child and child.poll() is None:
                child.kill()
                child.wait(timeout=5)
        return
    if mode != "blocked_stdin":
        line = sys.stdin.buffer.readline(128 * 1024)
        if mode == "handshake_failed":
            request = json.loads(line)
            response = {"jsonrpc": "2.0", "id": request["id"], "result": {
                "protocolVersion": "invalid-fixture", "capabilities": {}, "serverInfo": {"name": "fixture", "version": "1"},
            }}
            marker(workspace, "startup_ready")
            PROTOCOL_STDOUT.write((json.dumps(response) + "\n").encode())
            PROTOCOL_STDOUT.flush()
            return
    marker(workspace, "startup_ready")
    await wait_control(workspace, "close")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--page-size", type=int, default=128)
    parser.add_argument("--mode", choices=[
        "readonly", "mutation_success", "malformed", "oversize", "no_newline", "nesting", "split_utf8",
        "duplicate", "cursor_loop", "schema_remote_ref", "schema_regex", "recursive_schema",
        "catalog_change", "remove", "unknown", "dangerous_annotation", "notification_flood", "stderr_flood",
        "delayed", "call_timeout", "startup_timeout", "handshake_failed", "blocked_stdin", "crash", "root_exit_child",
        "uncertain_mutation", "ignore_cancel", "clean_shutdown", "tree_child", "tree_grandchild",
    ], default="readonly")
    args = parser.parse_args()
    workspace = args.workspace.resolve()
    if workspace != Path.cwd().resolve() or args.workspace.is_symlink() or not 1 <= args.page_size <= 128:
        parser.error("fixture requires its disposable cwd and bounded page size")
    marker(workspace, "environment_isolated", json.dumps({
        "extensions_disabled": os.getenv("MCP_EXTENSIONS_ENABLED") == "0",
        "parent_canary_absent": "FIXTURE_PARENT_SECRET" not in os.environ,
        "provider_config_absent": "MCP_EXTENSIONS_CONFIG" not in os.environ,
    }))
    if args.mode in {"startup_timeout", "handshake_failed", "blocked_stdin", "tree_child", "tree_grandchild"}:
        asyncio.run(special_mode(workspace, args.mode))
    else:
        asyncio.run(run(workspace, args.page_size, args.mode))


if __name__ == "__main__":
    main()
