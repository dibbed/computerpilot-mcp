from __future__ import annotations

from pathlib import Path

from tools.testing.watch import diff_snapshots, snapshot


def test_snapshot_detects_create_change_delete_and_ignores(tmp_path: Path) -> None:
    source = tmp_path / "a.py"
    source.write_text("one\n", encoding="utf-8")
    ignored = tmp_path / ".git" / "index"
    ignored.parent.mkdir()
    ignored.write_text("ignored", encoding="utf-8")
    before, truncated = snapshot(tmp_path)
    assert truncated is False
    assert list(before) == ["a.py"]

    source.write_text("changed and larger\n", encoding="utf-8")
    created = tmp_path / "b.py"
    created.write_text("new\n", encoding="utf-8")
    after, _ = snapshot(tmp_path)
    assert diff_snapshots(before, after) == ["a.py", "b.py"]

    source.unlink()
    final, _ = snapshot(tmp_path)
    assert diff_snapshots(after, final) == ["a.py"]


def test_snapshot_reports_scan_truncation(tmp_path: Path) -> None:
    for index in range(3):
        (tmp_path / f"{index}.py").write_text("x", encoding="utf-8")
    result, truncated = snapshot(tmp_path, max_files=2)
    assert len(result) == 2
    assert truncated is True
