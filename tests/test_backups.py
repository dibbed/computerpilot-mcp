from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
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
    max_count: int = 0,
) -> BackupPolicy:
    return BackupPolicy(
        max_bytes=max_bytes,
        max_age_sec=max_age_sec,
        cleanup_interval_sec=interval,
        recent_grace_sec=recent_grace_sec,
        max_count=max_count,
    )


@pytest.mark.parametrize("size", [0, 100])
def test_count_quota_removes_oldest_backups_including_empty_files(tmp_path: Path, size: int) -> None:
    for index in range(4):
        _backup(tmp_path / f"{index}.bak", size, 100 + index)

    result = cleanup_backups(tmp_path, _policy(max_count=2), now=1_000)

    assert sorted(path.name for path in tmp_path.iterdir()) == ["2.bak", "3.bak"]
    assert result.scanned_files == 4
    assert result.scanned_bytes == 4 * size
    assert result.removed_files == result.removed_for_count == 2
    assert result.removed_for_age == result.removed_for_quota == 0
    assert result.removed_bytes == result.remaining_bytes == 2 * size
    assert result.remaining_files == 2
    assert result.quota_satisfied is True


def test_disabled_count_quota_preserves_positional_policy_and_result_contracts(tmp_path: Path) -> None:
    policy = BackupPolicy(0, 0, 0, 0)
    assert policy.recent_grace_sec == policy.max_count == 0
    legacy_result = BackupCleanupResult(1, 10, 0, 0, 0, 0, 1, 10, True, True, 0)
    assert legacy_result.removed_for_count == 0
    assert legacy_result.to_dict()["removed_for_count"] == 0
    assert cleanup_backups(tmp_path, policy).to_dict()["removed_for_count"] == 0
    for index in range(3):
        _backup(tmp_path / f"{index}.bak", 0, index)

    result = cleanup_backups(tmp_path, policy, now=1_000)

    assert result.remaining_files == 3
    assert result.removed_files == result.removed_for_count == 0
    assert result.quota_satisfied is True


def test_age_retention_precedes_count_quota(tmp_path: Path) -> None:
    for index, created_at in enumerate((100, 200, 920, 930, 940)):
        _backup(tmp_path / f"{index}.bak", 10, created_at)

    result = cleanup_backups(tmp_path, _policy(max_age_sec=100, max_count=2), now=1_000)

    assert result.removed_for_age == 2
    assert result.removed_for_count == 1
    assert result.removed_for_quota == 0
    assert result.removed_files == 3
    assert result.remaining_files == 2
    assert result.remaining_bytes == 20
    assert result.quota_satisfied is True


def test_byte_pressure_takes_priority_over_count_reason_until_byte_limit_is_met(tmp_path: Path) -> None:
    for index, size in enumerate((100, 0, 100, 100)):
        _backup(tmp_path / f"{index}.bak", size, 100 + index)

    result = cleanup_backups(tmp_path, _policy(max_bytes=200, max_count=1), now=1_000)

    assert result.removed_for_quota == 1
    assert result.removed_for_count == 2
    assert result.removed_files == result.removed_for_age + result.removed_for_quota + result.removed_for_count == 3
    assert result.remaining_files == 1
    assert result.remaining_bytes == 100
    assert result.quota_satisfied is True


@pytest.mark.parametrize("recent", [False, True])
def test_count_quota_reports_overflow_when_protected_or_recent_backups_survive(tmp_path: Path, recent: bool) -> None:
    protected = tmp_path / "protected.bak"
    newest = tmp_path / "newest.bak"
    _backup(protected, 0, 990 if recent else 100)
    _backup(newest, 0, 999)

    result = cleanup_backups(
        tmp_path,
        _policy(max_count=1, recent_grace_sec=60 if recent else 0),
        now=1_000,
        protected_paths=set() if recent else {protected},
    )

    assert protected.is_file() and newest.is_file()
    assert result.remaining_files == 2
    assert result.remaining_bytes == result.removed_for_count == 0
    assert result.quota_satisfied is False
    assert result.safety_floor_preserved is True


def test_newest_survives_when_byte_and_count_limits_cannot_both_be_met(tmp_path: Path) -> None:
    _backup(tmp_path / "old.bak", 0, 100)
    newest = tmp_path / "newest.bak"
    _backup(newest, 100, 200)

    result = cleanup_backups(tmp_path, _policy(max_count=1, max_bytes=1), now=1_000)

    assert newest.is_file()
    assert result.remaining_files == 1
    assert result.remaining_bytes == 100
    assert result.removed_for_quota == 1
    assert result.removed_for_count == 0
    assert result.quota_satisfied is False
    assert result.safety_floor_preserved is True


@pytest.mark.parametrize("blocked_names", [{"0.bak"}, {"0.bak", "1.bak"}])
def test_count_decreases_only_after_successful_unlink(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, blocked_names: set[str],
) -> None:
    for index in range(4):
        _backup(tmp_path / f"{index}.bak", index + 1, 100 + index)
    unlink = Path.unlink

    def denied_unlink(path: Path, *, missing_ok: bool = False) -> None:
        if path.name in blocked_names:
            raise PermissionError("backup still in use")
        unlink(path, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", denied_unlink)

    result = cleanup_backups(tmp_path, _policy(max_count=2), now=1_000)

    survivors = list(tmp_path.iterdir())
    assert result.errors == len(blocked_names)
    assert result.remaining_files == len(survivors) == len(blocked_names) + 1
    assert result.remaining_bytes == sum(path.stat().st_size for path in survivors)
    assert result.removed_files == result.removed_for_count == 3 - len(blocked_names)
    assert result.quota_satisfied is (len(survivors) <= 2)
    assert (tmp_path / "3.bak").is_file()


@pytest.mark.parametrize("size, max_bytes, max_count", [(0, 0, 1), (100, 150, 0)])
def test_final_stat_failure_preserves_scanned_survivor_usage_and_overflow(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, size: int, max_bytes: int, max_count: int,
) -> None:
    protected = tmp_path / "protected.bak"
    newest = tmp_path / "newest.bak"
    _backup(protected, size, 100)
    _backup(newest, size, 200)
    stat = Path.stat

    def denied_stat(path: Path, *, follow_symlinks: bool = True) -> os.stat_result:
        if path == protected and follow_symlinks:
            raise PermissionError("cannot inspect surviving backup")
        return stat(path, follow_symlinks=follow_symlinks)

    monkeypatch.setattr(Path, "stat", denied_stat)

    result = cleanup_backups(
        tmp_path, _policy(max_bytes=max_bytes, max_count=max_count),
        now=1_000, protected_paths={protected},
    )

    assert result.scanned_files == result.remaining_files == 2
    assert result.scanned_bytes == result.remaining_bytes == 2 * size
    assert result.removed_files == 0
    assert result.quota_satisfied is False
    assert result.errors == 1


def test_count_quota_uses_actual_survivors_after_concurrent_removal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    for index in range(4):
        _backup(tmp_path / f"{index}.bak", index + 1, 100 + index)
    unlink = Path.unlink

    def concurrent_unlink(path: Path, *, missing_ok: bool = False) -> None:
        unlink(path, missing_ok=missing_ok)
        if path == tmp_path / "2.bak":
            unlink(tmp_path / "0.bak")

    monkeypatch.setattr(Path, "unlink", concurrent_unlink)

    result = cleanup_backups(
        tmp_path, _policy(max_count=2), now=1_000,
        protected_paths={tmp_path / "0.bak", tmp_path / "1.bak"},
    )

    assert result.removed_files == result.removed_for_count == 1
    assert result.removed_bytes == 3
    assert result.remaining_files == 2
    assert result.remaining_bytes == 6
    assert result.quota_satisfied is True


def test_count_retention_coalesces_concurrent_atomic_backup_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    directory = tmp_path / "backups"
    directory.mkdir()
    for index in range(2):
        _backup(directory / f"{index}.bak", 1, 100 + index)
    source = tmp_path / "source.txt"
    source.write_text("recoverable", encoding="utf-8")
    entered = threading.Event()
    release = threading.Event()
    scan = backups._scan_backups
    calls = 0

    def blocked_scan(path: Path) -> tuple[list[backups.BackupEntry], int]:
        nonlocal calls
        entries = scan(path)
        calls += 1
        if calls == 1:
            entered.set()
            assert release.wait(2)
        return entries

    monkeypatch.setattr(backups, "_scan_backups", blocked_scan)
    monkeypatch.setattr(service, "SETTINGS", SimpleNamespace(backup_dir=directory))
    monkeypatch.setattr(service, "ensure_runtime_dirs", lambda: None)
    manager = BackupRetentionManager(directory, _policy(max_count=1, recent_grace_sec=60))
    manager.schedule()
    assert entered.wait(2)
    try:
        published = Path(service._backup_file(source, "a" * 64))
        manager.schedule(published)
    finally:
        release.set()
    assert manager.flush(3)

    assert published.read_text(encoding="utf-8") == "recoverable"
    assert list(directory.iterdir()) == [published]
    assert calls == 2
    result = manager.snapshot()["last_result"]
    assert isinstance(result, dict)
    assert result["remaining_files"] == 1
    assert result["removed_for_count"] == 1
    assert result["quota_satisfied"] is True


@pytest.mark.parametrize("configured, expected", [(None, 0), ("13", 13), ("-1", 0), ("2000000", 1_000_000), ("bad", 0)])
def test_backup_count_setting_defaults_bounds_and_policy(
    monkeypatch: pytest.MonkeyPatch, configured: str | None, expected: int,
) -> None:
    if configured is None:
        monkeypatch.delenv("MCP_BACKUP_MAX_COUNT", raising=False)
    else:
        monkeypatch.setenv("MCP_BACKUP_MAX_COUNT", configured)
    result = subprocess.run(
        [sys.executable, "-c", "import json; from core.config import Settings; "
         "from core.backups import policy_from_settings; s = Settings(); "
         "print(json.dumps([s.backup_max_count, s.resource_budgets()['backup_max_count'], policy_from_settings(s).max_count]))"],
        cwd=Path(__file__).resolve().parent.parent,
        check=True, capture_output=True, text=True, timeout=10,
    )

    assert json.loads(result.stdout) == [expected, expected, expected]


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


def test_backup_cleanup_never_enters_linked_state_or_linked_backup(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    old = outside / "old.bak"
    _backup(old, 100, 100)
    _backup(outside / "new.bak", 100, 200)
    link = tmp_path / "backups"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except (NotImplementedError, OSError):
        pytest.skip("Directory links are unavailable")
    assert cleanup_backups(link, _policy(max_age_sec=10), now=1_000).removed_files == 0
    assert old.exists()
    link.unlink()
    link.mkdir()
    linked_backup = link / "old.bak"
    linked_backup.symlink_to(old)
    _backup(link / "new.bak", 100, 200)
    assert cleanup_backups(link, _policy(max_age_sec=10), now=1_000).removed_files == 0
    assert linked_backup.is_symlink() and old.exists()


def test_backup_cleanup_skips_linked_state_parent(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    backups = outside / "backups"
    backups.mkdir(parents=True)
    old = backups / "old.bak"
    _backup(old, 100, 100)
    _backup(backups / "new.bak", 100, 200)
    try:
        (tmp_path / ".agent_state").symlink_to(outside, target_is_directory=True)
    except (NotImplementedError, OSError):
        pytest.skip("Directory links are unavailable")
    result = cleanup_backups(tmp_path / ".agent_state" / "backups", _policy(max_age_sec=10), now=1_000)
    assert result.removed_files == 0
    assert old.exists()


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
