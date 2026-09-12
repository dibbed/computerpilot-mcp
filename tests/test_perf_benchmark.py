from __future__ import annotations

from scripts.perf_benchmark import PROFILE_LIMITS, BenchmarkSkip, _stats, measure, run_benchmarks


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
