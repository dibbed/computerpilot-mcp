"""Deterministic persistent LSP test fixture (test-only; stdio, Content-Length framed).

Modes are selected with ``--mode`` and exist only to exercise bounded client and
session-manager behavior. The fixture never performs network I/O, never opens
the production registry, and writes a log file only when ``--log`` is given.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import time
from pathlib import Path

MODES = (
    "normal",
    "slow",
    "malformed",
    "crash",
    "oversize",
    "hang-shutdown",
    "flood",
    "reverse-replies",
)
SLOW_DELAY_SECONDS = 0.2
OVERSIZE_HEADER = b"Content-Length: 10000000\r\n\r\n"


def _mode_from(argv: list[str]) -> str:
    if "--mode" in argv:
        value = argv[argv.index("--mode") + 1]
        if value in MODES:
            return value
    return "normal"


def _log_target(argv: list[str]) -> Path | None:
    if "--log" in argv:
        return Path(argv[argv.index("--log") + 1])
    return None


def _append_log(path: Path | None, event: str) -> None:
    if path is None:
        return
    with path.open("a", encoding="utf-8") as handle:
        handle.write(f"{event}\n")


def _read_message() -> dict[str, object] | None:
    length = 0
    while True:
        line = sys.stdin.buffer.readline()
        if line in {b"\r\n", b"\n", b""}:
            break
        if line.lower().startswith(b"content-length:"):
            try:
                length = int(line.split(b":", 1)[1])
            except ValueError:
                length = 0
    if length <= 0:
        return None
    body = sys.stdin.buffer.read(length)
    try:
        message = json.loads(body)
    except json.JSONDecodeError:
        return {"__malformed__": True}
    return message if isinstance(message, dict) else {"__malformed__": True}


def _write_message(payload: dict[str, object]) -> None:
    body = json.dumps(payload, separators=(",", ":")).encode()
    sys.stdout.buffer.write(f"Content-Length: {len(body)}\r\n\r\n".encode() + body)
    sys.stdout.buffer.flush()


def _write_raw(frame: bytes) -> None:
    sys.stdout.buffer.write(frame)
    sys.stdout.buffer.flush()


def _error(message: dict[str, object], code: int, text: str) -> dict[str, object]:
    return {"jsonrpc": "2.0", "id": message.get("id"), "error": {"code": code, "message": text}}


def _dict(value: object) -> dict[str, object]:
    return value if isinstance(value, dict) else {}


def main() -> int:
    mode = _mode_from(sys.argv)
    log_path = _log_target(sys.argv)
    fixture_digest = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    initialized = False
    initialize_seen = False
    request_count = 0
    opened: dict[str, tuple[int, str]] = {}
    held_requests: list[dict[str, object]] = []
    while True:
        message = _read_message()
        if message is None:
            break
        if message.get("__malformed__"):
            _append_log(log_path, "malformed-input")
            continue
        method = message.get("method")
        if method == "exit":
            _append_log(log_path, "exit")
            break

        if "id" not in message:
            if method == "initialized":
                initialized = True
            elif method == "textDocument/didOpen":
                params = _dict(message.get("params"))
                document = _dict(params.get("textDocument"))
                uri = document.get("uri")
                if isinstance(uri, str):
                    version = document.get("version", 0)
                    opened[uri] = (version if isinstance(version, int) else 0, str(document.get("text", "")))
            elif method == "textDocument/didClose":
                params = _dict(message.get("params"))
                document = _dict(params.get("textDocument"))
                uri = document.get("uri")
                if isinstance(uri, str):
                    opened.pop(uri, None)
            continue

        request_count += 1
        if mode == "crash" and method != "initialize":
            _append_log(log_path, "crash")
            os._exit(3)
        if mode == "oversize" and method != "test/inspect":
            _write_raw(OVERSIZE_HEADER)
            _append_log(log_path, "oversize-hold")
            time.sleep(2.0)
            break
        if mode == "reverse-replies" and method not in {"initialize", "shutdown", "test/inspect"}:
            if not held_requests:
                held_requests.append(message)
                continue
            _write_message({"jsonrpc": "2.0", "id": message.get("id"), "result": {"method": method, "reversed": True}})
            pending = held_requests.pop(0)
            _write_message({"jsonrpc": "2.0", "id": pending.get("id"), "result": {"method": pending.get("method"), "reversed": True}})
            continue

        if method == "initialize":
            if initialize_seen:
                _write_message(_error(message, -32600, "initialize already received"))
                continue
            initialize_seen = True
            _write_message(
                {
                    "jsonrpc": "2.0",
                    "id": message.get("id"),
                    "result": {
                        "capabilities": {
                            "textDocumentSync": {"openClose": True, "change": 1},
                            "hoverProvider": True,
                            "definitionProvider": True,
                            "referencesProvider": True,
                            "callHierarchyProvider": True,
                        },
                        "serverInfo": {"name": "persistent-lsp-fixture", "version": "test"},
                    },
                }
            )
            _append_log(log_path, "initialize")
            if mode == "flood":
                sys.stderr.write("fixture-stderr-canary\n")
                sys.stderr.flush()
            continue

        if method == "shutdown":
            if mode == "hang-shutdown":
                _append_log(log_path, "shutdown-hang")
                while True:
                    time.sleep(1.0)
            _write_message({"jsonrpc": "2.0", "id": message.get("id"), "result": None})
            _append_log(log_path, "shutdown")
            continue

        if method == "test/inspect":
            inspect_result = {
                "pid": os.getpid(),
                "mode": mode,
                "initialized": initialized,
                "request_count": request_count,
                "open_count": len(opened),
                "documents": {uri: version for uri, (version, _text) in opened.items()},
                "fixture_digest": fixture_digest,
                "held_requests": len(held_requests),
            }
            _write_message({"jsonrpc": "2.0", "id": message.get("id"), "result": inspect_result})
            continue

        if method == "test/emitApplyEdit":
            _write_message(
                {
                    "jsonrpc": "2.0",
                    "id": "fixture-apply-edit",
                    "method": "workspace/applyEdit",
                    "params": {"edit": {"changes": {}}},
                }
            )
            _write_message({"jsonrpc": "2.0", "id": message.get("id"), "result": {"emitted": True}})
            continue

        if method == "test/flood":
            count = 0
            for index in range(100):
                _write_message(
                    {"jsonrpc": "2.0", "method": "test/notification", "params": {"index": index}}
                )
                count += 1
            _write_message({"jsonrpc": "2.0", "id": message.get("id"), "result": {"notifications": count}})
            continue

        if method == "textDocument/didChange":
            params = _dict(message.get("params"))
            document = _dict(params.get("textDocument"))
            uri = document.get("uri")
            version = document.get("version")
            if not isinstance(uri, str) or uri not in opened:
                _write_message(_error(message, -32602, "document was not opened"))
                continue
            if not isinstance(version, int) or version <= opened[uri][0]:
                _write_message(_error(message, -32602, "document version did not increase"))
                continue
            changes_value = params.get("contentChanges")
            changes = changes_value if isinstance(changes_value, list) else []
            text = str(changes[-1].get("text", "")) if changes and isinstance(changes[-1], dict) else opened[uri][1]
            opened[uri] = (version, text)
            _write_message({"jsonrpc": "2.0", "id": message.get("id"), "result": None})
            continue

        if mode == "malformed" and method != "test/inspect":
            _write_raw(b"Content-Length: 5\r\n\r\n{bad}")
            _append_log(log_path, "malformed-output")
            continue

        params = _dict(message.get("params"))
        document = _dict(params.get("textDocument"))
        uri_value = document.get("uri")
        uri = uri_value if isinstance(uri_value, str) else None
        if isinstance(uri, str) and uri not in opened:
            _write_message(_error(message, -32002, "document was not opened"))
            continue
        if mode == "slow":
            time.sleep(SLOW_DELAY_SECONDS)
        result: dict[str, object] = {"method": method, "initialized": initialized}
        if isinstance(uri, str):
            result["uri"] = uri
            result["version"] = opened[uri][0]
        _write_message({"jsonrpc": "2.0", "id": message.get("id"), "result": result})

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
