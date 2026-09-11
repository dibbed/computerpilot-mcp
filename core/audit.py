"""Append-only audit trail for mutating or potentially destructive tools."""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.config import SETTINGS, ensure_runtime_dirs

_LOCK = threading.Lock()


def audit_action(
    operation: str,
    *,
    target: str | Path | None = None,
    details: dict[str, Any] | None = None,
    outcome: str = "attempted",
) -> None:
    """Record metadata only; never store file contents, secrets, or typed text."""

    ensure_runtime_dirs()
    record = {
        "time": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
        "operation": operation,
        "outcome": outcome,
        "pid": os.getpid(),
    }
    if target is not None:
        record["target"] = str(target)
    if details:
        record["details"] = details
    line = json.dumps(record, ensure_ascii=False, separators=(",", ":"))
    with _LOCK, SETTINGS.audit_log.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(line + "\n")
