"""Offline provider configuration, trust, and session identity tests (no spawn, no network)."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from core.errors import ToolError
from core.resource_locks import canonical_path
from tools.language.providers import (
    LspProvider,
    command_for,
    load_lsp_providers,
    make_session_key,
    validate_provider,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
CONFIG_CANARY = "provider-config-canary-3f9c"


@pytest.fixture
def spawn_spy(monkeypatch: pytest.MonkeyPatch) -> list[object]:
    """Fail any attempt to launch a process while provider work stays offline."""

    calls: list[object] = []

    def fake_popen(*args: object, **kwargs: object) -> None:
        calls.append((args, kwargs))
        raise AssertionError("A process launch was attempted during an offline provider operation.")

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    return calls


def _entry(
    provider_id: str,
    *,
    executable: Path,
    entrypoint: Path | None,
    trusted_roots: list[Path],
    languages: list[str],
    executable_data: bytes = b"provider-binary",
    entrypoint_data: bytes = b"provider-entrypoint",
    declared_version: str = "1.0.0",
) -> dict[str, object]:
    executable.write_bytes(executable_data)
    entry: dict[str, object] = {
        "provider_id": provider_id,
        "languages": languages,
        "executable": str(executable),
        "executable_sha256": hashlib.sha256(executable_data).hexdigest(),
        "entrypoint": None,
        "entrypoint_sha256": None,
        "declared_version": declared_version,
        "trusted_roots": [str(root) for root in trusted_roots],
    }
    if entrypoint is not None:
        entrypoint.write_bytes(entrypoint_data)
        entry["entrypoint"] = str(entrypoint)
        entry["entrypoint_sha256"] = hashlib.sha256(entrypoint_data).hexdigest()
    return entry


def _write_config(path: Path, providers: list[dict[str, object]]) -> Path:
    path.write_text(json.dumps({"schema_version": 1, "providers": providers}), encoding="utf-8")
    return path


def test_provider_mapping_is_exact_and_does_not_spawn(tmp_path: Path, spawn_spy: list[object]) -> None:
    trusted = tmp_path / "workspace"
    trusted.mkdir()
    node = tmp_path / "node.exe"
    php = tmp_path / "php.exe"
    entries = [
        _entry(
            "pyright",
            executable=node,
            entrypoint=tmp_path / "pyright.js",
            trusted_roots=[trusted],
            languages=["python"],
        ),
        _entry(
            "typescript",
            executable=node,
            entrypoint=tmp_path / "tsserver.js",
            trusted_roots=[trusted],
            languages=["javascript", "javascriptreact", "typescript", "typescriptreact"],
        ),
        _entry(
            "phpactor",
            executable=php,
            entrypoint=tmp_path / "phpactor.php",
            trusted_roots=[trusted],
            languages=["php"],
        ),
    ]
    providers = load_lsp_providers(_write_config(tmp_path / "providers.json", entries))

    by_id = {provider.provider_id: provider for provider in providers}
    assert tuple(by_id) == ("pyright", "typescript", "phpactor")
    assert by_id["pyright"].languages == ("python",)
    assert by_id["typescript"].languages == ("javascript", "javascriptreact", "typescript", "typescriptreact")
    assert by_id["phpactor"].languages == ("php",)
    assert by_id["pyright"].declared_version == "1.0.0"
    assert by_id["pyright"].entrypoint == tmp_path / "pyright.js"
    assert by_id["pyright"].entrypoint_sha256 is not None

    assert command_for(by_id["pyright"]) == (str(node), str(tmp_path / "pyright.js"), "--stdio")
    assert command_for(by_id["typescript"]) == (str(node), str(tmp_path / "tsserver.js"), "--stdio")
    assert command_for(by_id["phpactor"]) == (str(php), str(tmp_path / "phpactor.php"), "language-server")

    assert validate_provider(trusted, "python", providers=providers).provider_id == "pyright"
    assert validate_provider(trusted, "javascript", providers=providers).provider_id == "typescript"
    assert validate_provider(trusted, "php", providers=providers).provider_id == "phpactor"
    assert spawn_spy == []


def test_native_standalone_pyright_command_has_no_entrypoint(tmp_path: Path, spawn_spy: list[object]) -> None:
    trusted = tmp_path / "workspace"
    trusted.mkdir()
    native = tmp_path / "pyright.exe"
    entries = [
        _entry("pyright", executable=native, entrypoint=None, trusted_roots=[trusted], languages=["python"]),
    ]
    providers = load_lsp_providers(_write_config(tmp_path / "providers.json", entries))
    provider = providers[0]

    assert provider.entrypoint is None
    assert provider.entrypoint_sha256 is None
    assert command_for(provider) == (str(native), "--stdio")
    assert validate_provider(trusted, "python", providers=providers).provider_id == "pyright"
    assert spawn_spy == []


def test_intelephense_is_pinned_explicit_and_php_only(tmp_path: Path, spawn_spy: list[object]) -> None:
    trusted = tmp_path / "workspace"
    trusted.mkdir()
    node = tmp_path / "node.exe"
    script = tmp_path / "intelephense.js"
    entry = _entry(
        "intelephense", executable=node, entrypoint=script,
        trusted_roots=[trusted], languages=["php"],
    )
    config = _write_config(tmp_path / "providers.json", [entry])
    providers = load_lsp_providers(config)
    assert validate_provider(trusted, "php", providers=providers).provider_id == "intelephense"
    assert command_for(providers[0]) == (str(node), str(script), "--stdio")
    with pytest.raises(ToolError, match="No LSP provider") as unavailable:
        validate_provider(trusted, "python", providers=providers)
    assert unavailable.value.code == "lsp_provider_unavailable"
    entry["languages"] = ["python"]
    with pytest.raises(ToolError) as wrong_language:
        load_lsp_providers(_write_config(config, [entry]))
    assert wrong_language.value.code == "config_invalid"
    entry["languages"] = ["php"]
    script.write_text("modified", encoding="utf-8")
    with pytest.raises(ToolError) as changed:
        validate_provider(trusted, "php", providers=providers)
    assert changed.value.code == "lsp_provider_changed"
    entry["entrypoint"] = None
    entry["entrypoint_sha256"] = None
    with pytest.raises(ToolError) as missing_entrypoint:
        load_lsp_providers(_write_config(config, [entry]))
    assert missing_entrypoint.value.code == "config_invalid"
    assert spawn_spy == []


def test_four_known_provider_configs_stay_bounded(tmp_path: Path, spawn_spy: list[object]) -> None:
    entries = [
        _entry(
            provider_id, executable=tmp_path / f"{provider_id}.exe", entrypoint=tmp_path / f"{provider_id}.js",
            trusted_roots=[tmp_path], languages=[language],
        )
        for provider_id, language in (
            ("pyright", "python"), ("typescript", "typescript"), ("phpactor", "php"), ("intelephense", "php"),
        )
    ]
    config = tmp_path / "providers.json"
    assert len(load_lsp_providers(_write_config(config, entries))) == 4
    with pytest.raises(ToolError) as oversize:
        load_lsp_providers(_write_config(config, [*entries, entries[0]]))
    assert oversize.value.code == "config_invalid"
    assert spawn_spy == []


def test_php_provider_overlap_stays_ambiguous(tmp_path: Path, spawn_spy: list[object]) -> None:
    trusted = tmp_path / "workspace"
    trusted.mkdir()
    entries = [
        _entry(
            provider_id, executable=tmp_path / executable, entrypoint=tmp_path / script,
            trusted_roots=[trusted], languages=["php"],
        )
        for provider_id, executable, script in (
            ("phpactor", "php.exe", "phpactor.php"),
            ("intelephense", "node.exe", "intelephense.js"),
        )
    ]
    providers = load_lsp_providers(_write_config(tmp_path / "providers.json", entries))
    with pytest.raises(ToolError) as ambiguous:
        validate_provider(trusted, "php", providers=providers)
    assert ambiguous.value.code == "lsp_provider_ambiguous"
    assert spawn_spy == []


def test_duplicate_unknown_or_oversize_config_rejected(tmp_path: Path, spawn_spy: list[object]) -> None:
    config = tmp_path / "providers.json"
    config.write_text('{"schema_version":1,"schema_version":1,"providers":[]}', encoding="utf-8")
    with pytest.raises(ToolError) as duplicate:
        load_lsp_providers(config)
    assert duplicate.value.code == "config_invalid"

    trusted = tmp_path / "workspace"
    trusted.mkdir()
    entry = _entry(
        "pyright",
        executable=tmp_path / "pyright.exe",
        entrypoint=None,
        trusted_roots=[trusted],
        languages=["python"],
    )
    entry["extra"] = CONFIG_CANARY
    _write_config(config, [entry])
    with pytest.raises(ToolError) as unknown:
        load_lsp_providers(config)
    assert unknown.value.code == "config_invalid"
    assert CONFIG_CANARY not in str(unknown.value)
    assert CONFIG_CANARY not in repr(unknown.value)

    config.write_bytes(b"x" * (64 * 1024 + 1))
    with pytest.raises(ToolError) as oversize:
        load_lsp_providers(config)
    assert oversize.value.code == "config_invalid"

    config.write_text(json.dumps({"schema_version": 2, "providers": []}), encoding="utf-8")
    with pytest.raises(ToolError) as future_version:
        load_lsp_providers(config)
    assert future_version.value.code == "config_version_unsupported"

    config.write_text('{"schema_version":1,"providers":' + "[" * 10 + "]" * 10 + "}", encoding="utf-8")
    with pytest.raises(ToolError) as deep:
        load_lsp_providers(config)
    assert deep.value.code == "config_invalid"

    config.write_text(json.dumps({"schema_version": 1, "providers": []}), encoding="utf-8")
    with pytest.raises(ToolError) as empty:
        load_lsp_providers(config)
    assert empty.value.code == "config_invalid"

    entry = _entry(
        "pyright",
        executable=tmp_path / "pyright.exe",
        entrypoint=None,
        trusted_roots=[trusted],
        languages=["python", "php"],
    )
    _write_config(config, [entry])
    with pytest.raises(ToolError) as subset:
        load_lsp_providers(config)
    assert subset.value.code == "config_invalid"

    entry = _entry(
        "pyright",
        executable=tmp_path / "pyright.exe",
        entrypoint=tmp_path / "pyright.js",
        trusted_roots=[trusted],
        languages=["python"],
    )
    entry["entrypoint_sha256"] = None
    _write_config(config, [entry])
    with pytest.raises(ToolError) as pairing:
        load_lsp_providers(config)
    assert pairing.value.code == "config_invalid"

    entry = _entry(
        "pyright",
        executable=tmp_path / "pyright.exe",
        entrypoint=None,
        trusted_roots=[trusted],
        languages=["python"],
    )
    entry["executable_sha256"] = "ABC"
    _write_config(config, [entry])
    with pytest.raises(ToolError) as bad_digest:
        load_lsp_providers(config)
    assert bad_digest.value.code == "config_invalid"

    with pytest.raises(ToolError) as missing:
        load_lsp_providers(tmp_path / "missing.json")
    assert missing.value.code == "lsp_provider_config_missing"

    assert spawn_spy == []


def test_windows_batch_launcher_denied(tmp_path: Path, spawn_spy: list[object]) -> None:
    trusted = tmp_path / "workspace"
    trusted.mkdir()
    for suffix in (".cmd", ".CMD", ".bat", ".ps1"):
        entry = _entry(
            "pyright",
            executable=tmp_path / f"node{suffix}",
            entrypoint=None,
            trusted_roots=[trusted],
            languages=["python"],
        )
        config = _write_config(tmp_path / f"providers{suffix.replace('.', '_')}.json", [entry])
        with pytest.raises(ToolError) as denied:
            load_lsp_providers(config)
        assert denied.value.code == "config_invalid"

    script_entry = _entry(
        "phpactor",
        executable=tmp_path / "php.exe",
        entrypoint=tmp_path / "phpactor.ps1",
        trusted_roots=[trusted],
        languages=["php"],
    )
    config = _write_config(tmp_path / "providers-entry.json", [script_entry])
    with pytest.raises(ToolError) as entry_denied:
        load_lsp_providers(config)
    assert entry_denied.value.code == "config_invalid"

    direct = LspProvider(
        provider_id="pyright",
        languages=("python",),
        executable=tmp_path / "node.cmd",
        executable_sha256="0" * 64,
        entrypoint=None,
        entrypoint_sha256=None,
        declared_version="1.0.0",
        trusted_roots=(trusted,),
    )
    with pytest.raises(ToolError) as command_denied:
        command_for(direct)
    assert command_denied.value.code == "lsp_provider_command_denied"
    assert spawn_spy == []


def test_provider_digest_change_denied_before_spawn(tmp_path: Path, spawn_spy: list[object]) -> None:
    trusted = tmp_path / "workspace"
    trusted.mkdir()
    executable = tmp_path / "pyright.exe"
    entrypoint = tmp_path / "pyright.js"
    entry = _entry(
        "pyright",
        executable=executable,
        entrypoint=entrypoint,
        trusted_roots=[trusted],
        languages=["python"],
    )
    providers = load_lsp_providers(_write_config(tmp_path / "providers.json", [entry]))

    assert validate_provider(trusted, "python", providers=providers).provider_id == "pyright"

    executable.write_bytes(b"tampered-binary")
    with pytest.raises(ToolError) as binary_changed:
        validate_provider(trusted, "python", providers=providers)
    assert binary_changed.value.code == "lsp_provider_changed"

    executable.write_bytes(b"provider-binary")
    entrypoint.write_bytes(b"tampered-entrypoint")
    with pytest.raises(ToolError) as entry_changed:
        validate_provider(trusted, "python", providers=providers)
    assert entry_changed.value.code == "lsp_provider_changed"

    entrypoint.write_bytes(b"provider-entrypoint")
    executable.unlink()
    with pytest.raises(ToolError) as missing:
        validate_provider(trusted, "python", providers=providers)
    assert missing.value.code == "lsp_provider_changed"

    with pytest.raises(ToolError) as unavailable:
        validate_provider(trusted, "rust", providers=providers)
    assert unavailable.value.code == "lsp_provider_unavailable"

    outside = tmp_path / "outside"
    outside.mkdir()
    with pytest.raises(ToolError) as untrusted:
        validate_provider(outside, "python", providers=providers)
    assert untrusted.value.code == "lsp_provider_unavailable"

    assert spawn_spy == []


def test_ambiguous_provider_selection_denied(tmp_path: Path, spawn_spy: list[object]) -> None:
    trusted = tmp_path / "workspace"
    trusted.mkdir()
    duplicate_python = (
        LspProvider(
            provider_id="pyright",
            languages=("python",),
            executable=tmp_path / "a.exe",
            executable_sha256="0" * 64,
            entrypoint=None,
            entrypoint_sha256=None,
            declared_version="1.0.0",
            trusted_roots=(trusted,),
        ),
        LspProvider(
            provider_id="typescript",
            languages=("python",),
            executable=tmp_path / "b.exe",
            executable_sha256="0" * 64,
            entrypoint=None,
            entrypoint_sha256=None,
            declared_version="1.0.0",
            trusted_roots=(trusted,),
        ),
    )
    with pytest.raises(ToolError) as ambiguous:
        validate_provider(trusted, "python", providers=duplicate_python)
    assert ambiguous.value.code == "lsp_provider_ambiguous"
    assert spawn_spy == []


def test_worktrees_same_common_dir_have_distinct_keys(tmp_path: Path) -> None:
    trusted = tmp_path / "workspace"
    trusted.mkdir()
    entry = _entry(
        "pyright",
        executable=tmp_path / "pyright.exe",
        entrypoint=None,
        trusted_roots=[trusted],
        languages=["python"],
    )
    providers = load_lsp_providers(_write_config(tmp_path / "providers.json", [entry]))
    provider = providers[0]

    common = tmp_path / "common"
    worktrees = []
    for name in ("alpha", "beta"):
        root = trusted / name
        root.mkdir()
        (root / ".git").write_text(f"gitdir: {common}/worktrees/{name}\n", encoding="utf-8")
        worktrees.append(root)

    key_alpha = make_session_key(worktrees[0], "python", provider, "generation-fixed")
    key_beta = make_session_key(worktrees[1], "python", provider, "generation-fixed")
    assert key_alpha != key_beta
    assert key_alpha.worktree_marker_digest.startswith("file:")
    assert key_beta.worktree_marker_digest.startswith("file:")
    assert key_alpha.workspace_root == canonical_path(worktrees[0])
    assert key_alpha.language_id == "python"
    assert key_alpha.provider_id == "pyright"
    assert key_alpha.runtime_generation == "generation-fixed"
    assert make_session_key(worktrees[0], "python", provider, "generation-fixed") == key_alpha

    plain = trusted / "plain"
    plain.mkdir()
    assert make_session_key(plain, "python", provider, "generation-fixed").worktree_marker_digest == "no-git"

    main = trusted / "main"
    main.mkdir()
    (main / ".git").mkdir()
    first = make_session_key(main, "python", provider, "generation-fixed")
    second = make_session_key(main, "python", provider, "generation-fixed")
    assert first == second
    assert first.worktree_marker_digest.startswith("dir:")

    oversized = trusted / "oversized"
    oversized.mkdir()
    (oversized / ".git").write_bytes(b"x" * 4_097)
    with pytest.raises(ToolError) as marker_oversize:
        make_session_key(oversized, "python", provider, "generation-fixed")
    assert marker_oversize.value.code == "lsp_worktree_marker_invalid"


def test_root_symlink_escape_denied(tmp_path: Path) -> None:
    trusted = tmp_path / "trusted"
    trusted.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    entry = _entry(
        "pyright",
        executable=tmp_path / "pyright.exe",
        entrypoint=None,
        trusted_roots=[trusted],
        languages=["python"],
    )
    providers = load_lsp_providers(_write_config(tmp_path / "providers.json", [entry]))

    link = trusted / "escape"
    try:
        os.symlink(outside, link, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation is not permitted on this machine")

    assert canonical_path(link) == canonical_path(outside)
    with pytest.raises(ToolError) as denied:
        validate_provider(link, "python", providers=providers)
    assert denied.value.code == "lsp_provider_unavailable"

    inside = trusted / "inside"
    inside.mkdir()
    assert validate_provider(inside, "python", providers=providers).provider_id == "pyright"


def test_unicode_and_case_paths_follow_canonical_path(tmp_path: Path) -> None:
    trusted = tmp_path / "wörk-ü"
    trusted.mkdir()
    root = trusted / "ünïcode"
    root.mkdir()
    entry = _entry(
        "pyright",
        executable=tmp_path / "pyright.exe",
        entrypoint=None,
        trusted_roots=[trusted],
        languages=["python"],
    )
    providers = load_lsp_providers(_write_config(tmp_path / "providers.json", [entry]))
    assert validate_provider(root, "python", providers=providers).provider_id == "pyright"

    ascii_trusted = tmp_path / "CaseRoot"
    ascii_trusted.mkdir()
    ascii_root = ascii_trusted / "inner"
    ascii_root.mkdir()
    upper_entry = _entry(
        "pyright",
        executable=tmp_path / "pyright.exe",
        entrypoint=None,
        trusted_roots=[Path(str(ascii_trusted).upper())],
        languages=["python"],
    )
    upper_providers = load_lsp_providers(_write_config(tmp_path / "providers-upper.json", [upper_entry]))
    if sys.platform == "win32":
        assert validate_provider(ascii_root, "python", providers=upper_providers).provider_id == "pyright"
    else:
        with pytest.raises(ToolError) as case_sensitive:
            validate_provider(ascii_root, "python", providers=upper_providers)
        assert case_sensitive.value.code == "lsp_provider_unavailable"


def test_settings_scalar_flags_follow_environment(tmp_path: Path) -> None:
    config = tmp_path / "providers.json"
    code = "from core.config import Settings; s = Settings(); print(s.persistent_enabled, s.providers_config)"
    base_env = {
        key: value
        for key, value in os.environ.items()
        if key not in {"MCP_LSP_PERSISTENT_ENABLED", "MCP_LSP_PROVIDERS_CONFIG"}
    }

    off = subprocess.run(
        [sys.executable, "-c", code],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        env=base_env,
        check=True,
    )
    assert off.stdout.strip() == "False None"

    on = subprocess.run(
        [sys.executable, "-c", code],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        env={**base_env, "MCP_LSP_PERSISTENT_ENABLED": "true", "MCP_LSP_PROVIDERS_CONFIG": str(config)},
        check=True,
    )
    assert on.stdout.strip() == f"True {config}"
