"""Crash recovery for native command ownership left behind by a dead runtime."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import psutil
import pytest

from core.resource_claims import ResourceClaim
from core.workflow_store import WorkflowStore

CLAIMS = [ResourceClaim("coordination", "host", "write", "exact")]


def _reserve(store: WorkflowStore, request_id: str, owner: str) -> None:
    result = store.reserve_resource_claims(
        request_id=request_id, owner_token=owner, claims=CLAIMS, category="native.command",
    )
    assert result["admitted"] is True


def test_dispatch_identity_that_exited_releases_the_claim(tmp_path: Path) -> None:
    token = "native-owner-999999-123.000000-dead"
    store = WorkflowStore(tmp_path / "workflows.sqlite3")
    _reserve(store, "stale-exited", token)
    assert store.record_resource_dispatch(
        "stale-exited", owner_token=token, process_pid=999997, process_created=123.0,
    )
    store.close()

    recovered = WorkflowStore(tmp_path / "workflows.sqlite3")
    row = recovered.resource_request("stale-exited")
    assert row["state"] == "released"
    assert row["reason"] == "stale_native_owner_command_exited"
    assert recovered.last_native_recovery == {"released": 1, "quarantined": 0}


def test_live_orphan_stays_fenced_until_it_exits(tmp_path: Path) -> None:
    token = "native-owner-999999-124.000000-orphan"
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        created = psutil.Process(child.pid).create_time()
        store = WorkflowStore(tmp_path / "workflows.sqlite3")
        _reserve(store, "stale-orphan", token)
        assert store.record_resource_dispatch(
            "stale-orphan", owner_token=token, process_pid=child.pid, process_created=created,
        )
        store.close()

        fenced = WorkflowStore(tmp_path / "workflows.sqlite3")
        row = fenced.resource_request("stale-orphan")
        assert row["state"] == "quarantined"
        assert row["reason"] == "stale_native_owner_command_alive"
        assert fenced.last_native_recovery == {"released": 0, "quarantined": 1}
        fenced.close()
    finally:
        child.kill()
        child.wait(timeout=10)

    released = WorkflowStore(tmp_path / "workflows.sqlite3")
    row = released.resource_request("stale-orphan")
    assert row["state"] == "released"
    assert row["reason"] == "stale_native_owner_command_exited"


def test_missing_dispatch_marker_is_preserved_as_unknown(tmp_path: Path) -> None:
    token = "native-owner-999999-125.000000-unknown"
    store = WorkflowStore(tmp_path / "workflows.sqlite3")
    _reserve(store, "stale-unknown", token)
    store.close()

    recovered = WorkflowStore(tmp_path / "workflows.sqlite3")
    row = recovered.resource_request("stale-unknown")
    assert row["state"] == "quarantined"
    assert row["reason"] == "stale_native_owner_dispatch_unknown"


def test_live_owner_claim_is_not_released(tmp_path: Path) -> None:
    owner = psutil.Process()
    token = f"native-owner-{os.getpid()}-{owner.create_time():.6f}-live"
    store = WorkflowStore(tmp_path / "workflows.sqlite3")
    _reserve(store, "live-owner", token)
    store.close()

    recovered = WorkflowStore(tmp_path / "workflows.sqlite3")
    row = recovered.resource_request("live-owner")
    assert row["state"] == "active"
    assert recovered.last_native_recovery == {"released": 0, "quarantined": 0}


@pytest.mark.skipif(os.name == "nt", reason="POSIX SIGKILL of the owning runtime")
def test_sigkill_recovery_releases_after_owned_command_is_gone(tmp_path: Path) -> None:
    owner = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        token = f"native-owner-{owner.pid}-{psutil.Process(owner.pid).create_time():.6f}-killed"
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
        try:
            created = psutil.Process(child.pid).create_time()
            store = WorkflowStore(tmp_path / "workflows.sqlite3")
            _reserve(store, "sigkill-claim", token)
            assert store.record_resource_dispatch(
                "sigkill-claim", owner_token=token, process_pid=child.pid, process_created=created,
            )
            store.close()

            owner.kill()
            owner.wait(timeout=10)
            fenced = WorkflowStore(tmp_path / "workflows.sqlite3")
            assert fenced.resource_request("sigkill-claim")["reason"] == "stale_native_owner_command_alive"
            fenced.close()
        finally:
            child.kill()
            child.wait(timeout=10)

        released = WorkflowStore(tmp_path / "workflows.sqlite3")
        assert released.resource_request("sigkill-claim")["state"] == "released"
    finally:
        if owner.poll() is None:
            owner.kill()
            owner.wait(timeout=10)
