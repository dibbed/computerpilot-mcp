from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from core.registry import create_server
from scripts.perf_benchmark import (
    PROFILE_LIMITS,
    BenchmarkSkip,
    MiB,
    _cleanup_stale_temp_roots,
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
    assert result["details"]["tool_count"] == 59
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
