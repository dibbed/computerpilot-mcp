from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from core.jobs import JobStore
from core.registry import create_server
from scripts.perf_benchmark import (
    PROFILE_LIMITS,
    BenchmarkSkip,
    MiB,
    _artifact_inventory_once,
    _artifact_retention_once,
    _audit_batched_once,
    _audit_legacy_sync_once,
    _backup_inventory_once,
    _backup_retention_once,
    _browser_batch_once,
    _browser_pool_details,
    _cleanup_stale_temp_roots,
    _job_history_inventory_once,
    _job_history_retention_once,
    _job_wait_change_once,
    _job_wait_timeout_once,
    _launcher_validation_once,
    _output_once,
    _prepare_python_fixture,
    _project_lookup_once,
    _stats,
    measure,
    run_benchmarks,
)


def test_benchmark_profiles_cover_planned_scale_points() -> None:
    full = PROFILE_LIMITS["full"]
    assert full["output_sizes"] == [1_024, 1_048_576, 100 * 1_048_576]
    assert full["ast_files"] == [100, 1_000, 10_000]
    assert full["search_files"] == [10_000, 100_000]
    assert full["job_counts"] == [1, 10, 50]
    assert full["browser_counts"] == [1, 5, 20]
    assert full["audit_events"] == [20_000]
    assert full["backup_files"] == [2_000]
    assert full["artifact_files"] == [5_000]
    assert full["job_history_rows"] == [2_000]


def test_browser_pool_details_report_instances_contexts_and_keys() -> None:
    manager = SimpleNamespace(
        _pools={
            "chromium": SimpleNamespace(
                active_contexts=5,
                key=SimpleNamespace(browser_name="chromium", headless=True),
            ),
            "firefox": SimpleNamespace(
                active_contexts=5,
                key=SimpleNamespace(browser_name="firefox", headless=True),
            ),
        }
    )
    details = _browser_pool_details(manager, 10)
    assert details["sessions"] == 10
    assert details["browser_instances"] == 2
    assert details["contexts"] == 10
    assert details["pool_keys"] == [
        {"browser": "chromium", "headless": True, "active_contexts": 5},
        {"browser": "firefox", "headless": True, "active_contexts": 5},
    ]


def test_browser_batch_reports_open_navigation_and_cleanup_timings(monkeypatch: pytest.MonkeyPatch) -> None:
    import tools.browser.manager as browser_manager_module

    class FakeManager:
        def __init__(self) -> None:
            self._pools = {
                "chromium": SimpleNamespace(
                    active_contexts=2,
                    key=SimpleNamespace(browser_name="chromium", headless=True),
                )
            }
            self._playwright = SimpleNamespace(stop=AsyncMock())

        async def open(self, *args: object, **kwargs: object) -> dict[str, bool]:
            await asyncio.sleep(0)
            return {"ok": True}

        async def close(self, session_id: str) -> dict[str, bool]:
            await asyncio.sleep(0)
            return {"ok": True}

    monkeypatch.setattr(browser_manager_module, "BrowserManager", FakeManager)
    details = _browser_batch_once(2)
    assert details["browser_instances"] == 1
    assert details["contexts"] == 2
    assert details["open_latency_ms"] >= 0
    assert details["parallel_navigation_ms"] >= 0
    assert details["cleanup_latency_ms"] >= 0


def test_stats_are_stable_and_population_based() -> None:
    result = _stats([1.0, 2.0, 3.0])
    assert result == {
        "min_ms": 1.0,
        "median_ms": 2.0,
        "mean_ms": 2.0,
        "max_ms": 3.0,
        "stdev_ms": 0.816,
    }


def test_measure_reports_success_skip_and_error() -> None:
    success = measure("success", lambda: {"value": 1}, 2)
    assert success["status"] == "ok"
    assert success["runs"] == 2
    assert success["details"] == {"value": 1}

    def skipped() -> dict[str, object]:
        raise BenchmarkSkip("optional dependency unavailable")

    skipped_result = measure("skipped", skipped, 1)
    assert skipped_result == {
        "name": "skipped",
        "status": "skipped",
        "reason": "optional dependency unavailable",
    }

    def failed() -> dict[str, object]:
        raise ValueError("boom")

    failed_result = measure("failed", failed, 1)
    assert failed_result["status"] == "error"
    assert failed_result["error"] == "ValueError"
    assert failed_result["message"] == "boom"


def test_catalog_benchmark_smoke() -> None:
    report = run_benchmarks(
        profile="quick",
        runs=1,
        suites={"catalog"},
        include_jobs=False,
        include_browser=False,
    )
    assert report["schema_version"] == 1
    assert report["profile"] == "quick"
    assert len(report["results"]) == 1
    result = report["results"][0]
    assert result["name"] == "tool_catalog"
    assert result["status"] == "ok"
    assert result["details"]["tool_count"] == 65
    assert result["details"]["catalog_json_bytes"] > 10_000


def test_catalog_benchmark_uses_complete_wire_tool_models() -> None:
    report = run_benchmarks(
        profile="quick",
        runs=1,
        suites={"catalog"},
        include_jobs=False,
        include_browser=False,
    )
    result = report["results"][0]
    tools = asyncio.run(create_server().list_tools())
    wire_catalog = [tool.model_dump(mode="json", by_alias=True, exclude_none=True) for tool in tools]
    expected = len(json.dumps(wire_catalog, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    assert result["details"]["catalog_json_bytes"] == expected


def test_output_benchmark_measures_current_default_inline_behavior() -> None:
    result = _output_once(2 * MiB)
    assert result["delivery"] == "inline"
    assert result["response_json_bytes"] > 2 * MiB


def test_sampler_includes_descendant_processes() -> None:
    def child_case() -> dict[str, int]:
        completed = subprocess.run(
            [sys.executable, "-c", "import time; payload=bytearray(20_000_000); time.sleep(0.2)"],
            check=False,
        )
        return {"exit_code": completed.returncode}

    result = measure("child-tree", child_case, 1)
    assert result["status"] == "ok"
    assert result["rss_scope"] == "process_tree"
    assert result["peak_processes"] >= 2


def test_launcher_benchmark_executes_real_bootstrap(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[str] = []

    def fake_run(command: list[str], **_: object) -> subprocess.CompletedProcess[bytes]:
        captured.extend(command)
        return subprocess.CompletedProcess(command, 0, stdout=b"", stderr=b"")

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert _launcher_validation_once() == {"exit_code": 0}
    assert captured[0].casefold() == "powershell.exe"
    assert any(part.endswith("bootstrap.ps1") for part in captured)


def test_project_lookup_benchmark_exercises_real_tool(tmp_path: Path) -> None:
    _prepare_python_fixture(tmp_path, 5)
    result = _project_lookup_once(tmp_path, 5)
    assert result["files"] == 5
    assert result["total_count"] == 1


def test_search_benchmark_reports_exact_streaming_and_snapshot_pagination() -> None:
    report = run_benchmarks(
        profile="quick",
        runs=1,
        suites={"search"},
        include_jobs=False,
        include_browser=False,
    )
    results = {item["name"]: item for item in report["results"]}
    exact = results["name_search_1000_files"]
    streaming = results["name_search_streaming_first_page_1000_files"]
    snapshot_build = results["name_search_snapshot_build_1000_files"]
    snapshot_page2 = results["name_search_snapshot_page2_1000_files"]

    assert exact["status"] == "ok"
    assert exact["details"]["matches"] == 1
    assert streaming["status"] == "ok"
    assert streaming["details"]["matches_returned"] == 50
    assert streaming["details"]["has_more"] is True
    assert streaming["details"]["scanned_files"] == 51
    assert snapshot_build["status"] == "ok"
    assert snapshot_build["details"]["page_two_items"] == 50
    assert snapshot_build["details"]["snapshot_bytes"] > 0
    assert snapshot_page2["status"] == "ok"
    assert snapshot_page2["details"]["matches_returned"] == 50
    assert snapshot_page2["details"]["scanned_files"] == 0


def test_job_wait_benchmark_covers_change_and_timeout_paths(tmp_path: Path) -> None:
    store = JobStore(tmp_path / "jobs.sqlite3")
    changed = _job_wait_change_once(store, waiters=4)
    timed_out = _job_wait_timeout_once(store, timeout=0.05)

    assert changed["waiters"] == 4
    assert changed["observed_version"] == 2
    assert changed["wake_latency_ms"] >= 0
    assert timed_out["observed_version"] == 1
    assert timed_out["elapsed_ms"] >= 40


def test_stale_benchmark_temp_cleanup_is_age_bounded(tmp_path: Path) -> None:
    stale = tmp_path / "stale"
    recent = tmp_path / "recent"
    stale.mkdir()
    recent.mkdir()
    os.utime(stale, (1, 1))
    os.utime(recent, (950, 950))
    removed = _cleanup_stale_temp_roots(tmp_path, now=1_000, max_age_sec=100)
    assert removed == 1
    assert stale.exists() is False
    assert recent.is_dir()

def test_audit_benchmark_preserves_all_records_and_reports_caller_latency(tmp_path: Path) -> None:
    legacy = _audit_legacy_sync_once(tmp_path / "legacy.jsonl", events=200, threads=4)
    batched = _audit_batched_once(tmp_path / "batched.jsonl", events=200, threads=4)

    assert legacy["lines"] == 200
    assert batched["lines"] == 200
    assert legacy["bytes"] == batched["bytes"]
    assert legacy["caller_p95_ms"] >= 0
    assert batched["caller_p95_ms"] >= 0
    assert batched["batch_size"] >= 1
    assert batched["queue_max"] >= 64


def test_audit_suite_is_available_through_benchmark_runner() -> None:
    report = run_benchmarks(
        profile="quick",
        runs=1,
        suites={"audit"},
        include_jobs=False,
        include_browser=False,
    )
    results = {item["name"]: item for item in report["results"]}
    assert set(results) == {
        "audit_legacy_sync_2000_t1",
        "audit_batched_2000_t1",
        "audit_legacy_sync_2000_t8",
        "audit_batched_2000_t8",
    }
    assert all(item["status"] == "ok" for item in results.values())
    assert all(item["details"]["lines"] == 2_000 for item in results.values())


def test_backup_inventory_reports_dedup_savings_and_gate(tmp_path: Path) -> None:
    (tmp_path / "a.bak").write_bytes(b"same")
    (tmp_path / "b.bak").write_bytes(b"same")
    (tmp_path / "c.bak").write_bytes(b"different")

    result = _backup_inventory_once(tmp_path)

    assert result["files"] == 3
    assert result["unique_hashes"] == 2
    assert result["duplicate_files"] == 1
    assert result["dedup_savings_bytes"] == 4
    assert result["dedup_gate_pass"] is False


def test_backup_retention_benchmark_enforces_age_and_quota(tmp_path: Path) -> None:
    result = _backup_retention_once(tmp_path / "fixture", files=40)

    assert result["fixture_files"] == 40
    assert result["removed_for_age"] == 20
    assert result["removed_for_quota"] > 0
    assert result["quota_satisfied"] is True
    assert result["remaining_bytes"] <= 40 * 2_048 // 4


def test_backup_suite_is_available_through_benchmark_runner(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import scripts.perf_benchmark as perf

    monkeypatch.setattr(perf, "SETTINGS", SimpleNamespace(backup_dir=tmp_path, state_dir=tmp_path / "state"))
    report = run_benchmarks(
        profile="quick",
        runs=1,
        suites={"backups"},
        include_jobs=False,
        include_browser=False,
    )
    results = {item["name"]: item for item in report["results"]}
    assert set(results) == {"backup_inventory", "backup_retention_500_files"}
    assert all(item["status"] == "ok" for item in results.values())


def test_retention_benchmark_helpers_cover_artifacts_and_jobs(tmp_path: Path) -> None:
    artifacts_dir = tmp_path / "artifacts"
    artifacts_dir.mkdir()
    (artifacts_dir / "a.bin").write_bytes(b"123")
    inventory = _artifact_inventory_once(artifacts_dir)
    assert inventory["files"] == 1
    assert inventory["bytes"] == 3

    artifact_result = _artifact_retention_once(tmp_path / "artifact-fixture", files=40)
    assert artifact_result["removed_for_age"] == 20
    assert artifact_result["remaining_files"] <= 10
    assert artifact_result["quota_satisfied"] is True

    job_root = tmp_path / "job-fixture"
    job_result = _job_history_retention_once(job_root, rows=40)
    assert job_result["removed_for_age"] == 20
    assert job_result["active_rows_remaining"] == 3
    assert job_result["remaining_terminal_rows"] <= 10
    assert job_result["quota_satisfied"] is True
    job_inventory = _job_history_inventory_once(job_root / "jobs.sqlite3")
    assert job_inventory["active_rows"] == 3


def test_retention_suite_is_available_through_benchmark_runner(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import scripts.perf_benchmark as perf

    state_dir = tmp_path / "state"
    state_dir.mkdir()
    monkeypatch.setattr(perf, "SETTINGS", SimpleNamespace(state_dir=state_dir))
    report = run_benchmarks(
        profile="quick",
        runs=1,
        suites={"retention"},
        include_jobs=False,
        include_browser=False,
    )
    results = {item["name"]: item for item in report["results"]}
    assert set(results) == {
        "artifact_inventory",
        "job_history_inventory",
        "artifact_retention_1000_files",
        "job_history_retention_500_rows",
    }
    assert all(item["status"] == "ok" for item in results.values())
