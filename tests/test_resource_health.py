from __future__ import annotations

import os
import subprocess
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace

import pytest

from core import resource_health
from core.jobs import JobStore


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
