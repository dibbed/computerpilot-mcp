"""Process-local resource locks; external writers still need their own coordination."""

from __future__ import annotations

import os
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from core.config import resolve_path


def canonical_path(path: str | Path) -> str:
    """Resolve existing Windows junctions/aliases, including parents of new files."""
    return os.path.normcase(os.path.normpath(str(resolve_path(path))))


class ResourceLocks:
    def __init__(self) -> None:
        self._guard = threading.Lock()
        self._entries: dict[str, tuple[threading.RLock, int]] = {}

    @contextmanager
    def sync(self, *paths: str | Path) -> Iterator[None]:
        keys = sorted({canonical_path(path) for path in paths})
        with self._guard:
            entries = []
            for key in keys:
                lock, users = self._entries.get(key, (threading.RLock(), 0))
                self._entries[key] = (lock, users + 1)
                entries.append((key, lock))
        acquired = []
        try:
            for _, lock in entries:
                lock.acquire()
                acquired.append(lock)
            yield
        finally:
            for lock in reversed(acquired):
                lock.release()
            with self._guard:
                for key, lock in entries:
                    users = self._entries[key][1] - 1
                    if users:
                        self._entries[key] = (lock, users)
                    else:
                        del self._entries[key]


RESOURCE_LOCKS = ResourceLocks()
