from __future__ import annotations

import hashlib
import os
import subprocess
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest
from pydantic import ValidationError

from core.errors import ToolError
from tools.project import project_types
from tools.project.intelligence_models import ContextEvidence, EvidenceEdge, SnapshotEvidence, SymbolKey
from tools.project.project_types import detect_project_types, language_for_path, resolve_package_for_file


def _symbol(root: Path, **changes: object) -> SymbolKey:
    values: dict[str, object] = {
        "canonical_workspace": str(root.resolve()), "language_id": "python", "relative_uri": "src/app.py",
        "qualified_name": "app.Service", "kind": "class", "start_line": 1, "start_character": 0,
        "content_digest": "a" * 64,
    }
    values.update(changes)
    return SymbolKey(**values)  # type: ignore[arg-type]


def _snapshot() -> SnapshotEvidence:
    return SnapshotEvidence(
        source_sha=None, worktree_id="worktree-one", document_digest="a" * 64,
        document_version=None, index_generation="index-one", provider_id=None, source_kind="ast",
    )


def test_symbol_identity_separates_worktrees(tmp_path: Path) -> None:
    first = _symbol(tmp_path / "one")
    second = _symbol(tmp_path / "two")
    assert first != second
    assert len({first, second}) == 2
    with pytest.raises(FrozenInstanceError):
        first.qualified_name = "changed"  # type: ignore[misc]


@pytest.mark.parametrize("changes", [{"start_line": True}, {"start_character": -1}, {"relative_uri": "../escape.py"},
                                     {"content_digest": "bad"}, {"language_id": "unknown"}])
def test_symbol_rejects_invalid_identity(tmp_path: Path, changes: dict[str, object]) -> None:
    with pytest.raises((ValidationError, ValueError)):
        _symbol(tmp_path, **changes)


def test_unknown_ast_column_is_not_claimed_as_utf16(tmp_path: Path) -> None:
    with pytest.raises((ValidationError, ValueError)):
        _symbol(tmp_path, start_character=None)
    symbol = _symbol(tmp_path, start_character=None, coordinate_system="python_legacy")
    assert symbol.start_character is None


def test_schema_unknown_version_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValidationError):
        _symbol(tmp_path, version=2)
    with pytest.raises(ValidationError):
        ContextEvidence(language_id="python", backend="ast", version=2)
    with pytest.raises(ValidationError):
        ContextEvidence.model_validate({"language_id": "python", "backend": "ast", "extra_field": "not accepted"})


def test_evidence_edges_preserve_provenance_and_output_page_bounds(tmp_path: Path) -> None:
    symbol = _symbol(tmp_path)
    edge = EvidenceEdge(
        from_symbol=symbol, to_symbol=symbol, kind="reference", direction="incoming",
        confidence="static_resolved", evidence=_snapshot(), depth=1,
    )
    context = ContextEvidence(language_id="python", backend="ast", definitions=[symbol], edges=[edge])
    assert context.edges[0].evidence.document_digest == symbol.content_digest
    assert edge.model_dump(by_alias=True)["from"]["qualified_name"] == "app.Service"
    with pytest.raises(ValidationError):
        ContextEvidence(language_id="python", backend="ast", edges=[edge] * 1001)
    with pytest.raises(ValidationError):
        EvidenceEdge(
            from_symbol=symbol, to_symbol=symbol, kind="reference", direction="incoming",
            confidence="exact", evidence=_snapshot(), depth=1,  # type: ignore[arg-type]
        )


def test_nested_manifest_owner(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text('{"name":"outer","workspaces":["packages/*"]}', encoding="utf-8")
    nested = tmp_path / "packages" / "inner"
    nested.mkdir(parents=True)
    (nested / "composer.json").write_text('{"name":"inner"}', encoding="utf-8")
    source = nested / "src.php"
    source.write_text("<?php", encoding="utf-8")
    inventory = detect_project_types(tmp_path)
    assert inventory.complete
    assert len(inventory.packages) == 2
    owner = resolve_package_for_file(inventory, source)
    assert owner is not None and owner.root == nested.resolve()
    assert owner.manifests[0].kind == "php"
    outer = resolve_package_for_file(inventory, tmp_path / "index.mjs")
    assert outer is not None and outer.root == tmp_path.resolve()
    assert outer.manifests[0].workspace_patterns == ("packages/*",)


def test_multiple_manifest_types_share_their_package_root(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text("{}", encoding="utf-8")
    (tmp_path / "composer.json").write_text("{}", encoding="utf-8")
    inventory = detect_project_types(tmp_path)
    assert len(inventory.packages) == 1
    assert {item.kind for item in inventory.packages[0].manifests} == {"node", "php"}


@pytest.mark.parametrize(("suffix", "expected"), [(".py", "python"), (".pyi", "python"), (".ts", "typescript"),
                                                    (".tsx", "typescriptreact"), (".js", "javascript"),
                                                    (".jsx", "javascriptreact"), (".mjs", "javascript"),
                                                    (".cjs", "javascript"), (".php", "php"), (".xyz", "unsupported")])
def test_language_suffixes_are_explicit(suffix: str, expected: str) -> None:
    assert language_for_path(Path("module" + suffix)) == expected


def test_json_duplicate_keys_rejected_without_losing_other_packages(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text('{"name":"secret-one","name":"secret-two"}', encoding="utf-8")
    good = tmp_path / "good"
    good.mkdir()
    (good / "composer.json").write_text("{}", encoding="utf-8")
    inventory = detect_project_types(tmp_path)
    assert not inventory.complete
    assert len(inventory.packages) == 1
    assert inventory.packages[0].root == good.resolve()
    assert any("invalid_project_manifest" in item for item in inventory.missing_inputs)
    assert "secret-one" not in repr(inventory.missing_inputs)
    assert "secret-two" not in repr(inventory.missing_inputs)


def test_toml_parser_unavailable_preserves_marker_and_hash(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manifest = tmp_path / "pyproject.toml"
    manifest.write_text('[project]\nname="example"\n', encoding="utf-8")
    monkeypatch.setattr(project_types, "_toml", None)
    inventory = detect_project_types(tmp_path)
    assert not inventory.complete
    assert inventory.language_ids == ("python",)
    assert inventory.packages[0].manifests[0].sha256 == hashlib.sha256(manifest.read_bytes()).hexdigest()
    assert any("toml_parser_unavailable" in item for item in inventory.missing_inputs)
    assert resolve_package_for_file(inventory, tmp_path / "module.py") == inventory.packages[0]


def test_consumption_rechecks_manifest_hash(tmp_path: Path) -> None:
    manifest = tmp_path / "package.json"
    manifest.write_text("{}", encoding="utf-8")
    inventory = detect_project_types(tmp_path)
    manifest.write_text('{"name":"changed"}', encoding="utf-8")
    with pytest.raises(ToolError) as changed:
        resolve_package_for_file(inventory, tmp_path / "app.js")
    assert changed.value.code == "invalid_project_manifest"
    assert "changed" not in changed.value.message


def test_package_resolution_rejects_scope_escape(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text("{}", encoding="utf-8")
    inventory = detect_project_types(tmp_path)
    with pytest.raises(ToolError) as outside:
        resolve_package_for_file(inventory, tmp_path.parent / "outside.py")
    assert outside.value.code == "project_scope_escape"


def test_symlink_escape_denied(tmp_path: Path) -> None:
    root, outside = tmp_path / "root", tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    (outside / "package.json").write_text('{"name":"outside-secret"}', encoding="utf-8")
    try:
        (root / "linked").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("Directory symlinks are unavailable on this host")
    inventory = detect_project_types(root)
    assert not inventory.complete
    assert not inventory.packages
    assert any("project_scope_escape" in item for item in inventory.missing_inputs)
    assert "outside-secret" not in repr(inventory)


def test_manifest_count_and_byte_budgets_are_reported(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text("{}", encoding="utf-8")
    nested = tmp_path / "nested"
    nested.mkdir()
    (nested / "composer.json").write_text("{}", encoding="utf-8")
    limited = detect_project_types(tmp_path, max_manifests=1)
    assert not limited.complete
    assert sum(len(package.manifests) for package in limited.packages) == 1
    assert any("inventory_limit" in item for item in limited.missing_inputs)
    (tmp_path / "package.json").write_bytes(b" " * (256 * 1024 + 1))
    oversized = detect_project_types(tmp_path)
    assert not oversized.complete
    assert all(item.path.name != "package.json" for package in oversized.packages for item in package.manifests)


@pytest.mark.parametrize("maximum", [0, True, 257])
def test_invalid_manifest_admission_budget_is_rejected(tmp_path: Path, maximum: int) -> None:
    with pytest.raises(ToolError) as invalid:
        detect_project_types(tmp_path, max_manifests=maximum)
    assert invalid.value.code == "inventory_limit"


def test_empty_directory_traversal_and_depth_are_bounded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for index in range(4):
        (tmp_path / str(index)).mkdir()
    monkeypatch.setattr(project_types, "_MAX_ENTRIES", 2)
    limited = detect_project_types(tmp_path)
    assert not limited.complete
    assert any("inventory_limit" in item for item in limited.missing_inputs)
    monkeypatch.setattr(project_types, "_MAX_ENTRIES", 100)
    monkeypatch.setattr(project_types, "_MAX_DEPTH", 1)
    (tmp_path / "0" / "deep").mkdir()
    assert not detect_project_types(tmp_path).complete


@pytest.mark.parametrize("pattern", ["../outside", "packages/**", "/absolute", "packages/prefix*"])
def test_unsafe_workspace_patterns_are_rejected(tmp_path: Path, pattern: str) -> None:
    import json

    (tmp_path / "package.json").write_text(json.dumps({"workspaces": [pattern]}), encoding="utf-8")
    inventory = detect_project_types(tmp_path)
    assert not inventory.complete
    assert not inventory.packages
    assert any("invalid_project_manifest" in item for item in inventory.missing_inputs)


def test_inventory_deadline_is_bounded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    times = iter([0.0, 10.0])
    monkeypatch.setattr(project_types.time, "monotonic", lambda: next(times, 10.0))
    inventory = detect_project_types(tmp_path)
    assert not inventory.complete
    assert any("inventory_limit" in item for item in inventory.missing_inputs)


def test_deadline_also_bounds_processing_an_already_scanned_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "app.py").write_text("pass", encoding="utf-8")
    times = iter([0.0, 0.0, 0.0, 10.0])
    monkeypatch.setattr(project_types.time, "monotonic", lambda: next(times, 10.0))
    inventory = detect_project_types(tmp_path)
    assert not inventory.complete
    assert inventory.language_ids == ()


def test_worktree_marker_distinguishes_roots_without_following_its_pointer(tmp_path: Path) -> None:
    first, second = tmp_path / "one", tmp_path / "two"
    first.mkdir()
    second.mkdir()
    (first / ".git").write_text("gitdir: ../unavailable-worktree-admin", encoding="utf-8")
    (second / ".git").write_text("gitdir: ../unavailable-worktree-admin", encoding="utf-8")
    left, right = detect_project_types(first), detect_project_types(second)
    assert left.complete and right.complete
    assert left.worktree_id != right.worktree_id
    assert not (tmp_path / "unavailable-worktree-admin").exists()


@pytest.mark.parametrize("moment", ["before_open", "during_enumeration", "after_enumeration", "aba", "aba_metadata"])
@pytest.mark.parametrize("fingerprint_sources", [False, True])
def test_queued_directory_swap_does_not_enumerate_or_disclose_outside_names(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, moment: str, fingerprint_sources: bool,
) -> None:
    root, outside = tmp_path / "root", tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    queued = root / "queued"
    queued.mkdir()
    if moment != "before_open":
        (queued / "known-local-name.py").write_text("pass", encoding="utf-8")
    private_name = "outside-private-name.py"
    (outside / private_name).write_text("private fixture", encoding="utf-8")
    original = project_types.os.scandir
    consumed: list[str] = []
    swapped = False
    restored = False
    metadata_raced = False

    def swap() -> None:
        nonlocal swapped
        previous = root / "previous-queued"
        assert queued.resolve().is_relative_to(tmp_path.resolve())
        assert previous.resolve().is_relative_to(tmp_path.resolve())
        assert outside.resolve().is_relative_to(tmp_path.resolve())
        queued.rename(previous)
        if os.name == "nt":
            subprocess.run(
                ["cmd", "/c", "mklink", "/J", str(queued), str(outside)],
                check=True, capture_output=True,
            )
        else:
            queued.symlink_to(outside, target_is_directory=True)
        swapped = True

    def restore() -> None:
        nonlocal restored
        queued.rmdir() if os.name == "nt" else queued.unlink()
        previous = root / "previous-queued"
        assert previous.resolve().is_relative_to(tmp_path.resolve())
        assert queued.resolve().is_relative_to(tmp_path.resolve())
        previous.rename(queued)
        restored = True

    @contextmanager
    def swap_at_open(path: str | os.PathLike[str]) -> Iterator[Iterator[os.DirEntry[str]]]:
        target = Path(path) == queued
        if target and moment in {"before_open", "aba", "aba_metadata"}:
            swap()
        with original(path) as scanner:
            if target and moment.startswith("aba"):
                restore()

            def track() -> Iterator[os.DirEntry[str]]:
                for entry in scanner:
                    consumed.append(entry.name)
                    if moment == "during_enumeration":
                        swap()
                    yield entry
                if moment == "after_enumeration":
                    swap()

            yield track() if target else scanner

    monkeypatch.setattr(project_types.os, "scandir", swap_at_open)
    original_lstat = Path.lstat

    def race_metadata(path: Path) -> os.stat_result:
        nonlocal metadata_raced
        if moment == "aba_metadata" and path == queued / private_name and not metadata_raced:
            metadata_raced = True
            swap()
            try:
                return original_lstat(path)
            finally:
                restore()
        return original_lstat(path)

    monkeypatch.setattr(Path, "lstat", race_metadata)
    try:
        inventory = detect_project_types(root, fingerprint_sources=fingerprint_sources)
        assert swapped
        assert not inventory.complete
        if moment == "before_open":
            assert not consumed
        if moment.startswith("aba"):
            assert private_name in consumed
        else:
            assert private_name not in consumed
        assert private_name not in repr(inventory)
        assert not inventory.packages
        assert inventory.language_ids == ()
        if moment == "aba_metadata":
            assert metadata_raced
        reason = "invalid_project_manifest" if moment.startswith("aba") else "project_scope_escape"
        assert inventory.missing_inputs == (f"{reason}:queued",)
    finally:
        if swapped and not restored:
            queued.rmdir() if os.name == "nt" else queued.unlink()
    assert (outside / private_name).read_text(encoding="utf-8") == "private fixture"


@pytest.mark.parametrize("fingerprint_sources", [False, True])
def test_queued_directory_replacement_within_root_discards_the_new_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fingerprint_sources: bool,
) -> None:
    queued = tmp_path / "queued"
    queued.mkdir()
    original = project_types.os.scandir

    @contextmanager
    def replace_at_open(path: str | os.PathLike[str]) -> Iterator[Iterator[os.DirEntry[str]]]:
        if Path(path) == queued:
            previous = tmp_path / "previous-queued"
            assert queued.resolve().is_relative_to(tmp_path.resolve())
            assert previous.resolve().is_relative_to(tmp_path.resolve())
            queued.rename(previous)
            queued.mkdir()
            (queued / "composer.json").write_text("{}", encoding="utf-8")
        with original(path) as scanner:
            yield scanner

    monkeypatch.setattr(project_types.os, "scandir", replace_at_open)
    inventory = detect_project_types(tmp_path, fingerprint_sources=fingerprint_sources)
    assert not inventory.complete
    assert not inventory.packages
    assert inventory.missing_inputs == ("invalid_project_manifest:queued",)


def test_source_fingerprint_is_opt_in_and_tracks_unreturned_same_stat_edits(tmp_path: Path) -> None:
    source = tmp_path / "unreturned.ts"
    source.write_bytes(b"export const x = 1;\n")
    assert detect_project_types(tmp_path).source_fingerprint is None
    first = detect_project_types(tmp_path, fingerprint_sources=True)
    assert first.complete and first.source_fingerprint is not None
    original = source.stat()
    source.write_bytes(b"export const x = 2;\n")
    os.utime(source, ns=(original.st_atime_ns, original.st_mtime_ns))
    second = detect_project_types(tmp_path, fingerprint_sources=True)
    assert second.source_fingerprint != first.source_fingerprint
    assert second.source_fingerprint == detect_project_types(tmp_path, fingerprint_sources=True).source_fingerprint


def test_source_fingerprint_tracks_source_set_and_manifest_bytes(tmp_path: Path) -> None:
    source = tmp_path / "a.py"
    source.write_bytes(b"pass\n")
    first = detect_project_types(tmp_path, fingerprint_sources=True).source_fingerprint
    added = tmp_path / "b.php"
    added.write_bytes(b"<?php\n")
    second = detect_project_types(tmp_path, fingerprint_sources=True).source_fingerprint
    assert first is not None and second is not None and first != second
    added.unlink()
    assert detect_project_types(tmp_path, fingerprint_sources=True).source_fingerprint == first
    source.rename(tmp_path / "renamed.py")
    renamed = detect_project_types(tmp_path, fingerprint_sources=True).source_fingerprint
    assert renamed is not None and renamed != first
    manifest = tmp_path / "package.json"
    manifest.write_bytes(b"{}")
    manifest_first = detect_project_types(tmp_path, fingerprint_sources=True).source_fingerprint
    manifest.write_bytes(b"{ }")
    assert detect_project_types(tmp_path, fingerprint_sources=True).source_fingerprint != manifest_first


def test_source_fingerprint_tracks_typescript_config_bytes_without_parsing(tmp_path: Path) -> None:
    (tmp_path / "app.ts").write_bytes(b"export {};\n")
    config = tmp_path / "tsconfig.json"
    config.write_bytes(b'{"x":1}')
    baseline = detect_project_types(tmp_path, fingerprint_sources=True)
    assert baseline.complete and baseline.source_fingerprint is not None
    assert not baseline.packages
    original = config.stat()
    config.write_bytes(b'{"x":2}')
    os.utime(config, ns=(original.st_atime_ns, original.st_mtime_ns))
    changed = detect_project_types(tmp_path, fingerprint_sources=True)
    assert changed.complete and changed.source_fingerprint != baseline.source_fingerprint
    jsconfig = tmp_path / "jsconfig.json"
    jsconfig.write_bytes(b'{"y":1}')
    added = detect_project_types(tmp_path, fingerprint_sources=True)
    assert added.complete and added.source_fingerprint != changed.source_fingerprint
    jsconfig.write_bytes(b'{"y":2}')
    edited = detect_project_types(tmp_path, fingerprint_sources=True)
    assert edited.complete and edited.source_fingerprint != added.source_fingerprint
    assert not edited.packages


def test_source_fingerprint_separates_roots_and_skips_unknown_and_excluded(tmp_path: Path) -> None:
    first, second = tmp_path / "one", tmp_path / "two"
    for root in (first, second):
        root.mkdir()
        (root / "app.mjs").write_bytes(b"export {};\n")
    baseline = detect_project_types(first, fingerprint_sources=True).source_fingerprint
    assert baseline is not None
    assert detect_project_types(second, fingerprint_sources=True).source_fingerprint != baseline
    (first / "secret.unknown").write_bytes(b"private content")
    excluded = first / "node_modules"
    excluded.mkdir()
    (excluded / "app.py").write_bytes(b"private content")
    assert detect_project_types(first, fingerprint_sources=True).source_fingerprint == baseline


@pytest.mark.parametrize("payload", [b"\xff", b"x" * (2 * 1024 * 1024 + 1)], ids=["invalid-utf8", "oversized"])
def test_source_fingerprint_read_limit_preserves_normal_inventory(tmp_path: Path, payload: bytes) -> None:
    (tmp_path / "app.ts").write_bytes(payload)
    (tmp_path / "package.json").write_bytes(b"{}")
    inventory = detect_project_types(tmp_path, fingerprint_sources=True)
    assert inventory.complete and inventory.language_ids == ("typescript",)
    assert len(inventory.packages) == 1
    assert inventory.source_fingerprint is None
    assert not inventory.missing_inputs


def test_source_fingerprint_total_limit_never_returns_partial_hash(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "a.py").write_bytes(b"pass\n")
    (tmp_path / "b.py").write_bytes(b"pass\n")
    monkeypatch.setattr(project_types, "_MAX_SOURCE_BYTES", 8)
    inventory = detect_project_types(tmp_path, fingerprint_sources=True)
    assert inventory.complete
    assert inventory.source_fingerprint is None


def test_source_fingerprint_checks_worktree_marker_again(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    marker = tmp_path / ".git"
    marker.write_bytes(b"gitdir: one")
    (tmp_path / "app.py").write_bytes(b"pass\n")
    original = project_types._read_bytes

    def change_marker(path: Path, root: Path, maximum: int = 256 * 1024) -> bytes:
        result = original(path, root, maximum)
        if path.name == "app.py":
            marker.write_bytes(b"gitdir: two")
        return result

    monkeypatch.setattr(project_types, "_read_bytes", change_marker)
    inventory = detect_project_types(tmp_path, fingerprint_sources=True)
    assert inventory.source_fingerprint is None


def test_source_fingerprint_read_failure_and_deadline_never_admit_partial(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "app.py").write_bytes(b"pass\n")
    original = project_types._read_bytes

    def fail_read(path: Path, root: Path, maximum: int = 256 * 1024) -> bytes:
        if path.name == "app.py":
            raise OSError("private payload")
        return original(path, root, maximum)

    monkeypatch.setattr(project_types, "_read_bytes", fail_read)
    inventory = detect_project_types(tmp_path, fingerprint_sources=True)
    assert inventory.source_fingerprint is None
    assert "private payload" not in repr(inventory)
    monkeypatch.setattr(project_types, "_read_bytes", original)
    monkeypatch.setattr(project_types, "_INVENTORY_SECONDS", 0.0)
    inventory = detect_project_types(tmp_path, fingerprint_sources=True)
    assert not inventory.complete and inventory.source_fingerprint is None


def test_source_fingerprint_rejects_source_added_after_directory_enumeration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "app.py").write_bytes(b"pass\n")
    original = project_types._read_bytes

    def add_source(path: Path, root: Path, maximum: int = 256 * 1024) -> bytes:
        result = original(path, root, maximum)
        if path.name == "app.py":
            (root / "late.py").write_bytes(b"pass\n")
        return result

    monkeypatch.setattr(project_types, "_read_bytes", add_source)
    inventory = detect_project_types(tmp_path, fingerprint_sources=True)
    assert inventory.complete and inventory.source_fingerprint is None


def test_source_fingerprint_deadline_includes_the_final_source_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "app.py").write_bytes(b"pass\n")
    original = project_types._read_bytes
    clock = [0.0]
    monkeypatch.setattr(project_types.time, "monotonic", lambda: clock[0])

    def slow_read(path: Path, root: Path, maximum: int = 256 * 1024) -> bytes:
        result = original(path, root, maximum)
        clock[0] = 10.0
        return result

    monkeypatch.setattr(project_types, "_read_bytes", slow_read)
    inventory = detect_project_types(tmp_path, fingerprint_sources=True)
    assert not inventory.complete and inventory.source_fingerprint is None
    assert inventory.missing_inputs == ("inventory_limit:.",)


@pytest.mark.parametrize("value", [1, "true", None])
def test_source_fingerprint_selector_is_a_strict_boolean(tmp_path: Path, value: object) -> None:
    with pytest.raises(ToolError) as invalid:
        detect_project_types(tmp_path, fingerprint_sources=value)  # type: ignore[arg-type]
    assert invalid.value.code == "inventory_limit"


@pytest.mark.parametrize("earlier", ["a.py", "package.json"])
def test_source_fingerprint_revalidates_earlier_content_after_later_source_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, earlier: str,
) -> None:
    target = tmp_path / earlier
    target.write_bytes(b"{}" if earlier.endswith("json") else b"x=1\n")
    (tmp_path / "z.py").write_bytes(b"pass\n")
    before = target.stat()
    original = project_types._read_bytes
    changed = False

    def change_earlier(path: Path, root: Path, maximum: int = 256 * 1024) -> bytes:
        nonlocal changed
        result = original(path, root, maximum)
        if path.name == "z.py" and not changed:
            changed = True
            target.write_bytes(b"[]" if earlier.endswith("json") else b"x=2\n")
            os.utime(target, ns=(before.st_atime_ns, before.st_mtime_ns))
        return result

    monkeypatch.setattr(project_types, "_read_bytes", change_earlier)
    inventory = detect_project_types(tmp_path, fingerprint_sources=True)
    assert changed and target.stat().st_mtime_ns == before.st_mtime_ns
    assert inventory.complete
    assert inventory.source_fingerprint is None



def test_installed_vendor_packages_are_not_workspace_projects(tmp_path: Path) -> None:
    from tools.project import service

    for relative in ["composer.json", "apps/first-party/composer.json", "vendor/acme/library/composer.json"]:
        manifest = tmp_path / relative
        manifest.parent.mkdir(parents=True, exist_ok=True)
        manifest.write_text('{"name":"fixture/package"}', encoding="utf-8")
    target = tmp_path / "vendor/acme/library/internal.py"
    target.write_text("private_dependency = 1\n", encoding="utf-8")
    before = detect_project_types(tmp_path, fingerprint_sources=True)
    assert {item.root.relative_to(tmp_path).as_posix() for item in before.packages} == {".", "apps/first-party"}
    files, truncated = service.iter_files(tmp_path, 100)
    assert not truncated and not any(path.is_relative_to(tmp_path / "vendor") for path in files)
    target.write_text("private_dependency = 2\n", encoding="utf-8")
    after = detect_project_types(tmp_path, fingerprint_sources=True)
    assert before.source_fingerprint == after.source_fingerprint
    explicit, truncated = service.iter_files(tmp_path / "vendor", 100)
    assert target in explicit and not truncated


@pytest.mark.parametrize("ignored", [".real_lsp_tools", ".real_lsp_runs"])
def test_ignored_real_lsp_fixtures_do_not_pollute_project_or_test_selection(
    tmp_path: Path, ignored: str,
) -> None:
    from tools.project import service
    from tools.testing.impact import select_affected_tests

    source = tmp_path / "src" / "app.py"
    source.parent.mkdir()
    source.write_text("def calculate() -> int:\n    return 1\n", encoding="utf-8")
    test_file = tmp_path / "tests" / ignored / "isolated" / "test_false_positive.py"
    test_file.parent.mkdir(parents=True)
    test_file.write_text("def test_canary() -> None:\n    assert False\n", encoding="utf-8")

    files, truncated = service.iter_files(tmp_path, 50)
    assert not truncated
    assert source in files
    assert test_file not in files
    assert not any(ignored in str(file) for file in files)

    inventory_before = detect_project_types(tmp_path, fingerprint_sources=True)
    assert inventory_before.complete
    test_file.write_text("def test_canary() -> None:\n    assert True\n", encoding="utf-8")
    inventory_after = detect_project_types(tmp_path, fingerprint_sources=True)
    assert inventory_after.source_fingerprint == inventory_before.source_fingerprint

    selection = select_affected_tests(
        tmp_path, changed_paths=["src/app.py"], max_files=50, max_depth=5,
        fallback_policy="report",
    )
    assert selection["ok"] is True
    assert selection["test_count"] == 0
    assert not any(ignored in item["path"] for item in selection["tests"])
