"""Share in-flight refreshes without caching results or serializing unrelated keys."""

from __future__ import annotations

import threading
from collections.abc import Callable, Hashable
from concurrent.futures import Future
from dataclasses import dataclass
from typing import Generic, TypeVar

K = TypeVar("K", bound=Hashable)
T = TypeVar("T")


@dataclass
class _Flight(Generic[T]):
    result: Future[T]
    owner: int
    follow_up: Callable[[], T] | None = None


class SingleFlight(Generic[K, T]):
    """Callers share one result; dirty requests coalesce to one pending follow-up.

    No result survives completion. Operations must be read-only refreshes, and
    callers must not mutate shared results. A failure wakes every waiter and
    permits the next call to retry. Recursive use of the same key is rejected.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._flights: dict[K, _Flight[T]] = {}

    def run(self, key: K, operation: Callable[[], T], *, dirty: bool = False) -> T:
        with self._lock:
            flight = self._flights.get(key)
            owns = flight is None
            if flight is None:
                flight = _Flight(Future(), threading.get_ident())
                self._flights[key] = flight
            else:
                if flight.owner == threading.get_ident():
                    raise RuntimeError("Recursive single-flight refresh for the same key")
                if dirty:
                    flight.follow_up = operation
        if not owns:
            return flight.result.result()
        try:
            while True:
                value = operation()
                with self._lock:
                    follow_up = flight.follow_up
                    flight.follow_up = None
                    if follow_up is None:
                        del self._flights[key]
                        flight.result.set_result(value)
                        return value
                operation = follow_up
        except BaseException as exc:
            with self._lock:
                self._flights.pop(key, None)
                flight.result.set_exception(exc)
            raise
