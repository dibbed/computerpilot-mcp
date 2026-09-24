"""Durable metadata-only recovery journal for supervised mutating MCP tools."""

from __future__ import annotations

from contextlib import AbstractContextManager
import json
import os
import threading
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from core.errors import ToolError
from core.file_lock import exclusive_file_lock
from core.recovery_models import Evidence, OperationState, Postcondition

JOURNAL_SCHEMA_VERSION = 1
COMPACT_AFTER_RECORDS = 4_096
_MAX_LINE_BYTES = 32_768
_MAX_TARGET_CHARS = 1_000
_JOURNAL_LOCK_WAIT_SEC = 10.0

RecordType = Literal["begin", "result", "uncertain", "reconciliation", "acknowledged"]


def _interprocess_journal_lock(path: Path) -> AbstractContextManager[None]:
    """Serialize journal append and compaction across runtime processes."""

    lock_path = path.with_name(f".{path.name}.lock")
    return exclusive_file_lock(
        lock_path,
        timeout_sec=_JOURNAL_LOCK_WAIT_SEC,
        label=f"recovery journal {path}",
    )

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
        self._record_count = 0
        self._next_compact_at = COMPACT_AFTER_RECORDS

    def configure(self, path: Path | None, runtime_id: str | None) -> None:
        with self._lock:
            self._path = path
            self._runtime_id = runtime_id
            self._enabled = path is not None and bool(runtime_id)
            self._record_count = 0
            self._next_compact_at = COMPACT_AFTER_RECORDS
            if self._enabled:
                assert path is not None
                path.parent.mkdir(parents=True, exist_ok=True)
                records = self._load_locked()
                self._record_count = len(records)
                self._recover_previous_locked(records)
                self._maybe_compact_locked()

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
            with _interprocess_journal_lock(path):
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
            self._record_count += 1
        except (OSError, TimeoutError) as exc:
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
            if not isinstance(operation_id, str) or record_type not in {
                "begin", "result", "uncertain", "reconciliation", "acknowledged",
            }:
                continue
            if record_type == "begin":
                state[operation_id] = {"begin": record}
            elif operation_id in state:
                state[operation_id][record_type] = record
        return state

    def _recover_previous_locked(self, records: list[dict[str, Any]] | None = None) -> None:
        runtime_id = self._runtime_id
        if runtime_id is None:
            return
        if records is None:
            records = self._load_locked()
        for operation_id, item in self._state(records).items():
            begin = item.get("begin")
            if (
                not begin
                or item.get("result")
                or item.get("uncertain")
                or item.get("reconciliation")
                or item.get("acknowledged")
            ):
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

    def _compact_locked(self) -> None:
        """Drop ordinary completed history while preserving recovery evidence."""
        path = self._path
        if not self._enabled or path is None:
            return
        retained: list[dict[str, Any]] = []
        temporary = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
        try:
            with _interprocess_journal_lock(path):
                records = self._load_locked()
                for item in self._state(records).values():
                    begin = item.get("begin")
                    if begin is None or item.get("result") is not None:
                        continue
                    terminal = item.get("acknowledged") or item.get("reconciliation")
                    retained.append(begin)
                    uncertain = item.get("uncertain")
                    if uncertain is not None:
                        retained.append(uncertain)
                    if terminal is not None:
                        retained.append(terminal)
                with temporary.open("wb", buffering=0) as handle:
                    for record in retained:
                        payload = (
                            json.dumps(record, ensure_ascii=False, separators=(",", ":"), sort_keys=True) + "\n"
                        ).encode("utf-8")
                        if len(payload) > _MAX_LINE_BYTES:
                            raise ToolError(
                                "operation_journal_record_too_large",
                                "Recovery journal metadata exceeded its bounded record size.",
                            )
                        handle.write(payload)
                    os.fsync(handle.fileno())
                os.replace(temporary, path)
        except (OSError, TimeoutError) as exc:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
            raise ToolError(
                "operation_journal_unavailable",
                "Could not compact the operation recovery journal.",
            ) from exc
        self._record_count = len(retained)
        self._next_compact_at = self._record_count + COMPACT_AFTER_RECORDS

    def _maybe_compact_locked(self) -> None:
        if self._record_count < self._next_compact_at:
            return
        try:
            self._compact_locked()
        except ToolError:
            # Compaction is maintenance only. The append that triggered it is
            # already fsync'd, so never turn a known operation result into an
            # apparent failure that could encourage a duplicate retry.
            self._next_compact_at = self._record_count + COMPACT_AFTER_RECORDS

    @staticmethod
    def _pagination(offset: int, max_items: int) -> tuple[int, int]:
        if offset < 0:
            raise ToolError("invalid_offset", "offset must be zero or greater.")
        if not 1 <= max_items <= 1_000:
            raise ToolError("invalid_max_items", "max_items must be between 1 and 1000.")
        return offset, max_items

    @staticmethod
    def _operation_view(operation_id: str, item: dict[str, Any]) -> dict[str, Any]:
        begin = item["begin"]
        result = item.get("result")
        reconciliation = item.get("reconciliation")
        acknowledgment = item.get("acknowledged")
        if acknowledgment is not None:
            state = str(acknowledgment.get("state") or OperationState.ACKNOWLEDGED.value)
        elif reconciliation is not None:
            state = str(reconciliation.get("state") or OperationState.UNRESOLVABLE.value)
        elif result is not None:
            state = (
                OperationState.FAILED.value
                if result.get("known_result") in {"raised", "returned_error"}
                else OperationState.SUCCEEDED.value
            )
        elif item.get("uncertain") is not None:
            state = OperationState.UNCERTAIN.value
        else:
            state = OperationState.RUNNING.value
        evidence: list[dict[str, Any]] = []
        for record in (reconciliation, acknowledgment):
            if isinstance(record, dict) and isinstance(record.get("evidence"), dict):
                evidence.append(record["evidence"])
        view: dict[str, Any] = {
            "operation_id": operation_id,
            "operation_type": begin.get("operation_type"),
            "state": state,
            "target": begin.get("target"),
            "started_at": begin.get("started_at"),
            "runtime_id": begin.get("runtime_id"),
            "evidence": evidence,
        }
        if reconciliation is not None and isinstance(reconciliation.get("postcondition"), dict):
            view["postcondition"] = reconciliation["postcondition"]
        return view

    def list_operations(
        self,
        *,
        state: OperationState | str | None = None,
        offset: int = 0,
        max_items: int = 100,
    ) -> dict[str, Any]:
        """List durable operations without exposing command payloads."""
        offset, max_items = self._pagination(offset, max_items)
        requested_state = state.value if isinstance(state, OperationState) else state
        with self._lock:
            items = [
                self._operation_view(operation_id, item)
                for operation_id, item in self._state(self._load_locked()).items()
                if item.get("begin") is not None
            ]
        if requested_state is not None:
            items = [item for item in items if item["state"] == requested_state]
        items.sort(key=lambda item: str(item.get("started_at") or ""), reverse=True)
        total_count = len(items)
        page = items[offset:offset + max_items]
        return {
            "total_count": total_count,
            "count": len(page),
            "offset": offset,
            "has_more": offset + len(page) < total_count,
            "items": page,
        }

    def inspect(self, operation_id: str) -> dict[str, Any]:
        with self._lock:
            item = self._state(self._load_locked()).get(operation_id)
            if item is None or item.get("begin") is None:
                raise ToolError("operation_not_found", f"Operation {operation_id!r} was not found.")
            return self._operation_view(operation_id, item)

    def history(self, operation_id: str, offset: int = 0, max_items: int = 100) -> dict[str, Any]:
        offset, max_items = self._pagination(offset, max_items)
        with self._lock:
            items = [record for record in self._load_locked() if record.get("operation_id") == operation_id]
        if not items:
            raise ToolError("operation_not_found", f"Operation {operation_id!r} was not found.")
        total_count = len(items)
        page = items[offset:offset + max_items]
        return {
            "total_count": total_count,
            "count": len(page),
            "offset": offset,
            "has_more": offset + len(page) < total_count,
            "items": page,
        }

    def acknowledge(
        self,
        operation_id: str,
        state: OperationState,
        evidence: Evidence | None,
    ) -> dict[str, Any]:
        if state not in {OperationState.ACKNOWLEDGED, OperationState.UNRESOLVABLE}:
            raise ToolError("invalid_operation_state", "Acknowledgment state must be acknowledged or unresolvable.")
        if evidence is None or not evidence.source.strip() or not evidence.data:
            raise ToolError("evidence_required", "Acknowledgment requires non-empty evidence.")
        with self._lock:
            current = self._state(self._load_locked()).get(operation_id)
            if current is None or current.get("begin") is None:
                raise ToolError("operation_not_found", f"Operation {operation_id!r} was not found.")
            existing = current.get("acknowledged")
            serialized = asdict(evidence)
            if existing is not None:
                if existing.get("state") == state.value and existing.get("evidence") == serialized:
                    return self._operation_view(operation_id, current)
                raise ToolError("operation_already_acknowledged", "Operation already has a different acknowledgment.")
            record = {
                "schema_version": JOURNAL_SCHEMA_VERSION,
                "type": "acknowledged",
                "status": "completed",
                "state": state.value,
                "operation_id": operation_id,
                "runtime_id": self._runtime_id,
                "recorded_at": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
                "evidence": serialized,
            }
            self._append_locked(record)
            self._maybe_compact_locked()
            current["acknowledged"] = record
            return self._operation_view(operation_id, current)

    def record_reconciliation(
        self,
        operation_id: str,
        state: OperationState,
        postcondition: Postcondition,
        evidence: Evidence,
    ) -> dict[str, Any]:
        if state not in {OperationState.SUCCEEDED, OperationState.FAILED, OperationState.UNRESOLVABLE}:
            raise ToolError("invalid_operation_state", "Reconciliation must resolve to succeeded, failed, or unresolvable.")
        if state in {OperationState.SUCCEEDED, OperationState.FAILED} and evidence.data.get("conclusive") is not True:
            raise ToolError("conclusive_evidence_required", "A conclusive reconciliation requires evidence marked conclusive.")
        with self._lock:
            current = self._state(self._load_locked()).get(operation_id)
            if current is None or current.get("begin") is None:
                raise ToolError("operation_not_found", f"Operation {operation_id!r} was not found.")
            if current.get("result") is not None:
                raise ToolError("operation_already_completed", "Operation already has a known runtime result.")
            serialized_postcondition = asdict(postcondition)
            serialized_evidence = asdict(evidence)
            existing = current.get("reconciliation")
            if existing is not None:
                if (
                    existing.get("state") == state.value
                    and existing.get("postcondition") == serialized_postcondition
                    and existing.get("evidence") == serialized_evidence
                ):
                    return self._operation_view(operation_id, current)
                raise ToolError("operation_already_reconciled", "Operation already has different reconciliation evidence.")
            record = {
                "schema_version": JOURNAL_SCHEMA_VERSION,
                "type": "reconciliation",
                "status": "completed",
                "state": state.value,
                "operation_id": operation_id,
                "runtime_id": self._runtime_id,
                "recorded_at": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
                "postcondition": serialized_postcondition,
                "evidence": serialized_evidence,
            }
            self._append_locked(record)
            self._maybe_compact_locked()
            current["reconciliation"] = record
            return self._operation_view(operation_id, current)

    def compact(self) -> None:
        with self._lock:
            self._compact_locked()

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
            self._maybe_compact_locked()

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
                if (
                    not begin
                    or item.get("result")
                    or item.get("reconciliation")
                    or item.get("acknowledged")
                ):
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
