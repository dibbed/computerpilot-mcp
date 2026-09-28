from __future__ import annotations

from types import SimpleNamespace

import psutil
import pytest

from core import windows_job


@pytest.mark.parametrize("error", [psutil.AccessDenied(123), psutil.ZombieProcess(123)])
def test_job_termination_survives_process_inspection_failure(
    monkeypatch: pytest.MonkeyPatch, error: psutil.Error,
) -> None:
    terminated: list[int] = []
    job = SimpleNamespace(closed=False, terminate=lambda code: terminated.append(code) or True)
    process = SimpleNamespace(pid=123, wait=lambda timeout: 0)
    owned = windows_job.OwnedProcess(process, job, "windows_job")

    def unavailable(pid: int) -> psutil.Process:
        raise error

    monkeypatch.setattr(windows_job.psutil, "Process", unavailable)
    result = owned.terminate_tree()

    assert terminated == [1]
    assert result["backend"] == "windows_job"
