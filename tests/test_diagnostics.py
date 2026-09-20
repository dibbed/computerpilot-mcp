from __future__ import annotations

from pathlib import Path

from core.workflow_models import WorkflowState
from core.workflows import StepDefinition, WorkflowDefinition, WorkflowStore
from tools.testing.diagnostics import merge_diagnostics, normalize_diagnostic


def test_normalizes_lsp_severity_and_range() -> None:
    result = normalize_diagnostic(
        {"file": "C:\\repo\\a.py", "line": 2, "column": 4, "end_line": 2, "end_column": 8, "severity": 2, "message": "warn", "code": 7},
        source="lsp",
    )
    assert result == {
        "file": "C:\\repo\\a.py",
        "line": 2,
        "column": 4,
        "end_line": 2,
        "end_column": 8,
        "severity": "warning",
        "message": "warn",
        "code": "7",
        "source": "lsp",
    }


def test_merge_is_deduplicated_stable_and_capped() -> None:
    duplicate = {"file": "b.py", "line": 3, "severity": "error", "message": "bad", "source": "ruff"}
    diagnostics, truncated = merge_diagnostics(
        [[duplicate], [duplicate, {"file": "a.py", "line": 1, "severity": "note", "message": "first", "source": "mypy"}]], cap=1
    )
    assert diagnostics[0]["file"] == "a.py"
    assert truncated is True


def test_workflow_health_separates_aggregate_operations_and_leases(tmp_path: Path) -> None:
    store = WorkflowStore(tmp_path / "workflows.db")
    workflow = store.create(
        WorkflowDefinition("health", (StepDefinition("file", "check_file", {"path": "unused"}),)),
        initial_state=WorkflowState.QUEUED,
    )
    store.acquire_lease(workflow["workflow_id"], "health-test", 30)

    summary = store.health_summary()

    assert summary["workflow_counts"] == {"queued": 1}
    assert summary["operation_counts"] == {"created": 1}
    assert summary["unresolved_operation_count"] == 0
    assert summary["active_lease_count"] == 1
