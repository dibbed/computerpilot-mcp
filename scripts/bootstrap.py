"""Portable post-venv bootstrap for validation, dependencies, tunnel setup, and Supervisor launch."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import os
import subprocess
import sys
from pathlib import Path

from scripts.tunnel_runtime import TunnelRuntimeError, detect_profile, ensure_runtime

ROOT = Path(__file__).resolve().parents[1]
MIN_PYTHON = (3, 10)


class BootstrapError(RuntimeError):
    def __init__(self, message: str, code: int = 1) -> None:
        super().__init__(message)
        self.code = code


def _requirements_files(path: Path, *, seen: set[Path] | None = None) -> list[Path]:
    """Resolve nested -r includes so dependency fingerprints cannot go stale."""
    resolved = path.resolve()
    visited = seen if seen is not None else set()
    if resolved in visited:
        return []
    visited.add(resolved)
    files = [resolved]
    try:
        lines = resolved.read_text(encoding="utf-8").splitlines()
    except OSError:
        return files
    for line in lines:
        stripped = line.strip()
        if stripped.startswith(("-r ", "--requirement ")):
            _, include = stripped.split(maxsplit=1)
            files.extend(_requirements_files(resolved.parent / include, seen=visited))
    return files


def requirements_fingerprint(path: Path) -> str:
    digest = hashlib.sha256()
    for item in sorted(_requirements_files(path), key=str):
        digest.update(str(item).encode())
        if item.is_file():
            digest.update(item.read_bytes())
        else:
            digest.update(b"<missing>")
    return digest.hexdigest().upper()


def _required_imports() -> tuple[str, ...]:
    common = ("mcp", "pydantic", "psutil", "charset_normalizer", "PIL", "pytest", "ruff", "mypy")
    return (*common, "uiautomation") if os.name == "nt" else common


def imports_healthy() -> bool:
    return all(importlib.util.find_spec(name) is not None for name in _required_imports())


def _run(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, cwd=ROOT, text=True, check=False)


def _mode() -> str:
    mode = os.getenv("MCP_START_MODE", "tunnel").strip().casefold()
    if mode not in {"tunnel", "local-http"}:
        raise BootstrapError(f"Unsupported MCP_START_MODE: {mode!r}", 16)
    return mode


def _load_control_plane_key() -> None:
    if os.getenv("CONTROL_PLANE_API_KEY", "").strip():
        return
    secret = ROOT / ".secrets" / "control_plane_api_key.txt"
    try:
        value = secret.read_text(encoding="utf-8").strip()
    except OSError:
        value = ""
    if not value:
        raise BootstrapError(
            "CONTROL_PLANE_API_KEY is unavailable and .secrets/control_plane_api_key.txt was not found.",
            17,
        )
    os.environ["CONTROL_PLANE_API_KEY"] = value


def _resolve_tunnel() -> str:
    try:
        selection = ensure_runtime(ROOT)
    except TunnelRuntimeError as exc:
        raise BootstrapError(f"No verified tunnel-client runtime is available: {exc}", 16) from exc
    os.environ["MCP_TUNNEL_CLIENT_BIN"] = str(selection.path)
    if selection.warning:
        print(f"WARN tunnel runtime update: {selection.warning}", file=sys.stderr)
    profile = detect_profile().strip() or "default"
    _load_control_plane_key()
    print(f"INFO tunnel runtime v{selection.version} ({selection.source})")
    print(f"INFO tunnel profile {profile}")
    return profile


def _doctor_check(mode: str, profile: str) -> bool:
    result = _run([sys.executable, "-m", "scripts.doctor", "--check", "--mode", mode, "--profile", profile])
    return result.returncode == 0


def _requirements_path() -> Path:
    browser = importlib.util.find_spec("playwright") is not None
    return ROOT / ("requirements-browser.txt" if browser else "requirements.txt")


def _ensure_dependencies() -> None:
    requirements = _requirements_path()
    marker = Path(sys.prefix) / ".requirements.sha256"
    expected = requirements_fingerprint(requirements)
    try:
        installed = marker.read_text(encoding="ascii").strip()
    except OSError:
        installed = ""

    if installed == expected and imports_healthy():
        print("[3/5] Dependencies are current.")
        return

    print("[3/5] Installing or refreshing dependencies...")
    result = _run([
        sys.executable,
        "-m",
        "pip",
        "install",
        "--quiet",
        "--disable-pip-version-check",
        "--requirement",
        str(requirements),
    ])
    if result.returncode:
        raise BootstrapError("Dependency installation failed.", 13)
    if not imports_healthy():
        raise BootstrapError("Dependency installation completed but required imports are unavailable.", 13)
    marker.write_text(expected, encoding="ascii")


def _full_doctor(mode: str, profile: str) -> None:
    result = _run([sys.executable, "-m", "scripts.doctor", "--mode", mode, "--profile", profile])
    if result.returncode:
        raise BootstrapError("Full doctor failed; run python -m scripts.doctor for diagnosis.", 15)


def validate() -> tuple[str, str]:
    if sys.version_info < MIN_PYTHON:
        raise BootstrapError("Python 3.10 or newer is required.", 12)

    print(f"[2/5] Python {sys.version.split()[0]}")
    mode = _mode()
    profile = "default"
    if mode == "tunnel":
        print("INFO Resolving Secure MCP Tunnel runtime...")
        profile = _resolve_tunnel()

    if _doctor_check(mode, profile):
        print("[3/5] Successful startup fingerprint unchanged.")
        print("[4/5] Fast checks complete.")
        return mode, profile

    _ensure_dependencies()
    print("[4/5] Running full startup doctor...")
    _full_doctor(mode, profile)
    return mode, profile


def _supervisor_command(mode: str, profile: str) -> list[str]:
    command = [sys.executable, "-m", "scripts.supervisor", "--mode", mode]
    if mode == "tunnel":
        command.extend(["--profile", profile])
    return command


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", action="store_true", help="Start the Supervisor after validation.")
    args = parser.parse_args(argv)

    try:
        mode, profile = validate()
    except BootstrapError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return exc.code

    if not args.start:
        print("[5/5] Validation complete.")
        return 0

    print("[5/5] Starting MCP supervisor...")
    try:
        return subprocess.call(_supervisor_command(mode, profile), cwd=ROOT)
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
