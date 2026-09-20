"""Durable polling worker for change-aware repository verification."""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from tools.testing.verification import verify_changed_repository
from tools.testing.watch import diff_snapshots, snapshot


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("repo", type=Path)
    parser.add_argument("--poll-sec", type=float, default=1.0)
    parser.add_argument("--debounce-sec", type=float, default=0.5)
    parser.add_argument("--max-runtime-sec", type=float, default=3600.0)
    parser.add_argument("--max-files", type=int, default=50_000)
    args = parser.parse_args()

    baseline, truncated = snapshot(args.repo, max_files=args.max_files)
    print(json.dumps({"event": "ready", "file_count": len(baseline), "scan_truncated": truncated}), flush=True)
    deadline = time.monotonic() + args.max_runtime_sec
    generation = 0
    while time.monotonic() < deadline:
        time.sleep(args.poll_sec)
        current, truncated = snapshot(args.repo, max_files=args.max_files)
        changed = diff_snapshots(baseline, current)
        if not changed:
            baseline = current
            continue
        time.sleep(args.debounce_sec)
        settled, settled_truncated = snapshot(args.repo, max_files=args.max_files)
        changed = sorted(set(changed) | set(diff_snapshots(current, settled)))
        baseline = settled
        generation += 1
        try:
            result = verify_changed_repository(args.repo, changed_paths=changed, affected_only=True)
            record = {
                "event": "verification",
                "generation": generation,
                "changed_paths": changed,
                "scan_truncated": truncated or settled_truncated,
                "result": result,
            }
        except Exception as exc:  # keep the watch alive after one failed generation
            record = {
                "event": "verification_error",
                "generation": generation,
                "changed_paths": changed,
                "error": type(exc).__name__,
                "message": str(exc),
            }
        print(json.dumps(record, ensure_ascii=False, separators=(",", ":")), flush=True)
    print(json.dumps({"event": "stopped", "reason": "max_runtime", "generations": generation}), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
