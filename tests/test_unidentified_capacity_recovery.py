from __future__ import annotations

import os
from pathlib import Path

import psutil
import pytest

from core.errors import ToolError
from core.resource_claims import path_claim
from core.workflow_store import WorkflowStore


def _quarantined_parent(tmp_path: Path, *, child_owner: str = "unknown-owner") -> WorkflowStore:
    store = WorkflowStore(tmp_path / "workflows.sqlite3", recover=False)
    admitted = store.reserve_resource_claims(
        request_id="parent-request",
        owner_token="parent-owner",
        claims=(path_claim(tmp_path / "target", "write", scope="subtree"),),
        category="native.job",
    )
    assert admitted["admitted"] is True
    child = store.reserve_capacity(
        admission_id="opaque-child",
        category="analyzer",
        owner_token=child_owner,
        weight=1,
        limit=1,
        resource_request_id="parent-request",
        resource_owner_token="parent-owner",
    )
    assert child["admitted"] is True
    store.quarantine_resource_request(
        "parent-request", owner_token="parent-owner", reason="managed_effect_or_cleanup_unresolved",
    )
    return store


def test_unidentified_capacity_settlement_is_bounded_explicit_and_idempotent(tmp_path: Path) -> None:
    store = _quarantined_parent(tmp_path)
    with pytest.raises(ToolError) as unresolved:
        store.resolve_quarantined_resource_request(
            "parent-request", owner_token="parent-owner", resolution="released", evidence="operator ack",
        )
    assert unresolved.value.code == "resource_children_unresolved"

    with pytest.raises(ToolError) as mismatch:
        store.release_unidentified_resource_capacity(
            "parent-request",
            owner_token="wrong-owner",
            admission_ids=["opaque-child"],
            evidence="dead test process",
        )
    assert mismatch.value.code == "resource_owner_mismatch"

    with pytest.raises(ToolError) as evidence_required:
        store.release_unidentified_resource_capacity(
            "parent-request", owner_token="parent-owner", admission_ids=["opaque-child"], evidence=" ",
        )
    assert evidence_required.value.code == "resource_resolution_evidence_required"

    assert store.release_unidentified_resource_capacity(
        "parent-request",
        owner_token="parent-owner",
        admission_ids=["opaque-child"],
        evidence="contaminated pytest child exited; no live owner remains",
    ) == 1
    assert store.release_unidentified_resource_capacity(
        "parent-request",
        owner_token="parent-owner",
        admission_ids=["opaque-child"],
        evidence="contaminated pytest child exited; no live owner remains",
    ) == 0

    counts = store.health_summary()["runtime_capacity_counts"]
    assert "analyzer:active" not in counts
    store.resolve_quarantined_resource_request(
        "parent-request",
        owner_token="parent-owner",
        resolution="released",
        evidence="opaque child settled explicitly; no live owner remains",
    )
    assert store.health_summary()["quarantined_resource_request_count"] == 0


def test_unidentified_capacity_settlement_rejects_pid_identity(tmp_path: Path) -> None:
    child_owner = f"pid-{os.getpid()}-{psutil.Process().create_time():.6f}-fixture"
    store = _quarantined_parent(tmp_path, child_owner=child_owner)
    with pytest.raises(ToolError) as rejected:
        store.release_unidentified_resource_capacity(
            "parent-request",
            owner_token="parent-owner",
            admission_ids=["opaque-child"],
            evidence="must not bypass death proof",
        )
    assert rejected.value.code == "capacity_identity_requires_death_proof"
    assert store.health_summary()["runtime_capacity_counts"]["analyzer:active"] == 1


def test_unidentified_capacity_settlement_is_atomic_for_unknown_or_foreign_ids(tmp_path: Path) -> None:
    store = _quarantined_parent(tmp_path)
    with pytest.raises(ToolError) as missing:
        store.release_unidentified_resource_capacity(
            "parent-request",
            owner_token="parent-owner",
            admission_ids=["opaque-child", "not-linked"],
            evidence="one id is not linked",
        )
    assert missing.value.code == "capacity_request_not_found"
    assert store.health_summary()["runtime_capacity_counts"]["analyzer:active"] == 1


def test_unidentified_capacity_settlement_requires_a_quarantined_parent(tmp_path: Path) -> None:
    store = WorkflowStore(tmp_path / "workflows.sqlite3", recover=False)
    assert store.reserve_resource_claims(
        request_id="active-parent",
        owner_token="parent-owner",
        claims=(path_claim(tmp_path / "target", "write", scope="subtree"),),
        category="native.job",
    )["admitted"] is True
    assert store.reserve_capacity(
        admission_id="opaque-child",
        category="analyzer",
        owner_token="unknown-owner",
        weight=1,
        limit=1,
        resource_request_id="active-parent",
        resource_owner_token="parent-owner",
    )["admitted"] is True
    with pytest.raises(ToolError) as not_quarantined:
        store.release_unidentified_resource_capacity(
            "active-parent",
            owner_token="parent-owner",
            admission_ids=["opaque-child"],
            evidence="active parents must use normal release",
        )
    assert not_quarantined.value.code == "resource_request_not_quarantined"
