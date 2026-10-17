from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Literal

import pytest

from core.errors import ToolError
from core.resource_claims import ResourceClaim, claims_conflict, managed_resource_claims, path_claim, resource_capability
from core.workflow_models import StepDefinition, WorkflowDefinition, WorkflowState
from core.workflow_store import WorkflowStore


def test_resource_conflict_matrix_handles_exact_and_subtree_paths(tmp_path: Path) -> None:
    root = str((tmp_path / "repo").resolve(strict=False))
    child = str((tmp_path / "repo" / "a.txt").resolve(strict=False))
    sibling_prefix = str((tmp_path / "repo2" / "a.txt").resolve(strict=False))

    read_root = ResourceClaim("fs", root, "read", "subtree")
    read_child = ResourceClaim("fs", child, "read", "exact")
    write_child = ResourceClaim("fs", child, "write", "exact")
    write_other = ResourceClaim("fs", sibling_prefix, "write", "exact")

    assert claims_conflict(read_root, read_child) is False
    assert claims_conflict(read_root, write_child) is True
    assert claims_conflict(write_child, read_root) is True
    assert claims_conflict(read_root, write_other) is False


def test_resource_reservation_is_all_or_none_across_store_instances(tmp_path: Path) -> None:
    path = tmp_path / "workflows.sqlite3"
    first_store = WorkflowStore(path, recover=False)
    second_store = WorkflowStore(path, recover=False)
    root = str((tmp_path / "repo").resolve(strict=False))
    claims = (
        ResourceClaim("coordination", "host", "read", "exact"),
        ResourceClaim("fs", root, "write", "subtree"),
    )

    first = first_store.reserve_resource_claims(
        request_id="request-1",
        owner_token="owner-1",
        claims=claims,
        category="workflow_step",
    )
    second = second_store.reserve_resource_claims(
        request_id="request-2",
        owner_token="owner-2",
        claims=claims,
        category="workflow_step",
    )
    assert first["admitted"] is True
    assert second["admitted"] is False
    assert second["reason"] == "resource_conflict"
    assert second_store.resource_request("request-2")["state"] == "pending"

    first_store.release_resource_request("request-1", owner_token="owner-1")
    retried = second_store.reserve_resource_claims(
        request_id="request-2",
        owner_token="owner-2",
        claims=claims,
        category="workflow_step",
    )
    assert retried["admitted"] is True


def test_quarantined_resource_request_blocks_conflicts_until_explicit_resolution(tmp_path: Path) -> None:
    store = WorkflowStore(tmp_path / "workflows.sqlite3", recover=False)
    claim = ResourceClaim("fs", str((tmp_path / "target.txt").resolve(strict=False)), "write", "exact")
    admitted = store.reserve_resource_claims(
        request_id="request-1",
        owner_token="owner-1",
        claims=(claim,),
        category="workflow_step",
    )
    assert admitted["admitted"] is True
    store.quarantine_resource_request("request-1", owner_token="owner-1", reason="lease lost")

    blocked = store.reserve_resource_claims(
        request_id="request-2",
        owner_token="owner-2",
        claims=(claim,),
        category="workflow_step",
    )
    assert blocked["admitted"] is False
    assert blocked["reason"] == "resource_quarantined"
    assert store.release_resource_request("request-1", owner_token="owner-1") is False
    assert store.resolve_quarantined_resource_request(
        "request-1", owner_token="owner-1", resolution="released", evidence="effect reconciled"
    )["state"] == "released"
    assert store.reserve_resource_claims(
        request_id="request-2",
        owner_token="owner-2",
        claims=(claim,),
        category="workflow_step",
    )["admitted"] is True


def test_failed_quarantine_does_not_release_unknown_effect(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = WorkflowStore(tmp_path / "workflows.sqlite3", recover=False)
    claim = path_claim(tmp_path / "target", "write")

    def deny_quarantine(*args: object, **kwargs: object) -> None:
        raise sqlite3.OperationalError("storage unavailable")

    monkeypatch.setattr(store, "quarantine_resource_request", deny_quarantine)
    with pytest.raises(sqlite3.OperationalError):
        with managed_resource_claims((claim,), category="test", store=store, quarantine_on_error=True):
            raise PermissionError("cleanup denied")

    assert store.reserve_resource_claims(
        request_id="conflict", owner_token="other", category="test", claims=(claim,),
    )["admitted"] is False


@pytest.mark.parametrize("writer", ["document", "workspace_edit"])
def test_document_and_language_writers_enforce_inherited_scope(tmp_path: Path, writer: str) -> None:
    from tools.documents.common import publish_document
    from tools.language.edits import PlannedFile, apply_workspace_edit

    store = WorkflowStore(tmp_path / "workflows.sqlite3", recover=False)
    target = tmp_path / "target.txt"
    target.write_bytes(b"before")

    def write_document(staged: Path) -> None:
        staged.write_bytes(b"after")

    with managed_resource_claims((path_claim(target, "read"),), category="reader", store=store):
        with pytest.raises(ToolError) as raised:
            if writer == "document":
                publish_document(
                    target, writer=write_document, validator=None,
                    media_type="text/plain", backup=False,
                )
            else:
                apply_workspace_edit((PlannedFile(target, b"before", b"after"),))
        assert raised.value.code == "resource_capability_scope_violation"
    assert target.read_bytes() == b"before"


def test_inherited_resource_capability_cannot_expand_parent_scope(tmp_path: Path) -> None:
    store = WorkflowStore(tmp_path / "workflows.sqlite3", recover=False)
    repo = tmp_path / "repo"
    repo.mkdir()
    outside = tmp_path / "outside.txt"

    with managed_resource_claims(
        (path_claim(repo, "write", scope="subtree"),),
        category="parent",
        store=store,
    ) as capability:
        with resource_capability(capability):
            with pytest.raises(ToolError) as raised:
                with managed_resource_claims(
                    (path_claim(outside, "write"),),
                    category="child",
                    store=store,
                ):
                    pass

    assert raised.value.code == "resource_capability_scope_violation"


def test_forged_resource_capability_fails_closed(tmp_path: Path) -> None:
    store = WorkflowStore(tmp_path / "workflows.sqlite3", recover=False)
    target = tmp_path / "target.txt"

    with resource_capability("forged-capability"):
        with pytest.raises(ToolError) as raised:
            with managed_resource_claims(
                (path_claim(target, "read"),),
                category="child",
                store=store,
            ):
                pass

    assert raised.value.code == "resource_capability_invalid"


@pytest.mark.parametrize("durable", [True, False])
def test_child_reuses_parent_claim_across_processes(tmp_path: Path, durable: bool) -> None:
    from core.executor import run_bounded
    from core.jobs import JobStore
    from core.resource_claims import coordination_claim

    resources = WorkflowStore(tmp_path / "workflows.sqlite3", recover=False)
    jobs = JobStore(tmp_path / "jobs.sqlite3")
    marker = tmp_path / "child.txt"
    script = '''
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from core.resource_claims import coordination_claim, managed_resource_claims, path_claim
with managed_resource_claims(
    (coordination_claim(), path_claim(sys.argv[2], "write")), category="nested-child", timeout_sec=1,
    db_path=Path(sys.argv[3]),
):
    Path(sys.argv[2]).write_text("once", encoding="utf-8")
'''
    with managed_resource_claims(
        (coordination_claim(exclusive=True), path_claim(tmp_path, "write", scope="subtree")),
        category="parent", store=resources,
    ) as capability:
        command = [sys.executable, "-c", script, str(Path(__file__).resolve().parents[1]), str(marker), str(resources.path)]
        if durable:
            submitted = jobs.submit(command, tmp_path, 10, "nested-child", _resource_capability=capability)
            job_id = submitted["job_id"]
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                result = jobs.get(job_id)
                if result["status"] not in {"queued", "running"}:
                    break
                time.sleep(0.05)
            else:
                jobs.cancel(job_id)
                pytest.fail("Nested child did not settle before the deadline")
            assert result["status"] == "succeeded", result
        else:
            result = run_bounded(
                command, cwd=tmp_path, timeout_sec=10,
                env={"computerpilot_internal_resource_capability": "forged-override"},
            )
            assert result["ok"] is True, result
        assert marker.read_text(encoding="utf-8") == "once"
        assert resources.health_summary()["active_resource_request_count"] == 1


def test_capacity_admission_is_shared_between_store_instances(tmp_path: Path) -> None:
    path = tmp_path / "workflows.sqlite3"
    first_store = WorkflowStore(path, recover=False)
    second_store = WorkflowStore(path, recover=False)

    one = first_store.reserve_capacity(
        admission_id="a1", category="analyzer", owner_token="owner-1", weight=1, limit=1
    )
    two = second_store.reserve_capacity(
        admission_id="a2", category="analyzer", owner_token="owner-2", weight=1, limit=1
    )
    assert one["admitted"] is True
    assert two["admitted"] is False
    assert two["reason"] == "capacity_exhausted"
    first_store.release_capacity("a1", owner_token="owner-1")
    assert second_store.reserve_capacity(
        admission_id="a2", category="analyzer", owner_token="owner-2", weight=1, limit=1
    )["admitted"] is True


def test_resource_claim_conflict_is_enforced_across_os_processes(tmp_path: Path) -> None:
    db_path = tmp_path / "workflows.sqlite3"
    target = tmp_path / "shared.txt"
    repo_root = Path(__file__).resolve().parents[1]
    script = r'''
import json
import sys
import time
from pathlib import Path
from core.resource_claims import ResourceClaim
from core.workflow_store import WorkflowStore

store = WorkflowStore(Path(sys.argv[1]), recover=False)
claim = ResourceClaim("fs", str(Path(sys.argv[2]).resolve(strict=False)), "write", "exact")
request_id, owner_token, delay = sys.argv[3], sys.argv[4], float(sys.argv[5])
release_marker = Path(sys.argv[6])
result = store.reserve_resource_claims(
    request_id=request_id,
    owner_token=owner_token,
    claims=(claim,),
    category="process-test",
)
print(json.dumps(result), flush=True)
if result["admitted"]:
    if delay < 0:
        deadline = time.monotonic() + 10
        while not release_marker.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
    else:
        time.sleep(delay)
    store.release_resource_request(request_id, owner_token=owner_token)
'''

    release_marker = tmp_path / "release.marker"
    first = subprocess.Popen(
        [sys.executable, "-c", script, str(db_path), str(target), "p1", "o1", "-1", str(release_marker)],
        cwd=repo_root,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert first.stdout is not None
    first_result = json.loads(first.stdout.readline())
    assert first_result["admitted"] is True

    second = subprocess.run(
        [sys.executable, "-c", script, str(db_path), str(target), "p2", "o2", "0", str(release_marker)],
        cwd=repo_root,
        capture_output=True,
        text=True,
        timeout=10,
        check=True,
    )
    second_result = json.loads(second.stdout.strip())
    assert second_result["admitted"] is False
    assert second_result["reason"] == "resource_conflict"

    release_marker.write_text("release", encoding="utf-8")
    assert first.wait(timeout=10) == 0
    third = subprocess.run(
        [sys.executable, "-c", script, str(db_path), str(target), "p2", "o2", "0", str(release_marker)],
        cwd=repo_root,
        capture_output=True,
        text=True,
        timeout=10,
        check=True,
    )
    third_result = json.loads(third.stdout.strip())
    assert third_result["admitted"] is True


def test_reversed_multikey_requests_do_not_deadlock_or_partially_admit(tmp_path: Path) -> None:
    path = tmp_path / "workflows.sqlite3"
    first_store = WorkflowStore(path, recover=False)
    second_store = WorkflowStore(path, recover=False)
    first_key = str((tmp_path / "a.txt").resolve(strict=False))
    second_key = str((tmp_path / "b.txt").resolve(strict=False))
    first_claims = (
        ResourceClaim("fs", first_key, "write", "exact"),
        ResourceClaim("fs", second_key, "write", "exact"),
    )
    reversed_claims = tuple(reversed(first_claims))

    first = first_store.reserve_resource_claims(
        request_id="multi-1",
        owner_token="multi-owner-1",
        claims=first_claims,
        category="stress",
    )
    second = second_store.reserve_resource_claims(
        request_id="multi-2",
        owner_token="multi-owner-2",
        claims=reversed_claims,
        category="stress",
    )
    assert first["admitted"] is True
    assert second["admitted"] is False
    assert second_store.resource_request("multi-2")["state"] == "pending"
    first_store.release_resource_request("multi-1", owner_token="multi-owner-1")
    retried = second_store.reserve_resource_claims(
        request_id="multi-2",
        owner_token="multi-owner-2",
        claims=reversed_claims,
        category="stress",
    )
    assert retried["admitted"] is True
    assert len(second_store.resource_request("multi-2")["claims"]) == 2


def test_mixed_resource_claim_pressure_has_finite_progress(tmp_path: Path) -> None:
    path = tmp_path / "workflows.sqlite3"
    WorkflowStore(path, recover=False)
    keys = [str((tmp_path / f"resource-{index}.txt").resolve(strict=False)) for index in range(4)]

    def run_request(index: int) -> bool:
        store = WorkflowStore(path, recover=False)
        request_id = f"pressure-{index}"
        owner_token = f"pressure-owner-{index}"
        mode: Literal["read", "write"] = "write" if index % 3 == 0 else "read"
        claim = ResourceClaim("fs", keys[index % len(keys)], mode, "exact")
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            result = store.reserve_resource_claims(
                request_id=request_id,
                owner_token=owner_token,
                claims=(claim,),
                category="pressure",
            )
            if result["admitted"] is True:
                time.sleep(0.001)
                store.release_resource_request(request_id, owner_token=owner_token)
                return True
            time.sleep(0.002)
        store.release_resource_request(request_id, owner_token=owner_token)
        return False

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(run_request, range(200)))

    assert all(results)


def test_health_summary_reports_resource_and_capacity_pressure(tmp_path: Path) -> None:
    store = WorkflowStore(tmp_path / "workflows.sqlite3", recover=False)
    claim = ResourceClaim("fs", str((tmp_path / "health.txt").resolve(strict=False)), "write", "exact")
    assert store.reserve_resource_claims(
        request_id="health-resource",
        owner_token="health-owner",
        claims=(claim,),
        category="health",
    )["admitted"] is True
    store.quarantine_resource_request("health-resource", owner_token="health-owner", reason="health-test")
    assert store.reserve_capacity(
        admission_id="health-analyzer",
        category="analyzer",
        owner_token="health-capacity-owner",
        weight=1,
        limit=2,
    )["admitted"] is True

    summary = store.health_summary()
    assert summary["quarantined_resource_request_count"] == 1
    assert summary["active_analyzer_capacity_weight"] == 1
    assert summary["runtime_capacity_counts"]["analyzer:active"] == 1
    assert summary["oldest_quarantined_resources"][0]["reason"] == "health-test"


def test_expired_workflow_lease_quarantines_active_resources(tmp_path: Path) -> None:
    path = tmp_path / "workflows.sqlite3"
    store = WorkflowStore(path, recover=False)
    created = store.create(
        WorkflowDefinition("one", (StepDefinition("check", "check_file", {"path": str(tmp_path)}),)),
        initial_state=WorkflowState.QUEUED,
    )
    lease = store.acquire_lease(created["workflow_id"], "worker", 30, allow_reentry=False)
    store.transition(
        created["workflow_id"],
        created["version"],
        WorkflowState.RUNNING,
        lease_token=lease.lease_token,
    )
    claim = ResourceClaim("fs", str((tmp_path / "target.txt").resolve(strict=False)), "write", "exact")
    admitted = store.reserve_resource_claims(
        request_id="active-resource",
        owner_token="resource-owner",
        claims=(claim,),
        category="workflow_step",
        workflow_id=created["workflow_id"],
    )
    assert admitted["admitted"] is True

    with sqlite3.connect(path) as connection:
        connection.execute(
            "UPDATE workflow_leases SET expires_at = '2000-01-01T00:00:00+00:00' WHERE workflow_id = ?",
            (created["workflow_id"],),
        )
        connection.commit()

    recovered = WorkflowStore(path)
    resource = recovered.resource_request("active-resource")
    assert resource["state"] == "quarantined"
    assert resource["reason"] == "workflow_lease_lost_before_resource_reconciliation"
    blocked = recovered.reserve_resource_claims(
        request_id="conflicting-resource",
        owner_token="other-owner",
        claims=(claim,),
        category="workflow_step",
    )
    assert blocked["admitted"] is False
    assert blocked["reason"] == "resource_quarantined"
    assert recovered.release_resource_request("active-resource", owner_token="resource-owner") is False
