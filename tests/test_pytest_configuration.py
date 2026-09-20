from __future__ import annotations

from pathlib import Path

import tomllib


def test_pytest_basetemp_is_isolated_from_live_agent_state() -> None:
    root = Path(__file__).resolve().parent.parent
    config = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    addopts = str(config["tool"]["pytest"]["ini_options"].get("addopts", ""))
    normalized = addopts.replace("\\", "/")
    assert "--basetemp=.pytest-tmp" in normalized
    assert ".agent_state" not in normalized
