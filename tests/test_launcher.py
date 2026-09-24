"""Exercise the real batch retry loop with an isolated bootstrap substitute."""

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest


@pytest.mark.skipif(os.name != "nt", reason="Windows batch launcher")
@pytest.mark.parametrize("first_exit", [0, 7])
def test_launcher_restarts_then_honors_interrupt(tmp_path: Path, first_exit: int) -> None:
    project = tmp_path / "project with spaces"
    scripts = project / "scripts"
    scripts.mkdir(parents=True)
    shutil.copyfile(Path(__file__).resolve().parents[1] / "START_MCP.bat", project / "START_MCP.bat")
    (scripts / "bootstrap.ps1").write_text(
        "param([switch]$Start)\n"
        "if (-not $Start) { exit 99 }\n"
        "$marker = Join-Path $PSScriptRoot 'attempts.txt'\n"
        "if (Test-Path -LiteralPath $marker) {\n"
        "    Add-Content -LiteralPath $marker -Value 'second'\n"
        "    exit 130\n"
        "}\n"
        "Set-Content -LiteralPath $marker -Value 'first'\n"
        f"exit {first_exit}\n",
        encoding="utf-8",
    )
    started = time.monotonic()
    result = subprocess.run(
        [os.environ.get("COMSPEC", "cmd.exe"), "/d", "/c", "START_MCP.bat"],
        cwd=project,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 130, result.stdout + result.stderr
    assert (scripts / "attempts.txt").read_text().splitlines() == ["first", "second"]
    assert f"MCP exited with code {first_exit}" in result.stdout
    assert time.monotonic() - started >= 5


@pytest.mark.skipif(os.name != "nt", reason="PowerShell bootstrap")
@pytest.mark.parametrize("start,expected_args,exit_code", [
    (False, [], 0),
    (True, ["--start"], 17),
])
def test_powershell_bootstrap_delegates_to_python_module(
    tmp_path: Path, start: bool, expected_args: list[str], exit_code: int,
) -> None:
    project = tmp_path / "bootstrap project"
    scripts = project / "scripts"
    scripts.mkdir(parents=True)
    shutil.copyfile(Path(__file__).resolve().parents[1] / "scripts/bootstrap.ps1", scripts / "bootstrap.ps1")
    subprocess.run(
        [sys.executable, "-m", "venv", "--without-pip", "--system-site-packages", str(project / ".venv")],
        check=True,
        capture_output=True,
        timeout=40,
    )
    (scripts / "bootstrap.py").write_text(
        "import sys\n"
        "from pathlib import Path\n"
        "Path('bootstrap-args').write_text(' '.join(sys.argv[1:]))\n"
        f"raise SystemExit({exit_code})\n",
        encoding="utf-8",
    )
    command = [
        "powershell.exe",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(scripts / "bootstrap.ps1"),
    ]
    if start:
        command.append("-Start")
    result = subprocess.run(command, cwd=project, capture_output=True, text=True, timeout=60)
    assert result.returncode == exit_code, result.stdout + result.stderr
    assert (project / "bootstrap-args").read_text(encoding="utf-8").split() == expected_args
