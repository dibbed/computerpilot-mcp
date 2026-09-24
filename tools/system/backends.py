"""Platform-specific installed-software and service discovery backends."""

from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
from pathlib import Path
from typing import Any


def _run(command: list[str], *, timeout: float = 30.0) -> subprocess.CompletedProcess[str] | None:
    try:
        return subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            errors="replace",
        )
    except (OSError, subprocess.SubprocessError):
        return None


def _windows_installed_software() -> list[dict[str, Any]]:
    if os.name != "nt":
        return []
    import winreg

    locations = [
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall", winreg.KEY_WOW64_64KEY),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall", winreg.KEY_WOW64_32KEY),
        (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall", 0),
    ]
    rows: list[dict[str, Any]] = []
    seen: set[tuple[str, str | None, str | None]] = set()
    for hive, key_path, view in locations:
        try:
            root = winreg.OpenKey(hive, key_path, 0, winreg.KEY_READ | view)
        except OSError:
            continue
        with root:
            count = winreg.QueryInfoKey(root)[0]
            for index in range(count):
                try:
                    child = winreg.OpenKey(root, winreg.EnumKey(root, index))
                except OSError:
                    continue
                with child:
                    values: dict[str, Any] = {}
                    for name in ("DisplayName", "DisplayVersion", "Publisher", "InstallDate"):
                        try:
                            values[name] = winreg.QueryValueEx(child, name)[0]
                        except OSError:
                            values[name] = None
                display_name = values["DisplayName"]
                if not display_name:
                    continue
                identity = (str(display_name), values["DisplayVersion"], values["Publisher"])
                if identity in seen:
                    continue
                seen.add(identity)
                rows.append(
                    {
                        "name": str(display_name),
                        "version": values["DisplayVersion"],
                        "publisher": values["Publisher"],
                        "install_date": values["InstallDate"],
                        "source": "windows_registry",
                    }
                )
    return rows


def _linux_installed_software() -> tuple[str, list[dict[str, Any]]]:
    dpkg = shutil.which("dpkg-query")
    if dpkg:
        result = _run([dpkg, "-W", "-f=${binary:Package}\t${Version}\t${Maintainer}\n"])
        if result is not None and result.returncode == 0:
            rows = []
            for line in result.stdout.splitlines():
                parts = line.split("\t", 2)
                if not parts or not parts[0]:
                    continue
                rows.append(
                    {
                        "name": parts[0],
                        "version": parts[1] if len(parts) > 1 else None,
                        "publisher": parts[2] if len(parts) > 2 else None,
                        "install_date": None,
                        "source": "dpkg",
                    }
                )
            return "dpkg", rows

    rpm = shutil.which("rpm")
    if rpm:
        result = _run([rpm, "-qa", "--qf", "%{NAME}\t%{VERSION}-%{RELEASE}\t%{VENDOR}\n"])
        if result is not None and result.returncode == 0:
            rows = []
            for line in result.stdout.splitlines():
                parts = line.split("\t", 2)
                if not parts or not parts[0]:
                    continue
                rows.append(
                    {
                        "name": parts[0],
                        "version": parts[1] if len(parts) > 1 else None,
                        "publisher": parts[2] if len(parts) > 2 else None,
                        "install_date": None,
                        "source": "rpm",
                    }
                )
            return "rpm", rows

    pacman = shutil.which("pacman")
    if pacman:
        result = _run([pacman, "-Q"])
        if result is not None and result.returncode == 0:
            rows = []
            for line in result.stdout.splitlines():
                name, _, version = line.partition(" ")
                if name:
                    rows.append(
                        {
                            "name": name,
                            "version": version or None,
                            "publisher": None,
                            "install_date": None,
                            "source": "pacman",
                        }
                    )
            return "pacman", rows

    return "unavailable", []


def _macos_installed_software() -> tuple[str, list[dict[str, Any]]]:
    profiler = shutil.which("system_profiler")
    if profiler:
        result = _run([profiler, "SPApplicationsDataType", "-json"], timeout=60)
        if result is not None and result.returncode == 0:
            try:
                payload = json.loads(result.stdout)
            except ValueError:
                payload = {}
            items = payload.get("SPApplicationsDataType", []) if isinstance(payload, dict) else []
            rows = []
            if isinstance(items, list):
                for item in items:
                    if not isinstance(item, dict):
                        continue
                    name = str(item.get("_name") or "").strip()
                    if not name:
                        continue
                    rows.append(
                        {
                            "name": name,
                            "version": item.get("version"),
                            "publisher": item.get("obtained_from"),
                            "install_date": item.get("lastModified"),
                            "path": item.get("path"),
                            "source": "system_profiler",
                        }
                    )
            return "system_profiler", rows

    rows = []
    seen: set[str] = set()
    for directory in (Path("/Applications"), Path.home() / "Applications"):
        try:
            apps = list(directory.glob("*.app"))
        except OSError:
            continue
        for app in apps:
            name = app.stem
            if name in seen:
                continue
            seen.add(name)
            rows.append(
                {
                    "name": name,
                    "version": None,
                    "publisher": None,
                    "install_date": None,
                    "path": str(app),
                    "source": "applications_directory",
                }
            )
    return ("applications_directory" if rows else "unavailable"), rows


def installed_software_rows() -> tuple[str, list[dict[str, Any]]]:
    system = platform.system().casefold()
    if system == "windows":
        return "windows_registry", _windows_installed_software()
    if system == "linux":
        return _linux_installed_software()
    if system == "darwin":
        return _macos_installed_software()
    return "unavailable", []


def _windows_services() -> list[dict[str, Any]]:
    import psutil

    rows = []
    for service in psutil.win_service_iter():
        try:
            data = service.as_dict()
        except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
            continue
        rows.append(
            {
                "name": data.get("name"),
                "display_name": data.get("display_name"),
                "status": data.get("status"),
                "start_type": data.get("start_type"),
                "pid": data.get("pid"),
                "source": "windows_service_manager",
            }
        )
    return rows


def _linux_services() -> tuple[str, list[dict[str, Any]]]:
    systemctl = shutil.which("systemctl")
    if systemctl:
        result = _run([
            systemctl,
            "list-units",
            "--type=service",
            "--all",
            "--no-legend",
            "--no-pager",
            "--plain",
        ])
        if result is not None and result.returncode == 0:
            rows = []
            for line in result.stdout.splitlines():
                parts = line.split(None, 4)
                if len(parts) < 4:
                    continue
                name, load, active, sub = parts[:4]
                description = parts[4] if len(parts) > 4 else ""
                rows.append(
                    {
                        "name": name,
                        "display_name": description,
                        "status": sub if active == "active" else active,
                        "active_state": active,
                        "load_state": load,
                        "pid": None,
                        "source": "systemd",
                    }
                )
            return "systemd", rows

    init_dir = Path("/etc/init.d")
    if init_dir.is_dir():
        try:
            entries = sorted(path for path in init_dir.iterdir() if path.is_file())
        except OSError:
            entries = []
        return "sysv_init", [
            {
                "name": path.name,
                "display_name": path.name,
                "status": "unknown",
                "pid": None,
                "source": "sysv_init",
            }
            for path in entries
        ]
    return "unavailable", []


def _macos_services() -> tuple[str, list[dict[str, Any]]]:
    launchctl = shutil.which("launchctl")
    if launchctl is None:
        return "unavailable", []
    result = _run([launchctl, "list"])
    if result is None or result.returncode != 0:
        return "unavailable", []
    rows = []
    for line in result.stdout.splitlines():
        if not line.strip() or line.lstrip().startswith("PID"):
            continue
        parts = line.split("\t")
        if len(parts) < 3:
            parts = line.split(None, 2)
        if len(parts) < 3:
            continue
        pid_raw, status_raw, label = parts[:3]
        try:
            pid = int(pid_raw)
        except ValueError:
            pid = None
        rows.append(
            {
                "name": label,
                "display_name": label,
                "status": "running" if pid is not None else ("stopped" if status_raw != "0" else "loaded"),
                "exit_status": status_raw,
                "pid": pid,
                "source": "launchd",
            }
        )
    return "launchd", rows


def service_rows() -> tuple[str, list[dict[str, Any]]]:
    system = platform.system().casefold()
    if system == "windows":
        return "windows_service_manager", _windows_services()
    if system == "linux":
        return _linux_services()
    if system == "darwin":
        return _macos_services()
    return "unavailable", []
