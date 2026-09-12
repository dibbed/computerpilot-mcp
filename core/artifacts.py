"""Snapshot output delivery with reusable finalized file artifacts."""

from __future__ import annotations

import codecs
import hashlib
import io
import os
import threading
import uuid
from collections import OrderedDict
from pathlib import Path
from typing import Any, BinaryIO, Literal

from core.config import SETTINGS
from core.resource_locks import RESOURCE_LOCKS
from core.timings import timing_span

Delivery = Literal["inline", "file", "auto"]
AUTO_FILE_BYTES = 1_048_576
_CACHE: OrderedDict[tuple[object, ...], dict[str, Any]] = OrderedDict()
_CACHE_LOCK = threading.Lock()
_CACHE_MAX_ITEMS = 64


def _copy_result(result: dict[str, Any]) -> dict[str, Any]:
    return dict(result)


def deliver_stream(
    source: BinaryIO,
    *,
    encoding: str,
    total: int,
    offset: int = 0,
    delivery: Delivery = "inline",
    final: bool = True,
) -> dict[str, Any]:
    if offset < 0 or offset > total:
        raise ValueError(f"since_byte must be between 0 and {total}.")
    source.seek(offset)
    size = total - offset
    mode = "file" if delivery == "file" or (delivery == "auto" and size > AUTO_FILE_BYTES) else "inline"
    result: dict[str, Any] = {
        "delivery": mode,
        "bytes": size,
        "total_bytes": total,
        "since_byte": offset,
        "next_byte": total,
        "truncated": False,
        "encoding": encoding,
        "final": final,
        "snapshot_end_byte": total,
    }
    if mode == "inline":
        payload = source.read(size)
        decoder = codecs.getincrementaldecoder(encoding)(errors="replace")
        result["text"] = decoder.decode(payload, final=final)
        pending = len(decoder.getstate()[0])
        result["bytes"] = len(payload) - pending
        result["next_byte"] = offset + result["bytes"]
        result["chars"] = len(result["text"])
        return result
    directory = SETTINGS.state_dir / "artifacts"
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"{uuid.uuid4().hex}.bin"
    digest = hashlib.sha256()
    try:
        with timing_span("artifact_snapshot", metadata={"bytes": size}):
            with target.open("xb") as output:
                remaining = size
                while remaining:
                    chunk = source.read(min(remaining, 1_048_576))
                    if not chunk:
                        raise OSError("Output changed while taking its snapshot.")
                    output.write(chunk)
                    digest.update(chunk)
                    remaining -= len(chunk)
        result.update(path=str(target), sha256=digest.hexdigest(), snapshot_version=target.stem)
        return result
    except BaseException:
        target.unlink(missing_ok=True)
        raise


def deliver_file(
    path: Path,
    *,
    encoding: str = "utf-8",
    offset: int = 0,
    delivery: Delivery = "inline",
    final: bool = True,
) -> dict[str, Any]:
    # The keyed resource lock prevents duplicate snapshots for the same source
    # while keeping unrelated files independent. External writers are still
    # detected by the post-copy stat check.
    with RESOURCE_LOCKS.sync(path):
        with path.open("rb") as source:
            stat = os.fstat(source.fileno())
            key = (
                str(path.resolve()),
                stat.st_size,
                stat.st_mtime_ns,
                stat.st_ctime_ns,
                offset,
                encoding,
                delivery,
                str(SETTINGS.state_dir),
            )
            if final:
                with _CACHE_LOCK:
                    cached = _CACHE.get(key)
                    if cached is not None:
                        artifact = cached.get("path")
                        if isinstance(artifact, str) and Path(artifact).is_file():
                            _CACHE.move_to_end(key)
                            return _copy_result(cached)
                        _CACHE.pop(key, None)
            result = deliver_stream(
                source,
                encoding=encoding,
                total=stat.st_size,
                offset=offset,
                delivery=delivery,
                final=final,
            )
            after = os.fstat(source.fileno())
            if final and (after.st_size, after.st_mtime_ns, after.st_ctime_ns) != (
                stat.st_size,
                stat.st_mtime_ns,
                stat.st_ctime_ns,
            ):
                artifact = result.get("path")
                if isinstance(artifact, str):
                    Path(artifact).unlink(missing_ok=True)
                raise OSError("Final output changed while taking its snapshot.")
            if final and "path" in result:
                with _CACHE_LOCK:
                    _CACHE[key] = _copy_result(result)
                    _CACHE.move_to_end(key)
                    while len(_CACHE) > _CACHE_MAX_ITEMS:
                        _CACHE.popitem(last=False)
            return _copy_result(result)


def deliver_text(text: str, delivery: Delivery) -> dict[str, Any]:
    payload = text.encode("utf-8")
    return deliver_stream(io.BytesIO(payload), encoding="utf-8", total=len(payload), delivery=delivery)
