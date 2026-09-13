from __future__ import annotations

import hashlib
import io
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path

import pytest

from core import artifacts


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
