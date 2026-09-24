"""Portable inter-process exclusive file locking."""

from __future__ import annotations

import importlib
import os
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any


def _timeout_message(path: Path, label: str | None) -> str:
    subject = label or f"file {path}"
    return f"Timed out locking {subject}."


@contextmanager
def exclusive_file_lock(
    path: Path,
    *,
    timeout_sec: float | None = None,
    poll_interval_sec: float = 0.01,
    label: str | None = None,
) -> Iterator[None]:
    """Hold one cross-process exclusive lock without importing foreign-OS APIs statically."""

    path.parent.mkdir(parents=True, exist_ok=True)
    deadline = None if timeout_sec is None else time.monotonic() + max(timeout_sec, 0.0)

    with path.open("a+b", buffering=0) as handle:
        handle.seek(0)
        if os.name == "nt":
            msvcrt: Any = importlib.import_module("msvcrt")
            while True:
                try:
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError:
                    if deadline is not None and time.monotonic() >= deadline:
                        raise TimeoutError(_timeout_message(path, label)) from None
                    time.sleep(poll_interval_sec)
            try:
                yield
            finally:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            return

        fcntl: Any = importlib.import_module("fcntl")
        if deadline is None:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        else:
            while True:
                try:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except (BlockingIOError, OSError):
                    if time.monotonic() >= deadline:
                        raise TimeoutError(_timeout_message(path, label)) from None
                    time.sleep(poll_interval_sec)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
