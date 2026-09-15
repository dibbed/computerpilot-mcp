"""Bounded output delivery with reusable finalized file artifacts."""

from __future__ import annotations

import codecs
import copy
import hashlib
import io
import os
import threading
import uuid
from collections import OrderedDict
from pathlib import Path
from typing import Any, BinaryIO, Literal

from core.artifact_retention import schedule_artifact_retention
from core.config import SETTINGS
from core.resource_locks import RESOURCE_LOCKS, canonical_path
from core.timings import timing_span

Delivery = Literal["inline", "file", "auto"]
AUTO_FILE_BYTES = 1_048_576
INLINE_SOFT_DEFAULT = 128 * 1_024
PREVIEW_DEFAULT = 256 * 1_024
PREVIEW_HARD_MAX = 1_048_576
PREVIEW_MIN_BYTES = 16
_CACHE: OrderedDict[tuple[object, ...], dict[str, Any]] = OrderedDict()
_CACHE_LOCK = threading.Lock()
_CACHE_MAX_ITEMS = 64


def default_delivery() -> Delivery:
    """Resolve the controlled migration flag without silently changing legacy behavior."""

    value = os.getenv("MCP_OUTPUT_DEFAULT", "legacy-inline").strip().casefold()
    if value == "legacy-inline":
        return "inline"
    if value == "auto":
        return "auto"
    raise ValueError("MCP_OUTPUT_DEFAULT must be legacy-inline or auto.")


def _positive_limit(name: str, default: int | None) -> int | None:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        number = int(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be a positive integer.") from exc
    if number < 1:
        raise ValueError(f"{name} must be a positive integer.")
    return number


def _policy() -> tuple[int, int | None, int]:
    soft = _positive_limit("MCP_INLINE_SOFT_LIMIT_BYTES", INLINE_SOFT_DEFAULT)
    hard = _positive_limit("MCP_INLINE_HARD_LIMIT_BYTES", None)
    preview = _positive_limit("MCP_PREVIEW_BYTES", PREVIEW_DEFAULT)
    assert soft is not None and preview is not None
    if preview < PREVIEW_MIN_BYTES:
        raise ValueError(f"MCP_PREVIEW_BYTES must be at least {PREVIEW_MIN_BYTES}.")
    return soft, hard, min(preview, PREVIEW_HARD_MAX)


def _copy_result(result: dict[str, Any]) -> dict[str, Any]:
    return copy.deepcopy(result)


def _read_exact(source: BinaryIO, offset: int, size: int) -> bytes:
    source.seek(offset)
    payload = source.read(size)
    if len(payload) != size:
        raise OSError("Output changed while reading its snapshot.")
    return payload


def _decode_prefix(
    source: BinaryIO,
    *,
    encoding: str,
    offset: int,
    size: int,
    final: bool,
) -> tuple[str, int]:
    payload = _read_exact(source, offset, size)
    decoder = codecs.getincrementaldecoder(encoding)(errors="replace")
    text = decoder.decode(payload, final=final)
    return text, len(payload) - len(decoder.getstate()[0])


def _decode_tail(source: BinaryIO, *, encoding: str, offset: int, size: int) -> tuple[str, int]:
    payload = _read_exact(source, offset, size)
    return payload.decode(encoding, errors="replace"), len(payload)


def _preview(
    source: BinaryIO,
    *,
    encoding: str,
    total: int,
    offset: int,
    limit: int,
    final: bool,
) -> tuple[dict[str, Any], int, int]:
    """Return bounded head/tail text plus a contiguous, encoding-safe head cursor."""

    size = total - offset
    budget = min(size, limit)
    if budget <= 0:
        return {"head": "", "tail": "", "chars": 0}, 0, 0
    if budget >= size:
        head, consumed = _decode_prefix(source, encoding=encoding, offset=offset, size=size, final=final)
        return {"head": head, "tail": "", "chars": len(head)}, consumed, size

    # Reserve enough contiguous bytes for common multibyte encodings so the
    # cursor advances on a complete character without exceeding the budget.
    head_budget = min(size, max(PREVIEW_MIN_BYTES, (budget + 1) // 2))
    head_budget = min(head_budget, budget)
    head, head_consumed = _decode_prefix(
        source,
        encoding=encoding,
        offset=offset,
        size=head_budget,
        final=False,
    )
    tail_budget = budget - head_budget
    tail = ""
    tail_bytes = 0
    if tail_budget:
        tail_start = max(offset + head_budget, total - tail_budget)
        tail, tail_bytes = _decode_tail(source, encoding=encoding, offset=tail_start, size=total - tail_start)
    return {"head": head, "tail": tail, "chars": len(head) + len(tail)}, head_consumed, head_budget + tail_bytes


def deliver_stream(
    source: BinaryIO,
    *,
    encoding: str,
    total: int,
    offset: int = 0,
    delivery: Delivery = "inline",
    final: bool = True,
) -> dict[str, Any]:
    if delivery not in {"inline", "file", "auto"}:
        raise ValueError("Unknown output delivery mode.")
    if offset < 0 or offset > total:
        raise ValueError(f"since_byte must be between 0 and {total}.")

    size = total - offset
    soft, hard, preview_limit = _policy()
    if delivery == "inline" and hard is not None and size > hard:
        raise ValueError("Output exceeds MCP_INLINE_HARD_LIMIT_BYTES; use delivery=auto or file.")

    auto_inline = delivery == "auto" and size <= soft and (hard is None or size <= hard)
    if delivery == "inline" or auto_inline:
        text, consumed = _decode_prefix(source, encoding=encoding, offset=offset, size=size, final=final)
        return {
            "delivery": "inline",
            "bytes": consumed,
            "total_bytes": total,
            "since_byte": offset,
            "next_byte": offset + consumed,
            "truncated": False,
            "encoding": encoding,
            "final": final,
            "snapshot_end_byte": total,
            "text": text,
            "chars": len(text),
        }

    directory = SETTINGS.state_dir / "artifacts"
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"{uuid.uuid4().hex}.bin"
    temporary = directory / f".{target.name}.tmp"
    digest = hashlib.sha256()
    try:
        source.seek(offset)
        with timing_span("artifact_snapshot", metadata={"bytes": size}):
            with temporary.open("xb") as output:
                remaining = size
                while remaining:
                    chunk = source.read(min(remaining, 1_048_576))
                    if not chunk:
                        raise OSError("Output changed while taking its snapshot.")
                    output.write(chunk)
                    digest.update(chunk)
                    remaining -= len(chunk)
            os.replace(temporary, target)
        sha256 = digest.hexdigest()
        result: dict[str, Any] = {
            "delivery": "file",
            "bytes": size,
            "total_bytes": total,
            "since_byte": offset,
            "next_byte": total,
            "truncated": False,
            "encoding": encoding,
            "final": final,
            "snapshot_end_byte": total,
            "path": str(target),
            "sha256": sha256,
            "snapshot_version": target.stem,
        }
        if delivery == "auto":
            preview, head_bytes, preview_bytes = _preview(
                source,
                encoding=encoding,
                total=total,
                offset=offset,
                limit=preview_limit,
                final=final,
            )
            next_byte = offset + head_bytes
            artifact = {
                "path": str(target),
                "sha256": sha256,
                "bytes": size,
                "final": final,
                "snapshot_version": target.stem,
            }
            result.update(
                delivery="file" if size > AUTO_FILE_BYTES else "preview",
                preview=preview,
                preview_bytes=preview_bytes,
                artifact=artifact,
                cursor={"next_byte": next_byte},
                next_byte=next_byte,
                has_more=next_byte < total,
            )
        schedule_artifact_retention(target, settings=SETTINGS)
        return result
    except BaseException:
        temporary.unlink(missing_ok=True)
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
    # while unrelated sources stay concurrent. External writers are detected by
    # the post-copy stat check.
    with RESOURCE_LOCKS.sync(path):
        with path.open("rb") as source:
            stat = os.fstat(source.fileno())
            key = (
                canonical_path(path),
                stat.st_size,
                stat.st_mtime_ns,
                stat.st_ctime_ns,
                offset,
                encoding,
                delivery,
                str(SETTINGS.state_dir),
                os.getenv("MCP_INLINE_SOFT_LIMIT_BYTES"),
                os.getenv("MCP_INLINE_HARD_LIMIT_BYTES"),
                os.getenv("MCP_PREVIEW_BYTES"),
            )
            if final:
                with _CACHE_LOCK:
                    cached = _CACHE.get(key)
                    if cached is not None:
                        artifact = cached.get("path")
                        if isinstance(artifact, str):
                            artifact_path = Path(artifact)
                            with RESOURCE_LOCKS.sync(artifact_path):
                                if artifact_path.is_file():
                                    try:
                                        os.utime(artifact_path, None)
                                    except OSError:
                                        pass
                                    else:
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
