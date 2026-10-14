"""Run queued durable workflows from the configured state database."""

from __future__ import annotations

import argparse
import asyncio
import os
import socket
import time
from collections.abc import Callable
from typing import Any

from core.config import SETTINGS
from core.errors import ToolError
from core.execution_models import ExecutionIntent
from core.execution_router import build_route_planner
from core.platform import detect_capabilities
from core.registry import create_server
from core.workflows import WorkflowExecutor, workflow_store


def build_executor_route_planner() -> Callable[[ExecutionIntent], dict[str, Any]]:
    """Plan step routes against the native catalog this worker registers.

    Constructing and listing the server never launches an extension provider, so
    the worker plans against native capabilities only, exactly like the MCP
    workflow tool. A step that declares an execution intent must persist its
    selected route before any side effect; without a planner the worker marked
    those workflows failed with `workflow_route_planner_missing` and silently
    executed nothing.
    """
    registered_tools = frozenset(tool.name for tool in asyncio.run(create_server().list_tools()))
    return build_route_planner(
        registered_tools=registered_tools,
        capabilities=detect_capabilities(),
        policy_version=SETTINGS.execution_router_policy,
        enabled=SETTINGS.execution_router_enabled,
        explain=SETTINGS.execution_router_explain,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true", help="Process the current queue and exit.")
    parser.add_argument("--poll-sec", type=float, default=1.0)
    args = parser.parse_args()
    store = workflow_store(SETTINGS.workflow_db)
    # Build the planner once: every routed step in this process must plan against
    # the same tool and capability snapshot, and a broken catalog must fail the
    # worker before it marks any queued workflow as failed.
    executor = WorkflowExecutor(store, route_planner=build_executor_route_planner())
    owner_id = f"worker-{socket.gethostname()}-{os.getpid()}"
    fatal_codes = {"workflow_schema_too_new"}
    while True:
        queued = store.list_queued(limit=100)["items"]
        for item in queued:
            try:
                executor.execute(str(item["workflow_id"]), owner_id=owner_id)
            except ToolError as exc:
                if exc.code == "workflow_already_claimed":
                    continue
                if exc.code in fatal_codes:
                    raise
                # A deterministic failure of one workflow must not starve the rest
                # of the queue. Executor-owned failures are persisted when possible.
                continue
        if args.once:
            return 0
        time.sleep(max(args.poll_sec, 0.1))


if __name__ == "__main__":
    raise SystemExit(main())
