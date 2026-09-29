from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from core import timings


def test_completed_timing_history_is_bounded_and_latest_records_survive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "timings.jsonl"
    monkeypatch.setenv("MCP_TIMINGS", "1")
    monkeypatch.setenv("MCP_TIMINGS_FILE", str(target))
    monkeypatch.setattr(timings, "TIMING_MAX_FILE_BYTES", 512, raising=False)
    monkeypatch.setattr(timings, "TIMING_KEEP_FILES", 2, raising=False)

    for index in range(60):
        timings.record_timing("probe", index, metadata={"index": index})
    timings.flush_timings()

    files = [target, tmp_path / "timings.1.jsonl", tmp_path / "timings.2.jsonl"]
    assert all(path.stat().st_size <= 512 for path in files if path.exists())
    assert not (tmp_path / "timings.3.jsonl").exists()
    assert [json.loads(line)["index"] for line in target.read_text().splitlines()][-1] == 59
    assert len(list(tmp_path.glob("timings*.jsonl"))) <= 3


def test_old_unbounded_timing_log_is_trimmed_without_touching_durable_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "timings.jsonl"
    target.write_text("".join(json.dumps({"index": index}) + "\n" for index in range(100)))
    journal = tmp_path / "operation-recovery.jsonl"
    journal.write_text("recoverable")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "valuable").write_text("keep")
    (tmp_path / "linked").symlink_to(outside, target_is_directory=True)
    monkeypatch.setattr(timings, "TIMING_MAX_FILE_BYTES", 256)
    monkeypatch.setattr(timings, "TIMING_KEEP_FILES", 2)

    timings._append_timing_records(target, [{"index": "new"}])
    timings._append_timing_records(target, [])

    assert all(path.stat().st_size <= 256 for path in tmp_path.glob("timings*.jsonl"))
    assert [json.loads(line) for path in tmp_path.glob("timings*.jsonl") for line in path.read_text().splitlines()]
    assert journal.read_text() == "recoverable"
    assert (outside / "valuable").read_text() == "keep"


def test_independent_timing_writers_keep_complete_json_lines(tmp_path: Path) -> None:
    target = tmp_path / "timings.jsonl"
    code = (
        "from pathlib import Path; import sys; from core.timings import _append_timing_records; "
        "_append_timing_records(Path(sys.argv[1]), [{'worker': sys.argv[2], 'index': i} for i in range(30)])"
    )
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(Path(__file__).resolve().parents[1])
    processes = [
        subprocess.Popen([sys.executable, "-c", code, str(target), str(index)], env=environment)
        for index in range(2)
    ]
    assert [process.wait(timeout=10) for process in processes] == [0, 0]
    records = [json.loads(line) for line in target.read_text().splitlines()]
    assert len(records) == 60
    assert {(record["worker"], record["index"]) for record in records} == {
        (str(worker), index) for worker in range(2) for index in range(30)
    }
