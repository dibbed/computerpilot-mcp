from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from core import artifacts
from core.artifacts import deliver_file
from core.config import PROJECT_ROOT
from core.errors import ToolError
from core.executor import OutputCapture, run_bounded
from core.jobs import JobStore


def wait_job(store: JobStore, job_id: str) -> dict[str, Any]:
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        result = store.get(job_id)
        if result["status"] not in {"queued", "running"}:
            return result
        time.sleep(0.05)
    store.cancel(job_id)
    raise AssertionError("Job did not finish.")


def test_incremental_utf8_defers_partial_character() -> None:
    capture = OutputCapture(None, "tail")
    try:
        encoded = "سلام".encode()
        capture.feed(encoded[:3])
        first = capture.result("utf-8", since_byte=0, final=False)
        assert first["text"] == "س"
        assert first["next_byte"] == 2
        capture.feed(encoded[3:])
        second = capture.result("utf-8", since_byte=first["next_byte"], final=False)
        assert first["text"] + second["text"] == "سلام"
        assert second["next_byte"] == len(encoded)
        assert capture.result("utf-8", since_byte=second["next_byte"])["text"] == ""
        with pytest.raises(ValueError):
            capture.result("utf-8", since_byte=100)
    finally:
        capture.close()


def test_cursor_rejects_limited_capture() -> None:
    capture = OutputCapture(2, "tail")
    try:
        capture.feed(b"abcdef")
        with pytest.raises(ToolError, match="capture_limit"):
            capture.result("utf-8", since_byte=0)
    finally:
        capture.close()


def test_file_auto_delivery_exact_bytes_and_hash(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(artifacts, "SETTINGS", replace(artifacts.SETTINGS, state_dir=tmp_path))
    payload = "سلام".encode() * 200_000
    source = tmp_path / "source.bin"
    source.write_bytes(payload)
    result = deliver_file(source, delivery="auto", offset=8)
    assert "text" not in result
    assert Path(result["path"]).read_bytes() == payload[8:]
    assert result["sha256"] == hashlib.sha256(payload[8:]).hexdigest()
    assert result["next_byte"] == len(payload)
    assert result["truncated"] is False
    assert deliver_file(source, offset=len(payload), delivery="auto")["text"] == ""


def test_foreground_file_delivery(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(artifacts, "SETTINGS", replace(artifacts.SETTINGS, state_dir=tmp_path))
    result = run_bounded([sys.executable, "-c", "import sys;sys.stdout.write('x'*1200000);sys.stderr.write('oops')"],
                         cwd=tmp_path, timeout_sec=10, delivery="file")
    assert result["ok"]
    assert Path(result["stdout"]["path"]).stat().st_size == 1_200_000
    assert Path(result["stderr"]["path"]).read_bytes() == b"oops"


def test_concurrent_job_deduplication_and_conflict(tmp_path: Path) -> None:
    store = JobStore(tmp_path / "jobs.sqlite3")
    code = "from pathlib import Path; p=Path('side_effect'); p.open('a').write('once'); print('done')"
    def submit() -> dict[str, Any]:
        return store.submit([sys.executable, "-c", code], tmp_path, 10, "same-key")
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: submit(), range(2)))
    assert results[0]["job_id"] == results[1]["job_id"]
    assert sorted(r["deduplicated"] for r in results) == [False, True]
    job_id = results[0]["job_id"]
    assert wait_job(store, job_id)["status"] == "succeeded"
    assert (tmp_path / "side_effect").read_text() == "once"
    assert JobStore(store.path).output(job_id)["stdout"]["text"].strip() == "done"
    assert store.list(0, 1)["total_count"] == 1
    with pytest.raises(ToolError, match="different command"):
        store.submit([sys.executable, "-c", "print(2)"], tmp_path, 10, "same-key")


@pytest.mark.parametrize("cancel", [False, True])
def test_job_timeout_and_cancel(tmp_path: Path, cancel: bool) -> None:
    store = JobStore(tmp_path / "jobs.sqlite3")
    result = store.submit([sys.executable, "-c", "import time;time.sleep(30)"], tmp_path, 0.3 if not cancel else 30, "key")
    if cancel:
        store.cancel(result["job_id"])
    finished = wait_job(store, result["job_id"])
    assert finished["status"] == ("cancelled" if cancel else "timed_out")


def test_job_survives_submitter_exit(tmp_path: Path) -> None:
    code = (
        "import json,sys;from pathlib import Path;from core.jobs import JobStore;"
        "s=JobStore(Path(sys.argv[1]));"
        "print(json.dumps(s.submit([sys.executable,'-c','import time;time.sleep(1);print(42)'],Path(sys.argv[2]),10,'survive')))"
    )
    result = subprocess.run([sys.executable, "-c", code, str(tmp_path / "jobs.sqlite3"), str(tmp_path)],
                            cwd=PROJECT_ROOT, capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    job_id = json.loads(result.stdout)["job_id"]
    store = JobStore(tmp_path / "jobs.sqlite3")
    assert wait_job(store, job_id)["status"] == "succeeded"
    assert store.output(job_id)["stdout"]["text"].strip() == "42"


def test_lost_worker_is_not_replayed(tmp_path: Path) -> None:
    store = JobStore(tmp_path / "jobs.sqlite3")
    result = store.submit([sys.executable, "-c", "print('done')"], tmp_path, 10, "lost")
    job_id = result["job_id"]
    wait_job(store, job_id)
    store.update(job_id, status="running", worker_pid=99999999, worker_created=0)
    assert store.get(job_id)["status"] == "interrupted"
    assert store.submit([sys.executable, "-c", "print('done')"], tmp_path, 10, "lost")["deduplicated"] is True
