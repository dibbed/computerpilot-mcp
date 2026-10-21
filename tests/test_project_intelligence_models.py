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
def test_queued_directory_swap_does_not_enumerate_or_disclose_outside_names(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, moment: str,
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
        inventory = detect_project_types(root)
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


def test_queued_directory_replacement_within_root_discards_the_new_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
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
    inventory = detect_project_types(tmp_path)
    assert not inventory.complete
    assert not inventory.packages
    assert inventory.missing_inputs == ("invalid_project_manifest:queued",)
