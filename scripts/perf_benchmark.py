"""Repeatable local performance benchmarks for the Windows Developer Agent MCP."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import platform
import shutil
import statistics
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import partial
from pathlib import Path
from typing import Any, Literal

import psutil

from core.artifact_retention import ArtifactPolicy, cleanup_artifacts
from core.audit import AuditPolicy, AuditWriter
from core.backups import BackupPolicy, backup_created_at, cleanup_backups
from core.config import PROJECT_ROOT, SETTINGS
from core.executor import run_bounded
from core.job_retention import JobHistoryPolicy, cleanup_job_history
from core.jobs import JobStore, same_process
from core.registry import create_server
from tools.filesystem.search_snapshots import SearchSnapshotStore, search_fingerprint
from tools.filesystem.service import search_by_name, search_by_name_streaming
from tools.project.context import lookup_code_context
from tools.project.service import parse_python

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
    },
}
SUITES = ("catalog", "startup", "output", "project", "search", "jobs", "browser", "audit", "backups", "retention")
FINAL_JOB_STATES = {"succeeded", "failed", "cancelled", "timed_out", "interrupted"}
STALE_TEMP_MAX_AGE_SEC = 24 * 60 * 60


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
        "serialization_ms": round(serialization_ms, 3),
    }


def _launcher_validation_once() -> dict[str, Any]:
    result = subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(PROJECT_ROOT / "scripts" / "bootstrap.ps1"),
        ],
        cwd=PROJECT_ROOT,
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

    def wait_one() -> dict[str, Any]:
        barrier.wait(timeout=5)
        return store.wait(job_id, after_version=1, timeout=5)

    with ThreadPoolExecutor(max_workers=waiters) as pool:
        futures = [pool.submit(wait_one) for _ in range(waiters)]
        barrier.wait(timeout=5)
        time.sleep(0.1)
        changed_at = time.perf_counter()
        store.update(job_id, status="succeeded", exit_code=0)
        results = [future.result(timeout=5) for future in futures]
        wake_latency_ms = (time.perf_counter() - changed_at) * 1_000
    if not all(result["changed"] and not result["timed_out"] for result in results):
        raise RuntimeError("job_wait failed to observe the authoritative version change.")
    return {
        "waiters": waiters,
        "update_delay_ms": 100.0,
        "wake_latency_ms": round(wake_latency_ms, 3),
        "observed_version": int(results[0]["version"]),
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
    if not root.is_dir():
        return 0
    current = time.time() if now is None else now
    removed = 0
    for child in root.iterdir():
        if not child.is_dir():
            continue
        try:
            age = current - child.stat().st_mtime
        except OSError:
            continue
        if age < max_age_sec:
            continue
        try:
            shutil.rmtree(child)
        except OSError:
            continue
        removed += 1
    return removed

def _temporary_root(name: str) -> tempfile.TemporaryDirectory[str]:
    parent = SETTINGS.state_dir / "benchmarks" / "tmp"
    parent.mkdir(parents=True, exist_ok=True)
    return tempfile.TemporaryDirectory(prefix=f"{name}-", dir=parent)


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
    stale_temp_dirs_removed = _cleanup_stale_temp_roots()
    results: list[dict[str, Any]] = []

    if "catalog" in suites:
        results.append(measure("tool_catalog", _catalog_once, runs))
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
        if not include_jobs:
            results.append({"name": "jobs", "status": "skipped", "reason": "Pass --include-jobs to launch benchmark workers."})
        else:
            with _temporary_root("jobs") as temporary:
                store = JobStore(Path(temporary) / "jobs.sqlite3")
                sequence = 0
                for count in limits["job_counts"]:
                    def job_case(count: int = count) -> dict[str, Any]:
                        nonlocal sequence
                        sequence += 1
                        return _job_batch_once(store, count, sequence)

                    results.append(measure(f"jobs_{count}_concurrent", job_case, runs))
                results.append(measure("job_wait_change", partial(_job_wait_change_once, store, 1), runs))
                results.append(measure("job_wait_timeout", partial(_job_wait_timeout_once, store), runs))
                if profile == "full":
                    results.append(measure("job_wait_50_waiters", partial(_job_wait_change_once, store, 50), runs))

    if "browser" in suites:
        if not include_browser:
            results.append({"name": "browser", "status": "skipped", "reason": "Pass --include-browser to launch Playwright sessions."})
        else:
            for count in limits["browser_counts"]:
                results.append(measure(f"browser_{count}_sessions", partial(_browser_batch_once, count), runs))
            if profile == "full":
                results.append(measure("browser_idle_eviction", _browser_idle_eviction_once, runs))
                results.append(measure("browser_5_chromium_5_firefox", _browser_mixed_once, runs))

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
        "results": results,
    }


def _default_output() -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return SETTINGS.state_dir / "benchmarks" / f"benchmark-{stamp}.json"


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
    suites = set(args.suites or ("catalog", "startup", "output", "project", "search", "jobs", "browser", "audit"))
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
    output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    if args.stdout:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    print(f"benchmark_report={output}")
    errors = [item for item in report["results"] if item.get("status") == "error"]
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
