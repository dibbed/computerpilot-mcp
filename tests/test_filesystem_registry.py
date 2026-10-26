import sqlite3
import sys
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from typing import Any, cast

import pytest

from core import config
from core.resource_claims import coordination_claim, make_resource_capability, path_claim, resource_capability
from core.workflow_store import WorkflowStore
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


@pytest.mark.parametrize("tool", ["write_file", "create_file", "delete_file", "move_file", "copy_file", "replace_exact"])
def test_native_writers_reject_read_only_parent_before_effect(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, tool: str,
) -> None:
    monkeypatch.setattr(registry, "audit_action", lambda *a, **k: None)
    server = ToolCapture()
    registry.register(cast(Any, server))
    target = tmp_path / "data.txt"
    target.write_text("before", encoding="utf-8")
    destination = tmp_path / "destination.txt"
    resources = WorkflowStore(tmp_path / "workflows.sqlite3", recover=False)
    resources.reserve_resource_claims(
        request_id="reader", owner_token="owner", category="test",
        claims=(coordination_claim(), path_claim(tmp_path, "read", scope="subtree")),
    )
    arguments: dict[str, dict[str, Any]] = {
        "write_file": {"path": str(target), "content": "after", "backup": False},
        "create_file": {"path": str(destination), "content": "after"},
        "delete_file": {"path": str(target)},
        "move_file": {"source": str(target), "destination": str(destination)},
        "copy_file": {"source": str(target), "destination": str(destination)},
        "replace_exact": {"path": str(target), "old": "before", "new": "after", "backup": False},
    }

    with resource_capability(make_resource_capability("reader", "owner", resources.path)):
        result = server.functions[tool](**arguments[tool])

    assert result["ok"] is False
    assert result["error"] == "resource_capability_scope_violation"
    assert target.read_text(encoding="utf-8") == "before"
    assert not destination.exists()


def test_native_writer_waits_for_shared_reader_without_holding_local_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(config, "SETTINGS", replace(config.SETTINGS, state_dir=tmp_path))
    monkeypatch.setattr(registry, "audit_action", lambda *a, **k: None)
    server = ToolCapture()
    registry.register(cast(Any, server))
    target = tmp_path / "data.txt"
    target.write_text("before", encoding="utf-8")
    resources = WorkflowStore(config.SETTINGS.workflow_db, recover=False)
    resources.reserve_resource_claims(
        request_id="reader", owner_token="owner", category="workflow_step",
        claims=(coordination_claim(), path_claim(target, "read")),
    )
    with ThreadPoolExecutor(1) as pool:
        future = pool.submit(server.functions["write_file"], str(target), "after", backup=False)
        try:
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                with sqlite3.connect(resources.path) as db:
                    pending = db.execute("SELECT COUNT(*) FROM workflow_resource_requests WHERE state='pending'").fetchone()[0]
                if pending:
                    break
                time.sleep(0.01)
            assert pending == 1
            assert not future.done()
            assert target.read_text(encoding="utf-8") == "before"
            assert not registry.RESOURCE_LOCKS._entries
        finally:
            resources.release_resource_request("reader", owner_token="owner")
        assert future.result(timeout=5)["ok"] is True
    assert target.read_text(encoding="utf-8") == "after"


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


def test_known_independent_edits_have_equal_sequential_refactor_and_patch_results(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import difflib
    import hashlib

    settings = replace(config.SETTINGS, state_dir=tmp_path / "state")
    monkeypatch.setattr(config, "SETTINGS", settings)
    monkeypatch.setattr(registry.service, "SETTINGS", settings)
    monkeypatch.setattr(registry, "audit_action", lambda *a, **k: None)
    server = ToolCapture()
    registry.register(cast(Any, server))
    before = "alpha\nbeta\ngamma\ndelta\n"
    after = before.upper()
    paths = {mode: tmp_path / f"{mode}.txt" for mode in ("sequential", "refactor", "patch")}
    for target in paths.values():
        target.write_bytes(before.encode())
    for word in before.splitlines():
        result = server.functions["replace_exact"](str(paths["sequential"]), word, word.upper(), backup=False)
        assert result["ok"], result
    edits = [registry.RefactorEdit(mode="exact", old=word, new=word.upper()) for word in before.splitlines()]
    result = server.functions["safe_refactor"](str(paths["refactor"]), edits)
    assert result["ok"], result
    patch = "diff --git a/patch.txt b/patch.txt\n" + "".join(
        difflib.unified_diff(before.splitlines(True), after.splitlines(True), "a/patch.txt", "b/patch.txt"),
    )
    result = server.functions["apply_patch"](
        str(tmp_path), patch, expected_sha256={"patch.txt": hashlib.sha256(before.encode()).hexdigest()},
    )
    assert result["ok"], result
    assert {target.read_bytes() for target in paths.values()} == {after.encode()}


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


def test_failed_safe_refactor_invalidates_after_rollback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    capture = InvalidationCapture()
    monkeypatch.setattr(registry, "PYTHON_METADATA_CACHE", capture)
    monkeypatch.setattr(registry, "audit_action", lambda *a, **k: None)
    server = ToolCapture()
    registry.register(cast(Any, server))
    target = tmp_path / "module.py"
    original = "VALUE = 1\n"
    target.write_text(original, encoding="utf-8")

    result = server.functions["safe_refactor"](
        str(target),
        [registry.RefactorEdit(mode="exact", old="1", new="2")],
        validation_command=[sys.executable, "-c", "raise SystemExit(7)"],
        validation_cwd=str(tmp_path),
        timeout_sec=10,
    )

    assert result["ok"] is False
    assert result["error"] == "validation_failed_rolled_back"
    assert target.read_text(encoding="utf-8") == original
    assert capture.calls == [(target, False)]
