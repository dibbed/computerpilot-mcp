from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path
from typing import Any, cast

import pytest
from mcp import Client
from mcp.server import MCPServer

from core.errors import ToolError
from tools.language import edits, registry
from tools.language.edits import apply_workspace_edit, prepare_workspace_edit


@pytest.fixture(autouse=True)
def _workspace_journal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    from types import SimpleNamespace

    from core import recovery
    from core.backups import BackupPolicy, BackupRetentionManager
    from tools.filesystem import service

    journal = tmp_path / "runtime" / "operation-recovery.jsonl"
    journal.parent.mkdir()
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    monkeypatch.setattr(edits, "SETTINGS", SimpleNamespace(backup_dir=backup_dir))
    monkeypatch.setattr(edits, "BACKUP_RETENTION", BackupRetentionManager(
        backup_dir, BackupPolicy(max_bytes=0, max_age_sec=0, cleanup_interval_sec=0, recent_grace_sec=0),
    ))
    monkeypatch.setattr(service, "SETTINGS", SimpleNamespace(backup_dir=backup_dir))
    monkeypatch.setattr(service, "ensure_runtime_dirs", lambda: None)
    recovery.OPERATION_RECOVERY.configure(journal, "workspace-edit-tests")
    try:
        yield recovery.OPERATION_RECOVERY
    finally:
        recovery.OPERATION_RECOVERY.configure(None, None)


def _edit(path: Path) -> dict[str, object]:
    return {
        "changes": {
            path.as_uri(): [
                {"range": {"start": {"line": 0, "character": 0}, "end": {"line": 0, "character": 3}}, "newText": "new"},
                {"range": {"start": {"line": 0, "character": 4}, "end": {"line": 0, "character": 7}}, "newText": "name"},
            ]
        }
    }


def test_workspace_edit_plans_and_applies_multi_edit_with_hash_guard(tmp_path: Path) -> None:
    path = tmp_path / "a.py"
    path.write_text("old key\n", encoding="utf-8")
    expected = hashlib.sha256(path.read_bytes()).hexdigest()
    plan = prepare_workspace_edit(tmp_path, _edit(path), expected_sha256={"a.py": expected})
    result = apply_workspace_edit(plan)
    assert result["file_count"] == 1
    assert path.read_text(encoding="utf-8") == "new name\n"


def test_workspace_edit_rejects_overlap_stale_hash_and_external_path(tmp_path: Path) -> None:
    path = tmp_path / "a.py"
    path.write_text("old key\n", encoding="utf-8")
    with pytest.raises(ToolError, match="precondition"):
        prepare_workspace_edit(tmp_path, _edit(path), expected_sha256={"a.py": "0" * 64})
    overlap = _edit(path)
    overlap["changes"][path.as_uri()].append(  # type: ignore[index]
        {"range": {"start": {"line": 0, "character": 1}, "end": {"line": 0, "character": 5}}, "newText": "x"}
    )
    with pytest.raises(ToolError, match="overlap"):
        prepare_workspace_edit(tmp_path, overlap)
    with pytest.raises(ToolError, match="outside"):
        prepare_workspace_edit(tmp_path, {"changes": {(tmp_path.parent / "x.py").as_uri(): []}})


def test_workspace_edit_accepts_text_document_edits_and_rejects_resource_operations(tmp_path: Path) -> None:
    path = tmp_path / "a.py"
    path.write_text("old\n", encoding="utf-8")
    plan = prepare_workspace_edit(
        tmp_path,
        {
            "documentChanges": [
                {
                    "textDocument": {"uri": path.as_uri(), "version": 1},
                    "edits": [{"range": {"start": {"line": 0, "character": 0}, "end": {"line": 0, "character": 3}}, "newText": "new"}],
                }
            ]
        },
        document_versions={path.as_uri(): 1},
    )
    apply_workspace_edit(plan)
    assert path.read_text(encoding="utf-8") == "new\n"
    with pytest.raises(ToolError, match="resource operations"):
        prepare_workspace_edit(tmp_path, {"documentChanges": [{"kind": "create", "uri": (tmp_path / "b.py").as_uri()}]})


class _RecordingCache:
    def __init__(self) -> None:
        self.paths: list[Path] = []

    def invalidate(self, path: str | Path, *, recursive: bool = False) -> None:
        self.paths.append(Path(path))


class _ClosingManager:
    def __init__(self) -> None:
        self.closed: list[Path] = []

    def close_workspace(self, root: Path, *, timeout_sec: float = 2.0) -> None:
        self.closed.append(Path(root))


def _structured(result: Any) -> dict[str, Any]:
    assert isinstance(result.structured_content, dict)
    return result.structured_content


def test_success_invalidates_context_and_lsp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "a.py"
    target.write_text("old\n", encoding="utf-8")
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    python_cache, context_cache = _RecordingCache(), _RecordingCache()
    manager = _ClosingManager()
    monkeypatch.setattr(edits, "PYTHON_METADATA_CACHE", python_cache)
    monkeypatch.setattr(edits, "CONTEXT_CACHE", context_cache)
    action = {
        "edit": {
            "changes": {
                target.as_uri(): [
                    {"range": {"start": {"line": 0, "character": 0}, "end": {"line": 0, "character": 3}}, "newText": "new"}
                ]
            }
        }
    }

    async def scenario() -> None:
        server = MCPServer("semantic-cache")
        registry.register(server, manager=cast(Any, manager))
        async with Client(server, raise_exceptions=True) as client:
            dry = _structured(
                await client.call_tool(
                    "apply_code_action",
                    {
                        "root": str(tmp_path),
                        "action": action,
                        "expected_sha256": {"a.py": digest},
                        "dry_run": True,
                    },
                )
            )
            assert dry["ok"] is True
            assert manager.closed == []
            assert python_cache.paths == []
            assert context_cache.paths == []

            applied = _structured(
                await client.call_tool(
                    "apply_code_action",
                    {
                        "root": str(tmp_path),
                        "action": action,
                        "expected_sha256": {"a.py": digest},
                    },
                )
            )
            assert applied["ok"] is True

    asyncio.run(scenario())
    assert target.read_text(encoding="utf-8") == "new\n"
    assert python_cache.paths == [target]
    assert context_cache.paths == [target]
    assert manager.closed == [tmp_path.resolve()]


class _RenameResponse(dict[str, object]):
    def __init__(self, payload: dict[str, Any], uri: str, version: int, digest: str) -> None:
        super().__init__(payload)
        self.snapshot_version = {uri: version}
        self.source_sha256 = {uri: digest}


class _RenameManager:
    def __init__(self, response: _RenameResponse) -> None:
        self.response = response
        self.closed: list[Path] = []

    async def arequest(
        self,
        root: Path,
        language_id: str,
        method: str,
        params: dict[str, object],
        *,
        timeout_sec: float,
    ) -> object:
        return self.response

    def close_workspace(self, root: Path, *, timeout_sec: float = 2.0) -> None:
        self.closed.append(Path(root))


def test_rename_checks_server_version_and_source_snapshot(
    tmp_path: Path,
) -> None:
    target = tmp_path / "a.py"
    target.write_text("old\n", encoding="utf-8")
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    uri = target.as_uri()
    payload = {
        "documentChanges": [
            {
                "textDocument": {"uri": uri, "version": 1},
                "edits": [
                    {"range": {"start": {"line": 0, "character": 0}, "end": {"line": 0, "character": 3}}, "newText": "new"}
                ],
            }
        ]
    }
    manager = _RenameManager(_RenameResponse(payload, uri, 2, digest))

    async def scenario() -> None:
        server = MCPServer("semantic-rename")
        registry.register(server, manager=cast(Any, manager))
        async with Client(server, raise_exceptions=True) as client:
            stale_version = _structured(
                await client.call_tool(
                    "rename_symbol",
                    {
                        "root": str(tmp_path),
                        "document_uri": uri,
                        "line": 0,
                        "character": 0,
                        "new_name": "new",
                        "expected_sha256": {"a.py": digest},
                    },
                )
            )
            assert stale_version["ok"] is False
            assert stale_version["error"] == "lsp_document_version_conflict"

            manager.response = _RenameResponse(payload, uri, 1, "0" * 64)
            stale_source = _structured(
                await client.call_tool(
                    "rename_symbol",
                    {
                        "root": str(tmp_path),
                        "document_uri": uri,
                        "line": 0,
                        "character": 0,
                        "new_name": "new",
                        "expected_sha256": {"a.py": digest},
                    },
                )
            )
            assert stale_source["ok"] is False
            assert stale_source["error"] == "lsp_result_stale"

            manager.response = _RenameResponse(payload, uri, 1, digest)
            applied = _structured(
                await client.call_tool(
                    "rename_symbol",
                    {
                        "root": str(tmp_path),
                        "document_uri": uri,
                        "line": 0,
                        "character": 0,
                        "new_name": "new",
                        "expected_sha256": {"a.py": digest},
                    },
                )
            )
            assert applied["ok"] is True

    asyncio.run(scenario())
    assert target.read_text(encoding="utf-8") == "new\n"
    assert manager.closed == [tmp_path.resolve()]
