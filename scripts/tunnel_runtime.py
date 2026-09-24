"""Secure managed tunnel-client selection and self-update support.

The repository-bundled tunnel client remains an offline fallback. Normal startup
can install a verified upstream release into .agent_state without mutating Git
tracked binaries, then run that immutable managed copy.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import uuid
import zipfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
UPSTREAM_REPOSITORY = "openai/tunnel-client"
GITHUB_API = f"https://api.github.com/repos/{UPSTREAM_REPOSITORY}"
DEFAULT_UPDATE_INTERVAL_HOURS = 24.0
METADATA_SCHEMA_VERSION = 1
_VERSION_RE = re.compile(r"(?<![0-9])(\d+\.\d+\.\d+)(?![0-9])")


class TunnelRuntimeError(RuntimeError):
    """Raised when no safe tunnel runtime can be resolved."""


@dataclass(frozen=True, slots=True)
class TunnelRuntimeSelection:
    path: Path
    version: str
    source: str
    platform_key: str
    warning: str | None = None


def _bool_env(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().casefold() not in {"0", "false", "no", "off", ""}


def _normalize_tag(value: str) -> str:
    value = value.strip()
    if not value:
        raise TunnelRuntimeError("Tunnel runtime version pin is empty.")
    tag = value if value.startswith("v") else f"v{value}"
    if _VERSION_RE.fullmatch(tag[1:]) is None:
        raise TunnelRuntimeError(f"Unsupported tunnel runtime version pin: {value!r}")
    return tag


def _version_key(version: str) -> tuple[int, int, int]:
    matched = _VERSION_RE.search(version)
    if matched is None:
        return (-1, -1, -1)
    return tuple(int(part) for part in matched.group(1).split("."))  # type: ignore[return-value]


def platform_parts(
    *,
    system: str | None = None,
    machine: str | None = None,
) -> tuple[str, str]:
    raw_system = (system or platform.system()).strip().casefold()
    raw_machine = (machine or platform.machine()).strip().casefold()

    systems = {
        "windows": "windows",
        "linux": "linux",
        "darwin": "darwin",
    }
    architectures = {
        "amd64": "amd64",
        "x86_64": "amd64",
        "x64": "amd64",
        "arm64": "arm64",
        "aarch64": "arm64",
    }
    system_name = systems.get(raw_system)
    architecture = architectures.get(raw_machine)
    if system_name is None:
        raise TunnelRuntimeError(f"Unsupported tunnel runtime operating system: {raw_system or '<unknown>'}")
    if architecture is None:
        raise TunnelRuntimeError(f"Unsupported tunnel runtime architecture: {raw_machine or '<unknown>'}")
    return system_name, architecture


def platform_key() -> str:
    system_name, architecture = platform_parts()
    return f"{system_name}-{architecture}"


def executable_name(system_name: str) -> str:
    return "tunnel-client.exe" if system_name == "windows" else "tunnel-client"


def runtime_executable_name(system_name: str) -> str:
    return "tunnel-client-runtime.exe" if system_name == "windows" else "tunnel-client-runtime"


def release_asset_name(tag: str, system_name: str, architecture: str) -> str:
    return f"tunnel-client-{tag}-{system_name}-{architecture}.zip"


def _state_root(root: Path) -> Path:
    configured = os.getenv("MCP_STATE_DIR", "").strip()
    state_dir = Path(configured).expanduser() if configured else root / ".agent_state"
    if not state_dir.is_absolute():
        state_dir = root / state_dir
    return state_dir / "tunnel-runtime"


def _metadata_path(root: Path) -> Path:
    return _state_root(root) / "current.json"


def _failed_check_path(root: Path) -> Path:
    return _state_root(root) / "last-check-failure.json"


@contextmanager
def _update_lock(root: Path) -> Iterator[None]:
    """Serialize managed-runtime checks and publication across local processes."""
    lock_path = _state_root(root) / ".update.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+b") as handle:
        if os.name == "nt":
            import msvcrt

            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
            try:
                yield
            finally:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            return

        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)  # type: ignore[attr-defined]
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)  # type: ignore[attr-defined]


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    return value if isinstance(value, dict) else None


def _write_json_atomic(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}-", suffix=".tmp", dir=path.parent)
    temporary_path = Path(temporary)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(value, stream, sort_keys=True, separators=(",", ":"))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)


def binary_version(path: Path) -> str:
    if not path.is_file():
        raise TunnelRuntimeError(f"Tunnel runtime binary does not exist: {path}")
    try:
        result = subprocess.run(
            [str(path), "--version"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise TunnelRuntimeError(f"Could not execute tunnel runtime version check: {type(exc).__name__}") from exc
    output = "\n".join(part for part in (result.stdout, result.stderr) if part).strip()
    if result.returncode != 0:
        raise TunnelRuntimeError(f"Tunnel runtime version check exited with {result.returncode}.")
    matched = _VERSION_RE.search(output)
    if matched is None:
        raise TunnelRuntimeError("Tunnel runtime did not report a semantic version.")
    return matched.group(1)


def _managed_from_metadata(root: Path) -> TunnelRuntimeSelection | None:
    metadata = _read_json(_metadata_path(root))
    if not metadata:
        return None
    path_raw = metadata.get("binary_path")
    version = str(metadata.get("version") or "").removeprefix("v")
    recorded_platform = str(metadata.get("platform") or "")
    if not isinstance(path_raw, str) or not path_raw or _VERSION_RE.fullmatch(version) is None:
        return None
    path = Path(path_raw)
    if not path.is_absolute():
        path = root / path
    if not path.is_file():
        return None
    try:
        actual_version = binary_version(path)
    except TunnelRuntimeError:
        return None
    if actual_version != version:
        return None
    try:
        current_platform = platform_key()
    except TunnelRuntimeError:
        current_platform = recorded_platform
    if recorded_platform and current_platform and recorded_platform != current_platform:
        return None
    return TunnelRuntimeSelection(
        path=path,
        version=actual_version,
        source="managed",
        platform_key=recorded_platform or current_platform,
    )


def _explicit_override() -> TunnelRuntimeSelection | None:
    raw = os.getenv("MCP_TUNNEL_CLIENT_BIN", "").strip()
    if not raw:
        return None
    path = Path(raw).expanduser()
    version = binary_version(path)
    return TunnelRuntimeSelection(path=path, version=version, source="override", platform_key=platform_key())


def _local_candidates(root: Path) -> list[TunnelRuntimeSelection]:
    system_name, architecture = platform_parts()
    candidates: list[tuple[Path, str, int, int]] = [
        # Keep the full-client fallback ahead of a manually dropped narrow runtime.
        # The managed installer also uses the full-client release archive, preserving
        # the command/Cloudflared surface that the Supervisor historically ran.
        (root / executable_name(system_name), "bundled-client", 2, 1),
        (root / runtime_executable_name(system_name), "bundled-runtime", 1, 1),
    ]
    managed = _managed_from_metadata(root)
    resolved: list[tuple[TunnelRuntimeSelection, int, int]] = []
    if managed is not None:
        resolved.append((managed, 2, 2))
    for path, source, flavor_rank, source_rank in candidates:
        if not path.is_file():
            continue
        try:
            version = binary_version(path)
        except TunnelRuntimeError:
            continue
        resolved.append((
            TunnelRuntimeSelection(
                path=path,
                version=version,
                source=source,
                platform_key=f"{system_name}-{architecture}",
            ),
            flavor_rank,
            source_rank,
        ))
    resolved.sort(
        key=lambda item: (item[1], _version_key(item[0].version), item[2]),
        reverse=True,
    )
    return [item[0] for item in resolved]


def current_runtime(root: Path = PROJECT_ROOT) -> TunnelRuntimeSelection:
    override = _explicit_override()
    if override is not None:
        return override
    candidates = _local_candidates(root)
    if not candidates:
        raise TunnelRuntimeError("No usable tunnel-client runtime is available.")
    return candidates[0]


def _request_bytes(url: str, *, timeout: float = 30.0) -> bytes:
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "windows-agent-mcp-tunnel-updater",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read()
    except (OSError, urllib.error.URLError) as exc:
        raise TunnelRuntimeError(f"Tunnel runtime update request failed: {type(exc).__name__}") from exc


def _release(tag: str | None) -> dict[str, Any]:
    endpoint = f"{GITHUB_API}/releases/tags/{tag}" if tag else f"{GITHUB_API}/releases/latest"
    try:
        value = json.loads(_request_bytes(endpoint).decode("utf-8"))
    except (UnicodeError, ValueError) as exc:
        raise TunnelRuntimeError("GitHub returned invalid tunnel runtime release metadata.") from exc
    if not isinstance(value, dict):
        raise TunnelRuntimeError("GitHub returned an unexpected tunnel runtime release payload.")
    if bool(value.get("draft")):
        raise TunnelRuntimeError("Refusing to install a draft tunnel-client release.")
    if bool(value.get("prerelease")) and not _bool_env("MCP_TUNNEL_ALLOW_PRERELEASE", False):
        raise TunnelRuntimeError("Refusing to install a prerelease tunnel-client build.")
    return value


def _asset(release: dict[str, Any], name: str) -> dict[str, Any]:
    assets = release.get("assets")
    if not isinstance(assets, list):
        raise TunnelRuntimeError("Tunnel runtime release has no asset list.")
    for item in assets:
        if isinstance(item, dict) and item.get("name") == name:
            return item
    raise TunnelRuntimeError(f"Tunnel runtime release is missing required asset: {name}")


def _verified_asset_bytes(asset: dict[str, Any]) -> bytes:
    url = asset.get("browser_download_url")
    if not isinstance(url, str) or not url.startswith("https://github.com/openai/tunnel-client/"):
        raise TunnelRuntimeError("Tunnel runtime release asset URL is not an approved OpenAI GitHub URL.")
    content = _request_bytes(url, timeout=120.0)
    digest = str(asset.get("digest") or "")
    if not digest.startswith("sha256:"):
        raise TunnelRuntimeError("Tunnel runtime release asset is missing a GitHub SHA-256 digest.")
    expected = digest.partition(":")[2].casefold()
    actual = hashlib.sha256(content).hexdigest()
    if actual != expected:
        raise TunnelRuntimeError(f"SHA-256 mismatch for release asset {asset.get('name')!r}.")
    return content


def _checksum_for(manifest: bytes, asset_name: str) -> str:
    try:
        text = manifest.decode("utf-8")
    except UnicodeError as exc:
        raise TunnelRuntimeError("Tunnel runtime checksum manifest is not UTF-8.") from exc
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        parts = stripped.split(maxsplit=1)
        if len(parts) != 2:
            continue
        digest, raw_name = parts
        name = raw_name.lstrip("*").strip()
        if name == asset_name and re.fullmatch(r"[0-9a-fA-F]{64}", digest):
            return digest.casefold()
    raise TunnelRuntimeError(f"Checksum manifest has no entry for {asset_name}.")


def _safe_extract(archive_path: Path, destination: Path) -> None:
    with zipfile.ZipFile(archive_path) as archive:
        root = destination.resolve()
        for member in archive.infolist():
            candidate = (destination / member.filename).resolve()
            try:
                candidate.relative_to(root)
            except ValueError as exc:
                raise TunnelRuntimeError("Tunnel runtime archive contains an unsafe path.") from exc
            unix_mode = (member.external_attr >> 16) & 0o170000
            if stat.S_ISLNK(unix_mode):
                raise TunnelRuntimeError("Tunnel runtime archive contains a symbolic link.")
        archive.extractall(destination)


def _find_binary(directory: Path, name: str) -> Path:
    matches = [path for path in directory.rglob(name) if path.is_file()]
    if len(matches) != 1:
        raise TunnelRuntimeError(f"Expected exactly one {name} in the tunnel runtime archive; found {len(matches)}.")
    return matches[0]


def _install_release(root: Path, release: dict[str, Any]) -> TunnelRuntimeSelection:
    tag = _normalize_tag(str(release.get("tag_name") or ""))
    system_name, architecture = platform_parts()
    key = f"{system_name}-{architecture}"
    asset_name = release_asset_name(tag, system_name, architecture)
    archive_asset = _asset(release, asset_name)
    checksums_asset = _asset(release, "SHA256SUMS.txt")

    manifest = _verified_asset_bytes(checksums_asset)
    archive_bytes = _verified_asset_bytes(archive_asset)
    expected_archive_hash = _checksum_for(manifest, asset_name)
    actual_archive_hash = hashlib.sha256(archive_bytes).hexdigest()
    if actual_archive_hash != expected_archive_hash:
        raise TunnelRuntimeError(f"SHA256SUMS.txt verification failed for {asset_name}.")

    runtime_root = _state_root(root)
    final_dir = runtime_root / tag / key
    binary_name = executable_name(system_name)
    if final_dir.is_dir():
        try:
            existing = _find_binary(final_dir, binary_name)
            existing_version = binary_version(existing)
            if existing_version == tag.removeprefix("v"):
                _record_current(root, tag, key, existing, actual_archive_hash, asset_name)
                return TunnelRuntimeSelection(existing, existing_version, "managed", key)
        except TunnelRuntimeError:
            shutil.rmtree(final_dir, ignore_errors=True)

    runtime_root.mkdir(parents=True, exist_ok=True)
    staging = runtime_root / f".staging-{uuid.uuid4().hex}"
    archive_path = runtime_root / f".download-{uuid.uuid4().hex}.zip"
    try:
        staging.mkdir()
        archive_path.write_bytes(archive_bytes)
        _safe_extract(archive_path, staging)
        staged_binary = _find_binary(staging, binary_name)
        if system_name != "windows":
            staged_binary.chmod(staged_binary.stat().st_mode | 0o111)
            for companion_name in ("cloudflared",):
                for companion in staging.rglob(companion_name):
                    if companion.is_file():
                        companion.chmod(companion.stat().st_mode | 0o111)
        staged_version = binary_version(staged_binary)
        if staged_version != tag.removeprefix("v"):
            raise TunnelRuntimeError(
                f"Downloaded tunnel runtime reports {staged_version}, expected {tag.removeprefix('v')}."
            )
        relative_binary = staged_binary.relative_to(staging)
        final_dir.parent.mkdir(parents=True, exist_ok=True)
        if final_dir.exists():
            shutil.rmtree(final_dir)
        os.replace(staging, final_dir)
        final_binary = final_dir / relative_binary
        _record_current(root, tag, key, final_binary, actual_archive_hash, asset_name)
        return TunnelRuntimeSelection(final_binary, staged_version, "managed", key)
    finally:
        archive_path.unlink(missing_ok=True)
        shutil.rmtree(staging, ignore_errors=True)


def _record_current(
    root: Path,
    tag: str,
    key: str,
    binary: Path,
    archive_sha256: str,
    asset_name: str,
) -> None:
    _write_json_atomic(
        _metadata_path(root),
        {
            "schema_version": METADATA_SCHEMA_VERSION,
            "version": tag,
            "platform": key,
            "binary_path": str(binary.resolve()),
            "archive_asset": asset_name,
            "archive_sha256": archive_sha256,
            "checked_at": time.time(),
            "upstream_repository": UPSTREAM_REPOSITORY,
        },
    )
    _clear_failed_check(root)


def _record_failed_check(root: Path, error: str) -> None:
    _write_json_atomic(
        _failed_check_path(root),
        {
            "schema_version": METADATA_SCHEMA_VERSION,
            "checked_at": time.time(),
            "error": error[:500],
            "upstream_repository": UPSTREAM_REPOSITORY,
        },
    )


def _clear_failed_check(root: Path) -> None:
    _failed_check_path(root).unlink(missing_ok=True)


def _record_check(root: Path, selection: TunnelRuntimeSelection, *, latest_tag: str) -> None:
    existing = _read_json(_metadata_path(root)) or {}
    existing.update({
        "schema_version": METADATA_SCHEMA_VERSION,
        "version": f"v{selection.version}",
        "platform": selection.platform_key,
        "binary_path": str(selection.path.resolve()),
        "checked_at": time.time(),
        "latest_seen": latest_tag,
        "upstream_repository": UPSTREAM_REPOSITORY,
    })
    _write_json_atomic(_metadata_path(root), existing)
    _clear_failed_check(root)


def _update_due(root: Path, interval_hours: float, *, include_failures: bool) -> bool:
    metadata = _read_json(_metadata_path(root))
    checked_at = metadata.get("checked_at") if metadata else None
    if not isinstance(checked_at, (int, float)) and include_failures:
        failed = _read_json(_failed_check_path(root))
        checked_at = failed.get("checked_at") if failed else None
    if not isinstance(checked_at, (int, float)):
        return True
    return time.time() - float(checked_at) >= max(interval_hours, 0.0) * 3600.0


def ensure_runtime(root: Path = PROJECT_ROOT) -> TunnelRuntimeSelection:
    override = _explicit_override()
    if override is not None:
        return override

    with _update_lock(root):
        fallback: TunnelRuntimeSelection | None
        try:
            fallback = current_runtime(root)
        except TunnelRuntimeError:
            fallback = None

        if not _bool_env("MCP_TUNNEL_AUTO_UPDATE", True):
            if fallback is None:
                raise TunnelRuntimeError("Tunnel auto-update is disabled and no local runtime is available.")
            return fallback

        try:
            interval_hours = float(os.getenv("MCP_TUNNEL_UPDATE_INTERVAL_HOURS", str(DEFAULT_UPDATE_INTERVAL_HOURS)))
        except ValueError as exc:
            raise TunnelRuntimeError("MCP_TUNNEL_UPDATE_INTERVAL_HOURS must be numeric.") from exc
        if interval_hours < 0:
            raise TunnelRuntimeError("MCP_TUNNEL_UPDATE_INTERVAL_HOURS cannot be negative.")

        pin_raw = os.getenv("MCP_TUNNEL_VERSION", "").strip()
        pin = _normalize_tag(pin_raw) if pin_raw else None
        strict = _bool_env("MCP_TUNNEL_UPDATE_REQUIRED", False)
        if (
            pin is None
            and fallback is not None
            and not _update_due(root, interval_hours, include_failures=not strict)
        ):
            return fallback
        if pin is not None and fallback is not None and fallback.source == "managed" and fallback.version == pin.removeprefix("v"):
            _record_check(root, fallback, latest_tag=pin)
            return fallback

        try:
            release = _release(pin)
            tag = _normalize_tag(str(release.get("tag_name") or ""))
            if fallback is not None and fallback.source == "managed" and fallback.version == tag.removeprefix("v"):
                _record_check(root, fallback, latest_tag=tag)
                return fallback
            return _install_release(root, release)
        except TunnelRuntimeError as exc:
            _record_failed_check(root, str(exc))
            if strict or fallback is None:
                raise
            return TunnelRuntimeSelection(
                path=fallback.path,
                version=fallback.version,
                source=fallback.source,
                platform_key=fallback.platform_key,
                warning=str(exc),
            )


def profile_directory() -> Path:
    """Return the upstream tunnel-client profile directory using its real precedence."""
    configured_dir = os.getenv("TUNNEL_CLIENT_PROFILE_DIR", "").strip()
    if configured_dir:
        return Path(configured_dir).expanduser()

    xdg = os.getenv("XDG_CONFIG_HOME", "").strip()
    home = os.getenv("HOME", "").strip()
    if xdg:
        return Path(xdg).expanduser() / "tunnel-client"
    if home:
        return Path(home).expanduser() / ".config" / "tunnel-client"
    if platform.system().casefold() == "windows":
        appdata = os.getenv("APPDATA", "").strip()
        return (Path(appdata) if appdata else Path.home() / "AppData" / "Roaming") / "tunnel-client"
    return Path.home() / ".config" / "tunnel-client"


def detect_profile() -> str:
    explicit = os.getenv("MCP_TUNNEL_PROFILE", "").strip()
    if explicit:
        return explicit
    upstream_profile = os.getenv("TUNNEL_CLIENT_PROFILE", "").strip()
    if upstream_profile:
        return upstream_profile

    profile_file = os.getenv("TUNNEL_CLIENT_PROFILE_FILE", "").strip()
    if profile_file:
        stem = Path(profile_file).stem.strip()
        if stem:
            return stem

    profile_dir = profile_directory()
    try:
        candidates = sorted({
            path.stem
            for path in profile_dir.glob("*.yaml")
            if path.is_file() and path.stem
        })
    except OSError:
        candidates = []
    return candidates[0] if candidates else "default"


def profile_run_args(profile: str | None) -> list[str]:
    """Preserve upstream config/profile-file precedence when building runtime CLI args."""
    if os.getenv("TUNNEL_CLIENT_CONFIG", "").strip() or os.getenv("TUNNEL_CLIENT_PROFILE_FILE", "").strip():
        return []
    selected = (profile or "").strip()
    return ["--profile", selected] if selected else []


def _selection_payload(selection: TunnelRuntimeSelection) -> dict[str, Any]:
    return {
        "ok": True,
        "path": str(selection.path),
        "version": selection.version,
        "source": selection.source,
        "platform": selection.platform_key,
        "warning": selection.warning,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subcommands = parser.add_subparsers(dest="command", required=True)

    ensure_parser = subcommands.add_parser("ensure", help="Resolve and, when due, securely update the tunnel runtime.")
    ensure_parser.add_argument("--print-path", action="store_true", help="Print only the selected binary path.")
    ensure_parser.add_argument("--json", action="store_true", help="Print structured selection metadata.")

    current_parser = subcommands.add_parser("current", help="Resolve the best installed tunnel runtime without network access.")
    current_parser.add_argument("--json", action="store_true")

    subcommands.add_parser("profile", help="Print the selected tunnel profile name.")

    args = parser.parse_args()
    try:
        if args.command == "profile":
            print(detect_profile())
            return 0
        selection = ensure_runtime() if args.command == "ensure" else current_runtime()
        if selection.warning:
            print(f"WARN tunnel runtime update: {selection.warning}", file=sys.stderr)
        if getattr(args, "print_path", False):
            print(selection.path)
        elif getattr(args, "json", False):
            print(json.dumps(_selection_payload(selection), separators=(",", ":")))
        else:
            print(f"{selection.path} v{selection.version} ({selection.source})")
        return 0
    except TunnelRuntimeError as exc:
        print(f"ERROR tunnel runtime: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
