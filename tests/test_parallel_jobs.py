"""Regression checks for resource-aware durable-job concurrency and batch APIs."""

from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

import pytest
from mcp import Client
from mcp.server import MCPServer

from core.jobs import JobStore
from core.resource_claims import path_claim
from tools.jobs.registry import register


def _await_jobs(store: JobStore, ids: list[str], *, timeout: float = 20.0) -> list[dict[str, object]]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        rows = [store.get(job_id) for job_id in ids]
        if all(row["status"] not in {"running", "queued", "orphaned"} for row in rows):
            return rows
        time.sleep(0.05)
    raise AssertionError(f"jobs did not terminate: {rows!r}")


def test_declared_independent_jobs_run_concurrently_in_same_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MCP_MAX_RUNNING_JOBS", "4")
    store = JobStore(tmp_path / "jobs.sqlite3")
    root = tmp_path / "workspace"
    root.mkdir()
    ids: list[str] = []
    for index in range(4):
        marker = root / f"started-{index}"
        code = (
            "import sys,time; from pathlib import Path; "
            "root=Path(sys.argv[1]); marker=Path(sys.argv[2]); "
            "marker.write_text('started'); "
            "deadline=time.monotonic()+10; "
            "exec('while len(list(root.glob(\"started-*\")))<4 and time.monotonic()<deadline:\\n time.sleep(.03)'); "
            "sys.exit(0 if len(list(root.glob('started-*')))==4 else 5)"
        )
        result = store.submit(
            [sys.executable, "-c", code, str(root), str(marker)],
            root, 15, f"parallel-{index}",
            parallel_resources=(path_claim(marker, "write"),),
        )
        ids.append(str(result["job_id"]))
    rows = _await_jobs(store, ids)
    assert [row["status"] for row in rows] == ["succeeded"] * 4


def test_parallel_claims_must_be_explicit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from core import job_scheduler

    monkeypatch.setattr(job_scheduler, "ensure_job_scheduler", lambda store: None)
    store = JobStore(tmp_path / "jobs.sqlite3")
    with pytest.raises(ValueError, match="resource"):
        store.submit(["python", "-V"], tmp_path, 5, "empty-claims", parallel_resources=())
    result = store.submit(["python", "-V"], tmp_path, 5, "default-exclusive")
    assert result["status"] == "queued"


def test_batch_api_returns_independent_ids_and_progress(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from core import job_scheduler

    monkeypatch.setattr(job_scheduler, "ensure_job_scheduler", lambda store: None)
    store = JobStore(tmp_path / "jobs.sqlite3")

    async def scenario() -> None:
        server = MCPServer("parallel-jobs")
        register(server, store_getter=lambda: store)
        async with Client(server) as client:
            result = await client.call_tool(
                "submit_jobs", {
                    "jobs": [
                        {"executable": "python", "args": ["-V"], "cwd": str(tmp_path),
                         "idempotency_key": "batch-a"},
                        {"executable": "python", "args": ["-V"], "cwd": str(tmp_path),
                         "idempotency_key": "batch-b"},
                    ],
                },
            )
            assert result.structured_content is not None
            data = result.structured_content
            assert data["ok"] is True
            ids = [entry["job_id"] for entry in data["items"]]
            assert len(set(ids)) == 2
            (store.output_dir / ids[0] / "stdout.bin").write_bytes(b"alpha-job")
            (store.output_dir / ids[1] / "stdout.bin").write_bytes(b"beta-job")
            (store.output_dir / ids[1] / "stderr.bin").write_bytes(b"beta-warning")
            snapshot = await client.call_tool(
                "jobs_snapshot", {"jobs": [{"job_id": job_id} for job_id in ids]},
            )
            assert snapshot.structured_content is not None
            items = snapshot.structured_content["items"]
            assert [entry["job_id"] for entry in items] == ids
            assert all(entry["status"] == "queued" for entry in items)
            assert all("stdout" in entry and "stderr" in entry for entry in items)
            assert [entry["stdout"]["text"] for entry in items] == ["alpha-job", "beta-job"]
            assert items[0]["stderr"]["text"] == ""
            assert items[1]["stderr"]["text"] == "beta-warning"
            assert items[1]["stdout"]["next_byte"] == len(b"beta-job")
            again = await client.call_tool(
                "submit_jobs", {
                    "jobs": [
                        {"executable": "python", "args": ["-V"], "cwd": str(tmp_path),
                         "idempotency_key": "batch-a"},
                        {"executable": "python", "args": ["-V"], "cwd": str(tmp_path),
                         "idempotency_key": "batch-b"},
                    ],
                },
            )
            assert again.structured_content is not None
            assert [entry["job_id"] for entry in again.structured_content["items"]] == ids
            assert all(entry["deduplicated"] for entry in again.structured_content["items"])

    asyncio.run(scenario())


def test_conflicting_parallel_jobs_do_not_overlap(tmp_path: Path) -> None:
    store = JobStore(tmp_path / "jobs.sqlite3")
    working = tmp_path / "workspace"
    working.mkdir()
    shared = working / "shared-resource"
    ids: list[str] = []
    for index in range(2):
        result_file = working / f"result-{index}"
        code = (
            "import sys,time; from pathlib import Path; "
            "start=time.monotonic(); time.sleep(.55); "
            "Path(sys.argv[1]).write_text(str(start)+','+str(time.monotonic()))"
        )
        entry = store.submit(
            [sys.executable, "-c", code, str(result_file)],
            working, 20, f"conflicting-{index}",
            parallel_resources=(path_claim(shared, "write"), path_claim(result_file, "write")),
        )
        ids.append(str(entry["job_id"]))
    rows = _await_jobs(store, ids)
    assert all(row["status"] == "succeeded" for row in rows)
    intervals = [tuple(map(float, (working / f"result-{index}").read_text().split(","))) for index in range(2)]
    assert intervals[0][1] <= intervals[1][0] or intervals[1][1] <= intervals[0][0]


def test_cancel_while_waiting_for_exclusive_resource(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from core.resource_claims import RESOURCE_CAPABILITY_ENV, managed_resource_claims
    from core.workflow_store import WorkflowStore

    monkeypatch.delenv(RESOURCE_CAPABILITY_ENV, raising=False)
    store = JobStore(tmp_path / "jobs.sqlite3")
    working = tmp_path / "workspace"
    working.mkdir()
    marker = working / "blocked-output"
    authority = WorkflowStore(tmp_path / "workflows.sqlite3", recover=False)
    # This external resource holder is not a Job, so the scheduler cannot
    # pre-filter it. The worker must notice cancellation while waiting.
    with managed_resource_claims(
        (path_claim(marker, "write"),),
        category="test.external_holder",
        store=authority,
        timeout_sec=10,
    ):
        blocked = store.submit(
            [sys.executable, "-c", "from pathlib import Path; import sys; Path(sys.argv[1]).touch()", str(marker)],
            working, 20, "blocked-then-cancelled",
            parallel_resources=(path_claim(marker, "write"),),
        )
        blocked_id = str(blocked["job_id"])
        deadline = time.monotonic() + 6
        while store.get(blocked_id)["status"] == "queued" and time.monotonic() < deadline:
            time.sleep(.05)
        assert store.get(blocked_id)["status"] == "running"
        began = time.monotonic()
        store.cancel(blocked_id)
        result = _await_jobs(store, [blocked_id], timeout=3)[0]
        assert result["status"] == "cancelled"
        assert time.monotonic() - began < 3
        assert not marker.exists()


def test_scheduler_skips_resource_blockers_and_fills_independent_slots(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from core import job_scheduler

    monkeypatch.setattr(job_scheduler, "ensure_job_scheduler", lambda store: None)
    store = JobStore(tmp_path / "jobs.sqlite3")
    shared = tmp_path / "shared"
    distinct = tmp_path / "distinct"
    ids = []
    for index, claim in enumerate((shared, shared, distinct)):
        result = store.submit(
            [sys.executable, "-V"], tmp_path, 15, f"schedule-{index}",
            parallel_resources=(path_claim(claim, "write"),),
        )
        ids.append(str(result["job_id"]))
    reservations, queued = store.reserve_worker_launches(max_running=2)
    assert [job_id for job_id, _ in reservations] == [ids[0], ids[2]]
    assert queued == 3
    assert store.raw(ids[1])["launch_token"] is None


def test_cancel_one_parallel_job_leaves_sibling_running(tmp_path: Path) -> None:
    store = JobStore(tmp_path / "jobs.sqlite3")
    working = tmp_path / "workspace"
    working.mkdir()
    slow_marker = working / "slow-started"
    fast_marker = working / "fast-result"
    slow = store.submit(
        [sys.executable, "-c",
         "from pathlib import Path; import sys,time; Path(sys.argv[1]).touch(); time.sleep(6)",
         str(slow_marker)],
        working, 15, "cancel-one-slow",
        parallel_resources=(path_claim(slow_marker, "write"),),
    )
    fast = store.submit(
        [sys.executable, "-c",
         "from pathlib import Path; import sys,time; time.sleep(.5); Path(sys.argv[1]).write_text('done')",
         str(fast_marker)],
        working, 15, "cancel-one-fast",
        parallel_resources=(path_claim(fast_marker, "write"),),
    )
    slow_id, fast_id = str(slow["job_id"]), str(fast["job_id"])
    try:
        deadline = time.monotonic() + 8
        while not slow_marker.exists() and time.monotonic() < deadline:
            time.sleep(.05)
        assert slow_marker.exists()
        store.cancel(slow_id)
        rows = _await_jobs(store, [slow_id, fast_id], timeout=12)
        assert [row["status"] for row in rows] == ["cancelled", "succeeded"]
        assert fast_marker.read_text() == "done"
    finally:
        if store.get(slow_id)["status"] in {"running", "queued", "orphaned"}:
            store.cancel(slow_id)
        if store.get(fast_id)["status"] in {"running", "queued", "orphaned"}:
            store.cancel(fast_id)


def test_scheduler_finds_independent_job_behind_large_conflicting_prefix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from core import job_scheduler

    monkeypatch.setattr(job_scheduler, "ensure_job_scheduler", lambda _store: None)
    store = JobStore(tmp_path / "jobs.sqlite3")
    shared = path_claim(tmp_path / "shared", "write")
    independent = path_claim(tmp_path / "independent", "write")
    first_id = ""
    for index in range(195):
        submitted = store.submit(
            [sys.executable, "-V"], tmp_path, 10, f"blocked-prefix-{index}",
            parallel_resources=(shared,),
        )
        if index == 0:
            first_id = str(submitted["job_id"])
    last_id = str(store.submit(
        [sys.executable, "-V"], tmp_path, 10, "tail-independent",
        parallel_resources=(independent,),
    )["job_id"])
    reservations, _ = store.reserve_worker_launches(max_running=2)
    assert [job_id for job_id, _ in reservations] == [first_id, last_id]


def test_mcp_batch_four_real_jobs_have_independent_streams(tmp_path: Path) -> None:
    store = JobStore(tmp_path / "jobs.sqlite3")
    working = tmp_path / "workspace"
    working.mkdir()
    command = (
        "import sys,time; from pathlib import Path; "
        "marker=Path(sys.argv[1]); marker.touch(); "
        "deadline=time.monotonic()+12; "
        "exec('while len(list(marker.parent.glob(\"batch-start-*\")))<4 and time.monotonic()<deadline:\\n time.sleep(.03)'); "
        "count=len(list(marker.parent.glob('batch-start-*'))); "
        "print('stdout:'+marker.name,flush=True); "
        "print('stderr:'+marker.name,file=sys.stderr,flush=True); "
        "sys.exit(0 if count==4 else 7)"
    )

    async def scenario() -> None:
        server = MCPServer("batch-live")
        register(server, store_getter=lambda: store)
        jobs = [
            {
                "executable": sys.executable,
                "args": ["-c", command, str(working / f"batch-start-{index}")],
                "cwd": str(working), "idempotency_key": f"live-batch-{index}",
                "execution_mode": "declared",
                "resources": [{"path": f"batch-start-{index}", "mode": "write"}],
            }
            for index in range(4)
        ]
        async with Client(server) as client:
            submitted = await client.call_tool("submit_jobs", {"jobs": jobs})
            assert submitted.structured_content is not None
            assert submitted.structured_content["accepted"] == 4
            ids = [entry["job_id"] for entry in submitted.structured_content["items"]]
            assert len(set(ids)) == 4
            assert all(item["status"] == "succeeded" for item in _await_jobs(store, ids, timeout=24))
            result = await client.call_tool("jobs_snapshot", {
                "jobs": [{"job_id": job_id} for job_id in ids],
            })
            assert result.structured_content is not None
            rows = result.structured_content["items"]
            assert result.structured_content["all_terminal"] is True
            for index, row in enumerate(rows):
                assert row["stdout"]["text"].strip() == f"stdout:batch-start-{index}"
                assert row["stderr"]["text"].strip() == f"stderr:batch-start-{index}"
                assert row["stdout"]["final"] is True
                assert row["stderr"]["final"] is True

    asyncio.run(scenario())


def test_batch_snapshot_has_bounded_aggregate_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from core import job_scheduler

    monkeypatch.setattr(job_scheduler, "ensure_job_scheduler", lambda _store: None)
    store = JobStore(tmp_path / "jobs.sqlite3")
    ids = []
    for index in range(16):
        item = store.submit([sys.executable, "-V"], tmp_path, 10, f"bounded-batch-{index}")
        job_id = str(item["job_id"])
        ids.append(job_id)
        directory = store.output_dir / job_id
        (directory / "stdout.bin").write_bytes(b"a" * 65536)
        (directory / "stderr.bin").write_bytes(b"b" * 65536)

    async def scenario() -> None:
        server = MCPServer("bounded-snapshot")
        register(server, store_getter=lambda: store)
        async with Client(server) as client:
            response = await client.call_tool("jobs_snapshot", {
                "jobs": [{"job_id": job_id} for job_id in ids],
                "progress_bytes": 65536,
            })
        assert response.structured_content is not None
        data = response.structured_content
        assert data["ok"] is True
        total = sum(row["stdout"]["bytes"] + row["stderr"]["bytes"] for row in data["items"])
        assert total <= 128 * 1024
        assert all(row["stdout"]["has_more"] for row in data["items"])
        assert all(row["stderr"]["has_more"] for row in data["items"])

    asyncio.run(scenario())



def test_default_exclusive_jobs_remain_serial_across_working_directories(tmp_path: Path) -> None:
    store = JobStore(tmp_path / "jobs.sqlite3")
    ids: list[str] = []
    results: list[Path] = []
    for index in range(2):
        cwd = tmp_path / f"working-{index}"
        cwd.mkdir()
        result_file = cwd / "interval.txt"
        results.append(result_file)
        code = (
            "import sys,time; from pathlib import Path; "
            "start=time.monotonic(); time.sleep(.25); "
            "Path(sys.argv[1]).write_text(str(start)+','+str(time.monotonic()))"
        )
        ids.append(str(store.submit(
            [sys.executable, "-c", code, str(result_file)],
            cwd, 12, f"exclusive-default-{index}",
        )["job_id"]))
    assert all(row["status"] == "succeeded" for row in _await_jobs(store, ids))
    intervals = [tuple(map(float, item.read_text().split(","))) for item in results]
    assert intervals[0][1] <= intervals[1][0] or intervals[1][1] <= intervals[0][0]


def test_parallel_resource_order_is_idempotent_but_resource_changes_conflict(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from core import job_scheduler
    from core.errors import ToolError

    monkeypatch.setattr(job_scheduler, "ensure_job_scheduler", lambda _store: None)
    store = JobStore(tmp_path / "jobs.sqlite3")
    a = path_claim(tmp_path / "a", "write")
    b = path_claim(tmp_path / "b", "read")
    first = store.submit(
        [sys.executable, "-V"], tmp_path, 12, "canonical-resource-key",
        parallel_resources=(a, b),
    )
    repeated = store.submit(
        [sys.executable, "-V"], tmp_path, 12, "canonical-resource-key",
        parallel_resources=(b, a),
    )
    assert repeated["job_id"] == first["job_id"]
    assert repeated["deduplicated"] is True
    with pytest.raises(ToolError, match="different command") as exc:
        store.submit(
            [sys.executable, "-V"], tmp_path, 12, "canonical-resource-key",
            parallel_resources=(a,),
        )
    assert exc.value.code == "idempotency_conflict"


def test_batch_partial_failure_does_not_lose_successful_job(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from core import job_scheduler

    monkeypatch.setattr(job_scheduler, "ensure_job_scheduler", lambda _store: None)
    store = JobStore(tmp_path / "jobs.sqlite3")

    async def scenario() -> None:
        server = MCPServer("partial-batch")
        register(server, store_getter=lambda: store)
        async with Client(server) as client:
            result = await client.call_tool("submit_jobs", {"jobs": [
                {"executable": sys.executable, "cwd": str(tmp_path), "idempotency_key": "partial-good"},
                {"executable": sys.executable, "cwd": str(tmp_path / "missing"),
                 "idempotency_key": "partial-bad"},
            ]})
        assert result.structured_content is not None
        data = result.structured_content
        assert data["accepted"] == 1 and data["failed"] == 1
        assert data["ok"] is False
        assert data["items"][0]["ok"] is True
        assert data["items"][1]["ok"] is False
        assert store.get(data["items"][0]["job_id"])["status"] == "queued"

    asyncio.run(scenario())


def test_snapshot_independent_cursors_do_not_repeat_data(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from core import job_scheduler

    monkeypatch.setattr(job_scheduler, "ensure_job_scheduler", lambda _store: None)
    store = JobStore(tmp_path / "jobs.sqlite3")
    first = store.submit([sys.executable, "-V"], tmp_path, 10, "cursor-first")
    second = store.submit([sys.executable, "-V"], tmp_path, 10, "cursor-second")
    ids = [str(first["job_id"]), str(second["job_id"])]
    (store.output_dir / ids[0] / "stdout.bin").write_bytes(b"111111")
    (store.output_dir / ids[1] / "stdout.bin").write_bytes(b"222222")

    async def scenario() -> None:
        server = MCPServer("cursor-batch")
        register(server, store_getter=lambda: store)
        async with Client(server) as client:
            first = await client.call_tool("jobs_snapshot", {
                "jobs": [{"job_id": job_id} for job_id in ids], "progress_bytes": 16,
            })
            assert first.structured_content is not None
            assert first.structured_content["ok"] is True
            cursors = [row["stdout"]["next_byte"] for row in first.structured_content["items"]]
            assert cursors == [6, 6]
            for job_id, suffix in zip(ids, (b"AA", b"BB"), strict=True):
                with (store.output_dir / job_id / "stdout.bin").open("ab") as stream:
                    stream.write(suffix)
            followup = await client.call_tool("jobs_snapshot", {
                "jobs": [
                    {"job_id": job_id, "stdout_since_byte": cursor}
                    for job_id, cursor in zip(ids, cursors, strict=True)
                ],
            })
            assert followup.structured_content is not None
            assert [row["stdout"]["text"] for row in followup.structured_content["items"]] == ["AA", "BB"]

    asyncio.run(scenario())



def test_completed_job_state_and_streams_survive_store_reopen(tmp_path: Path) -> None:
    store = JobStore(tmp_path / "jobs.sqlite3")
    working = tmp_path / "workspace"
    working.mkdir()
    first = store.submit(
        [sys.executable, "-c", "import sys; print('persist-stdout'); print('persist-stderr',file=sys.stderr)"],
        working, 15, "persist-parallel-result",
        parallel_resources=(path_claim(working / "stream-marker", "write"),),
    )
    job_id = str(first["job_id"])
    assert _await_jobs(store, [job_id])[0]["status"] == "succeeded"
    reopened = JobStore(tmp_path / "jobs.sqlite3", initialize=False)
    assert reopened.get(job_id)["status"] == "succeeded"
    progress = reopened.progress(job_id)
    assert progress["stdout"]["text"].strip() == "persist-stdout"
    assert progress["stderr"]["text"].strip() == "persist-stderr"
    assert progress["stdout"]["final"] is True
    assert progress["stderr"]["final"] is True


def test_failed_parallel_command_does_not_cancel_siblings(tmp_path: Path) -> None:
    store = JobStore(tmp_path / "jobs.sqlite3")
    working = tmp_path / "workspace"
    working.mkdir()
    entries = [
        (path_claim(working / f"resource-{i}", "write"),
         [sys.executable, "-c",
          "import sys,time; time.sleep(.3); print('job:'+sys.argv[1]); sys.exit(int(sys.argv[2]))",
          str(i), "17" if i == 1 else "0"])
        for i in range(4)
    ]
    ids = [
        str(store.submit(command, working, 12, f"failure-isolation-{i}", parallel_resources=(claim,))["job_id"])
        for i, (claim, command) in enumerate(entries)
    ]
    rows = _await_jobs(store, ids)
    assert [row["status"] for row in rows] == ["succeeded", "failed", "succeeded", "succeeded"]
    assert rows[1]["exit_code"] == 17
    for i, job_id in enumerate(ids):
        output = store.progress(job_id)
        assert output["stdout"]["text"].strip() == f"job:{i}"


def test_cancel_resource_conflict_while_queued_frees_independent_work(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from core import job_scheduler

    monkeypatch.setattr(job_scheduler, "ensure_job_scheduler", lambda _store: None)
    store = JobStore(tmp_path / "jobs.sqlite3")
    shared = path_claim(tmp_path / "shared", "write")
    a = store.submit([sys.executable, "-V"], tmp_path, 10, "queued-a", parallel_resources=(shared,))
    b = store.submit([sys.executable, "-V"], tmp_path, 10, "queued-b", parallel_resources=(shared,))
    c = store.submit(
        [sys.executable, "-V"], tmp_path, 10, "queued-c",
        parallel_resources=(path_claim(tmp_path / "other", "write"),),
    )
    reservations, _ = store.reserve_worker_launches(max_running=2)
    assert [job_id for job_id, _ in reservations] == [a["job_id"], c["job_id"]]
    cancelled = store.cancel(str(b["job_id"]))
    assert cancelled["status"] == "cancelled"
    assert store.get(str(a["job_id"]))["status"] == "queued"
    assert store.get(str(c["job_id"]))["status"] == "queued"



def test_batch_snapshot_distinguishes_resource_waiting_from_ready_queue(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from core import job_scheduler

    monkeypatch.setattr(job_scheduler, "ensure_job_scheduler", lambda _store: None)
    store = JobStore(tmp_path / "jobs.sqlite3")
    shared = path_claim(tmp_path / "shared", "write")
    independent = path_claim(tmp_path / "independent", "write")
    first = store.submit([sys.executable, "-V"], tmp_path, 10, "waiting-first", parallel_resources=(shared,))
    blocked = store.submit([sys.executable, "-V"], tmp_path, 10, "waiting-blocked", parallel_resources=(shared,))
    ready = store.submit([sys.executable, "-V"], tmp_path, 10, "waiting-independent", parallel_resources=(independent,))
    reservations, _ = store.reserve_worker_launches(max_running=1)
    assert [item[0] for item in reservations] == [first["job_id"]]

    async def scenario() -> None:
        server = MCPServer("queue-status")
        register(server, store_getter=lambda: store)
        async with Client(server) as client:
            snapshots = await client.call_tool("jobs_snapshot", {
                "jobs": [
                    {"job_id": blocked["job_id"]},
                    {"job_id": ready["job_id"]},
                ],
            })
            assert snapshots.structured_content is not None
            rows = snapshots.structured_content["items"]
            assert [row["status"] for row in rows] == ["queued", "queued"]
            assert [row["queue_phase"] for row in rows] == ["waiting_resources", "awaiting_worker"]
            status = await client.call_tool("job_status", {"job_id": blocked["job_id"]})
            assert status.structured_content is not None
            assert status.structured_content["queue_phase"] == "waiting_resources"

    asyncio.run(scenario())
