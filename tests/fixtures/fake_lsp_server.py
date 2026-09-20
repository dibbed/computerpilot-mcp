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
    return json.loads(sys.stdin.buffer.read(length))


def write_message(payload: dict[str, object]) -> None:
    body = json.dumps(payload).encode()
    sys.stdout.buffer.write(f"Content-Length: {len(body)}\r\n\r\n".encode() + body)
    sys.stdout.buffer.flush()


while True:
    message = read_message()
    method = message.get("method")
    if method == "exit" or not message:
        break
    if "id" not in message:
        continue
    if method == "initialize":
        result: object = {"capabilities": {"definitionProvider": True}}
    elif method == "shutdown":
        result = None
    else:
        result = {"method": method, "params": message.get("params")}
    write_message({"jsonrpc": "2.0", "id": message["id"], "result": result})
