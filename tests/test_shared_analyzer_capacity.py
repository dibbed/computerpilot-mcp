from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from tools.testing import verification


@pytest.mark.parametrize("storage_failure", [False, True])
def test_cleanup_denial_fences_parent_even_if_quarantine_storage_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, storage_failure: bool,
) -> None:
    from core.errors import ToolError
    from core.resource_claims import managed_resource_claims, path_claim
    from core.runtime_capacity import acquire_runtime_capacity
    from core.workflow_store import WorkflowStore

    store = WorkflowStore(tmp_path / "workflows.sqlite3", recover=False)
    claim = path_claim(tmp_path / "target", "write")

    def deny_quarantine(*args: object, **kwargs: object) -> None:
        raise sqlite3.OperationalError("disk unavailable")

    if storage_failure:
        monkeypatch.setattr(store, "quarantine_capacity", deny_quarantine, raising=False)
    with managed_resource_claims((claim,), category="parent", store=store):
        permit = acquire_runtime_capacity(
            store.path, category="analyzer", limit=1, deadline=time.monotonic() + 1, store=store,
        )
        assert permit is not None
        with pytest.raises((ToolError, sqlite3.OperationalError)), permit:
            raise ToolError("process_cleanup_incomplete", "cleanup denied")

    result = store.reserve_resource_claims(request_id="other", owner_token="other", category="test", claims=(claim,))
    assert result["admitted"] is False
    assert result["reason"] == "resource_quarantined"
    counts = store.health_summary()["runtime_capacity_counts"]
    assert counts["analyzer:active" if storage_failure else "analyzer:quarantined"] == 1


def test_parent_cannot_release_while_analyzer_permit_is_still_active(tmp_path: Path) -> None:
    from core.resource_claims import managed_resource_claims, path_claim
    from core.runtime_capacity import acquire_runtime_capacity
    from core.workflow_store import WorkflowStore

    store = WorkflowStore(tmp_path / "workflows.sqlite3", recover=False)
    claim = path_claim(tmp_path / "target", "write")
    with managed_resource_claims((claim,), category="parent", store=store):
        permit = acquire_runtime_capacity(
            store.path, category="analyzer", limit=1, deadline=time.monotonic() + 1, store=store,
        )
        assert permit is not None
    assert store.reserve_resource_claims(
        request_id="other", owner_token="other", category="test", claims=(claim,),
    )["admitted"] is False
    permit.release()
    assert store.health_summary()["quarantined_resource_request_count"] == 1


def test_process_capacity_cleanup_fences_reused_pid(tmp_path: Path) -> None:
    import os

    import psutil

    from core.runtime_capacity import acquire_runtime_capacity

    permit = acquire_runtime_capacity(
        tmp_path / "workflows.sqlite3", category="analyzer", limit=1, deadline=time.monotonic() + 1,
    )
    assert permit is not None
    with permit:
        created = psutil.Process().create_time()
        assert permit.store.release_process_capacity(os.getpid(), created + 1) == 0
        assert permit.store.health_summary()["active_analyzer_capacity_weight"] == 1
        assert permit.store.release_process_capacity(os.getpid(), created) == 1
        assert permit.store.release_process_capacity(os.getpid(), created) == 0


def test_analyzer_capacity_uses_parent_resource_authority(tmp_path: Path) -> None:
    from core.resource_claims import managed_resource_claims, path_claim
    from core.runtime_capacity import acquire_runtime_capacity
    from core.workflow_store import WorkflowStore

    parent = WorkflowStore(tmp_path / "parent" / "workflows.sqlite3", recover=False)
    unrelated = tmp_path / "unrelated" / "workflows.sqlite3"
    with managed_resource_claims(
        (path_claim(tmp_path, "write", scope="subtree"),), category="parent", store=parent,
    ):
        permit = acquire_runtime_capacity(unrelated, category="analyzer", limit=1, deadline=time.monotonic() + 1)
        assert permit is not None
        with permit:
            assert parent.health_summary()["active_analyzer_capacity_weight"] == 1
        assert parent.health_summary()["active_analyzer_capacity_weight"] == 0
    assert not unrelated.exists()


def test_parallel_analyzers_inherit_parent_resource_capability(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from core.resource_claims import current_resource_capability, managed_resource_claims, path_claim
    from core.workflow_store import WorkflowStore

    (tmp_path / "a.py").write_text("value = 1\n", encoding="utf-8")
    inherited: list[str | None] = []

    def runner(*args: object, **kwargs: object) -> dict[str, object]:
        inherited.append(current_resource_capability())
        return {"ok": True, "exit_code": 0, "diagnostics": []}

    monkeypatch.setattr(verification, "run_ruff_summary", runner)
    monkeypatch.setattr(verification, "run_mypy_summary", runner)
    monkeypatch.setattr(verification, "run_pytest_summary", runner)
    with managed_resource_claims(
        (path_claim(tmp_path, "write", scope="subtree"),), category="parent",
        store=WorkflowStore(tmp_path / "workflows.sqlite3", recover=False),
    ) as capability:
        result = verification.verify_changed_repository(
            tmp_path, changed_paths=["a.py"], checks=["ruff", "mypy", "pytest"],
            affected_only=False, fail_fast=False, max_parallel_checks=3,
        )
    assert result["ok"] is True
    assert inherited == [capability] * 3


def test_parallel_verifications_share_durable_analyzer_capacity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.py").write_text("value = 1\n", encoding="utf-8")
    capacity_db = tmp_path / "capacity.sqlite3"
    lock = threading.Lock()
    active = 0
    peak = 0

    def fake_runner(*args: object, **kwargs: object) -> dict[str, object]:
        nonlocal active, peak
        del args, kwargs
        with lock:
            active += 1
            peak = max(peak, active)
        try:
            time.sleep(0.05)
            return {
                "ok": True,
                "exit_code": 0,
                "timed_out": False,
                "summary": "ok",
                "diagnostics": [],
            }
        finally:
            with lock:
                active -= 1

    monkeypatch.setattr(verification, "run_ruff_summary", fake_runner)
    monkeypatch.setattr(verification, "run_mypy_summary", fake_runner)
    monkeypatch.setattr(verification, "run_pytest_summary", fake_runner)

    def run_one() -> dict[str, object]:
        return verification.verify_changed_repository(
            repo,
            changed_paths=["a.py"],
            checks=["ruff", "mypy", "pytest"],
            affected_only=False,
            fail_fast=False,
            total_timeout_sec=5,
            max_parallel_checks=3,
            analyzer_capacity_db=capacity_db,
            analyzer_capacity_limit=2,
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        first, second = list(pool.map(lambda _: run_one(), range(2)))

    assert first["ok"] is True
    assert second["ok"] is True
    assert peak == 2


def test_analyzer_capacity_is_enforced_across_os_processes(tmp_path: Path) -> None:
    db_path = tmp_path / "capacity.sqlite3"
    release_marker = tmp_path / "release.marker"
    repo_root = Path(__file__).resolve().parents[1]
    script = r'''
import json
import sys
import time
from pathlib import Path
from core.workflow_store import WorkflowStore

store = WorkflowStore(Path(sys.argv[1]), recover=False)
admission_id, owner_token = sys.argv[2], sys.argv[3]
release_marker = Path(sys.argv[4])
wait_for_release = sys.argv[5] == "wait"
result = store.reserve_capacity(
    admission_id=admission_id,
    category="analyzer",
    owner_token=owner_token,
    weight=1,
    limit=1,
)
print(json.dumps(result), flush=True)
if result["admitted"]:
    if wait_for_release:
        deadline = time.monotonic() + 10
        while not release_marker.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
    store.release_capacity(admission_id, owner_token=owner_token)
'''

    first = subprocess.Popen(
        [sys.executable, "-c", script, str(db_path), "analyzer-1", "owner-1", str(release_marker), "wait"],
        cwd=repo_root,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert first.stdout is not None
    assert json.loads(first.stdout.readline())["admitted"] is True

    second = subprocess.run(
        [sys.executable, "-c", script, str(db_path), "analyzer-2", "owner-2", str(release_marker), "nowait"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        timeout=10,
        check=True,
    )
    second_result = json.loads(second.stdout.strip())
    assert second_result["admitted"] is False
    assert second_result["reason"] == "capacity_exhausted"

    release_marker.write_text("release", encoding="utf-8")
    assert first.wait(timeout=10) == 0
    third = subprocess.run(
        [sys.executable, "-c", script, str(db_path), "analyzer-2", "owner-2", str(release_marker), "nowait"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        timeout=10,
        check=True,
    )
    assert json.loads(third.stdout.strip())["admitted"] is True
