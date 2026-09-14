"""Heartbeat from the actual MCP event loop, only when launched by the supervisor."""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import Any

from core.config import SETTINGS
from core.lifecycle import RUNTIME_LIFECYCLE
from core.recovery import OPERATION_RECOVERY


@asynccontextmanager
async def lifespan(server: Any) -> AsyncIterator[dict[str, Any]]:
    async def pulse(path: Path) -> None:
        path.write_text(str(os.getpid()), encoding="ascii")
        while True:
            os.utime(path, None)
            await asyncio.sleep(2)

    async def watch_lifecycle() -> None:
        while True:
            RUNTIME_LIFECYCLE.poll_control()
            await asyncio.sleep(0.05)

    RUNTIME_LIFECYCLE.configure_from_env()
    OPERATION_RECOVERY.configure_from_env(SETTINGS.state_dir)
    value = os.environ.get("MCP_HEARTBEAT_FILE")
    task = asyncio.create_task(pulse(Path(value))) if value else None
    lifecycle_task = asyncio.create_task(watch_lifecycle())
    try:
        yield {}
    finally:
        RUNTIME_LIFECYCLE.mark_stopping()
        lifecycle_task.cancel()
        with suppress(asyncio.CancelledError):
            await lifecycle_task
        if task:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        # Preserve the published STOPPING acknowledgement on disk while keeping
        # repeated in-process MCP server instances isolated from the old lifecycle.
        OPERATION_RECOVERY.configure(None, None)
        RUNTIME_LIFECYCLE.configure(None, None)
