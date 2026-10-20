from __future__ import annotations

import asyncio
import os
import subprocess
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from core import resource_health
from core.config import Settings
from core.jobs import JobStore


@pytest.mark.parametrize("maximum, ratio, pressure", [(0, None, "normal"), (4, 1.0, "critical"), (5, 0.8, "warning")])
def test_backup_count_budget_reuses_usage_scan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, maximum: int, ratio: float | None, pressure: str,
) -> None:
    settings = replace(Settings(), state_dir=tmp_path, backup_max_count=maximum, backup_max_bytes=0)
    settings.backup_dir.mkdir()
    for index in range(4):
        (settings.backup_dir / f"{index}.bak").write_bytes(b"")
    (settings.backup_dir / "ignored.txt").write_bytes(b"ignored")
    monkeypatch.setattr(resource_health, "SETTINGS", settings)
    monkeypatch.setattr(resource_health.PYTHON_METADATA_CACHE, "stats", lambda: {"entries": 0, "bytes": 0})
    monkeypatch.setattr(resource_health.SEARCH_SNAPSHOTS, "stats", lambda: {"count": 0, "bytes": 0})
    monkeypatch.setattr(resource_health, "background_stats", lambda: {"running": 0, "tracked": 0})
    scans = 0
    iterdir = Path.iterdir

    def counted_iterdir(path: Path) -> Iterator[Path]:
        nonlocal scans
        if path == settings.backup_dir:
            scans += 1
        return iterdir(path)

    monkeypatch.setattr(Path, "iterdir", counted_iterdir)

    metrics = resource_health.collect_resource_metrics(
        {"active_sessions": 0, "pending_sessions": 0, "active_pools": 0, "active_contexts": 0}
    )

    assert metrics["backup_count"] == 4
    assert metrics["budgets"]["backup_count"] == {"value": 4, "max": maximum, "unit": "files", "usage_ratio": ratio}
    assert metrics["resource_pressure"] == pressure
    assert scans == 1


def test_job_metrics_account_for_terminal_output_in_one_tree_walk(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = JobStore(tmp_path / "jobs.sqlite3")
    with store.connect() as db, db:
        for index, status in enumerate(("succeeded", "running")):
            job_id = f"{index:032x}"
            db.execute(
                "INSERT INTO jobs(id,request_key,fingerprint,spec,status,created,updated) "
                "VALUES(?,?,?,?,?,?,?)",
                (job_id, job_id, "fp", "{}", status, 1.0, 1.0),
            )
            directory = store.output_dir / job_id
            directory.mkdir()
            (directory / "stdout.bin").write_bytes(b"x" * (index + 3))
    monkeypatch.setattr(resource_health, "SETTINGS", SimpleNamespace(state_dir=tmp_path))
    real_iterdir = Path.iterdir
    scans = 0

    def counted_iterdir(path: Path) -> Iterator[Path]:
        nonlocal scans
        if path == store.output_dir or path.parent == store.output_dir:
            scans += 1
        return real_iterdir(path)

    monkeypatch.setattr(Path, "iterdir", counted_iterdir)

    output = resource_health._job_metrics()

    assert output["job_output_files"] == 2
    assert output["job_output_bytes"] == 7
    assert output["job_history_output_bytes"] == 3
    assert scans == 3


@pytest.mark.parametrize("job_tree", [False, True])
def test_health_usage_does_not_follow_directory_symlink_cycles(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, job_tree: bool,
) -> None:
    (tmp_path / "payload.bin").write_bytes(b"abc")
    try:
        (tmp_path / "loop").symlink_to(tmp_path, target_is_directory=True)
    except (NotImplementedError, OSError):
        pytest.skip("Directory symlinks are unavailable in this test environment")
    original_iterdir = Path.iterdir
    scans = 0

    def bounded_iterdir(path: Path) -> Iterator[Path]:
        nonlocal scans
        scans += 1
        assert scans <= 2, "health accounting revisited a directory symlink"
        return original_iterdir(path)

    monkeypatch.setattr(Path, "iterdir", bounded_iterdir)
    if job_tree:
        assert resource_health._job_output_usage(tmp_path, set())[:2] == (1, 3)
    else:
        assert resource_health._files_usage(tmp_path) == (1, 3)


@pytest.mark.skipif(os.name != "nt", reason="Windows junction semantics only")
@pytest.mark.parametrize("job_tree", [False, True])
def test_health_usage_does_not_follow_windows_junctions(
    tmp_path: Path, job_tree: bool,
) -> None:
    inside = tmp_path / "inside"
    outside = tmp_path / "outside"
    inside.mkdir()
    outside.mkdir()
    (inside / "local.bin").write_bytes(b"abc")
    (outside / "outside.bin").write_bytes(b"outside")
    junction = inside / "junction"

    result = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(junction), str(outside)],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        pytest.skip(f"Could not create Windows junction: {result.stderr.strip()}")

    try:
        if job_tree:
            assert resource_health._job_output_usage(inside, set())[:2] == (1, 3)
        else:
            assert resource_health._files_usage(inside) == (1, 3)
    finally:
        junction.rmdir()


def test_resource_health_exposes_semantic_browser_counters() -> None:
    metrics = resource_health.collect_resource_metrics(
        {
            "active_sessions": 0,
            "pending_sessions": 0,
            "active_pools": 0,
            "active_contexts": 0,
            "semantic_snapshots_total": 3,
            "semantic_snapshot_ms_total": 25.5,
            "semantic_snapshot_nodes_total": 42,
            "semantic_snapshot_truncations": 1,
            "semantic_action_success": 5,
            "semantic_action_failure": 2,
            "semantic_stale_ref_failures": 1,
            "browser_download_bytes": 4096,
        }
    )

    assert metrics["browser_semantic_snapshots_total"] == 3
    assert metrics["browser_semantic_snapshot_ms_total"] == 25.5
    assert metrics["browser_semantic_snapshot_nodes_total"] == 42
    assert metrics["browser_semantic_snapshot_truncations"] == 1
    assert metrics["browser_semantic_action_success"] == 5
    assert metrics["browser_semantic_action_failure"] == 2
    assert metrics["browser_stale_ref_failures"] == 1
    assert metrics["browser_download_bytes"] == 4096


def test_lsp_health_is_bounded_counter_only(tmp_path: Path) -> None:
    from core.health_snapshot import collect_server_health
    from core.workflow_store import WorkflowStore

    class Server:
        lsp_manager = SimpleNamespace(snapshot=lambda: {
            "active": 1, "ready": 1, "quarantined": 1, "spawn_count": 2**100,
            "documents": -1, "queue_depth": "canary-private-source", "roots": ["canary-private-root"],
        })

        async def list_tools(self) -> list[object]:
            return []

    class Browser:
        async def stats(self) -> dict[str, int]:
            return {}

    store = WorkflowStore(tmp_path / "workflows.sqlite3", recover=False)
    settings = replace(Settings(), state_dir=tmp_path)
    result = asyncio.run(collect_server_health(
        Server(), settings=settings, browser_manager=Browser(),
        resource_collector=lambda _stats: {"budgets": {}, "resource_pressure": "normal"},
        recovery=SimpleNamespace(summary=lambda **_kwargs: {"uncertain_count": 0, "pending_count": 0}),
        workflow_store_factory=lambda _path: store,
    ))
    health = result["lsp"]
    assert health["enabled"] is True and health["available"] is True
    assert health["active"] == 1 and health["quarantined"] == 1
    assert 0 <= health["spawn_count"] <= 2**63 - 1
    assert health["documents"] == health["queue_depth"] == 0
    assert "canary-private" not in str(health)
    assert "lsp_ownership_unresolved" in result["degraded_reasons"]
