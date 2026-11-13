"""Portable post-venv bootstrap for validation, dependencies, tunnel setup, and Supervisor launch."""

from __future__ import annotations

import argparse
import getpass
import hashlib
import importlib.util
import os
import re
import subprocess
import sys
import sysconfig
import tempfile
from pathlib import Path

from core.file_lock import exclusive_file_lock
from scripts.tunnel_runtime import TunnelRuntimeError, clean_control_plane_key, detect_profile, ensure_runtime

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
    common = (
        "mcp",
        "anyio",
        "pydantic",
        "psutil",
        "charset_normalizer",
        "PIL",
        "openpyxl",
        "docx",
        "pypdf",
        "reportlab",
        "pytest",
        "ruff",
        "mypy",
    )
    return (*common, "uiautomation") if os.name == "nt" else common


def imports_healthy() -> bool:
    """Probe in a fresh interpreter so installed modules are never held open during repairs."""
    try:
        result = subprocess.run(
            [sys.executable, "-c", f"import {','.join(_required_imports())}"],
            cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def _run(command: list[str], *, timeout: float | None = None) -> subprocess.CompletedProcess[str]:
    if timeout is None:
        return subprocess.run(command, cwd=ROOT, text=True, check=False)
    try:
        from core.process_ownership import spawn_owned_process

        process = spawn_owned_process(
            command, cwd=ROOT, env=None, stdin=None, stdout=None, stderr=None, creationflags=0, require_native=True,
        )
        try:
            return subprocess.CompletedProcess(command, process.wait(timeout=timeout))
        finally:
            process.close_ownership()
    except Exception as exc:
        raise BootstrapError(f"Startup Doctor: {type(exc).__name__}; watchdog budget {timeout:g}s.", 15) from None


def _mode() -> str:
    mode = os.getenv("MCP_START_MODE", "tunnel").strip().casefold()
    if mode not in {"tunnel", "local-http"}:
        raise BootstrapError(f"Unsupported MCP_START_MODE: {mode!r}", 16)
    return mode


def _prompt_control_plane_key(secret: Path) -> str:
    """Ask only on interactive first-run setup; never echo or log the key."""
    if not sys.stdin.isatty():
        raise BootstrapError(
            "CONTROL_PLANE_API_KEY is missing. Run START_MCP.bat interactively "
            "or provide the key via the environment / .secrets/control_plane_api_key.txt.",
            17,
        )

    print("\n[Setup] Secure Tunnel API key is missing.")
    print(f"[Setup] Enter the key once; it will be saved to: {secret}")
    try:
        value = clean_control_plane_key(getpass.getpass("CONTROL_PLANE_API_KEY (hidden): "))
    except (EOFError, KeyboardInterrupt):
        raise BootstrapError("Secure Tunnel key setup cancelled.", 17) from None
    if not value:
        raise BootstrapError("Empty CONTROL_PLANE_API_KEY; nothing was saved.", 17)
    return value


def _save_control_plane_key(secret: Path, value: str, *, expected_empty_bytes: bytes | None) -> None:
    """Create the secret privately, without overwriting an existing nonempty key."""
    try:
        secret.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if expected_empty_bytes is not None:
            if secret.read_bytes() != expected_empty_bytes:
                raise BootstrapError("The key file changed during setup; restart to read it.", 17)
            secret.write_bytes(value.encode("utf-8"))
        else:
            fd = os.open(secret, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as handle:
                handle.write(value.encode("utf-8"))
    except FileExistsError:
        raise BootstrapError("The key file appeared during setup; restart to read it.", 17) from None
    except OSError:
        raise BootstrapError("Could not save the key under .secrets; check folder permissions.", 17) from None


def _load_control_plane_key() -> None:
    existing = clean_control_plane_key(os.getenv("CONTROL_PLANE_API_KEY", ""))
    if existing:
        os.environ["CONTROL_PLANE_API_KEY"] = existing
        return

    secret = ROOT / ".secrets" / "control_plane_api_key.txt"
    expected_empty_bytes: bytes | None = None
    if secret.is_file():
        try:
            raw = secret.read_bytes()
        except OSError:
            raise BootstrapError("Cannot read the existing Control Plane key file.", 17) from None
        try:
            value = clean_control_plane_key(raw)
        except ValueError:
            value = ""
        if value:
            os.environ["CONTROL_PLANE_API_KEY"] = value
            try:
                if raw != value.encode("utf-8"):
                    secret.write_bytes(value.encode("utf-8"))
            except OSError:
                pass
            return
        expected_empty_bytes = raw
    elif secret.exists():
        raise BootstrapError("The Control Plane key path exists but is not a file.", 17)

    value = _prompt_control_plane_key(secret)
    _save_control_plane_key(secret, value, expected_empty_bytes=expected_empty_bytes)
    os.environ["CONTROL_PLANE_API_KEY"] = value
    print("[Setup] Secure Tunnel key saved. Future starts will reuse it.")


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
    if not imports_healthy():
        return False
    result = _run([sys.executable, "-m", "scripts.doctor", "--check", "--mode", mode, "--profile", profile], timeout=30)
    return result.returncode == 0


def _requirements_path() -> Path:
    browser = importlib.util.find_spec("playwright") is not None
    return ROOT / ("requirements-browser.txt" if browser else "requirements.txt")


def _repair_dependencies() -> bool:
    venv = ROOT.resolve() / ".venv"
    if Path(sys.prefix).resolve() != venv or not (venv / "pyvenv.cfg").is_file():
        return False

    paths = sysconfig.get_paths()
    sites = sorted({Path(paths[key]).resolve() for key in ("purelib", "platlib")})
    leftovers: list[Path] = []
    wheels: dict[str, list[tuple[str, Path]]] = {}
    for site in sites:
        if not site.is_relative_to(venv):
            raise BootstrapError("Virtual environment site-packages is outside the project .venv.", 13)
        leftovers.extend(site.glob("~*"))
        for metadata in sorted(site.glob("*.dist-info")):
            match = re.fullmatch(r"([a-zA-Z0-9_]+)-([a-zA-Z0-9.!+_]+)\.dist-info", metadata.name)
            if match:
                name, version = match.groups()
                wheels.setdefault(name.lower(), []).append((f"{name}=={version}", metadata))

    damaged = []
    for installed in wheels.values():
        for requirement, metadata in installed:
            if (metadata / "METADATA").is_file() and (metadata / "RECORD").is_file():
                continue
            if len(installed) != 1 or metadata.resolve() != metadata:
                raise BootstrapError(f"Cannot safely repair ambiguous package metadata for {requirement}.", 13)
            damaged.append((requirement, metadata))

    if leftovers:
        # Keep interrupted uninstall files available for manual recovery.
        quarantine = Path(tempfile.mkdtemp(prefix="repair-quarantine-", dir=venv))
        print(f"[3/5] Moving interrupted pip files to .venv/{quarantine.name}", flush=True)
        for path in leftovers:
            target = quarantine / path.relative_to(venv)
            target.parent.mkdir(parents=True, exist_ok=True)
            path.rename(target)

    if damaged:
        requirements = [requirement for requirement, _ in damaged]
        print(f"[3/5] Repairing incomplete Python packages: {', '.join(requirements)}", flush=True)
        result = _run([
            sys.executable, "-m", "pip", "--require-virtualenv", "install", "--quiet",
            "--disable-pip-version-check", "--ignore-installed", "--no-deps", "--only-binary=:all:", *requirements,
        ])
        if result.returncode or any(
            not ((metadata / "METADATA").is_file() and (metadata / "RECORD").is_file()) for _, metadata in damaged
        ):
            raise BootstrapError("Could not reinstall incomplete Python packages.", 13)
    return bool(damaged or leftovers)


def _ensure_dependencies(*, refresh: bool = False) -> None:
    requirements = _requirements_path()
    marker = Path(sys.prefix) / ".requirements.sha256"
    expected = requirements_fingerprint(requirements)
    try:
        installed = marker.read_text(encoding="ascii").strip()
    except OSError:
        installed = ""

    healthy = imports_healthy()
    if installed == expected and healthy and not refresh:
        print("[3/5] Dependencies are current.")
        return

    print("[3/5] Installing or refreshing dependencies...")
    cmd = [
        sys.executable,
        "-m",
        "pip",
        "install",
        "--quiet",
        "--disable-pip-version-check",
    ]
    if installed == expected and not healthy:
        cmd.append("--force-reinstall")
    cmd.extend(["--requirement", str(requirements)])

    result = _run(cmd)
    if result.returncode:
        raise BootstrapError("Dependency installation failed.", 13)
    if not imports_healthy():
        raise BootstrapError("Dependency installation completed but required imports are unavailable.", 13)
    marker.write_text(expected, encoding="ascii")


def _full_doctor(mode: str, profile: str) -> None:
    result = _run([sys.executable, "-m", "scripts.doctor", "--mode", mode, "--profile", profile], timeout=900)
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

    try:
        with exclusive_file_lock(ROOT / ".agent_state/startup-validation.lock", timeout_sec=900, label="startup validation"):
            repaired = _repair_dependencies()
            if not repaired and _doctor_check(mode, profile):
                print("[3/5] Successful startup fingerprint unchanged.")
                print("[4/5] Fast checks complete.")
                return mode, profile

            _ensure_dependencies(refresh=repaired)
            print("[4/5] Running full startup doctor...")
            _full_doctor(mode, profile)
    except OSError as exc:
        raise BootstrapError(f"Startup validation could not access local files ({type(exc).__name__}).", 13) from None
    return mode, profile


def _supervisor_args(mode: str, profile: str) -> list[str]:
    args = ["--mode", mode]
    if mode == "tunnel":
        args.extend(["--profile", profile])
    return args


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
        from scripts.supervisor import main as supervisor_main

        return supervisor_main(_supervisor_args(mode, profile))
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
