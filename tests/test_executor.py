import sys
from dataclasses import replace
from pathlib import Path
from typing import BinaryIO, cast

import pytest

from core import artifacts
from core.executor import OutputCapture, _read_pipe, run_bounded


def test_final_capture_reuses_snapshot(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(artifacts, "SETTINGS", replace(artifacts.SETTINGS, state_dir=tmp_path))
    capture = OutputCapture(None, "tail")
    try:
        capture.feed(b"a" * 2000000)
        first = capture.result("utf-8", delivery="auto")
        second = capture.result("utf-8", delivery="auto")
        assert first["path"] == second["path"]
        assert len(list((tmp_path / "artifacts").iterdir())) == 1
    finally:
        capture.close()
    assert Path(first["path"]).stat().st_size == 2000000


def test_large_process_auto_and_limited_capture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(artifacts, "SETTINGS", replace(artifacts.SETTINGS, state_dir=tmp_path))
    result = run_bounded(
        [sys.executable, "-c", "import sys;sys.stdout.buffer.write(b'x'*2000000)"], cwd=tmp_path, timeout_sec=10, delivery="auto"
    )
    assert result["ok"] and result["capture_complete"]
    assert result["stdout"]["delivery"] == "file"
    assert Path(result["stdout"]["path"]).stat().st_size == 2000000
    capture = OutputCapture(10, "head")
    try:
        capture.feed(b"x" * 100)
        value = capture.result("utf-8", delivery="auto")
        assert value["truncated"] and value["total_bytes"] == 100
        assert value["text"] == "x" * 10
    finally:
        capture.close()


def test_reader_failure_recorded() -> None:
    class Broken:
        def read(self, size: int) -> bytes:
            raise OSError("read failure")

        def close(self) -> None:
            pass

    capture = OutputCapture(None, "tail")
    try:
        _read_pipe(cast(BinaryIO, Broken()), capture)
        assert capture.read_error == "OSError: read failure"
    finally:
        capture.close()


def test_capture_failure_not_reported_complete(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from core import executor

    def fail(pipe: BinaryIO | None, capture: OutputCapture) -> None:
        capture.read_error = "injected drain failure"
        if pipe is not None:
            pipe.close()

    monkeypatch.setattr(executor, "_read_pipe", fail)
    result = run_bounded([sys.executable, "-c", "pass"], cwd=tmp_path, timeout_sec=5)
    assert not result["ok"]
    assert not result["capture_complete"]
    assert result["capture_error"] == "injected drain failure"
    assert result["stdout"]["final"] is False
