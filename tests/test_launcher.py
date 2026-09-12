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
@pytest.mark.parametrize("cached,doctor_exit", [(True, 0), (False, 0), (False, 9)])
def test_bootstrap_fast_and_full_paths(tmp_path: Path, cached: bool, doctor_exit: int) -> None:
    project = tmp_path / "bootstrap project"
    scripts = project / "scripts"
    scripts.mkdir(parents=True)
    shutil.copyfile(Path(__file__).resolve().parents[1] / "scripts/bootstrap.ps1", scripts / "bootstrap.ps1")
    subprocess.run([sys.executable, "-m", "venv", "--without-pip", "--system-site-packages", str(project / ".venv")],
                   check=True, capture_output=True, timeout=40)
    (project / "requirements.txt").write_text("")
    (project / "requirements-browser.txt").write_text("")
    # A local pip substitute records installation without touching the test environment.
    (project / "pip.py").write_text("from pathlib import Path\nPath('pip-called').touch()\n")
    (scripts / "doctor.py").write_text(
        "import sys\nfrom pathlib import Path\n"
        "with Path('doctor-calls').open('a') as f: f.write(' '.join(sys.argv[1:])+'\\n')\n"
        f"raise SystemExit(({0 if cached else 2}) if '--check' in sys.argv else {doctor_exit})\n"
    )
    environment = {**os.environ, "MCP_START_MODE": "local-http"}
    result = subprocess.run(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(scripts / "bootstrap.ps1")],
                            cwd=project, env=environment, capture_output=True, text=True, timeout=60)
    assert result.returncode == (15 if doctor_exit else 0), result.stdout + result.stderr
    calls = (project / "doctor-calls").read_text().splitlines()
    assert len(calls) == (1 if cached else 2)
    assert "--check" in calls[0]
    assert (project / "pip-called").exists() is not cached
