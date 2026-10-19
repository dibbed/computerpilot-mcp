from __future__ import annotations

import os
import threading
from collections import Counter
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, cast

import pytest

from core.errors import ToolError
from tools.project import context as project_context
from tools.project import registry, service
from tools.project.context import lookup_code_context
from tools.project.index import PythonFileMetadata, PythonMetadataCache, build_python_metadata
from tools.testing.impact import select_affected_tests

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


def test_project_discovery_excludes_generated_directories_across_consumers(tmp_path: Path) -> None:
    source = "def create() -> int:\n    return 1\n"
    test_source = "from app.service import create\n\ndef test_create() -> None:\n    assert create() == 1\n"
    for relative, content in (("app/service.py", source), ("tests/test_service.py", test_source)):
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")

    generated_names = {".worktrees", ".playwright-cli", ".pytest-tmp", ".pytest-tmp-r4"}
    for parent in (tmp_path, tmp_path / "app"):
        for name in generated_names:
            generated = parent / name / "copy"
            for relative, content in (
                ("app/service.py", source),
                ("tests/test_service.py", test_source),
                ("broken.py", "def broken(:\n"),
            ):
                target = generated / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content, encoding="utf-8")

    server = ToolCapture()
    registry.register(cast(Any, server))

    summary = server.functions["project_summary"](str(tmp_path))
    assert summary["files_scanned"] == 2
    assert summary["languages"] == [{"name": "Python", "files": 2}]
    assert summary["scan_truncated"] is False
    assert generated_names <= {item["name"] for item in summary["top_level"]}

    functions = server.functions["find_function"](str(tmp_path), "create")
    assert functions["total_count"] == 1
    assert functions["items"][0]["file"] == "app/service.py"
    assert functions["parse_errors"] == []

    context = lookup_code_context(tmp_path, "create")
    assert context["ambiguous"] is False
    assert [(item["file"], item["qualified_name"]) for item in context["definitions"]] == [
        ("app/service.py", "app.service.create")
    ]
    assert [item["file"] for item in context["tests"]] == ["tests/test_service.py"]
    assert context["parse_errors"] == []
    assert context["complete"] is True

    affected = select_affected_tests(tmp_path, changed_paths=["app/service.py"])
    assert [item["path"] for item in affected["tests"]] == ["tests/test_service.py"]
    assert affected["files_scanned"] == 2
    assert affected["parse_errors"] == []
    assert affected["decision"] == "focused"
    assert affected["complete"] is True


def test_project_discovery_preserves_similar_directory_names(tmp_path: Path) -> None:
    names = {"worktrees", ".worktrees-source", ".playwright-cli-source", ".pytest-tmp2", ".pytest-tmp_saved", ".hidden"}
    for name in names:
        directory = tmp_path / name
        directory.mkdir()
        (directory / "source.py").write_text("def create():\n    pass\n", encoding="utf-8")

    files, truncated = service.iter_files(tmp_path, 100)

    assert {path.relative_to(tmp_path).as_posix() for path in files} == {f"{name}/source.py" for name in names}
    assert truncated is False


@pytest.mark.parametrize("name", [".worktrees", ".playwright-cli", ".pytest-tmp", ".pytest-tmp-r4"])
def test_project_discovery_allows_explicit_generated_root_and_file(tmp_path: Path, name: str) -> None:
    root = tmp_path / name
    root.mkdir()
    target = root / "service.py"
    target.write_text("def create():\n    pass\n", encoding="utf-8")
    server = ToolCapture()
    registry.register(cast(Any, server))

    assert service.project_summary(root, 100)["files_scanned"] == 1
    for path in (root, target):
        functions = server.functions["find_function"](str(path), "create")
        assert functions["total_count"] == 1
        assert functions["items"][0]["file"] == "service.py"
        assert functions["parse_errors"] == []


def test_project_discovery_marks_real_source_at_file_limit_truncated(tmp_path: Path) -> None:
    target = tmp_path / "source.py"
    target.write_text("def create():\n    pass\n", encoding="utf-8")

    assert service.iter_files(tmp_path, 1) == ([target], True)
    assert service.iter_files(tmp_path, 2) == ([target], False)


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


@pytest.mark.parametrize("after_load", [False, True])
def test_metadata_cache_retries_transient_content_version_race(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, after_load: bool,
) -> None:
    from tools.project import index

    target = tmp_path / "race.py"
    target.write_text("def old():\n    return 1\n", encoding="utf-8")
    original = index.file_version
    loaded = False
    changed = False

    def loader(path: Path) -> PythonFileMetadata:
        nonlocal loaded
        result = build_python_metadata(path)
        loaded = True
        return result

    def racing(path: Path) -> index.FileVersion:
        nonlocal changed
        if not changed and loaded == after_load:
            changed = True
            target.write_text("def new():\n    return 2\n", encoding="utf-8")
            raise ToolError("project_file_changed_during_parse", "Python source changed while being read")
        return original(path)

    monkeypatch.setattr(index, "file_version", racing)
    cache = PythonMetadataCache(max_files=2, max_bytes=1024 * 1024, loader=loader)
    result = cache.get(target)
    assert result.functions[0].qualified_name == "new"
    assert cache.get(target) is result


def test_concurrent_metadata_waiters_share_version_reads_during_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tools.project import index

    target = tmp_path / "race.py"
    target.write_text("def old():\n    return 1\n", encoding="utf-8")
    started = threading.Event()
    release = threading.Event()
    joined = threading.Event()
    owner: int | None = None
    original_version = index.file_version

    def loader(path: Path) -> PythonFileMetadata:
        nonlocal owner
        result = build_python_metadata(path)
        if owner is None:
            owner = threading.get_ident()
            started.set()
            assert release.wait(5)
        return result

    def guarded(path: Path) -> index.FileVersion:
        if started.is_set() and not release.is_set() and threading.get_ident() != owner:
            raise ToolError("project_file_changed_during_parse", "Writer is updating the source")
        return original_version(path)

    cache = PythonMetadataCache(max_files=2, max_bytes=1024 * 1024, loader=loader)
    original_run = cache._flights.run

    def observed_run(*args: Any, **kwargs: Any) -> Any:
        if started.is_set() and threading.get_ident() != owner:
            joined.set()
        return original_run(*args, **kwargs)

    monkeypatch.setattr(index, "file_version", guarded)
    monkeypatch.setattr(cache._flights, "run", observed_run)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(cache.get, target)
        assert started.wait(5)
        second = pool.submit(cache.get, target)
        second.add_done_callback(lambda future: joined.set())
        try:
            assert joined.wait(5)
            target.write_text("def new():\n    return 2\n", encoding="utf-8")
            cache.invalidate(target)
        finally:
            release.set()
        result = first.result(timeout=5)
        assert result.functions[0].qualified_name == "new"
        assert second.result(timeout=5) is result


def test_metadata_cache_bounds_retries_for_continuous_content_race(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tools.project import index

    target = tmp_path / "race.py"
    target.write_text("def old():\n    return 1\n", encoding="utf-8")
    reads = 0

    def changing(path: Path) -> index.FileVersion:
        nonlocal reads
        reads += 1
        raise ToolError("project_file_changed_during_parse", "Python source changed while being read")

    monkeypatch.setattr(index, "file_version", changing)
    cache = PythonMetadataCache(max_files=2, max_bytes=1024 * 1024)
    with pytest.raises(ToolError) as failure:
        cache.get(target)
    assert failure.value.code == "project_file_changed_during_parse"
    assert reads == index.MAX_PARSE_VERSION_RETRIES
    assert cache.stats()["entries"] == 0


def test_file_version_detects_same_size_same_mtime_content_change(tmp_path: Path) -> None:
    from tools.project.index import file_version

    target = tmp_path / "app.py"
    target.write_text("def foo():\n    return 1\n", encoding="utf-8")
    original = target.stat()
    first = file_version(target)

    target.write_text("def bar():\n    return 2\n", encoding="utf-8")
    os.utime(target, ns=(original.st_atime_ns, original.st_mtime_ns))
    after = target.stat()
    second = file_version(target)

    assert after.st_size == original.st_size
    assert after.st_mtime_ns == original.st_mtime_ns
    assert first.mtime_ns == second.mtime_ns
    assert first.size == second.size
    assert first.content_sha256 != second.content_sha256


def test_context_same_size_same_mtime_change_invalidates_both_caches(tmp_path: Path) -> None:
    target = tmp_path / "app.py"
    target.write_text("def foo():\n    return 1\n", encoding="utf-8")
    original = target.stat()
    metadata_cache = PythonMetadataCache(max_files=8, max_bytes=4 * 1024 * 1024)
    context_cache = project_context.ContextIndexCache(max_entries=4, max_bytes=4 * 1024 * 1024)

    assert metadata_cache.get(target).functions[0].qualified_name == "foo"
    first = context_cache.get(tmp_path, 5_000)

    target.write_text("def bar():\n    return 2\n", encoding="utf-8")
    os.utime(target, ns=(original.st_atime_ns, original.st_mtime_ns))
    assert target.stat().st_size == original.st_size
    assert target.stat().st_mtime_ns == original.st_mtime_ns

    assert metadata_cache.get(target).functions[0].qualified_name == "bar"
    second = context_cache.get(tmp_path, 5_000)

    assert first.snapshot_id != second.snapshot_id
    assert [item.qualified_name for item in second.definitions] == ["app.bar"]


def test_context_delete_rename_invalidates(tmp_path: Path) -> None:
    cache = project_context.ContextIndexCache(max_entries=4, max_bytes=4 * 1024 * 1024)
    target = tmp_path / "app.py"
    target.write_text("def foo():\n    return 1\n", encoding="utf-8")
    first = cache.get(tmp_path, 5_000)

    renamed = tmp_path / "renamed.py"
    target.rename(renamed)
    second = cache.get(tmp_path, 5_000)

    assert first.snapshot_id != second.snapshot_id
    assert [item.qualified_name for item in second.definitions] == ["renamed.foo"]


@pytest.mark.parametrize("invalidate_file", [False, True])
def test_context_build_cannot_publish_after_invalidation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, invalidate_file: bool
) -> None:
    target = tmp_path / "app.py"
    target.write_text("def foo():\n    return 1\n", encoding="utf-8")
    cache = project_context.ContextIndexCache(max_entries=4, max_bytes=4 * 1024 * 1024)
    started = threading.Event()
    release = threading.Event()
    calls = 0
    original = project_context._build_context_index

    def observed(*args: Any, **kwargs: Any) -> Any:
        nonlocal calls
        calls += 1
        index = original(*args, **kwargs)
        if calls == 1:
            started.set()
            assert release.wait(5)
        return index

    monkeypatch.setattr(project_context, "_build_context_index", observed)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(cache.get, tmp_path, 5_000)
        assert started.wait(5)
        cache.invalidate(target if invalidate_file else tmp_path)
        release.set()
        stale = future.result(timeout=5)

    assert stale.freshness == "stale"
    assert cache.stats()["entries"] == 0
    refreshed = cache.get(tmp_path, 5_000)
    assert refreshed.freshness == "current"
    assert cache.stats()["entries"] == 1


def test_context_lru_count_and_byte_limits(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    roots = []
    for name in ("alpha", "beta", "gamma"):
        root = tmp_path / name
        root.mkdir()
        (root / "app.py").write_text(f"def {name}():\n    return 1\n", encoding="utf-8")
        roots.append(root)
    calls: Counter[str] = Counter()
    original = project_context._build_context_index

    def observed(*args: Any, **kwargs: Any) -> Any:
        calls[Path(args[0]).name] += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(project_context, "_build_context_index", observed)
    cache = project_context.ContextIndexCache(max_entries=2, max_bytes=8 * 1024 * 1024)
    cache.get(roots[0], 5_000)
    cache.get(roots[1], 5_000)
    cache.get(roots[0], 5_000)
    cache.get(roots[2], 5_000)

    assert cache.stats()["entries"] == 2
    cache.get(roots[1], 5_000)
    assert calls["beta"] == 2
    assert calls["alpha"] == 1
    assert cache.stats()["entries"] == 2

    tight = project_context.ContextIndexCache(max_entries=8, max_bytes=1)
    tight.get(roots[0], 5_000)
    tight.get(roots[0], 5_000)
    assert tight.stats()["entries"] == 0
    assert tight.stats()["bytes"] == 0
    assert tight.stats()["max_entries"] == 8
    assert tight.stats()["max_bytes"] == 1


def test_context_lru_also_bounds_generation_metadata(tmp_path: Path) -> None:
    cache = project_context.ContextIndexCache(max_entries=2, max_bytes=8 * 1024 * 1024)
    roots = []
    for number in range(12):
        root = tmp_path / str(number)
        root.mkdir()
        (root / "app.py").write_text(f"def value_{number}():\n    return 1\n", encoding="utf-8")
        roots.append(root)
        assert cache.get(root, 5_000).definitions[0].qualified_name == f"app.value_{number}"
        assert cache.stats()["generations"] <= 2
    assert cache.stats()["entries"] == 2
    cache.invalidate(roots[-1] / "app.py")
    assert cache.stats()["entries"] == 1
    cache.clear()
    assert cache.stats()["entries"] == cache.stats()["generations"] == 0


def test_context_source_snapshot_change_never_returns_mixed_rows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "app.py"
    target.write_text("def foo():\n    return 1\n", encoding="utf-8")
    cache = project_context.ContextIndexCache(max_entries=4, max_bytes=4 * 1024 * 1024)
    attempts = 0
    original = project_context._build_context_index

    def observed(*args: Any, **kwargs: Any) -> Any:
        nonlocal attempts
        attempts += 1
        index = original(*args, **kwargs)
        if attempts == 1:
            target.write_text("def bar():\n    return 2\n", encoding="utf-8")
        return index

    monkeypatch.setattr(project_context, "_build_context_index", observed)
    index = cache.get(tmp_path, 5_000)

    assert [item.qualified_name for item in index.definitions] == ["app.bar"]
    assert index.freshness == "current"
    assert attempts == 2


def test_context_build_deadline_raises_before_any_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "app.py"
    target.write_text("def foo():\n    return 1\n", encoding="utf-8")
    cache = project_context.ContextIndexCache(max_entries=4, max_bytes=4 * 1024 * 1024)
    monkeypatch.setattr(project_context, "CONTEXT_INDEX_DEADLINE_SECONDS", -1.0)

    with pytest.raises(ToolError) as excinfo:
        cache.get(tmp_path, 5_000)

    assert excinfo.value.code == "context_index_deadline"
    assert cache.stats()["entries"] == 0


def test_context_symlink_outside_root_is_skipped(tmp_path: Path) -> None:
    root = tmp_path / "root"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    secret = outside / "secret.py"
    secret.write_text("def leak():\n    return 1\n", encoding="utf-8")
    link = root / "linked.py"
    try:
        link.symlink_to(secret)
    except OSError:
        pytest.skip("Symlink creation is not available in this environment.")

    result = lookup_code_context(root, "leak")

    assert result["definitions"] == []
    assert "symlink_outside_root" in result["incomplete_reasons"]
    assert result["complete"] is False


def test_project_large_file_is_bounded(tmp_path: Path) -> None:
    target = tmp_path / "huge.py"
    target.write_text("value = 1\n" * 250_000, encoding="utf-8")
    assert target.stat().st_size > 2 * 1024 * 1024
    cache = PythonMetadataCache(max_files=8, max_bytes=4 * 1024 * 1024)

    with pytest.raises(ToolError) as excinfo:
        cache.get(target)

    assert excinfo.value.code == "project_file_too_large"
    metadata = build_python_metadata(target)
    assert metadata.parse_error is not None
    assert "2 MiB" in metadata.parse_error


def test_project_discovery_sorts_before_cap(tmp_path: Path) -> None:
    for name in ("c.py", "a.py", "b.py"):
        (tmp_path / name).write_text("def value():\n    return 1\n", encoding="utf-8")

    files, truncated = service.iter_files(tmp_path, 2)

    assert [path.name for path in files] == ["a.py", "b.py"]
    assert truncated is True
