"""Keep Pyright focused on project source, not unbounded local artifacts."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_pyright_root_config_limits_source_inventory() -> None:
    config_path = PROJECT_ROOT / "pyrightconfig.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    include = set(config["include"])
    exclude = set(config["exclude"])
    assert {"core", "tools", "scripts", "tests", "main.py", "local_pc_mcp.py"} <= include
    assert {"output", "graphify-out", ".worktrees"} <= exclude
    assert not include & exclude


def test_pyright_config_does_not_exclude_first_party_sources() -> None:
    config = json.loads((PROJECT_ROOT / "pyrightconfig.json").read_text(encoding="utf-8"))
    for entry in config["include"]:
        assert (PROJECT_ROOT / entry).exists(), entry
    assert "core" not in config["exclude"]
    assert "tools" not in config["exclude"]


def test_pyright_config_covers_all_tracked_python_paths() -> None:
    """Adding a new first-party source root requires updating the LSP inventory."""
    if not (PROJECT_ROOT / ".git").exists():
        return  # Source distribution without Git metadata.
    result = subprocess.run(
        ["git", "ls-files", "--cached", "--", "*.py", "*.pyi"],
        cwd=PROJECT_ROOT, text=True, capture_output=True, check=True,
    )
    config = json.loads((PROJECT_ROOT / "pyrightconfig.json").read_text(encoding="utf-8"))
    source_roots = set(config["include"])
    missing = [
        path for path in result.stdout.splitlines()
        if path.split("/", 1)[0] not in source_roots
    ]
    assert not missing, f"Python files omitted from Pyright inventory: {missing[:10]}"
