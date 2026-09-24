from __future__ import annotations

import hashlib
import io
import json
import zipfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest

from scripts import tunnel_runtime as module


class FakeResponse(io.BytesIO):
    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()


def _fake_binary(path: Path, version: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"fake-" + version.encode())


def _fake_managed_runtime(root: Path, version: str = "0.0.14") -> Path:
    path = root / ".agent_state/tunnel-runtime" / f"v{version}" / "windows-amd64" / "tunnel-client-runtime-cloudflared.exe"
    _fake_binary(path, version)
    metadata = {
        "schema_version": 1,
        "version": f"v{version}",
        "platform": "windows-amd64",
        "binary_path": str(path.resolve()),
        "runtime_flavor": "tunnel-client-runtime-cloudflared",
        "checked_at": 0.0,
    }
    current = root / ".agent_state/tunnel-runtime/current.json"
    current.parent.mkdir(parents=True, exist_ok=True)
    current.write_text(json.dumps(metadata), encoding="utf-8")
    return path


def test_platform_parts_maps_supported_hosts() -> None:
    assert module.platform_parts(system="Windows", machine="AMD64") == ("windows", "amd64")
    assert module.platform_parts(system="Linux", machine="x86_64") == ("linux", "amd64")
    assert module.platform_parts(system="Darwin", machine="arm64") == ("darwin", "arm64")


def test_platform_parts_rejects_unknown_architecture() -> None:
    with pytest.raises(module.TunnelRuntimeError):
        module.platform_parts(system="Linux", machine="mips64")


def test_release_asset_name_is_platform_specific() -> None:
    assert module.release_asset_name("v0.0.14", "windows", "amd64") == (
        "tunnel-client-runtime-cloudflared-v0.0.14-windows-amd64.zip"
    )
    assert module.executable_name("windows") == "tunnel-client-runtime-cloudflared.exe"
    assert module.executable_name("linux") == "tunnel-client-runtime-cloudflared"


def test_checksum_manifest_requires_exact_asset_name() -> None:
    manifest = (
        b"0" * 64 + b"  unrelated.zip\n"
        + b"a" * 64 + b"  tunnel-client-v0.0.14-windows-amd64.zip\n"
    )
    assert module._checksum_for(manifest, "tunnel-client-v0.0.14-windows-amd64.zip") == "a" * 64


def test_profile_run_args_preserves_explicit_config_sources(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TUNNEL_CLIENT_CONFIG", "custom.yaml")
    assert module.profile_run_args("default") == []
    monkeypatch.delenv("TUNNEL_CLIENT_CONFIG")
    monkeypatch.setenv("TUNNEL_CLIENT_PROFILE_FILE", "profiles/custom.yaml")
    assert module.profile_run_args("custom") == []
    monkeypatch.delenv("TUNNEL_CLIENT_PROFILE_FILE")
    assert module.profile_run_args("custom") == ["--profile", "custom"]


@pytest.mark.parametrize(
    "raw,expected",
    [
        (None, ""),
        ("", ""),
        (b"", ""),
        ("sk-plain-credential", "sk-plain-credential"),
        (b"sk-plain-credential\r\n", "sk-plain-credential"),
        (b"\xef\xbb\xbfsk-utf8-bom\r\n", "sk-utf8-bom"),
        ("sk-utf16-le".encode("utf-16"), "sk-utf16-le"),
        ("sk-utf16-be".encode("utf-16-be"), "sk-utf16-be"),
        ('"sk-double-quoted"', "sk-double-quoted"),
        ("'sk-single-quoted'", "sk-single-quoted"),
        (b'\xef\xbb\xbf"sk-bom-quoted"\r\n', "sk-bom-quoted"),
        (b'\xef\xbb\xbf   \'sk-spaced-quoted\'   \r\n', "sk-spaced-quoted"),
        ("sk-with-newline\n\n".encode("utf-16"), "sk-with-newline"),
    ],
)
def test_clean_control_plane_key(raw: str | bytes | None, expected: str) -> None:
    assert module.clean_control_plane_key(raw) == expected


def test_detect_profile_prefers_explicit_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TUNNEL_CLIENT_PROFILE", "upstream")
    monkeypatch.setenv("MCP_TUNNEL_PROFILE", "work")
    assert module.detect_profile() == "work"
    monkeypatch.delenv("MCP_TUNNEL_PROFILE")
    assert module.detect_profile() == "upstream"


def test_detect_profile_finds_yaml_without_running_full_client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("MCP_TUNNEL_PROFILE", raising=False)
    monkeypatch.delenv("TUNNEL_CLIENT_PROFILE_FILE", raising=False)
    monkeypatch.setenv("TUNNEL_CLIENT_PROFILE_DIR", str(tmp_path))
    (tmp_path / "zeta.yaml").write_text("config_version: 1\n", encoding="utf-8")
    (tmp_path / "alpha.yaml").write_text("config_version: 1\n", encoding="utf-8")
    (tmp_path / "ignored.yml").write_text("config_version: 1\n", encoding="utf-8")
    assert module.detect_profile() == "alpha"


def test_detect_profile_matches_upstream_xdg_then_home_precedence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("MCP_TUNNEL_PROFILE", raising=False)
    monkeypatch.delenv("TUNNEL_CLIENT_PROFILE_FILE", raising=False)
    monkeypatch.delenv("TUNNEL_CLIENT_PROFILE_DIR", raising=False)
    xdg = tmp_path / "xdg"
    home = tmp_path / "home"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))
    monkeypatch.setenv("HOME", str(home))
    (xdg / "tunnel-client").mkdir(parents=True)
    (home / ".config" / "tunnel-client").mkdir(parents=True)
    (xdg / "tunnel-client" / "xdg-profile.yaml").write_text("config_version: 1\n", encoding="utf-8")
    (home / ".config" / "tunnel-client" / "home-profile.yaml").write_text("config_version: 1\n", encoding="utf-8")
    assert module.detect_profile() == "xdg-profile"


def test_current_runtime_requires_managed_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("MCP_TUNNEL_CLIENT_BIN", raising=False)
    with pytest.raises(module.TunnelRuntimeError, match="No managed tunnel runtime"):
        module.current_runtime(tmp_path)


def test_safe_extract_rejects_symbolic_links(tmp_path: Path) -> None:
    archive = tmp_path / "symlink.zip"
    info = zipfile.ZipInfo("link")
    info.create_system = 3
    info.external_attr = (0o120777 << 16)
    with zipfile.ZipFile(archive, "w") as writer:
        writer.writestr(info, "target")
    with pytest.raises(module.TunnelRuntimeError, match="symbolic link"):
        module._safe_extract(archive, tmp_path / "extract")


def test_safe_extract_rejects_path_traversal(tmp_path: Path) -> None:
    archive = tmp_path / "bad.zip"
    with zipfile.ZipFile(archive, "w") as writer:
        writer.writestr("../escape.txt", "bad")
    with pytest.raises(module.TunnelRuntimeError):
        module._safe_extract(archive, tmp_path / "extract")


def test_install_release_verifies_both_release_digest_and_checksum_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(module, "platform_parts", lambda **_: ("windows", "amd64"))
    monkeypatch.setattr(module, "binary_version", lambda path: "0.0.14")

    archive_buffer = io.BytesIO()
    with zipfile.ZipFile(archive_buffer, "w") as writer:
        writer.writestr("tunnel-client-runtime-cloudflared.exe", b"binary")
        writer.writestr("cloudflared.exe", b"cloudflared")
        writer.writestr("cloudflared-manifest.json", "{}")
    archive = archive_buffer.getvalue()
    archive_hash = hashlib.sha256(archive).hexdigest()
    sums = f"{archive_hash}  tunnel-client-runtime-cloudflared-v0.0.14-windows-amd64.zip\n".encode()
    sums_hash = hashlib.sha256(sums).hexdigest()

    release: dict[str, Any] = {
        "tag_name": "v0.0.14",
        "assets": [
            {
                "name": "SHA256SUMS.txt",
                "browser_download_url": "https://github.com/openai/tunnel-client/releases/download/v0.0.14/SHA256SUMS.txt",
                "digest": f"sha256:{sums_hash}",
            },
            {
                "name": "tunnel-client-runtime-cloudflared-v0.0.14-windows-amd64.zip",
                "browser_download_url": (
                    "https://github.com/openai/tunnel-client/releases/download/v0.0.14/"
                    "tunnel-client-runtime-cloudflared-v0.0.14-windows-amd64.zip"
                ),
                "digest": f"sha256:{archive_hash}",
            },
        ],
    }

    def fake_request(url: str, *, timeout: float = 30.0) -> bytes:
        return sums if url.endswith("SHA256SUMS.txt") else archive

    legacy_dir = tmp_path / ".agent_state/tunnel-runtime/v0.0.14/windows-amd64"
    legacy_dir.mkdir(parents=True)
    legacy_client = legacy_dir / "tunnel-client.exe"
    legacy_client.write_bytes(b"legacy-running-client")

    monkeypatch.setattr(module, "_request_bytes", fake_request)
    selection = module._install_release(tmp_path, release)

    assert selection.version == "0.0.14"
    assert selection.source == "managed"
    assert selection.path.is_file()
    assert selection.path.parent.name == "tunnel-client-runtime-cloudflared"
    assert legacy_client.read_bytes() == b"legacy-running-client"
    metadata = json.loads((tmp_path / ".agent_state/tunnel-runtime/current.json").read_text(encoding="utf-8"))
    assert metadata["version"] == "v0.0.14"
    assert metadata["runtime_flavor"] == "tunnel-client-runtime-cloudflared"
    assert metadata["archive_sha256"] == archive_hash


def test_ensure_runtime_uses_update_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("MCP_TUNNEL_CLIENT_BIN", raising=False)
    monkeypatch.setenv("MCP_TUNNEL_AUTO_UPDATE", "0")
    monkeypatch.setattr(module, "platform_parts", lambda **_: ("windows", "amd64"))
    managed = _fake_managed_runtime(tmp_path)
    monkeypatch.setattr(module, "binary_version", lambda path: "0.0.14")
    entered: list[bool] = []

    @contextmanager
    def fake_lock(root: Path) -> Iterator[None]:
        assert root == tmp_path
        entered.append(True)
        yield

    monkeypatch.setattr(module, "_update_lock", fake_lock)
    selection = module.ensure_runtime(tmp_path)

    assert entered == [True]
    assert selection.path == managed


def test_ensure_runtime_falls_back_to_managed_cache_when_update_is_offline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("MCP_TUNNEL_CLIENT_BIN", raising=False)
    monkeypatch.setenv("MCP_TUNNEL_AUTO_UPDATE", "1")
    monkeypatch.setenv("MCP_TUNNEL_UPDATE_INTERVAL_HOURS", "0")
    monkeypatch.delenv("MCP_TUNNEL_UPDATE_REQUIRED", raising=False)
    monkeypatch.setattr(module, "platform_parts", lambda **_: ("windows", "amd64"))
    managed = _fake_managed_runtime(tmp_path)
    monkeypatch.setattr(module, "binary_version", lambda path: "0.0.14")
    monkeypatch.setattr(
        module,
        "_release",
        lambda tag: (_ for _ in ()).throw(module.TunnelRuntimeError("offline")),
    )

    selection = module.ensure_runtime(tmp_path)

    assert selection.path == managed
    assert selection.version == "0.0.14"
    assert selection.warning == "offline"
    failure = json.loads(
        (tmp_path / ".agent_state/tunnel-runtime/last-check-failure.json").read_text(encoding="utf-8")
    )
    assert failure["error"] == "offline"

    monkeypatch.setenv("MCP_TUNNEL_UPDATE_INTERVAL_HOURS", "24")
    monkeypatch.setattr(
        module,
        "_release",
        lambda tag: (_ for _ in ()).throw(AssertionError("release check should be backed off")),
    )
    second = module.ensure_runtime(tmp_path)
    assert second.path == managed
    assert second.warning is None


def test_first_run_without_managed_cache_installs_latest_release(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("MCP_TUNNEL_CLIENT_BIN", raising=False)
    monkeypatch.setenv("MCP_TUNNEL_AUTO_UPDATE", "1")
    monkeypatch.setattr(module, "platform_parts", lambda **_: ("windows", "amd64"))
    release = {"tag_name": "v0.0.14"}
    installed = module.TunnelRuntimeSelection(
        tmp_path / ".agent_state/tunnel-runtime/v0.0.14/windows-amd64/tunnel-client-runtime-cloudflared.exe",
        "0.0.14",
        "managed",
        "windows-amd64",
    )
    seen: list[object] = []

    def fake_release(tag: str | None) -> dict[str, str]:
        seen.append(tag)
        return release

    monkeypatch.setattr(module, "_release", fake_release)
    monkeypatch.setattr(module, "_install_release", lambda root, value: installed)

    selection = module.ensure_runtime(tmp_path)

    assert seen == [None]
    assert selection == installed


def test_required_update_does_not_silently_fall_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("MCP_TUNNEL_CLIENT_BIN", raising=False)
    monkeypatch.setenv("MCP_TUNNEL_AUTO_UPDATE", "1")
    monkeypatch.setenv("MCP_TUNNEL_UPDATE_INTERVAL_HOURS", "0")
    monkeypatch.setenv("MCP_TUNNEL_UPDATE_REQUIRED", "1")
    monkeypatch.setattr(module, "platform_parts", lambda **_: ("windows", "amd64"))
    _fake_managed_runtime(tmp_path)
    monkeypatch.setattr(module, "binary_version", lambda path: "0.0.14")
    monkeypatch.setattr(
        module,
        "_release",
        lambda tag: (_ for _ in ()).throw(module.TunnelRuntimeError("offline")),
    )

    with pytest.raises(module.TunnelRuntimeError, match="offline"):
        module.ensure_runtime(tmp_path)
