"""Foreground runtime supervisor and loopback control panel. No installation or autostart."""

from __future__ import annotations

import argparse
import json
import logging
import logging.handlers
import os
import secrets
import subprocess
import sys
import threading
import time
import urllib.request
import uuid
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, BinaryIO, cast

import psutil

from core.audit import audit_action
from core.config import PROJECT_ROOT, SETTINGS
from core.executor import _creation_flags
from core.jobs import JobStore


def restart_delay(failures: int) -> int:
    return (5, 10, 30, 60)[min(max(failures - 1, 0), 3)]


class Supervisor:
    def __init__(self, command: list[str], *, readiness_url: str | None,
                 state_dir: Path | None = None, grace: float = 90, interval: float = 5) -> None:
        self.command = command
        self.readiness_url = readiness_url
        self.state_dir = state_dir or SETTINGS.state_dir
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.heartbeat = self.state_dir / f"heartbeat-{uuid.uuid4().hex}"
        self.grace = grace
        self.interval = interval
        self.stop = threading.Event()
        self.restart = threading.Event()
        self.lock = threading.Lock()
        self.state: dict[str, Any] = {"state": "starting", "restart_count": 0, "last_error": None,
                                      "pid": None, "mcp_healthy": False, "tunnel_healthy": None}
        self.logs: deque[str] = deque(maxlen=100)
        self.logger = logging.getLogger(f"mcp.supervisor.{uuid.uuid4().hex}")
        self.logger.setLevel(logging.INFO)
        self.logger.propagate = False
        handler = logging.handlers.RotatingFileHandler(self.state_dir / "supervisor.log", maxBytes=2_097_152,
                                                       backupCount=3, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
        self.logger.addHandler(handler)
        self.secret = os.environ.get("CONTROL_PLANE_API_KEY", "").strip()
        self.owned: dict[int, psutil.Process] = {}

    def event(self, message: str) -> None:
        if self.secret:
            message = message.replace(self.secret, "[redacted]")
        self.logger.info(message)
        display = message
        is_error = False
        try:
            entry = json.loads(message)
            if isinstance(entry, dict) and "msg" in entry:
                if entry.get("level") == "INFO" and entry["msg"] in {
                    "run", "provided", "invoking", "OnStart hook executing", "OnStart hook executed",
                }:
                    return  # Keep framework wiring noise in the rotated file, not the panel.
                display = f"{entry.get('level', 'INFO')} {entry.get('component', '')}: {entry['msg']}"
                if entry.get("error"):
                    display += f" | {entry['error']}"
                is_error = entry.get("level") in {"ERROR", "FATAL"}
        except (ValueError, TypeError):
            pass
        with self.lock:
            self.logs.append(display)
            if is_error:
                self.state["last_error"] = display

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            result = {**self.state, "logs": list(self.logs)}
        processes = []
        if result["pid"]:
            try:
                parent = psutil.Process(result["pid"])
                for process in [parent, *parent.children(recursive=True)]:
                    try:
                        processes.append({"pid": process.pid, "name": process.name(), "status": process.status()})
                    except psutil.NoSuchProcess:
                        pass
            except psutil.NoSuchProcess:
                pass
        result["active_processes"] = processes[:20]
        result["process_count"] = len(processes)
        return result

    def set_state(self, **values: Any) -> None:
        with self.lock:
            self.state.update(values)

    def read_log(self, pipe: BinaryIO) -> None:
        pending = b""
        try:
            read = getattr(pipe, "read1", pipe.read)
            while chunk := read(4096):
                pending += chunk
                while b"\n" in pending:
                    line, pending = pending.split(b"\n", 1)
                    self.event(line.decode("utf-8", errors="replace").rstrip())
                if len(pending) > 65536:
                    if self.secret:
                        pending = pending.replace(self.secret.encode(), b"[redacted]")
                    # Retain overlap so a credential split between reads is still redacted.
                    keep = max(len(self.secret.encode()), 4)
                    self.event(pending[:-keep].decode("utf-8", errors="replace"))
                    pending = pending[-keep:]
            if pending:
                self.event(pending.decode("utf-8", errors="replace").rstrip())
        finally:
            pipe.close()

    def healthy(self) -> bool:
        try:
            heartbeat_ok = time.time() - self.heartbeat.stat().st_mtime < 15
        except OSError:
            heartbeat_ok = False
        ready: bool | None = None
        if self.readiness_url:
            try:
                with urllib.request.urlopen(self.readiness_url, timeout=2) as response:
                    ready = response.status == 200
            except (OSError, ValueError):
                ready = False
        self.set_state(mcp_healthy=heartbeat_ok, tunnel_healthy=ready)
        return heartbeat_ok and ready is not False

    def remember_children(self, process: subprocess.Popen[bytes]) -> None:
        try:
            parent = psutil.Process(process.pid)
            children = parent.children(recursive=True)
            excluded: set[int] = set()
            for child in children:
                try:
                    if "scripts.job_worker" in child.cmdline():
                        excluded.update([child.pid, *(p.pid for p in child.children(recursive=True))])
                except psutil.NoSuchProcess:
                    pass
            for child in [parent, *children]:
                if child.pid in excluded:
                    self.owned.pop(child.pid, None)
                    continue
                self.owned[child.pid] = child
        except psutil.NoSuchProcess:
            pass

    def cleanup(self, process: subprocess.Popen[bytes]) -> None:
        self.remember_children(process)
        # psutil Process objects check creation time before signalling, avoiding PID reuse.
        for child in reversed(list(self.owned.values())):
            try:
                child.kill()
            except psutil.NoSuchProcess:
                pass
            except psutil.AccessDenied:
                self.event(f"Access denied cleaning up runtime pid={child.pid}")
        psutil.wait_procs(list(self.owned.values()), timeout=5)
        self.owned.clear()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.event("Runtime process did not exit after cleanup.")

    def run(self) -> int:
        failures = 0
        attempts = 0
        try:
            while not self.stop.is_set():
                self.restart.clear()
                self.heartbeat.unlink(missing_ok=True)
                env = os.environ.copy()
                env["MCP_HEARTBEAT_FILE"] = str(self.heartbeat)
                started = time.monotonic()
                process = None
                readers: list[threading.Thread] = []
                reason = "runtime_exit"
                try:
                    process = subprocess.Popen(self.command, cwd=PROJECT_ROOT, env=env,
                                               stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                               creationflags=_creation_flags())
                    self.set_state(state="starting", pid=process.pid, restart_count=attempts, next_retry_seconds=None)
                    self.event(f"Runtime started pid={process.pid} restart_count={attempts}")
                    for pipe in (process.stdout, process.stderr):
                        assert pipe is not None
                        thread = threading.Thread(target=self.read_log, args=(pipe,), daemon=True)
                        thread.start()
                        readers.append(thread)
                    missed = 0
                    while process.poll() is None and not self.stop.is_set() and not self.restart.is_set():
                        self.remember_children(process)
                        if self.healthy():
                            missed = 0
                            self.set_state(state="running")
                        elif time.monotonic() - started > self.grace:
                            missed += 1
                            self.set_state(state="unhealthy", failed_probes=missed)
                            if missed >= 3:
                                reason = "watchdog_unhealthy"
                                break
                        self.stop.wait(self.interval)
                    if self.stop.is_set():
                        reason = "user_stop"
                    elif self.restart.is_set():
                        reason = "user_restart"
                    code = process.poll()
                    self.event(f"Runtime stopped reason={reason} exit_code={code}")
                    self.set_state(last_exit_code=code, last_exit_reason=reason)
                    if not reason.startswith("user_") and not self.snapshot()["last_error"]:
                        self.set_state(last_error=reason)
                    if reason in {"watchdog_unhealthy", "user_restart", "user_stop"}:
                        audit_action("supervisor_terminate_runtime", target=str(process.pid), details={"reason": reason})
                except Exception as exc:
                    self.event(f"Runtime launch failed: {type(exc).__name__}: {exc}")
                    self.set_state(last_error=str(exc))
                finally:
                    if process is not None:
                        self.cleanup(process)
                        self.set_state(last_exit_code=process.returncode)
                        self.event(f"Runtime cleanup completed pid={process.pid} exit_code={process.returncode}")
                    for thread in readers:
                        thread.join(timeout=2)
                if self.stop.is_set():
                    break
                stable = time.monotonic() - started >= 300
                failures = 1 if stable else failures + 1
                delay = 0 if reason == "user_restart" else restart_delay(failures)
                attempts += 1
                self.set_state(state="retrying", pid=None, next_retry_seconds=delay, restart_count=attempts)
                self.event(f"Retry in {delay}s (consecutive_failures={failures})")
                print(f"MCP restart in {delay}s; reason={reason}", flush=True)
                deadline = time.monotonic() + delay
                self.restart.clear()
                while time.monotonic() < deadline and not self.stop.is_set() and not self.restart.is_set():
                    self.stop.wait(0.2)
            return 130  # Tell BAT that the panel Stop is intentional.
        except KeyboardInterrupt:
            self.stop.set()
            return 130
        finally:
            self.set_state(state="stopped", pid=None, mcp_healthy=False)
            self.heartbeat.unlink(missing_ok=True)
            for handler in list(self.logger.handlers):
                handler.close()
                self.logger.removeHandler(handler)


def make_panel(supervisor: Supervisor, port: int) -> ThreadingHTTPServer:
    token = secrets.token_urlsafe(32)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:
            pass

        def valid_host(self) -> bool:
            return self.headers.get("Host") == f"127.0.0.1:{cast(ThreadingHTTPServer, self.server).server_port}"

        def send(self, data: bytes, content_type: str = "application/json", status: int = 200) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self) -> None:
            if not self.valid_host():
                self.send(b'{}', status=403)
                return
            if self.path == "/":
                html = (PROJECT_ROOT / "tools" / "panel" / "index.html").read_text(encoding="utf-8")
                self.send(html.replace("__CONTROL_TOKEN__", token).encode(), "text/html; charset=utf-8")
            elif self.path == "/favicon.ico":
                self.send(b"", "image/x-icon", 204)
            elif self.path == "/api/status":
                snapshot = supervisor.snapshot()
                snapshot["jobs"] = JobStore().list(0, 20)
                snapshot["storage_bytes"] = {
                    name: sum(p.stat().st_size for p in (supervisor.state_dir / name).rglob("*") if p.is_file())
                    for name in ("jobs", "artifacts")
                }
                self.send(json.dumps(snapshot).encode())
            else:
                self.send(b'{}', status=404)

        def do_POST(self) -> None:
            origin = f"http://127.0.0.1:{cast(ThreadingHTTPServer, self.server).server_port}"
            if (not self.valid_host() or self.headers.get("Origin") != origin
                    or not secrets.compare_digest(self.headers.get("X-Control-Token", ""), token)):
                self.send(b'{}', status=403)
                return
            if self.path == "/api/restart":
                supervisor.restart.set()
            elif self.path == "/api/stop":
                supervisor.stop.set()
            else:
                self.send(b'{}', status=404)
                return
            self.send(b'{"ok":true}')

    return ThreadingHTTPServer(("127.0.0.1", port), Handler)


def _default_profile() -> str:
    env_profile = os.getenv("MCP_TUNNEL_PROFILE")
    if env_profile:
        return env_profile
    tunnel_client = PROJECT_ROOT / "tunnel-client.exe"
    if tunnel_client.is_file():
        try:
            result = subprocess.run(
                [str(tunnel_client), "profiles", "list"],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            for line in result.stdout.splitlines():
                parts = line.split("\t")
                if len(parts) >= 2 and parts[0].strip():
                    return parts[0].strip()
        except Exception:
            pass
    return "default"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["tunnel", "local-http"], default="tunnel")
    parser.add_argument("--profile", default=_default_profile())
    parser.add_argument("--panel-port", type=int, default=8766)
    args = parser.parse_args()
    command = ([str(PROJECT_ROOT / "tunnel-client.exe"), "run", "--profile", args.profile]
               if args.mode == "tunnel" else
               [sys.executable, str(PROJECT_ROOT / "main.py"), "--transport", "streamable-http"])
    supervisor = Supervisor(command, readiness_url="http://127.0.0.1:8080/readyz" if args.mode == "tunnel" else None)
    panel = make_panel(supervisor, args.panel_port)
    thread = threading.Thread(target=panel.serve_forever, daemon=True)
    thread.start()
    print(f"MCP panel: http://127.0.0.1:{panel.server_port} (keep this window open)", flush=True)
    try:
        return supervisor.run()
    finally:
        panel.shutdown()
        panel.server_close()


if __name__ == "__main__":
    raise SystemExit(main())
