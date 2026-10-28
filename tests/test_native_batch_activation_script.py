"""Smoke checks for the Windows-only opt-in Batch activation runbook."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "activate_native_read_batch.ps1"


def test_activation_keeps_workspace_and_resource_capability_boundaries() -> None:
    body = SCRIPT.read_text(encoding="utf-8")
    assert "Join-Path $PSScriptRoot '..'" in body
    assert "Remove-Item Env:COMPUTERPILOT_INTERNAL_RESOURCE_CAPABILITY" in body
    assert "active_mutations" in body
    assert "workflow_resource_requests" in body
    assert "RequestStop" in body
    assert "runtime-settings.original.bin" in body
    assert "WriteAllBytes($settings, $original)" in body
    assert "native_read_batch" in body


@pytest.mark.skipif(os.name != "nt", reason="PowerShell activation is Windows-only")
def test_activation_powershell_syntax() -> None:
    command = (
        "$tokens=$null; $parseErrors=$null; "
        "[void][System.Management.Automation.Language.Parser]::ParseFile("
        f"'{str(SCRIPT).replace(chr(39), chr(39) * 2)}',"
        "[ref]$tokens,[ref]$parseErrors); "
        "if($parseErrors.Count){ $parseErrors | ForEach-Object { Write-Error $_.Message }; exit 1 }"
    )
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command],
        cwd=ROOT, text=True, capture_output=True, timeout=25, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
