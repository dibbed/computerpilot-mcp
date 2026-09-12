from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, cast

import pytest

from tools.filesystem import registry

ToolFunction = Callable[..., dict[str, Any]]


class ToolCapture:
    def __init__(self) -> None:
        self.functions: dict[str, ToolFunction] = {}

    def tool(self, **kwargs: Any) -> Callable[[ToolFunction], ToolFunction]:
        def decorate(function: ToolFunction) -> ToolFunction:
            self.functions[function.__name__] = function
            return function

        return decorate


def test_fifty_concurrent_edits_keep_all_changes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(registry, "audit_action", lambda *a, **k: None)
    server = ToolCapture()
    registry.register(cast(Any, server))
    path = tmp_path / "data.txt"
    path.write_text("\n".join(f"old_{i}!" for i in range(50)))

    def edit(i: int) -> dict[str, Any]:
        return server.functions["replace_exact"](str(path), f"old_{i}!", f"new_{i}!", backup=False)

    with ThreadPoolExecutor(12) as pool:
        results = list(pool.map(edit, range(50)))
    assert all(result["ok"] for result in results), results
    assert path.read_text() == "\n".join(f"new_{i}!" for i in range(50))


def test_opposite_moves_finish(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(registry, "audit_action", lambda *a, **k: None)
    server = ToolCapture()
    registry.register(cast(Any, server))
    a, b = tmp_path / "a", tmp_path / "b"
    a.write_text("a")
    b.write_text("b")
    with ThreadPoolExecutor(2) as pool:
        futures = [pool.submit(server.functions["move_file"], str(x), str(y), overwrite=True) for x, y in [(a, b), (b, a)]]
        results = [f.result(timeout=5) for f in futures]
    assert all(result["ok"] for result in results), results
