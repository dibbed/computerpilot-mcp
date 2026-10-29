from __future__ import annotations

import json
import subprocess
import tarfile
import zipfile
from pathlib import Path

import pytest

from scripts.package_release import (
    SUPPORTED_TARGETS,
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
    _git(tmp_path, "add", ".")
    _git(tmp_path, "update-index", "--chmod=+x", "start_mcp.sh")
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


def test_target_filter_uses_same_source_inventory_for_all_platforms(release_repo: Path) -> None:
    entries = tracked_entries(release_repo)
    windows = {entry.path for entry in _entries_for_target(entries, "windows-amd64")}
    linux = {entry.path for entry in _entries_for_target(entries, "linux-amd64")}
    assert windows == linux
    assert "tunnel-client.exe" not in windows
    assert "cloudflared.exe" not in windows


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
        manifest_name = "computerpilot-mcp-v0.2.7/RELEASE-MANIFEST.json"
        assert manifest_name in names
        manifest = json.loads(archive.read(manifest_name))
        assert manifest["target"] == "windows-amd64"
        assert manifest["project"] == "computerpilot-mcp"
        assert manifest["bundled_tunnel_runtime_included"] is False
        assert manifest["managed_tunnel_runtime"] is True
        assert manifest["tunnel_runtime_download_on_first_use"] is True


def test_build_posix_tar_preserves_launcher_executable_without_bundled_runtime(
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
        prefix = "computerpilot-mcp-v0.2.7"
        assert f"{prefix}/tunnel-client.exe" not in names
        launcher = archive.getmember(f"{prefix}/start_mcp.sh")
        assert launcher.mode & 0o111
        manifest_stream = archive.extractfile(f"{prefix}/RELEASE-MANIFEST.json")
        assert manifest_stream is not None
        manifest = json.loads(manifest_stream.read())
        assert manifest["target"] == "linux-arm64"
        assert manifest["bundled_tunnel_runtime_included"] is False
        assert manifest["tunnel_runtime_download_on_first_use"] is True


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


def test_bundled_upstream_tunnel_file_fails_closed(tmp_path: Path) -> None:
    _git(tmp_path, "init")
    (tmp_path / "tunnel-client-runtime-cloudflared.exe").write_bytes(b"bad")
    _git(tmp_path, "add", "-f", "tunnel-client-runtime-cloudflared.exe")
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
    with pytest.raises(RuntimeError, match="must not be tracked"):
        tracked_entries(tmp_path)


@pytest.mark.parametrize("target", SUPPORTED_TARGETS)
def test_release_ref_ignores_dirty_checkout_and_untracked_secrets(
    release_repo: Path, tmp_path: Path, target: str,
) -> None:
    source = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=release_repo, text=True).strip()
    (release_repo / "README.md").write_text("unpublished replacement", encoding="utf-8")
    (release_repo / "core/config.py").write_text('version = "9.9.9"\n', encoding="utf-8")
    (release_repo / ".secrets").mkdir()
    (release_repo / ".secrets/canary.txt").write_text("private-release-canary", encoding="utf-8")
    (release_repo / ".agent_state").mkdir()
    (release_repo / ".agent_state/operator.db").write_bytes(b"private-state-canary")
    artifact = build_artifact(release_repo, version="0.2.7", target=target, output_dir=tmp_path / "artifacts", ref=source)
    prefix = "computerpilot-mcp-v0.2.7/"
    if target.startswith("windows-"):
        with zipfile.ZipFile(artifact.path) as archive:
            contents = {name: archive.read(name) for name in archive.namelist()}
    else:
        with tarfile.open(artifact.path, "r:gz") as archive:
            contents = {}
            for member in archive.getmembers():
                stream = archive.extractfile(member)
                assert stream is not None
                contents[member.name] = stream.read()
    assert contents[prefix + "README.md"] == b"hello\n"
    assert all(".secrets/" not in name and ".agent_state/" not in name for name in contents)
    manifest = json.loads(contents[prefix + "RELEASE-MANIFEST.json"])
    assert manifest["source_commit"] == source
    assert manifest["version"] == "0.2.7" and manifest["target"] == target
    assert manifest["file_count"] + 1 == len(contents) == artifact.file_count
    repeated = build_artifact(release_repo, version="0.2.7", target=target, output_dir=tmp_path / "repeat", ref=source)
    assert repeated.sha256 == artifact.sha256


def test_split_runtime_and_package_versions_fail_before_writing_archive(release_repo: Path, tmp_path: Path) -> None:
    (release_repo / "core/config.py").write_text('class Settings:\n    version: str = "0.9.0"\n', encoding="utf-8")
    _git(release_repo, "add", "core/config.py")
    _git(release_repo, "-c", "user.name=Tests", "-c", "user.email=tests@example.invalid", "commit", "-m", "split versions")
    output = tmp_path / "refused-artifacts"
    with pytest.raises(RuntimeError, match="Release version mismatch"):
        build_artifact(release_repo, version="0.9.0", target="windows-amd64", output_dir=output)
    assert not output.exists()
