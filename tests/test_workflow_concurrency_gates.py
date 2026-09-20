from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from core.errors import ToolError
from core.workflow_actions import ACTION_HANDLERS, SideEffectUncertain
from core.workflow_reconciliation import acknowledge_operation, reconcile_operation
from core.workflow_retention import WorkflowHistoryPolicy, cleanup_workflow_history
from core.workflows import StepDefinition, WorkflowDefinition, WorkflowExecutor, WorkflowState, WorkflowStore


def _definition(name: str = "concurrency") -> WorkflowDefinition:
    return WorkflowDefinition(
        name,
        (StepDefinition("check", "check_file", {"path": "unused"}),),
    )


def test_two_executors_cannot_run_same_workflow_twice(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = WorkflowStore(tmp_path / "workflows.db")
    workflow = store.create(_definition(), initial_state=WorkflowState.QUEUED)
    workflow_id = str(workflow["workflow_id"])
    entered = threading.Event()
    release = threading.Event()
    call_lock = threading.Lock()
    calls = 0

    def blocked(arguments: dict[str, object], timeout: float) -> dict[str, object]:
        nonlocal calls
        del arguments, timeout
        with call_lock:
            calls += 1
        entered.set()
        assert release.wait(timeout=5)
        return {"ok": True}

    monkeypatch.setitem(ACTION_HANDLERS, "check_file", blocked)

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(WorkflowExecutor(store).execute, workflow_id, owner_id="worker-a")
        assert entered.wait(timeout=5)
        second = pool.submit(WorkflowExecutor(WorkflowStore(store.path)).execute, workflow_id, owner_id="worker-b")
        second_error = second.exception(timeout=5)
        release.set()
        completed = first.result(timeout=10)

    assert completed["state"] == "completed"
    assert calls == 1
    assert isinstance(second_error, ToolError)
    assert second_error.code in {"workflow_not_queued", "workflow_already_claimed"}


def test_reconcile_and_manual_acknowledgement_are_cas_serialized(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "done.txt"
    target.write_text("done", encoding="utf-8")
    store = WorkflowStore(tmp_path / "workflows.db")

    def uncertain(arguments: dict[str, object], timeout: float) -> dict[str, object]:
        del arguments, timeout
        raise SideEffectUncertain("result lost")

    monkeypatch.setitem(ACTION_HANDLERS, "check_file", uncertain)
    workflow = store.create(
        WorkflowDefinition(
            "reconcile-race",
            (
                StepDefinition(
                    "effect",
                    "check_file",
                    {"path": str(target)},
                    postcondition={"kind": "file_exists", "expected": {"path": str(target)}},
                ),
            ),
        ),
        initial_state=WorkflowState.QUEUED,
    )
    WorkflowExecutor(store).execute(str(workflow["workflow_id"]), owner_id="worker-a")
    operation = store.list_operations(str(workflow["workflow_id"]))["items"][0]
    version = int(operation["version"])
    operation_id = str(operation["operation_id"])
    barrier = threading.Barrier(2)

    def automated() -> tuple[str, Any]:
        barrier.wait(timeout=5)
        try:
            return "ok", reconcile_operation(store, operation_id, expected_version=version)
        except ToolError as exc:
            return "error", exc.code

    def manual() -> tuple[str, Any]:
        barrier.wait(timeout=5)
        try:
            return "ok", acknowledge_operation(
                store,
                operation_id,
                expected_version=version,
                resolution="resolved_completed",
                reason="operator verified",
                actor="operator",
            )
        except ToolError as exc:
            return "error", exc.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = [future.result(timeout=10) for future in (pool.submit(automated), pool.submit(manual))]

    assert sum(kind == "ok" for kind, _ in results) == 1
    assert sum(kind == "error" for kind, _ in results) == 1
    final_operation = store.get_operation(operation_id)
    assert final_operation["state"] in {"succeeded", "acknowledged"}
    assert store.get(str(workflow["workflow_id"]))["state"] == "completed"


def test_retention_can_race_status_reads_without_touching_active_workflow(tmp_path: Path) -> None:
    path = tmp_path / "workflows.db"
    store = WorkflowStore(path)
    active = store.create(_definition("active"), initial_state=WorkflowState.QUEUED)
    active_id = str(active["workflow_id"])
    for index in range(40):
        store.create(_definition(f"done-{index}"), initial_state=WorkflowState.COMPLETED)

    errors: list[BaseException] = []
    barrier = threading.Barrier(2)

    def read_status() -> None:
        try:
            barrier.wait(timeout=5)
            for _ in range(100):
                current = store.get(active_id)
                assert current["state"] == "queued"
        except BaseException as exc:
            errors.append(exc)

    def cleanup() -> None:
        try:
            barrier.wait(timeout=5)
            cleanup_workflow_history(
                path,
                WorkflowHistoryPolicy(max_age_days=0, max_count=5, cleanup_interval_sec=1),
                now=datetime.now(timezone.utc),
            )
        except BaseException as exc:
            errors.append(exc)

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(read_status), pool.submit(cleanup)]
        for future in futures:
            future.result(timeout=15)

    assert errors == []
    assert store.get(active_id)["state"] == "queued"
