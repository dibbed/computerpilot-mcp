"""Run the production entrypoint with disposable test state and operator config."""

from __future__ import annotations

import json
import runpy
import sys
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from core import config  # noqa: E402

workspace = Path(sys.argv[1])
config.SETTINGS = replace(
    config.SETTINGS, state_dir=workspace / "state", extensions_enabled=True,
    extensions_config=str(workspace / "extensions.json"),
)
sys.argv = [str(ROOT / "main.py"), *sys.argv[2:]]
if sys.argv[1:] == ["--import-only"]:
    runpy.run_path(sys.argv[0], run_name="fixture_import")
    print(json.dumps({"imported": True}))
else:
    runpy.run_path(sys.argv[0], run_name="__main__")
