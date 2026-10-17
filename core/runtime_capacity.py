"""Durable shared runtime capacity permits backed by WorkflowStore."""

from __future__ import annotations

import os
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

import psutil

from core.resource_claims import current_resource_capability, validate_resource_capability
from core.workflow_store import WorkflowStore


@dataclass(slots=True)
class RuntimeCapacityPermit:
    store: WorkflowStore
    admission_id: str
    owner_token: str
    released: bool = False

    def release(self) -> None:
        if self.released:
            return
        self.store.release_capacity(self.admission_id, owner_token=self.owner_token)
        self.released = True

    def __enter__(self) -> RuntimeCapacityPermit:
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        del exc_type, exc, traceback
        self.release()


def acquire_runtime_capacity(
    db_path: Path,
    *,
    category: str,
    limit: int,
    deadline: float | None,
    poll_sec: float = 0.05,
    store: WorkflowStore | None = None,
) -> RuntimeCapacityPermit | None:
    """Wait without holding a DB transaction until shared capacity is admitted."""

    inherited = current_resource_capability()
    capacity_store = (
        validate_resource_capability(inherited, (), store) if inherited is not None
        else store or WorkflowStore(db_path, recover=False)
    )
    admission_id = f"capacity-{uuid.uuid4().hex}"
    owner_token = f"pid-{os.getpid()}-{psutil.Process().create_time():.6f}-{uuid.uuid4().hex}"
    try:
        while True:
            result = capacity_store.reserve_capacity(
                admission_id=admission_id,
                category=category,
                owner_token=owner_token,
                weight=1,
                limit=limit,
            )
            if result["admitted"] is True:
                return RuntimeCapacityPermit(capacity_store, admission_id, owner_token)
            if deadline is not None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    capacity_store.release_capacity(admission_id, owner_token=owner_token)
                    return None
                time.sleep(min(poll_sec, remaining))
            else:
                time.sleep(poll_sec)
    except BaseException:
        capacity_store.release_capacity(admission_id, owner_token=owner_token)
        raise
