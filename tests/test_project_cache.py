from __future__ import annotations

import os
import threading
from collections import Counter
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, cast

import pytest

from tools.project import registry, service
from tools.project.index import PythonFileMetadata, PythonMetadataCache, build_python_metadata

ToolFunction = Callable[..., dict[str, Any]]


class ToolCapture:
    def __init__(self) -> None:
        self.functions: dict[str, ToolFunction] = {}

    def tool(self, **kwargs: Any) -> Callable[[ToolFunction], ToolFunction]:
        def decorate(function: ToolFunction) -> ToolFunction:
            self.functions[function.__name__] = function
            return function

        return decorate


def counting_cache(*, max_files: int = 16, max_bytes: int = 4 * 1024 * 1024) -> tuple[PythonMetadataCache, Counter[str]]:
    calls: Counter[str] = Counter()

    def load(path: Path) -> PythonFileMetadata:
        calls[path.name] += 1
        return build_python_metadata(path)

    return PythonMetadataCache(max_files=max_files, max_bytes=max_bytes, loader=load), calls


def test_same_file_version_is_parsed_once(tmp_path: Path) -> None:
    target = tmp_path / "app.py"
    target.write_text("import os\n\ndef answer():\n    return 42\n", encoding="utf-8")
    cache, calls = counting_cache()

    first = cache.get(target)
    second = cache.get(target)

    assert first is second
    assert calls["app.py"] == 1
    assert cache.stats()["entries"] == 1
    assert cache.stats()["bytes"] <= cache.stats()["max_bytes"]


def test_changed_file_version_is_reparsed(tmp_path: Path) -> None:
    target = tmp_path / "app.py"
    target.write_text("def old():\n    return 1\n", encoding="utf-8")
    cache, calls = counting_cache()
    assert cache.get(target).functions[0].qualified_name == "old"

    before = target.stat()
    target.write_text("def new():\n    return 2\n", encoding="utf-8")
    os.utime(target, ns=(before.st_atime_ns, before.st_mtime_ns + 10_000_000))

    updated = cache.get(target)
    assert updated.functions[0].qualified_name == "new"
    assert calls["app.py"] == 2
    assert cache.stats()["entries"] == 1


def test_lru_file_budget_evicts_least_recently_used_entry(tmp_path: Path) -> None:
    cache, calls = counting_cache(max_files=2)
    files = []
    for name in ("a.py", "b.py", "c.py"):
        path = tmp_path / name
        path.write_text(f"def {name[0]}():\n    return 1\n", encoding="utf-8")
        files.append(path)

    cache.get(files[0])
    cache.get(files[1])
    cache.get(files[0])  # a.py becomes most recently used.
    cache.get(files[2])  # b.py should be evicted.
    assert cache.stats()["entries"] == 2

    cache.get(files[1])
    assert calls["a.py"] == 1
    assert calls["b.py"] == 2
    assert calls["c.py"] == 1
    assert cache.stats()["entries"] == 2


def test_entry_larger_than_byte_budget_is_not_retained(tmp_path: Path) -> None:
    target = tmp_path / "large.py"
    target.write_text("\n".join(f"def f{i}():\n    return {i}" for i in range(200)), encoding="utf-8")
    cache, calls = counting_cache(max_bytes=64)

    first = cache.get(target)
    second = cache.get(target)

    assert first.functions == second.functions
    assert calls["large.py"] == 2
    assert cache.stats()["entries"] == 0
    assert cache.stats()["bytes"] == 0


def test_parse_error_metadata_is_version_cached(tmp_path: Path) -> None:
    target = tmp_path / "broken.py"
    target.write_text("def broken(:\n", encoding="utf-8")
    cache, calls = counting_cache()

    first = cache.get(target)
    second = cache.get(target)

    assert first.parse_error is not None
    assert second.parse_error == first.parse_error
    assert calls["broken.py"] == 1


def test_project_tools_share_cached_metadata(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "app.py"
    target.write_text(
        "import os\nfrom pathlib import Path\n\nclass Service:\n    async def run(self, value: int):\n        return value\n",
        encoding="utf-8",
    )
    cache, calls = counting_cache()
    monkeypatch.setattr(service, "PYTHON_METADATA_CACHE", cache)
    server = ToolCapture()
    registry.register(cast(Any, server))

    functions = server.functions["find_function"](str(tmp_path), "Service.run")
    classes = server.functions["find_class"](str(tmp_path), "Service")
    imports = server.functions["find_imports"](str(tmp_path), "pathlib")
    graph = server.functions["dependency_graph"](str(tmp_path), include_external=True)

    assert functions["total_count"] == 1
    assert functions["items"][0]["async"] is True
    assert classes["total_count"] == 1
    assert classes["items"][0]["method_count"] == 1
    assert imports["total_count"] == 1
    assert graph["total_count"] >= 1
    assert calls["app.py"] == 1


def test_change_during_parse_retries_latest_version(tmp_path: Path) -> None:
    target = tmp_path / "moving.py"
    target.write_text("def old():\n    return 1\n", encoding="utf-8")
    calls = 0

    def moving_loader(path: Path) -> PythonFileMetadata:
        nonlocal calls
        calls += 1
        metadata = build_python_metadata(path)
        if calls == 1:
            before = path.stat()
            path.write_text("def new():\n    return 2\n", encoding="utf-8")
            os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns + 10_000_000))
        return metadata

    cache = PythonMetadataCache(max_files=8, max_bytes=1024 * 1024, loader=moving_loader)
    metadata = cache.get(target)

    assert calls == 2
    assert metadata.functions[0].qualified_name == "new"
    assert cache.stats()["entries"] == 1


def test_project_tool_turns_disappearing_file_into_parse_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "gone.py"
    target.write_text("def gone():\n    pass\n", encoding="utf-8")
    server = ToolCapture()
    registry.register(cast(Any, server))

    monkeypatch.setattr(service, "python_metadata", lambda path: (_ for _ in ()).throw(FileNotFoundError(path)))
    result = server.functions["find_function"](str(tmp_path), "gone")

    assert result["ok"] is True
    assert result["total_count"] == 0
    assert len(result["parse_errors"]) == 1
    assert "gone.py" in result["parse_errors"][0]["file"]


def test_concurrent_same_version_uses_one_parse(tmp_path: Path) -> None:
    target = tmp_path / "shared.py"
    target.write_text("def shared():\n    return 1\n", encoding="utf-8")
    started = threading.Event()
    release = threading.Event()
    calls = 0
    calls_lock = threading.Lock()

    def slow_loader(path: Path) -> PythonFileMetadata:
        nonlocal calls
        with calls_lock:
            calls += 1
        started.set()
        assert release.wait(5)
        return build_python_metadata(path)

    cache = PythonMetadataCache(max_files=8, max_bytes=1024 * 1024, loader=slow_loader)
    with ThreadPoolExecutor(max_workers=12) as pool:
        futures = [pool.submit(cache.get, target) for _ in range(12)]
        assert started.wait(5)
        release.set()
        results = [future.result(timeout=5) for future in futures]

    assert calls == 1
    assert all(result is results[0] for result in results)
    assert cache.stats()["entries"] == 1


def test_unrelated_files_parse_concurrently(tmp_path: Path) -> None:
    first = tmp_path / "first.py"
    second = tmp_path / "second.py"
    first.write_text("def first():\n    pass\n", encoding="utf-8")
    second.write_text("def second():\n    pass\n", encoding="utf-8")
    barrier = threading.Barrier(2)

    def loader(path: Path) -> PythonFileMetadata:
        barrier.wait(timeout=5)
        return build_python_metadata(path)

    cache = PythonMetadataCache(max_files=8, max_bytes=1024 * 1024, loader=loader)
    with ThreadPoolExecutor(max_workers=2) as pool:
        one = pool.submit(cache.get, first)
        two = pool.submit(cache.get, second)
        assert one.result(timeout=5).functions[0].qualified_name == "first"
        assert two.result(timeout=5).functions[0].qualified_name == "second"


def test_invalidate_forces_reparse_even_when_stat_version_is_restored(tmp_path: Path) -> None:
    target = tmp_path / "stable.py"
    target.write_text("def old():\n    return 1\n", encoding="utf-8")
    cache, calls = counting_cache()
    original = target.stat()
    assert cache.get(target).functions[0].qualified_name == "old"

    target.write_text("def new():\n    return 2\n", encoding="utf-8")
    os.utime(target, ns=(original.st_atime_ns, original.st_mtime_ns))
    assert target.stat().st_size == original.st_size
    assert target.stat().st_mtime_ns == original.st_mtime_ns

    assert cache.invalidate(target) == 1
    assert cache.get(target).functions[0].qualified_name == "new"
    assert calls["stable.py"] == 2


def test_recursive_invalidation_removes_only_subtree(tmp_path: Path) -> None:
    inside = tmp_path / "pkg" / "inside.py"
    nested = tmp_path / "pkg" / "nested" / "other.py"
    outside = tmp_path / "outside.py"
    for path in (inside, nested, outside):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"def {path.stem}():\n    pass\n", encoding="utf-8")
    cache, calls = counting_cache()
    for path in (inside, nested, outside):
        cache.get(path)

    assert cache.invalidate(tmp_path / "pkg", recursive=True) == 2
    assert cache.stats()["entries"] == 1
    cache.get(outside)
    cache.get(inside)
    cache.get(nested)
    assert calls["outside.py"] == 1
    assert calls["inside.py"] == 2
    assert calls["other.py"] == 2


def test_invalidation_during_parse_cannot_resurrect_cache_entry(tmp_path: Path) -> None:
    target = tmp_path / "race.py"
    target.write_text("def old():\n    return 1\n", encoding="utf-8")
    original = target.stat()
    started = threading.Event()
    release = threading.Event()
    calls = 0


    def loader(path: Path) -> PythonFileMetadata:
        nonlocal calls
        calls += 1
        metadata = build_python_metadata(path)
        if calls == 1:
            started.set()
            assert release.wait(5)
        return metadata


    cache = PythonMetadataCache(max_files=8, max_bytes=1024 * 1024, loader=loader)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(cache.get, target)
        assert started.wait(5)
        target.write_text("def new():\n    return 2\n", encoding="utf-8")
        os.utime(target, ns=(original.st_atime_ns, original.st_mtime_ns))
        assert target.stat().st_size == original.st_size
        assert target.stat().st_mtime_ns == original.st_mtime_ns
        cache.invalidate(target)
        release.set()
        result = future.result(timeout=5)

    assert result.functions[0].qualified_name == "new"
    assert calls == 2
    assert cache.stats()["entries"] == 1
    assert cache.get(target) is result


def test_concurrent_waiters_retry_after_invalidation(tmp_path: Path) -> None:
    target = tmp_path / "shared_race.py"
    target.write_text("def old():\n    return 1\n", encoding="utf-8")
    original = target.stat()
    started = threading.Event()
    release = threading.Event()
    calls = 0
    calls_lock = threading.Lock()

    def loader(path: Path) -> PythonFileMetadata:
        nonlocal calls
        with calls_lock:
            calls += 1
            call_number = calls
        metadata = build_python_metadata(path)
        if call_number == 1:
            started.set()
            assert release.wait(5)
        return metadata

    cache = PythonMetadataCache(max_files=8, max_bytes=1024 * 1024, loader=loader)
    with ThreadPoolExecutor(max_workers=12) as pool:
        futures = [pool.submit(cache.get, target) for _ in range(12)]
        assert started.wait(5)
        target.write_text("def new():\n    return 2\n", encoding="utf-8")
        os.utime(target, ns=(original.st_atime_ns, original.st_mtime_ns))
        cache.invalidate(target)
        release.set()
        results = [future.result(timeout=5) for future in futures]

    assert calls == 2
    assert all(result.functions[0].qualified_name == "new" for result in results)
    assert all(result is results[0] for result in results)
    assert cache.stats()["entries"] == 1
