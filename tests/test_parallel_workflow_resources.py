from __future__ import annotations

import hashlib
import json
import os
import shlex
import sqlite3
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from functools import partial
from pathlib import Path
from types import SimpleNamespace
from typing import Literal

import pytest

from core import config, resource_claims
from core.errors import ToolError
from core.resource_claims import (
    ResourceClaim,
    claims_conflict,
    make_resource_capability,
    managed_path_claims,
    managed_resource_claims,
    path_claim,
    resource_capability,
    validate_resource_capability,
)
from core.workflow_models import StepDefinition, WorkflowDefinition, WorkflowState
from core.workflow_store import WorkflowStore


def test_sequential_verifier_deducts_admission_wait_from_deadline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from core import workflow_actions

    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.setattr(config, "SETTINGS", replace(config.SETTINGS, state_dir=tmp_path))
    monkeypatch.setattr(workflow_actions, "SETTINGS", config.SETTINGS)
    store = WorkflowStore(tmp_path / "workflows.sqlite3", recover=False)
    store.reserve_resource_claims(
        request_id="reader", owner_token="reader", category="test",
        claims=(path_claim(repo, "read", scope="subtree"),),
    )
    observed: list[tuple[float, float]] = []
    start = time.monotonic()

    def fake_verify(*args: object, **kwargs: object) -> dict[str, object]:
        value = kwargs["total_timeout_sec"]
        assert isinstance(value, (int, float))
        observed.append((time.monotonic() - start, float(value)))
        return {"ok": True}

    monkeypatch.setattr(workflow_actions, "verify_changed_repository", fake_verify)
    release = threading.Timer(
        0.15, lambda: store.release_resource_request("reader", owner_token="reader"),
    )
    release.start()
    try:
        assert workflow_actions._verify_changes({"cwd": str(repo)}, timeout_sec=0.8)["ok"] is True
    finally:
        release.join(timeout=2)
    assert len(observed) == 1
    elapsed_to_verifier, allotted = observed[0]
    assert elapsed_to_verifier >= 0.1
    assert allotted <= 0.8 - elapsed_to_verifier + 0.04
    assert allotted > 0


def test_sequential_workflow_verification_obeys_repository_claims(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from core import workflow_actions

    repo = tmp_path / "repo"
    repo.mkdir()
    db_path = tmp_path / "workflows.sqlite3"
    monkeypatch.setattr(config, "SETTINGS", replace(config.SETTINGS, state_dir=tmp_path))
    monkeypatch.setattr(workflow_actions, "SETTINGS", config.SETTINGS)
    store = WorkflowStore(db_path, recover=False)
    store.reserve_resource_claims(
        request_id="reader", owner_token="reader", category="test",
        claims=(path_claim(repo, "read", scope="subtree"),),
    )
    ran = []

    def fake_verify(*args: object, **kwargs: object) -> dict[str, object]:
        ran.append(True)
        return {"ok": True}

    monkeypatch.setattr(workflow_actions, "verify_changed_repository", fake_verify)
    with pytest.raises(ToolError) as error:
        workflow_actions._verify_changes(
            {"cwd": str(repo), "ruff_fix": True}, timeout_sec=0.06,
        )
    assert error.value.code == "resource_admission_timeout"
    assert not ran


@pytest.mark.parametrize("category", ["resource", "capacity"])
def test_admission_record_quota_preserves_unresolved_fences(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, category: str,
) -> None:
    monkeypatch.setattr(config, "SETTINGS", replace(config.SETTINGS, max_runtime_admission_records=3))
    store = WorkflowStore(tmp_path / "workflows.sqlite3", recover=False)

    def reserve(index: int) -> dict[str, object]:
        if category == "capacity":
            return store.reserve_capacity(
                admission_id=str(index), category="test", owner_token="owner", weight=1, limit=10,
            )
        return store.reserve_resource_claims(
            request_id=str(index), category="test", owner_token="owner",
            claims=(path_claim(tmp_path / str(index), "write"),),
        )

    for index in range(3):
        assert reserve(index)["admitted"] is True
    with pytest.raises(ToolError) as raised:
        reserve(3)
    assert raised.value.code == "runtime_admission_storage_exhausted"
    table = "runtime_capacity_requests" if category == "capacity" else "workflow_resource_requests"
    with sqlite3.connect(store.path) as db:
        assert db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 3
        db.execute(f"UPDATE {table} SET state='quarantined' WHERE ticket=1")
    if category == "capacity":
        store.release_capacity("1", owner_token="owner")
    else:
        store.release_resource_request("1", owner_token="owner")
    assert reserve(3)["admitted"] is True
    with sqlite3.connect(store.path) as db:
        assert db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 3
        assert db.execute(f"SELECT state FROM {table} WHERE ticket=1").fetchone()[0] == "quarantined"


def test_resource_claim_set_has_a_byte_budget_before_persistence(tmp_path: Path) -> None:
    store = WorkflowStore(tmp_path / "workflows.sqlite3", recover=False)
    with pytest.raises(ToolError) as raised:
        store.reserve_resource_claims(
            request_id="oversize", owner_token="owner", category="test",
            claims=tuple(ResourceClaim("http", str(index) + "x" * 3990, "read") for index in range(20)),
        )
    assert raised.value.code == "resource_claims_too_large"
    assert store.health_summary()["active_resource_request_count"] == 0


def _workflow_capability(tmp_path: Path) -> tuple[WorkflowStore, str, str, str]:
    store = WorkflowStore(tmp_path / "workflows.sqlite3", recover=False)
    workflow = store.create(
        WorkflowDefinition("owner", (StepDefinition("check", "check_file", {"path": str(tmp_path)}),)),
        initial_state=WorkflowState.QUEUED,
    )
    workflow_id = str(workflow["workflow_id"])
    lease = store.acquire_lease(workflow_id, "worker", 30, allow_reentry=False)
    store.transition(workflow_id, int(workflow["version"]), WorkflowState.RUNNING, lease_token=lease.lease_token)
    operation_id = str(store.list_operations(workflow_id)["items"][0]["operation_id"])
    attempt = store.claim_ready_operation(
        operation_id, lease_token=lease.lease_token, owner_id="worker", execution_id="execution", global_limit=1,
    )
    assert attempt is not None
    store.reserve_resource_claims(
        request_id="parent", owner_token="owner", category="workflow_step", workflow_id=workflow_id,
        operation_id=operation_id, claims=(path_claim(tmp_path, "write", scope="subtree"),),
    )
    store.bind_resource_request_attempt("parent", owner_token="owner", attempt_token=str(attempt["attempt_token"]))
    capability = make_resource_capability("parent", "owner", store.path)
    return store, workflow_id, capability, lease.lease_token


@pytest.mark.parametrize("stale", ["lease", "attempt", "cancel", "completed"])
def test_workflow_capability_revalidates_live_attempt_before_nested_dispatch(tmp_path: Path, stale: str) -> None:
    store, _, capability, _ = _workflow_capability(tmp_path)
    validate_resource_capability(capability, (), store)
    with sqlite3.connect(store.path) as db:
        if stale == "lease":
            db.execute("UPDATE workflow_leases SET expires_at='2000-01-01T00:00:00+00:00'")
        elif stale == "attempt":
            db.execute("UPDATE workflow_operations SET attempt_token='replacement'")
        elif stale == "cancel":
            db.execute("UPDATE workflows SET cancel_requested_at='now'")
        else:
            db.execute("DELETE FROM workflow_execution_admissions")
    with pytest.raises(ToolError) as raised:
        validate_resource_capability(capability, (), store)
    assert raised.value.code == "resource_capability_invalid"
    assert store.resource_request("parent")["state"] == "active"
    with pytest.raises(ToolError) as capacity_error:
        store.reserve_capacity(
            admission_id="late", category="analyzer", owner_token="child", weight=1, limit=1,
            resource_request_id="parent", resource_owner_token="owner",
        )
    assert capacity_error.value.code == "resource_capability_invalid"


@pytest.mark.parametrize("crossed_barrier", [False, True])
def test_workflow_children_require_dispatch_barrier_and_native_ownership(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, crossed_barrier: bool,
) -> None:
    from core.executor import run_bounded
    from core.windows_job import NativeOwnershipRequiredError

    store, workflow_id, capability, lease_token = _workflow_capability(tmp_path)
    if crossed_barrier:
        operation = store.list_operations(workflow_id)["items"][0]
        prepared = store.prepare_operation_attempt(
            operation["operation_id"], expected_version=operation["version"], attempt_token=operation["attempt_token"],
            lease_token=lease_token, intent_evidence={}, postcondition=None,
        )
        store.mark_operation_may_dispatch(
            operation["operation_id"], expected_version=prepared["version"], attempt_token=operation["attempt_token"],
            lease_token=lease_token,
        )
    monkeypatch.setenv("MCP_WINDOWS_JOB_OBJECTS", "0")
    monkeypatch.setenv("MCP_POSIX_PROCESS_GROUPS", "0")
    marker = tmp_path / "effect.txt"
    expected = NativeOwnershipRequiredError if crossed_barrier else ToolError
    with resource_capability(capability), pytest.raises(expected):
        run_bounded(
            [sys.executable, "-c", f"from pathlib import Path; Path({str(marker)!r}).write_text('effect')"],
            cwd=tmp_path, timeout_sec=2,
        )
    assert not marker.exists()


def test_managed_native_command_refuses_unowned_fallback_before_effect(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from core.executor import run_bounded
    from core.windows_job import NativeOwnershipRequiredError

    monkeypatch.setenv("MCP_WINDOWS_JOB_OBJECTS", "0")
    monkeypatch.setenv("MCP_POSIX_PROCESS_GROUPS", "0")
    store = WorkflowStore(tmp_path / "workflows.sqlite3", recover=False)
    marker = tmp_path / "effect.txt"
    with managed_resource_claims((path_claim(tmp_path, "write", scope="subtree"),), category="native", store=store):
        with pytest.raises(NativeOwnershipRequiredError):
            run_bounded(
                [sys.executable, "-c", f"from pathlib import Path; Path({str(marker)!r}).write_text('effect')"],
                cwd=tmp_path, timeout_sec=2,
            )
    assert not marker.exists()
    assert store.health_summary()["active_resource_request_count"] == 0


@pytest.mark.parametrize("unavailable", ["reused", "denied", "zombie"])
def test_native_owner_reuse_or_inspection_denial_preserves_the_fence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, unavailable: str,
) -> None:
    import psutil

    store = WorkflowStore(tmp_path / "workflows.sqlite3", recover=False)
    created = psutil.Process().create_time() - (1 if unavailable == "reused" else 0)
    pid = os.getpid() + (1 if unavailable == "zombie" else 0)
    owner = f"native-owner-{pid}-{created:.6f}-0123456789abcdef"
    claim = path_claim(tmp_path / "target", "write")
    store.reserve_resource_claims(request_id="native", owner_token=owner, category="native", claims=(claim,))

    def denied(*args: object, **kwargs: object) -> None:
        raise psutil.AccessDenied()

    if unavailable == "denied":
        monkeypatch.setattr(psutil, "Process", denied)
    elif unavailable == "zombie":
        from core import jobs

        monkeypatch.setattr(jobs, "same_process", lambda *args: True)
        monkeypatch.setattr(psutil, "Process", lambda *args: SimpleNamespace(status=lambda: psutil.STATUS_ZOMBIE))
    with pytest.raises(ToolError) as raised:
        validate_resource_capability(make_resource_capability("native", owner, store.path), (), store)
    assert raised.value.code == "resource_capability_invalid"
    store.recover_interrupted()
    assert store.resource_request("native")["state"] == "quarantined"
    assert store.reserve_resource_claims(
        request_id="other", owner_token="other", category="test", claims=(claim,),
    )["admitted"] is False


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


def test_older_writer_fences_late_readers_without_blocking_independent_work(tmp_path: Path) -> None:
    store = WorkflowStore(tmp_path / "workflows.sqlite3", recover=False)
    target = tmp_path / "data"

    def reserve(name: str, path: Path, mode: Literal["read", "write"]) -> dict[str, object]:
        return store.reserve_resource_claims(
            request_id=name, owner_token=name, category="test", claims=(path_claim(path, mode),),
        )

    assert reserve("reader", target, "read")["admitted"] is True
    assert reserve("writer", target, "write")["admitted"] is False
    assert reserve("late-reader", target, "read")["reason"] == "older_conflicting_request"
    assert reserve("independent", tmp_path / "other", "write")["admitted"] is True
    store.release_resource_request("reader", owner_token="reader")
    assert reserve("writer", target, "write")["admitted"] is True
    assert reserve("late-reader", target, "read")["admitted"] is False
    store.release_resource_request("writer", owner_token="writer")
    assert reserve("late-reader", target, "read")["admitted"] is True


def _git_repository(repo: Path) -> None:
    subprocess.run(["git", "init", "-q", str(repo)], check=True, capture_output=True)
    for key, value in {"user.name": "Fixture", "user.email": "fixture@example.invalid", "commit.gpgsign": "false",
                       "core.hooksPath": str(repo.parent / "empty-hooks")}.items():
        subprocess.run(["git", "-C", str(repo), "config", key, value], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-q", "--allow-empty", "-m", "fixture"],
        check=True, capture_output=True,
    )


def test_git_worktrees_share_the_authoritative_common_directory_claim(tmp_path: Path) -> None:
    from core.workflow_actions import derive_action_resource_claims

    repo, sibling = tmp_path / "repo", tmp_path / "sibling"
    _git_repository(repo)
    subprocess.run(["git", "-C", str(repo), "worktree", "add", "-q", "--detach", str(sibling)], check=True, capture_output=True)
    first = derive_action_resource_claims("git_stage", {"repo": str(repo), "paths": ["data"]}, {"kind": "git_stage_intent"})
    second = derive_action_resource_claims("git_stage", {"repo": str(sibling), "paths": ["data"]}, {"kind": "git_stage_intent"})
    assert {claim for claim in first if claim.kind == "git"} == {claim for claim in second if claim.kind == "git"}
    store = WorkflowStore(tmp_path / "workflows.sqlite3", recover=False)
    assert store.reserve_resource_claims(request_id="a", owner_token="a", category="test", claims=first)["admitted"] is True
    assert store.reserve_resource_claims(request_id="b", owner_token="b", category="test", claims=second)["admitted"] is False


@pytest.mark.parametrize("interface", ["workflow", "native"])
def test_git_mutations_fence_unknown_hook_and_filter_effects(tmp_path: Path, interface: str) -> None:
    from core.workflow_actions import derive_action_resource_claims
    from tools.git.registry import _git_resource_claims

    repo = tmp_path / "repo"
    _git_repository(repo)
    claims = (
        derive_action_resource_claims("git_commit", {"repo": str(repo), "message": "fixture"}, {"kind": "git_commit_intent"})
        if interface == "workflow" else _git_resource_claims(repo)
    )
    store = WorkflowStore(tmp_path / "workflows.sqlite3", recover=False)
    assert store.reserve_resource_claims(request_id="git", owner_token="git", category="test", claims=claims)["admitted"] is True
    assert store.reserve_resource_claims(
        request_id="reader", owner_token="reader", category="test",
        claims=(resource_claims.coordination_claim(), path_claim(tmp_path / "outside-repo", "read")),
    )["admitted"] is False


@pytest.mark.parametrize("interface", ["workflow", "native", "intent", "reconcile", "verification"])
def test_git_reads_do_not_activate_repository_fsmonitor_commands(tmp_path: Path, interface: str) -> None:
    from core.workflow_actions import _run_git, prepare_action_intent
    from tools.git.service import run_git

    repo = tmp_path / "repo"
    _git_repository(repo)
    (repo / "seed").write_text("fixture", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "seed"], check=True, capture_output=True)
    marker, monitor = tmp_path / "activated", tmp_path / "monitor"
    monitor.write_text(
        "#!/bin/sh\nprintf activated > " + shlex.quote(marker.as_posix()) + "\nprintf 'fixture\\0'\n", encoding="utf-8",
    )
    monitor.chmod(0o700)
    subprocess.run(["git", "-C", str(repo), "config", "core.fsmonitor", monitor.as_posix()], check=True, capture_output=True)
    control = subprocess.run(["git", "-C", str(repo), "status", "--short"], check=True, capture_output=True, text=True)
    assert marker.exists(), control.stderr
    marker.unlink()
    if interface == "workflow":
        _run_git({"repo": str(repo)}, 10, "status", "--short")
    elif interface == "native":
        run_git(repo, ["status", "--short"])
    elif interface == "intent":
        prepare_action_intent("git_commit", {"repo": str(repo), "message": "fixture"}, {"kind": "git_commit_intent"})
    elif interface == "verification":
        from tools.testing.impact import _git

        _git(repo, ["status", "--short"])
    else:
        from core.reconcilers import evaluate_postcondition
        from core.recovery_models import Postcondition

        evaluate_postcondition(Postcondition("git_index_contains", {"repo": str(repo), "paths": ["seed"]}))
    assert not marker.exists()


@pytest.mark.parametrize("interface", ["workflow", "native", "reconcile"])
def test_git_metadata_reads_do_not_activate_signature_verifiers(tmp_path: Path, interface: str) -> None:
    from core.workflow_actions import _run_git
    from tools.git.service import run_git

    repo = tmp_path / "repo"
    _git_repository(repo)

    def git(*args: str, content: str | None = None) -> str:
        result = subprocess.run(
            ["git", "-C", str(repo), *args], input=content.encode("utf-8") if content is not None else None,
            capture_output=True, check=True,
        )
        return result.stdout.decode("utf-8").strip()

    before, tree = git("rev-parse", "HEAD"), git("write-tree")
    commit = git(
        "hash-object", "-t", "commit", "-w", "--stdin",
        content=f"tree {tree}\nparent {before}\nauthor Fixture <fixture@example.invalid> 1700000000 +0000\n"
        "committer Fixture <fixture@example.invalid> 1700000000 +0000\n"
        "gpgsig -----BEGIN PGP SIGNATURE-----\n fixture\n -----END PGP SIGNATURE-----\n\nfixture\n",
    )
    git("update-ref", "HEAD", commit, before)
    marker, verifier = tmp_path / "activated", tmp_path / "verifier"
    verifier.write_text("#!/bin/sh\nprintf activated > " + shlex.quote(marker.as_posix()) + "\nexit 1\n", encoding="utf-8")
    verifier.chmod(0o700)
    for key, value in {"gpg.format": "openpgp", "gpg.program": verifier.as_posix(),
                       "gpg.openpgp.program": verifier.as_posix(), "log.showSignature": "true"}.items():
        git("config", key, value)
    subprocess.run(["git", "-C", str(repo), "show", "-s", "--format=%H", "HEAD"], capture_output=True, check=False)
    assert marker.exists()
    marker.unlink()
    if interface == "workflow":
        _run_git({"repo": str(repo)}, 10, "show", "-s", "--format=%H", "HEAD")
    elif interface == "native":
        run_git(repo, ["show", "-s", "--format=%H", "HEAD"])
    else:
        from core.reconcilers import evaluate_postcondition
        from core.recovery_models import Postcondition

        evaluate_postcondition(Postcondition("git_commit_effect", {
            "repo": str(repo), "before_head": before, "staged_tree": tree,
            "message_sha256": hashlib.sha256(b"fixture").hexdigest(),
        }))
    assert not marker.exists()


@pytest.mark.parametrize("interface", ["workflow", "native", "reconcile", "verification"])
def test_missing_git_objects_never_activate_implicit_promisor_transport(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, interface: str,
) -> None:
    from core.workflow_actions import _run_git
    from tools.git.service import run_git

    repo = tmp_path / "repo"
    _git_repository(repo)
    head = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
    marker, transport = tmp_path / "activated", tmp_path / "transport"
    transport.write_text("#!/bin/sh\nprintf activated > " + shlex.quote(marker.as_posix()) + "\nexit 1\n", encoding="utf-8")
    transport.chmod(0o700)
    for key, value in {"core.repositoryformatversion": "1", "extensions.partialclone": "origin",
                       "remote.origin.url": "ext::" + transport.as_posix(), "remote.origin.promisor": "true",
                       "remote.origin.partialclonefilter": "blob:none", "protocol.ext.allow": "always"}.items():
        subprocess.run(["git", "-C", str(repo), "config", key, value], check=True, capture_output=True)
    missing = repo / ".git" / "objects" / head[:2] / head[2:]
    assert missing.resolve().is_relative_to(repo.resolve())
    missing.chmod(0o600)
    missing.unlink()
    monkeypatch.delenv("GIT_ALLOW_PROTOCOL", raising=False)
    monkeypatch.delenv("GIT_NO_LAZY_FETCH", raising=False)
    subprocess.run(["git", "-C", str(repo), "show", "-s", "HEAD"], capture_output=True, check=False)
    assert marker.exists()
    marker.unlink()
    if interface == "reconcile":
        from core.reconcilers import evaluate_postcondition
        from core.recovery_models import Postcondition

        result = evaluate_postcondition(Postcondition("git_commit_effect", {
            "repo": str(repo), "before_head": "0" * 40, "staged_tree": "0" * 40, "message_sha256": "0" * 64,
        }))
        assert result.data["conclusive"] is False
    else:
        with pytest.raises(ToolError):
            if interface == "workflow":
                _run_git({"repo": str(repo)}, 10, "show", "-s", "HEAD")
            elif interface == "native":
                run_git(repo, ["show", "-s", "HEAD"])
            else:
                from tools.testing.impact import _git

                _git(repo, ["show", "-s", "HEAD"])
    assert not marker.exists()


@pytest.mark.parametrize("interface", ["workflow", "native"])
@pytest.mark.parametrize("backend", ["external", "textconv"])
def test_git_diff_uses_native_text_without_executing_repository_helpers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, interface: str, backend: str,
) -> None:
    from core.workflow_actions import _run_git
    from tools.git.service import run_git

    repo = tmp_path / "repo"
    _git_repository(repo)
    target = repo / "seed.txt"
    target.write_text("before\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "seed.txt"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", "seed"], check=True, capture_output=True)
    target.write_text("after\n", encoding="utf-8")
    marker, helper = tmp_path / "activated", tmp_path / "diff-helper"
    helper.write_text("#!/bin/sh\nprintf activated > " + shlex.quote(marker.as_posix()) + "\nprintf fixture\n", encoding="utf-8")
    helper.chmod(0o700)
    if backend == "external":
        monkeypatch.setenv("GIT_EXTERNAL_DIFF", helper.as_posix())
    else:
        (repo / ".gitattributes").write_text("seed.txt diff=fixture\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(repo), "config", "diff.fixture.textconv", helper.as_posix()], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "diff"], check=True, capture_output=True)
    assert marker.exists()
    marker.unlink()
    output = (
        _run_git({"repo": str(repo)}, 10, "diff")["stdout"]
        if interface == "workflow" else run_git(repo, ["diff"])["stdout"]["text"]
    )
    assert not marker.exists()
    assert "diff --git" in output


def test_workflow_git_mutation_reaps_hook_descendants_before_return(tmp_path: Path) -> None:
    import psutil

    from core.workflow_actions import _run_git

    repo = tmp_path / "repo"
    _git_repository(repo)
    hooks = tmp_path / "hooks"
    hooks.mkdir()
    for key, value in {"user.name": "Fixture", "user.email": "fixture@example.invalid", "commit.gpgsign": "false",
                       "core.hooksPath": str(hooks)}.items():
        subprocess.run(["git", "-C", str(repo), "config", key, value], check=True, capture_output=True)
    identity = tmp_path / "child.json"
    child = (
        "import json,os,time,psutil;from pathlib import Path;"
        f"Path({str(identity)!r}).write_text(json.dumps({{'pid':os.getpid(),'created':psutil.Process().create_time()}}));"
        "time.sleep(60)"
    )
    parent = (
        "import subprocess,sys,time;from pathlib import Path;"
        f"subprocess.Popen([sys.executable,'-c',{child!r}],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL);"
        "deadline=time.monotonic()+5\n"
        f"while not Path({str(identity)!r}).exists() and time.monotonic()<deadline: time.sleep(.01)\n"
        f"assert Path({str(identity)!r}).exists()"
    )
    hook = hooks / "post-commit"
    hook.write_text("#!/bin/sh\n" + shlex.quote(sys.executable.replace("\\", "/")) + " -c " + shlex.quote(parent) + "\n", encoding="utf-8")
    hook.chmod(0o700)
    store = WorkflowStore(tmp_path / "workflows.sqlite3", recover=False)
    try:
        with managed_resource_claims(
            (resource_claims.coordination_claim(exclusive=True), path_claim(repo, "write", scope="subtree")),
            category="git-test", store=store,
        ):
            result = _run_git({"repo": str(repo)}, 15, "commit", "--allow-empty", "-m", "effect", uncertain_on_timeout=True)
        assert result["exit_code"] == 0
        record = json.loads(identity.read_text(encoding="utf-8"))
        try:
            process = psutil.Process(record["pid"])
            alive = abs(process.create_time() - record["created"]) < .01 and process.status() != psutil.STATUS_ZOMBIE
        except psutil.NoSuchProcess:
            alive = False
        if alive:
            # Native Job Object termination can precede psutil's observable
            # process-table retirement. Poll the exact PID/create-time briefly;
            # a leaked descendant still fails after the bounded grace.
            observed = []
            for _ in range(24):
                time.sleep(0.1)
                try:
                    candidate = psutil.Process(record["pid"])
                    alive = (
                        abs(candidate.create_time() - record["created"]) < .01
                        and candidate.status() != psutil.STATUS_ZOMBIE
                    )
                    observed.append((candidate.status(), candidate.ppid()))
                except psutil.NoSuchProcess:
                    alive = False
                    break
                if not alive:
                    break
            assert not alive, f"child remained alive after 2.4s: {observed[-5:]}"
    finally:
        if identity.exists():
            record = json.loads(identity.read_text(encoding="utf-8"))
            try:
                process = psutil.Process(record["pid"])
                if abs(process.create_time() - record["created"]) < .01:
                    process.kill()
                    process.wait(timeout=5)
            except psutil.NoSuchProcess:
                pass


def test_alias_changed_during_claim_wait_is_rejected_before_writing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    first, second, alias = tmp_path / "first", tmp_path / "second", tmp_path / "alias"
    for directory in (first, second):
        directory.mkdir()
        (directory / "data").write_text("before", encoding="utf-8")

    def point_to(target: Path) -> None:
        if os.name == "nt":
            if alias.exists():
                alias.rmdir()
            subprocess.run(
                ["cmd.exe", "/d", "/c", "mklink", "/J", str(alias), str(target)], check=True, capture_output=True,
            )
        else:
            alias.unlink(missing_ok=True)
            alias.symlink_to(target, target_is_directory=True)

    point_to(first)
    store = WorkflowStore(tmp_path / "workflows.sqlite3", recover=False)
    store.reserve_resource_claims(
        request_id="reader", owner_token="reader", category="test", claims=(path_claim(first / "data", "read"),),
    )
    monkeypatch.setattr(resource_claims, "managed_resource_claims", partial(managed_resource_claims, store=store))

    def write() -> None:
        with managed_path_claims(alias / "data", category="test", timeout_sec=5):
            (alias / "data").write_text("after", encoding="utf-8")

    with ThreadPoolExecutor(1) as pool:
        future = pool.submit(write)
        try:
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                if store.health_summary()["pending_resource_request_count"]:
                    break
                time.sleep(0.01)
            assert store.health_summary()["pending_resource_request_count"] == 1
            point_to(second)
        finally:
            store.release_resource_request("reader", owner_token="reader")
        with pytest.raises(ToolError) as raised:
            future.result(timeout=5)
        assert raised.value.code == "resource_path_changed"
    assert (first / "data").read_text(encoding="utf-8") == "before"
    assert (second / "data").read_text(encoding="utf-8") == "before"


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
    with pytest.raises(ToolError, match="quarantine"):
        with managed_resource_claims((claim,), category="test", store=store, quarantine_on_error=True):
            raise PermissionError("cleanup denied")

    assert store.reserve_resource_claims(
        request_id="conflict", owner_token="other", category="test", claims=(claim,),
    )["admitted"] is False


def test_inherited_cleanup_storage_failure_preserves_parent_fence(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = WorkflowStore(tmp_path / "workflows.sqlite3", recover=False)
    claim = path_claim(tmp_path / "target", "write")

    def denied(*args: object, **kwargs: object) -> None:
        raise sqlite3.OperationalError("private-storage-canary")

    monkeypatch.setattr(store, "quarantine_resource_request", denied)
    with pytest.raises(ToolError) as failure:
        with managed_resource_claims((claim,), category="parent", store=store):
            with managed_resource_claims((claim,), category="child", store=store):
                raise ToolError("process_cleanup_incomplete", "child cleanup denied")
    assert "private-storage-canary" not in failure.value.message
    assert store.reserve_resource_claims(
        request_id="conflict", owner_token="other", category="test", claims=(claim,),
    )["admitted"] is False


def test_managed_claims_report_quarantine_without_waiting_or_releasing_fence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = WorkflowStore(tmp_path / "workflows.sqlite3", recover=False)
    claim = path_claim(tmp_path / "target", "write")
    store.reserve_resource_claims(request_id="blocked", owner_token="owner", category="test", claims=(claim,))
    store.quarantine_resource_request("blocked", owner_token="owner", reason="cleanup_unresolved")
    monkeypatch.setattr(resource_claims.time, "sleep", lambda _: pytest.fail("waited on unresolved quarantine"))

    with pytest.raises(ToolError) as raised:
        with managed_resource_claims((claim,), category="test", store=store):
            pytest.fail("dispatched through quarantine")

    assert raised.value.code == "resource_quarantined"
    assert store.resource_request("blocked")["state"] == "quarantined"
    summary = store.health_summary()
    assert summary["pending_resource_request_count"] == 0


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


@pytest.mark.parametrize("entrypoint", ["scope", "process", "capacity"])
def test_inherited_capability_rejects_incompatible_runtime_policy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, entrypoint: str,
) -> None:
    from core.executor import run_bounded

    store = WorkflowStore(tmp_path / "workflows.sqlite3", recover=False)
    with managed_resource_claims((path_claim(tmp_path, "write", scope="subtree"),), category="parent", store=store) as capability:
        monkeypatch.setattr(config, "SETTINGS", replace(config.SETTINGS, max_running_analyzers=config.SETTINGS.max_running_analyzers + 1))
        with pytest.raises(ToolError) as failure:
            if entrypoint == "scope":
                validate_resource_capability(capability, (), store)
            elif entrypoint == "capacity":
                request_id, owner, _ = resource_claims._parse_resource_capability(capability)
                store.reserve_capacity(
                    admission_id="denied", category="analyzer", owner_token="child", weight=1, limit=3,
                    resource_request_id=request_id, resource_owner_token=owner,
                )
            else:
                run_bounded(
                    [sys.executable, "-c", f"from pathlib import Path; Path({str(tmp_path / 'effect')!r}).touch()"],
                    cwd=tmp_path, timeout_sec=1,
                )
        assert failure.value.code == "resource_capability_invalid"
    assert not (tmp_path / "effect").exists()
    assert store.health_summary()["runtime_capacity_counts"] == {}


def test_managed_success_cannot_hide_unresolved_child_capacity(tmp_path: Path) -> None:
    store = WorkflowStore(tmp_path / "workflows.sqlite3", recover=False)
    with pytest.raises(ToolError) as raised:
        with managed_resource_claims((path_claim(tmp_path, "write"),), category="parent", store=store) as capability:
            request, owner, _ = resource_claims._parse_resource_capability(capability)
            assert store.reserve_capacity(
                admission_id="unresolved", category="analyzer", owner_token="unknown-owner", weight=1, limit=1,
                resource_request_id=request, resource_owner_token=owner,
            )["admitted"] is True
    assert raised.value.code == "process_cleanup_incomplete"
    assert store.health_summary()["quarantined_resource_request_count"] == 1
    assert store.health_summary()["active_analyzer_capacity_weight"] == 1


def test_older_incompatible_policy_waiter_drains_active_profile_without_starvation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = WorkflowStore(tmp_path / "workflows.sqlite3", recover=False)
    original = config.SETTINGS

    def reserve(name: str) -> dict[str, object]:
        return store.reserve_resource_claims(
            request_id=name, owner_token=name, category="test", claims=(path_claim(tmp_path / name, "write"),),
        )

    assert reserve("active-a")["admitted"] is True
    monkeypatch.setattr(config, "SETTINGS", replace(original, max_running_analyzers=original.max_running_analyzers + 1))
    assert reserve("older-b")["reason"] == "resource_policy_incompatible"
    monkeypatch.setattr(config, "SETTINGS", original)
    assert reserve("newer-a")["admitted"] is False
    store.release_resource_request("active-a", owner_token="active-a")
    monkeypatch.setattr(config, "SETTINGS", replace(original, max_running_analyzers=original.max_running_analyzers + 1))
    assert reserve("older-b")["admitted"] is True
    store.release_resource_request("older-b", owner_token="older-b")
    monkeypatch.setattr(config, "SETTINGS", original)
    assert reserve("newer-a")["admitted"] is True


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


@pytest.mark.parametrize("different_policy", [False, True])
@pytest.mark.parametrize(
    ("first_mode", "second_mode", "subtree", "independent", "overlap"),
    [("write", "write", False, False, False), ("read", "write", False, False, False),
     ("read", "read", False, False, True), ("read", "write", True, False, False),
     ("write", "read", True, False, False), ("write", "write", True, True, True)],
)
def test_resource_claim_conflict_is_enforced_across_os_processes(
    tmp_path: Path, first_mode: Literal["read", "write"], second_mode: Literal["read", "write"],
    subtree: bool, independent: bool, overlap: bool, different_policy: bool,
) -> None:
    overlap = overlap and not different_policy
    db_path = tmp_path / "workflows.sqlite3"
    root = tmp_path / "repo"
    first_claim = path_claim(root if subtree else root / "shared.txt", first_mode, scope="subtree" if subtree else "exact")
    target = tmp_path / "repo2" / "shared.txt" if independent else root / "." / "shared.txt"
    second_claim = path_claim(target, second_mode)
    repo_root = Path(__file__).resolve().parents[1]
    script = r'''
import json,sys,time
from pathlib import Path
from core.resource_claims import ResourceClaim
from core.workflow_store import WorkflowStore
store=WorkflowStore(Path(sys.argv[1]),recover=False)
claim=ResourceClaim(**json.loads(sys.argv[2]))
request_id=sys.argv[3]
result=store.reserve_resource_claims(request_id=request_id,owner_token=request_id,claims=(claim,),category="process-test")
print(json.dumps(result),flush=True)
if result["admitted"]:
    deadline=time.monotonic()+10
    while sys.argv[4] != "-" and not Path(sys.argv[4]).exists() and time.monotonic()<deadline:
        time.sleep(0.01)
    store.release_resource_request(request_id,owner_token=request_id)
'''
    release_marker = tmp_path / "release.marker"
    first_env, second_env = os.environ.copy(), os.environ.copy()
    if different_policy:
        first_env["MCP_MAX_RUNNING_ANALYZERS"] = "1"
        second_env["MCP_MAX_RUNNING_ANALYZERS"] = "3"
    first = subprocess.Popen(
        [sys.executable, "-c", script, str(db_path), json.dumps(first_claim.public()), "p1", str(release_marker)],
        cwd=repo_root, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=first_env,
    )
    try:
        assert first.stdout is not None
        assert json.loads(first.stdout.readline())["admitted"] is True
        second = subprocess.run(
            [sys.executable, "-c", script, str(db_path), json.dumps(second_claim.public()), "p2", "-"],
            cwd=repo_root, capture_output=True, text=True, timeout=10, check=True, env=second_env,
        )
        second_result = json.loads(second.stdout.strip())
        assert second_result["admitted"] is overlap
        if not overlap:
            assert second_result["reason"] == ("resource_policy_incompatible" if different_policy else "resource_conflict")
    finally:
        release_marker.write_text("release", encoding="utf-8")
        first.communicate(timeout=10)
        assert first.returncode == 0
    third = subprocess.run(
        [sys.executable, "-c", script, str(db_path), json.dumps(second_claim.public()), "p3" if overlap else "p2", "-"],
        cwd=repo_root, capture_output=True, text=True, timeout=10, check=True, env=second_env,
    )
    assert json.loads(third.stdout.strip())["admitted"] is True


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
