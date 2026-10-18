from __future__ import annotations

import ast
import inspect
from pathlib import Path
from typing import Any, cast

import pytest

from core.errors import ToolError
from core.workflow_actions import ACTION_HANDLERS, SideEffectUncertain, TransientActionError, execute_action
from core.workflows import NATIVE_ONLY_ROUTING, StepDefinition, WorkflowDefinition, WorkflowExecutor, WorkflowState, WorkflowStore


def test_unknown_action_is_rejected() -> None:
    with pytest.raises(ToolError, match="not allowlisted"):
        execute_action("shell", {"command": "whoami"}, 1)


def test_executor_checkpoints_success(tmp_path: Path) -> None:
    target = tmp_path / "ready.txt"
    target.write_text("ready", encoding="utf-8")
    store = WorkflowStore(tmp_path / "workflows.db")
    workflow = store.create(
        WorkflowDefinition("check", (StepDefinition("file", "check_file", {"path": str(target)}),)),
        initial_state=WorkflowState.QUEUED,
    )
    result = WorkflowExecutor(store, route_planner=NATIVE_ONLY_ROUTING).run(workflow["workflow_id"])
    assert result["state"] == "completed"
    assert result["steps"][0]["attempts"] == 1
    assert result["steps"][0]["evidence"]["postcondition"]["conclusive"] is True


def test_dry_run_does_not_advance_state(tmp_path: Path) -> None:
    store = WorkflowStore(tmp_path / "workflows.db")
    workflow = store.create(
        WorkflowDefinition("check", (StepDefinition("file", "check_file", {"path": "missing"}),)),
        initial_state=WorkflowState.QUEUED,
    )
    result = WorkflowExecutor(store, route_planner=NATIVE_ONLY_ROUTING).run(workflow["workflow_id"], dry_run=True)
    assert result["dry_run"] is True
    assert store.get(workflow["workflow_id"])["state"] == "queued"


def test_transient_retry_is_bounded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = 0

    def flaky(arguments, timeout):  # type: ignore[no-untyped-def]
        nonlocal calls
        calls += 1
        if calls == 1:
            raise TransientActionError("temporary")
        return {"ok": True}

    monkeypatch.setitem(ACTION_HANDLERS, "check_http", flaky)
    store = WorkflowStore(tmp_path / "workflows.db")
    workflow = store.create(
        WorkflowDefinition("retry", (StepDefinition("check", "check_http", {"url": "http://localhost"}, max_retries=1),)),
        initial_state=WorkflowState.QUEUED,
    )
    result = WorkflowExecutor(store, route_planner=NATIVE_ONLY_ROUTING).run(workflow["workflow_id"])
    assert result["state"] == "completed"
    assert calls == 2


def test_cancellation_during_retry_backoff_prevents_next_attempt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = WorkflowStore(tmp_path / "workflows.db")
    workflow = store.create(
        WorkflowDefinition("retry-cancel", (StepDefinition("http", "check_http", {"url": "http://localhost"}, max_retries=1),)),
        initial_state=WorkflowState.QUEUED,
    )
    workflow_id = workflow["workflow_id"]
    calls = 0

    def flaky(arguments: dict[str, object], timeout: float) -> dict[str, object]:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise TransientActionError("temporary")
        return {"ok": True}

    def cancel_during_backoff(seconds: float) -> None:
        current = store.get(workflow_id)
        store.request_cancel(workflow_id, current["version"])

    monkeypatch.setitem(ACTION_HANDLERS, "check_http", flaky)
    monkeypatch.setattr("core.workflow_executor.time.sleep", cancel_during_backoff)
    result = WorkflowExecutor(store, route_planner=NATIVE_ONLY_ROUTING).run(workflow_id)

    assert result["state"] == "cancelled"
    assert calls == 1
    operation = store.list_operations(workflow_id)["items"][0]
    assert operation["state"] == "cancelled"
    assert operation["attempts"] == 1


def test_uncertain_side_effect_is_never_advanced(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def uncertain(arguments, timeout):  # type: ignore[no-untyped-def]
        raise SideEffectUncertain("unknown result")

    monkeypatch.setitem(ACTION_HANDLERS, "run_durable_job", uncertain)
    store = WorkflowStore(tmp_path / "workflows.db")
    workflow = store.create(
        WorkflowDefinition(
            "uncertain",
            (
                StepDefinition(
                    "job",
                    "run_durable_job",
                    {"executable": "python", "idempotency_key": "test-job"},
                    postcondition={"kind": "job_request_key_intent"},
                ),
            ),
        ),
        initial_state=WorkflowState.QUEUED,
    )
    result = WorkflowExecutor(store, route_planner=NATIVE_ONLY_ROUTING).run(workflow["workflow_id"])
    assert result["state"] == "uncertain"
    assert result["current_step"] == 0


def test_cancellation_during_action_stops_before_next_operation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = WorkflowStore(tmp_path / "workflows.db")
    workflow = store.create(
        WorkflowDefinition(
            "cancel",
            (
                StepDefinition("first", "check_file", {"path": "unused"}),
                StepDefinition("second", "check_file", {"path": "unused"}),
            ),
        ),
        initial_state=WorkflowState.QUEUED,
    )
    calls = 0

    def cancel(arguments, timeout):  # type: ignore[no-untyped-def]
        nonlocal calls
        del arguments, timeout
        calls += 1
        current = store.get(workflow["workflow_id"])
        store.transition(workflow["workflow_id"], current["version"], WorkflowState.CANCELLED)
        return {"ok": True}

    monkeypatch.setitem(ACTION_HANDLERS, "check_file", cancel)

    result = WorkflowExecutor(store, route_planner=NATIVE_ONLY_ROUTING).execute(workflow["workflow_id"], owner_id="worker-a")

    assert result["state"] == "cancelled"
    assert calls == 1
    assert store.list_operations(workflow["workflow_id"])["items"][0]["state"] == "succeeded"
    assert store.list_operations(workflow["workflow_id"])["items"][1]["state"] == "created"


def test_executor_requires_an_explicit_routing_declaration() -> None:
    """Omission must be a TypeError, not a silent run-time failure later."""
    parameter = inspect.signature(WorkflowExecutor.__init__).parameters["route_planner"]

    assert parameter.kind is inspect.Parameter.KEYWORD_ONLY
    # No default: `WorkflowExecutor(store)` raises instead of building an
    # executor that fails every routed step with workflow_route_planner_missing.
    assert parameter.default is inspect.Parameter.empty


def test_executor_rejects_a_missing_planner_instead_of_defaulting(tmp_path: Path) -> None:
    store = WorkflowStore(tmp_path / "workflows.db")

    construct = cast("Any", WorkflowExecutor)
    with pytest.raises(TypeError, match="route_planner"):
        construct(store)

    with pytest.raises(TypeError):
        WorkflowExecutor(store, route_planner=cast("Any", None))

    executor = WorkflowExecutor(store, route_planner=NATIVE_ONLY_ROUTING)
    assert executor.route_planner is None


def test_native_only_executor_still_fails_closed_on_a_routed_step(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Native-only routing keeps the documented fail-closed behavior: no dispatch."""
    dispatches = 0

    def counted(arguments: object, timeout: float) -> dict[str, object]:
        nonlocal dispatches
        del arguments, timeout
        dispatches += 1
        return {"ok": True}

    monkeypatch.setitem(ACTION_HANDLERS, "check_file", counted)
    store = WorkflowStore(tmp_path / "workflows.db")
    workflow = store.create(
        WorkflowDefinition(
            "routed-check",
            (
                StepDefinition(
                    "check",
                    "check_file",
                    {"path": "unused"},
                    execution_intent={"name": "filesystem.read"},
                ),
            ),
        ),
        initial_state=WorkflowState.QUEUED,
    )

    result = WorkflowExecutor(store, route_planner=NATIVE_ONLY_ROUTING).execute(
        str(workflow["workflow_id"]), owner_id="native-only"
    )

    operation = store.list_operations(str(workflow["workflow_id"]))["items"][0]
    assert result["state"] == "failed"
    assert dispatches == 0
    assert operation["selected_route"] is None
    assert operation["router_policy_version"] is None
    assert "native-only routing" in str(operation["error"])


def _is_executor_construction(func: ast.expr) -> bool:
    if isinstance(func, ast.Name):
        return func.id == "WorkflowExecutor"
    return isinstance(func, ast.Attribute) and func.attr == "WorkflowExecutor"


def test_every_production_executor_construction_declares_routing() -> None:
    """A production entrypoint must state how its workflow steps are routed.

    The omission that produced the worker defect is only caught at run time by
    the required keyword; this scan catches it for every call site, including
    ones no test exercises.
    """
    root = Path(__file__).resolve().parents[1]
    sources = [
        *(root / "core").rglob("*.py"),
        *(root / "tools").rglob("*.py"),
        *(root / "scripts").rglob("*.py"),
        root / "main.py",
    ]
    declared: dict[str, int] = {}
    for path in sorted(sources):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and _is_executor_construction(node.func):
                keywords = {keyword.arg for keyword in node.keywords}
                assert "route_planner" in keywords, (
                    f"{path.relative_to(root).as_posix()} constructs WorkflowExecutor "
                    "without declaring routing"
                )
                relative = path.relative_to(root).as_posix()
                declared[relative] = declared.get(relative, 0) + 1

    # Guard the scan itself: a rename or refactor must not make it vacuous.
    assert {
        "scripts/perf_benchmark.py",
        "scripts/workflow_soak.py",
        "scripts/workflow_worker.py",
        "tools/workflows/registry.py",
    } <= set(declared)
