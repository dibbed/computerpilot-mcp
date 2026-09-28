from __future__ import annotations

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
