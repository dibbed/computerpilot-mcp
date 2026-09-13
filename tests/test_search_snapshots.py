from __future__ import annotations

import asyncio
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

import pytest

from core.errors import ToolError
from core.registry import create_server
from tools.filesystem import registry, service
from tools.filesystem.search_snapshots import SearchSnapshotStore, search_fingerprint

ToolFunction = Callable[..., dict[str, Any]]


class ToolCapture:
    def __init__(self) -> None:
        self.functions: dict[str, ToolFunction] = {}

    def tool(self, **kwargs: Any) -> Callable[[ToolFunction], ToolFunction]:
        def decorate(function: ToolFunction) -> ToolFunction:
            self.functions[function.__name__] = function
            return function

        return decorate


def _store(
    tmp_path: Path,
    *,
    clock: Callable[[], float] | None = None,
    max_bytes: int = 1_000_000,
    max_count: int = 8,
) -> SearchSnapshotStore:
    return SearchSnapshotStore(
        tmp_path / "snapshots",
        ttl_sec=60,
        max_bytes=max_bytes,
        max_count=max_count,
        clock=clock or (lambda: 100.0),
    )


def _fingerprint(root: Path, query: str = "hit_") -> str:
    return search_fingerprint(
        root,
        {
            "query": query,
            "search_type": "name",
            "regex": False,
            "case_sensitive": False,
            "glob": None,
            "include_hidden": False,
            "exclude_common": True,
            "count_mode": "exact",
            "max_scan_files": 100_000,
            "timeout_sec": 30.0,
        },
    )


def test_snapshot_page_two_does_not_rescan_and_keeps_fixed_view(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    for index in range(8):
        (tmp_path / f"hit_{index:02d}.txt").write_text("", encoding="utf-8")
    store = _store(tmp_path / "state")
    monkeypatch.setattr(registry, "SEARCH_SNAPSHOTS", store)
    server = ToolCapture()
    registry.register(cast(Any, server))

    first = server.functions["search_files"](
        str(tmp_path),
        "hit_",
        search_type="name",
        snapshot=True,
        max_results=3,
    )
    assert first["ok"] is True
    assert first["snapshot_created"] is True
    assert first["from_snapshot"] is True
    assert first["total_count"] == 8
    assert first["cursor"] is not None
    assert [Path(item["path"]).name for item in first["items"]] == ["hit_00.txt", "hit_01.txt", "hit_02.txt"]

    (tmp_path / "hit_04.txt").unlink()
    (tmp_path / "new_hit.txt").write_text("", encoding="utf-8")

    def unexpected_rescan(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("snapshot continuation rescanned the filesystem")

    monkeypatch.setattr(service, "search_by_name", unexpected_rescan)
    second = server.functions["search_files"](
        str(tmp_path),
        "hit_",
        search_type="name",
        cursor=first["cursor"],
        max_results=3,
    )

    assert second["ok"] is True
    assert second["scanned_files"] == 0
    assert second["from_snapshot"] is True
    assert [Path(item["path"]).name for item in second["items"]] == ["hit_03.txt", "hit_04.txt", "hit_05.txt"]
    assert "new_hit.txt" not in {Path(item["path"]).name for item in second["items"]}
    assert second["cursor"] is not None


def test_cursor_continues_even_if_original_root_was_deleted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "project"
    root.mkdir()
    for index in range(4):
        (root / f"hit_{index}.txt").write_text("", encoding="utf-8")
    store = _store(tmp_path / "state")
    monkeypatch.setattr(registry, "SEARCH_SNAPSHOTS", store)
    server = ToolCapture()
    registry.register(cast(Any, server))

    first = server.functions["search_files"](
        str(root), "hit_", search_type="name", snapshot=True, max_results=2,
    )
    for child in root.iterdir():
        child.unlink()
    root.rmdir()

    second = server.functions["search_files"](
        str(root), "hit_", search_type="name", cursor=first["cursor"], max_results=2,
    )
    assert second["ok"] is True
    assert [Path(item["path"]).name for item in second["items"]] == ["hit_2.txt", "hit_3.txt"]
    assert second["has_more"] is False
    assert second["cursor"] is None


def test_snapshot_cursor_is_bound_to_query_parameters(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "hit_a.txt").write_text("", encoding="utf-8")
    (tmp_path / "hit_b.txt").write_text("", encoding="utf-8")
    store = _store(tmp_path / "state")
    monkeypatch.setattr(registry, "SEARCH_SNAPSHOTS", store)
    server = ToolCapture()
    registry.register(cast(Any, server))

    first = server.functions["search_files"](
        str(tmp_path), "hit_", search_type="name", snapshot=True, max_results=1,
    )
    mismatch = server.functions["search_files"](
        str(tmp_path), "other", search_type="name", cursor=first["cursor"], max_results=1,
    )

    assert mismatch["ok"] is False
    assert mismatch["error"] == "search_cursor_mismatch"


def test_fast_mode_snapshot_is_explicit_full_snapshot_with_unknown_total(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    for index in range(6):
        (tmp_path / f"hit_{index}.txt").write_text("", encoding="utf-8")
    store = _store(tmp_path / "state")
    monkeypatch.setattr(registry, "SEARCH_SNAPSHOTS", store)
    monkeypatch.setattr(
        service,
        "search_by_name_streaming",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("snapshot mode must not use early-stop traversal")),
    )
    server = ToolCapture()
    registry.register(cast(Any, server))

    first = server.functions["search_files"](
        str(tmp_path),
        "hit_",
        search_type="name",
        count_mode="none",
        snapshot=True,
        max_results=2,
    )

    assert first["ok"] is True
    assert first["count_mode"] == "none"
    assert first["result_order"] == "traversal"
    assert first["total_count"] is None
    assert first["count"] == 2
    assert first["cursor"] is not None


def test_store_expiry_deletes_snapshot_and_rejects_cursor(tmp_path: Path) -> None:
    now = {"value": 100.0}
    store = SearchSnapshotStore(
        tmp_path / "snapshots",
        ttl_sec=10,
        max_bytes=100_000,
        max_count=4,
        clock=lambda: now["value"],
    )
    fingerprint = _fingerprint(tmp_path)
    handle = store.create(
        [{"path": "a", "matched_in": ["name"]}, {"path": "b", "matched_in": ["name"]}],
        fingerprint=fingerprint,
        count_mode="exact",
        result_order="path",
        scan_truncated=False,
        total_count=2,
    )
    cursor = store.cursor(handle.snapshot_id, 0)
    assert store.read_page(cursor, fingerprint=fingerprint, limit=1).items[0]["path"] == "a"

    now["value"] = 111.0
    with pytest.raises(ToolError) as caught:
        store.read_page(cursor, fingerprint=fingerprint, limit=1)
    assert caught.value.code == "search_cursor_expired"
    assert list((tmp_path / "snapshots").glob("*.jsonl")) == []


def test_store_count_quota_evicts_oldest_snapshot(tmp_path: Path) -> None:
    now = {"value": 100.0}
    store = SearchSnapshotStore(
        tmp_path / "snapshots",
        ttl_sec=1_000,
        max_bytes=1_000_000,
        max_count=2,
        clock=lambda: now["value"],
    )
    fingerprint = _fingerprint(tmp_path)
    handles = []
    for index in range(3):
        now["value"] += 1
        handles.append(
            store.create(
                [{"path": f"item-{index}", "matched_in": ["name"]}],
                fingerprint=fingerprint,
                count_mode="exact",
                result_order="path",
                scan_truncated=False,
                total_count=1,
            )
        )

    files = {path.stem for path in (tmp_path / "snapshots").glob("*.jsonl")}
    assert handles[0].snapshot_id not in files
    assert handles[1].snapshot_id in files
    assert handles[2].snapshot_id in files
    assert len(files) == 2


def test_store_byte_quota_evicts_old_snapshot_without_exceeding_budget(tmp_path: Path) -> None:
    now = {"value": 100.0}
    store = SearchSnapshotStore(
        tmp_path / "snapshots",
        ttl_sec=1_000,
        max_bytes=2_000,
        max_count=10,
        clock=lambda: now["value"],
    )
    fingerprint = _fingerprint(tmp_path)
    first = store.create(
        [{"path": "a" * 700, "matched_in": ["name"]}],
        fingerprint=fingerprint,
        count_mode="exact",
        result_order="path",
        scan_truncated=False,
        total_count=1,
    )
    now["value"] += 1
    second = store.create(
        [{"path": "b" * 700, "matched_in": ["name"]}],
        fingerprint=fingerprint,
        count_mode="exact",
        result_order="path",
        scan_truncated=False,
        total_count=1,
    )

    files = {path.stem for path in (tmp_path / "snapshots").glob("*.jsonl")}
    assert second.snapshot_id in files
    assert first.snapshot_id not in files
    assert sum(path.stat().st_size for path in (tmp_path / "snapshots").glob("*.jsonl")) <= 2_000


def test_store_rejects_malformed_and_out_of_range_cursors(tmp_path: Path) -> None:
    store = _store(tmp_path)
    fingerprint = _fingerprint(tmp_path)
    with pytest.raises(ToolError) as malformed:
        store.read_page("../../snapshot", fingerprint=fingerprint, limit=10)
    assert malformed.value.code == "invalid_search_cursor"

    handle = store.create(
        [{"path": "a", "matched_in": ["name"]}],
        fingerprint=fingerprint,
        count_mode="exact",
        result_order="path",
        scan_truncated=False,
        total_count=1,
    )
    with pytest.raises(ToolError) as outside:
        store.read_page(store.cursor(handle.snapshot_id, 2), fingerprint=fingerprint, limit=10)
    assert outside.value.code == "search_cursor_out_of_range"


def test_snapshot_publish_leaves_no_temporary_files(tmp_path: Path) -> None:
    store = _store(tmp_path)
    fingerprint = _fingerprint(tmp_path)
    store.create(
        [{"path": "a", "matched_in": ["name"]}],
        fingerprint=fingerprint,
        count_mode="exact",
        result_order="path",
        scan_truncated=False,
        total_count=1,
    )
    assert list((tmp_path / "snapshots").glob("*.tmp")) == []
    assert len(list((tmp_path / "snapshots").glob("*.jsonl"))) == 1


def test_exact_content_snapshot_continuation_does_not_rescan(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for index in range(4):
        (tmp_path / f"doc_{index}.txt").write_text("needle", encoding="utf-8")
    store = _store(tmp_path / "state")
    monkeypatch.setattr(registry, "SEARCH_SNAPSHOTS", store)
    server = ToolCapture()
    registry.register(cast(Any, server))

    first = server.functions["search_files"](
        str(tmp_path), "needle", search_type="content", snapshot=True, max_results=2,
    )
    assert first["ok"] is True
    assert first["cursor"] is not None

    monkeypatch.setattr(
        service,
        "search_by_content",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("content snapshot continuation rescanned")),
    )
    second = server.functions["search_files"](
        str(tmp_path), "needle", search_type="content", cursor=first["cursor"], max_results=2,
    )
    assert second["ok"] is True
    assert second["scanned_files"] == 0
    assert second["count"] == 2
    assert second["has_more"] is False


def test_truncated_snapshot_reports_unknown_more_without_fake_cursor(tmp_path: Path) -> None:
    store = _store(tmp_path)
    fingerprint = _fingerprint(tmp_path)
    handle = store.create(
        [{"path": "a", "matched_in": ["name"]}],
        fingerprint=fingerprint,
        count_mode="exact",
        result_order="path",
        scan_truncated=True,
        total_count=1,
    )
    page = store.first_page(handle, fingerprint=fingerprint, limit=10)

    assert page.has_more is True
    assert page.next_offset is None
    assert page.cursor is None
    assert page.scan_truncated is True


def test_oversized_snapshot_is_rejected_without_partial_publish(tmp_path: Path) -> None:
    store = SearchSnapshotStore(
        tmp_path / "snapshots",
        ttl_sec=60,
        max_bytes=512,
        max_count=4,
        clock=lambda: 100.0,
    )
    fingerprint = _fingerprint(tmp_path)
    with pytest.raises(ToolError) as caught:
        store.create(
            [{"path": "x" * 2_000, "matched_in": ["name"]}],
            fingerprint=fingerprint,
            count_mode="exact",
            result_order="path",
            scan_truncated=False,
            total_count=1,
        )
    assert caught.value.code == "search_snapshot_too_large"
    assert list((tmp_path / "snapshots").iterdir()) == []


def test_search_files_schema_exposes_snapshot_and_cursor_without_changing_defaults() -> None:
    tools = {tool.name: tool for tool in asyncio.run(create_server().list_tools())}
    properties = tools["search_files"].input_schema["properties"]

    assert properties["count_mode"]["default"] == "exact"
    assert properties["snapshot"]["default"] is False
    assert properties["cursor"]["default"] is None
