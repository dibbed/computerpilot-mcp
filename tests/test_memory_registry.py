from __future__ import annotations

import json
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, cast

import pytest

from core.memory_store import MEMORY_SCHEMA_VERSION
from tools.memory import registry

ToolFunction = Callable[..., dict[str, Any]]


def _functions(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, ToolFunction]:
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
    return functions


def test_concurrent_memory_merge(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    functions = _functions(tmp_path, monkeypatch)

    def update(i: int) -> dict[str, Any]:
        return functions["memory_update"]("same", previous_fixes=[f"fix {i}"])

    with ThreadPoolExecutor(12) as pool:
        results = list(pool.map(update, range(50)))
    assert all(result["ok"] for result in results), results
    records = registry._load("same")["previous_fixes"]
    assert {record["text"] for record in records} == {f"fix {i}" for i in range(50)}
    assert len({record["id"] for record in records}) == 50
    assert all(record["revision"] == 1 for record in records)


def test_legacy_strings_load_as_stable_versioned_records_without_rewriting(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    functions = _functions(tmp_path, monkeypatch)
    path = tmp_path / "legacy.json"
    legacy = {
        "project": "legacy",
        "updated_at": "2026-01-01T00:00:00+00:00",
        "architecture_decisions": ["Keep it bounded."],
        "important_paths": [],
        "user_preferences": [],
        "previous_fixes": [],
    }
    path.write_text(json.dumps(legacy), encoding="utf-8")
    before = path.read_bytes()

    first = functions["memory_read"]("legacy")
    second = functions["memory_read"]("legacy")

    assert path.read_bytes() == before
    assert first["schema_version"] == MEMORY_SCHEMA_VERSION
    assert first["revision"] == 0
    record = first["sections"]["architecture_decisions"][0]
    assert record == second["sections"]["architecture_decisions"][0]
    assert record["text"] == "Keep it bounded."
    assert record["revision"] == 1
    assert len(record["id"]) == 32


def test_first_update_materializes_legacy_records_without_changing_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    functions = _functions(tmp_path, monkeypatch)
    path = tmp_path / "legacy.json"
    path.write_text(
        json.dumps(
            {
                "project": "legacy",
                "updated_at": "2026-01-01T00:00:00+00:00",
                "architecture_decisions": ["Keep it bounded."],
            }
        ),
        encoding="utf-8",
    )
    before = functions["memory_read"]("legacy")["sections"]["architecture_decisions"][0]

    result = functions["memory_update"]("legacy", architecture_decisions=["Keep it bounded."])
    saved = json.loads(path.read_text(encoding="utf-8"))
    after = functions["memory_read"]("legacy")["sections"]["architecture_decisions"][0]

    assert result["changed"] is False
    assert result["revision"] == 0
    assert saved["schema_version"] == MEMORY_SCHEMA_VERSION
    assert saved["architecture_decisions"][0] == before == after


def test_memory_updates_increment_project_revision_only_on_semantic_change(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    functions = _functions(tmp_path, monkeypatch)

    first = functions["memory_update"]("project", previous_fixes=["fixed race"])
    duplicate = functions["memory_update"]("project", previous_fixes=["fixed race"])
    second = functions["memory_update"]("project", previous_fixes=["fixed another race"])

    assert first["revision"] == 1
    assert first["changed"] is True
    assert duplicate["revision"] == 1
    assert duplicate["changed"] is False
    assert second["revision"] == 2
    assert second["changed"] is True


def test_replace_preserves_record_identity_for_unchanged_text(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    functions = _functions(tmp_path, monkeypatch)
    functions["memory_update"]("project", important_paths=["one", "two"])
    before = functions["memory_read"]("project", section="important_paths", max_items=10)
    one = next(item for item in before["items"] if item["text"] == "one")

    replaced = functions["memory_update"]("project", important_paths=["one", "three"], replace=True)
    after = functions["memory_read"]("project", section="important_paths", max_items=10)
    one_after = next(item for item in after["items"] if item["text"] == "one")

    assert replaced["revision"] == 2
    assert one_after["id"] == one["id"]
    assert one_after["revision"] == one["revision"]
    assert {item["text"] for item in after["items"]} == {"one", "three"}


def test_future_memory_schema_is_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    functions = _functions(tmp_path, monkeypatch)
    path = tmp_path / "future.json"
    path.write_text(json.dumps({"schema_version": MEMORY_SCHEMA_VERSION + 1, "project": "future"}), encoding="utf-8")

    result = functions["memory_read"]("future")
    assert result["ok"] is False
    assert result["error"] == "memory_schema_newer"
    assert "newer than supported" in result["message"]
