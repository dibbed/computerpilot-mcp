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


class InvalidationCapture:
    def __init__(self) -> None:
        self.calls: list[tuple[Path, bool]] = []

    def invalidate(self, path: str | Path, *, recursive: bool = False) -> int:
        self.calls.append((Path(path), recursive))
        return 0

    def clear(self) -> None:
        self.calls.clear()


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


def test_changed_write_and_surgical_edits_invalidate_project_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    capture = InvalidationCapture()
    monkeypatch.setattr(registry, "PYTHON_METADATA_CACHE", capture)
    monkeypatch.setattr(registry, "audit_action", lambda *a, **k: None)
    server = ToolCapture()
    registry.register(cast(Any, server))
    target = tmp_path / "app.py"
    initial = "def answer():\n    return 1\n"
    server.functions["create_file"](str(target), initial)
    assert capture.calls == [(target, False)]
    capture.clear()

    unchanged = server.functions["write_file"](str(target), initial, backup=False)
    assert unchanged["changed"] is False
    assert capture.calls == []

    changed = server.functions["write_file"](str(target), "def answer():\n    return 2\n", backup=False)
    assert changed["changed"] is True
    assert capture.calls == [(target, False)]

    capture.clear()
    server.functions["replace_exact"](str(target), "return 2", "return 3", backup=False)
    assert capture.calls == [(target, False)]

    capture.clear()
    server.functions["replace_function"](str(target), "answer", "return 4", backup=False)
    assert capture.calls == [(target, False)]

    capture.clear()
    server.functions["safe_refactor"](
        str(target),
        [registry.RefactorEdit(mode="exact", old="return 4", new="return 5")],
    )
    assert capture.calls == [(target, False)]


def test_copy_move_delete_invalidate_destination_and_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    capture = InvalidationCapture()
    monkeypatch.setattr(registry, "PYTHON_METADATA_CACHE", capture)
    monkeypatch.setattr(registry, "audit_action", lambda *a, **k: None)
    server = ToolCapture()
    registry.register(cast(Any, server))
    source = tmp_path / "source.py"
    copied = tmp_path / "copied.py"
    moved = tmp_path / "moved.py"
    source.write_text("def value():\n    return 1\n", encoding="utf-8")

    server.functions["copy_file"](str(source), str(copied))
    assert capture.calls == [(copied, False)]

    capture.clear()
    server.functions["move_file"](str(copied), str(moved))
    assert capture.calls == [(copied, False), (moved, False)]

    capture.clear()
    server.functions["delete_file"](str(moved))
    assert capture.calls == [(moved, False)]


def test_directory_move_and_delete_use_recursive_invalidation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    capture = InvalidationCapture()
    monkeypatch.setattr(registry, "PYTHON_METADATA_CACHE", capture)
    monkeypatch.setattr(registry, "audit_action", lambda *a, **k: None)
    server = ToolCapture()
    registry.register(cast(Any, server))
    source = tmp_path / "pkg"
    destination = tmp_path / "pkg2"
    source.mkdir()
    (source / "module.py").write_text("def value():\n    return 1\n", encoding="utf-8")

    server.functions["move_file"](str(source), str(destination))
    assert capture.calls == [(source, True), (destination, True)]

    capture.clear()
    server.functions["delete_file"](str(destination), recursive=True)
    assert capture.calls == [(destination, True)]
