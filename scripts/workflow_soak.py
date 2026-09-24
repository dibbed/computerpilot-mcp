"""Deterministic local soak gate for durable workflow correctness."""

from __future__ import annotations

import argparse
import gc
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import psutil

from core.jobs import JobStore
from core.workflow_models import OperationState
from core.workflow_reconciliation import reconcile_operation
from core.workflow_retention import WorkflowHistoryPolicy, cleanup_workflow_history
from core.workflows import StepDefinition, WorkflowDefinition, WorkflowExecutor, WorkflowState, WorkflowStore

PROJECT_ROOT = Path(__file__).resolve().parent.parent
_TERMINAL_JOBS = {"succeeded", "failed", "cancelled", "timed_out", "interrupted"}


def _storage_breakdown(database: Path) -> dict[str, int]:
    paths = {
        "main": database,
        "wal": Path(f"{database}-wal"),
        "shm": Path(f"{database}-shm"),
    }
    sizes: dict[str, int] = {}
    for name, path in paths.items():
        try:
            sizes[name] = path.stat().st_size if path.is_file() else 0
        except OSError:
            sizes[name] = 0
    sizes["total"] = sum(sizes.values())
    return sizes


def _storage_bytes(database: Path) -> int:
    return _storage_breakdown(database)["total"]


def _expire_lease(database: Path, workflow_id: str) -> None:
    with sqlite3.connect(database) as connection:
        connection.execute(
            "UPDATE workflow_leases SET expires_at = '2000-01-01T00:00:00.000+00:00' WHERE workflow_id = ?",
            (workflow_id,),
        )


def _process_reopen(database: Path) -> int:
    code = (
        "import sys; from pathlib import Path; "
        "from core.workflows import WorkflowStore; "
        "print(WorkflowStore(Path(sys.argv[1])).health_summary()['workflow_total'])"
    )
    result = subprocess.run(
        [sys.executable, "-c", code, str(database)],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "workflow reopen subprocess failed")
    return int(result.stdout.strip().splitlines()[-1])


def _process_mcp_restart(state_root: Path) -> dict[str, Any]:
    state_root.mkdir(parents=True, exist_ok=True)
    code = """
import asyncio
import json
import sys
from dataclasses import replace
from pathlib import Path

import core.config as config

state_root = Path(sys.argv[1])
settings = replace(config.SETTINGS, state_dir=state_root, memory_dir=state_root / "memory", tool_profile="full")
config.SETTINGS = settings

import core.heartbeat as heartbeat
import core.resource_health as resource_health

heartbeat.SETTINGS = settings
resource_health.SETTINGS = settings

import core.registry as registry
from mcp import Client

registry.SETTINGS = settings

async def run():
    async with Client(registry.create_server()) as client:
        response = await client.call_tool("server_health", {})
        if response.is_error:
            raise RuntimeError("server_health failed during soak restart")
        payload = response.structured_content or {}
        print(json.dumps({
            "tool_count": payload.get("tool_count"),
            "unique_tool_names": payload.get("unique_tool_names"),
            "health_status": payload.get("health_status"),
            "workflow_total": payload.get("workflow_total"),
        }, separators=(",", ":")))

asyncio.run(run())
"""
    result = subprocess.run(
        [sys.executable, "-c", code, str(state_root)],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        timeout=90,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "MCP restart smoke failed")
    try:
        payload = json.loads(result.stdout.strip().splitlines()[-1])
    except (IndexError, json.JSONDecodeError) as exc:
        raise RuntimeError("MCP restart smoke returned invalid output") from exc
    tool_count = payload.get("tool_count")
    if not isinstance(tool_count, int) or tool_count <= 0:
        raise RuntimeError(f"MCP restart returned invalid tool count: {tool_count!r}")
    if payload.get("unique_tool_names") is not True:
        raise RuntimeError("MCP restart registered duplicate tool names")
    if os.name == "nt" and tool_count != 112:
        raise RuntimeError(f"MCP restart registered {tool_count} tools instead of 112 on Windows")
    return payload


def _run_jobs(root: Path, count: int) -> dict[str, Any]:
    if count <= 0:
        return {"submitted": 0, "succeeded": 0, "active": 0}
    store = JobStore(root / "jobs.sqlite3")
    jobs: list[dict[str, Any]] = []
    for index in range(count):
        jobs.append(
            store.submit(
                [sys.executable, "-c", "print('workflow-soak-job')"],
                root,
                30,
                f"workflow-soak-{index}",
                "utf-8",
            )
        )
    succeeded = 0
    worker_pids: set[int] = set()
    for job in jobs:
        current = job
        job_id = str(current["job_id"])
        while str(current["status"]) not in _TERMINAL_JOBS:
            current = store.wait(job_id, int(current["version"]), 5)
            pid = current.get("pid")
            if isinstance(pid, int) and pid > 0:
                worker_pids.add(pid)
        if current["status"] == "succeeded":
            succeeded += 1
    processes = []
    for pid in worker_pids:
        try:
            processes.append(psutil.Process(pid))
        except psutil.NoSuchProcess:
            continue
    if processes:
        psutil.wait_procs(processes, timeout=5)
    with sqlite3.connect(store.path) as connection:
        active = int(
            connection.execute(
                "SELECT COUNT(*) FROM jobs WHERE status IN ('queued','running','orphaned')"
            ).fetchone()[0]
        )
    return {"submitted": count, "succeeded": succeeded, "active": active}


def run_soak(
    root: Path,
    *,
    workflows: int = 300,
    failures: int = 30,
    uncertain: int = 10,
    restarts: int = 3,
    durable_jobs: int = 5,
) -> dict[str, Any]:
    if workflows < 10:
        raise ValueError("workflows must be at least 10")
    if failures < 0 or uncertain < 0 or failures + uncertain >= workflows:
        raise ValueError("failures and uncertain counts must leave at least one successful workflow")
    if restarts < 1:
        raise ValueError("restarts must be positive")

    root.mkdir(parents=True, exist_ok=True)
    database = root / "workflows.sqlite3"
    for path in (database, Path(f"{database}-wal"), Path(f"{database}-shm")):
        path.unlink(missing_ok=True)

    process = psutil.Process()
    rss_before = process.memory_info().rss
    store = WorkflowStore(database)
    success_target = root / "ready.txt"
    success_target.write_text("ready\n", encoding="utf-8")
    success_count = workflows - failures - uncertain

    success_definition = WorkflowDefinition(
        "soak-success",
        (StepDefinition("check", "check_file", {"path": str(success_target)}),),
    )
    for index in range(success_count):
        created = store.create(
            success_definition,
            idempotency_key=f"soak-success-{index}",
            initial_state=WorkflowState.QUEUED,
        )
        result = WorkflowExecutor(store).execute(
            str(created["workflow_id"]),
            owner_id=f"soak-success-{index}",
        )
        if result["state"] != "completed":
            raise RuntimeError(f"success workflow ended as {result['state']}")

    for index in range(failures):
        missing = root / f"missing-{index}.txt"
        created = store.create(
            WorkflowDefinition(
                "soak-failure",
                (StepDefinition("check", "check_file", {"path": str(missing), "exists": True}),),
            ),
            idempotency_key=f"soak-failure-{index}",
            initial_state=WorkflowState.QUEUED,
        )
        result = WorkflowExecutor(store).execute(
            str(created["workflow_id"]),
            owner_id=f"soak-failure-{index}",
        )
        if result["state"] != "failed":
            raise RuntimeError(f"failure workflow ended as {result['state']}")

    reconciled = 0
    for index in range(uncertain):
        created = store.create(
            WorkflowDefinition(
                "soak-uncertain",
                (
                    StepDefinition(
                        "check",
                        "check_file",
                        {"path": str(success_target)},
                        postcondition={
                            "kind": "file_exists",
                            "expected": {"path": str(success_target)},
                        },
                    ),
                ),
            ),
            idempotency_key=f"soak-uncertain-{index}",
            initial_state=WorkflowState.QUEUED,
        )
        workflow_id = str(created["workflow_id"])
        lease = store.acquire_lease(workflow_id, f"soak-crash-{index}", 30)
        running = store.transition(
            workflow_id,
            int(created["version"]),
            WorkflowState.RUNNING,
            lease_token=lease.lease_token,
        )
        operation = store.list_operations(workflow_id)["items"][0]
        store.checkpoint_operation(
            str(operation["operation_id"]),
            OperationState.RUNNING,
            lease_token=lease.lease_token,
            increment_attempt=True,
        )
        store.checkpoint_step(
            workflow_id,
            0,
            "running",
            increment_attempt=True,
            lease_token=lease.lease_token,
        )
        if running["state"] != "running":
            raise RuntimeError("failed to establish crash-window workflow")
        _expire_lease(database, workflow_id)
        store = WorkflowStore(database)
        operation = store.list_operations(workflow_id)["items"][0]
        if operation["state"] != "uncertain":
            raise RuntimeError("restart did not convert interrupted operation to uncertain")
        resolved = reconcile_operation(
            store,
            str(operation["operation_id"]),
            expected_version=int(operation["version"]),
        )
        if resolved["workflow"]["state"] != "completed":
            raise RuntimeError("uncertain workflow did not reconcile to completed")
        reconciled += 1

    reopen_counts = [_process_reopen(database) for _ in range(restarts)]
    mcp_restart_checks = [
        _process_mcp_restart(root / f"mcp-restart-{index}")
        for index in range(restarts)
    ]
    store = WorkflowStore(database)
    before_cleanup = store.health_summary()
    storage_before_cleanup = _storage_breakdown(database)
    bytes_before_cleanup = storage_before_cleanup["total"]
    cleanup = cleanup_workflow_history(
        database,
        WorkflowHistoryPolicy(
            max_age_days=0,
            max_count=max(50, workflows // 3),
            cleanup_interval_sec=1,
        ),
        now=datetime.now(timezone.utc),
    )
    store = WorkflowStore(database)
    after_cleanup = store.health_summary()
    with sqlite3.connect(database) as connection:
        leaked_leases = int(connection.execute("SELECT COUNT(*) FROM workflow_leases").fetchone()[0])
        orphan_operations = int(
            connection.execute(
                "SELECT COUNT(*) FROM workflow_operations "
                "WHERE workflow_id NOT IN (SELECT workflow_id FROM workflows)"
            ).fetchone()[0]
        )
        orphan_events = int(
            connection.execute(
                "SELECT COUNT(*) FROM workflow_events "
                "WHERE workflow_id NOT IN (SELECT workflow_id FROM workflows)"
            ).fetchone()[0]
        )
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    job_result = _run_jobs(root / "jobs", durable_jobs)
    gc.collect()
    rss_after = process.memory_info().rss
    rss_growth = max(rss_after - rss_before, 0)
    storage_after_cleanup = _storage_breakdown(database)
    bytes_after_cleanup = storage_after_cleanup["total"]

    if before_cleanup["unresolved_operation_count"] != 0:
        raise RuntimeError("soak left unresolved workflow operations")
    if leaked_leases != 0:
        raise RuntimeError(f"soak leaked {leaked_leases} workflow leases")
    if orphan_operations or orphan_events:
        raise RuntimeError("soak created orphan workflow rows")
    if job_result["active"] != 0 or job_result["succeeded"] != job_result["submitted"]:
        raise RuntimeError("soak left active/failed durable jobs")
    if rss_growth > 256 * 1_048_576:
        raise RuntimeError("soak RSS growth exceeded the 256 MiB safety gate")

    return {
        "ok": True,
        "workflows_requested": workflows,
        "completed": success_count + reconciled,
        "failed": failures,
        "uncertain_reconciled": reconciled,
        "process_restarts": restarts,
        "reopen_counts": reopen_counts,
        "mcp_restart_checks": mcp_restart_checks,
        "cleanup": cleanup.to_dict(),
        "workflow_total_before_cleanup": before_cleanup["workflow_total"],
        "workflow_total_after_cleanup": after_cleanup["workflow_total"],
        "workflow_db_bytes_before_cleanup": bytes_before_cleanup,
        "workflow_db_bytes_after_cleanup": bytes_after_cleanup,
        "workflow_main_db_bytes_before_cleanup": storage_before_cleanup["main"],
        "workflow_wal_bytes_before_cleanup": storage_before_cleanup["wal"],
        "workflow_shm_bytes_before_cleanup": storage_before_cleanup["shm"],
        "workflow_main_db_bytes_after_cleanup": storage_after_cleanup["main"],
        "workflow_wal_bytes_after_cleanup": storage_after_cleanup["wal"],
        "workflow_shm_bytes_after_cleanup": storage_after_cleanup["shm"],
        "workflow_wal_bytes_delta": storage_after_cleanup["wal"] - storage_before_cleanup["wal"],
        "workflow_db_bytes_per_created_workflow": round(bytes_before_cleanup / workflows, 3),
        "leaked_leases": leaked_leases,
        "orphan_operations": orphan_operations,
        "orphan_events": orphan_events,
        "durable_jobs": job_result,
        "rss_before_mb": round(rss_before / 1_048_576, 3),
        "rss_after_mb": round(rss_after / 1_048_576, 3),
        "rss_growth_mb": round(rss_growth / 1_048_576, 3),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=None)
    parser.add_argument("--workflows", type=int, default=300)
    parser.add_argument("--failures", type=int, default=30)
    parser.add_argument("--uncertain", type=int, default=10)
    parser.add_argument("--restarts", type=int, default=3)
    parser.add_argument("--durable-jobs", type=int, default=5)
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.root is not None:
        result = run_soak(
            args.root,
            workflows=args.workflows,
            failures=args.failures,
            uncertain=args.uncertain,
            restarts=args.restarts,
            durable_jobs=args.durable_jobs,
        )
    else:
        with tempfile.TemporaryDirectory(prefix="workflow-soak-", ignore_cleanup_errors=True) as temporary:
            result = run_soak(
                Path(temporary),
                workflows=args.workflows,
                failures=args.failures,
                uncertain=args.uncertain,
                restarts=args.restarts,
                durable_jobs=args.durable_jobs,
            )
    print(json.dumps(result, ensure_ascii=False, separators=(",", ":") if args.json else None))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
