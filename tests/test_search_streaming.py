from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any, cast

import pytest

from core.registry import create_server
from tools.filesystem import registry, service

ToolFunction = Callable[..., dict[str, Any]]


class ToolCapture:
    def __init__(self) -> None:
        self.functions: dict[str, ToolFunction] = {}

    def tool(self, **kwargs: Any) -> Callable[[ToolFunction], ToolFunction]:
        def decorate(function: ToolFunction) -> ToolFunction:
            self.functions[function.__name__] = function
            return function

        return decorate


def test_streaming_name_search_stops_after_limit_plus_one_match(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    yielded: list[int] = []

    def fake_iter(
        root: Path,
        include_hidden: bool,
        max_scan_files: int,
        exclude_common: bool,
        progress: service._WalkProgress,
    ) -> Iterator[Path]:
        del include_hidden, max_scan_files, exclude_common
        for index in range(100):
            progress.scanned_files += 1
            yielded.append(index)
            yield root / f"hit_{index:03d}.txt"

    monkeypatch.setattr(service, "_iter_files", fake_iter)
    result = service.search_by_name_streaming(
        tmp_path,
        "hit_",
        regex=False,
        case_sensitive=False,
        glob=None,
        include_hidden=False,
        max_scan_files=100_000,
        exclude_common=True,
        offset=0,
        limit=5,
    )

    assert [item.name for item in result.items] == [f"hit_{index:03d}.txt" for index in range(5)]
    assert yielded == list(range(6))
    assert result.scanned_files == 6
    assert result.has_more is True
    assert result.scan_truncated is False


def test_streaming_name_search_offset_only_scans_until_next_page_is_known(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    yielded: list[int] = []

    def fake_iter(
        root: Path,
        include_hidden: bool,
        max_scan_files: int,
        exclude_common: bool,
        progress: service._WalkProgress,
    ) -> Iterator[Path]:
        del include_hidden, max_scan_files, exclude_common
        for index in range(100):
            progress.scanned_files += 1
            yielded.append(index)
            yield root / f"match_{index:03d}.txt"

    monkeypatch.setattr(service, "_iter_files", fake_iter)
    result = service.search_by_name_streaming(
        tmp_path,
        "match_",
        regex=False,
        case_sensitive=False,
        glob=None,
        include_hidden=False,
        max_scan_files=100_000,
        exclude_common=True,
        offset=3,
        limit=2,
    )

    assert [item.name for item in result.items] == ["match_003.txt", "match_004.txt"]
    assert yielded == list(range(6))
    assert result.has_more is True


def test_streaming_name_search_marks_scan_cap_as_unknown_more(tmp_path: Path) -> None:
    for index in range(5):
        (tmp_path / f"hit_{index}.txt").write_text("", encoding="utf-8")

    result = service.search_by_name_streaming(
        tmp_path,
        "hit_",
        regex=False,
        case_sensitive=False,
        glob=None,
        include_hidden=False,
        max_scan_files=3,
        exclude_common=True,
        offset=0,
        limit=10,
    )

    assert len(result.items) == 3
    assert result.scanned_files == 3
    assert result.scan_truncated is True
    assert result.has_more is True
    assert result.continuation_available is False


def test_exact_name_search_keeps_sorted_exact_count_semantics(tmp_path: Path) -> None:
    for name in ("zeta.txt", "Alpha.txt", "middle.txt"):
        (tmp_path / name).write_text("", encoding="utf-8")
    server = ToolCapture()
    registry.register(cast(Any, server))

    result = server.functions["search_files"](
        str(tmp_path),
        ".txt",
        search_type="name",
        max_results=2,
    )

    assert result["count_mode"] == "exact"
    assert result["result_order"] == "path"
    assert result["total_count"] == 3
    assert result["count"] == 2
    assert result["has_more"] is True
    assert result["next_offset"] == 2
    assert [Path(item["path"]).name for item in result["items"]] == ["Alpha.txt", "middle.txt"]


def test_registry_streaming_mode_returns_unknown_total_and_traversal_order(tmp_path: Path) -> None:
    for index in range(10):
        (tmp_path / f"hit_{index:02d}.txt").write_text("", encoding="utf-8")
    server = ToolCapture()
    registry.register(cast(Any, server))

    result = server.functions["search_files"](
        str(tmp_path),
        "hit_",
        search_type="name",
        count_mode="none",
        max_results=3,
    )

    assert result["ok"] is True
    assert result["count_mode"] == "none"
    assert result["result_order"] == "traversal"
    assert result["total_count"] is None
    assert result["count"] == 3
    assert result["has_more"] is True
    assert result["next_offset"] == 3
    assert result["scanned_files"] <= 4
    assert all(item["matched_in"] == ["name"] for item in result["items"])


@pytest.mark.parametrize("search_type", ["content", "both"])
def test_streaming_count_mode_rejects_non_name_search(tmp_path: Path, search_type: str) -> None:
    server = ToolCapture()
    registry.register(cast(Any, server))

    result = server.functions["search_files"](
        str(tmp_path),
        "needle",
        search_type=search_type,
        count_mode="none",
    )

    assert result["ok"] is False
    assert result["error"] == "streaming_name_search_only"


def test_streaming_and_exact_name_matching_share_regex_glob_case_semantics(tmp_path: Path) -> None:
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "Alpha_TEST.py").write_text("", encoding="utf-8")
    (tmp_path / "pkg" / "beta_test.py").write_text("", encoding="utf-8")
    (tmp_path / "pkg" / "other.txt").write_text("", encoding="utf-8")

    exact, truncated = service.search_by_name(
        tmp_path,
        r"test\.py$",
        regex=True,
        case_sensitive=False,
        glob="*.py",
        include_hidden=False,
        max_scan_files=100,
        exclude_common=True,
    )
    streamed = service.search_by_name_streaming(
        tmp_path,
        r"test\.py$",
        regex=True,
        case_sensitive=False,
        glob="*.py",
        include_hidden=False,
        max_scan_files=100,
        exclude_common=True,
        offset=0,
        limit=10,
    )

    assert truncated is False
    assert set(exact) == set(streamed.items)
    assert {item.name for item in streamed.items} == {"Alpha_TEST.py", "beta_test.py"}
    assert streamed.has_more is False


def test_streaming_scan_cap_does_not_offer_non_progressing_offset(tmp_path: Path) -> None:
    for index in range(5):
        (tmp_path / f"hit_{index}.txt").write_text("", encoding="utf-8")
    server = ToolCapture()
    registry.register(cast(Any, server))

    result = server.functions["search_files"](
        str(tmp_path),
        "hit_",
        search_type="name",
        count_mode="none",
        max_results=10,
        max_scan_files=3,
    )

    assert result["scan_truncated"] is True
    assert result["has_more"] is True
    assert result["next_offset"] is None


def test_search_files_schema_exposes_compatible_count_mode_default() -> None:
    tools = {tool.name: tool for tool in asyncio.run(create_server().list_tools())}
    schema = tools["search_files"].input_schema["properties"]["count_mode"]

    assert schema["default"] == "exact"
    assert set(schema["enum"]) == {"exact", "none"}
