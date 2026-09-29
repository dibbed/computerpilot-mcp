"""Heartbeat from the actual MCP event loop, only when launched by the supervisor."""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import Any

from core.artifact_retention import schedule_artifact_retention
from core.audit import flush_audit
from core.backups import BACKUP_RETENTION
from core.config import SETTINGS
from core.health_snapshot import collect_server_health
from core.job_retention import schedule_job_history_retention
from core.lifecycle import RUNTIME_LIFECYCLE
from core.recovery import OPERATION_RECOVERY
from core.tool_activity import TOOL_ACTIVITY
from core.workflow_retention import schedule_workflow_history_retention
from tools.filesystem.search_snapshots import SEARCH_SNAPSHOTS

RUNTIME_HEALTH_PUBLISH_INTERVAL_SEC = 10.0


@asynccontextmanager
async def lifespan(server: Any) -> AsyncIterator[dict[str, Any]]:
    async def pulse(path: Path) -> None:
        path.write_text(str(os.getpid()), encoding="ascii")
        while True:
            os.utime(path, None)
            await asyncio.sleep(2)

    async def publish_health(path: Path) -> None:
        while True:
            try:
                payload = await collect_server_health(server, settings=SETTINGS)
                temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
                temporary.write_text(
                    json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True),
                    encoding="utf-8",
                )
                os.replace(temporary, path)
            except Exception:
                # Panel observability is best-effort and must never kill the publisher task.
                pass
            await asyncio.sleep(RUNTIME_HEALTH_PUBLISH_INTERVAL_SEC)

    async def watch_lifecycle() -> None:
        while True:
            RUNTIME_LIFECYCLE.poll_control()
            await asyncio.sleep(0.05)

    async def maintain_state() -> None:
        interval = max(1.0, min(float(SETTINGS.job_history_cleanup_interval_sec), 300.0))
        while True:
            try:
                SEARCH_SNAPSHOTS.cleanup()
            except Exception:
                # Expired search snapshots are disposable; maintenance must
                # continue even if their directory is temporarily unavailable.
                pass
            schedule_artifact_retention(settings=SETTINGS)
            schedule_job_history_retention(settings=SETTINGS)
            schedule_workflow_history_retention(settings=SETTINGS)
            await asyncio.sleep(interval)

    RUNTIME_LIFECYCLE.configure_from_env()
    OPERATION_RECOVERY.configure_from_env(SETTINGS.state_dir)
    TOOL_ACTIVITY.configure_from_env()
    BACKUP_RETENTION.schedule()
    value = os.environ.get("MCP_HEARTBEAT_FILE")
    health_value = os.environ.get("MCP_RUNTIME_HEALTH_FILE")
    task = asyncio.create_task(pulse(Path(value))) if value else None
    health_task = asyncio.create_task(publish_health(Path(health_value))) if health_value else None
    lifecycle_task = asyncio.create_task(watch_lifecycle())
    maintenance_task = asyncio.create_task(maintain_state())
    try:
        yield {}
    finally:
        RUNTIME_LIFECYCLE.mark_stopping()
        lifecycle_task.cancel()
        maintenance_task.cancel()
        with suppress(asyncio.CancelledError):
            await lifecycle_task
        with suppress(asyncio.CancelledError):
            await maintenance_task
        if health_task:
            health_task.cancel()
            with suppress(asyncio.CancelledError):
                await health_task
        if task:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        flush_audit()
        # Preserve the published STOPPING acknowledgement on disk while keeping
        # repeated in-process MCP server instances isolated from the old lifecycle.
        OPERATION_RECOVERY.configure(None, None)
        RUNTIME_LIFECYCLE.configure(None, None)
        TOOL_ACTIVITY.configure(None, None)
