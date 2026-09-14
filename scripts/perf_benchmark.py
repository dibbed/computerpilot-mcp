"""Repeatable local performance benchmarks for the Windows Developer Agent MCP."""

from __future__ import annotations

import argparse
import asyncio
import json
import platform
import shutil
import statistics
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import partial
from pathlib import Path
from typing import Any, Literal

import psutil

from core.config import PROJECT_ROOT, SETTINGS
from core.executor import run_bounded
from core.jobs import JobStore, same_process
from core.registry import create_server
from tools.filesystem.search_snapshots import SearchSnapshotStore, search_fingerprint
from tools.filesystem.service import search_by_name, search_by_name_streaming
from tools.project.service import parse_python

MiB = 1_048_576
PROFILE_LIMITS: dict[str, dict[str, list[int]]] = {
    "quick": {
        "output_sizes": [1_024, MiB],
        "ast_files": [100],
        "search_files": [1_000],
        "job_counts": [1, 10],
        "browser_counts": [1],
    },
    "full": {
        "output_sizes": [1_024, MiB, 100 * MiB],
        "ast_files": [100, 1_000, 10_000],
        "search_files": [10_000, 100_000],
        "job_counts": [1, 10, 50],
        "browser_counts": [1, 5, 20],
    },
}
SUITES = ("catalog", "startup", "output", "project", "search", "jobs", "browser")
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
            [sys.executable, "-c", "pass"],
            PROJECT_ROOT,
            30.0,
            f"benchmark-{run_key}-{count}-{index}",
        )
        job_ids.append(str(submitted["job_id"]))
    deadline = time.monotonic() + 60
    states: list[str] = []
    while time.monotonic() < deadline:
        states = [str(store.get(job_id)["status"]) for job_id in job_ids]
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
    return {"jobs": count, "states": states}


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
            details = _browser_pool_details(manager, count)
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
            await asyncio.gather(*(manager.close(session_id) for session_id in session_ids), return_exceptions=True)
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

    if "project" in suites:
        max_count = max(limits["ast_files"])
        with _temporary_root("ast") as temporary:
            paths = _prepare_python_fixture(Path(temporary), max_count)
            root = Path(temporary)
            for count in limits["ast_files"]:
                results.append(measure(f"ast_parse_{count}_files", partial(_parse_fixture, paths, count), runs))
                results.append(measure(f"find_function_{count}_files", partial(_project_lookup_once, root, count), runs))

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

    if "browser" in suites:
        if not include_browser:
            results.append({"name": "browser", "status": "skipped", "reason": "Pass --include-browser to launch Playwright sessions."})
        else:
            for count in limits["browser_counts"]:
                results.append(measure(f"browser_{count}_sessions", partial(_browser_batch_once, count), runs))
            if profile == "full":
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
    suites = set(args.suites or ("catalog", "startup", "output", "project", "search", "jobs", "browser"))
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
