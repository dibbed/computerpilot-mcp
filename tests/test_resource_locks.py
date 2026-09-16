import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

import pytest

from core.resource_locks import ResourceLocks, canonical_path


def test_ordered_multi_path_and_cleanup(tmp_path: Path) -> None:
    locks = ResourceLocks()
    a, b = tmp_path / "a", tmp_path / "b"
    with ThreadPoolExecutor(8) as pool:

        def operation(i: int) -> int:
            with locks.sync(*((a, b) if i % 2 else (b, a))):
                with locks.sync(a):
                    return i

        assert list(pool.map(operation, range(100))) == list(range(100))
    assert not locks._entries


def test_independent_paths_overlap(tmp_path: Path) -> None:
    locks = ResourceLocks()
    barrier = Barrier(2)

    def operation(name: str) -> None:
        with locks.sync(tmp_path / name):
            barrier.wait(timeout=3)

    with ThreadPoolExecutor(2) as pool:
        list(pool.map(operation, ["a", "b"]))
    assert not locks._entries


def test_same_resource_never_overlaps_under_contention(tmp_path: Path) -> None:
    locks = ResourceLocks()
    path = tmp_path / "shared.txt"
    gate = threading.Event()
    state_lock = threading.Lock()
    active = 0
    peak = 0

    def operation(_: int) -> None:
        nonlocal active, peak
        gate.wait(timeout=3)
        with locks.sync(path):
            with state_lock:
                active += 1
                peak = max(peak, active)
            time.sleep(0.002)
            with state_lock:
                active -= 1

    with ThreadPoolExecutor(max_workers=16) as pool:
        futures = [pool.submit(operation, index) for index in range(64)]
        gate.set()
        for future in futures:
            future.result(timeout=5)

    assert peak == 1
    assert active == 0
    assert not locks._entries


def test_windows_path_alias(tmp_path: Path) -> None:
    path = tmp_path / "MixedCase.txt"
    path.touch()
    assert canonical_path(path) == canonical_path(path.parent / "." / path.name)
    if os.name == "nt":
        assert canonical_path(path) == canonical_path(str(path).upper().replace("\\", "/"))


def test_failure_releases(tmp_path: Path) -> None:
    locks = ResourceLocks()
    with pytest.raises(ValueError), locks.sync(tmp_path):
        raise ValueError()
    assert not locks._entries
