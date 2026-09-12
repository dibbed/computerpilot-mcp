"""Snapshot output delivery without loading file artifacts into memory."""

from __future__ import annotations

import codecs
import hashlib
import io
import os
import uuid
from pathlib import Path
from typing import Any, BinaryIO, Literal

from core.config import SETTINGS
from core.timings import timing_span

Delivery = Literal["inline", "file", "auto"]
AUTO_FILE_BYTES = 1_048_576


def deliver_stream(
    source: BinaryIO, *, encoding: str, total: int, offset: int = 0,
    delivery: Delivery = "inline", final: bool = True,
) -> dict[str, Any]:
    if offset < 0 or offset > total:
        raise ValueError(f"since_byte must be between 0 and {total}.")
    source.seek(offset)
    size = total - offset
    mode = "file" if delivery == "file" or (delivery == "auto" and size > AUTO_FILE_BYTES) else "inline"
    result: dict[str, Any] = {
        "delivery": mode, "bytes": size, "total_bytes": total, "since_byte": offset,
        "next_byte": total, "truncated": False, "encoding": encoding,
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
        result.update(path=str(target), sha256=digest.hexdigest())
        return result
    except BaseException:
        target.unlink(missing_ok=True)
        raise


def deliver_file(path: Path, *, encoding: str = "utf-8", offset: int = 0,
                 delivery: Delivery = "inline", final: bool = True) -> dict[str, Any]:
    with path.open("rb") as source:
        return deliver_stream(source, encoding=encoding, total=os.fstat(source.fileno()).st_size,
                              offset=offset, delivery=delivery, final=final)


def deliver_text(text: str, delivery: Delivery) -> dict[str, Any]:
    payload = text.encode("utf-8")
    return deliver_stream(io.BytesIO(payload), encoding="utf-8", total=len(payload), delivery=delivery)
