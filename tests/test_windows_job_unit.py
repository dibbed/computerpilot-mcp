from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast

import psutil
import pytest

from core import windows_job


@pytest.mark.parametrize("error", [psutil.AccessDenied(123), psutil.ZombieProcess(123)])
def test_job_termination_survives_process_inspection_failure(
    monkeypatch: pytest.MonkeyPatch, error: psutil.Error,
) -> None:
    terminated: list[int] = []

    def terminate(code: int) -> bool:
        terminated.append(code)
        return True

    job = SimpleNamespace(closed=False, terminate=terminate)
    process = SimpleNamespace(pid=123, wait=lambda timeout: 0)
    owned = windows_job.OwnedProcess(cast(Any, process), cast(Any, job), "windows_job")

    def unavailable(pid: int) -> psutil.Process:
        raise error

    monkeypatch.setattr(windows_job.psutil, "Process", unavailable)
    result = owned.terminate_tree()

    assert terminated == [1]
    assert result["backend"] == "windows_job"
