from __future__ import annotations

import json
import sys


def read_message() -> dict[str, object]:
    length = 0
    while True:
        line = sys.stdin.buffer.readline()
        if line in {b"\r\n", b"\n", b""}:
            break
        if line.lower().startswith(b"content-length:"):
            length = int(line.split(b":", 1)[1])
    if length <= 0:
        return {}
    return json.loads(sys.stdin.buffer.read(length))


def write_message(payload: dict[str, object]) -> None:
    body = json.dumps(payload, separators=(",", ":")).encode()
    sys.stdout.buffer.write(f"Content-Length: {len(body)}\r\n\r\n".encode() + body)
    sys.stdout.buffer.flush()


initialized = False
opened: set[str] = set()

while True:
    message = read_message()
    if not message:
        break
    method = message.get("method")
    if method == "initialize":
        write_message(
            {
                "jsonrpc": "2.0",
                "id": message["id"],
                "result": {"capabilities": {"definitionProvider": True, "hoverProvider": True}},
            }
        )
        continue
    if method == "initialized":
        initialized = True
        continue
    if method == "textDocument/didOpen":
        if not initialized:
            sys.exit(20)
        params = message.get("params") or {}
        document = params.get("textDocument") if isinstance(params, dict) else None
        if isinstance(document, dict) and isinstance(document.get("uri"), str):
            opened.add(document["uri"])
        continue
    if method == "textDocument/didClose":
        params = message.get("params") or {}
        document = params.get("textDocument") if isinstance(params, dict) else None
        if isinstance(document, dict) and isinstance(document.get("uri"), str):
            opened.discard(document["uri"])
        continue
    if method == "shutdown":
        write_message({"jsonrpc": "2.0", "id": message["id"], "result": None})
        continue
    if method == "exit":
        break
    if "id" in message:
        if not initialized:
            write_message(
                {
                    "jsonrpc": "2.0",
                    "id": message["id"],
                    "error": {"code": -32001, "message": "request before initialized"},
                }
            )
            continue
        params = message.get("params")
        if isinstance(method, str) and method.startswith("textDocument/"):
            document = params.get("textDocument") if isinstance(params, dict) else None
            uri = document.get("uri") if isinstance(document, dict) else None
            if not isinstance(uri, str) or uri not in opened:
                write_message(
                    {
                        "jsonrpc": "2.0",
                        "id": message["id"],
                        "error": {"code": -32002, "message": "document was not opened"},
                    }
                )
                continue
        write_message(
            {
                "jsonrpc": "2.0",
                "id": message["id"],
                "result": {"method": method, "initialized": initialized, "opened_count": len(opened)},
            }
        )
