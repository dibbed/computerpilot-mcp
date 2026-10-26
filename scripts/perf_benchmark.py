"""Repeatable local performance benchmarks for the Windows Developer Agent MCP."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import platform
import random
import re
import shutil
import sqlite3
import stat
import statistics
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager, closing, contextmanager, nullcontext
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from functools import partial
from pathlib import Path
from typing import Annotated, Any, Literal, cast

import psutil
from pydantic import Field

from core.artifact_retention import ArtifactPolicy, cleanup_artifacts
from core.audit import AuditPolicy, AuditWriter
from core.backups import BackupPolicy, backup_created_at, cleanup_backups
from core.config import PROJECT_ROOT, SETTINGS
from core.executor import run_bounded
from core.extensions.models import LIMITS
from core.file_lock import exclusive_file_lock
from core.job_retention import JobHistoryPolicy, cleanup_job_history
from core.jobs import JobStore, same_process
from core.recovery import OperationRecoveryJournal
from core.registry import create_server
from core.resource_claims import RESOURCE_CAPABILITY_ENV
from core.state_paths import unlinked_directory_tree, unlinked_regular_file
from core.workflows import NATIVE_ONLY_ROUTING, StepDefinition, WorkflowDefinition, WorkflowExecutor, WorkflowState, WorkflowStore
from tools.desktop.uia import ElementLocator, UIAutomationService, WindowLocator
from tools.filesystem.patches import PatchLimits, apply_patch_transaction
from tools.filesystem.search_snapshots import SearchSnapshotStore, search_fingerprint
from tools.filesystem.service import search_by_name, search_by_name_streaming
from tools.git.service import run_git
from tools.language.edits import prepare_workspace_edit
from tools.language.lsp import LspClient, _frame, _parse_frames
from tools.language.providers import LspProvider, command_for
from tools.language.sessions import LspSessionManager
from tools.project.context import lookup_code_context
from tools.project.service import iter_files, parse_python
from tools.testing.diagnostics import merge_diagnostics
from tools.testing.impact import select_affected_tests
from tools.testing.verification import build_verification_plan
from tools.testing.watch import snapshot

MiB = 1_048_576
PROFILE_LIMITS: dict[str, dict[str, list[int]]] = {
    "quick": {
        "output_sizes": [1_024, MiB],
        "ast_files": [100],
        "search_files": [1_000],
        "job_counts": [1, 10],
        "browser_counts": [1],
        "audit_events": [2_000],
        "backup_files": [500],
        "artifact_files": [1_000],
        "job_history_rows": [500],
        "extension_counts": [1, 2],
        "lsp_requests": [1, 10],
        "lsp_workspaces": [1, 2],
    },
    "full": {
        "output_sizes": [1_024, MiB, 100 * MiB],
        "ast_files": [100, 1_000, 10_000],
        "search_files": [10_000, 100_000],
        "job_counts": [1, 10, 50],
        "browser_counts": [1, 5, 20],
        "audit_events": [20_000],
        "backup_files": [2_000],
        "artifact_files": [5_000],
        "job_history_rows": [2_000],
        "extension_counts": [1, 2, 4],
        "lsp_requests": [1, 10, 100],
        "lsp_workspaces": [1, 2],
    },
}
SUITES = (
    "exec_cost",
    "native_batch",
    "catalog",
    "startup",
    "output",
    "project",
    "search",
    "jobs",
    "browser",
    "audit",
    "backups",
    "retention",
    "resilience",
    "workflow",
    "extensions",
    "lsp",
)
FINAL_JOB_STATES = {"succeeded", "failed", "cancelled", "timed_out", "interrupted"}
STALE_TEMP_MAX_AGE_SEC = 24 * 60 * 60
_GC_STAGING = re.compile(r"^\.gc-[0-9a-f]{32}$")
_MANAGED_REPORT = re.compile(r"^benchmark-[0-9]{8}T[0-9]{12}Z-[0-9a-f]{32}\.json$")
_MANAGED_REPORT_TEMP = re.compile(r"^\.benchmark-[0-9]{8}T[0-9]{12}Z-[0-9a-f]{32}\.json\.[a-zA-Z0-9_-]+\.tmp$")
REPORT_MAX_AGE_SEC = 30 * 86_400
REPORT_RECENT_GRACE_SEC = 86_400
REPORT_TEMP_MAX_AGE_SEC = 86_400
REPORT_MAX_COUNT = 100
REPORT_MAX_BYTES = 64 * MiB


class BenchmarkSkip(RuntimeError):
    """Signal that a benchmark is unavailable in the current environment."""


@dataclass(slots=True)
class SampleResources:
    peak_rss_bytes: int
    read_bytes: int
    write_bytes: int
    peak_processes: int


class PeakRSSSampler:
    """Sample RSS and I/O for the benchmark process plus live descendants."""

    def __init__(self, interval_sec: float = 0.01) -> None:
        self._process = psutil.Process()
        self._interval_sec = interval_sec
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._peak = 0
        self._peak_processes = 0
        self._io_first: dict[tuple[int, float], tuple[int, int]] = {}
        self._io_last: dict[tuple[int, float], tuple[int, int]] = {}
        self._snapshot()

    def _tree(self) -> list[psutil.Process]:
        try:
            return [self._process, *self._process.children(recursive=True)]
        except psutil.Error:
            return [self._process]

    @staticmethod
    def _identity(process: psutil.Process) -> tuple[int, float] | None:
        try:
            return process.pid, process.create_time()
        except psutil.Error:
            return None

    def _snapshot(self) -> None:
        rss = 0
        count = 0
        for process in self._tree():
            identity = self._identity(process)
            if identity is None:
                continue
            try:
                rss += process.memory_info().rss
                count += 1
                counters = process.io_counters()
                current = (int(counters.read_bytes), int(counters.write_bytes))
                self._io_first.setdefault(identity, current)
                self._io_last[identity] = current
            except (AttributeError, psutil.Error):
                continue
        self._peak = max(self._peak, rss)
        self._peak_processes = max(self._peak_processes, count)

    def _sample(self) -> None:
        while not self._stop.wait(self._interval_sec):
            self._snapshot()

    def start(self) -> None:
        self._thread = threading.Thread(target=self._sample, name="perf-rss-sampler", daemon=True)
        self._thread.start()

    def stop(self) -> SampleResources:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)
        self._snapshot()
        read_bytes = 0
        write_bytes = 0
        for identity, latest in self._io_last.items():
            first = self._io_first.get(identity, latest)
            read_bytes += max(latest[0] - first[0], 0)
            write_bytes += max(latest[1] - first[1], 0)
        return SampleResources(
            peak_rss_bytes=self._peak,
            read_bytes=read_bytes,
            write_bytes=write_bytes,
            peak_processes=self._peak_processes,
        )


def _stats(samples: list[float]) -> dict[str, float]:
    if not samples:
        return {}
    return {
        "min_ms": round(min(samples), 3),
        "median_ms": round(statistics.median(samples), 3),
        "mean_ms": round(statistics.fmean(samples), 3),
        "max_ms": round(max(samples), 3),
        "stdev_ms": round(statistics.pstdev(samples), 3),
    }


def measure(name: str, fn: Callable[[], dict[str, Any] | None], runs: int) -> dict[str, Any]:
    """Measure one case repeatedly while keeping failures isolated to that case."""

    samples: list[float] = []
    peaks: list[int] = []
    reads: list[int] = []
    writes: list[int] = []
    process_counts: list[int] = []
    details: dict[str, Any] = {}
    for _ in range(runs):
        sampler = PeakRSSSampler()
        sampler.start()
        started = time.perf_counter()
        try:
            sample_details = fn() or {}
        except BenchmarkSkip as exc:
            sampler.stop()
            return {"name": name, "status": "skipped", "reason": str(exc)}
        except Exception as exc:
            sampler.stop()
            return {
                "name": name,
                "status": "error",
                "error": type(exc).__name__,
                "message": str(exc)[:500],
            }
        elapsed_ms = (time.perf_counter() - started) * 1_000
        resources = sampler.stop()
        samples.append(elapsed_ms)
        peaks.append(resources.peak_rss_bytes)
        reads.append(resources.read_bytes)
        writes.append(resources.write_bytes)
        process_counts.append(resources.peak_processes)
        details = sample_details
    return {
        "name": name,
        "status": "ok",
        "runs": runs,
        "samples_ms": [round(value, 3) for value in samples],
        "stats": _stats(samples),
        "peak_rss_mb": round(max(peaks, default=0) / MiB, 3),
        "rss_scope": "process_tree",
        "peak_processes": max(process_counts, default=0),
        "read_bytes": max(reads, default=0),
        "write_bytes": max(writes, default=0),
        "details": details,
    }


def _git_commit() -> str | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=PROJECT_ROOT,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=5,
        )
        return result.stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


def _catalog_once() -> dict[str, Any]:
    async def collect() -> list[Any]:
        return list(await create_server().list_tools())

    tools = asyncio.run(collect())
    catalog = [tool.model_dump(mode="json", by_alias=True, exclude_none=True) for tool in tools]
    started = time.perf_counter()
    payload = json.dumps(catalog, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    serialization_ms = (time.perf_counter() - started) * 1_000
    return {
        "tool_count": len(tools),
        "catalog_json_bytes": len(payload),
        "output_schema_json_bytes": sum(
            len(json.dumps(tool["outputSchema"], ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
            for tool in catalog if "outputSchema" in tool
        ),
        "serialization_ms": round(serialization_ms, 3),
    }


def _recovery_pagination_once(root: Path, count: int = 1_000) -> dict[str, Any]:
    path = root / "recovery.jsonl"
    path.unlink(missing_ok=True)
    journal = OperationRecoveryJournal()
    journal.configure(path, "runtime-a")
    handles = [journal.begin("run_process", f"cwd=C:/repo/{index}") for index in range(count)]
    journal.configure(path, "runtime-b")
    page_result = journal.list_operations(state="uncertain", offset=max(count - 50, 0), max_items=50)
    journal.compact()
    return {
        "operations": count,
        "page_count": page_result["count"],
        "total_count": page_result["total_count"],
        "journal_bytes": path.stat().st_size,
        "handles_created": sum(handle is not None for handle in handles),
    }


class _BenchmarkNode:
    def __init__(self, name: str, children: list[Any] | None = None) -> None:
        self.name = name
        self.children = children or []


class _BenchmarkUIABackend:
    def __init__(self, nodes: int) -> None:
        self.root = _BenchmarkNode("Benchmark", [_BenchmarkNode(f"Node {index}") for index in range(nodes)])

    def windows(self) -> list[Any]:
        return [self.root]

    def children(self, element: Any) -> list[Any]:
        return list(element.children)

    def properties(self, element: Any) -> dict[str, Any]:
        return {"name": element.name, "control_type": "WindowControl" if element is self.root else "TextControl", "pid": 1, "handle": 1}

    def runtime_id(self, element: Any) -> list[int]:
        return [42, 0 if element is self.root else self.root.children.index(element) + 1]

    def invoke(self, element: Any) -> None:
        del element

    def set_value(self, element: Any, value: str) -> None:
        del element, value

    def select(self, element: Any) -> None:
        del element


def _uia_traversal_once(nodes: int = 1_000) -> dict[str, Any]:
    service = UIAutomationService(_BenchmarkUIABackend(nodes))
    result = service.find_elements(
        WindowLocator(title="Benchmark"), ElementLocator(control_type="TextControl"), limit=50, max_nodes=nodes + 1,
    )
    return {"nodes": nodes, "matches": result["total_count"], "page_count": result["count"]}


def _workflow_checkpoint_once(root: Path, steps: int = 100) -> dict[str, Any]:
    database = root / "workflows.sqlite3"
    database.unlink(missing_ok=True)
    database.with_name(f"{database.name}-wal").unlink(missing_ok=True)
    database.with_name(f"{database.name}-shm").unlink(missing_ok=True)
    store = WorkflowStore(database)
    definition = WorkflowDefinition(
        "benchmark", tuple(StepDefinition(f"step-{index}", "check_file", {"path": "x"}) for index in range(steps)),
    )
    workflow = store.create(definition, initial_state=WorkflowState.QUEUED)
    running = store.transition(workflow["workflow_id"], workflow["version"], WorkflowState.RUNNING)
    for index in range(steps):
        store.checkpoint_step(workflow["workflow_id"], index, "running", increment_attempt=True)
        store.checkpoint_step(workflow["workflow_id"], index, "completed", evidence={"conclusive": True})
    store.transition(workflow["workflow_id"], running["version"], WorkflowState.COMPLETED, current_step=steps)
    recovered = WorkflowStore(database).get(workflow["workflow_id"])
    return {"steps": steps, "state": recovered["state"], "db_bytes": database.stat().st_size}


def _prepare_workflow_history_fixture(
    root: Path,
    *,
    rows: int = 10_000,
    queued: int = 100,
) -> tuple[WorkflowStore, str]:
    root.mkdir(parents=True, exist_ok=True)
    database = root / "workflows.sqlite3"
    for path in (database, Path(f"{database}-wal"), Path(f"{database}-shm")):
        path.unlink(missing_ok=True)
    store = WorkflowStore(database)
    now = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
    step_definition: dict[str, Any] = {
        "name": "check",
        "action": "check_file",
        "arguments": {"path": "unused"},
        "timeout_sec": 300,
        "max_retries": 0,
        "postcondition": None,
    }
    definition: dict[str, Any] = {
        "name": "history-benchmark",
        "steps": [step_definition],
        "description": "",
    }
    definition_json = json.dumps(definition, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    definition_hash = hashlib.sha256(definition_json.encode("utf-8")).hexdigest()
    _, operation_definition_hash, operation_arguments_fingerprint = WorkflowStore._operation_payload(
        step_definition
    )
    workflow_rows: list[tuple[Any, ...]] = []
    step_rows: list[tuple[Any, ...]] = []
    operation_rows: list[tuple[Any, ...]] = []
    event_rows: list[tuple[Any, ...]] = []
    for index in range(rows):
        workflow_id = f"{index + 1:032x}"
        is_queued = index < queued
        state = "queued" if is_queued else "completed"
        step_state = "created" if is_queued else "completed"
        operation_state = "created" if is_queued else "succeeded"
        current_step = 0 if is_queued else 1
        workflow_rows.append(
            (
                workflow_id,
                definition_json,
                "{}",
                state,
                current_step,
                1,
                now,
                now,
                definition_json,
                definition_hash,
                1,
            )
        )
        step_rows.append(
            (
                workflow_id,
                0,
                step_state,
                0 if is_queued else 1,
                None if is_queued else now,
                None if is_queued else now,
                None,
                None,
            )
        )
        operation_id = f"{index + rows + 1:032x}"
        operation_rows.append(
            (
                operation_id,
                workflow_id,
                0,
                "check_file",
                operation_definition_hash,
                f"benchmark-operation-{index}",
                operation_state,
                0 if is_queued else 1,
                operation_arguments_fingerprint,
                now,
                None if is_queued else now,
            )
        )
        event_rows.append(
            (
                workflow_id,
                0,
                operation_id,
                "benchmark_fixture",
                operation_state,
                "{}",
                now,
            )
        )
    with closing(sqlite3.connect(database, timeout=30)) as connection, connection:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.executemany(
            """
            INSERT INTO workflows(
                workflow_id, definition_json, inputs_json, state, current_step,
                version, created_at, updated_at, execution_definition_json,
                execution_definition_hash, execution_compatible
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            workflow_rows,
        )
        connection.executemany(
            """
            INSERT INTO workflow_steps(
                workflow_id, step_index, state, attempts, started_at,
                finished_at, evidence_json, error
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            step_rows,
        )
        connection.executemany(
            """
            INSERT INTO workflow_operations(
                operation_id, workflow_id, step_index, operation_index, action,
                definition_hash, idempotency_key, state, attempts,
                arguments_fingerprint, updated_at, finished_at
            ) VALUES (?, ?, ?, 0, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            operation_rows,
        )
        connection.executemany(
            """
            INSERT INTO workflow_events(
                workflow_id, step_index, operation_id, event_type, state,
                metadata_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            event_rows,
        )
        connection.commit()
    return store, f"{rows:032x}"


def _workflow_start_once(store: WorkflowStore) -> dict[str, Any]:
    definition = WorkflowDefinition(
        "benchmark-start",
        (StepDefinition("check", "check_file", {"path": "unused"}),),
    )
    created = store.create(definition, initial_state=WorkflowState.QUEUED)
    return {"workflow_id": created["workflow_id"], "state": created["state"]}


def _workflow_status_once(store: WorkflowStore, workflow_id: str) -> dict[str, Any]:
    result = store.get(workflow_id)
    return {"workflow_id": workflow_id, "state": result["state"], "step_count": len(result["steps"])}


def _workflow_store_reopen_once(database: Path) -> dict[str, Any]:
    store = WorkflowStore(database)
    health = store.health_summary()
    return {
        "workflow_total": health["workflow_total"],
        "operation_total": health["operation_total"],
        "event_total": health["event_total"],
        "workflow_db_bytes": health["workflow_db_bytes"],
    }


def _workflow_queue_once(store: WorkflowStore) -> dict[str, Any]:
    result = store.list_queued(limit=100)
    return {
        "count": result["count"],
        "total_count": result["total_count"],
        "has_more": result["has_more"],
    }


def _workflow_health_once(store: WorkflowStore) -> dict[str, Any]:
    result = store.health_summary()
    return {
        "workflow_total": result["workflow_total"],
        "operation_total": result["operation_total"],
        "event_total": result["event_total"],
        "workflow_db_bytes": result["workflow_db_bytes"],
    }


def _workflow_db_density_once(store: WorkflowStore, rows: int) -> dict[str, Any]:
    with closing(sqlite3.connect(store.path, timeout=30)) as connection, connection:
        connection.execute("PRAGMA wal_checkpoint(PASSIVE)")
    total_bytes = 0
    for path in (store.path, Path(f"{store.path}-wal"), Path(f"{store.path}-shm")):
        if path.is_file():
            total_bytes += path.stat().st_size
    return {
        "workflows": rows,
        "db_bytes": total_bytes,
        "bytes_per_workflow": round(total_bytes / max(rows, 1), 3),
    }


def _workflow_execute_read_once(store: WorkflowStore, target: Path) -> dict[str, Any]:
    target.write_text("ready\n", encoding="utf-8")
    workflow = store.create(
        WorkflowDefinition(
            "benchmark-execute",
            (StepDefinition("check", "check_file", {"path": str(target)}),),
        ),
        initial_state=WorkflowState.QUEUED,
    )
    result = WorkflowExecutor(store, route_planner=NATIVE_ONLY_ROUTING).execute(
        str(workflow["workflow_id"]),
        owner_id=f"benchmark-{workflow['workflow_id']}",
    )
    return {"state": result["state"]}


def _workflow_lease_renew_once(store: WorkflowStore) -> dict[str, Any]:
    workflow = store.create(
        WorkflowDefinition(
            "benchmark-lease",
            (StepDefinition("check", "check_file", {"path": "unused"}),),
        ),
        initial_state=WorkflowState.QUEUED,
    )
    workflow_id = str(workflow["workflow_id"])
    lease = store.acquire_lease(workflow_id, "benchmark-heartbeat", 30)
    renewed = store.renew_lease(workflow_id, lease.lease_token, 30)
    store.release_lease_if_current(workflow_id, lease.lease_token)
    interval_sec = min(max(30 / 3.0, 1.0), 10.0)
    return {
        "lease_version": renewed.version,
        "heartbeat_interval_sec": interval_sec,
        "configured_writes_per_minute": round(60 / interval_sec, 3),
    }


def _launcher_validation_once() -> dict[str, Any]:
    environment = os.environ.copy()
    environment["MCP_START_MODE"] = "local-http"
    environment["MCP_START_VALIDATE_ONLY"] = "1"
    environment["PYTHON_BIN"] = sys.executable
    command = (
        ["cmd.exe", "/d", "/s", "/c", "START_MCP.bat"]
        if os.name == "nt"
        else [str(PROJECT_ROOT / "start_mcp.sh")]
    )
    result = subprocess.run(
        command,
        cwd=PROJECT_ROOT,
        env=environment,
        capture_output=True,
        timeout=180,
    )
    if result.returncode != 0:
        message = (result.stderr or result.stdout).decode(errors="replace")[-1000:]
        raise RuntimeError(message)
    return {"exit_code": result.returncode}


def _server_cold_start_once() -> dict[str, Any]:
    code = "from core.registry import create_server; create_server()"
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=PROJECT_ROOT,
        capture_output=True,
        timeout=60,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.decode(errors="replace")[-500:])
    return {"exit_code": result.returncode}


def _server_warm_start_once() -> dict[str, Any]:
    server = create_server()
    tools = asyncio.run(server.list_tools())
    return {"tool_count": len(tools)}


def _cleanup_artifact(result: dict[str, Any]) -> None:
    stdout = result.get("stdout")
    stderr = result.get("stderr")
    for stream in (stdout, stderr):
        if isinstance(stream, dict) and stream.get("delivery") == "file" and isinstance(stream.get("path"), str):
            Path(stream["path"]).unlink(missing_ok=True)


def _output_once(size: int) -> dict[str, Any]:
    delivery: Literal["inline"] = "inline"
    result = run_bounded(
        [sys.executable, "-c", f"import sys;sys.stdout.buffer.write(b'x'*{size})"],
        cwd=PROJECT_ROOT,
        timeout_sec=120,
        stdout_limit=None,
        stderr_limit=0,
        output_mode="tail",
        encoding="utf-8",
        delivery=delivery,
    )
    try:
        if not result["ok"] or result["stdout"]["total_bytes"] != size:
            raise RuntimeError("Output benchmark did not capture the expected byte count.")
        started = time.perf_counter()
        encoded = json.dumps(result, ensure_ascii=False, default=str).encode("utf-8")
        serialization_ms = (time.perf_counter() - started) * 1_000
        return {
            "bytes": size,
            "delivery": result["stdout"]["delivery"],
            "response_json_bytes": len(encoded),
            "serialization_ms": round(serialization_ms, 3),
        }
    finally:
        _cleanup_artifact(result)


def _prepare_python_fixture(root: Path, count: int) -> list[Path]:
    root.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    for index in range(count):
        directory = root / f"d{index // 1_000:04d}"
        directory.mkdir(exist_ok=True)
        path = directory / f"module_{index:06d}.py"
        path.write_text(f"def value_{index}():\n    return {index}\n", encoding="utf-8")
        paths.append(path)
    return paths


def _parse_fixture(paths: list[Path], count: int) -> dict[str, Any]:
    for path in paths[:count]:
        parse_python(path)
    return {"files": count}


def _project_lookup_once(root: Path, count: int) -> dict[str, Any]:
    async def invoke() -> dict[str, Any]:
        result = await create_server().call_tool(
            "find_function",
            {
                "path": str(root),
                "function_name": "value_0",
                "match": "exact",
                "max_results": 20,
                "max_files": count,
            },
        )
        structured = getattr(result, "structured_content", None)
        if not isinstance(structured, dict):
            raise RuntimeError("find_function benchmark returned no structured content.")
        return structured

    structured = asyncio.run(invoke())
    if structured.get("total_count") != 1:
        raise RuntimeError(f"find_function correctness mismatch: {structured.get('total_count')!r}")
    return {
        "files": count,
        "total_count": structured["total_count"],
        "scan_truncated": bool(structured.get("scan_truncated")),
    }


def _context_lookup_once(root: Path, count: int) -> dict[str, Any]:
    result = lookup_code_context(root, "d0000.module_000000.value_0", max_files=count, include_source=False)
    if len(result["definitions"]) != 1:
        raise RuntimeError(f"code_context correctness mismatch: {len(result['definitions'])!r}")
    return {
        "files": count,
        "definitions": len(result["definitions"]),
        "relationships": result["total"],
        "scan_truncated": bool(result["scan_truncated"]),
    }


def _prepare_patch_fixture(root: Path, count: int) -> str:
    root.mkdir(parents=True, exist_ok=True)
    chunks: list[str] = []
    for index in range(count):
        name = f"file_{index:04d}.txt"
        (root / name).write_text("before\n", encoding="utf-8")
        chunks.append(f"diff --git a/{name} b/{name}\n--- a/{name}\n+++ b/{name}\n@@ -1 +1 @@\n-before\n+after\n")
    return "".join(chunks)


def _patch_dry_run_once(root: Path, patch: str, count: int) -> dict[str, Any]:
    result = apply_patch_transaction(
        root,
        patch,
        dry_run=True,
        backup=False,
        limits=PatchLimits(max_bytes=2_000_000, max_files=count, max_hunks=count),
    )
    if result["file_count"] != count:
        raise RuntimeError(f"apply_patch dry-run correctness mismatch: {result['file_count']!r}")
    return {"files": count, "hunks": result["hunk_count"], "dry_run": result["dry_run"]}


def _prepare_impact_fixture(root: Path, count: int) -> None:
    app = root / "app"
    tests = root / "tests"
    app.mkdir(parents=True, exist_ok=True)
    tests.mkdir(parents=True, exist_ok=True)
    (app / "__init__.py").write_text("", encoding="utf-8")
    for index in range(count):
        previous = f"from app.module_{index - 1:04d} import value\n" if index else "value = 0\n"
        (app / f"module_{index:04d}.py").write_text(previous, encoding="utf-8")
    for index in range(max(count // 5, 1)):
        target = min(index * 5, count - 1)
        (tests / f"test_module_{target:04d}.py").write_text(
            f"from app.module_{target:04d} import value\n\ndef test_value() -> None:\n    assert value >= 0\n",
            encoding="utf-8",
        )


def _impact_once(root: Path, count: int) -> dict[str, Any]:
    result = select_affected_tests(root, changed_paths=["app/module_0000.py"], max_files=count + count // 5 + 10, max_depth=20)
    decision = result["decision"]
    if decision not in {"focused", "focused_plus_full_recommended", "full_suite"}:
        raise RuntimeError(f"affected_tests correctness mismatch: {decision!r}")
    if decision == "full_suite" and not result.get("full_suite_reasons"):
        raise RuntimeError("Full-suite fallback lacks explicit coverage evidence.")
    return {
        "files": count, "tests": result["test_count"], "modules_traversed": result["modules_traversed"],
        "decision": decision, "unknown_coverage": result.get("unknown_coverage", False),
        "full_suite_reasons": result.get("full_suite_reasons", []),
    }


def _verification_plan_once(root: Path) -> dict[str, Any]:
    result = build_verification_plan(root, changed_paths=["app/module_0000.py"])
    if result["mode"] not in {"focused", "full"}:
        raise RuntimeError(f"verification plan correctness mismatch: {result['mode']!r}")
    return {"mode": result["mode"], "tests": result["affected"]["test_count"]}


def _lsp_protocol_once() -> dict[str, Any]:
    payload = {"jsonrpc": "2.0", "id": 1, "result": {"items": list(range(100))}}
    framed = _frame(payload)
    parsed = _parse_frames(framed, 1_000_000)
    if parsed != [payload]:
        raise RuntimeError("LSP framing correctness mismatch.")
    return {"bytes": len(framed), "messages": len(parsed)}


@dataclass(slots=True)
class _LspSample:
    peak_rss_bytes: int
    peak_handles: int
    spawns: int
    max_queue: int


class _LspSampler:
    """Sample only LSP fixture descendants, excluding the benchmark process itself."""

    def __init__(
        self,
        entrypoint: Path,
        *,
        queue_depth: Callable[[], int] | None = None,
        interval_sec: float = 0.01,
    ) -> None:
        self._entrypoint_real = os.path.realpath(entrypoint)
        self._entrypoint_name = entrypoint.name
        self._queue_depth = queue_depth
        self._interval_sec = interval_sec
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._peak_rss = 0
        self._peak_handles = 0
        self._pids: set[int] = set()
        self._max_queue = 0

    def _matches_entrypoint(self, argument: str) -> bool:
        """Match the fixture despite POSIX symlink rewrites such as /var -> /private/var."""

        if not argument:
            return False
        try:
            if os.path.realpath(argument) == self._entrypoint_real:
                return True
        except OSError:
            pass
        return os.path.basename(argument) == self._entrypoint_name

    @staticmethod
    def _descends_from(process: psutil.Process, parent_pid: int) -> bool:
        seen: set[int] = set()
        try:
            current = process.ppid()
            while current not in (0, parent_pid):
                if current in seen:
                    return False
                seen.add(current)
                current = psutil.Process(current).ppid()
            return current == parent_pid
        except psutil.Error:
            return False

    def _sample_once(self) -> None:
        rss = 0
        handles = 0
        pids: list[int] = []
        benchmark = psutil.Process()
        benchmark_pid = benchmark.pid
        try:
            children = benchmark.children(recursive=True)
        except psutil.Error:
            try:
                children = list(psutil.process_iter())
            except psutil.Error:
                children = []
        for child in children:
            try:
                if not any(self._matches_entrypoint(argument) for argument in child.cmdline()):
                    continue
                if not self._descends_from(child, benchmark_pid):
                    continue
                if child.ppid() == benchmark_pid:
                    pids.append(child.pid)
                rss += child.memory_info().rss
                num_handles = getattr(child, "num_handles", None)
                handles += int(num_handles()) if num_handles is not None else int(child.num_fds())
            except (psutil.Error, AttributeError, OSError):
                continue
        queue_depth = int(self._queue_depth()) if self._queue_depth is not None else 0
        with self._lock:
            self._peak_rss = max(self._peak_rss, rss)
            self._peak_handles = max(self._peak_handles, handles)
            self._pids.update(pids)
            self._max_queue = max(self._max_queue, queue_depth)

    def _sample(self) -> None:
        while not self._stop.wait(self._interval_sec):
            self._sample_once()

    def start(self) -> None:
        self._sample_once()
        self._thread = threading.Thread(target=self._sample, name="lsp-fixture-sampler", daemon=True)
        self._thread.start()

    def stop(self) -> _LspSample:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)
        self._sample_once()
        with self._lock:
            return _LspSample(self._peak_rss, self._peak_handles, len(self._pids), self._max_queue)


def _lsp_percentile(samples: list[float], fraction: float) -> float:
    if not samples:
        return 0.0
    ordered = sorted(samples)
    return round(ordered[max(int(len(ordered) * fraction) - 1, 0)], 3)


def _lsp_case_details(
    *,
    started: float,
    latencies_ms: list[float],
    spawns: int,
    restarts: int,
    max_queue: int,
    peak_rss: int,
    open_handles: int,
    errors: int,
) -> dict[str, object]:
    return {
        "elapsed": round(time.perf_counter() - started, 3),
        "p50": _lsp_percentile(latencies_ms, 0.50),
        "p95": _lsp_percentile(latencies_ms, 0.95),
        "p99": _lsp_percentile(latencies_ms, 0.99),
        "spawns": spawns,
        "restarts": restarts,
        "max_queue": max_queue,
        "peak_rss": round(peak_rss / MiB, 3),
        "open_handles": open_handles,
        "errors": errors,
    }


def _one_shot_request(
    provider: LspProvider,
    workspace: Path,
    index: int,
    sampler: _LspSampler,
) -> tuple[bool, float]:
    """Run one one-shot request while sampling its short-lived child from this thread."""
    started = time.perf_counter()
    outcome: list[bool] = []

    def run() -> None:
        try:
            LspClient(list(command_for(provider)), workspace, timeout_sec=30).request(
                "workspace/symbol", {"query": f"benchmark-{index}"},
            )
            outcome.append(True)
        except Exception:
            outcome.append(False)

    worker = threading.Thread(target=run, name="lsp-one-shot-request", daemon=True)
    worker.start()
    while worker.is_alive():
        sampler._sample_once()
        worker.join(timeout=0.002)
    worker.join()
    return (outcome[0] if outcome else False), (time.perf_counter() - started) * 1_000


def _lsp_session_case(requests: int, workspaces: int, persistent: bool) -> dict[str, object]:
    """Measure the reviewed local fixture only; this is not real-provider coverage.

    Persistent cases warm one session per workspace before timing; one-shot cases are
    the cold baseline. Per-session requests stay serial, so latency excludes queueing.
    """

    if type(requests) is not int or not 1 <= requests <= 1_000:
        raise ValueError("requests must be an integer between 1 and 1000")
    if type(workspaces) is not int or not 1 <= workspaces <= 2:
        raise ValueError("workspaces must be 1 or 2")
    fixture = PROJECT_ROOT / "tests" / "fixtures" / "persistent_lsp_server.py"
    if not fixture.is_file():
        raise BenchmarkSkip("The persistent LSP fixture is unavailable.")
    executable = Path(sys.executable).resolve()
    started = time.perf_counter()
    with _temporary_root("lsp-session") as temporary:
        root = Path(temporary)
        entrypoint = root / "lsp_benchmark_server.py"
        shutil.copyfile(fixture, entrypoint)
        provider = LspProvider(
            provider_id="pyright",
            languages=("python",),
            executable=executable,
            executable_sha256=_hash_file(executable),
            entrypoint=entrypoint,
            entrypoint_sha256=_hash_file(entrypoint),
            declared_version="fixture",
            trusted_roots=(root,),
        )
        workspace_roots = [root / f"workspace_{index}" for index in range(workspaces)]
        for workspace in workspace_roots:
            workspace.mkdir()
        latencies_ms: list[float] = []
        errors = 0
        peak_rss = 0
        open_handles = 0
        max_queue = 0

        if persistent:
            manager = LspSessionManager((provider,), generation=f"benchmark-{uuid.uuid4().hex}")
            sampler = _LspSampler(entrypoint, queue_depth=lambda: manager.snapshot()["queue_depth"])
            sampler.start()

            async def run_requests() -> None:
                nonlocal errors
                for workspace in workspace_roots:
                    try:
                        await manager.arequest(
                            workspace, "python", "workspace/symbol",
                            {"query": "warmup"}, timeout_sec=30,
                        )
                    except Exception:
                        errors += 1

                async def run_workspace(workspace: Path, indices: list[int]) -> None:
                    nonlocal errors
                    for index in indices:
                        request_started = time.perf_counter()
                        try:
                            await manager.arequest(
                                workspace, "python", "workspace/symbol",
                                {"query": f"benchmark-{index}"}, timeout_sec=30,
                            )
                        except Exception:
                            errors += 1
                        latencies_ms.append((time.perf_counter() - request_started) * 1_000)

                assignments = [
                    [index for index in range(requests) if index % workspaces == workspace_index]
                    for workspace_index in range(workspaces)
                ]
                await asyncio.gather(*(
                    run_workspace(workspace, indices)
                    for workspace, indices in zip(workspace_roots, assignments, strict=True)
                ))

            live_rss = 0
            try:
                try:
                    asyncio.run(run_requests())
                    live_rss = manager.snapshot()["rss_bytes"]
                finally:
                    manager.close_all(timeout_sec=5)
            finally:
                sample = sampler.stop()
            snapshot = manager.snapshot()
            if snapshot["active"] or snapshot["quarantined"]:
                raise RuntimeError("Persistent LSP sessions were not fully cleaned up.")
            spawns = snapshot["spawn_count"]
            restarts = snapshot["restart_count"]
            max_queue = sample.max_queue
            peak_rss = max(sample.peak_rss_bytes, live_rss)
            open_handles = sample.peak_handles
        else:
            spawns = 0
            for index in range(requests):
                workspace = workspace_roots[index % workspaces]
                sampler = _LspSampler(entrypoint)
                sampler.start()
                try:
                    succeeded, elapsed_ms = _one_shot_request(provider, workspace, index, sampler)
                finally:
                    sample = sampler.stop()
                latencies_ms.append(elapsed_ms)
                if not succeeded:
                    errors += 1
                peak_rss = max(peak_rss, sample.peak_rss_bytes)
                open_handles = max(open_handles, sample.peak_handles)
                if succeeded:
                    # A completed one-shot request always launched the server; this
                    # minimum covers a child that exited between sampler ticks.
                    spawns += max(sample.spawns, 1)
            restarts = 0

        if errors:
            raise RuntimeError(f"{errors} LSP request(s) failed; benchmark case must fail closed.")

        return _lsp_case_details(
            started=started,
            latencies_ms=latencies_ms,
            spawns=spawns,
            restarts=restarts,
            max_queue=max_queue,
            peak_rss=peak_rss,
            open_handles=open_handles,
            errors=errors,
        )


def _workspace_edit_plan_once(root: Path) -> dict[str, Any]:
    path = root / "semantic.py"
    path.write_text("old\n", encoding="utf-8")
    edit = {
        "changes": {
            path.as_uri(): [{"range": {"start": {"line": 0, "character": 0}, "end": {"line": 0, "character": 3}}, "newText": "new"}]
        }
    }
    plan = prepare_workspace_edit(root, edit)
    return {"files": len(plan), "output_bytes": len(plan[0].after)}


def _git_read_once(root: Path) -> dict[str, Any]:
    result = run_git(root, ["rev-parse", "--verify", "HEAD"])
    return {"hash_chars": len(result["stdout"]["text"].strip())}


def _diagnostic_merge_once() -> dict[str, Any]:
    groups = [
        [
            {"file": f"module_{index}.py", "line": index + 1, "severity": "error", "message": "failure", "source": "ruff"}
            for index in range(1_000)
        ]
    ]
    diagnostics, truncated = merge_diagnostics(groups, cap=1_000)
    return {"diagnostics": len(diagnostics), "truncated": truncated}


def _watch_snapshot_once(root: Path) -> dict[str, Any]:
    files, truncated = snapshot(root, max_files=100_000)
    return {"files": len(files), "truncated": truncated}


def _prepare_search_fixture(root: Path, count: int) -> None:
    root.mkdir(parents=True, exist_ok=True)
    for index in range(count):
        directory = root / f"d{index // 1_000:04d}"
        directory.mkdir(exist_ok=True)
        (directory / f"fastneedle_{index:06d}.txt").touch()


def _search_fixture(root: Path, count: int) -> dict[str, Any]:
    matches, truncated = search_by_name(
        root,
        "needle_000000",
        regex=False,
        case_sensitive=False,
        glob=None,
        include_hidden=False,
        max_scan_files=count,
        exclude_common=True,
    )
    expected_matches = 1
    if len(matches) != expected_matches:
        raise RuntimeError(f"Search correctness mismatch: expected {expected_matches}, got {len(matches)}.")
    return {"files": count, "matches": len(matches), "expected_matches": expected_matches, "scan_truncated": truncated}


def _search_streaming_fixture(root: Path, count: int, page_size: int = 50) -> dict[str, Any]:
    result = search_by_name_streaming(
        root,
        "fastneedle_",
        regex=False,
        case_sensitive=False,
        glob=None,
        include_hidden=False,
        max_scan_files=count,
        exclude_common=True,
        offset=0,
        limit=page_size,
    )
    if len(result.items) != page_size or not result.has_more:
        raise RuntimeError(
            f"Streaming search correctness mismatch: expected {page_size} items plus continuation, "
            f"got {len(result.items)} items and has_more={result.has_more}."
        )
    return {
        "files": count,
        "page_size": page_size,
        "matches_returned": len(result.items),
        "scanned_files": result.scanned_files,
        "scan_truncated": result.scan_truncated,
        "has_more": result.has_more,
    }


def _search_snapshot_fingerprint(root: Path, count: int) -> str:
    return search_fingerprint(
        root,
        {
            "query": "fastneedle_",
            "search_type": "name",
            "regex": False,
            "case_sensitive": False,
            "glob": None,
            "include_hidden": False,
            "exclude_common": True,
            "count_mode": "none",
            "max_scan_files": count,
            "timeout_sec": 30.0,
        },
    )


def _create_search_snapshot(
    root: Path,
    count: int,
    *,
    page_size: int = 50,
    namespace: str = "build",
) -> tuple[SearchSnapshotStore, str, str, int]:
    matches, truncated = search_by_name(
        root,
        "fastneedle_",
        regex=False,
        case_sensitive=False,
        glob=None,
        include_hidden=False,
        max_scan_files=count,
        exclude_common=True,
    )
    fingerprint = _search_snapshot_fingerprint(root, count)
    store = SearchSnapshotStore(
        root / ".agent_state" / f"benchmark_snapshots_{namespace}",
        ttl_sec=3_600,
        max_bytes=SETTINGS.search_snapshot_max_bytes,
        max_count=max(SETTINGS.search_snapshot_max_count, 16),
    )
    items = [{"path": str(path), "matched_in": ["name"]} for path in matches]
    handle = store.create(
        items,
        fingerprint=fingerprint,
        count_mode="none",
        result_order="traversal",
        scan_truncated=truncated,
        total_count=None,
    )
    first = store.first_page(handle, fingerprint=fingerprint, limit=page_size)
    if len(first.items) != page_size or first.cursor is None:
        raise RuntimeError("Snapshot benchmark did not produce a continuable first page.")
    return store, first.cursor, fingerprint, handle.bytes


def _search_snapshot_build_fixture(root: Path, count: int, page_size: int = 50) -> dict[str, Any]:
    store, cursor, fingerprint, snapshot_bytes = _create_search_snapshot(root, count, page_size=page_size, namespace="build")
    page = store.read_page(cursor, fingerprint=fingerprint, limit=page_size)
    return {
        "files": count,
        "page_size": page_size,
        "snapshot_bytes": snapshot_bytes,
        "page_two_items": len(page.items),
        "scanned_files_page_two": 0,
    }


def _search_snapshot_page_fixture(
    store: SearchSnapshotStore,
    cursor: str,
    fingerprint: str,
    page_size: int = 50,
) -> dict[str, Any]:
    page = store.read_page(cursor, fingerprint=fingerprint, limit=page_size)
    if len(page.items) != page_size:
        raise RuntimeError(f"Snapshot continuation expected {page_size} items, got {len(page.items)}.")
    return {
        "page_size": page_size,
        "matches_returned": len(page.items),
        "scanned_files": 0,
        "has_more": page.has_more,
    }


def _job_batch_once(store: JobStore, count: int, run_key: int) -> dict[str, Any]:
    job_ids: list[str] = []
    for index in range(count):
        submitted = store.submit(
            [sys.executable, "-c", "import time; time.sleep(0.1)"],
            PROJECT_ROOT,
            30.0,
            f"benchmark-{run_key}-{count}-{index}",
        )
        job_ids.append(str(submitted["job_id"]))
    deadline = time.monotonic() + 60
    states: list[str] = []
    peak_active = 0
    peak_queued = 0
    while time.monotonic() < deadline:
        states = [str(store.get(job_id)["status"]) for job_id in job_ids]
        active = sum(state in {"running", "orphaned"} for state in states)
        queued = sum(state == "queued" for state in states)
        peak_active = max(peak_active, active)
        peak_queued = max(peak_queued, queued)
        if all(state in FINAL_JOB_STATES for state in states):
            break
        time.sleep(0.02)
    else:
        raise RuntimeError(f"Timed out waiting for {count} benchmark jobs.")
    if any(state != "succeeded" for state in states):
        raise RuntimeError(f"Benchmark jobs ended in unexpected states: {states}")
    worker_deadline = time.monotonic() + 10
    while time.monotonic() < worker_deadline:
        workers = [store.raw(job_id) for job_id in job_ids]
        if all(not same_process(row["worker_pid"], row["worker_created"]) for row in workers):
            break
        time.sleep(0.02)
    else:
        raise RuntimeError(f"Timed out waiting for {count} benchmark workers to exit.")
    return {
        "jobs": count,
        "states": states,
        "max_running_jobs": SETTINGS.max_running_jobs,
        "peak_active_jobs": peak_active,
        "peak_queued_jobs": peak_queued,
    }


def _job_wait_seed(store: JobStore) -> str:
    """Create one synthetic live row so wait latency is measured without command startup noise."""
    job_id = f"{time.time_ns() & ((1 << 128) - 1):032x}"
    now = time.time()
    process = psutil.Process()
    spec = json.dumps({"encoding": "utf-8"}, sort_keys=True)
    db = store.connect()
    try:
        with db:
            db.execute(
                "INSERT INTO jobs "
                "(id,request_key,fingerprint,spec,status,created,updated,version,worker_pid,worker_created) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    job_id,
                    f"wait-benchmark-{job_id}",
                    f"wait-fingerprint-{job_id}",
                    spec,
                    "running",
                    now,
                    now,
                    1,
                    process.pid,
                    process.create_time(),
                ),
            )
    finally:
        db.close()
    return job_id


def _job_wait_change_once(store: JobStore, waiters: int = 1) -> dict[str, Any]:
    job_id = _job_wait_seed(store)
    barrier = threading.Barrier(waiters + 1)
    original_connect = store.connect
    counter_lock = threading.Lock()
    wait_selects = 0

    def traced_connect() -> sqlite3.Connection:
        connection = original_connect()

        def trace(statement: str) -> None:
            nonlocal wait_selects
            if (
                threading.current_thread() is not threading.main_thread()
                and statement.lstrip().upper().startswith("SELECT")
                and " FROM JOBS WHERE ID=" in statement.upper()
            ):
                with counter_lock:
                    wait_selects += 1

        connection.set_trace_callback(trace)
        return connection

    store.connect = traced_connect  # type: ignore[method-assign]

    def wait_one() -> dict[str, Any]:
        barrier.wait(timeout=5)
        return store.wait(job_id, after_version=1, timeout=5)

    started = time.perf_counter()
    try:
        with ThreadPoolExecutor(max_workers=waiters) as pool:
            futures = [pool.submit(wait_one) for _ in range(waiters)]
            barrier.wait(timeout=5)
            time.sleep(0.1)
            changed_at = time.perf_counter()
            store.update(job_id, status="succeeded", exit_code=0)
            results = [future.result(timeout=5) for future in futures]
            wake_latency_ms = (time.perf_counter() - changed_at) * 1_000
    finally:
        store.connect = original_connect  # type: ignore[method-assign]
    wait_window_sec = max(time.perf_counter() - started, 0.001)
    if not all(result["changed"] and not result["timed_out"] for result in results):
        raise RuntimeError("job_wait failed to observe the authoritative version change.")
    return {
        "waiters": waiters,
        "update_delay_ms": 100.0,
        "wake_latency_ms": round(wake_latency_ms, 3),
        "observed_version": int(results[0]["version"]),
        "wait_selects": wait_selects,
        "wait_window_ms": round(wait_window_sec * 1_000, 3),
        "db_queries_per_minute": round(wait_selects / wait_window_sec * 60, 3),
    }


def _job_wait_timeout_once(store: JobStore, timeout: float = 0.2) -> dict[str, Any]:
    job_id = _job_wait_seed(store)
    started = time.perf_counter()
    result = store.wait(job_id, after_version=1, timeout=timeout)
    elapsed_ms = (time.perf_counter() - started) * 1_000
    store.update(job_id, status="succeeded", exit_code=0)
    if result["changed"] or not result["timed_out"]:
        raise RuntimeError("job_wait timeout benchmark unexpectedly observed a version change.")
    return {
        "timeout_ms": round(timeout * 1_000, 3),
        "elapsed_ms": round(elapsed_ms, 3),
        "observed_version": int(result["version"]),
    }


def _browser_pool_details(manager: Any, sessions: int) -> dict[str, Any]:
    pools = list(getattr(manager, "_pools", {}).values())
    return {
        "sessions": sessions,
        "browser_instances": len(pools),
        "contexts": sum(int(pool.active_contexts) for pool in pools),
        "pool_keys": [
            {
                "browser": pool.key.browser_name,
                "headless": pool.key.headless,
                "active_contexts": int(pool.active_contexts),
            }
            for pool in pools
        ],
    }


def _browser_batch_once(count: int) -> dict[str, Any]:
    try:
        from tools.browser.manager import BrowserManager
    except ImportError as exc:
        raise BenchmarkSkip("Playwright support is not installed.") from exc

    async def scenario() -> dict[str, Any]:
        manager = BrowserManager()
        session_ids = [f"bench-{index}" for index in range(count)]
        details: dict[str, Any] = {}
        try:
            opened_at = time.perf_counter()
            await asyncio.gather(
                *(
                    manager.open(
                        session_id,
                        "data:text/html,<title>benchmark</title>",
                        browser_name="chromium",
                        headless=True,
                        timeout_ms=30_000,
                        wait_until="load",
                    )
                    for session_id in session_ids
                )
            )
            open_latency_ms = (time.perf_counter() - opened_at) * 1_000
            details = _browser_pool_details(manager, count)
            details["open_latency_ms"] = round(open_latency_ms, 3)

            navigated_at = time.perf_counter()
            await asyncio.gather(
                *(
                    manager.open(
                        session_id,
                        "data:text/html,<title>benchmark-nav</title>",
                        browser_name="chromium",
                        headless=True,
                        timeout_ms=30_000,
                        wait_until="load",
                    )
                    for session_id in session_ids
                )
            )
            details["parallel_navigation_ms"] = round((time.perf_counter() - navigated_at) * 1_000, 3)
        except Exception as exc:
            message = str(exc)
            if (
                getattr(exc, "code", None) == "browser_runtime_missing"
                or "Executable doesn't exist" in message
                or "playwright install" in message
            ):
                raise BenchmarkSkip("Chromium Playwright runtime is not installed.") from exc
            raise
        finally:
            cleanup_at = time.perf_counter()
            await asyncio.gather(*(manager.close(session_id) for session_id in session_ids), return_exceptions=True)
            if details:
                details["cleanup_latency_ms"] = round((time.perf_counter() - cleanup_at) * 1_000, 3)
            runtime = getattr(manager, "_playwright", None)
            if runtime is not None:
                await runtime.stop()
        return details

    return asyncio.run(scenario())


def _browser_mixed_once(count_per_engine: int = 5) -> dict[str, Any]:
    try:
        from tools.browser.manager import BrowserManager
    except ImportError as exc:
        raise BenchmarkSkip("Playwright support is not installed.") from exc

    async def scenario() -> dict[str, Any]:
        manager = BrowserManager()
        specs: list[tuple[str, Literal["chromium", "firefox", "webkit"]]] = [
            *[(f"chromium-{index}", "chromium") for index in range(count_per_engine)],
            *[(f"firefox-{index}", "firefox") for index in range(count_per_engine)],
        ]
        details: dict[str, Any] = {}
        try:
            await asyncio.gather(
                *(
                    manager.open(
                        session_id,
                        "data:text/html,<title>benchmark</title>",
                        browser_name=browser_name,
                        headless=True,
                        timeout_ms=30_000,
                        wait_until="load",
                    )
                    for session_id, browser_name in specs
                )
            )
            details = _browser_pool_details(manager, len(specs))
        except Exception as exc:
            message = str(exc)
            if (
                getattr(exc, "code", None) == "browser_runtime_missing"
                or "Executable doesn't exist" in message
                or "playwright install" in message
            ):
                raise BenchmarkSkip("Chromium/Firefox Playwright runtimes are not both installed.") from exc
            raise
        finally:
            await asyncio.gather(*(manager.close(session_id) for session_id, _ in specs), return_exceptions=True)
            runtime = getattr(manager, "_playwright", None)
            if runtime is not None:
                await runtime.stop()
        return details

    return asyncio.run(scenario())


def _browser_idle_eviction_once() -> dict[str, Any]:
    try:
        from tools.browser.manager import BrowserManager
    except ImportError as exc:
        raise BenchmarkSkip("Playwright support is not installed.") from exc

    async def scenario() -> dict[str, Any]:
        session_idle_sec = 0.05
        pool_idle_sec = 0.05
        manager = BrowserManager(session_idle_sec=session_idle_sec, pool_idle_sec=pool_idle_sec)
        try:
            await manager.open(
                "idle-bench",
                "data:text/html,<title>idle-benchmark</title>",
                browser_name="chromium",
                headless=True,
                timeout_ms=30_000,
                wait_until="load",
            )
        except Exception as exc:
            message = str(exc)
            if (
                getattr(exc, "code", None) == "browser_runtime_missing"
                or "Executable doesn't exist" in message
                or "playwright install" in message
            ):
                raise BenchmarkSkip("Chromium Playwright runtime is not installed.") from exc
            raise

        started = time.perf_counter()
        session_evicted_at: float | None = None
        deadline = started + 5.0
        try:
            while time.perf_counter() < deadline:
                now = time.perf_counter()
                if session_evicted_at is None and not manager._sessions:
                    session_evicted_at = now
                if session_evicted_at is not None and not manager._pools:
                    finished = now
                    return {
                        "session_idle_sec": session_idle_sec,
                        "pool_idle_sec": pool_idle_sec,
                        "session_eviction_ms": round((session_evicted_at - started) * 1_000, 3),
                        "pool_eviction_ms": round((finished - session_evicted_at) * 1_000, 3),
                        "total_idle_reclamation_ms": round((finished - started) * 1_000, 3),
                        "remaining_sessions": 0,
                        "remaining_browser_instances": 0,
                    }
                await asyncio.sleep(0.005)
            raise RuntimeError("Timed out waiting for browser idle eviction.")
        finally:
            await manager.close("idle-bench")
            for pool in list(manager._pools.values()):
                try:
                    await pool.browser.close()
                except Exception:
                    pass
            runtime = getattr(manager, "_playwright", None)
            if runtime is not None:
                await runtime.stop()

    return asyncio.run(scenario())


def _audit_payload() -> bytes:
    return (
        json.dumps(
            {
                "time": "2026-09-15T00:00:00.000+00:00",
                "operation": "write_file",
                "outcome": "succeeded",
                "pid": 1234,
                "target": "C:/work/example.txt",
                "details": {"changed": True},
            },
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")


def _percentile_95(samples: list[float]) -> float:
    ordered = sorted(samples)
    if not ordered:
        return 0.0
    return ordered[max(int(len(ordered) * 0.95) - 1, 0)]


def _reset_audit_benchmark_files(path: Path) -> None:
    path.unlink(missing_ok=True)
    for candidate in path.parent.glob(f"{path.stem}.*{path.suffix}"):
        candidate.unlink(missing_ok=True)


def _line_count(path: Path) -> int:
    with path.open("rb") as handle:
        return sum(1 for _ in handle)


def _audit_legacy_sync_once(path: Path, events: int, threads: int) -> dict[str, Any]:
    _reset_audit_benchmark_files(path)
    payload = _audit_payload()
    lock = threading.Lock()
    latencies: list[float] = []

    def worker(count: int) -> None:
        local: list[float] = []
        for _ in range(count):
            started = time.perf_counter_ns()
            with lock, path.open("ab", buffering=0) as handle:
                handle.write(payload)
            local.append((time.perf_counter_ns() - started) / 1_000_000)
        with lock:
            latencies.extend(local)

    counts = [events // threads + (1 if index < events % threads else 0) for index in range(threads)]
    workers = [threading.Thread(target=worker, args=(count,)) for count in counts]
    for worker_thread in workers:
        worker_thread.start()
    for worker_thread in workers:
        worker_thread.join()
    return {
        "events": events,
        "threads": threads,
        "lines": _line_count(path),
        "bytes": path.stat().st_size,
        "caller_median_ms": round(statistics.median(latencies), 6),
        "caller_p95_ms": round(_percentile_95(latencies), 6),
    }


def _audit_batched_once(path: Path, events: int, threads: int) -> dict[str, Any]:
    _reset_audit_benchmark_files(path)
    payload = _audit_payload()
    policy = AuditPolicy(
        batch_size=SETTINGS.audit_batch_size,
        flush_interval_sec=SETTINGS.audit_flush_ms / 1_000,
        queue_max=SETTINGS.audit_queue_max,
        max_file_bytes=max(SETTINGS.audit_max_file_bytes, events * len(payload) * 2),
        keep_files=SETTINGS.audit_keep_files,
    )
    writer = AuditWriter(path, policy)
    latencies: list[float] = []
    latency_lock = threading.Lock()

    def worker(count: int) -> None:
        local: list[float] = []
        for _ in range(count):
            started = time.perf_counter_ns()
            writer.submit(payload)
            local.append((time.perf_counter_ns() - started) / 1_000_000)
        with latency_lock:
            latencies.extend(local)

    counts = [events // threads + (1 if index < events % threads else 0) for index in range(threads)]
    workers = [threading.Thread(target=worker, args=(count,)) for count in counts]
    for worker_thread in workers:
        worker_thread.start()
    for worker_thread in workers:
        worker_thread.join()
    writer.close()
    return {
        "events": events,
        "threads": threads,
        "lines": _line_count(path),
        "bytes": path.stat().st_size,
        "caller_median_ms": round(statistics.median(latencies), 6),
        "caller_p95_ms": round(_percentile_95(latencies), 6),
        "batch_size": policy.batch_size,
        "flush_interval_ms": round(policy.flush_interval_sec * 1_000, 3),
        "queue_max": policy.queue_max,
    }


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(MiB), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _benchmark_launcher_fingerprint(executable: str | None) -> tuple[str | None, str]:
    """Only benchmark metadata may tolerate an unreadable OS command shim."""
    if executable is None:
        return None, "unavailable"
    try:
        return _hash_file(Path(executable)), "verified"
    except OSError:
        # Windows Store App Execution Aliases can launch but cannot be opened.
        # Do not weaken _hash_file for security-sensitive callers.
        return None, "unreadable_launcher"


def _backup_inventory_once(directory: Path) -> dict[str, Any]:
    counts: dict[str, int] = {}
    sizes: dict[str, int] = {}
    ages: list[float] = []
    total_bytes = 0
    files = 0
    now = time.time()
    if directory.is_dir():
        for path in directory.glob("*.bak"):
            try:
                if not path.is_file():
                    continue
                stat = path.stat()
                digest = _hash_file(path)
            except OSError:
                continue
            files += 1
            total_bytes += stat.st_size
            ages.append(max(0.0, (now - backup_created_at(path, stat.st_mtime)) / 86_400))
            counts[digest] = counts.get(digest, 0) + 1
            sizes.setdefault(digest, stat.st_size)
    unique_bytes = sum(sizes.values())
    savings = max(total_bytes - unique_bytes, 0)
    ratio = savings / total_bytes if total_bytes else 0.0
    return {
        "files": files,
        "bytes": total_bytes,
        "unique_hashes": len(counts),
        "duplicate_files": max(files - len(counts), 0),
        "dedup_savings_bytes": savings,
        "dedup_savings_ratio": round(ratio, 6),
        "dedup_gate_min_savings_bytes": MiB,
        "dedup_gate_min_ratio": 0.10,
        "dedup_gate_pass": savings >= MiB and ratio >= 0.10,
        "age_days_median": round(statistics.median(ages), 3) if ages else 0.0,
        "age_days_max": round(max(ages), 3) if ages else 0.0,
    }


def _backup_retention_once(directory: Path, files: int) -> dict[str, Any]:
    shutil.rmtree(directory, ignore_errors=True)
    directory.mkdir(parents=True, exist_ok=True)
    now = time.time()
    size = 2_048
    old_cutoff = files // 2
    for index in range(files):
        payload = index.to_bytes(4, "little") * (size // 4)
        path = directory / f"{index:06d}.bak"
        path.write_bytes(payload)
        mtime = now - 40 * 86_400 if index < old_cutoff else now - (files - index)
        os.utime(path, (mtime, mtime))
    policy = BackupPolicy(
        max_bytes=max(size, files * size // 4),
        max_age_sec=30 * 86_400,
        cleanup_interval_sec=0,
        recent_grace_sec=0,
    )
    result = cleanup_backups(directory, policy, now=now)
    return {"fixture_files": files, "file_size": size, **result.to_dict()}


def _artifact_inventory_once(directory: Path) -> dict[str, Any]:
    files: list[tuple[int, float]] = []
    now = time.time()
    if directory.is_dir():
        for path in directory.glob("*.bin"):
            try:
                if path.is_file():
                    stat = path.stat()
                    files.append((stat.st_size, max(0.0, (now - stat.st_mtime) / 86_400)))
            except OSError:
                continue
    return {
        "files": len(files),
        "bytes": sum(size for size, _ in files),
        "age_days_median": round(statistics.median(age for _, age in files), 3) if files else 0.0,
        "age_days_max": round(max(age for _, age in files), 3) if files else 0.0,
        "size_max": max((size for size, _ in files), default=0),
    }


def _artifact_retention_once(directory: Path, files: int) -> dict[str, Any]:
    shutil.rmtree(directory, ignore_errors=True)
    directory.mkdir(parents=True, exist_ok=True)
    now = time.time()
    size = 1_024
    old_cutoff = files // 2
    for index in range(files):
        path = directory / f"{index:08d}.bin"
        path.write_bytes(index.to_bytes(4, "little") * (size // 4))
        mtime = now - 10 * 86_400 if index < old_cutoff else now - (files - index)
        os.utime(path, (mtime, mtime))
    policy = ArtifactPolicy(
        max_bytes=max(size, files * size // 8),
        max_age_sec=7 * 86_400,
        max_count=max(1, files // 4),
        cleanup_interval_sec=0,
        recent_grace_sec=0,
    )
    started = time.perf_counter()
    result = cleanup_artifacts(directory, policy, now=now)
    cleanup_ms = (time.perf_counter() - started) * 1_000
    return {"fixture_files": files, "file_size": size, "cleanup_ms": round(cleanup_ms, 3), **result.to_dict()}


def _job_history_inventory_once(db_path: Path) -> dict[str, Any]:
    if not db_path.is_file():
        return {"rows": 0, "terminal_rows": 0, "active_rows": 0, "output_bytes": 0, "db_bytes": 0}
    store = JobStore(db_path)
    with closing(store.connect()) as db:
        rows = [dict(row) for row in db.execute("SELECT id,status FROM jobs")]
    output_bytes = 0
    for row in rows:
        directory = store.output_dir / str(row["id"])
        if directory.is_dir():
            output_bytes += sum(path.stat().st_size for path in directory.iterdir() if path.is_file())
    terminal = sum(1 for row in rows if row["status"] in FINAL_JOB_STATES)
    return {
        "rows": len(rows),
        "terminal_rows": terminal,
        "active_rows": len(rows) - terminal,
        "output_bytes": output_bytes,
        "db_bytes": db_path.stat().st_size,
    }


def _job_history_retention_once(root: Path, rows: int) -> dict[str, Any]:
    shutil.rmtree(root, ignore_errors=True)
    root.mkdir(parents=True, exist_ok=True)
    store = JobStore(root / "jobs.sqlite3")
    now = time.time()
    spec = json.dumps({"command": ["python"], "cwd": str(root), "timeout_sec": 1, "encoding": "utf-8"})
    with closing(store.connect()) as db, db:
        for index in range(rows):
            job_id = f"{index + 1:032x}"
            updated = now - 40 * 86_400 if index < rows // 2 else now - (rows - index)
            db.execute(
                "INSERT INTO jobs (id,request_key,fingerprint,spec,status,created,updated,version) VALUES (?,?,?,?,?,?,?,1)",
                (job_id, f"bench-{job_id}", f"fp-{job_id}", spec, "succeeded", updated, updated),
            )
            directory = store.output_dir / job_id
            directory.mkdir(exist_ok=True)
            (directory / "stdout.bin").write_bytes(index.to_bytes(4, "little") * 512)
            (directory / "stderr.bin").write_bytes(b"")
            os.utime(directory, (updated, updated))
        for suffix, status in (("a", "queued"), ("b", "running"), ("c", "orphaned")):
            job_id = suffix * 32
            db.execute(
                "INSERT INTO jobs (id,request_key,fingerprint,spec,status,created,updated,version) VALUES (?,?,?,?,?,?,?,1)",
                (job_id, f"bench-{job_id}", f"fp-{job_id}", spec, status, now - 100, now - 100),
            )
            directory = store.output_dir / job_id
            directory.mkdir(exist_ok=True)
            (directory / "stdout.bin").write_bytes(b"active")
            (directory / "stderr.bin").write_bytes(b"")
    policy = JobHistoryPolicy(
        max_age_sec=30 * 86_400,
        max_count=max(1, rows // 4),
        max_bytes=max(2_048, rows * 2_048 // 8),
        cleanup_interval_sec=0,
        orphan_grace_sec=60,
    )
    started = time.perf_counter()
    result = cleanup_job_history(store.path, store.output_dir, policy, now=now)
    cleanup_ms = (time.perf_counter() - started) * 1_000
    with closing(store.connect()) as db:
        active_remaining = int(db.execute("SELECT count(*) FROM jobs WHERE status IN ('queued','running','orphaned')").fetchone()[0])
    return {
        "fixture_terminal_rows": rows,
        "active_rows": 3,
        "active_rows_remaining": active_remaining,
        "output_bytes_per_terminal_job": 2_048,
        "cleanup_ms": round(cleanup_ms, 3),
        **result.to_dict(),
    }


def _cleanup_stale_temp_roots(
    parent: Path | None = None,
    *,
    now: float | None = None,
    max_age_sec: float = STALE_TEMP_MAX_AGE_SEC,
) -> int:
    root = parent or SETTINGS.state_dir / "benchmarks" / "tmp"
    if not unlinked_directory_tree(root):
        return 0
    current = time.time() if now is None else now
    removed = 0
    for child in root.iterdir():
        try:
            info = child.lstat()
        except OSError:
            continue
        if not stat.S_ISDIR(info.st_mode) or (
            os.name == "nt" and int(getattr(info, "st_file_attributes", 0)) & stat.FILE_ATTRIBUTE_REPARSE_POINT
        ):
            continue
        if _GC_STAGING.fullmatch(child.name):
            try:
                shutil.rmtree(child)
            except OSError:
                continue
            removed += 1
            continue
        owner = child / ".active.lock"
        try:
            if not stat.S_ISREG(owner.lstat().st_mode):
                continue
            age = current - info.st_mtime
        except OSError:
            continue
        if age < max_age_sec:
            continue
        try:
            with exclusive_file_lock(owner, timeout_sec=0):
                if current - child.stat().st_mtime < max_age_sec:
                    continue
            staging = root / f".gc-{uuid.uuid4().hex}"
            os.replace(child, staging)
            shutil.rmtree(staging)
        except (OSError, TimeoutError):
            continue
        removed += 1
    return removed


def _extension_baseline_disabled_once() -> dict[str, Any]:
    from dataclasses import is_dataclass, replace

    from core.extensions.config import load_extension_config
    if is_dataclass(SETTINGS):
        settings = replace(SETTINGS, extensions_enabled=False)
    else:
        from core.config import Settings
        settings = Settings(extensions_enabled=False)
    res = load_extension_config(settings)
    return {"configured_count": len(res.config.extensions), "error_code": res.error_code}


def _extension_baseline_empty_config_once(root: Path) -> dict[str, Any]:
    from dataclasses import is_dataclass, replace

    from core.extensions.config import load_extension_config
    from core.extensions.manager import ExtensionLifecycleManager
    root.mkdir(parents=True, exist_ok=True)
    path = root / "empty_ext.json"
    if not path.exists():
        path.write_text('{"schema_version": 1, "extensions": []}', encoding="utf-8")
    if is_dataclass(SETTINGS):
        settings = replace(SETTINGS, extensions_enabled=True, extensions_config=str(path))
    else:
        from core.config import Settings
        settings = Settings(extensions_enabled=True, extensions_config=str(path))
    res = load_extension_config(settings)
    manager = ExtensionLifecycleManager(config=res.config)
    return {"configured_count": len(res.config.extensions), "catalog_tools": len(manager.list_tools())}


def _build_bench_extension_definition(root: Path, ext_id: str = "fixture", mode: str = "readonly") -> Any:
    from core.extensions.models import ExtensionDefinition
    from core.platform import normalize_system
    fixture_path = PROJECT_ROOT / "tests" / "fixtures" / "fake_mcp_server.py"
    sub_root = root / ext_id
    sub_root.mkdir(parents=True, exist_ok=True)
    return ExtensionDefinition.model_validate_json(
        json.dumps({
            "extension_id": ext_id,
            "display_name": f"Bench {ext_id}",
            "provider": "fixture",
            "enabled": True,
            "launch_approved": True,
            "trust_class": "trusted",
            "platforms": [normalize_system()],
            "command": sys.executable,
            "arguments": [str(fixture_path.resolve()), "--workspace", str(sub_root), f"--mode={mode}"],
            "working_directory": str(sub_root),
            "tools": {
                "read_value": {
                    "remote_name": "read_value",
                    "domain": "system",
                    "mutation_class": "read_only",
                    "allow_call": True,
                    "profiles": ["full"],
                }
            },
        })
    )


def _extension_1_valid_startup_list_once(root: Path) -> dict[str, Any]:
    import anyio

    from core.extensions.manager import ExtensionLifecycleManager, ExtensionState
    defn = _build_bench_extension_definition(root, "fixture", "readonly")
    manager = ExtensionLifecycleManager()
    owner = manager.register_extension(defn)

    async def _run() -> int:
        discovered = 0
        keep = anyio.Event()
        async with anyio.create_task_group() as tg:
            await manager.start_all_background(tg, keep_alive_event=keep)
            with anyio.fail_after(10):
                while owner.state != ExtensionState.HEALTHY:
                    await anyio.sleep(0.01)
            discovered = len(manager.list_tools())
            keep.set()
            await manager.drain_and_stop()
        return discovered

    discovered = anyio.run(_run)
    return {"discovered_tools": discovered, "extension_id": defn.extension_id}


def _extension_1_call_roundtrip_once(root: Path) -> dict[str, Any]:
    import anyio

    from core.extensions.manager import ExtensionLifecycleManager, ExtensionState
    defn = _build_bench_extension_definition(root, "fixture_call", "readonly")
    manager = ExtensionLifecycleManager()
    owner = manager.register_extension(defn)

    async def _run() -> str:
        outcome = "failed"
        keep = anyio.Event()
        async with anyio.create_task_group() as tg:
            await manager.start_all_background(tg, keep_alive_event=keep)
            with anyio.fail_after(10):
                while owner.state != ExtensionState.HEALTHY:
                    await anyio.sleep(0.01)
            lease = manager.registry.create_lease(f"external.{defn.extension_id}.read_value")
            if lease is None:
                raise ValueError("could not create lease")
            result = await manager.execute_guarded_call(
                lease,
                {"value": "bench_test"},
            )
            outcome = str(result.get("outcome", "failed"))
            keep.set()
            await manager.drain_and_stop()
        return outcome

    outcome = anyio.run(_run)
    return {"outcome": outcome, "tool": f"external.{defn.extension_id}.read_value"}


def _extension_1_health_snapshot_once(root: Path) -> dict[str, Any]:
    from core.extensions.manager import ExtensionLifecycleManager
    defn = _build_bench_extension_definition(root, "fixture_health", "readonly")
    manager = ExtensionLifecycleManager()
    manager.register_extension(defn)
    snap = manager.health_snapshot("full")
    return {"configured_count": snap["configured_count"], "health_keys": len(snap)}


def _extension_n_parallel_startup_once(root: Path, count: int) -> dict[str, Any]:
    import anyio

    from core.extensions.manager import ExtensionLifecycleManager, ExtensionState
    from core.extensions.models import LIMITS
    manager = ExtensionLifecycleManager(parallel_startup=LIMITS.parallel_startup)
    for i in range(count):
        manager.register_extension(_build_bench_extension_definition(root, f"ext_{i}", "readonly"))

    async def _run() -> int:
        discovered = 0
        keep = anyio.Event()
        async with anyio.create_task_group() as tg:
            await manager.start_all_background(tg, keep_alive_event=keep)
            for own in manager._owners.values():
                with anyio.fail_after(15):
                    while own.state != ExtensionState.HEALTHY:
                        await anyio.sleep(0.01)
            discovered = len(manager.list_tools())
            keep.set()
            await manager.drain_and_stop()
        return discovered

    discovered = anyio.run(_run)
    return {"configured_count": count, "discovered_tools": discovered}


def _extension_fault_isolation_once(root: Path) -> dict[str, Any]:
    import anyio

    from core.extensions.manager import ExtensionLifecycleManager, ExtensionState
    defn_a = _build_bench_extension_definition(root, "hanging_ext", "startup_delayed")
    defn_b = _build_bench_extension_definition(root, "fast_ext", "readonly")
    manager = ExtensionLifecycleManager()
    manager.register_extension(defn_a)
    owner_b = manager.register_extension(defn_b)

    async def _run() -> bool:
        succeeded = False
        keep = anyio.Event()
        async with anyio.create_task_group() as tg:
            await manager.start_all_background(tg, keep_alive_event=keep)
            with anyio.fail_after(10):
                while owner_b.state != ExtensionState.HEALTHY:
                    await anyio.sleep(0.01)
            lease_b = manager.registry.create_lease(f"external.{defn_b.extension_id}.read_value")
            if lease_b is None:
                raise ValueError("could not create lease")
            res_b = await manager.execute_guarded_call(
                lease_b, {"value": "quick"},
            )
            succeeded = str(res_b.get("outcome")) == "succeeded"
            keep.set()
            await manager.drain_and_stop()
        return succeeded

    isolated = anyio.run(_run)
    return {"fault_isolated": isolated, "b_succeeded": isolated}


def _extension_bounded_resources_once(root: Path) -> dict[str, Any]:
    from core.extensions.models import LIMITS
    from core.extensions.transport import BoundedStderrDrain
    drain = BoundedStderrDrain(max_bytes=LIMITS.stderr_bytes)
    drain.feed_chunk(b"E" * (LIMITS.stderr_bytes * 10))
    tail = drain.get_tail()
    tail_len = len(tail.encode("utf-8"))
    if tail_len > LIMITS.stderr_bytes:
        raise ValueError("stderr tail exceeds limit")

    from core.recovery import OperationRecoveryJournal
    from core.recovery_models import ExternalOperationMetadata
    journal = OperationRecoveryJournal()
    journal.configure(root / "quota_ops.jsonl", "bench_runtime")
    for i in range(LIMITS.unresolved_operations):
        meta = ExternalOperationMetadata(
            extension_id="bench_quota",
            provider="p",
            public_name="external.bench_quota.act",
            remote_name="act",
            catalog_generation=1,
            schema_fingerprint="0" * 64,
            mutation_class="remote_mutation",
            dispatch_stage="prepared",
        )
        h = journal.begin("external.bench_quota.act", f"id_{i}", external_metadata=meta)
        if h is not None:
            journal.checkpoint_external_dispatch(h)
            journal.mark_uncertain(h, "timeout")
    count = journal.unresolved_external_count()
    return {
        "stderr_bounded": tail_len <= LIMITS.stderr_bytes,
        "stderr_tail_bytes": tail_len,
        "journal_quota_bounded": count == LIMITS.unresolved_operations,
        "unresolved_operations": count,
    }


@contextmanager
def _temporary_root(name: str) -> Iterator[str]:
    parent = SETTINGS.state_dir / "benchmarks" / "tmp"
    parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=f"{name}-", dir=parent) as directory:
        with exclusive_file_lock(Path(directory) / ".active.lock"):
            yield directory


def _exec_cost_summary(samples: list[dict[str, Any]], expected: str) -> dict[str, Any]:
    errors = sum("error" in sample or sample.get("sha256") != expected for sample in samples)
    if errors or not samples:
        return {"status": "error", "comparable": False, "errors": max(errors, 1)}
    elapsed = sorted(float(sample["elapsed_ms"]) for sample in samples)

    def percentile(fraction: float) -> float:
        position = (len(elapsed) - 1) * fraction
        low = int(position)
        return round(elapsed[low] + (elapsed[min(low + 1, len(elapsed) - 1)] - elapsed[low]) * (position - low), 3)

    return {
        "status": "ok",
        "comparable": True,
        "errors": 0,
        "count": len(samples),
        "p50_ms": percentile(0.5),
        "p95_ms": percentile(0.95),
        "p99_ms": percentile(0.99),
        "p99_exploratory": True,
        "output_sha256": expected,
        "samples": samples,
    }


def _exec_cost_worker(repeats: int, include_jobs: bool) -> dict[str, Any]:
    """Run only in the owned source copy: all default state paths then stay isolated."""
    import difflib

    from mcp import Client

    from core import resource_claims
    from tools.terminal import registry as terminal_registry

    root = PROJECT_ROOT / "fixture"
    root.mkdir()
    text = "alpha\nbeta\ngamma\ndelta\n"
    (root / "sample.txt").write_text(text, encoding="utf-8", newline="\n")
    for index in range(32):
        (root / f"item-{index:02}.txt").write_text(str(index), encoding="utf-8")
    after = text.upper()
    search = "\n".join(f"item-{index:02}.txt" for index in range(32))
    workloads = {
        "read": ["native", "python", "powershell"],
        "search": ["native", "python", "powershell"],
        "edit": ["sequential", "patch"],
    }
    if include_jobs:
        workloads.update({"short": ["direct", "job"], "medium": ["direct", "job"]})
    powershell = shutil.which("pwsh") or shutil.which("powershell")
    if not powershell:
        for workload in ("read", "search"):
            workloads[workload].remove("powershell")
    expected = {
        name: hashlib.sha256(value.encode()).hexdigest()
        for name, value in (
            ("read", text),
            ("search", search),
            ("edit", after),
            ("short", "completed"),
            ("medium", "completed"),
        )
    }
    samples: dict[str, list[dict[str, Any]]] = {f"{w}/{v}": [] for w, variants in workloads.items() for v in variants}
    waits: list[float] = []
    managed = resource_claims.managed_resource_claims

    @contextmanager
    def observed_claims(*args: Any, **kwargs: Any) -> Iterator[str]:
        started = time.perf_counter()
        with managed(*args, **kwargs) as capability:
            waits.append((time.perf_counter() - started) * 1_000)
            yield capability

    resource_claims.managed_resource_claims = observed_claims
    terminal_registry.managed_resource_claims = observed_claims
    job_ids: list[str] = []
    store: JobStore | None = None
    host_load = {"cpu_percent": psutil.cpu_percent(interval=0.1), "memory_percent": psutil.virtual_memory().percent}

    async def run() -> None:
        nonlocal store
        server = create_server()
        async with Client(server) as client:

            async def call(name: str, **arguments: Any) -> dict[str, Any]:
                result = await client.call_tool(name, arguments)
                payload = result.structured_content
                if not isinstance(payload, dict) or payload.get("ok") is False or result.is_error:
                    if isinstance(payload, dict):
                        raise RuntimeError(f"Benchmark tool failed: {name} {payload.get('error')}")
                    raise RuntimeError("Benchmark tool did not return a successful structured result.")
                return payload

            if include_jobs:
                store = server.job_store_getter()  # type: ignore[attr-defined]
            rng = random.Random(1401)
            for round_index in range(5 + repeats):
                for workload, variants in workloads.items():
                    order = list(variants)
                    rng.shuffle(order)
                    for variant in order:
                        target = root / "edit.txt"
                        if workload == "edit":
                            target.write_text(text, encoding="utf-8", newline="\n")
                        waits.clear()
                        started = time.perf_counter()
                        launches = 0
                        output = ""
                        try:
                            if workload in {"read", "search"}:
                                if variant == "native":
                                    if workload == "read":
                                        output = str((await call("read_file", path=str(root / "sample.txt"), encoding="utf-8"))["content"])
                                    else:
                                        found = await call(
                                            "search_files", path=str(root), query="item-", search_type="name", max_results=50
                                        )
                                        output = "\n".join(sorted(Path(item["path"]).name for item in found["items"]))
                                else:
                                    if variant == "python":
                                        script = (
                                            "from pathlib import Path; import sys; sys.stdout.write("
                                            + (
                                                "Path('sample.txt').read_text(encoding='utf-8')"
                                                if workload == "read"
                                                else "'\\n'.join(sorted(p.name for p in Path('.').glob('item-*')))"
                                            )
                                            + ")"
                                        )
                                        result = await call(
                                            "run_process",
                                            executable=sys.executable,
                                            args=["-c", script],
                                            cwd=str(root),
                                            timeout_sec=15,
                                            delivery="inline",
                                        )
                                    else:
                                        script = (
                                            "[Console]::Out.Write("
                                            + (
                                                "[IO.File]::ReadAllText((Join-Path (Get-Location) 'sample.txt'))"
                                                if workload == "read"
                                        else "((Get-ChildItem -File -Filter 'item-*' | Sort-Object Name | "
                                        "ForEach-Object {$_.Name}) -join [char]10)"
                                            )
                                            + ")"
                                        )
                                        result = await call(
                                            "run_powershell", script=script, cwd=str(root), timeout_sec=15, delivery="inline"
                                        )
                                    if result["exit_code"] != 0 or result["stdout"].get("truncated"):
                                        raise RuntimeError("Benchmark process failed or truncated its output.")
                                    launches = int(isinstance(result.get("pid"), int))
                                    output = str(result["stdout"]["text"]).replace("\r\n", "\n")
                            elif workload == "edit":
                                if variant == "sequential":
                                    for old in text.splitlines():
                                        await call(
                                            "replace_exact", path=str(target), old=old, new=old.upper(), expected_count=1, encoding="utf-8"
                                        )
                                else:
                                    # Both variants validate the same exact preimage in this uncontended fixture.
                                    patch = "diff --git a/edit.txt b/edit.txt\n" + "".join(
                                        difflib.unified_diff(
                                            text.splitlines(True),
                                            after.splitlines(True),
                                            fromfile="a/edit.txt",
                                            tofile="b/edit.txt",
                                        )
                                    )
                                    await call(
                                        "apply_patch",
                                        root=str(root),
                                        patch=patch,
                                        expected_sha256={"edit.txt": _hash_file(target)},
                                        encoding="utf-8",
                                    )
                                output = target.read_text(encoding="utf-8")
                            else:
                                delay = 0.01 if workload == "short" else 0.2
                                args = ["-c", f"import time; time.sleep({delay}); print('completed', end='')"]
                                if variant == "direct":
                                    result = await call(
                                        "run_process",
                                        executable=sys.executable,
                                        args=args,
                                        cwd=str(root),
                                        timeout_sec=15,
                                        delivery="inline",
                                    )
                                    if result["exit_code"] != 0:
                                        raise RuntimeError("Direct benchmark failed.")
                                    output = str(result["stdout"]["text"])
                                    launches = int(isinstance(result.get("pid"), int))
                                else:
                                    submitted = await call(
                                        "submit_job",
                                        executable=sys.executable,
                                        args=args,
                                        cwd=str(root),
                                        timeout_sec=15,
                                        idempotency_key=uuid.uuid4().hex,
                                    )
                                    job_id = str(submitted["job_id"])
                                    job_ids.append(job_id)
                                    version = 0
                                    deadline = time.monotonic() + 30
                                    while time.monotonic() < deadline:
                                        result = await call("job_wait", job_id=job_id, after_version=version, timeout=1)
                                        version = int(result["version"])
                                        if result["status"] in FINAL_JOB_STATES:
                                            break
                                    else:
                                        raise RuntimeError("Benchmark job did not complete within its deadline.")
                                    if result["status"] != "succeeded":
                                        raise RuntimeError("Benchmark job failed.")
                                    output = str((await call("job_output", job_id=job_id, delivery="inline"))["stdout"]["text"])
                                    assert store is not None
                                    row = store.raw(job_id)
                                    launches = len({int(row["worker_pid"]), int(row["pid"])})
                            sample = {
                                "elapsed_ms": round((time.perf_counter() - started) * 1_000, 3),
                                "sha256": hashlib.sha256(output.encode()).hexdigest(),
                                "process_launches": launches,
                                "claim_wait_ms": round(sum(waits), 3) if waits else None,
                            }
                        except Exception as exc:
                            sample = {"error": type(exc).__name__}
                        if sample.get("sha256") != expected[workload]:
                            samples[f"{workload}/{variant}"].append(sample)
                            raise RuntimeError(f"Non-equivalent benchmark sample: {workload}/{variant} {sample.get('error', 'checksum')}")
                        if round_index >= 5:
                            samples[f"{workload}/{variant}"].append(sample)

    try:
        asyncio.run(run())
    finally:
        resource_claims.managed_resource_claims = managed
        terminal_registry.managed_resource_claims = managed
        if store is not None:
            for job_id in job_ids:
                row = store.raw(job_id)
                if row["status"] not in FINAL_JOB_STATES:
                    store.cancel(job_id)
            deadline = time.monotonic() + 20
            while any(
                same_process(store.raw(j)[pid], store.raw(j)[created])
                for j in job_ids for pid, created in (("worker_pid", "worker_created"), ("pid", "pid_created"))
            ):
                if time.monotonic() >= deadline:
                    raise RuntimeError("Benchmark worker cleanup could not be verified.")
                time.sleep(0.02)
    powershell_hash, powershell_fingerprint_status = _benchmark_launcher_fingerprint(powershell)
    return {
        "warmups": 5,
        "repeats": repeats,
        "seed": 1401,
        "order": "paired_randomized",
        "host_load_at_start": host_load,
        "host_load_at_end": {"cpu_percent": psutil.cpu_percent(), "memory_percent": psutil.virtual_memory().percent},
        "runtime": {"python": platform.python_version(), "powershell_binary_sha256": powershell_hash,
                    "powershell_binary_fingerprint_status": powershell_fingerprint_status},
        "host_control": "shared host; exploratory timings, not a production SLA",
        "scope": "in-process SDK calls; no client/model/network latency; short=10ms, medium=200ms",
        "process_launch_scope": "controlled command roots plus durable workers; not OS helper processes",
        "claim_wait_scope": "parent-process admission only; worker claim/SQLite/serialization NOT_MEASURED until P14-02",
        "powershell_available": bool(powershell),
        "job_cases_enabled": include_jobs,
        "workers_cleaned": True,
        "cases": {key: _exec_cost_summary(value, expected[key.split("/")[0]]) for key, value in samples.items()},
    }


def _native_batch_worker(repeats: int, include_powershell: bool = True) -> dict[str, Any]:
    """Measure a private file-only prototype; it is not a production dispatcher."""
    import base64

    from mcp import Client
    from mcp.client._memory import InMemoryTransport
    from mcp.server import MCPServer
    from mcp.shared.message import SessionMessage
    from mcp.types import JSONRPCMessage
    from pydantic import TypeAdapter

    from core.extensions.bridge import ExtensionMCPServer
    from tools.filesystem import registry as filesystem_registry
    from tools.filesystem.service import read_window
    from tools.terminal import registry as terminal_registry

    root = PROJECT_ROOT / "native-batch-fixture"
    root.mkdir()
    contents = [f"fixture-{index}: alpha beta\n" * 16 for index in range(16)]
    paths = [root / f"item-{index}.txt" for index in range(16)]
    for path, content in zip(paths, contents, strict=True):
        path.write_bytes(content.encode())
    counters = {"requests": 0, "responses": 0, "request_frame_bytes": 0, "response_frame_bytes": 0}
    workers = {"active": 0, "peak": 0}
    lock = threading.Lock()
    request_ids: set[Any] = set()
    message_adapter: TypeAdapter[Any] = TypeAdapter(cast(Any, JSONRPCMessage))

    def frame(message: Any) -> Any:
        if not isinstance(message, SessionMessage):
            return message
        encoded = message.message.model_dump_json(by_alias=True, exclude_none=True) + "\n"
        value = json.loads(encoded)
        if value.get("method") == "tools/call":
            request_ids.add(value["id"])
            counters["requests"] += 1
            counters["request_frame_bytes"] += len(encoded.encode())
        elif value.get("id") in request_ids and ("result" in value or "error" in value):
            request_ids.remove(value["id"])
            counters["responses"] += 1
            counters["response_frame_bytes"] += len(encoded.encode())
        return SessionMessage(message_adapter.validate_json(encoded), message.metadata)

    class FramedStream:
        def __init__(self, stream: Any) -> None:
            self.stream = stream

        async def send(self, message: Any) -> None:
            await self.stream.send(frame(message))

        async def receive(self) -> Any:
            return frame(await self.stream.receive())

        def __aiter__(self) -> Any:
            return self

        async def __anext__(self) -> Any:
            import anyio

            try:
                return await self.receive()
            except anyio.EndOfStream:
                raise StopAsyncIteration from None

        async def aclose(self) -> None:
            await self.stream.aclose()

        async def __aenter__(self) -> Any:
            return self

        async def __aexit__(self, *args: Any) -> None:
            await self.aclose()

        def __getattr__(self, name: str) -> Any:
            return getattr(self.stream, name)

    powershell = include_powershell and bool(shutil.which("pwsh") or shutil.which("powershell"))
    allowed = frozenset({"read_file", "fixture_read_batch", *({"run_powershell"} if powershell else set())})
    server = ExtensionMCPServer("native-batch-benchmark", native_allowlist=allowed)
    filesystem_registry.register(server)
    if powershell:
        terminal_registry.register(server)

    def read(path: str) -> str:
        with lock:
            workers["active"] += 1
            workers["peak"] = max(workers["peak"], workers["active"])
        try:
            return str(read_window(path, start_line=1, end_line=None, offset=0, max_chars=4096, encoding="utf-8")["content"])
        finally:
            with lock:
                workers["active"] -= 1

    @server.tool()
    async def fixture_read_batch(
        paths: Annotated[list[str], Field(min_length=1, max_length=16)],
        parallel: Annotated[int, Field(ge=1, le=4)],
    ) -> dict[str, Any]:
        # Only owned benchmark files enter this private, unexported prototype.
        assert all(Path(path).parent == root for path in paths)
        semaphore = asyncio.Semaphore(parallel)

        async def one(path: str) -> str:
            async with semaphore:
                return await asyncio.to_thread(read, path)

        return {"ok": True, "contents": await asyncio.gather(*(one(path) for path in paths))}

    samples: dict[str, list[dict[str, Any]]] = {}
    peaks: dict[str, int] = {}
    expected: dict[int, str] = {}
    denied_dispatches = 0

    def hidden_probe() -> dict[str, bool]:
        nonlocal denied_dispatches
        denied_dispatches += 1
        return {"ok": True}

    MCPServer.add_tool(server, hidden_probe)

    @asynccontextmanager
    async def transport() -> Any:
        async with InMemoryTransport(server) as (reader, writer):
            yield FramedStream(reader), FramedStream(writer)

    async def run() -> None:
        rng = random.Random(1501)
        async with Client(transport()) as client:
            for name, arguments in (("hidden_probe", {}), ("fixture_read_batch", {"paths": [], "parallel": 5})):
                denied = await client.call_tool(name, arguments)
                assert denied.is_error
            assert denied_dispatches == 0 and workers["active"] == 0
            async def call(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
                result = await client.call_tool(name, arguments)
                value = result.structured_content
                if not isinstance(value, dict) or value.get("ok") is False or result.is_error:
                    raise RuntimeError("Native batch benchmark call failed.")
                return value

            for count in (1, 4, 8, 16):
                expected[count] = hashlib.sha256(json.dumps(contents[:count], ensure_ascii=False).encode()).hexdigest()
                variants = ["direct", "batch1", "batch2", "batch4", *(["powershell"] if powershell else [])]
                for round_index in range(5 + repeats):
                    rng.shuffle(variants)
                    for variant in variants:
                        for key in counters:
                            counters[key] = 0
                        workers["peak"] = 0
                        started = time.perf_counter()
                        if variant == "direct":
                            values = [(await call("read_file", {"path": str(path), "max_chars": 4096}))["content"]
                                      for path in paths[:count]]
                        elif variant == "powershell":
                            encoded = base64.b64encode(json.dumps([str(p) for p in paths[:count]]).encode()).decode()
                            script = (
                                "$p=[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('" + encoded + "'))|ConvertFrom-Json;"
                                "ConvertTo-Json -Compress -InputObject @($p|ForEach-Object{[IO.File]::ReadAllText($_)})"
                            )
                            value = await call("run_powershell", {"script": script, "cwd": str(root), "timeout_sec": 15})
                            assert value["exit_code"] == 0 and not value["stdout"]["truncated"]
                            values = json.loads(value["stdout"]["text"])
                        else:
                            values = (await call("fixture_read_batch", {
                                "paths": [str(path) for path in paths[:count]], "parallel": int(variant[-1]),
                            }))["contents"]
                        sample = {"elapsed_ms": (time.perf_counter() - started) * 1000, **counters,
                                  "sha256": hashlib.sha256(json.dumps(values, ensure_ascii=False).encode()).hexdigest()}
                        assert sample["sha256"] == expected[count] and workers["active"] == 0
                        if round_index >= 5:
                            key = f"{count}/{variant}"
                            samples.setdefault(key, []).append(sample)
                            peaks[key] = max(peaks.get(key, 0), workers["peak"])

    process = psutil.Process()
    cpu_before = sum(process.cpu_times()[:2])
    rss_before = process.memory_info().rss
    asyncio.run(run())
    cases = {}
    for key, values in samples.items():
        count = int(key.split("/")[0])
        summary = _exec_cost_summary(values, expected[count])
        summary["peak_workers"] = peaks[key]
        summary["synthetic_wait_saving_percent"] = {
            str(latency): (0 if latency == 0 else 100 * (count - values[0]["requests"]) / count)
            for latency in (0, 20, 100, 250)
        }
        cases[key] = summary
    return {"warmups": 5, "repeats": repeats, "seed": 1501, "order": "paired_randomized",
            "prototype_only": True, "scope": "actual in-process MCP calls; synthetic RTT is arithmetic, not tunnel latency",
            "wire_scope": "NDJSON roundtrip of actual SDK messages; in-memory transport, no tunnel",
            "denied_dispatches": denied_dispatches, "tokenizer": "NOT_MEASURED",
            "cpu_sec": sum(process.cpu_times()[:2]) - cpu_before,
            "rss_before_after_max_bytes": max(rss_before, process.memory_info().rss),
            "workers_cleaned": workers["active"] == 0, "cases": cases}


def _exec_cost_once(repeats: int, include_jobs: bool, *, suite: Literal["exec_cost", "native_batch"] = "exec_cost") -> dict[str, Any]:
    if not 1 <= repeats <= 100:
        raise ValueError("exec_cost requires 1..100 paired repeats.")
    # A source copy isolates hardcoded PROJECT_ROOT state, including detached job workers.
    temporary = Path(tempfile.mkdtemp(prefix="computerpilot-exec-cost-"))
    cleanup_verified = False
    try:
        source = temporary / "source"
        source.mkdir()
        paths: list[Path] = []
        for directory in ("core", "tools", "scripts"):
            files, truncated = iter_files(PROJECT_ROOT / directory, 1_000)
            if truncated:
                raise RuntimeError("Benchmark source snapshot exceeded its file limit.")
            paths.extend(path for path in files if path.suffix == ".py")
        manifest: dict[str, str] = {}
        remaining_bytes = 32 * MiB
        for path in paths:
            if not path.resolve().is_relative_to(PROJECT_ROOT.resolve()) or not unlinked_regular_file(path):
                raise RuntimeError("Benchmark source is not a contained regular file.")
            with path.open("rb") as stream:
                content = stream.read(remaining_bytes + 1)
            if len(content) > remaining_bytes:
                raise RuntimeError("Benchmark source snapshot exceeded its byte limit.")
            remaining_bytes -= len(content)
            relative = path.relative_to(PROJECT_ROOT).as_posix()
            manifest[relative] = hashlib.sha256(content).hexdigest()
            destination = source / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(content)
        fingerprint = hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest()
        command = [
            sys.executable,
            "-c",
            # Keep the parent's claim around this child, but never reuse its database inside the owned fixture.
            f"import os,json; os.environ.pop({RESOURCE_CAPABILITY_ENV!r},None); "
            "[os.environ.pop(k,None) for k in list(os.environ) if k.upper().startswith(('MCP_','PYTHON'))]; "
            "from scripts.perf_benchmark import "
            + ("_exec_cost_worker; print(json.dumps(_exec_cost_worker(" if suite == "exec_cost"
               else "_native_batch_worker; print(json.dumps(_native_batch_worker(")
            + str(repeats)
            + ", "
            + str(include_jobs if suite == "exec_cost" else True)
            + ")))",
        ]
        env = {key: "" for key in os.environ if key.upper().startswith(("MCP_", "PYTHON"))}
        env.update({"MCP_TOOL_PROFILE": "full", "MCP_LSP_PERSISTENT_ENABLED": "0", "MCP_WORKFLOW_DAG_ENABLED": "0"})
        result = run_bounded(
            command,
            cwd=source,
            timeout_sec=min(3_600, 120 + repeats * 30),
            stdout_limit=2_000_000,
            stderr_limit=2_000,
            env=env,
            delivery="inline",
        )
        if result["exit_code"] != 0:
            return {
                "name": suite, "status": "error", "comparable": False,
                "error": "isolated_benchmark_failed", "retained_fixture": str(temporary),
            }
        details = json.loads(result["stdout"]["text"])
        cleanup_verified = details.get("workers_cleaned") is True and all(
            case.get("status") == "ok" for case in details["cases"].values()
        )
        if not cleanup_verified:
            raise RuntimeError("Isolated benchmark cleanup or correctness is unverified; fixture retained.")
        return {"name": suite, "status": "ok", "source_manifest_sha256": fingerprint, "source_files": len(manifest), **details}

    finally:
        if cleanup_verified:
            if temporary.parent.resolve() != Path(tempfile.gettempdir()).resolve():
                raise RuntimeError("Benchmark cleanup target escaped temporary storage.")
            shutil.rmtree(temporary)


def run_benchmarks(
    *,
    profile: str,
    runs: int,
    suites: set[str],
    include_jobs: bool,
    include_browser: bool,
) -> dict[str, Any]:
    if profile not in PROFILE_LIMITS:
        raise ValueError(f"Unknown profile: {profile}")
    if runs < 1:
        raise ValueError("runs must be at least 1")
    unknown = suites - set(SUITES)
    if unknown:
        raise ValueError(f"Unknown suites: {sorted(unknown)}")
    limits = PROFILE_LIMITS[profile]
    stale_temp_dirs_removed = 0 if suites <= {"exec_cost", "native_batch"} else _cleanup_stale_temp_roots()
    results: list[dict[str, Any]] = []

    if "exec_cost" in suites:
        results.append(_exec_cost_once(runs, include_jobs))
    if "native_batch" in suites:
        results.append(_exec_cost_once(runs, False, suite="native_batch"))

    if "catalog" in suites:
        results.append(measure("tool_catalog", _catalog_once, runs))
    if "resilience" in suites:
        with _temporary_root("resilience") as temporary:
            root = Path(temporary)
            results.append(measure("recovery_pagination_1000", partial(_recovery_pagination_once, root / "recovery"), runs))
            results.append(measure("uia_traversal_1000", _uia_traversal_once, runs))
            results.append(measure("workflow_checkpoints_100", partial(_workflow_checkpoint_once, root / "workflow"), runs))
    if "workflow" in suites:
        with _temporary_root("workflow-scale") as temporary:
            root = Path(temporary)
            history_rows = 10_000
            workflow_store_bench, status_target = _prepare_workflow_history_fixture(
                root / "history",
                rows=history_rows,
                queued=100,
            )
            results.append(measure(
                "workflow_start_10000_history",
                partial(_workflow_start_once, workflow_store_bench),
                runs,
            ))
            results.append(measure(
                "workflow_status_10000_history",
                partial(_workflow_status_once, workflow_store_bench, status_target),
                runs,
            ))
            results.append(measure(
                "workflow_store_reopen_10000_history",
                partial(_workflow_store_reopen_once, workflow_store_bench.path),
                runs,
            ))
            results.append(measure(
                "workflow_execute_read_10000_history",
                partial(_workflow_execute_read_once, workflow_store_bench, root / "ready.txt"),
                runs,
            ))
            results.append(measure(
                "workflow_lease_renew_30s_ttl",
                partial(_workflow_lease_renew_once, workflow_store_bench),
                runs,
            ))
            results.append(measure(
                "workflow_queue_select_10000_history",
                partial(_workflow_queue_once, workflow_store_bench),
                runs,
            ))
            results.append(measure(
                "workflow_health_10000_history",
                partial(_workflow_health_once, workflow_store_bench),
                runs,
            ))
            results.append(measure(
                "workflow_db_density_10000_history",
                partial(_workflow_db_density_once, workflow_store_bench, history_rows),
                1,
            ))
    if "startup" in suites:
        results.append(measure("startup_launcher_validation", _launcher_validation_once, runs))
        results.append(measure("startup_server_cold", _server_cold_start_once, runs))
        results.append(measure("startup_server_warm", _server_warm_start_once, runs))
    if "output" in suites:
        for size in limits["output_sizes"]:
            results.append(measure(f"output_{size}_bytes", partial(_output_once, size), runs))

    if "audit" in suites:
        events = max(limits["audit_events"])
        with _temporary_root("audit") as temporary:
            root = Path(temporary)
            for threads in (1, 8):
                results.append(
                    measure(
                        f"audit_legacy_sync_{events}_t{threads}",
                        partial(_audit_legacy_sync_once, root / f"legacy-{threads}.jsonl", events, threads),
                        runs,
                    )
                )
                results.append(
                    measure(
                        f"audit_batched_{events}_t{threads}",
                        partial(_audit_batched_once, root / f"batched-{threads}.jsonl", events, threads),
                        runs,
                    )
                )

    if "backups" in suites:
        results.append(measure("backup_inventory", partial(_backup_inventory_once, SETTINGS.backup_dir), 1))
        backup_files = max(limits["backup_files"])
        with _temporary_root("backups") as temporary:
            results.append(
                measure(
                    f"backup_retention_{backup_files}_files",
                    partial(_backup_retention_once, Path(temporary) / "fixture", backup_files),
                    runs,
                )
            )
    if "retention" in suites:
        artifact_files = max(limits["artifact_files"])
        job_rows = max(limits["job_history_rows"])
        results.append(measure("artifact_inventory", partial(_artifact_inventory_once, SETTINGS.state_dir / "artifacts"), 1))
        results.append(measure("job_history_inventory", partial(_job_history_inventory_once, SETTINGS.state_dir / "jobs.sqlite3"), 1))
        with _temporary_root("retention") as temporary:
            root = Path(temporary)
            results.append(
                measure(
                    f"artifact_retention_{artifact_files}_files",
                    partial(_artifact_retention_once, root / "artifacts", artifact_files),
                    runs,
                )
            )
            results.append(
                measure(
                    f"job_history_retention_{job_rows}_rows",
                    partial(_job_history_retention_once, root / "jobs", job_rows),
                    runs,
                )
            )

    if "project" in suites:
        max_count = max(limits["ast_files"])
        with _temporary_root("ast") as temporary:
            paths = _prepare_python_fixture(Path(temporary), max_count)
            root = Path(temporary)
            for count in limits["ast_files"]:
                results.append(measure(f"ast_parse_{count}_files", partial(_parse_fixture, paths, count), runs))
                results.append(measure(f"find_function_{count}_files", partial(_project_lookup_once, root, count), runs))
                results.append(measure(f"code_context_{count}_files", partial(_context_lookup_once, root, count), runs))
            patch_count = min(max_count, 100)
            patch_root = root / "patch"
            patch = _prepare_patch_fixture(patch_root, patch_count)
            results.append(
                measure(
                    f"patch_dry_run_{patch_count}_files",
                    partial(_patch_dry_run_once, patch_root, patch, patch_count),
                    runs,
                )
            )
            impact_count = 1_000
            impact_root = root / "impact"
            _prepare_impact_fixture(impact_root, impact_count)
            results.append(measure("affected_tests_1000_modules", partial(_impact_once, impact_root, impact_count), runs))
            results.append(measure("verification_plan_1000_modules", partial(_verification_plan_once, impact_root), runs))
            results.append(measure("lsp_protocol_roundtrip", _lsp_protocol_once, runs))
            results.append(measure("workspace_edit_plan", partial(_workspace_edit_plan_once, root), runs))
            results.append(measure("git_read", partial(_git_read_once, PROJECT_ROOT), runs))
            results.append(measure("diagnostic_merge_1000", _diagnostic_merge_once, runs))
            results.append(measure("watch_snapshot", partial(_watch_snapshot_once, root), runs))

    if "search" in suites:
        max_count = max(limits["search_files"])
        with _temporary_root("search") as temporary:
            root = Path(temporary)
            _prepare_search_fixture(root, max_count)
            for count in limits["search_files"]:
                results.append(measure(f"name_search_{count}_files", partial(_search_fixture, root, count), runs))
                results.append(
                    measure(
                        f"name_search_streaming_first_page_{count}_files",
                        partial(_search_streaming_fixture, root, count),
                        runs,
                    )
                )
                results.append(
                    measure(
                        f"name_search_snapshot_build_{count}_files",
                        partial(_search_snapshot_build_fixture, root, count),
                        runs,
                    )
                )
                snapshot_store, snapshot_cursor, snapshot_fingerprint, _ = _create_search_snapshot(
                    root,
                    count,
                    namespace=f"page2-{count}",
                )
                results.append(
                    measure(
                        f"name_search_snapshot_page2_{count}_files",
                        partial(_search_snapshot_page_fixture, snapshot_store, snapshot_cursor, snapshot_fingerprint),
                        runs,
                    )
                )

    if "jobs" in suites:
        with _temporary_root("job-wait") as temporary:
            wait_store = JobStore(Path(temporary) / "jobs.sqlite3")
            results.append(measure("job_wait_change", partial(_job_wait_change_once, wait_store, 1), runs))
            results.append(measure("job_wait_timeout", partial(_job_wait_timeout_once, wait_store), runs))
            if profile == "full":
                results.append(measure("job_wait_50_waiters", partial(_job_wait_change_once, wait_store, 50), runs))
        if not include_jobs:
            results.append({"name": "jobs", "status": "skipped", "reason": "Pass --include-jobs to launch benchmark workers."})
        else:
            with _temporary_root("jobs") as temporary:
                job_store_bench = JobStore(Path(temporary) / "jobs.sqlite3")
                sequence = 0
                for count in limits["job_counts"]:

                    def job_case(count: int = count) -> dict[str, Any]:
                        nonlocal sequence
                        sequence += 1
                        return _job_batch_once(job_store_bench, count, sequence)

                    results.append(measure(f"jobs_{count}_concurrent", job_case, runs))

    if "browser" in suites:
        if not include_browser:
            results.append({"name": "browser", "status": "skipped", "reason": "Pass --include-browser to launch Playwright sessions."})
        else:
            for count in limits["browser_counts"]:
                results.append(measure(f"browser_{count}_sessions", partial(_browser_batch_once, count), runs))
            if profile == "full":
                results.append(measure("browser_idle_eviction", _browser_idle_eviction_once, runs))
                results.append(measure("browser_5_chromium_5_firefox", _browser_mixed_once, runs))

    if "extensions" in suites:
        with _temporary_root("extensions") as temporary:
            root = Path(temporary)
            results.append(measure("extensions_baseline_disabled", _extension_baseline_disabled_once, runs))
            results.append(measure("extensions_baseline_empty_config", partial(_extension_baseline_empty_config_once, root), runs))
            results.append(measure("extensions_1_valid_startup_list", partial(_extension_1_valid_startup_list_once, root), runs))
            results.append(measure("extensions_1_call_roundtrip", partial(_extension_1_call_roundtrip_once, root), runs))
            results.append(measure("extensions_1_health_snapshot", partial(_extension_1_health_snapshot_once, root), runs))
            for count in limits.get("extension_counts", [1]):
                results.append(
                    measure(
                        f"extensions_{count}_parallel_startup",
                        partial(_extension_n_parallel_startup_once, root, count),
                        runs,
                    )
                )
            results.append(measure("extensions_fault_isolation", partial(_extension_fault_isolation_once, root), runs))
            results.append(measure("extensions_bounded_resources", partial(_extension_bounded_resources_once, root), runs))

    if "lsp" in suites:
        for requests in limits["lsp_requests"]:
            for workspaces in limits["lsp_workspaces"]:
                results.append(
                    measure(
                        f"lsp_persistent_{requests}_requests_{workspaces}_workspaces",
                        partial(_lsp_session_case, requests, workspaces, True),
                        runs,
                    )
                )
                results.append(
                    measure(
                        f"lsp_one_shot_{requests}_requests_{workspaces}_workspaces",
                        partial(_lsp_session_case, requests, workspaces, False),
                        runs,
                    )
                )

    process = psutil.Process()
    return {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "profile": profile,
        "runs": runs,
        "git_commit": _git_commit(),
        "stale_temp_dirs_removed": stale_temp_dirs_removed,
        "host": {
            "platform": platform.platform(),
            "python": platform.python_version(),
            "cpu_logical": psutil.cpu_count(logical=True),
            "memory_total_mb": round(psutil.virtual_memory().total / MiB, 1),
            "benchmark_process_rss_mb": round(process.memory_info().rss / MiB, 3),
        },
        "limits": limits,
        "extension_caps": asdict(LIMITS),
        "results": results,
    }


def _default_output() -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    return SETTINGS.state_dir / "benchmarks" / "reports" / f"benchmark-{stamp}-{uuid.uuid4().hex}.json"


def _cleanup_managed_reports(
    *, now: float | None = None, max_count: int = REPORT_MAX_COUNT,
    max_bytes: int = REPORT_MAX_BYTES,
) -> int:
    """Prune only completed, auto-named reports; never touch explicit output paths."""
    root = SETTINGS.state_dir / "benchmarks" / "reports"
    if not unlinked_directory_tree(root):
        return 0
    current = time.time() if now is None else now
    removed = 0
    try:
        with exclusive_file_lock(root / ".retention.lock", timeout_sec=1):
            if not unlinked_directory_tree(root):
                return 0
            entries: list[tuple[float, Path, os.stat_result]] = []
            temporary_entries: list[tuple[Path, os.stat_result]] = []
            for path in root.iterdir():
                is_report = bool(_MANAGED_REPORT.fullmatch(path.name))
                is_temporary = bool(_MANAGED_REPORT_TEMP.fullmatch(path.name))
                if not (is_report or is_temporary) or not unlinked_regular_file(path):
                    continue
                try:
                    info = path.lstat()
                except OSError:
                    continue
                if is_temporary:
                    temporary_entries.append((path, info))
                else:
                    entries.append((info.st_mtime, path, info))
            entries.sort(key=lambda entry: (entry[0], entry[1].name))
            total_bytes = sum(info.st_size for _, _, info in entries)
            remaining_count = len(entries)
            for modified, path, info in entries:
                if current - modified < REPORT_RECENT_GRACE_SEC:
                    continue
                if current - modified < REPORT_MAX_AGE_SEC and remaining_count <= max_count and total_bytes <= max_bytes:
                    continue
                try:
                    latest = path.lstat()
                    if not unlinked_regular_file(path) or (
                        latest.st_dev, latest.st_ino, latest.st_mtime_ns, latest.st_size
                    ) != (info.st_dev, info.st_ino, info.st_mtime_ns, info.st_size):
                        continue
                    path.unlink()
                except OSError:
                    continue
                removed += 1
                remaining_count -= 1
                total_bytes -= info.st_size
            for path, info in temporary_entries:
                if current - info.st_mtime < REPORT_TEMP_MAX_AGE_SEC:
                    continue
                try:
                    latest = path.lstat()
                    if not unlinked_regular_file(path) or (
                        latest.st_dev, latest.st_ino, latest.st_mtime_ns, latest.st_size
                    ) != (info.st_dev, info.st_ino, info.st_mtime_ns, info.st_size):
                        continue
                    path.unlink()
                except OSError:
                    continue
                removed += 1
    except (OSError, TimeoutError):
        pass  # Retention must not prevent publishing a benchmark result.
    return removed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run repeatable MCP performance benchmarks.")
    parser.add_argument("--profile", choices=sorted(PROFILE_LIMITS), default="quick")
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--suite", action="append", choices=SUITES, dest="suites")
    parser.add_argument("--include-jobs", action="store_true", help="Launch real durable benchmark jobs.")
    parser.add_argument("--include-browser", action="store_true", help="Launch real Playwright browser sessions.")
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--stdout", action="store_true", help="Also print the JSON report to stdout.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.output is None:
        _cleanup_managed_reports()  # Also recover reports left by an earlier process.
    suites = set(args.suites or ("catalog", "startup", "output", "project", "search", "jobs", "browser", "audit", "resilience", "workflow"))
    report = run_benchmarks(
        profile=args.profile,
        runs=args.runs,
        suites=suites,
        include_jobs=args.include_jobs,
        include_browser=args.include_browser,
    )
    output = args.output or _default_output()
    if not output.is_absolute():
        output = PROJECT_ROOT / output
    output.parent.mkdir(parents=True, exist_ok=True)
    publication_lock = exclusive_file_lock(output.parent / ".retention.lock") if args.output is None else nullcontext()
    with publication_lock:
        descriptor, temporary_name = tempfile.mkstemp(prefix=f".{output.name}.", suffix=".tmp", dir=output.parent)
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                json.dump(report, stream, indent=2, ensure_ascii=False)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, output)
        finally:
            temporary.unlink(missing_ok=True)
    if args.output is None:
        _cleanup_managed_reports()
    if args.stdout:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    print(f"benchmark_report={output}")
    errors = [item for item in report["results"] if item.get("status") == "error"]
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
