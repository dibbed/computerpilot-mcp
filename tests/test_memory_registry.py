from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, cast

import pytest

from tools.memory import registry

ToolFunction = Callable[..., dict[str, Any]]


def test_concurrent_memory_merge(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    functions: dict[str, ToolFunction] = {}

    class Capture:
        def tool(self, **kwargs: Any) -> Callable[[ToolFunction], ToolFunction]:
            def decorate(fn: ToolFunction) -> ToolFunction:
                functions[fn.__name__] = fn
                return fn

            return decorate

    monkeypatch.setattr(registry, "_path", lambda name: tmp_path / f"{name}.json")
    monkeypatch.setattr(registry, "audit_action", lambda *a, **k: None)
    registry.register(cast(Any, Capture()))

    def update(i: int) -> dict[str, Any]:
        return functions["memory_update"]("same", previous_fixes=[f"fix {i}"])

    with ThreadPoolExecutor(12) as pool:
        results = list(pool.map(update, range(50)))
    assert all(result["ok"] for result in results), results
    assert set(registry._load("same")["previous_fixes"]) == {f"fix {i}" for i in range(50)}
