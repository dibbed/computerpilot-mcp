from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from core import audit
from core.audit import AuditPolicy, AuditWriter


def _payload(index: int) -> bytes:
    return (
        json.dumps(
            {
                "time": "2026-09-15T00:00:00+00:00",
                "operation": "write_file",
                "outcome": "succeeded",
                "pid": os.getpid(),
                "target": f"C:/work/{index}.txt",
            },
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")


def _records(paths: list[Path]) -> list[dict[str, object]]:
    records: list[dict[str, object]] = []
    for path in paths:
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                records.append(json.loads(line))
    return records


def _policy(*, batch_size: int = 16, queue_max: int = 128) -> AuditPolicy:
    return AuditPolicy(
        batch_size=batch_size,
        flush_interval_sec=0.01,
        queue_max=queue_max,
    )


def test_batched_writer_flushes_every_record(tmp_path: Path) -> None:
    path = tmp_path / "audit.jsonl"
    writer = AuditWriter(path, _policy())
    for index in range(1_000):
        writer.submit(_payload(index))
    writer.flush_now()
    writer.close()

    records = _records([path])
    assert len(records) == 1_000
    assert {record["target"] for record in records} == {f"C:/work/{index}.txt" for index in range(1_000)}


def test_durable_record_fsyncs_before_submit_returns(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "audit.jsonl"
    writer = AuditWriter(path, _policy())
    original_fsync = audit.os.fsync
    calls = 0

    def counting_fsync(fd: int) -> None:
        nonlocal calls
        calls += 1
        original_fsync(fd)

    monkeypatch.setattr(audit.os, "fsync", counting_fsync)
    writer.submit(_payload(1), durable=True)
    assert calls >= 1
    assert len(_records([path])) == 1
    writer.close()


def test_background_write_failure_is_reported_on_flush(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    writer = AuditWriter(tmp_path / "audit.jsonl", _policy())

    def fail(*args: object, **kwargs: object) -> None:
        raise OSError("disk unavailable")

    monkeypatch.setattr(writer, "_append_payloads", fail)
    writer.submit(_payload(1))
    with pytest.raises(RuntimeError, match="Audit writer failed"):
        writer.flush_now()


def test_multiple_processes_share_audit_file_without_corrupting_json(tmp_path: Path) -> None:
    path = tmp_path / "audit.jsonl"
    helper = tmp_path / "writer_helper.py"
    repo = Path(__file__).resolve().parents[1]
    helper.write_text(
        "\n".join(
            [
                "import json, os, sys",
                "from pathlib import Path",
                f"sys.path.insert(0, {str(repo)!r})",
                "from core.audit import AuditPolicy, AuditWriter",
                "path = Path(sys.argv[1])",
                "worker = int(sys.argv[2])",
                "policy = AuditPolicy(batch_size=8, flush_interval_sec=0.01, queue_max=64)",
                "writer = AuditWriter(path, policy)",
                "for index in range(100):",
                "    payload = (json.dumps({'worker': worker, 'index': index}, separators=(',', ':')) + '\\n').encode('utf-8')",
                "    writer.submit(payload)",
                "writer.close()",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    processes = [
        subprocess.Popen(
            [sys.executable, str(helper), str(path), str(worker)],
            cwd=repo,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        for worker in range(4)
    ]
    for process in processes:
        _, stderr = process.communicate(timeout=20)
        assert process.returncode == 0, stderr.decode("utf-8", errors="replace")

    paths = sorted(tmp_path.glob("audit*.jsonl"))
    records = _records(paths)
    assert len(records) == 400
    assert {(int(str(record["worker"])), int(str(record["index"]))) for record in records} == {
        (worker, index) for worker in range(4) for index in range(100)
    }
