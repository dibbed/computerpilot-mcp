from __future__ import annotations

import json
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from core.config import SETTINGS
from core.errors import ToolError
from core.execution_models import ExecutionIntent
from core.execution_router import ROUTER_POLICY_VERSION
from core.extensions.config import load_extension_config
from core.platform import detect_capabilities
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


def test_worker_route_planning_never_activates_a_configured_extension(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Planning reads the native catalog only and must never start a provider.

    The manager fixture tests cover the positive case (a configured, approved
    extension does start and is discovered). This guards the worker's own path:
    it must not read or activate provider configuration while planning, even when
    extensions are enabled and the definition is launch-eligible on this host.
    """
    marker = tmp_path / "marker.txt"
    script = tmp_path / "marker_server.py"
    script.write_text(
        "import pathlib, sys\n"
        "pathlib.Path(sys.argv[1]).write_text('spawned', encoding='utf-8')\n",
        encoding="utf-8",
    )
    config = tmp_path / "extensions.json"
    config.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "extensions": [
                    {
                        "extension_id": "canary",
                        "display_name": "Canary",
                        "provider": "canary",
                        "command": sys.executable,
                        "arguments": [str(script), str(marker)],
                        "working_directory": str(tmp_path),
                        "enabled": True,
                        "launch_approved": True,
                        "trust_class": "restricted",
                        "platforms": ["windows", "linux", "macos"],
                        "tools": {
                            "read_value": {
                                "remote_name": "read_value",
                                "domain": "testing",
                                "allow_call": True,
                            }
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    settings = replace(
        SETTINGS,
        state_dir=tmp_path / "state",
        extensions_enabled=True,
        extensions_config=str(config),
    )

    # The definition itself is valid and launch-eligible, so "nothing spawned"
    # is a statement about the worker, not about a rejected configuration.
    loaded = load_extension_config(settings)
    assert loaded.error_code is None
    definition = loaded.config.extensions[0]
    assert definition.enabled is True and definition.launch_approved is True
    assert detect_capabilities().system in definition.platforms

    monkeypatch.setattr(workflow_worker, "SETTINGS", settings)
    planner = workflow_worker.build_executor_route_planner()
    assert planner(ExecutionIntent(name="filesystem.read"))["ok"] is True
    monkeypatch.setattr(sys, "argv", ["workflow_worker", "--once"])
    assert workflow_worker.main() == 0

    assert not marker.exists()
