from __future__ import annotations

import asyncio
import json
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
from typing import Any

import pytest
from mcp import Client
from mcp.server import MCPServer

from core.errors import ToolError
from core.jobs import MAX_JOB_WAIT_SEC, QUEUE_TIMEOUT_ERROR, JobStore, _job_wait_poll_interval
from tools.jobs import registry


def _seed(
    store: JobStore,
    job_id: str = "a" * 32,
    *,
    version: int = 1,
    status: str = "queued",
    queue_deadline: float | None = None,
) -> str:
    now = time.time()
    spec = json.dumps(
        {"command": ["fake"], "cwd": str(store.path.parent), "timeout_sec": 60.0, "encoding": "utf-8"},
        sort_keys=True,
    )
    with closing(store.connect()) as db, db:
        db.execute(
            "INSERT INTO jobs "
            "(id,request_key,fingerprint,spec,status,created,updated,version,queue_deadline) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (job_id, f"wait-{job_id}", f"fp-{job_id}", spec, status, now, now, version, queue_deadline),
        )
    directory = store.output_dir / job_id
    directory.mkdir(exist_ok=True)
    (directory / "stdout.bin").touch(exist_ok=True)
    (directory / "stderr.bin").touch(exist_ok=True)
    return job_id


def test_job_progress_returns_bounded_incremental_output(tmp_path: Path) -> None:
    store = JobStore(tmp_path / "jobs.sqlite3")
    job_id = _seed(store)
    stdout = store.output_dir / job_id / "stdout.bin"
    stderr = store.output_dir / job_id / "stderr.bin"
    stdout.write_bytes(b"alpha\nbeta\ngamma\n")
    stderr.write_bytes(b"warning\n")

    first = store.progress(job_id, max_bytes=16)

    assert first["stdout"]["text"] == "alpha\nbeta\ngamma"
    assert first["stdout"]["next_byte"] == 16
    assert first["stdout"]["has_more"] is True
    assert first["stderr"]["text"] == "warning\n"
    assert first["stderr"]["has_more"] is False

    second = store.progress(
        job_id,
        stdout_since_byte=first["stdout"]["next_byte"],
        stderr_since_byte=first["stderr"]["next_byte"],
        max_bytes=16,
    )
    assert second["stdout"]["text"] == "\n"
    assert second["stdout"]["has_more"] is False
    assert second["stderr"]["text"] == ""


def test_job_progress_missing_streams_return_empty_progress(tmp_path: Path) -> None:
    store = JobStore(tmp_path / "jobs.sqlite3")
    job_id = _seed(store)
    (store.output_dir / job_id / "stdout.bin").unlink()
    (store.output_dir / job_id / "stderr.bin").unlink()

    progress = store.progress(job_id)

    assert progress["stdout"]["missing"] is True
    assert progress["stdout"]["text"] == ""
    assert progress["stdout"]["next_byte"] == 0
    assert progress["stderr"]["missing"] is True
    with pytest.raises(ValueError, match="non-zero"):
        store.progress(job_id, stdout_since_byte=1)


def test_job_wait_returns_immediately_when_version_already_advanced(tmp_path: Path) -> None:
    store = JobStore(tmp_path / "jobs.sqlite3")
    job_id = _seed(store, version=4)

    started = time.monotonic()
    result = store.wait(job_id, after_version=3, timeout=10)

    assert time.monotonic() - started < 0.2
    assert result["changed"] is True
    assert result["timed_out"] is False
    assert result["after_version"] == 3
    assert result["version"] == 4
    assert result["status"] == "queued"


def test_job_wait_timeout_returns_current_unchanged_state(tmp_path: Path) -> None:
    store = JobStore(tmp_path / "jobs.sqlite3")
    job_id = _seed(store)

    started = time.monotonic()
    result = store.wait(job_id, after_version=1, timeout=0.08)
    elapsed = time.monotonic() - started

    assert 0.06 <= elapsed < 0.5
    assert result["changed"] is False
    assert result["timed_out"] is True
    assert result["version"] == 1
    assert result["status"] == "queued"


def test_job_wait_wakes_after_concurrent_version_change(tmp_path: Path) -> None:
    store = JobStore(tmp_path / "jobs.sqlite3")
    job_id = _seed(store)

    def update() -> None:
        time.sleep(0.08)
        store.update(job_id, cancel_requested=1)

    thread = threading.Thread(target=update)
    thread.start()
    try:
        result = store.wait(job_id, after_version=1, timeout=2)
    finally:
        thread.join(timeout=2)

    assert result["changed"] is True
    assert result["timed_out"] is False
    assert result["version"] == 2
    assert result["cancel_requested"] == 1


def test_job_wait_reconciles_queue_deadline_and_returns_new_version(tmp_path: Path) -> None:
    store = JobStore(tmp_path / "jobs.sqlite3")
    job_id = _seed(store, queue_deadline=time.time() + 0.06)

    result = store.wait(job_id, after_version=1, timeout=1)

    assert result["changed"] is True
    assert result["version"] == 2
    assert result["status"] == "timed_out"
    assert result["error"] == QUEUE_TIMEOUT_ERROR


def test_job_wait_rejects_future_version_and_invalid_bounds(tmp_path: Path) -> None:
    store = JobStore(tmp_path / "jobs.sqlite3")
    job_id = _seed(store, version=3)

    with pytest.raises(ToolError) as exc_info:
        store.wait(job_id, after_version=4, timeout=1)
    assert exc_info.value.code == "job_version_ahead"
    with pytest.raises(ValueError, match="after_version"):
        store.wait(job_id, after_version=-1, timeout=1)
    for timeout in (-0.1, MAX_JOB_WAIT_SEC + 0.1, float("inf"), float("nan")):
        with pytest.raises(ValueError, match="timeout"):
            store.wait(job_id, after_version=3, timeout=timeout)


def test_job_wait_uses_one_sqlite_connection_for_entire_wait(tmp_path: Path) -> None:
    path = tmp_path / "jobs.sqlite3"
    seed_store = JobStore(path)
    job_id = _seed(seed_store)
    opened = 0

    class CountingStore(JobStore):
        def connect(self) -> sqlite3.Connection:
            nonlocal opened
            opened += 1
            return super().connect()

    store = CountingStore(path, initialize=False)

    def update() -> None:
        time.sleep(0.07)
        seed_store.update(job_id, cancel_requested=1)

    thread = threading.Thread(target=update)
    thread.start()
    try:
        result = store.wait(job_id, after_version=1, timeout=2)
    finally:
        thread.join(timeout=2)

    assert result["version"] == 2
    assert opened == 1


def test_many_job_waiters_observe_one_authoritative_update(tmp_path: Path) -> None:
    store = JobStore(tmp_path / "jobs.sqlite3")
    job_id = _seed(store)
    ready = threading.Barrier(17)

    def waiter() -> dict[str, Any]:
        ready.wait(timeout=5)
        return store.wait(job_id, after_version=1, timeout=2)

    with ThreadPoolExecutor(max_workers=16) as pool:
        futures = [pool.submit(waiter) for _ in range(16)]
        ready.wait(timeout=5)
        time.sleep(0.05)
        store.update(job_id, cancel_requested=1)
        results = [future.result(timeout=3) for future in futures]

    assert all(result["changed"] is True for result in results)
    assert {int(result["version"]) for result in results} == {2}


def test_job_wait_poll_interval_is_adaptive_and_bounded() -> None:
    assert _job_wait_poll_interval(0) == 0.05
    assert _job_wait_poll_interval(1) == 0.1
    assert _job_wait_poll_interval(5) == 0.25
    assert _job_wait_poll_interval(30) == 0.5


def test_mcp_job_wait_returns_existing_output_immediately(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = JobStore(tmp_path / "jobs.sqlite3")
    job_id = _seed(store)
    (store.output_dir / job_id / "stdout.bin").write_bytes(b"collecting 42 items\n")
    monkeypatch.setattr(registry, "JobStore", lambda: store)
    monkeypatch.setattr(registry, "ensure_job_scheduler", lambda _: None)

    async def scenario() -> None:
        server = MCPServer("jobs-progress")
        registry.register(server)
        async with Client(server) as client:
            started = time.monotonic()
            response = await client.call_tool(
                "job_wait",
                {
                    "job_id": job_id,
                    "after_version": 1,
                    "timeout": 2,
                    "heartbeat_sec": 0.08,
                    "stdout_since_byte": 0,
                    "stderr_since_byte": 0,
                    "progress_bytes": 1024,
                },
            )
            elapsed = time.monotonic() - started

        assert response.structured_content
        result = response.structured_content
        assert elapsed < 0.5
        assert result["changed"] is False
        assert result["timed_out"] is False
        assert result["heartbeat"] is False
        assert result["progressed"] is True
        assert result["stdout"]["text"] == "collecting 42 items\n"
        assert result["stdout"]["next_byte"] == len(b"collecting 42 items\n")
        assert result["stderr"]["text"] == ""
        assert result["waited_seconds"] < 0.5

    asyncio.run(scenario())


def test_mcp_job_wait_returns_heartbeat_when_no_progress(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = JobStore(tmp_path / "jobs.sqlite3")
    job_id = _seed(store)
    monkeypatch.setattr(registry, "JobStore", lambda: store)
    monkeypatch.setattr(registry, "ensure_job_scheduler", lambda _: None)

    async def scenario() -> None:
        server = MCPServer("jobs-heartbeat")
        registry.register(server)
        async with Client(server) as client:
            started = time.monotonic()
            response = await client.call_tool(
                "job_wait",
                {
                    "job_id": job_id,
                    "after_version": 1,
                    "timeout": 2,
                    "heartbeat_sec": 0.08,
                },
            )
            elapsed = time.monotonic() - started

        assert response.structured_content
        result = response.structured_content
        assert 0.05 <= elapsed < 0.8
        assert result["changed"] is False
        assert result["timed_out"] is False
        assert result["heartbeat"] is True
        assert result["progressed"] is False

    asyncio.run(scenario())


def test_mcp_job_wait_preserves_explicit_short_timeout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = JobStore(tmp_path / "jobs.sqlite3")
    job_id = _seed(store)
    monkeypatch.setattr(registry, "JobStore", lambda: store)
    monkeypatch.setattr(registry, "ensure_job_scheduler", lambda _: None)

    async def scenario() -> None:
        server = MCPServer("jobs-timeout")
        registry.register(server)
        async with Client(server) as client:
            response = await client.call_tool(
                "job_wait",
                {
                    "job_id": job_id,
                    "after_version": 1,
                    "timeout": 0.06,
                    "heartbeat_sec": 1,
                },
            )

        assert response.structured_content
        result = response.structured_content
        assert result["changed"] is False
        assert result["timed_out"] is True
        assert result["heartbeat"] is False
        assert result["progressed"] is False

    asyncio.run(scenario())


def test_mcp_job_wait_does_not_block_state_update(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = JobStore(tmp_path / "jobs.sqlite3")
    job_id = _seed(store)
    monkeypatch.setattr(registry, "JobStore", lambda: store)
    monkeypatch.setattr(registry, "ensure_job_scheduler", lambda _: None)

    async def scenario() -> None:
        server = MCPServer("jobs-wait")
        registry.register(server)
        async with Client(server) as client:
            wait_task = asyncio.create_task(
                client.call_tool(
                    "job_wait",
                    {"job_id": job_id, "after_version": 1, "timeout": 2},
                )
            )
            await asyncio.sleep(0.08)
            store.update(job_id, cancel_requested=1)
            response = await asyncio.wait_for(wait_task, timeout=2)
        assert response.structured_content
        assert response.structured_content["changed"] is True
        assert response.structured_content["version"] == 2

    asyncio.run(scenario())
