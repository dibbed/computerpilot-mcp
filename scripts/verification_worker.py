"""Run one bounded native verification request inside a durable job."""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path
from typing import Any

from tools.testing.background import (
    MAX_EVENT_BYTES,
    MAX_REQUEST_BYTES,
    MAX_STREAM_BYTES,
    VerificationRequest,
    request_root,
    source_fingerprint,
)
from tools.testing.verification import verify_changed_repository


def _bounded(value: Any, depth: int = 0) -> Any:
    if depth > 8:
        return "<truncated>"
    if isinstance(value, str):
        return value[:2048]
    if isinstance(value, list):
        return [_bounded(item, depth + 1) for item in value[:200]]
    if isinstance(value, dict):
        return {str(key)[:100]: _bounded(item, depth + 1) for key, item in list(value.items())[:40]}
    return value


def run_request(payload: str) -> int:
    sent = 0

    def emit(event: str, data: dict[str, Any]) -> None:
        nonlocal sent
        row = {"schema_version": 1, "event": event, **_bounded(data)}
        line = json.dumps(row, ensure_ascii=False, allow_nan=False)
        if row != {"schema_version": 1, "event": event, **data}:
            row["output_truncated"] = True
            line = json.dumps(row, ensure_ascii=False, allow_nan=False)
        if len(line.encode("utf-8")) + 1 > MAX_EVENT_BYTES:
            row = {key: value for key, value in row.items() if key not in {"diagnostics", "targets"}}
            for stage in row.get("stages", []):
                stage.pop("diagnostics", None)
                stage.pop("targets", None)
            row["output_truncated"] = True
            line = json.dumps(row, ensure_ascii=False, allow_nan=False)
        size = len(line.encode("utf-8")) + 1
        if size > MAX_EVENT_BYTES or sent + size > MAX_STREAM_BYTES:
            raise ValueError("Verification event quota exceeded")
        print(line, flush=True)
        sent += size

    try:
        if len(payload.encode("utf-8")) > MAX_REQUEST_BYTES:
            raise ValueError("Request quota exceeded")
        request = VerificationRequest.model_validate_json(payload)
        repo = request_root(request)
        before = source_fingerprint(repo)
        emit("started", {"source_fingerprint": request.source_fingerprint})
        if before != request.source_fingerprint:
            emit("result", {"ok": False, "required_unknown": True, "freshness": "stale", "stages": []})
            return 1
        with tempfile.TemporaryDirectory(prefix="computerpilot-verification-") as temporary:
            result = verify_changed_repository(
                repo, **request.options(),
                stage_callback=lambda event, data: emit(event, data),
                max_parallel_checks=request.max_parallel_checks,
                pytest_extra_args=["--basetemp", str(Path(temporary) / "pytest"), "-o", f"cache_dir={temporary}/cache"],
            )
        after = source_fingerprint(repo)
        fresh = before == after
        emit("result", {
            "ok": bool(result["ok"] and fresh), "required_unknown": bool(result["required_unknown"] or not fresh),
            "freshness": "current" if fresh else "stale", "source_fingerprint": before,
            "final_fingerprint": after, "mode": result["mode"], "stages": result["stages"],
            "duration_ms": result["duration_ms"],
        })
        return 0 if result["ok"] and fresh else 1
    except Exception as exc:
        # Native worker errors must not echo request contents or arbitrary analyzer payloads.
        emit("result", {"ok": False, "required_unknown": True, "freshness": "unknown", "error": type(exc).__name__})
        return 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request-json", required=True)
    return run_request(parser.parse_args().request_json)


if __name__ == "__main__":
    sys.exit(main())
