"""Durable metadata-only recovery journal for supervised mutating MCP tools."""

from __future__ import annotations

import json
import os
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from core.errors import ToolError

JOURNAL_SCHEMA_VERSION = 1
_MAX_LINE_BYTES = 32_768
_MAX_TARGET_CHARS = 1_000

RecordType = Literal["begin", "result", "uncertain"]


@dataclass(frozen=True, slots=True)
class OperationHandle:
    operation_id: str
    runtime_id: str
    operation_type: str
    target: str | None
    started_at: str


class OperationRecoveryJournal:
    """Append-only fsync journal; incomplete previous-runtime mutations become uncertain."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._path: Path | None = None
        self._runtime_id: str | None = None
        self._enabled = False

    def configure(self, path: Path | None, runtime_id: str | None) -> None:
        with self._lock:
            self._path = path
            self._runtime_id = runtime_id
            self._enabled = path is not None and bool(runtime_id)
            if self._enabled:
                assert path is not None
                path.parent.mkdir(parents=True, exist_ok=True)
                self._recover_previous_locked()

    def configure_from_env(self, state_dir: Path) -> None:
        runtime_id = os.environ.get("MCP_RUNTIME_GENERATION_ID", "").strip() or None
        path = state_dir / "operation-recovery.jsonl" if runtime_id else None
        self.configure(path, runtime_id)

    @property
    def enabled(self) -> bool:
        with self._lock:
            return self._enabled

    def _append_locked(self, record: dict[str, Any]) -> None:
        path = self._path
        if not self._enabled or path is None:
            return
        payload = (json.dumps(record, ensure_ascii=False, separators=(",", ":"), sort_keys=True) + "\n").encode("utf-8")
        if len(payload) > _MAX_LINE_BYTES:
            raise ToolError("operation_journal_record_too_large", "Recovery journal metadata exceeded its bounded record size.")
        try:
            with path.open("a+b", buffering=0) as handle:
                handle.seek(0, os.SEEK_END)
                size = handle.tell()
                if size:
                    handle.seek(-1, os.SEEK_END)
                    if handle.read(1) != b"\n":
                        handle.seek(0, os.SEEK_END)
                        handle.write(b"\n")
                handle.seek(0, os.SEEK_END)
                handle.write(payload)
                os.fsync(handle.fileno())
        except OSError as exc:
            raise ToolError(
                "operation_journal_unavailable",
                "Could not durably update the operation recovery journal.",
                hint="Do not retry a possibly-started mutation blindly; inspect runtime storage first.",
            ) from exc

    def _load_locked(self) -> list[dict[str, Any]]:
        path = self._path
        if path is None or not path.is_file():
            return []
        records: list[dict[str, Any]] = []
        try:
            with path.open("rb") as handle:
                for raw in handle:
                    if len(raw) > _MAX_LINE_BYTES:
                        continue
                    try:
                        record = json.loads(raw.decode("utf-8"))
                    except (UnicodeDecodeError, json.JSONDecodeError):
                        # A crash can leave only the final append truncated.
                        continue
                    if isinstance(record, dict) and record.get("schema_version") == JOURNAL_SCHEMA_VERSION:
                        records.append(record)
        except OSError as exc:
            raise ToolError(
                "operation_journal_unavailable",
                "Could not read the operation recovery journal.",
            ) from exc
        return records

    @staticmethod
    def _state(records: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
        state: dict[str, dict[str, Any]] = {}
        for record in records:
            operation_id = record.get("operation_id")
            record_type = record.get("type")
            if not isinstance(operation_id, str) or record_type not in {"begin", "result", "uncertain"}:
                continue
            if record_type == "begin":
                state[operation_id] = {"begin": record}
            elif operation_id in state:
                state[operation_id][record_type] = record
        return state

    def _recover_previous_locked(self) -> None:
        runtime_id = self._runtime_id
        if runtime_id is None:
            return
        records = self._load_locked()
        for operation_id, item in self._state(records).items():
            begin = item.get("begin")
            if not begin or item.get("result") or item.get("uncertain"):
                continue
            if begin.get("runtime_id") == runtime_id:
                continue
            self._append_locked({
                "schema_version": JOURNAL_SCHEMA_VERSION,
                "type": "uncertain",
                "status": "uncertain",
                "operation_id": operation_id,
                "runtime_id": begin.get("runtime_id"),
                "marked_by_runtime_id": runtime_id,
                "marked_at": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
                "reason": "runtime_ended_without_known_result",
            })

    def begin(self, operation_type: str, target: str | None = None) -> OperationHandle | None:
        with self._lock:
            if not self._enabled:
                return None
            runtime_id = self._runtime_id
            assert runtime_id is not None
            started_at = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
            handle = OperationHandle(
                operation_id=uuid.uuid4().hex,
                runtime_id=runtime_id,
                operation_type=operation_type,
                target=target[:_MAX_TARGET_CHARS] if target else None,
                started_at=started_at,
            )
            self._append_locked({
                "schema_version": JOURNAL_SCHEMA_VERSION,
                "type": "begin",
                "status": "in_progress",
                "operation_id": handle.operation_id,
                "runtime_id": handle.runtime_id,
                "operation_type": handle.operation_type,
                "target": handle.target,
                "started_at": handle.started_at,
                "pid": os.getpid(),
            })
            return handle

    def finish(self, handle: OperationHandle | None, *, known_result: str) -> None:
        if handle is None:
            return
        with self._lock:
            self._append_locked({
                "schema_version": JOURNAL_SCHEMA_VERSION,
                "type": "result",
                "status": "completed",
                "operation_id": handle.operation_id,
                "runtime_id": handle.runtime_id,
                "finished_at": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
                "known_result": known_result[:100],
            })

    def finish_returned(self, handle: OperationHandle | None, result: Any) -> None:
        if isinstance(result, dict) and isinstance(result.get("ok"), bool):
            known_result = "ok" if result["ok"] else "returned_error"
        else:
            known_result = "returned"
        self.finish(handle, known_result=known_result)

    def summary(self, *, max_items: int = 5) -> dict[str, Any]:
        with self._lock:
            if not self._enabled or self._path is None:
                return {"enabled": False, "uncertain_count": 0, "pending_count": 0, "uncertain": []}
            records = self._load_locked()
            state = self._state(records)
            uncertain: list[dict[str, Any]] = []
            pending_count = 0
            for operation_id, item in state.items():
                begin = item.get("begin")
                if not begin or item.get("result"):
                    continue
                marker = item.get("uncertain")
                if marker is None:
                    pending_count += 1
                    continue
                uncertain.append({
                    "operation_id": operation_id,
                    "status": "uncertain",
                    "operation_type": begin.get("operation_type"),
                    "target": begin.get("target"),
                    "started_at": begin.get("started_at"),
                    "marked_at": marker.get("marked_at"),
                    "reason": marker.get("reason"),
                })
            uncertain.sort(key=lambda item: str(item.get("marked_at") or ""), reverse=True)
            return {
                "enabled": True,
                "uncertain_count": len(uncertain),
                "pending_count": pending_count,
                "uncertain": uncertain[:max_items],
            }


OPERATION_RECOVERY = OperationRecoveryJournal()
