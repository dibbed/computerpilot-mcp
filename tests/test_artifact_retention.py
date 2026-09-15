from __future__ import annotations

import os
import time
from pathlib import Path

from core.artifact_retention import ArtifactPolicy, cleanup_artifacts


def _policy(
    *,
    max_bytes: int = 1_000_000,
    max_age_sec: float = 0,
    max_count: int = 100,
    recent_grace_sec: float = 0,
) -> ArtifactPolicy:
    return ArtifactPolicy(
        max_bytes=max_bytes,
        max_age_sec=max_age_sec,
        max_count=max_count,
        cleanup_interval_sec=0,
        recent_grace_sec=recent_grace_sec,
    )


def _artifact(directory: Path, name: str, size: int, mtime: float) -> Path:
    path = directory / name
    path.write_bytes(b"x" * size)
    os.utime(path, (mtime, mtime))
    return path


def test_artifact_retention_applies_age_count_and_bytes(tmp_path: Path) -> None:
    now = 10_000.0
    for index in range(8):
        _artifact(tmp_path, f"{index:02d}.bin", 100, now - 1_000 + index)
    for index in range(8, 14):
        _artifact(tmp_path, f"{index:02d}.bin", 100, now - 10 + index)

    result = cleanup_artifacts(
        tmp_path,
        _policy(max_bytes=300, max_age_sec=100, max_count=4),
        now=now,
    )

    assert result.scanned_files == 14
    assert result.removed_for_age == 8
    assert result.removed_for_count == 2
    assert result.removed_for_quota == 1
    assert result.remaining_files == 3
    assert result.remaining_bytes == 300
    assert result.quota_satisfied is True
    assert result.safety_floor_preserved is True


def test_artifact_retention_protects_current_and_newest(tmp_path: Path) -> None:
    now = time.time()
    old = _artifact(tmp_path, "old.bin", 100, now - 100)
    current = _artifact(tmp_path, "current.bin", 100, now - 50)
    newest = _artifact(tmp_path, "newest.bin", 100, now - 1)

    result = cleanup_artifacts(
        tmp_path,
        _policy(max_bytes=1, max_age_sec=1, max_count=1),
        now=now,
        protected_paths={current},
    )

    assert old.exists() is False
    assert current.is_file()
    assert newest.is_file()
    assert result.remaining_files == 2
    assert result.quota_satisfied is False


def test_recent_artifacts_survive_cleanup_before_protection_request_is_observed(tmp_path: Path) -> None:
    now = 10_000.0
    recent = _artifact(tmp_path, "recent.bin", 100, now - 10)
    newest = _artifact(tmp_path, "newest.bin", 100, now - 1)

    result = cleanup_artifacts(
        tmp_path,
        _policy(max_bytes=1, max_count=1, recent_grace_sec=60),
        now=now,
    )

    assert recent.is_file()
    assert newest.is_file()
    assert result.remaining_files == 2
    assert result.quota_satisfied is False


def test_artifact_retention_ignores_non_artifact_files(tmp_path: Path) -> None:
    marker = tmp_path / "metadata.json"
    marker.write_text("keep", encoding="utf-8")
    _artifact(tmp_path, "old.bin", 100, 1)
    _artifact(tmp_path, "new.bin", 100, 2)

    cleanup_artifacts(tmp_path, _policy(max_bytes=100, max_count=1), now=3)

    assert marker.read_text(encoding="utf-8") == "keep"


def test_artifact_retention_preserves_newest_when_single_file_exceeds_quota(tmp_path: Path) -> None:
    newest = _artifact(tmp_path, "huge.bin", 2_000, time.time())

    result = cleanup_artifacts(tmp_path, _policy(max_bytes=10, max_count=1))

    assert newest.is_file()
    assert result.remaining_files == 1
    assert result.remaining_bytes == 2_000
    assert result.quota_satisfied is False
    assert result.safety_floor_preserved is True
