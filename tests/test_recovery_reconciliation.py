from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.errors import ToolError
from core.recovery import OperationRecoveryJournal
from core.recovery_models import Evidence, OperationState, Postcondition


def _journal(tmp_path: Path) -> OperationRecoveryJournal:
    journal = OperationRecoveryJournal()
    journal.configure(tmp_path / "recovery.jsonl", "current")
    return journal


def _seed_uncertain(path: Path, count: int = 8) -> None:
    records: list[dict[str, object]] = []
    for index in range(count):
        operation_id = f"{index:032x}"
        records.extend(
            [
                {
                    "schema_version": 1,
                    "type": "begin",
                    "status": "in_progress",
                    "operation_id": operation_id,
                    "runtime_id": "old",
                    "operation_type": "run_process",
                    "target": f"cwd=C:/repo/{index}",
                    "started_at": f"2026-09-20T00:00:{index:02d}+00:00",
                    "pid": 100 + index,
                },
                {
                    "schema_version": 1,
                    "type": "uncertain",
                    "status": "uncertain",
                    "operation_id": operation_id,
                    "runtime_id": "old",
                    "marked_by_runtime_id": "current",
                    "marked_at": f"2026-09-20T00:01:{index:02d}+00:00",
                    "reason": "runtime_ended_without_known_result",
                },
            ]
        )
    path.write_text("".join(json.dumps(item) + "\n" for item in records), encoding="utf-8")


def test_lists_all_legacy_uncertain_operations_with_pagination(tmp_path: Path) -> None:
    path = tmp_path / "recovery.jsonl"
    _seed_uncertain(path)
    journal = OperationRecoveryJournal()
    journal.configure(path, "current")

    first = journal.list_operations(state=OperationState.UNCERTAIN, offset=0, max_items=5)
    second = journal.list_operations(state=OperationState.UNCERTAIN, offset=5, max_items=5)

    assert first["total_count"] == 8
    assert first["count"] == 5
    assert first["has_more"] is True
    assert second["count"] == 3
    assert {item["operation_id"] for item in [*first["items"], *second["items"]]} == {
        f"{index:032x}" for index in range(8)
    }


def test_inspect_and_history_preserve_legacy_records(tmp_path: Path) -> None:
    path = tmp_path / "recovery.jsonl"
    _seed_uncertain(path, 1)
    journal = OperationRecoveryJournal()
    journal.configure(path, "current")
    operation_id = f"{0:032x}"

    inspected = journal.inspect(operation_id)
    history = journal.history(operation_id, offset=0, max_items=20)

    assert inspected["state"] == "uncertain"
    assert inspected["operation_type"] == "run_process"
    assert inspected["target"] == "cwd=C:/repo/0"
    assert [item["type"] for item in history["items"]] == ["begin", "uncertain"]


def test_acknowledgment_requires_evidence_and_is_idempotent(tmp_path: Path) -> None:
    path = tmp_path / "recovery.jsonl"
    _seed_uncertain(path, 1)
    journal = OperationRecoveryJournal()
    journal.configure(path, "current")
    operation_id = f"{0:032x}"

    with pytest.raises(ToolError, match="evidence"):
        journal.acknowledge(operation_id, OperationState.ACKNOWLEDGED, None)
    evidence = Evidence(source="operator", data={"note": "state inspected", "outcome": "accepted_unknown"})
    first = journal.acknowledge(operation_id, OperationState.ACKNOWLEDGED, evidence)
    second = journal.acknowledge(operation_id, OperationState.ACKNOWLEDGED, evidence)

    assert first["state"] == "acknowledged"
    assert second["state"] == "acknowledged"
    assert [item["type"] for item in journal.history(operation_id, 0, 20)["items"]].count("acknowledged") == 1


def test_reconciliation_records_conclusive_evidence_and_survives_compaction(tmp_path: Path) -> None:
    path = tmp_path / "recovery.jsonl"
    _seed_uncertain(path, 1)
    journal = OperationRecoveryJournal()
    journal.configure(path, "current")
    operation_id = f"{0:032x}"
    postcondition = Postcondition(kind="file_absent", expected={"path": "C:/gone.txt"})
    evidence = Evidence(source="filesystem", data={"exists": False, "conclusive": True})

    result = journal.record_reconciliation(operation_id, OperationState.SUCCEEDED, postcondition, evidence)
    journal.compact()

    assert result["state"] == "succeeded"
    reloaded = OperationRecoveryJournal()
    reloaded.configure(path, "next")
    inspected = reloaded.inspect(operation_id)
    assert inspected["state"] == "succeeded"
    assert inspected["postcondition"]["kind"] == "file_absent"
    assert inspected["evidence"][-1]["data"]["conclusive"] is True
