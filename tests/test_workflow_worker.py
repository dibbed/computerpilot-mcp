from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path

import pytest

from core.config import SETTINGS
from core.errors import ToolError
from core.execution_models import ExecutionIntent
from core.execution_router import ROUTER_POLICY_VERSION
from core.workflow_models import StepDefinition, WorkflowDefinition
from core.workflows import WorkflowState, workflow_store
from scripts import workflow_worker


class _FakeStore:
    def __init__(self, items: list[dict[str, str]]) -> None:
        self.items = items
        self.calls = 0

    def list_queued(self, *, limit: int = 100) -> dict[str, object]:
        self.calls += 1
        assert limit == 100
        return {"items": list(self.items)}


def _stub_route_planner(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep queue-loop tests independent of the live native catalog."""

    def builder() -> object:
        def plan(intent: ExecutionIntent) -> dict[str, object]:
            del intent
            return {"ok": False}

        return plan

    monkeypatch.setattr(workflow_worker, "build_executor_route_planner", builder)


def test_worker_once_continues_after_deterministic_workflow_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _FakeStore([{"workflow_id": "bad"}, {"workflow_id": "good"}])
    attempted: list[str] = []

    class FakeExecutor:
        def __init__(self, received: object, *, route_planner: object = None) -> None:
            assert received is store
            assert callable(route_planner)

        def execute(self, workflow_id: str, *, owner_id: str) -> dict[str, str]:
            assert owner_id.startswith("worker-")
            attempted.append(workflow_id)
            if workflow_id == "bad":
                raise ToolError("invalid_workflow_definition", "bad workflow")
            return {"state": "completed"}

    monkeypatch.setattr(workflow_worker, "workflow_store", lambda path: store)
    monkeypatch.setattr(workflow_worker, "WorkflowExecutor", FakeExecutor)
    monkeypatch.setattr(sys, "argv", ["workflow_worker", "--once"])
    _stub_route_planner(monkeypatch)

    assert workflow_worker.main() == 0
    assert attempted == ["bad", "good"]
    assert store.calls == 1


def test_worker_once_skips_already_claimed_workflow(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _FakeStore([{"workflow_id": "claimed"}, {"workflow_id": "next"}])
    attempted: list[str] = []

    class FakeExecutor:
        def __init__(self, received: object, *, route_planner: object = None) -> None:
            assert received is store
            assert callable(route_planner)

        def execute(self, workflow_id: str, *, owner_id: str) -> dict[str, str]:
            del owner_id
            attempted.append(workflow_id)
            if workflow_id == "claimed":
                raise ToolError("workflow_already_claimed", "claimed")
            return {"state": "completed"}

    monkeypatch.setattr(workflow_worker, "workflow_store", lambda path: store)
    monkeypatch.setattr(workflow_worker, "WorkflowExecutor", FakeExecutor)
    monkeypatch.setattr(sys, "argv", ["workflow_worker", "--once"])
    _stub_route_planner(monkeypatch)

    assert workflow_worker.main() == 0
    assert attempted == ["claimed", "next"]


def test_worker_propagates_schema_corruption_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _FakeStore([{"workflow_id": "fatal"}])

    class FakeExecutor:
        def __init__(self, received: object, *, route_planner: object = None) -> None:
            assert received is store
            assert callable(route_planner)

        def execute(self, workflow_id: str, *, owner_id: str) -> dict[str, str]:
            del workflow_id, owner_id
            raise ToolError("workflow_schema_too_new", "unsupported schema")

    monkeypatch.setattr(workflow_worker, "workflow_store", lambda path: store)
    monkeypatch.setattr(workflow_worker, "WorkflowExecutor", FakeExecutor)
    monkeypatch.setattr(sys, "argv", ["workflow_worker", "--once"])
    _stub_route_planner(monkeypatch)

    with pytest.raises(ToolError) as raised:
        workflow_worker.main()

    assert raised.value.code == "workflow_schema_too_new"


def test_worker_route_planner_uses_the_registered_native_catalog() -> None:
    planner = workflow_worker.build_executor_route_planner()

    decision = planner(ExecutionIntent(name="filesystem.read"))

    assert decision["ok"] is True
    assert decision["selected_route"] == "native.filesystem"
    assert decision["router_policy_version"] == ROUTER_POLICY_VERSION
    # The planner stays advisory and fails closed on an intent it cannot map.
    with pytest.raises(ToolError) as raised:
        planner(ExecutionIntent(name="not.a.real.intent"))
    assert raised.value.code == "unknown_execution_intent"


def test_worker_executes_queued_routed_workflow_through_its_own_entrypoint(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A routed queued workflow must plan its route instead of failing as unplannable."""
    target = tmp_path / "ready.txt"
    target.write_text("ready", encoding="utf-8")
    monkeypatch.setattr(workflow_worker, "SETTINGS", replace(SETTINGS, state_dir=tmp_path))
    store = workflow_store(tmp_path / "workflows.sqlite3")
    workflow = store.create(
        WorkflowDefinition(
            "routed-check",
            (
                StepDefinition(
                    "check",
                    "check_file",
                    {"path": str(target)},
                    execution_intent={"name": "filesystem.read"},
                ),
            ),
        ),
        initial_state=WorkflowState.QUEUED,
    )
    workflow_id = str(workflow["workflow_id"])
    monkeypatch.setattr(sys, "argv", ["workflow_worker", "--once"])

    assert workflow_worker.main() == 0

    result = store.get(workflow_id)
    operation = store.list_operations(workflow_id)["items"][0]
    assert result["state"] == WorkflowState.COMPLETED.value
    assert operation["state"] == "succeeded"
    assert operation["error"] is None
    assert operation["selected_route"] == "native.filesystem"
    assert operation["router_policy_version"] == ROUTER_POLICY_VERSION
    assert operation["route_decision"] is not None
