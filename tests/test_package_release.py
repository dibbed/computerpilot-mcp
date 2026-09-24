from __future__ import annotations

import json
import subprocess
import tarfile
import zipfile
from pathlib import Path

import pytest

from scripts.package_release import (
    _entries_for_target,
    build_artifact,
    tracked_entries,
    write_checksums,
)


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)


@pytest.fixture
def release_repo(tmp_path: Path) -> Path:
    _git(tmp_path, "init")
    (tmp_path / "core").mkdir()
    (tmp_path / "core/config.py").write_text(
        'class Settings:\n    version: str = "0.2.7"\n',
        encoding="utf-8",
    )
    (tmp_path / "pyproject.toml").write_text('[project]\nversion = "0.2.7"\n', encoding="utf-8")
    launcher = tmp_path / "start_mcp.sh"
    launcher.write_text("#!/bin/sh\necho ok\n", encoding="utf-8")
    launcher.chmod(0o755)
    (tmp_path / "README.md").write_text("hello\n", encoding="utf-8")
    (tmp_path / "tunnel-client.exe").write_bytes(b"tunnel")
    (tmp_path / "cloudflared.exe").write_bytes(b"cloudflare")
    (tmp_path / "cloudflared-manifest.json").write_text("{}\n", encoding="utf-8")
    _git(tmp_path, "add", ".")
    _git(
        tmp_path,
        "-c",
        "user.name=Tests",
        "-c",
        "user.email=tests@example.invalid",
        "commit",
        "-m",
        "fixture",
    )
    return tmp_path


def test_target_filter_keeps_windows_fallback_only_for_windows(release_repo: Path) -> None:
    entries = tracked_entries(release_repo)
    windows = {entry.path for entry in _entries_for_target(entries, "windows-amd64")}
    linux = {entry.path for entry in _entries_for_target(entries, "linux-amd64")}
    assert "tunnel-client.exe" in windows
    assert "cloudflared.exe" in windows
    assert "cloudflared-manifest.json" in windows
    assert "tunnel-client.exe" not in linux
    assert "cloudflared.exe" not in linux
    assert "cloudflared-manifest.json" not in linux


def test_build_windows_zip_is_deterministic_and_has_manifest(release_repo: Path, tmp_path: Path) -> None:
    first = build_artifact(
        release_repo,
        version="0.2.7",
        target="windows-amd64",
        output_dir=tmp_path / "one",
    )
    second = build_artifact(
        release_repo,
        version="0.2.7",
        target="windows-amd64",
        output_dir=tmp_path / "two",
    )
    assert first.sha256 == second.sha256
    with zipfile.ZipFile(first.path) as archive:
        names = set(archive.namelist())
        manifest_name = "windows-agent-mcp-v0.2.7/RELEASE-MANIFEST.json"
        assert manifest_name in names
        manifest = json.loads(archive.read(manifest_name))
        assert manifest["target"] == "windows-amd64"
        assert manifest["windows_offline_tunnel_fallback_included"] is True


def test_build_posix_tar_preserves_launcher_executable_and_excludes_windows_fallback(
    release_repo: Path,
    tmp_path: Path,
) -> None:
    result = build_artifact(
        release_repo,
        version="0.2.7",
        target="linux-arm64",
        output_dir=tmp_path,
    )
    with tarfile.open(result.path, "r:gz") as archive:
        names = set(archive.getnames())
        prefix = "windows-agent-mcp-v0.2.7"
        assert f"{prefix}/tunnel-client.exe" not in names
        launcher = archive.getmember(f"{prefix}/start_mcp.sh")
        assert launcher.mode & 0o111
        manifest_stream = archive.extractfile(f"{prefix}/RELEASE-MANIFEST.json")
        assert manifest_stream is not None
        manifest = json.loads(manifest_stream.read())
        assert manifest["target"] == "linux-arm64"
        assert manifest["windows_offline_tunnel_fallback_included"] is False


def test_version_mismatch_fails_closed(release_repo: Path, tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="Release version mismatch"):
        build_artifact(
            release_repo,
            version="9.9.9",
            target="linux-amd64",
            output_dir=tmp_path,
        )


def test_checksum_manifest_is_sorted(release_repo: Path, tmp_path: Path) -> None:
    results = [
        build_artifact(release_repo, version="0.2.7", target="linux-amd64", output_dir=tmp_path),
        build_artifact(release_repo, version="0.2.7", target="windows-amd64", output_dir=tmp_path),
    ]
    checksum = write_checksums(results, tmp_path)
    lines = checksum.read_text(encoding="utf-8").splitlines()
    assert lines == sorted(lines, key=lambda line: line.split("  ", 1)[1])


def test_forbidden_tracked_runtime_path_fails_closed(tmp_path: Path) -> None:
    _git(tmp_path, "init")
    (tmp_path / ".agent_state").mkdir()
    (tmp_path / ".agent_state/secret.txt").write_text("bad", encoding="utf-8")
    _git(tmp_path, "add", "-f", ".agent_state/secret.txt")
    _git(
        tmp_path,
        "-c",
        "user.name=Tests",
        "-c",
        "user.email=tests@example.invalid",
        "commit",
        "-m",
        "fixture",
    )
    with pytest.raises(RuntimeError, match="Forbidden"):
        tracked_entries(tmp_path)
