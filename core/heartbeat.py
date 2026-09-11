"""Heartbeat from the actual MCP event loop, only when launched by the supervisor."""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import Any


@asynccontextmanager
async def lifespan(server: Any) -> AsyncIterator[dict[str, Any]]:
    async def pulse(path: Path) -> None:
        path.write_text(str(os.getpid()), encoding="ascii")
        while True:
            os.utime(path, None)
            await asyncio.sleep(2)

    value = os.environ.get("MCP_HEARTBEAT_FILE")
    task = asyncio.create_task(pulse(Path(value))) if value else None
    try:
        yield {}
    finally:
        if task:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
