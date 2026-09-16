from __future__ import annotations

import os
import shutil
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

import core.backups as backups
from core.backups import BackupCleanupResult, BackupPolicy, BackupRetentionManager, cleanup_backups
from tools.filesystem import service


def _backup(path: Path, size: int, mtime: float) -> None:
    path.write_bytes(b"x" * size)
    os.utime(path, (mtime, mtime))


def _policy(
    *,
    max_bytes: int = 0,
    max_age_sec: float = 0,
    interval: float = 0,
    recent_grace_sec: float = 0,
) -> BackupPolicy:
    return BackupPolicy(
        max_bytes=max_bytes,
        max_age_sec=max_age_sec,
        cleanup_interval_sec=interval,
        recent_grace_sec=recent_grace_sec,
    )


def test_age_retention_removes_expired_backups_but_preserves_newest(tmp_path: Path) -> None:
    _backup(tmp_path / "old-a.bak", 100, 100)
    _backup(tmp_path / "old-b.bak", 100, 200)
    _backup(tmp_path / "newest.bak", 100, 950)

    result = cleanup_backups(tmp_path, _policy(max_age_sec=100), now=1_000)

    assert result.removed_for_age == 2
    assert result.removed_for_quota == 0
    assert result.remaining_files == 1
    assert result.remaining_bytes == 100
    assert (tmp_path / "newest.bak").is_file()
    assert result.safety_floor_preserved is True


def test_safety_floor_keeps_one_restore_point_even_when_every_backup_is_old(tmp_path: Path) -> None:
    _backup(tmp_path / "older.bak", 100, 100)
    _backup(tmp_path / "newer.bak", 120, 200)

    result = cleanup_backups(tmp_path, _policy(max_age_sec=10), now=1_000)

    assert result.removed_for_age == 1
    assert result.remaining_files == 1
    assert (tmp_path / "newer.bak").is_file()


def test_quota_removes_oldest_until_bounded(tmp_path: Path) -> None:
    _backup(tmp_path / "one.bak", 100, 100)
    _backup(tmp_path / "two.bak", 100, 200)
    _backup(tmp_path / "three.bak", 100, 300)

    result = cleanup_backups(tmp_path, _policy(max_bytes=180), now=1_000)

    assert result.removed_for_quota == 2
    assert result.remaining_bytes == 100
    assert result.quota_satisfied is True
    assert (tmp_path / "three.bak").is_file()


def test_protected_current_backup_is_not_removed_by_quota(tmp_path: Path) -> None:
    protected = tmp_path / "current.bak"
    _backup(protected, 100, 100)
    _backup(tmp_path / "newest.bak", 100, 200)

    result = cleanup_backups(
        tmp_path,
        _policy(max_bytes=50),
        protected_paths={protected},
        now=1_000,
    )

    assert protected.is_file()
    assert (tmp_path / "newest.bak").is_file()
    assert result.remaining_bytes == 200
    assert result.quota_satisfied is False


def test_recent_backups_survive_cleanup_before_protection_request_is_observed(tmp_path: Path) -> None:
    now = 1_000.0
    recent = tmp_path / "recent.bak"
    newest = tmp_path / "newest.bak"
    _backup(recent, 100, now - 10)
    _backup(newest, 100, now - 1)

    result = cleanup_backups(
        tmp_path,
        _policy(max_bytes=1, recent_grace_sec=60),
        now=now,
    )

    assert recent.is_file()
    assert newest.is_file()
    assert result.remaining_files == 2
    assert result.quota_satisfied is False


def test_cleanup_ignores_non_backup_files(tmp_path: Path) -> None:
    _backup(tmp_path / "old.bak", 100, 100)
    marker = tmp_path / "do-not-touch.txt"
    marker.write_text("metadata", encoding="utf-8")

    cleanup_backups(tmp_path, _policy(max_age_sec=10), now=1_000)

    assert marker.read_text(encoding="utf-8") == "metadata"


def test_retention_age_uses_backup_event_timestamp_not_copied_source_mtime(tmp_path: Path) -> None:
    old_stamp = "20250101T000000000000Z"
    new_stamp = "20260915T000000000000Z"
    old = tmp_path / f"{old_stamp}_aaa_old.txt.bak"
    recent = tmp_path / f"{new_stamp}_bbb_recent.txt.bak"
    old.write_bytes(b"old")
    recent.write_bytes(b"recent")
    os.utime(old, (2_000_000_000, 2_000_000_000))
    os.utime(recent, (1, 1))
    now = datetime(2026, 9, 16, tzinfo=timezone.utc).timestamp()

    result = cleanup_backups(tmp_path, _policy(max_age_sec=30 * 86_400), now=now)

    assert result.removed_for_age == 1
    assert old.exists() is False
    assert recent.is_file()


def test_backup_file_is_atomically_published_and_leaves_no_temp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = tmp_path / "source.txt"
    source.write_text("recoverable", encoding="utf-8")
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    monkeypatch.setattr(service, "SETTINGS", SimpleNamespace(backup_dir=backup_dir))

    backup_path = Path(service._backup_file(source, "a" * 64))

    assert backup_path.is_file()
    assert backup_path.read_text(encoding="utf-8") == "recoverable"
    assert list(backup_dir.glob("*.tmp")) == []


def test_retention_manager_coalesces_dirty_requests(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    entered = threading.Event()
    release = threading.Event()
    calls = 0
    calls_lock = threading.Lock()
    original = backups.cleanup_backups

    def blocked_cleanup(
        directory: Path,
        policy: BackupPolicy,
        *,
        now: float | None = None,
        protected_paths: set[Path] | None = None,
    ) -> BackupCleanupResult:
        nonlocal calls
        with calls_lock:
            calls += 1
            current = calls
        if current == 1:
            entered.set()
            assert release.wait(2)
        return original(directory, policy, now=now, protected_paths=protected_paths)

    monkeypatch.setattr(backups, "cleanup_backups", blocked_cleanup)
    manager = BackupRetentionManager(tmp_path, _policy(interval=0))
    manager.schedule()
    assert entered.wait(2)
    for _ in range(20):
        manager.schedule()
    release.set()
    assert manager.flush(3)
    assert calls == 2


def test_background_cleanup_failure_is_nonfatal_and_visible(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def fail_cleanup(
        directory: Path,
        policy: BackupPolicy,
        *,
        now: float | None = None,
        protected_paths: set[Path] | None = None,
    ) -> BackupCleanupResult:
        del directory, policy, now, protected_paths
        raise OSError("simulated cleanup failure")

    monkeypatch.setattr(backups, "cleanup_backups", fail_cleanup)
    manager = BackupRetentionManager(tmp_path, _policy(interval=0))
    manager.schedule()
    assert manager.flush(2)
    snapshot = manager.snapshot()
    assert snapshot["running"] is False
    assert "simulated cleanup failure" in str(snapshot["last_error"])


def test_atomic_write_schedules_cleanup_only_after_successful_replace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "target.txt"
    target.write_text("before", encoding="utf-8")
    backup = tmp_path / "backup.bak"
    scheduled: list[Path] = []

    def fake_backup(source: Path, digest: str) -> str:
        assert digest
        shutil.copy2(source, backup)
        return str(backup)

    class FakeRetention:
        def schedule(self, protected_path: Path | None = None) -> None:
            assert target.read_text(encoding="utf-8") == "after"
            assert protected_path is not None
            scheduled.append(protected_path)

    monkeypatch.setattr(service, "_backup_file", fake_backup)
    monkeypatch.setattr(service, "BACKUP_RETENTION", FakeRetention())

    result = service.atomic_write(
        target,
        "after",
        encoding="utf-8",
        create_parents=False,
        backup=True,
        validate_python=False,
    )

    assert result["backup"] == str(backup)
    assert backup.read_text(encoding="utf-8") == "before"
    assert scheduled == [backup]


def test_failed_replace_keeps_backup_and_does_not_schedule_cleanup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "target.txt"
    target.write_text("before", encoding="utf-8")
    backup = tmp_path / "backup.bak"
    scheduled: list[Path] = []

    def fake_backup(source: Path, digest: str) -> str:
        assert digest
        shutil.copy2(source, backup)
        return str(backup)

    class FakeRetention:
        def schedule(self, protected_path: Path | None = None) -> None:
            if protected_path is not None:
                scheduled.append(protected_path)

    monkeypatch.setattr(service, "_backup_file", fake_backup)
    monkeypatch.setattr(service, "BACKUP_RETENTION", FakeRetention())
    monkeypatch.setattr(service.os, "replace", lambda *_: (_ for _ in ()).throw(OSError("replace failed")))

    with pytest.raises(OSError, match="replace failed"):
        service.atomic_write(
            target,
            "after",
            encoding="utf-8",
            create_parents=False,
            backup=True,
            validate_python=False,
        )

    assert target.read_text(encoding="utf-8") == "before"
    assert backup.read_text(encoding="utf-8") == "before"
    assert scheduled == []


def test_manager_rate_limits_repeated_cleanup_scans(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[float] = []
    original = backups.cleanup_backups

    def tracked_cleanup(
        directory: Path,
        policy: BackupPolicy,
        *,
        now: float | None = None,
        protected_paths: set[Path] | None = None,
    ) -> BackupCleanupResult:
        calls.append(time.monotonic())
        return original(directory, policy, now=now, protected_paths=protected_paths)

    monkeypatch.setattr(backups, "cleanup_backups", tracked_cleanup)
    manager = BackupRetentionManager(tmp_path, _policy(interval=0.05))
    manager.schedule()
    assert manager.flush(2)
    manager.schedule()
    assert manager.flush(2)
    assert len(calls) == 2
    assert calls[1] - calls[0] >= 0.04
