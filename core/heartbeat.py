"""Heartbeat from the actual MCP event loop, only when launched by the supervisor."""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import Any

import anyio

from core.artifact_retention import schedule_artifact_retention
from core.audit import flush_audit
from core.backups import BACKUP_RETENTION
from core.config import SETTINGS, ensure_runtime_dirs
from core.health_snapshot import bound_runtime_health, collect_server_health
from core.job_retention import schedule_job_history_retention
from core.job_scheduler import ensure_job_scheduler
from core.lifecycle import RUNTIME_LIFECYCLE
from core.recovery import OPERATION_RECOVERY
from core.tool_activity import TOOL_ACTIVITY
from core.workflow_retention import schedule_workflow_history_retention
from tools.filesystem.search_snapshots import SEARCH_SNAPSHOTS

RUNTIME_HEALTH_PUBLISH_INTERVAL_SEC = 10.0


@asynccontextmanager
async def lifespan(server: Any) -> AsyncIterator[dict[str, Any]]:
    ensure_runtime_dirs()
    batch_reader = getattr(server, "native_read_batch_state", None)
    if batch_reader is not None:
        batch_reader.start()
    store_getter = getattr(server, "job_store_getter", None)
    if store_getter is not None:
        store = await asyncio.to_thread(store_getter)
        await asyncio.to_thread(ensure_job_scheduler, store)

    async def pulse(path: Path) -> None:
        path.write_text(str(os.getpid()), encoding="ascii")
        while True:
            os.utime(path, None)
            await asyncio.sleep(2)

    async def publish_health(path: Path) -> None:
        while True:
            try:
                payload = bound_runtime_health(await collect_server_health(server, settings=SETTINGS))
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
                await asyncio.to_thread(SEARCH_SNAPSHOTS.cleanup)
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
    if hasattr(server, "__dict__"):
        server.runtime_started = True
    BACKUP_RETENTION.schedule()
    value = os.environ.get("MCP_HEARTBEAT_FILE")
    health_value = os.environ.get("MCP_RUNTIME_HEALTH_FILE")
    task = asyncio.create_task(pulse(Path(value))) if value else None
    health_task = asyncio.create_task(publish_health(Path(health_value))) if health_value else None
    lifecycle_task = (
        asyncio.create_task(watch_lifecycle())
        if RUNTIME_LIFECYCLE.snapshot()["managed"] else None
    )
    maintenance_task = asyncio.create_task(maintain_state())
    extension_manager: Any = getattr(server, "extension_manager", None)
    config_owned_manager = False
    if hasattr(server, "__dict__"):
        server.extension_config_error_code = None
    if extension_manager is None and SETTINGS.extensions_enabled:
        from core.extensions.config import load_extension_config
        from core.extensions.manager import ExtensionLifecycleManager

        config_res = load_extension_config(settings=SETTINGS)
        if hasattr(server, "__dict__"):
            server.extension_config_error_code = config_res.error_code
        if config_res.config.extensions:
            config_owned_manager = True
            extension_manager = ExtensionLifecycleManager(config=config_res.config)
            if hasattr(server, "__dict__"):
                server.extension_manager = extension_manager

    extension_task = (
        asyncio.create_task(extension_manager.start_all_async())
        if extension_manager is not None
        else None
    )

    try:
        yield {}
    finally:
        if hasattr(server, "__dict__"):
            server.runtime_started = False
        RUNTIME_LIFECYCLE.mark_stopping()
        if batch_reader is not None:
            batch_reader.stop()
        lsp_manager = getattr(server, "lsp_manager", None)
        if lsp_manager is not None:
            # Seal admission before a busy worker pool can delay bounded draining.
            with suppress(Exception):
                lsp_manager.close_all(timeout_sec=0)

        async def finish_lifespan() -> None:
            if batch_reader is not None:
                with suppress(Exception):
                    await asyncio.wait_for(asyncio.to_thread(batch_reader.close), timeout=5.25)
            if lsp_manager is not None:
                with suppress(Exception):
                    await asyncio.wait_for(asyncio.to_thread(lsp_manager.close_all, timeout_sec=5.0), timeout=5.25)
            if extension_manager is not None:
                try:
                    await extension_manager.drain_and_stop(timeout_sec=5.0)
                except Exception:
                    pass
            if extension_task:
                extension_task.cancel()
                with suppress(asyncio.CancelledError):
                    await extension_task
            if lifecycle_task:
                lifecycle_task.cancel()
            maintenance_task.cancel()
            if lifecycle_task:
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
            if config_owned_manager and hasattr(server, "__dict__"):
                # A reused server must validate current configuration on its next startup.
                server.extension_manager = None

        cleanup = asyncio.create_task(finish_lifespan())
        cancelled = False
        with anyio.CancelScope(shield=True):
            while True:
                try:
                    await asyncio.shield(cleanup)
                    break
                except asyncio.CancelledError:
                    if cleanup.cancelled():
                        raise
                    cancelled = True
        if cancelled:
            raise asyncio.CancelledError
