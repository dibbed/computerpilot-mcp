from __future__ import annotations

from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10 CI
    import tomli as tomllib


def test_pytest_basetemp_is_isolated_from_live_agent_state() -> None:
    root = Path(__file__).resolve().parent.parent
    config = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    pytest_options = config["tool"]["pytest"]["ini_options"]
    addopts = str(pytest_options.get("addopts", ""))
    cache_dir = str(pytest_options.get("cache_dir", ""))
    normalized = addopts.replace("\\", "/")
    normalized_cache = cache_dir.replace("\\", "/")
    assert "--basetemp=.pytest-tmp" in normalized
    assert ".agent_state" not in normalized
    assert normalized_cache == ".pytest_cache"
    assert ".agent_state" not in normalized_cache
