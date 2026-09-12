from __future__ import annotations

import threading
from concurrent.futures import Future, ThreadPoolExecutor

import pytest

from core import singleflight
from core.singleflight import SingleFlight


@pytest.mark.parametrize("dirty,fail", [(False, False), (True, False), (False, True)])
def test_coalesces_waiters_and_dirty_refresh_then_releases_state(
    monkeypatch: pytest.MonkeyPatch, dirty: bool, fail: bool,
) -> None:
    joined = threading.Barrier(9)
    started = threading.Event()
    release = threading.Event()
    calls = []

    class ObservedFuture(Future[int]):
        def result(self, timeout: float | None = None) -> int:
            joined.wait(timeout=5)
            return super().result(timeout=timeout)

    monkeypatch.setattr(singleflight, "Future", ObservedFuture)
    refresh: SingleFlight[str, int] = SingleFlight()

    def operation() -> int:
        calls.append(1)
        started.set()
        assert release.wait(5)
        if fail:
            raise ValueError("refresh failed")
        return len(calls)

    with ThreadPoolExecutor(max_workers=9) as pool:
        owner = pool.submit(refresh.run, "same", operation)
        assert started.wait(5)
        waiters = [pool.submit(refresh.run, "same", operation, dirty=dirty) for _ in range(8)]
        joined.wait(timeout=5)
        release.set()
        for result in [owner, *waiters]:
            if fail:
                with pytest.raises(ValueError, match="refresh failed"):
                    result.result(timeout=5)
            else:
                assert result.result(timeout=5) == (2 if dirty else 1)
    assert len(calls) == (2 if dirty else 1)
    assert refresh._flights == {}
    assert refresh.run("same", lambda: 42) == 42


def test_unrelated_keys_run_in_parallel() -> None:
    refresh: SingleFlight[str, int] = SingleFlight()
    barrier = threading.Barrier(2)

    def operation() -> int:
        barrier.wait(timeout=5)
        return 1

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(refresh.run, "a", operation)
        second = pool.submit(refresh.run, "b", operation)
        assert first.result(timeout=5) == second.result(timeout=5) == 1


def test_recursive_refresh_fails_without_leaking_flight() -> None:
    refresh: SingleFlight[str, int] = SingleFlight()
    with pytest.raises(RuntimeError, match="Recursive"):
        refresh.run("a", lambda: refresh.run("a", lambda: 1))
    assert refresh._flights == {}
