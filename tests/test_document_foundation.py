from __future__ import annotations

import hashlib
import importlib
import importlib.util
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

import pytest

from core import registry
from core.tool_profiles import ALL_DOMAINS, PROFILE_DOMAINS


def test_documents_domain_is_registered_as_portable_profile() -> None:
    assert "documents" in ALL_DOMAINS
    assert PROFILE_DOMAINS["documents"] == ("filesystem", "documents", "recovery", "workflows")
    assert "documents" in registry.REGISTRARS


def test_document_binary_foundation_module_exists() -> None:
    assert importlib.util.find_spec("tools.documents.common") is not None


def test_publish_document_is_atomic_recoverable_and_returns_artifact(tmp_path: Path) -> None:
    common = importlib.import_module("tools.documents.common")
    publish_document = getattr(common, "publish_document", None)
    assert callable(publish_document)

    target = tmp_path / "book.xlsx"
    target.write_bytes(b"before")

    def writer(staged: Path) -> None:
        staged.write_bytes(b"after")

    def validator(staged: Path) -> None:
        assert staged.read_bytes() == b"after"

    result = publish_document(
        target,
        writer=writer,
        validator=validator,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        backup=True,
    )

    digest = hashlib.sha256(b"after").hexdigest()
    assert target.read_bytes() == b"after"
    assert result["changed"] is True
    assert Path(result["backup"]).read_bytes() == b"before"
    assert result["artifact"] == {
        "path": str(target),
        "sha256": digest,
        "bytes": 5,
        "media_type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    }
    assert result["postcondition"] == {
        "kind": "file_sha256",
        "expected": {"path": str(target), "sha256": digest},
    }



def test_document_write_lock_serializes_same_target_and_allows_independent_targets(tmp_path: Path) -> None:
    common = importlib.import_module("tools.documents.common")
    shared = tmp_path / "shared.xlsx"
    gate = threading.Event()
    state_lock = threading.Lock()
    active = 0
    peak = 0

    def same_target(_: int) -> None:
        nonlocal active, peak
        gate.wait(timeout=2)
        with common.document_write_lock(shared):
            with state_lock:
                active += 1
                peak = max(peak, active)
            time.sleep(0.03)
            with state_lock:
                active -= 1

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(same_target, index) for index in range(2)]
        gate.set()
        for future in futures:
            future.result(timeout=3)

    assert peak == 1

    barrier = Barrier(2)

    def independent(name: str) -> None:
        with common.document_write_lock(tmp_path / name):
            barrier.wait(timeout=2)

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(independent, name) for name in ("a.xlsx", "b.xlsx")]
        for future in futures:
            future.result(timeout=3)


def test_publish_document_checkpoints_postcondition_before_atomic_replace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    common = importlib.import_module("tools.documents.common")
    target = tmp_path / "checkpoint.xlsx"
    target.write_bytes(b"before")
    checkpoints: list[dict[str, object]] = []

    monkeypatch.setattr(
        common,
        "checkpoint_current_postcondition",
        lambda postcondition: checkpoints.append(postcondition),
        raising=False,
    )
    original_replace = common.filesystem_service._replace_with_retry

    def checked_replace(source: Path, destination: Path) -> None:
        assert checkpoints
        original_replace(source, destination)

    monkeypatch.setattr(common.filesystem_service, "_replace_with_retry", checked_replace)

    result = common.publish_document(
        target,
        writer=lambda staged: staged.write_bytes(b"after"),
        validator=None,
        media_type="application/octet-stream",
        backup=False,
    )

    assert checkpoints == [result["postcondition"]]
