from __future__ import annotations

import sys

import pytest

from core.errors import ToolError
from scripts import workflow_worker


class _FakeStore:
    def __init__(self, items: list[dict[str, str]]) -> None:
        self.items = items
        self.calls = 0

    def list_queued(self, *, limit: int = 100) -> dict[str, object]:
        self.calls += 1
        assert limit == 100
        return {"items": list(self.items)}


def test_worker_once_continues_after_deterministic_workflow_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _FakeStore([{"workflow_id": "bad"}, {"workflow_id": "good"}])
    attempted: list[str] = []

    class FakeExecutor:
        def __init__(self, received: object) -> None:
            assert received is store

        def execute(self, workflow_id: str, *, owner_id: str) -> dict[str, str]:
            assert owner_id.startswith("worker-")
            attempted.append(workflow_id)
            if workflow_id == "bad":
                raise ToolError("invalid_workflow_definition", "bad workflow")
            return {"state": "completed"}

    monkeypatch.setattr(workflow_worker, "workflow_store", lambda path: store)
    monkeypatch.setattr(workflow_worker, "WorkflowExecutor", FakeExecutor)
    monkeypatch.setattr(sys, "argv", ["workflow_worker", "--once"])

    assert workflow_worker.main() == 0
    assert attempted == ["bad", "good"]
    assert store.calls == 1


def test_worker_once_skips_already_claimed_workflow(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _FakeStore([{"workflow_id": "claimed"}, {"workflow_id": "next"}])
    attempted: list[str] = []

    class FakeExecutor:
        def __init__(self, received: object) -> None:
            assert received is store

        def execute(self, workflow_id: str, *, owner_id: str) -> dict[str, str]:
            del owner_id
            attempted.append(workflow_id)
            if workflow_id == "claimed":
                raise ToolError("workflow_already_claimed", "claimed")
            return {"state": "completed"}

    monkeypatch.setattr(workflow_worker, "workflow_store", lambda path: store)
    monkeypatch.setattr(workflow_worker, "WorkflowExecutor", FakeExecutor)
    monkeypatch.setattr(sys, "argv", ["workflow_worker", "--once"])

    assert workflow_worker.main() == 0
    assert attempted == ["claimed", "next"]


def test_worker_propagates_schema_corruption_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _FakeStore([{"workflow_id": "fatal"}])

    class FakeExecutor:
        def __init__(self, received: object) -> None:
            assert received is store

        def execute(self, workflow_id: str, *, owner_id: str) -> dict[str, str]:
            del workflow_id, owner_id
            raise ToolError("workflow_schema_too_new", "unsupported schema")

    monkeypatch.setattr(workflow_worker, "workflow_store", lambda path: store)
    monkeypatch.setattr(workflow_worker, "WorkflowExecutor", FakeExecutor)
    monkeypatch.setattr(sys, "argv", ["workflow_worker", "--once"])

    with pytest.raises(ToolError) as raised:
        workflow_worker.main()

    assert raised.value.code == "workflow_schema_too_new"
