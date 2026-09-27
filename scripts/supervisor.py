"""Foreground runtime supervisor and loopback control panel. No installation or autostart."""

from __future__ import annotations

import argparse
import copy
import json
import logging
import logging.handlers
import os
import secrets
import shlex
import subprocess
import sys
import threading
import time
import urllib.request
import uuid
from collections import deque
from collections.abc import Callable
from contextlib import closing
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, BinaryIO, cast
from urllib.parse import parse_qs, urlsplit

import psutil

from core.audit import audit_action
from core.config import PROJECT_ROOT, SETTINGS
from core.errors import ToolError
from core.executor import _creation_flags
from core.jobs import JobStore
from core.lifecycle import lifecycle_control_request
from core.singleflight import SingleFlight
from core.transport_health import classify_transport_health
from core.workflow_store import workflow_store
from scripts.panel_data import (
    build_identity,
    config_snapshot,
    doctor_snapshot,
    memory_summary,
    process_details,
    redact_diagnostics,
    storage_breakdown,
    summarize_transport_history,
    system_snapshot,
    tail_jsonl,
    timing_summary,
    tunnel_snapshot,
)
from scripts.tunnel_runtime import (
    TunnelRuntimeError,
    clean_control_plane_key,
    current_runtime,
    detect_profile,
    profile_run_args,
)


def restart_delay(failures: int) -> int:
    return (5, 10, 30, 60)[min(max(failures - 1, 0), 3)]


PANEL_PROCESS_TTL_SEC = 2.0
PANEL_JOBS_TTL_SEC = 2.0
PANEL_STORAGE_TTL_SEC = 20.0
PANEL_JOB_OUTPUT_PREVIEW_BYTES = 32 * 1024
LIFECYCLE_STATUS_MAX_BYTES = 16_384
LIFECYCLE_POLL_SEC = 0.025
LIFECYCLE_START_GRACE_SEC = 0.25
LIFECYCLE_STOP_ACK_SEC = 0.25
TRANSPORT_HEALTH_MAX_BYTES = 128 * 1024
TOOL_ACTIVITY_MAX_BYTES = 16 * 1024
TRANSPORT_HISTORY_MAX_BYTES = 4 * 1024 * 1024
TRANSPORT_HISTORY_BACKUPS = 3


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
        self.health_details_url = (
            readiness_url.rsplit("/", 1)[0] + "/health?details=true"
            if readiness_url is not None
            else None
        )
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
        started_at = time.time()
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
            "supervisor_started_at": started_at,
            "runtime_started_at": None,
            "last_runtime_started_at": None,
            "last_runtime_stopped_at": None,
            "last_state_change_at": started_at,
            "last_event_at": None,
            "last_exit_code": None,
            "last_exit_reason": None,
            "next_retry_seconds": None,
            "failed_probes": 0,
            "runtime_generation": 0,
            "transport_diagnosis": "LOCAL_MODE" if readiness_url is None else "UNKNOWN",
            "transport_health": None,
            "tool_activity": None,
            "transport_stall_observations": 0,
            "transport_restart_confirmed": False,
            "last_transport_recovery_at": None,
        }
        self.logs: deque[str] = deque(maxlen=100)
        self.events: deque[dict[str, Any]] = deque(maxlen=200)
        self.errors: deque[dict[str, Any]] = deque(maxlen=50)
        self.event_count = 0
        self.error_count = 0
        self.logger = logging.getLogger(f"mcp.supervisor.{uuid.uuid4().hex}")
        self.logger.setLevel(logging.INFO)
        self.logger.propagate = False
        handler = logging.handlers.RotatingFileHandler(self.state_dir / "supervisor.log", maxBytes=2_097_152,
                                                       backupCount=3, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
        self.logger.addHandler(handler)
        self.transport_logger = logging.getLogger(f"mcp.transport.{uuid.uuid4().hex}")
        self.transport_logger.setLevel(logging.INFO)
        self.transport_logger.propagate = False
        transport_handler = logging.handlers.RotatingFileHandler(
            self.state_dir / "transport-health.jsonl",
            maxBytes=TRANSPORT_HISTORY_MAX_BYTES,
            backupCount=TRANSPORT_HISTORY_BACKUPS,
            encoding="utf-8",
        )
        transport_handler.setFormatter(logging.Formatter("%(message)s"))
        self.transport_logger.addHandler(transport_handler)
        self.secret = clean_control_plane_key(os.environ.get("CONTROL_PLANE_API_KEY", ""))
        self.owned: dict[int, psutil.Process] = {}
        self.current_tool_activity_path: Path | None = None
        self.current_runtime_health_path: Path | None = None
        self._transport_stall_observations = 0
        self._last_transport_history_at = 0.0
        self._last_transport_diagnosis: str | None = None

    @staticmethod
    def _plain_event_level(message: str) -> str:
        lowered = message.casefold()
        if any(marker in lowered for marker in ("launch failed", "fallback kill failed", "fatal", "traceback")):
            return "ERROR"
        if any(marker in lowered for marker in (
            "unhealthy", "degraded", "access denied", "os error", "did not exit",
            "timed out", "timeout", "could not", "unavailable",
        )):
            return "WARN"
        return "INFO"

    @staticmethod
    def _runtime_event_level(level: str, component: str, message: str, error: str) -> str:
        """Normalize known non-fatal upstream cancellation noise without hiding real failures."""
        if (
            level in {"ERROR", "FATAL"}
            and component == "dispatcher"
            and message == "failed to post error response to control plane"
            and "controlplane responder: retry wait: context canceled" in error.casefold()
        ):
            return "WARN"
        return level

    def event(self, message: str) -> None:
        if self.secret:
            message = message.replace(self.secret, "[redacted]")
        self.logger.info(message)
        display = message
        level = self._plain_event_level(message)
        component = "supervisor"
        try:
            entry = json.loads(message)
            if isinstance(entry, dict) and "msg" in entry:
                raw_level = str(entry.get("level", "INFO")).upper()
                level = "WARN" if raw_level == "WARNING" else raw_level
                component = str(entry.get("component") or "runtime")
                entry_message = str(entry["msg"])
                entry_error = str(entry.get("error") or "")
                level = self._runtime_event_level(level, component, entry_message, entry_error)
                if level == "INFO" and entry_message in {
                    "run", "provided", "invoking", "OnStart hook executing", "OnStart hook executed",
                }:
                    return  # Keep framework wiring noise in the rotated file, not the panel.
                display = f"{level} {component}: {entry_message}"
                if entry_error:
                    display += f" | {entry_error}"
        except (ValueError, TypeError):
            pass

        occurred_at = time.time()
        event = {
            "timestamp": occurred_at,
            "level": level,
            "component": component,
            "message": display,
        }
        with self.lock:
            self.logs.append(display)
            self.events.append(event)
            self.event_count += 1
            self.state["last_event_at"] = occurred_at
            if level in {"ERROR", "FATAL"}:
                self.errors.append(event)
                self.error_count += 1
                self.state["last_error"] = display

    def state_snapshot(self) -> dict[str, Any]:
        now = time.time()
        with self.lock:
            snapshot = {
                **self.state,
                "logs": list(self.logs),
                "recent_events": [dict(item) for item in self.events],
                "recent_errors": [dict(item) for item in self.errors],
                "event_count": self.event_count,
                "error_count": self.error_count,
                "server_time": now,
            }
        supervisor_started_at = snapshot.get("supervisor_started_at")
        runtime_started_at = snapshot.get("runtime_started_at")
        snapshot["supervisor_uptime_seconds"] = (
            max(now - float(supervisor_started_at), 0.0) if supervisor_started_at else 0.0
        )
        snapshot["runtime_uptime_seconds"] = (
            max(now - float(runtime_started_at), 0.0) if runtime_started_at else None
        )
        return snapshot

    def process_snapshot(self) -> dict[str, Any]:
        with self.lock:
            pid = self.state["pid"]
        processes = []
        if pid:
            try:
                parent = psutil.Process(pid)
                try:
                    children = parent.children(recursive=True)
                except (psutil.NoSuchProcess, psutil.AccessDenied, OSError) as exc:
                    self.event(f"Process-tree inspection degraded for pid={pid}: {type(exc).__name__}: {exc}")
                    children = []
                for index, process in enumerate([parent, *children]):
                    try:
                        item: dict[str, Any] = {
                            "pid": process.pid,
                            "name": process.name(),
                            "status": process.status(),
                        }
                        try:
                            item["role"] = "runtime" if index == 0 else "child"
                            item["memory_mb"] = round(process.memory_info().rss / 1_048_576, 1)
                            item["created_at"] = process.create_time()
                        except (psutil.NoSuchProcess, psutil.AccessDenied, OSError, AttributeError):
                            pass
                        processes.append(item)
                    except (psutil.NoSuchProcess, psutil.AccessDenied, OSError, AttributeError):
                        pass
            except (psutil.NoSuchProcess, psutil.AccessDenied, OSError) as exc:
                if not isinstance(exc, psutil.NoSuchProcess):
                    self.event(f"Process inspection unavailable for pid={pid}: {type(exc).__name__}: {exc}")
        return {"active_processes": processes[:20], "process_count": len(processes)}

    def snapshot(self) -> dict[str, Any]:
        result = self.state_snapshot()
        result.update(self.process_snapshot())
        return result

    def set_state(self, **values: Any) -> None:
        with self.lock:
            next_state = values.get("state")
            if next_state is not None and next_state != self.state.get("state"):
                values.setdefault("last_state_change_at", time.time())
            self.state.update(values)

    @staticmethod
    def _read_bounded_json(path: Path | None, max_bytes: int) -> dict[str, Any] | None:
        if path is None:
            return None
        try:
            if path.stat().st_size > max_bytes:
                return None
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return None
        return payload if isinstance(payload, dict) else None

    def _fetch_tunnel_health(self) -> dict[str, Any] | None:
        if self.health_details_url is None:
            return None
        try:
            with urllib.request.urlopen(self.health_details_url, timeout=2) as response:
                if response.status != 200:
                    return None
                raw = response.read(TRANSPORT_HEALTH_MAX_BYTES + 1)
        except (OSError, ValueError):
            return None
        if len(raw) > TRANSPORT_HEALTH_MAX_BYTES:
            return None
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeError, ValueError, TypeError):
            return None
        return payload if isinstance(payload, dict) else None

    def _record_transport_history(self, summary: dict[str, Any]) -> None:
        now = time.time()
        diagnosis = str(summary.get("diagnosis") or "UNKNOWN")
        interval_elapsed = now - self._last_transport_history_at >= SETTINGS.transport_history_interval_sec
        if diagnosis == self._last_transport_diagnosis and not interval_elapsed:
            return
        record = {
            "time": now,
            "diagnosis": diagnosis,
            "severity": summary.get("severity"),
            "reason": summary.get("reason"),
            "ready": summary.get("ready"),
            "restart_recommended": summary.get("restart_recommended"),
            "restart_confirmed": summary.get("restart_confirmed"),
            "stall_observations": summary.get("stall_observations"),
            "runtime": summary.get("runtime"),
            "control_plane": summary.get("control_plane"),
            "queue": summary.get("queue"),
            "dispatcher": summary.get("dispatcher"),
            "response_delivery": summary.get("response_delivery"),
            "tool_activity": summary.get("tool_activity"),
            "diagnostic_ages": summary.get("diagnostic_ages"),
        }
        self.transport_logger.info(json.dumps(record, ensure_ascii=False, separators=(",", ":")))
        self._last_transport_history_at = now
        self._last_transport_diagnosis = diagnosis

    def transport_snapshot(self, ready: bool | None) -> dict[str, Any]:
        activity = self._read_bounded_json(self.current_tool_activity_path, TOOL_ACTIVITY_MAX_BYTES)
        detailed = self._fetch_tunnel_health() if self.health_details_url is not None else None
        summary = classify_transport_health(
            detailed,
            tool_activity=activity,
            ready=ready,
            tunnel_mode=self.health_details_url is not None,
            now=time.time(),
            poll_stall_grace_sec=SETTINGS.tunnel_poll_stall_grace_sec,
            upstream_idle_sec=SETTINGS.transport_upstream_idle_sec,
            mcp_tool_stall_sec=SETTINGS.mcp_tool_stall_sec,
        )
        if summary["diagnosis"] == "POLL_STALLED" and summary["restart_recommended"]:
            self._transport_stall_observations += 1
        else:
            self._transport_stall_observations = 0
        confirmed = (
            SETTINGS.tunnel_poll_watchdog_enabled
            and self._transport_stall_observations >= SETTINGS.tunnel_poll_stall_confirmations
        )
        summary["stall_observations"] = self._transport_stall_observations
        summary["restart_confirmed"] = confirmed
        summary["watchdog_enabled"] = SETTINGS.tunnel_poll_watchdog_enabled
        summary["required_confirmations"] = SETTINGS.tunnel_poll_stall_confirmations
        self.set_state(
            transport_diagnosis=summary["diagnosis"],
            transport_health=summary,
            tool_activity=activity,
            transport_stall_observations=self._transport_stall_observations,
            transport_restart_confirmed=confirmed,
        )
        self._record_transport_history(summary)
        return summary

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
        timeout = self.watchdog_drain_timeout if reason.startswith("watchdog_") else self.drain_timeout
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
        transport = self.transport_snapshot(ready)
        self.set_state(mcp_healthy=heartbeat_ok, tunnel_healthy=ready)
        return heartbeat_ok and ready is not False and not bool(transport.get("restart_confirmed"))

    def remember_children(self, process: subprocess.Popen[bytes]) -> None:
        try:
            parent = psutil.Process(process.pid)
        except (psutil.NoSuchProcess, psutil.AccessDenied, OSError) as exc:
            if not isinstance(exc, psutil.NoSuchProcess):
                self.event(f"Could not inspect runtime root pid={process.pid}: {type(exc).__name__}: {exc}")
            return

        # Track the root before any recursive inspection. If Windows process-table
        # enumeration later fails under memory/pagefile pressure, cleanup can still
        # terminate the runtime root instead of crashing the supervisor.
        self.owned[parent.pid] = parent
        try:
            children = parent.children(recursive=True)
        except (psutil.NoSuchProcess, psutil.AccessDenied, OSError) as exc:
            if not isinstance(exc, psutil.NoSuchProcess):
                self.event(f"Could not enumerate runtime children pid={process.pid}: {type(exc).__name__}: {exc}")
            return

        excluded: set[int] = set()
        for child in children:
            try:
                if "scripts.job_worker" in child.cmdline():
                    excluded.add(child.pid)
                    try:
                        excluded.update(p.pid for p in child.children(recursive=True))
                    except (psutil.NoSuchProcess, psutil.AccessDenied, OSError) as exc:
                        if not isinstance(exc, psutil.NoSuchProcess):
                            self.event(
                                f"Could not enumerate durable-worker descendants pid={child.pid}: "
                                f"{type(exc).__name__}: {exc}"
                            )
            except psutil.NoSuchProcess:
                excluded.add(child.pid)
            except (psutil.AccessDenied, OSError) as exc:
                # Unknown children are deliberately left untracked rather than risking
                # termination of a durable worker when process inspection is degraded.
                excluded.add(child.pid)
                self.event(f"Could not inspect runtime child pid={child.pid}: {type(exc).__name__}: {exc}")

        for child in children:
            if child.pid in excluded:
                self.owned.pop(child.pid, None)
                continue
            self.owned[child.pid] = child

    def cleanup(self, process: subprocess.Popen[bytes]) -> None:
        self.remember_children(process)
        tracked = list(self.owned.values())

        # Kill known non-durable descendants first, then the root. Every psutil call is
        # best-effort: diagnostics/cleanup must never take down the supervisor itself.
        for child in reversed(tracked):
            try:
                child.kill()
            except psutil.NoSuchProcess:
                pass
            except psutil.AccessDenied:
                self.event(f"Access denied cleaning up runtime pid={child.pid}")
            except OSError as exc:
                self.event(f"OS error cleaning up runtime pid={child.pid}: {type(exc).__name__}: {exc}")

        # If process-tree enumeration failed (for example WinError 1455 under transient
        # commit/pagefile pressure), the subprocess handle still gives us a reliable
        # way to terminate the runtime root and release ports such as 127.0.0.1:8080.
        try:
            if process.poll() is None:
                process.kill()
        except (ProcessLookupError, OSError) as exc:
            self.event(f"Runtime root fallback kill failed pid={process.pid}: {type(exc).__name__}: {exc}")

        if tracked:
            try:
                psutil.wait_procs(tracked, timeout=5)
            except (psutil.Error, OSError) as exc:
                self.event(f"Process cleanup wait degraded: {type(exc).__name__}: {exc}")
        self.owned.clear()

        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.event("Runtime process did not exit after cleanup.")
        except OSError as exc:
            self.event(f"Runtime wait failed pid={process.pid}: {type(exc).__name__}: {exc}")

    def run(self) -> int:
        failures = 0
        attempts = 0
        try:
            while not self.stop.is_set():
                self.restart.clear()
                self.heartbeat.unlink(missing_ok=True)
                control_path, status_path = self._runtime_lifecycle_paths()
                generation_id = control_path.stem.removeprefix("control-")
                activity_path = control_path.with_name(f"activity-{generation_id}.json")
                health_path = control_path.with_name(f"health-{generation_id}.json")
                control_path.unlink(missing_ok=True)
                status_path.unlink(missing_ok=True)
                activity_path.unlink(missing_ok=True)
                health_path.unlink(missing_ok=True)
                self.current_tool_activity_path = activity_path
                self.current_runtime_health_path = health_path
                self._transport_stall_observations = 0
                self._last_transport_history_at = 0.0
                self._last_transport_diagnosis = None
                env = os.environ.copy()
                env["MCP_HEARTBEAT_FILE"] = str(self.heartbeat)
                env["MCP_LIFECYCLE_CONTROL_FILE"] = str(control_path)
                env["MCP_LIFECYCLE_STATUS_FILE"] = str(status_path)
                env["MCP_RUNTIME_GENERATION_ID"] = generation_id
                env["MCP_TOOL_ACTIVITY_FILE"] = str(activity_path)
                env["MCP_RUNTIME_HEALTH_FILE"] = str(health_path)
                started = time.monotonic()
                process = None
                readers: list[threading.Thread] = []
                reason = "runtime_exit"
                try:
                    process = subprocess.Popen(self.command, cwd=PROJECT_ROOT, env=env,
                                               stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                               creationflags=_creation_flags())
                    runtime_started_at = time.time()
                    self.set_state(
                        state="starting",
                        pid=process.pid,
                        restart_count=attempts,
                        next_retry_seconds=None,
                        runtime_lifecycle=None,
                        active_mutations=0,
                        failed_probes=0,
                        runtime_started_at=runtime_started_at,
                        last_runtime_started_at=runtime_started_at,
                        runtime_generation=attempts + 1,
                        transport_diagnosis="LOCAL_MODE" if self.health_details_url is None else "UNKNOWN",
                        transport_health=None,
                        tool_activity=None,
                        transport_stall_observations=0,
                        transport_restart_confirmed=False,
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
                                self.set_state(state="running", failed_probes=0)
                            elif time.monotonic() - started > self.grace:
                                missed += 1
                                self.set_state(state="unhealthy", failed_probes=missed)
                                if missed >= 3:
                                    state = self.state_snapshot()
                                    if bool(state.get("transport_restart_confirmed")):
                                        reason = "watchdog_poll_stalled"
                                        self.set_state(last_transport_recovery_at=time.time())
                                        self.event(json.dumps({
                                            "level": "WARN",
                                            "component": "transport",
                                            "msg": "confirmed control-plane poll stall; restarting runtime",
                                        }))
                                    else:
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
                    if process.poll() is None and (
                        reason.startswith("watchdog_") or reason in {"user_restart", "user_stop"}
                    ):
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
                    if reason.startswith("watchdog_") or reason in {"user_restart", "user_stop"}:
                        audit_action(
                            "supervisor_terminate_runtime",
                            target=str(process.pid),
                            details={"reason": reason},
                            durable=True,
                        )
                except Exception as exc:
                    self.event(f"Runtime launch failed: {type(exc).__name__}: {exc}")
                    self.set_state(last_error=str(exc))
                finally:
                    if process is not None:
                        self.cleanup(process)
                        self.set_state(
                            last_exit_code=process.returncode,
                            last_runtime_stopped_at=time.time(),
                            runtime_started_at=None,
                        )
                        self.event(f"Runtime cleanup completed pid={process.pid} exit_code={process.returncode}")
                    control_path.unlink(missing_ok=True)
                    status_path.unlink(missing_ok=True)
                    activity_path.unlink(missing_ok=True)
                    health_path.unlink(missing_ok=True)
                    self.current_tool_activity_path = None
                    self.current_runtime_health_path = None
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
            for logger in (self.logger, self.transport_logger):
                for handler in list(logger.handlers):
                    handler.close()
                    logger.removeHandler(handler)


def make_panel(supervisor: Supervisor, port: int) -> ThreadingHTTPServer:
    token = secrets.token_urlsafe(32)
    store = JobStore(supervisor.state_dir / "jobs.sqlite3")
    cache = PanelStatusCache()

    def storage_bytes() -> dict[str, int]:
        storage = {
            "jobs": 0,
            "artifacts": 0,
            "databases": 0,
            "logs": 0,
            "other": 0,
            "total": 0,
        }
        for path in supervisor.state_dir.rglob("*"):
            try:
                if not path.is_file():
                    continue
                size = path.stat().st_size
                relative = path.relative_to(supervisor.state_dir)
            except (FileNotFoundError, PermissionError, OSError):
                continue
            storage["total"] += size
            top = relative.parts[0] if relative.parts else ""
            name = path.name.casefold()
            if top == "jobs":
                storage["jobs"] += size
            elif top == "artifacts":
                storage["artifacts"] += size
            elif (
                name.endswith((".sqlite", ".sqlite3", ".db", ".sqlite-wal", ".sqlite-shm", ".sqlite3-wal", ".sqlite3-shm"))
                or ".sqlite3-" in name
                or ".db-" in name
            ):
                storage["databases"] += size
            elif name.startswith(("supervisor.log", "audit", "transport-health")) or path.suffix.casefold() in {".log", ".jsonl"}:
                storage["logs"] += size
            else:
                storage["other"] += size
        return storage

    def job_snapshot(offset: int = 0, limit: int = 20) -> dict[str, Any]:
        bounded_offset = min(max(offset, 0), 1_000_000)
        bounded_limit = min(max(limit, 1), 100)
        result = store.list(bounded_offset, bounded_limit)
        counts: dict[str, int] = {}
        try:
            with closing(store.connect()) as connection:
                for row in connection.execute("SELECT status,COUNT(*) AS count FROM jobs GROUP BY status"):
                    counts[str(row["status"])] = int(row["count"])
        except (OSError, ValueError):
            counts = {}
        result["counts"] = counts
        result["active_count"] = sum(counts.get(status, 0) for status in ("queued", "running", "orphaned"))
        result["problem_count"] = sum(counts.get(status, 0) for status in ("failed", "timed_out", "interrupted"))
        return result

    def job_details(job_id: str) -> dict[str, Any]:
        status = store.get(job_id)
        raw = store.raw(job_id)
        try:
            spec = json.loads(str(raw["spec"]))
        except (TypeError, ValueError):
            spec = {}
        if not isinstance(spec, dict):
            spec = {}
        raw_command = spec.get("command")
        command = [str(item) for item in raw_command] if isinstance(raw_command, list) else []
        command_line = (
            subprocess.list2cmdline(command)
            if os.name == "nt"
            else shlex.join(command)
        ) if command else ""
        output = store.progress(job_id, max_bytes=PANEL_JOB_OUTPUT_PREVIEW_BYTES)
        return {
            "ok": True,
            **status,
            "worker_pid": raw.get("worker_pid"),
            "worker_created": raw.get("worker_created"),
            "pid_created": raw.get("pid_created"),
            "command": command,
            "command_line": command_line,
            "cwd": str(spec.get("cwd") or ""),
            "timeout_sec": spec.get("timeout_sec"),
            "queue_timeout_sec": spec.get("queue_timeout_sec"),
            "encoding": str(spec.get("encoding") or "utf-8"),
            "stdout": output["stdout"],
            "stderr": output["stderr"],
            "output_preview_limit_bytes": PANEL_JOB_OUTPUT_PREVIEW_BYTES,
        }

    def runtime_health_snapshot() -> dict[str, Any]:
        return supervisor._read_bounded_json(supervisor.current_runtime_health_path, 512 * 1024) or {}

    def summary_snapshot() -> dict[str, Any]:
        snapshot = supervisor.state_snapshot()
        for key in ("logs", "recent_events", "recent_errors"):
            snapshot.pop(key, None)
        snapshot.update(cache.get("processes", PANEL_PROCESS_TTL_SEC, supervisor.process_snapshot))
        jobs = cache.get("jobs_summary", PANEL_JOBS_TTL_SEC, lambda: job_snapshot(0, 1))
        jobs.pop("items", None)
        snapshot["jobs"] = jobs
        health = runtime_health_snapshot()
        snapshot["runtime_health_status"] = health.get("health_status")
        snapshot["resource_pressure"] = health.get("resource_pressure")
        snapshot["runtime_tool_count"] = health.get("tool_count")
        snapshot["panel_api_version"] = 2
        return snapshot

    def events_snapshot() -> dict[str, Any]:
        snapshot = supervisor.state_snapshot()
        return {
            "server_time": snapshot["server_time"],
            "event_count": snapshot["event_count"],
            "error_count": snapshot["error_count"],
            "recent_events": snapshot["recent_events"],
            "recent_errors": snapshot["recent_errors"],
            "logs": snapshot["logs"],
            "last_error": snapshot.get("last_error"),
        }

    def insights_snapshot() -> dict[str, Any]:
        return {
            "identity": cache.get("identity", 30.0, build_identity),
            "runtime_health": runtime_health_snapshot(),
            "tunnel": cache.get(
                "tunnel_identity",
                60.0,
                lambda: tunnel_snapshot(readiness_url=supervisor.readiness_url),
            ),
            "system": cache.get("system", 5.0, system_snapshot),
            "storage": cache.get(
                "storage_breakdown",
                PANEL_STORAGE_TTL_SEC,
                lambda: storage_breakdown(supervisor.state_dir),
            ),
            "memory": cache.get("memory", 30.0, memory_summary),
            "performance": cache.get(
                "performance",
                10.0,
                lambda: timing_summary(supervisor.state_dir / "timings.jsonl"),
            ),
        }

    def workflow_snapshot() -> dict[str, Any]:
        health = runtime_health_snapshot()
        recent = health.get("recent_workflows")
        if isinstance(recent, dict):
            return recent
        return {"items": [], "count": 0, "total_count": 0, "offset": 0, "has_more": False}

    def recovery_snapshot() -> dict[str, Any]:
        health = runtime_health_snapshot()
        recovery = health.get("operation_recovery")
        if not isinstance(recovery, dict):
            return {"items": [], "count": 0, "total_count": 0, "pending_count": 0}
        raw_items = recovery.get("uncertain")
        items = raw_items if isinstance(raw_items, list) else []
        return {
            "items": items[:100],
            "count": len(items[:100]),
            "total_count": int(recovery.get("uncertain_count", len(items)) or 0),
            "pending_count": int(recovery.get("pending_count", 0) or 0),
            "enabled": bool(recovery.get("enabled", True)),
        }

    def workflow_detail_snapshot(workflow_id: str) -> dict[str, Any]:
        store = workflow_store(supervisor.state_dir / "workflows.sqlite3")
        workflow = store.get(workflow_id)
        operations = store.list_operations(workflow_id, limit=100)
        safe_steps = [
            {
                "step_index": step.get("step_index"),
                "state": step.get("state"),
                "attempts": step.get("attempts"),
                "started_at": step.get("started_at"),
                "finished_at": step.get("finished_at"),
                "error": step.get("error"),
            }
            for step in workflow.get("steps", [])
            if isinstance(step, dict)
        ]
        safe_operations = []
        allowed_operation_keys = (
            "operation_id",
            "step_index",
            "operation_index",
            "action",
            "state",
            "attempts",
            "version",
            "started_at",
            "updated_at",
            "finished_at",
            "error",
        )
        for operation in operations.get("items", []):
            if isinstance(operation, dict):
                safe_operations.append({
                    key: operation.get(key)
                    for key in allowed_operation_keys
                    if key in operation
                })
        return {
            "workflow_id": workflow.get("workflow_id"),
            "state": workflow.get("state"),
            "current_step": workflow.get("current_step"),
            "version": workflow.get("version"),
            "execution_compatible": workflow.get("execution_compatible"),
            "cancel_requested_at": workflow.get("cancel_requested_at"),
            "cancel_reason": workflow.get("cancel_reason"),
            "created_at": workflow.get("created_at"),
            "updated_at": workflow.get("updated_at"),
            "last_error": workflow.get("last_error"),
            "steps": safe_steps,
            "operations": safe_operations,
            "operation_count": int(operations.get("total_count", len(safe_operations)) or 0),
            "operations_truncated": bool(operations.get("has_more", False)),
        }

    def transport_history_snapshot(limit: int = 100) -> dict[str, Any]:
        events = events_snapshot()["recent_events"]
        return summarize_transport_history(
            supervisor.state_dir / "transport-health.jsonl",
            limit=limit,
            recent_events=events,
        )

    def doctor_view() -> dict[str, Any]:
        insights = insights_snapshot()
        summary = summary_snapshot()
        transport = summary.get("transport_health")
        return doctor_snapshot(
            runtime_health=insights["runtime_health"],
            transport=transport if isinstance(transport, dict) else {},
            system=insights["system"],
            tunnel=insights["tunnel"],
        )

    def diagnostics_snapshot() -> dict[str, Any]:
        insights = insights_snapshot()
        events = events_snapshot()
        payload = {
            "generated_at": time.time(),
            "identity": insights["identity"],
            "summary": summary_snapshot(),
            "runtime_health": insights["runtime_health"],
            "tunnel": insights["tunnel"],
            "system": insights["system"],
            "storage": insights["storage"],
            "performance": insights["performance"],
            "config": config_snapshot(),
            "transport_history": transport_history_snapshot(100),
            "recent_events": events["recent_events"][-100:],
            "recent_errors": events["recent_errors"][-50:],
            "workflows": workflow_snapshot(),
            "recovery": recovery_snapshot(),
            "doctor": doctor_view(),
        }
        return cast(dict[str, Any], redact_diagnostics(payload, secret_values=(supervisor.secret, token)))

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:
            pass

        def valid_host(self) -> bool:
            return self.headers.get("Host") == f"127.0.0.1:{cast(ThreadingHTTPServer, self.server).server_port}"

        def authorized(self) -> bool:
            return secrets.compare_digest(self.headers.get("X-Control-Token", ""), token)

        def send(self, data: bytes, content_type: str = "application/json", status: int = 200) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; "
                "connect-src 'self'; img-src 'self' data:; object-src 'none'; frame-ancestors 'none'",
            )
            self.end_headers()
            self.wfile.write(data)

        def send_json(self, value: Any, status: int = 200) -> None:
            self.send(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8"), status=status)

        def send_download(self, path: Path, filename: str) -> None:
            try:
                size = path.stat().st_size
                source = path.open("rb")
            except OSError:
                self.send(b"{}", status=404)
                return
            with source:
                self.send_response(200)
                self.send_header("Content-Type", "application/octet-stream")
                self.send_header("Content-Length", str(size))
                self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("X-Frame-Options", "DENY")
                self.send_header("Referrer-Policy", "no-referrer")
                self.end_headers()
                while True:
                    chunk = source.read(64 * 1024)
                    if not chunk:
                        break
                    self.wfile.write(chunk)

        @staticmethod
        def _job_id(value: str) -> str | None:
            return value if len(value) == 32 and all(char in "0123456789abcdefABCDEF" for char in value) else None

        def _query_int(self, query: dict[str, list[str]], name: str, default: int, minimum: int, maximum: int) -> int:
            raw = query.get(name, [str(default)])[0]
            try:
                value = int(raw)
            except (TypeError, ValueError):
                value = default
            return min(max(value, minimum), maximum)

        def do_GET(self) -> None:
            if not self.valid_host():
                self.send(b"{}", status=403)
                return
            target = urlsplit(self.path)
            path = target.path
            query = parse_qs(target.query)
            if path == "/":
                html = (PROJECT_ROOT / "tools" / "panel" / "index.html").read_text(encoding="utf-8")
                self.send(html.replace("__CONTROL_TOKEN__", token).encode(), "text/html; charset=utf-8")
                return
            if path == "/favicon.ico":
                self.send(b"", "image/x-icon", 204)
                return
            if path == "/api/status":
                snapshot = supervisor.state_snapshot()
                snapshot.update(cache.get("processes", PANEL_PROCESS_TTL_SEC, supervisor.process_snapshot))
                snapshot["jobs"] = cache.get("jobs", PANEL_JOBS_TTL_SEC, lambda: job_snapshot(0, 20))
                snapshot["storage_bytes"] = cache.get("storage", PANEL_STORAGE_TTL_SEC, storage_bytes)
                snapshot["panel_api_version"] = 2
                self.send_json(snapshot)
                return
            if path == "/api/summary":
                self.send_json(summary_snapshot())
                return

            if not self.authorized():
                self.send(b"{}", status=403)
                return

            if path == "/api/insights":
                self.send_json(insights_snapshot())
            elif path == "/api/events":
                self.send_json(events_snapshot())
            elif path == "/api/jobs":
                offset = self._query_int(query, "offset", 0, 0, 1_000_000)
                limit = self._query_int(query, "limit", 20, 1, 100)
                self.send_json(job_snapshot(offset, limit))
            elif path.startswith("/api/jobs/") and path.endswith("/output/download"):
                job_id = self._job_id(path.removeprefix("/api/jobs/").removesuffix("/output/download"))
                stream = query.get("stream", ["stdout"])[0]
                if job_id is None or stream not in {"stdout", "stderr"}:
                    self.send(b"{}", status=404)
                    return
                try:
                    store.get(job_id)
                except ToolError as exc:
                    if exc.code == "job_not_found":
                        self.send(b"{}", status=404)
                        return
                    raise
                output_path = store.output_dir / job_id / f"{stream}.bin"
                self.send_download(output_path, f"{job_id}-{stream}.bin")
            elif path.startswith("/api/jobs/") and path.endswith("/output"):
                job_id = self._job_id(path.removeprefix("/api/jobs/").removesuffix("/output"))
                if job_id is None:
                    self.send(b"{}", status=404)
                    return
                stdout_since = self._query_int(query, "stdout_since", 0, 0, 2_147_483_647)
                stderr_since = self._query_int(query, "stderr_since", 0, 0, 2_147_483_647)
                max_bytes = self._query_int(query, "max_bytes", PANEL_JOB_OUTPUT_PREVIEW_BYTES, 16, 64 * 1024)
                try:
                    self.send_json(store.progress(
                        job_id,
                        stdout_since_byte=stdout_since,
                        stderr_since_byte=stderr_since,
                        max_bytes=max_bytes,
                    ))
                except ToolError as exc:
                    if exc.code == "job_not_found":
                        self.send(b"{}", status=404)
                        return
                    raise
                except ValueError:
                    self.send(b"{}", status=400)
            elif path.startswith("/api/jobs/"):
                job_id = self._job_id(path.removeprefix("/api/jobs/"))
                if job_id is None:
                    self.send(b"{}", status=404)
                    return
                try:
                    self.send_json(job_details(job_id))
                except ToolError as exc:
                    if exc.code == "job_not_found":
                        self.send(b"{}", status=404)
                        return
                    raise
            elif path == "/api/transport/history":
                limit = self._query_int(query, "limit", 100, 1, 500)
                self.send_json(transport_history_snapshot(limit))
            elif path == "/api/audit":
                limit = self._query_int(query, "limit", 50, 1, 200)
                items = tail_jsonl(supervisor.state_dir / "audit.jsonl", limit=limit)
                self.send_json({"items": items, "count": len(items)})
            elif path == "/api/workflows":
                self.send_json(workflow_snapshot())
            elif path.startswith("/api/workflows/"):
                workflow_id = path.removeprefix("/api/workflows/")
                if not workflow_id or len(workflow_id) > 128:
                    self.send(b"{}", status=404)
                    return
                try:
                    self.send_json(workflow_detail_snapshot(workflow_id))
                except ToolError as exc:
                    if exc.code == "workflow_not_found":
                        self.send(b"{}", status=404)
                        return
                    raise
            elif path == "/api/recovery":
                self.send_json(recovery_snapshot())
            elif path == "/api/config":
                self.send_json(config_snapshot())
            elif path == "/api/doctor":
                self.send_json(doctor_view())
            elif path == "/api/diagnostics":
                self.send_json(diagnostics_snapshot())
            elif path.startswith("/api/processes/"):
                raw_pid = path.removeprefix("/api/processes/")
                try:
                    pid = int(raw_pid)
                except ValueError:
                    self.send(b"{}", status=404)
                    return
                processes = supervisor.process_snapshot().get("active_processes", [])
                allowed = {int(item["pid"]) for item in processes if item.get("pid") is not None}
                detail = process_details(pid, allowed)
                if detail is None:
                    self.send(b"{}", status=404)
                    return
                self.send_json(detail)
            else:
                self.send(b"{}", status=404)

        def do_POST(self) -> None:
            origin = f"http://127.0.0.1:{cast(ThreadingHTTPServer, self.server).server_port}"
            if (
                not self.valid_host()
                or self.headers.get("Origin") != origin
                or not self.authorized()
            ):
                self.send(b"{}", status=403)
                return
            path = urlsplit(self.path).path
            if path == "/api/restart":
                supervisor.restart.set()
                self.send_json({"ok": True})
                return
            if path == "/api/stop":
                supervisor.stop.set()
                self.send_json({"ok": True})
                return
            if path.startswith("/api/jobs/") and path.endswith("/cancel"):
                job_id = self._job_id(path.removeprefix("/api/jobs/").removesuffix("/cancel"))
                if job_id is None:
                    self.send(b"{}", status=404)
                    return
                try:
                    result = store.cancel(job_id)
                except ToolError as exc:
                    if exc.code == "job_not_found":
                        self.send(b"{}", status=404)
                        return
                    raise
                self.send_json(result)
                return
            self.send(b"{}", status=404)

    return ThreadingHTTPServer(("127.0.0.1", port), Handler)


def _default_profile() -> str:
    return detect_profile()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["tunnel", "local-http"], default="tunnel")
    parser.add_argument("--profile", default=_default_profile())
    parser.add_argument("--panel-port", type=int, default=8766)
    args = parser.parse_args()
    if args.mode == "tunnel":
        try:
            tunnel_runtime = current_runtime(PROJECT_ROOT)
        except TunnelRuntimeError as exc:
            print(f"ERROR tunnel runtime: {exc}", file=sys.stderr)
            return 16
        command = [str(tunnel_runtime.path), "run", *profile_run_args(args.profile)]
        readiness_url = "http://127.0.0.1:8080/readyz"
        print(
            f"Tunnel runtime: v{tunnel_runtime.version} "
            f"({tunnel_runtime.source}, {tunnel_runtime.path})",
            flush=True,
        )
    else:
        command = [sys.executable, str(PROJECT_ROOT / "main.py"), "--transport", "streamable-http"]
        readiness_url = None
    supervisor = Supervisor(command, readiness_url=readiness_url)
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
