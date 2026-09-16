"""Lightweight durable-job worker launcher backed by SQLite admission state."""

from __future__ import annotations

import os
import subprocess
import sys
import threading
from pathlib import Path

import psutil

from core.config import PROJECT_ROOT, SETTINGS
from core.jobs import JobStore

_SCHEDULERS: dict[str, JobScheduler] = {}
_SCHEDULERS_LOCK = threading.Lock()


def _reap_detached_worker(process: subprocess.Popen[bytes]) -> None:
    """Wait only to release the parent process handle; the durable worker remains independently owned."""

    try:
        process.wait()
    except Exception:
        # Worker state is authoritative in SQLite; handle reaping must never
        # influence durable execution or scheduler recovery.
        pass


def _start_worker_reaper(process: subprocess.Popen[bytes]) -> None:
    threading.Thread(
        target=_reap_detached_worker,
        args=(process,),
        name=f"job-worker-reaper-{process.pid}",
        daemon=True,
    ).start()


def _scheduler_key(path: Path) -> str:
    return os.path.normcase(str(path.resolve()))


class JobScheduler:
    """Launch only enough independent workers to fill durable-job capacity."""

    def __init__(self, store: JobStore) -> None:
        self.store = store
        self.key = _scheduler_key(store.path)
        self._wake = threading.Event()
        self._thread = threading.Thread(
            target=self._run,
            name=f"job-scheduler-{store.path.stem}",
            daemon=True,
        )

    @property
    def alive(self) -> bool:
        return self._thread.is_alive()

    def start(self) -> None:
        self._thread.start()

    def wake(self) -> None:
        self._wake.set()

    def _launch_worker(self, job_id: str, launch_token: str) -> None:
        directory = self.store.output_dir / job_id
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "stdout.bin").touch(exist_ok=True)
        (directory / "stderr.bin").touch(exist_ok=True)
        flags = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
        try:
            with (directory / "worker.log").open("ab") as log:
                process = subprocess.Popen(
                    [
                        sys.executable,
                        "-m",
                        "scripts.job_worker",
                        str(self.store.path.resolve()),
                        job_id,
                        launch_token,
                    ],
                    cwd=PROJECT_ROOT,
                    stdin=subprocess.DEVNULL,
                    stdout=log,
                    stderr=log,
                    creationflags=flags,
                    start_new_session=os.name != "nt",
                )
                _start_worker_reaper(process)
            try:
                created = psutil.Process(process.pid).create_time()
            except psutil.NoSuchProcess:
                created = None
            self.store.record_worker_launch(
                job_id,
                launch_token,
                worker_pid=process.pid,
                worker_created=created,
            )
        except BaseException as exc:
            self.store.fail_worker_launch(job_id, launch_token, f"Worker launch failed: {type(exc).__name__}: {exc}")

    def launch_once(self) -> int:
        """Synchronously fill currently available worker slots and return queued count."""
        reservations, queued = self.store.reserve_worker_launches(max_running=SETTINGS.max_running_jobs)
        for job_id, launch_token in reservations:
            self._launch_worker(job_id, launch_token)
        return queued

    def _run(self) -> None:
        try:
            while True:
                queued = self.launch_once()
                if queued == 0:
                    return
                self._wake.wait(0.15)
                self._wake.clear()
        finally:
            with _SCHEDULERS_LOCK:
                if _SCHEDULERS.get(self.key) is self:
                    _SCHEDULERS.pop(self.key, None)
            # Close the queue-empty race: a submit can land after the last scan but
            # before this scheduler removes itself from the process registry.
            if self.store.has_queued_jobs():
                ensure_job_scheduler(self.store)


def ensure_job_scheduler(store: JobStore) -> JobScheduler | None:
    """Synchronously kick durable workers, then keep one recovery launcher per jobs DB."""
    if not store.has_queued_jobs():
        return None
    key = _scheduler_key(store.path)
    with _SCHEDULERS_LOCK:
        current = _SCHEDULERS.get(key)
        if current is not None:
            scheduler = current
            start = False
        else:
            scheduler = JobScheduler(store)
            _SCHEDULERS[key] = scheduler
            start = True
    try:
        queued = scheduler.launch_once()
    except BaseException:
        if start:
            with _SCHEDULERS_LOCK:
                if _SCHEDULERS.get(key) is scheduler:
                    _SCHEDULERS.pop(key, None)
        raise
    if queued == 0 and not store.has_queued_jobs():
        if start:
            with _SCHEDULERS_LOCK:
                if _SCHEDULERS.get(key) is scheduler:
                    _SCHEDULERS.pop(key, None)
        return None
    if start:
        try:
            scheduler.start()
        except BaseException:
            with _SCHEDULERS_LOCK:
                if _SCHEDULERS.get(key) is scheduler:
                    _SCHEDULERS.pop(key, None)
            raise
    else:
        scheduler.wake()
    return scheduler
