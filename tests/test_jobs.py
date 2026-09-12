from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
from typing import Any

import pytest

from core import jobs
from core.jobs import JobStore


def seed(store: JobStore, count: int, status: str = "succeeded") -> None:
    now = time.time()
    with closing(store.connect()) as db, db:
        db.executemany(
            "INSERT INTO jobs (id,request_key,fingerprint,spec,status,created,updated) VALUES (?,?,?,?,?,?,?)",
            [(f"{i:032x}", str(i), "fingerprint", json.dumps({"encoding": "utf-8"}), status, now - 100, now)
             for i in range(count)],
        )


@pytest.mark.parametrize("status,selects", [("succeeded", 2), ("running", 3), ("queued", 3)])
def test_listing_fifty_rows_has_constant_select_count(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, status: str, selects: int,
) -> None:
    store = JobStore(tmp_path / "jobs.sqlite3")
    seed(store, 50, status)
    statements: list[str] = []
    connect = store.connect

    def traced() -> Any:
        db = connect()
        db.set_trace_callback(statements.append)
        return db

    monkeypatch.setattr(store, "connect", traced)
    result = store.list(0, 50)
    assert result["total_count"] == len(result["items"]) == 50
    assert sum(statement.startswith("SELECT") for statement in statements) == selects
    assert all(row["status"] == ("succeeded" if status == "succeeded" else "interrupted") for row in result["items"])
    assert all("spec" not in row and "fingerprint" not in row for row in result["items"])


def test_reconciliation_preserves_concurrent_worker_completion(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = JobStore(tmp_path / "jobs.sqlite3")
    seed(store, 1, "running")

    def completed(pid: int | None, created: float | None) -> bool:
        store.update("0" * 32, status="succeeded", exit_code=0)
        return False

    monkeypatch.setattr(jobs, "same_process", completed)
    result = store.list(0, 1)["items"][0]
    assert result["status"] == "succeeded"
    assert result["exit_code"] == 0


def test_store_instance_is_safe_for_independent_thread_connections(tmp_path: Path) -> None:
    store = JobStore(tmp_path / "jobs.sqlite3")
    seed(store, 60)
    with ThreadPoolExecutor(max_workers=12) as pool:
        results = list(pool.map(lambda _: store.list(10, 20), range(48)))
    assert all(result == results[0] for result in results)
    assert len(results[0]["items"]) == 20
    assert results[0]["truncated"] is True
    assert store.list(60, 20)["items"] == []


def test_output_reads_metadata_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = JobStore(tmp_path / "jobs.sqlite3")
    seed(store, 1)
    directory = store.output_dir / ("0" * 32)
    directory.mkdir()
    (directory / "stdout.bin").write_bytes(b"complete")
    (directory / "stderr.bin").write_bytes(b"")
    reads = []
    raw = store.raw

    def counted(job_id: str) -> dict[str, Any]:
        reads.append(job_id)
        return raw(job_id)

    monkeypatch.setattr(store, "raw", counted)
    assert store.output("0" * 32)["stdout"]["text"] == "complete"
    assert reads == ["0" * 32]
