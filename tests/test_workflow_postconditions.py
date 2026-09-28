from __future__ import annotations

import hashlib
import tracemalloc
from pathlib import Path

from core.reconcilers import evaluate_postcondition, get_postcondition_descriptor
from core.recovery_models import Postcondition


def test_filesystem_hash_evidence_is_conclusive(tmp_path: Path) -> None:
    target = tmp_path / "value.txt"
    target.write_text("value", encoding="utf-8")
    digest = hashlib.sha256(b"value").hexdigest()
    evidence = evaluate_postcondition(Postcondition("file_sha256", {"path": str(target), "sha256": digest}))
    assert evidence.data["conclusive"] is True
    assert evidence.data["satisfied"] is True


def test_multifile_hash_does_not_buffer_large_files(tmp_path: Path) -> None:
    target = tmp_path / "large.bin"
    chunk = b"x" * 1_048_576
    digest = hashlib.sha256()
    with target.open("wb") as handle:
        for _ in range(8):
            handle.write(chunk)
            digest.update(chunk)
    tracemalloc.start()
    try:
        evidence = evaluate_postcondition(Postcondition(
            "files_all_sha256", {"files": [{"path": str(target), "sha256": digest.hexdigest()}]},
        ))
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()
    assert evidence.data["satisfied"] is True
    assert peak < 4_000_000


def test_package_version_uses_active_python_environment() -> None:
    evidence = evaluate_postcondition(Postcondition("package_version", {"name": "pytest"}))
    assert evidence.source == "package"
    assert evidence.data["conclusive"] is True
    assert evidence.data["satisfied"] is True
    assert evidence.data["version"]


def test_process_exit_is_conclusive_for_missing_pid() -> None:
    evidence = evaluate_postcondition(Postcondition("process_exited", {"pid": 2_147_483_647}))
    assert evidence.data == {"conclusive": True, "satisfied": True, "pid": 2_147_483_647, "pid_exists": False}


def test_registered_postconditions_declare_safe_reconciliation() -> None:
    for kind in (
        "git_index_contains",
        "job_state",
        "package_version",
        "http_response",
        "ui_element_state",
        "browser_state",
    ):
        descriptor = get_postcondition_descriptor(kind)
        assert descriptor.kind == kind
        assert descriptor.safe_for_reconciliation is True


def test_unavailable_semantic_adapter_is_inconclusive() -> None:
    for kind in ("ui_element_state", "browser_state"):
        evidence = evaluate_postcondition(Postcondition(kind, {"runtime_id": "missing"}))
        assert evidence.data["conclusive"] is False
        assert evidence.data["satisfied"] is False
