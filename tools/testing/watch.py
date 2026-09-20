"""Bounded filesystem snapshots used by incremental validation watches."""

from __future__ import annotations

from pathlib import Path
from typing import NamedTuple


class FileState(NamedTuple):
    mtime_ns: int
    size: int


DEFAULT_IGNORES = {".git", ".mypy_cache", ".pytest_cache", ".ruff_cache", ".venv", "venv", "__pycache__", "graphify-out"}


def snapshot(root: Path, *, max_files: int = 50_000) -> tuple[dict[str, FileState], bool]:
    files: dict[str, FileState] = {}
    truncated = False
    for path in root.rglob("*"):
        relative = path.relative_to(root)
        if any(part in DEFAULT_IGNORES for part in relative.parts) or not path.is_file():
            continue
        stat = path.stat()
        files[relative.as_posix()] = FileState(stat.st_mtime_ns, stat.st_size)
        if len(files) >= max_files:
            truncated = True
            break
    return files, truncated


def diff_snapshots(before: dict[str, FileState], after: dict[str, FileState]) -> list[str]:
    return sorted(path for path in before.keys() | after.keys() if before.get(path) != after.get(path))
