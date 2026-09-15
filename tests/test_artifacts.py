from __future__ import annotations

import hashlib
import io
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path

import pytest

from core import artifacts
from core.artifact_retention import ArtifactPolicy, cleanup_artifacts


@pytest.fixture(autouse=True)
def artifact_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(artifacts, "SETTINGS", replace(artifacts.SETTINGS, state_dir=tmp_path))
    for name in (
        "MCP_OUTPUT_DEFAULT",
        "MCP_INLINE_SOFT_LIMIT_BYTES",
        "MCP_INLINE_HARD_LIMIT_BYTES",
        "MCP_PREVIEW_BYTES",
    ):
        monkeypatch.delenv(name, raising=False)


def test_finalized_100_mib_snapshot_is_reused_in_parallel(tmp_path: Path) -> None:
    source = tmp_path / "source.bin"
    chunk = b"0123456789abcdef" * 65_536
    digest = hashlib.sha256()
    with source.open("wb") as stream:
        for _ in range(100):
            stream.write(chunk)
            digest.update(chunk)

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: artifacts.deliver_file(source, delivery="file", final=True), range(4)))

    assert len({result["path"] for result in results}) == 1
    assert len({result["sha256"] for result in results}) == 1
    assert results[0]["sha256"] == digest.hexdigest()
    assert results[0]["final"] is True
    assert results[0]["snapshot_end_byte"] == 100 * 1024 * 1024
    assert len(list((tmp_path / "artifacts").iterdir())) == 1


def test_live_snapshots_are_not_reused_and_final_change_invalidates_cache(tmp_path: Path) -> None:
    source = tmp_path / "source.bin"
    source.write_bytes(b"abc")
    live_one = artifacts.deliver_file(source, delivery="file", final=False)
    live_two = artifacts.deliver_file(source, delivery="file", final=False)
    assert live_one["path"] != live_two["path"]

    final_one = artifacts.deliver_file(source, delivery="file", final=True)
    final_two = artifacts.deliver_file(source, delivery="file", final=True)
    assert final_one["path"] == final_two["path"]

    source.write_bytes(b"abcdef")
    changed = artifacts.deliver_file(source, delivery="file", final=True)
    assert changed["path"] != final_one["path"]
    assert Path(changed["path"]).read_bytes() == b"abcdef"


def test_cached_artifact_access_renews_retention_lease(tmp_path: Path) -> None:
    first_source = tmp_path / "first.bin"
    second_source = tmp_path / "second.bin"
    first_source.write_bytes(b"first")
    second_source.write_bytes(b"second")
    first = artifacts.deliver_file(first_source, delivery="file", final=True)
    second = artifacts.deliver_file(second_source, delivery="file", final=True)
    first_path = Path(first["path"])
    second_path = Path(second["path"])
    now = time.time()
    artifacts.os.utime(first_path, (now - 120, now - 120))
    artifacts.os.utime(second_path, (now - 10, now - 10))

    cached = artifacts.deliver_file(first_source, delivery="file", final=True)
    result = cleanup_artifacts(
        tmp_path / "artifacts",
        ArtifactPolicy(
            max_bytes=1,
            max_age_sec=0,
            max_count=1,
            cleanup_interval_sec=0,
            recent_grace_sec=60,
        ),
        now=now + 1,
    )

    assert cached["path"] == str(first_path)
    assert first_path.is_file()
    assert second_path.is_file()
    assert result.quota_satisfied is False


def test_deleted_cached_artifact_is_recreated(tmp_path: Path) -> None:
    source = tmp_path / "source.bin"
    source.write_bytes(b"payload")
    first = artifacts.deliver_file(source, delivery="file", final=True)
    Path(first["path"]).unlink()
    second = artifacts.deliver_file(source, delivery="file", final=True)
    assert second["path"] != first["path"]
    assert Path(second["path"]).read_bytes() == b"payload"


def test_finalized_cache_uses_canonical_source_identity(tmp_path: Path) -> None:
    source = tmp_path / "source.bin"
    source.write_bytes(b"payload")
    first = artifacts.deliver_file(source, delivery="file", final=True)
    alias = source.parent / "." / source.name
    second = artifacts.deliver_file(alias, delivery="file", final=True)
    assert second["path"] == first["path"]
    if artifacts.os.name == "nt":
        case_alias = Path(str(source).upper())
        third = artifacts.deliver_file(case_alias, delivery="file", final=True)
        assert third["path"] == first["path"]


def test_short_source_cleans_failed_artifact(tmp_path: Path) -> None:
    with pytest.raises(OSError):
        artifacts.deliver_stream(io.BytesIO(b"a"), encoding="utf-8", total=10, delivery="file")
    assert not list((tmp_path / "artifacts").iterdir())


def test_artifact_is_not_visible_as_bin_until_snapshot_is_complete(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entered = threading.Event()
    release = threading.Event()
    payload = b"x" * (2 * 1024 * 1024)

    class BlockingStream(io.BytesIO):
        def read(self, size: int | None = -1) -> bytes:
            data = super().read(size)
            if not entered.is_set():
                entered.set()
                assert release.wait(2)
            return data

    monkeypatch.setattr(artifacts, "schedule_artifact_retention", lambda *args, **kwargs: None)
    results: list[dict[str, object]] = []
    thread = threading.Thread(
        target=lambda: results.append(
            artifacts.deliver_stream(BlockingStream(payload), encoding="utf-8", total=len(payload), delivery="file")
        )
    )
    thread.start()
    assert entered.wait(1)

    directory = tmp_path / "artifacts"
    assert directory.is_dir()
    assert list(directory.glob("*.bin")) == []
    assert list(directory.glob("*.tmp"))

    release.set()
    thread.join(3)
    assert thread.is_alive() is False
    assert len(results) == 1
    final_path = Path(str(results[0]["path"]))
    assert final_path.is_file()
    assert final_path.stat().st_size == len(payload)
    assert list(directory.glob("*.tmp")) == []
