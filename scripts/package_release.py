"""Deterministic cross-platform release packaging from tracked Git contents."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import re
import subprocess
import tarfile
import zipfile
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SUPPORTED_TARGETS = (
    "windows-amd64",
    "linux-amd64",
    "linux-arm64",
    "macos-amd64",
    "macos-arm64",
)
_WINDOWS_FALLBACK_FILES = frozenset({
    "tunnel-client.exe",
    "cloudflared.exe",
    "cloudflared-manifest.json",
})
_FORBIDDEN_PARTS = frozenset({
    ".agent_state",
    ".secrets",
    ".venv",
    ".pytest_cache",
    ".pytest-tmp",
    "__pycache__",
    "dist",
    "output",
})
_VERSION_RE = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")


@dataclass(frozen=True, slots=True)
class GitEntry:
    path: str
    mode: int


@dataclass(frozen=True, slots=True)
class ArtifactResult:
    target: str
    path: Path
    sha256: str
    size_bytes: int
    file_count: int

    def to_dict(self) -> dict[str, str | int]:
        return {
            "target": self.target,
            "name": self.path.name,
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
            "file_count": self.file_count,
        }


def _git(root: Path, *args: str, text: bool = False) -> bytes | str:
    result = subprocess.run(
        ["git", *args],
        cwd=root,
        capture_output=True,
        check=False,
        text=text,
    )
    if result.returncode:
        stderr = result.stderr if text else result.stderr.decode("utf-8", errors="replace")
        raise RuntimeError(f"git {' '.join(args)} failed: {stderr[-1000:]}")
    return result.stdout


def tracked_entries(root: Path, ref: str = "HEAD") -> list[GitEntry]:
    raw = _git(root, "ls-tree", "-rz", ref)
    assert isinstance(raw, bytes)
    entries: list[GitEntry] = []
    for record in raw.split(b"\0"):
        if not record:
            continue
        metadata, raw_path = record.split(b"\t", 1)
        mode_raw, kind, _sha = metadata.split(b" ", 2)
        if kind != b"blob":
            continue
        path = raw_path.decode("utf-8", errors="strict")
        parts = Path(path).parts
        if Path(path).is_absolute() or ".." in parts:
            raise RuntimeError(f"Unsafe tracked path: {path!r}")
        if any(part in _FORBIDDEN_PARTS for part in parts):
            raise RuntimeError(f"Forbidden runtime/build path is tracked: {path!r}")
        entries.append(GitEntry(path=path, mode=int(mode_raw, 8)))
    return sorted(entries, key=lambda item: item.path)


def file_bytes(root: Path, ref: str, path: str) -> bytes:
    value = _git(root, "show", f"{ref}:{path}")
    assert isinstance(value, bytes)
    return value


def _project_versions(root: Path, ref: str) -> tuple[str, str]:
    config = file_bytes(root, ref, "core/config.py").decode("utf-8")
    pyproject = file_bytes(root, ref, "pyproject.toml").decode("utf-8")
    config_match = re.search(r'^\s*version:\s*str\s*=\s*"([^"]+)"', config, re.MULTILINE)
    project_match = re.search(r'^version\s*=\s*"([^"]+)"', pyproject, re.MULTILINE)
    if config_match is None or project_match is None:
        raise RuntimeError("Could not resolve project versions from tracked source.")
    return config_match.group(1), project_match.group(1)


def validate_version(root: Path, ref: str, version: str) -> None:
    if not _VERSION_RE.fullmatch(version):
        raise ValueError(f"Invalid release version: {version!r}")
    config_version, project_version = _project_versions(root, ref)
    if config_version != version or project_version != version:
        raise RuntimeError(
            "Release version mismatch: "
            f"requested={version}, core/config.py={config_version}, pyproject.toml={project_version}"
        )


def _entries_for_target(entries: list[GitEntry], target: str) -> list[GitEntry]:
    if target not in SUPPORTED_TARGETS:
        raise ValueError(f"Unsupported target: {target}")
    if target.startswith("windows-"):
        return entries
    return [entry for entry in entries if entry.path not in _WINDOWS_FALLBACK_FILES]


def _manifest(
    *,
    root: Path,
    ref: str,
    version: str,
    target: str,
    entries: list[GitEntry],
) -> bytes:
    commit = _git(root, "rev-parse", ref, text=True)
    assert isinstance(commit, str)
    payload = {
        "schema_version": 1,
        "project": "windows-agent-mcp",
        "version": version,
        "target": target,
        "source_commit": commit.strip(),
        "file_count": len(entries),
        "windows_offline_tunnel_fallback_included": target.startswith("windows-"),
        "managed_tunnel_runtime": True,
    }
    return (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _archive_prefix(version: str) -> str:
    return f"windows-agent-mcp-v{version}"


def _write_zip(
    root: Path,
    ref: str,
    output: Path,
    *,
    prefix: str,
    entries: list[GitEntry],
    manifest: bytes,
) -> None:
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for entry in entries:
            info = zipfile.ZipInfo(f"{prefix}/{entry.path}", date_time=(1980, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.external_attr = (entry.mode & 0xFFFF) << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, file_bytes(root, ref, entry.path), compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
        info = zipfile.ZipInfo(f"{prefix}/RELEASE-MANIFEST.json", date_time=(1980, 1, 1, 0, 0, 0))
        info.create_system = 3
        info.external_attr = 0o100644 << 16
        info.compress_type = zipfile.ZIP_DEFLATED
        archive.writestr(info, manifest, compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)


def _tar_info(name: str, *, mode: int, size: int, kind: bytes = tarfile.REGTYPE) -> tarfile.TarInfo:
    info = tarfile.TarInfo(name)
    info.mode = mode & 0o7777
    info.size = size
    info.mtime = 0
    info.uid = 0
    info.gid = 0
    info.uname = ""
    info.gname = ""
    info.type = kind
    return info


def _write_tar_gz(
    root: Path,
    ref: str,
    output: Path,
    *,
    prefix: str,
    entries: list[GitEntry],
    manifest: bytes,
) -> None:
    with output.open("wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0, compresslevel=9) as compressed:
            with tarfile.open(fileobj=compressed, mode="w", format=tarfile.PAX_FORMAT) as archive:
                for entry in entries:
                    data = file_bytes(root, ref, entry.path)
                    info = _tar_info(f"{prefix}/{entry.path}", mode=entry.mode, size=len(data))
                    archive.addfile(info, io.BytesIO(data))
                info = _tar_info(f"{prefix}/RELEASE-MANIFEST.json", mode=0o100644, size=len(manifest))
                archive.addfile(info, io.BytesIO(manifest))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def build_artifact(
    root: Path,
    *,
    version: str,
    target: str,
    output_dir: Path,
    ref: str = "HEAD",
) -> ArtifactResult:
    validate_version(root, ref, version)
    entries = _entries_for_target(tracked_entries(root, ref), target)
    prefix = _archive_prefix(version)
    output_dir.mkdir(parents=True, exist_ok=True)
    suffix = ".zip" if target.startswith("windows-") else ".tar.gz"
    output = output_dir / f"{prefix}-{target}{suffix}"
    manifest = _manifest(root=root, ref=ref, version=version, target=target, entries=entries)
    if suffix == ".zip":
        _write_zip(root, ref, output, prefix=prefix, entries=entries, manifest=manifest)
    else:
        _write_tar_gz(root, ref, output, prefix=prefix, entries=entries, manifest=manifest)
    return ArtifactResult(
        target=target,
        path=output,
        sha256=_sha256(output),
        size_bytes=output.stat().st_size,
        file_count=len(entries) + 1,
    )


def write_checksums(results: list[ArtifactResult], output_dir: Path) -> Path:
    path = output_dir / "SHA256SUMS.txt"
    lines = [f"{result.sha256}  {result.path.name}" for result in sorted(results, key=lambda item: item.path.name)]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    return path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True)
    parser.add_argument("--target", action="append", choices=SUPPORTED_TARGETS)
    parser.add_argument("--all", action="store_true", help="Build every supported release target.")
    parser.add_argument("--output-dir", type=Path, default=PROJECT_ROOT / "dist")
    parser.add_argument("--ref", default="HEAD")
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.all and args.target:
        raise SystemExit("Use --all or --target, not both.")
    targets = list(SUPPORTED_TARGETS if args.all else (args.target or ()))
    if not targets:
        raise SystemExit("At least one --target or --all is required.")
    output_dir = args.output_dir if args.output_dir.is_absolute() else PROJECT_ROOT / args.output_dir
    results = [
        build_artifact(PROJECT_ROOT, version=args.version, target=target, output_dir=output_dir, ref=args.ref)
        for target in targets
    ]
    checksum_path = write_checksums(results, output_dir)
    payload = {
        "version": args.version,
        "ref": args.ref,
        "artifacts": [result.to_dict() for result in results],
        "checksums": str(checksum_path),
    }
    print(json.dumps(payload, separators=(",", ":") if args.json else None, indent=None if args.json else 2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
