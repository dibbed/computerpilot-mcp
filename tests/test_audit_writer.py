from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from core import audit
from core.audit import AuditPolicy, AuditWriter, _QueuedRecord


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


def _policy(*, batch_size: int = 16, queue_max: int = 128, max_file_bytes: int = 1_000_000, keep_files: int = 5) -> AuditPolicy:
    return AuditPolicy(
        batch_size=batch_size,
        flush_interval_sec=0.01,
        queue_max=queue_max,
        max_file_bytes=max_file_bytes,
        keep_files=keep_files,
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


def test_rotation_and_retention_keep_bounded_valid_json_files(tmp_path: Path) -> None:
    path = tmp_path / "audit.jsonl"
    writer = AuditWriter(path, _policy(batch_size=4, max_file_bytes=700, keep_files=2))
    for index in range(80):
        writer.submit(_payload(index))
    writer.close()

    retained = [candidate for candidate in [path, tmp_path / "audit.1.jsonl", tmp_path / "audit.2.jsonl"] if candidate.exists()]
    assert path.exists()
    assert (tmp_path / "audit.3.jsonl").exists() is False
    assert 1 <= len(retained) <= 3
    assert _records(retained)
    for candidate in retained:
        assert candidate.stat().st_size > 0


def test_lower_retention_prunes_old_rotations_on_start(tmp_path: Path) -> None:
    path = tmp_path / "audit.jsonl"
    for index in range(1, 6):
        (tmp_path / f"audit.{index}.jsonl").write_text("{}\n", encoding="utf-8")

    writer = AuditWriter(path, _policy(keep_files=2))
    writer.close()
    assert (tmp_path / "audit.1.jsonl").exists()
    assert (tmp_path / "audit.2.jsonl").exists()
    assert not (tmp_path / "audit.3.jsonl").exists()
    assert not (tmp_path / "audit.4.jsonl").exists()
    assert not (tmp_path / "audit.5.jsonl").exists()


def test_background_write_failure_is_reported_on_flush(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    writer = AuditWriter(tmp_path / "audit.jsonl", _policy())

    def fail(*args: object, **kwargs: object) -> None:
        raise OSError("disk unavailable")

    monkeypatch.setattr(writer, "_append_payloads", fail)
    writer.submit(_payload(1))
    with pytest.raises(RuntimeError, match="Audit writer failed"):
        writer.flush_now()


def test_large_batch_respects_rotation_ceiling_on_record_boundaries(tmp_path: Path) -> None:
    path = tmp_path / "audit.jsonl"
    writer = AuditWriter(path, _policy(batch_size=64, max_file_bytes=512, keep_files=32))
    for index in range(40):
        writer.submit(_payload(index))
    writer.close()

    paths = sorted(tmp_path.glob("audit*.jsonl"))
    assert len(_records(paths)) == 40
    assert all(candidate.stat().st_size <= 512 for candidate in paths)


def test_flush_fsyncs_active_and_rotated_segments(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "audit.jsonl"
    writer = AuditWriter(path, _policy(batch_size=8, max_file_bytes=512, keep_files=8))
    for index in range(30):
        writer.submit(_payload(index))
    writer._queue.join()
    retained = [candidate for candidate in tmp_path.glob("audit*.jsonl") if candidate.is_file()]
    assert len(retained) >= 2

    original_fsync = audit.os.fsync
    calls = 0

    def counting_fsync(fd: int) -> None:
        nonlocal calls
        calls += 1
        original_fsync(fd)

    monkeypatch.setattr(audit.os, "fsync", counting_fsync)
    writer.flush_now()
    assert calls == len(retained)
    writer.close()


def test_submit_accepted_before_close_is_drained_before_stop(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "audit.jsonl"
    writer = AuditWriter(path, _policy(batch_size=1))
    entered = threading.Event()
    release = threading.Event()
    close_done = threading.Event()
    original_put = writer._queue.put

    def blocked_put(item: _QueuedRecord, block: bool = True, timeout: float | None = None) -> None:
        if item.payload is not None and not entered.is_set():
            entered.set()
            assert release.wait(2)
        original_put(item, block=block, timeout=timeout)

    monkeypatch.setattr(writer._queue, "put", blocked_put)
    def close_writer() -> None:
        writer.close()
        close_done.set()

    submitter = threading.Thread(target=lambda: writer.submit(_payload(1)))
    closer = threading.Thread(target=close_writer)
    submitter.start()
    assert entered.wait(1)
    closer.start()
    time.sleep(0.05)
    assert close_done.is_set() is False

    release.set()
    submitter.join(3)
    closer.join(3)
    assert close_done.is_set()
    assert len(_records([path])) == 1


def test_close_timeout_keeps_writer_retryable_until_thread_stops(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    writer = AuditWriter(tmp_path / "audit.jsonl", _policy(batch_size=1))
    entered = threading.Event()
    release = threading.Event()
    original = writer._append_payloads

    def blocked(payloads: list[bytes], *, durable: bool) -> None:
        if payloads and not release.is_set():
            entered.set()
            assert release.wait(2)
        original(payloads, durable=durable)

    monkeypatch.setattr(audit, "_DURABLE_WAIT_SEC", 0.05)
    monkeypatch.setattr(writer, "_append_payloads", blocked)
    writer.submit(_payload(1))
    assert entered.wait(1)

    with pytest.raises(TimeoutError, match="Timed out draining"):
        writer.close()
    assert writer._thread is not None
    assert writer._thread.is_alive()

    release.set()
    writer.close()
    assert writer._thread is None


def test_multiple_processes_share_rotation_without_corrupting_json(tmp_path: Path) -> None:
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
                "policy = AuditPolicy(batch_size=8, flush_interval_sec=0.01, queue_max=64, max_file_bytes=4096, keep_files=64)",
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
