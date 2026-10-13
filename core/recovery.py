"""Durable metadata-only recovery journal for supervised mutating MCP tools."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import threading
import uuid
from collections.abc import Iterator, Mapping
from contextlib import AbstractContextManager, contextmanager
from contextvars import ContextVar
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from core.errors import ToolError
from core.file_lock import exclusive_file_lock
from core.recovery_models import (
    Evidence,
    ExternalOperationMetadata,
    ExternalRequestCorrelation,
    OperationState,
    Postcondition,
)

JOURNAL_SCHEMA_VERSION = 1
EXTERNAL_METADATA_VERSION = 1
COMPACT_AFTER_RECORDS = 4_096
_MAX_LINE_BYTES = 32_768
_MAX_TARGET_CHARS = 1_000
_JOURNAL_LOCK_WAIT_SEC = 10.0

RecordType = Literal["begin", "checkpoint", "result", "uncertain", "reconciliation", "acknowledged"]

_ALLOWED_EXTERNAL_KEYS = frozenset({
    "external_metadata_version",
    "extension_id",
    "provider",
    "public_name",
    "remote_name",
    "catalog_generation",
    "schema_fingerprint",
    "mutation_class",
    "policy_revision",
    "dispatch_stage",
    "external_outcome",
    "reason_code",
    "receipt_id",
    "correlation",
})
_ALLOWED_MUTATION_CLASSES = frozenset({
    "local_mutation",
    "remote_mutation",
    "destructive_local_mutation",
    "destructive_remote_mutation",
})
_ALLOWED_DISPATCH_STAGES = frozenset({"prepared", "may_dispatch"})
_ALLOWED_EXTERNAL_OUTCOMES = frozenset({
    "succeeded",
    "not_dispatched",
    "uncertain",
    "failed",
    "acknowledged",
    "unresolvable",
})
_SECRET_LIKE_PATTERN = re.compile(
    r"(?i)(?:bearer\s+\S+|ghp_[A-Za-z0-9_]{8,}|github_pat_[A-Za-z0-9_]{8,}|sk-[A-Za-z0-9_-]{8,}|"
    r"akia[0-9a-z]{8,}|xox[baprs]-[A-Za-z0-9-]{8,}|eyj[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}|"
    r"(?:token|secret|password|api[_-]?key|authorization)\s*[:=]\s*\S+)"
)


def _has_forbidden_text(value: str, *, max_len: int) -> bool:
    if not value or len(value) > max_len:
        return True
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in value):
        return True
    return bool(_SECRET_LIKE_PATTERN.search(value))


def _normalize_external_metadata(
    raw: ExternalOperationMetadata | Mapping[str, Any],
) -> dict[str, Any]:
    if isinstance(raw, ExternalOperationMetadata):
        data: dict[str, Any] = raw.as_dict()
    elif isinstance(raw, Mapping):
        data = dict(raw)
    else:
        raise ToolError(
            "invalid_external_recovery_metadata",
            "External recovery metadata must be a mapping or ExternalOperationMetadata.",
        )

    unknown = set(data) - _ALLOWED_EXTERNAL_KEYS
    if unknown:
        raise ToolError(
            "invalid_external_recovery_metadata",
            "External recovery metadata contains forbidden or unknown fields.",
        )

    version = data.get("external_metadata_version", EXTERNAL_METADATA_VERSION)
    if type(version) is not int or version != EXTERNAL_METADATA_VERSION:
        raise ToolError(
            "invalid_external_recovery_metadata",
            "Unsupported external recovery metadata version.",
        )

    extension_id = data.get("extension_id")
    remote_name = data.get("remote_name")
    public_name = data.get("public_name")
    provider = data.get("provider")
    catalog_generation = data.get("catalog_generation")
    schema_fingerprint = data.get("schema_fingerprint")
    mutation_class = data.get("mutation_class")
    dispatch_stage = data.get("dispatch_stage", "prepared")

    if (
        not isinstance(extension_id, str)
        or not re.fullmatch(r"[a-z][a-z0-9_-]{0,31}", extension_id, flags=re.ASCII)
        or not isinstance(remote_name, str)
        or not re.fullmatch(r"[A-Za-z0-9._-]{1,96}", remote_name, flags=re.ASCII)
        or not isinstance(public_name, str)
        or len(public_name) > 128
        or public_name != f"external.{extension_id}.{remote_name}"
    ):
        raise ToolError(
            "invalid_external_recovery_metadata",
            "External recovery metadata tool identity is invalid.",
        )

    if not isinstance(provider, str) or _has_forbidden_text(provider, max_len=64):
        raise ToolError(
            "invalid_external_recovery_metadata",
            "External recovery metadata provider string is invalid.",
        )

    if type(catalog_generation) is not int or catalog_generation < 1:
        raise ToolError(
            "invalid_external_recovery_metadata",
            "External recovery metadata requires a positive integer catalog_generation.",
        )

    if (
        not isinstance(schema_fingerprint, str)
        or len(schema_fingerprint) != 64
        or any(ch not in "0123456789abcdefABCDEF" for ch in schema_fingerprint)
    ):
        raise ToolError(
            "invalid_external_recovery_metadata",
            "External recovery metadata requires a 64-character hex schema_fingerprint.",
        )

    if not isinstance(mutation_class, str) or mutation_class not in _ALLOWED_MUTATION_CLASSES:
        raise ToolError(
            "invalid_external_recovery_metadata",
            "External recovery metadata requires an explicit mutating classification.",
        )

    if not isinstance(dispatch_stage, str) or dispatch_stage not in _ALLOWED_DISPATCH_STAGES:
        raise ToolError(
            "invalid_external_recovery_metadata",
            "External recovery metadata dispatch_stage is invalid.",
        )

    normalized: dict[str, Any] = {
        "external_metadata_version": EXTERNAL_METADATA_VERSION,
        "extension_id": extension_id,
        "provider": provider,
        "public_name": public_name,
        "remote_name": remote_name,
        "catalog_generation": catalog_generation,
        "schema_fingerprint": schema_fingerprint.casefold(),
        "mutation_class": mutation_class,
        "dispatch_stage": dispatch_stage,
    }

    for optional_key in ("policy_revision", "reason_code", "receipt_id"):
        val = data.get(optional_key)
        if val is not None:
            if not isinstance(val, str) or _has_forbidden_text(val, max_len=128):
                raise ToolError(
                    "invalid_external_recovery_metadata",
                    f"External recovery metadata {optional_key} is invalid.",
                )
            normalized[optional_key] = val

    external_outcome = data.get("external_outcome")
    if external_outcome is not None:
        if not isinstance(external_outcome, str) or external_outcome not in _ALLOWED_EXTERNAL_OUTCOMES:
            raise ToolError(
                "invalid_external_recovery_metadata",
                "External recovery metadata external_outcome is invalid.",
            )
        normalized["external_outcome"] = external_outcome

    correlation = data.get("correlation")
    if correlation is not None:
        if isinstance(correlation, ExternalRequestCorrelation):
            corr_dict = correlation.as_dict()
        elif isinstance(correlation, Mapping):
            corr_dict = dict(correlation)
        else:
            raise ToolError(
                "invalid_external_recovery_metadata",
                "External recovery metadata correlation is invalid.",
            )
        if (
            set(corr_dict) != {"algorithm", "key_id", "digest", "scope"}
            or corr_dict.get("algorithm") != "hmac-sha256-runtime-v1"
            or corr_dict.get("scope") != "runtime_only"
            or not isinstance(corr_dict.get("key_id"), str)
            or not re.fullmatch(r"[0-9a-f]{16}", corr_dict["key_id"])
            or not isinstance(corr_dict.get("digest"), str)
            or not re.fullmatch(r"[0-9a-f]{64}", corr_dict["digest"])
        ):
            raise ToolError(
                "invalid_external_recovery_metadata",
                "External recovery metadata correlation fields are invalid.",
            )
        normalized["correlation"] = {
            "algorithm": "hmac-sha256-runtime-v1",
            "key_id": corr_dict["key_id"],
            "digest": corr_dict["digest"],
            "scope": "runtime_only",
        }

    return normalized


def _bounded_result_postcondition(result: Any) -> dict[str, Any] | None:
    """Keep only a safe, reconciler-compatible file hash postcondition."""

    if not isinstance(result, dict):
        return None
    candidate = result.get("postcondition")
    if not isinstance(candidate, dict) or candidate.get("kind") != "file_sha256":
        return None
    expected = candidate.get("expected")
    if not isinstance(expected, dict):
        return None
    path = expected.get("path")
    digest = expected.get("sha256")
    if (
        not isinstance(path, str)
        or not path
        or len(path) > _MAX_TARGET_CHARS
        or not isinstance(digest, str)
        or len(digest) != 64
        or any(character not in "0123456789abcdefABCDEF" for character in digest)
    ):
        return None
    return {
        "kind": "file_sha256",
        "expected": {"path": path, "sha256": digest.casefold()},
    }


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


_CURRENT_OPERATION_HANDLE: ContextVar[OperationHandle | None] = ContextVar(
    "current_operation_recovery_handle",
    default=None,
)


@contextmanager
def operation_recovery_context(handle: OperationHandle | None) -> Iterator[None]:
    token = _CURRENT_OPERATION_HANDLE.set(handle)
    try:
        yield
    finally:
        _CURRENT_OPERATION_HANDLE.reset(token)


class OperationRecoveryJournal:
    """Append-only fsync journal; incomplete previous-runtime mutations become uncertain."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._path: Path | None = None
        self._runtime_id: str | None = None
        self._enabled = False
        self._record_count = 0
        self._next_compact_at = COMPACT_AFTER_RECORDS
        self._hmac_key: bytes = os.urandom(32)
        self._hmac_key_id: str = os.urandom(8).hex()

    def configure(self, path: Path | None, runtime_id: str | None) -> None:
        with self._lock:
            self._path = path
            self._runtime_id = runtime_id
            self._enabled = path is not None and bool(runtime_id)
            self._record_count = 0
            self._next_compact_at = COMPACT_AFTER_RECORDS
            self._hmac_key = os.urandom(32)
            self._hmac_key_id = os.urandom(8).hex()
            if self._enabled:
                assert path is not None
                path.parent.mkdir(parents=True, exist_ok=True)
                records = self._load_locked()
                self._record_count = len(records)
                self._recover_previous_locked(records)
                self._maybe_compact_locked()

    def correlate_external_request(
        self,
        *,
        extension_id: str,
        public_name: str,
        catalog_generation: int,
        schema_fingerprint: str,
        arguments: Mapping[str, Any] | None = None,
    ) -> ExternalRequestCorrelation:
        """Compute a runtime-scoped HMAC correlation digest without storing raw arguments."""
        canonical_payload = json.dumps(
            {
                "domain": "computerpilot.external_request.v1",
                "extension_id": extension_id,
                "public_name": public_name,
                "catalog_generation": catalog_generation,
                "schema_fingerprint": schema_fingerprint.casefold(),
                "arguments": dict(arguments) if arguments is not None else {},
            },
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
        with self._lock:
            key = self._hmac_key
            key_id = self._hmac_key_id
        digest = hmac.new(key, canonical_payload, hashlib.sha256).hexdigest()
        return ExternalRequestCorrelation(
            key_id=key_id,
            digest=digest,
            algorithm="hmac-sha256-runtime-v1",
            scope="runtime_only",
        )


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
                "begin", "checkpoint", "result", "uncertain", "reconciliation", "acknowledged",
            }:
                continue
            if record_type == "begin":
                state[operation_id] = {"begin": record}
            elif operation_id in state:
                state[operation_id][record_type] = record
        return state

    @staticmethod
    def _merged_external_metadata(item: dict[str, Any]) -> dict[str, Any] | None:
        begin = item.get("begin")
        if not isinstance(begin, dict):
            return None
        base_ext = begin.get("external")
        if not isinstance(base_ext, dict):
            return None
        merged = dict(base_ext)
        if isinstance(merged.get("correlation"), dict):
            merged["correlation"] = dict(merged["correlation"])
        for record_key in ("checkpoint", "result", "uncertain", "reconciliation", "acknowledged"):
            record = item.get(record_key)
            if not isinstance(record, dict):
                continue
            ext = record.get("external")
            if isinstance(ext, dict):
                for key, val in ext.items():
                    if key in _ALLOWED_EXTERNAL_KEYS and val is not None:
                        merged[key] = dict(val) if isinstance(val, dict) else val
            if record_key == "result" and isinstance(record.get("external_outcome"), str):
                merged["external_outcome"] = record["external_outcome"]
            elif record_key == "uncertain" and "external_outcome" not in merged:
                merged["external_outcome"] = "uncertain"
        return merged

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
            uncertain_record: dict[str, Any] = {
                "schema_version": JOURNAL_SCHEMA_VERSION,
                "type": "uncertain",
                "status": "uncertain",
                "operation_id": operation_id,
                "runtime_id": begin.get("runtime_id"),
                "marked_by_runtime_id": runtime_id,
                "marked_at": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
                "reason": "runtime_ended_without_known_result",
            }
            merged_ext = self._merged_external_metadata(item)
            if merged_ext is not None:
                merged_ext["external_outcome"] = "uncertain"
                merged_ext.setdefault("reason_code", "runtime_ended_without_known_result")
                uncertain_record["external"] = merged_ext
            self._append_locked(uncertain_record)

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
                    checkpoint = item.get("checkpoint")
                    if checkpoint is not None:
                        retained.append(checkpoint)
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

    @classmethod
    def _operation_view(cls, operation_id: str, item: dict[str, Any]) -> dict[str, Any]:
        begin = item["begin"]
        checkpoint = item.get("checkpoint")
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
        if checkpoint is not None and isinstance(checkpoint.get("postcondition"), dict):
            view["postcondition"] = checkpoint["postcondition"]
        if result is not None and isinstance(result.get("postcondition"), dict):
            view["postcondition"] = result["postcondition"]
        if reconciliation is not None and isinstance(reconciliation.get("postcondition"), dict):
            view["postcondition"] = reconciliation["postcondition"]
        external = cls._merged_external_metadata(item)
        if external is not None:
            view["external"] = external
            if isinstance(external.get("external_outcome"), str):
                view["external_outcome"] = external["external_outcome"]
        elif result is not None and isinstance(result.get("external_outcome"), str):
            view["external_outcome"] = result["external_outcome"]
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

    def begin(
        self,
        operation_type: str,
        target: str | None = None,
        *,
        external_metadata: ExternalOperationMetadata | Mapping[str, Any] | None = None,
    ) -> OperationHandle | None:
        normalized_external = (
            _normalize_external_metadata(external_metadata)
            if external_metadata is not None
            else None
        )
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
            record: dict[str, Any] = {
                "schema_version": JOURNAL_SCHEMA_VERSION,
                "type": "begin",
                "status": "in_progress",
                "operation_id": handle.operation_id,
                "runtime_id": handle.runtime_id,
                "operation_type": handle.operation_type,
                "target": handle.target,
                "started_at": handle.started_at,
                "pid": os.getpid(),
            }
            if normalized_external is not None:
                record["external"] = normalized_external
            self._append_locked(record)
            return handle

    def checkpoint_postcondition(
        self,
        handle: OperationHandle | None,
        postcondition: dict[str, Any],
    ) -> None:
        if handle is None:
            return
        bounded = _bounded_result_postcondition({"postcondition": postcondition})
        if bounded is None:
            raise ToolError(
                "invalid_recovery_postcondition",
                "Recovery checkpoint requires a bounded file_sha256 postcondition.",
            )
        with self._lock:
            current = self._state(self._load_locked()).get(handle.operation_id)
            if current is None or current.get("begin") is None:
                raise ToolError("operation_not_found", f"Operation {handle.operation_id!r} was not found.")
            if current.get("result") is not None:
                raise ToolError("operation_already_completed", "Operation already has a known runtime result.")
            existing = current.get("checkpoint")
            if existing is not None:
                if existing.get("postcondition") == bounded:
                    return
                raise ToolError(
                    "operation_checkpoint_conflict",
                    "Operation already has a different recovery checkpoint.",
                )
            self._append_locked({
                "schema_version": JOURNAL_SCHEMA_VERSION,
                "type": "checkpoint",
                "status": "in_progress",
                "operation_id": handle.operation_id,
                "runtime_id": handle.runtime_id,
                "recorded_at": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
                "postcondition": bounded,
            })
            self._maybe_compact_locked()

    def finish(
        self,
        handle: OperationHandle | None,
        *,
        known_result: str,
        postcondition: dict[str, Any] | None = None,
    ) -> None:
        if handle is None:
            return
        with self._lock:
            record: dict[str, Any] = {
                "schema_version": JOURNAL_SCHEMA_VERSION,
                "type": "result",
                "status": "completed",
                "operation_id": handle.operation_id,
                "runtime_id": handle.runtime_id,
                "finished_at": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
                "known_result": known_result[:100],
            }
            if postcondition is not None:
                record["postcondition"] = postcondition
            self._append_locked(record)
            self._maybe_compact_locked()

    def finish_returned(self, handle: OperationHandle | None, result: Any) -> None:
        if isinstance(result, dict) and isinstance(result.get("ok"), bool):
            known_result = "ok" if result["ok"] else "returned_error"
        else:
            known_result = "returned"
        self.finish(
            handle,
            known_result=known_result,
            postcondition=_bounded_result_postcondition(result),
        )

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
                entry: dict[str, Any] = {
                    "operation_id": operation_id,
                    "status": "uncertain",
                    "operation_type": begin.get("operation_type"),
                    "target": begin.get("target"),
                    "started_at": begin.get("started_at"),
                    "marked_at": marker.get("marked_at"),
                    "reason": marker.get("reason"),
                }
                external = self._merged_external_metadata(item)
                if external is not None:
                    entry["external"] = external
                uncertain.append(entry)
            uncertain.sort(key=lambda item: str(item.get("marked_at") or ""), reverse=True)
            return {
                "enabled": True,
                "uncertain_count": len(uncertain),
                "pending_count": pending_count,
                "uncertain": uncertain[:max_items],
            }


OPERATION_RECOVERY = OperationRecoveryJournal()


def checkpoint_current_postcondition(postcondition: dict[str, Any]) -> None:
    """Durably checkpoint the current mutation's file hash before its side effect."""

    OPERATION_RECOVERY.checkpoint_postcondition(_CURRENT_OPERATION_HANDLE.get(), postcondition)
