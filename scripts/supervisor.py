"""Foreground runtime supervisor and loopback control panel. No installation or autostart."""

from __future__ import annotations

import argparse
import copy
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
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, BinaryIO, cast

import psutil

from core.audit import audit_action
from core.config import PROJECT_ROOT, SETTINGS
from core.executor import _creation_flags
from core.jobs import JobStore
from core.lifecycle import lifecycle_control_request
from core.singleflight import SingleFlight


def restart_delay(failures: int) -> int:
    return (5, 10, 30, 60)[min(max(failures - 1, 0), 3)]


PANEL_PROCESS_TTL_SEC = 2.0
PANEL_JOBS_TTL_SEC = 2.0
PANEL_STORAGE_TTL_SEC = 20.0
LIFECYCLE_STATUS_MAX_BYTES = 16_384
LIFECYCLE_POLL_SEC = 0.025
LIFECYCLE_START_GRACE_SEC = 0.25
LIFECYCLE_STOP_ACK_SEC = 0.25


class PanelStatusCache:
    """Small per-component TTL cache with single-flight refreshes."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self._entries: dict[str, tuple[float, Any]] = {}
        self._refreshes: SingleFlight[str, Any] = SingleFlight()

    def get(self, key: str, ttl: float, loader: Callable[[], Any]) -> Any:
        if ttl <= 0:
            raise ValueError("Panel cache TTL must be positive.")
        now = self._clock()
        with self._lock:
            entry = self._entries.get(key)
            if entry is not None and entry[0] > now:
                return copy.deepcopy(entry[1])

        def refresh() -> Any:
            # Another caller may have refreshed between our initial miss and
            # acquiring the single-flight ownership. Recheck before I/O.
            current = self._clock()
            with self._lock:
                cached = self._entries.get(key)
                if cached is not None and cached[0] > current:
                    return copy.deepcopy(cached[1])
            value = loader()
            stored = copy.deepcopy(value)
            with self._lock:
                self._entries[key] = (self._clock() + ttl, stored)
            return copy.deepcopy(stored)

        return self._refreshes.run(key, refresh)


class Supervisor:
    def __init__(self, command: list[str], *, readiness_url: str | None,
                 state_dir: Path | None = None, grace: float = 90, interval: float = 5,
                 drain_timeout: float | None = None, watchdog_drain_timeout: float | None = None) -> None:
        self.command = command
        self.readiness_url = readiness_url
        self.state_dir = state_dir or SETTINGS.state_dir
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.heartbeat = self.state_dir / f"heartbeat-{uuid.uuid4().hex}"
        self.grace = grace
        self.interval = interval
        self.drain_timeout = float(SETTINGS.supervisor_drain_sec if drain_timeout is None else drain_timeout)
        self.watchdog_drain_timeout = float(
            SETTINGS.supervisor_watchdog_drain_sec if watchdog_drain_timeout is None else watchdog_drain_timeout
        )
        if self.drain_timeout <= 0 or self.watchdog_drain_timeout <= 0:
            raise ValueError("Supervisor drain timeouts must be positive.")
        self.stop = threading.Event()
        self.restart = threading.Event()
        self.lock = threading.Lock()
        self.state: dict[str, Any] = {
            "state": "starting",
            "restart_count": 0,
            "last_error": None,
            "pid": None,
            "mcp_healthy": False,
            "tunnel_healthy": None,
            "runtime_lifecycle": None,
            "active_mutations": 0,
            "last_drain_result": None,
        }
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

    def state_snapshot(self) -> dict[str, Any]:
        with self.lock:
            return {**self.state, "logs": list(self.logs)}

    def process_snapshot(self) -> dict[str, Any]:
        with self.lock:
            pid = self.state["pid"]
        processes = []
        if pid:
            try:
                parent = psutil.Process(pid)
                for process in [parent, *parent.children(recursive=True)]:
                    try:
                        processes.append({"pid": process.pid, "name": process.name(), "status": process.status()})
                    except psutil.NoSuchProcess:
                        pass
            except psutil.NoSuchProcess:
                pass
        return {"active_processes": processes[:20], "process_count": len(processes)}

    def snapshot(self) -> dict[str, Any]:
        result = self.state_snapshot()
        result.update(self.process_snapshot())
        return result

    def set_state(self, **values: Any) -> None:
        with self.lock:
            self.state.update(values)

    def _runtime_lifecycle_paths(self) -> tuple[Path, Path]:
        directory = self.state_dir / "runtime_lifecycle"
        directory.mkdir(parents=True, exist_ok=True)
        token = uuid.uuid4().hex
        return directory / f"control-{token}.json", directory / f"status-{token}.json"

    @staticmethod
    def _write_lifecycle_control(path: Path, command: str, request_id: str, deadline: float) -> None:
        payload = lifecycle_control_request(cast(Any, command), request_id, deadline)
        encoded = json.dumps(payload, separators=(",", ":"), sort_keys=True)
        last_error: OSError | None = None
        for attempt in range(20):
            temporary = path.with_name(f".{path.name}.{os.getpid()}.{attempt}.tmp")
            try:
                temporary.write_text(encoded, encoding="utf-8")
                os.replace(temporary, path)
                return
            except OSError as exc:
                last_error = exc
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass
                if attempt < 19:
                    time.sleep(0.01)
        assert last_error is not None
        raise last_error

    @staticmethod
    def _read_lifecycle_status(path: Path) -> dict[str, Any] | None:
        try:
            if path.stat().st_size > LIFECYCLE_STATUS_MAX_BYTES:
                return None
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return None
        if not isinstance(payload, dict) or payload.get("schema_version") != 1:
            return None
        return payload

    def drain_runtime(
        self,
        process: subprocess.Popen[bytes],
        *,
        reason: str,
        control_path: Path,
        status_path: Path,
    ) -> dict[str, Any]:
        """Request a bounded mutation drain before terminating one runtime generation."""
        timeout = self.watchdog_drain_timeout if reason == "watchdog_unhealthy" else self.drain_timeout
        request_id = uuid.uuid4().hex
        started = time.monotonic()
        deadline_monotonic = started + timeout
        deadline_epoch = time.time() + timeout
        self._write_lifecycle_control(control_path, "drain", request_id, deadline_epoch)
        acknowledged = False
        drained = False
        active_mutations: int | None = None
        runtime_state: str | None = None
        saw_status = False
        start_grace = min(LIFECYCLE_START_GRACE_SEC, timeout)

        while time.monotonic() < deadline_monotonic and process.poll() is None:
            status = self._read_lifecycle_status(status_path)
            if status is not None:
                saw_status = True
                runtime_state = str(status.get("state"))
                try:
                    active_mutations = int(status.get("active_mutations", 0))
                except (TypeError, ValueError):
                    active_mutations = None
                if status.get("request_id") == request_id and runtime_state in {"DRAINING", "STOPPING"}:
                    acknowledged = True
                    self.set_state(
                        state="draining",
                        runtime_lifecycle=runtime_state,
                        active_mutations=active_mutations,
                    )
                    if active_mutations == 0:
                        drained = True
                        break
            elif not saw_status and time.monotonic() - started >= start_grace:
                # Only give up early when this runtime generation never published
                # any lifecycle status. After one valid status, transient Windows
                # sharing/read failures must not be mistaken for an unstarted MCP.
                break
            time.sleep(LIFECYCLE_POLL_SEC)

        if drained and process.poll() is None:
            self._write_lifecycle_control(control_path, "stop", request_id, deadline_epoch)
            stop_deadline = min(deadline_monotonic, time.monotonic() + LIFECYCLE_STOP_ACK_SEC)
            while time.monotonic() < stop_deadline and process.poll() is None:
                status = self._read_lifecycle_status(status_path)
                if status is not None and status.get("request_id") == request_id:
                    runtime_state = str(status.get("state"))
                    try:
                        active_mutations = int(status.get("active_mutations", 0))
                    except (TypeError, ValueError):
                        active_mutations = None
                    if runtime_state == "STOPPING":
                        break
                time.sleep(LIFECYCLE_POLL_SEC)

        elapsed_ms = round((time.monotonic() - started) * 1_000, 3)
        result = {
            "reason": reason,
            "acknowledged": acknowledged,
            "drained": drained,
            "timed_out": saw_status and not drained and time.monotonic() >= deadline_monotonic,
            "runtime_lifecycle": runtime_state,
            "active_mutations": active_mutations,
            "elapsed_ms": elapsed_ms,
        }
        self.set_state(
            runtime_lifecycle=runtime_state,
            active_mutations=active_mutations or 0,
            last_drain_result=result,
        )
        self.event(
            "Runtime drain "
            f"reason={reason} acknowledged={acknowledged} drained={drained} "
            f"active_mutations={active_mutations} elapsed_ms={elapsed_ms}"
        )
        return result

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
                control_path, status_path = self._runtime_lifecycle_paths()
                control_path.unlink(missing_ok=True)
                status_path.unlink(missing_ok=True)
                env = os.environ.copy()
                env["MCP_HEARTBEAT_FILE"] = str(self.heartbeat)
                env["MCP_LIFECYCLE_CONTROL_FILE"] = str(control_path)
                env["MCP_LIFECYCLE_STATUS_FILE"] = str(status_path)
                env["MCP_RUNTIME_GENERATION_ID"] = control_path.stem.removeprefix("control-")
                started = time.monotonic()
                process = None
                readers: list[threading.Thread] = []
                reason = "runtime_exit"
                try:
                    process = subprocess.Popen(self.command, cwd=PROJECT_ROOT, env=env,
                                               stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                               creationflags=_creation_flags())
                    self.set_state(
                        state="starting",
                        pid=process.pid,
                        restart_count=attempts,
                        next_retry_seconds=None,
                        runtime_lifecycle=None,
                        active_mutations=0,
                    )
                    self.event(f"Runtime started pid={process.pid} restart_count={attempts}")
                    for pipe in (process.stdout, process.stderr):
                        assert pipe is not None
                        thread = threading.Thread(target=self.read_log, args=(pipe,), daemon=True)
                        thread.start()
                        readers.append(thread)
                    missed = 0
                    try:
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
                    except KeyboardInterrupt:
                        self.stop.set()
                        reason = "user_stop"
                    if self.stop.is_set():
                        reason = "user_stop"
                    elif self.restart.is_set():
                        reason = "user_restart"
                    if process.poll() is None and reason in {"watchdog_unhealthy", "user_restart", "user_stop"}:
                        self.drain_runtime(
                            process,
                            reason=reason,
                            control_path=control_path,
                            status_path=status_path,
                        )
                    code = process.poll()
                    self.event(f"Runtime stopping reason={reason} exit_code={code}")
                    self.set_state(last_exit_code=code, last_exit_reason=reason)
                    if not reason.startswith("user_") and not self.snapshot()["last_error"]:
                        self.set_state(last_error=reason)
                    if reason in {"watchdog_unhealthy", "user_restart", "user_stop"}:
                        audit_action("supervisor_terminate_runtime", target=str(process.pid), details={"reason": reason}, durable=True)
                except Exception as exc:
                    self.event(f"Runtime launch failed: {type(exc).__name__}: {exc}")
                    self.set_state(last_error=str(exc))
                finally:
                    if process is not None:
                        self.cleanup(process)
                        self.set_state(last_exit_code=process.returncode)
                        self.event(f"Runtime cleanup completed pid={process.pid} exit_code={process.returncode}")
                    control_path.unlink(missing_ok=True)
                    status_path.unlink(missing_ok=True)
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
    store = JobStore(supervisor.state_dir / "jobs.sqlite3")
    cache = PanelStatusCache()

    def storage_bytes() -> dict[str, int]:
        storage: dict[str, int] = {}
        for name in ("jobs", "artifacts"):
            total = 0
            for path in (supervisor.state_dir / name).rglob("*"):
                try:
                    if path.is_file():
                        total += path.stat().st_size
                except (FileNotFoundError, PermissionError):
                    continue  # Concurrent finalization or cleanup may remove a path.
            storage[name] = total
        return storage

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
                snapshot = supervisor.state_snapshot()
                snapshot.update(cache.get("processes", PANEL_PROCESS_TTL_SEC, supervisor.process_snapshot))
                snapshot["jobs"] = cache.get("jobs", PANEL_JOBS_TTL_SEC, lambda: store.list(0, 20))
                snapshot["storage_bytes"] = cache.get("storage", PANEL_STORAGE_TTL_SEC, storage_bytes)
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
