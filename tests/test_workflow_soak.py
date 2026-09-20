from __future__ import annotations

from pathlib import Path

from scripts.workflow_soak import run_soak


def test_workflow_soak_smoke_covers_restarts_reconciliation_retention_and_leaks(tmp_path: Path) -> None:
    result = run_soak(
        tmp_path,
        workflows=12,
        failures=2,
        uncertain=2,
        restarts=1,
        durable_jobs=0,
    )

    assert result["ok"] is True
    assert result["workflows_requested"] == 12
    assert result["completed"] == 10
    assert result["failed"] == 2
    assert result["uncertain_reconciled"] == 2
    assert result["process_restarts"] == 1
    assert result["leaked_leases"] == 0
    assert result["orphan_operations"] == 0
    assert result["orphan_events"] == 0
    assert result["durable_jobs"] == {"submitted": 0, "succeeded": 0, "active": 0}
    assert result["rss_growth_mb"] >= 0
