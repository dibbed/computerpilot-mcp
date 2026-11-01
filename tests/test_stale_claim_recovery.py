"""Crash recovery for native command ownership left behind by a dead runtime."""

from __future__ import annotations

import os
import subprocess
import sys
import time
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


def test_runtime_reconciliation_releases_an_orphan_that_exited_after_fencing(tmp_path: Path) -> None:
    """No second store construction may be needed to clear a proven-dead child."""

    path = tmp_path / "workflows.sqlite3"
    store = WorkflowStore(path, recover=False)
    token = "native-owner-999999-200.000000-runtime"
    _reserve(store, "runtime-exited", token)
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    created = psutil.Process(child.pid).create_time()
    assert store.record_resource_dispatch(
        "runtime-exited", owner_token=token, process_pid=child.pid, process_created=created,
    )
    child.kill()
    child.wait(timeout=10)

    deadline = time.monotonic() + 8
    admitted = False
    while time.monotonic() < deadline:
        result = store.reserve_resource_claims(
            request_id="runtime-writer",
            owner_token="native-owner-999999-201.000000-writer",
            claims=CLAIMS,
            category="native.command",
        )
        if result["admitted"] is True:
            admitted = True
            break
        time.sleep(0.1)
    assert admitted, "stale native claim still blocked admission in the same runtime"
    row = store.resource_request("runtime-exited")
    assert (row["state"], row["reason"]) == ("released", "stale_native_owner_command_exited")
    assert store.runtime_native_recovery["released"] == 1


def test_runtime_reconciliation_keeps_a_live_orphan_fenced_until_it_exits(tmp_path: Path) -> None:
    path = tmp_path / "workflows.sqlite3"
    store = WorkflowStore(path, recover=False)
    token = "native-owner-999999-202.000000-live"
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        created = psutil.Process(child.pid).create_time()
        _reserve(store, "runtime-live", token)
        assert store.record_resource_dispatch(
            "runtime-live", owner_token=token, process_pid=child.pid, process_created=created,
        )
        store.quarantine_resource_request(
            "runtime-live", owner_token=token, reason="stale_native_owner_command_alive",
        )

        blocked = store.reserve_resource_claims(
            request_id="runtime-blocked",
            owner_token="native-owner-999999-203.000000-blocked",
            claims=CLAIMS,
            category="native.command",
        )
        assert blocked["admitted"] is False
        assert blocked["reason"] == "resource_quarantined"
        assert store.resource_request("runtime-live")["state"] == "quarantined"
    finally:
        child.kill()
        child.wait(timeout=10)

    deadline = time.monotonic() + 8
    admitted = False
    while time.monotonic() < deadline:
        result = store.reserve_resource_claims(
            request_id="runtime-blocked",
            owner_token="native-owner-999999-203.000000-blocked",
            claims=CLAIMS,
            category="native.command",
        )
        if result["admitted"] is True:
            admitted = True
            break
        time.sleep(0.1)
    assert admitted, "claim was not reconciled after the fenced orphan exited"
    row = store.resource_request("runtime-live")
    assert (row["state"], row["reason"]) == ("released", "stale_native_owner_command_exited")


def test_runtime_reconciliation_preserves_markerless_ownership(tmp_path: Path) -> None:
    path = tmp_path / "workflows.sqlite3"
    store = WorkflowStore(path, recover=False)
    token = "native-owner-999999-204.000000-markerless"
    _reserve(store, "runtime-markerless", token)
    store.quarantine_resource_request(
        "runtime-markerless", owner_token=token, reason="stale_native_owner_command_alive",
    )

    blocked = store.reserve_resource_claims(
        request_id="runtime-markerless-writer",
        owner_token="native-owner-999999-205.000000-writer",
        claims=CLAIMS,
        category="native.command",
    )
    assert blocked["admitted"] is False
    assert blocked["reason"] == "resource_quarantined"
    row = store.resource_request("runtime-markerless")
    assert (row["state"], row["reason"]) == ("quarantined", "stale_native_owner_dispatch_unknown")


def test_runtime_reconciliation_never_releases_a_live_owner(tmp_path: Path) -> None:
    path = tmp_path / "workflows.sqlite3"
    store = WorkflowStore(path, recover=False)
    owner = psutil.Process()
    token = f"native-owner-{os.getpid()}-{owner.create_time():.6f}-liveowner"
    _reserve(store, "runtime-live-owner", token)

    blocked = store.reserve_resource_claims(
        request_id="runtime-live-owner-writer",
        owner_token="native-owner-999999-206.000000-writer",
        claims=CLAIMS,
        category="native.command",
    )
    assert blocked["admitted"] is False
    assert store.resource_request("runtime-live-owner")["state"] == "active"


def test_runtime_reconciliation_preserves_unresolved_effect_quarantine(tmp_path: Path) -> None:
    path = tmp_path / "workflows.sqlite3"
    store = WorkflowStore(path, recover=False)
    token = "native-owner-999999-207.000000-effects"
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    created = psutil.Process(child.pid).create_time()
    _reserve(store, "runtime-effects", token)
    assert store.record_resource_dispatch(
        "runtime-effects", owner_token=token, process_pid=child.pid, process_created=created,
    )
    child.kill()
    child.wait(timeout=10)
    store.quarantine_resource_request(
        "runtime-effects", owner_token=token, reason="managed_effect_or_cleanup_unresolved",
    )

    blocked = store.reserve_resource_claims(
        request_id="runtime-effects-writer",
        owner_token="native-owner-999999-208.000000-writer",
        claims=CLAIMS,
        category="native.command",
    )
    assert blocked["admitted"] is False
    assert blocked["reason"] == "resource_quarantined"
    row = store.resource_request("runtime-effects")
    assert (row["state"], row["reason"]) == ("quarantined", "managed_effect_or_cleanup_unresolved")


def test_runtime_reconciliation_releases_a_never_dispatched_pending_claim(tmp_path: Path) -> None:
    path = tmp_path / "workflows.sqlite3"
    store = WorkflowStore(path, recover=False)
    blocker = store.reserve_resource_claims(
        request_id="runtime-blocker", owner_token="blocker", claims=CLAIMS, category="test",
    )
    assert blocker["admitted"] is True
    pending = store.reserve_resource_claims(
        request_id="runtime-pending",
        owner_token="native-owner-999999-209.000000-pending",
        claims=CLAIMS,
        category="native.command",
    )
    assert pending["admitted"] is False
    assert store.resource_request("runtime-pending")["state"] == "pending"
    store.release_resource_request("runtime-blocker", owner_token="blocker")

    deadline = time.monotonic() + 8
    admitted = False
    while time.monotonic() < deadline:
        result = store.reserve_resource_claims(
            request_id="runtime-pending-writer",
            owner_token="native-owner-999999-210.000000-writer",
            claims=CLAIMS,
            category="native.command",
        )
        if result["admitted"] is True:
            admitted = True
            break
        time.sleep(0.1)
    assert admitted
    row = store.resource_request("runtime-pending")
    assert (row["state"], row["reason"]) == ("released", "stale_native_owner_never_dispatched")


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
