"""Run queued durable workflows from the configured state database."""

from __future__ import annotations

import argparse
import os
import socket
import time

from core.config import SETTINGS
from core.errors import ToolError
from core.workflows import WorkflowExecutor, workflow_store


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true", help="Process the current queue and exit.")
    parser.add_argument("--poll-sec", type=float, default=1.0)
    args = parser.parse_args()
    store = workflow_store(SETTINGS.workflow_db)
    executor = WorkflowExecutor(store)
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
