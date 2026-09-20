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
    while True:
        queued = [item for item in store.list(limit=100)["items"] if item["state"] == "queued"]
        for item in reversed(queued):
            try:
                executor.execute(str(item["workflow_id"]), owner_id=owner_id)
            except ToolError as exc:
                if exc.code != "workflow_already_claimed":
                    raise
        if args.once:
            return 0
        time.sleep(max(args.poll_sec, 0.1))


if __name__ == "__main__":
    raise SystemExit(main())
