"""One durable job worker; never replays an already claimed job."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from contextlib import closing
from pathlib import Path

import psutil

from core.audit import audit_action
from core.executor import _creation_flags, terminate_process_tree
from core.jobs import JobStore


def run(path: Path, job_id: str) -> None:
    store = JobStore(path)
    with closing(store.connect()) as db, db:
        claimed = db.execute(
            "UPDATE jobs SET status='running',worker_pid=?,worker_created=?,updated=? WHERE id=? AND status='queued'",
            (os.getpid(), psutil.Process().create_time(), time.time(), job_id),
        ).rowcount
    if not claimed:
        return
    process: subprocess.Popen[bytes] | None = None
    try:
        row = store.raw(job_id)
        if row["cancel_requested"]:
            store.update(job_id, status="cancelled")
            return
        spec = json.loads(row["spec"])
        directory = store.output_dir / job_id
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = spec["encoding"]
        with (directory / "stdout.bin").open("ab") as stdout, (directory / "stderr.bin").open("ab") as stderr:
            process = subprocess.Popen(spec["command"], cwd=spec["cwd"], env=env, stdin=subprocess.DEVNULL,
                                       stdout=stdout, stderr=stderr, creationflags=_creation_flags())
            try:
                created = psutil.Process(process.pid).create_time()
            except psutil.NoSuchProcess:
                created = None
            store.update(job_id, pid=process.pid, pid_created=created)
            deadline = time.monotonic() + spec["timeout_sec"]
            status = "succeeded"
            while process.poll() is None:
                cancelled = store.raw(job_id)["cancel_requested"]
                if cancelled or time.monotonic() >= deadline:
                    status = "cancelled" if cancelled else "timed_out"
                    audit_action("job_terminate_process_tree", target=str(process.pid),
                                 details={"job_id": job_id, "reason": status})
                    terminate_process_tree(process.pid, force=True)
                    break
                time.sleep(0.2)
            code = process.wait(timeout=5)
            if status == "succeeded" and code != 0:
                status = "failed"
        store.update(job_id, status=status, exit_code=code)
    except BaseException as exc:
        if process is not None and process.poll() is None:
            audit_action("job_terminate_process_tree", target=str(process.pid),
                         details={"job_id": job_id, "reason": "worker_error"})
            terminate_process_tree(process.pid, force=True)
        store.update(job_id, status="failed", error=str(exc))


if __name__ == "__main__":
    run(Path(sys.argv[1]), sys.argv[2])
