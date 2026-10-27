"""Durable metadata-only recovery journal for supervised mutating MCP tools."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import re
import threading
import time
import uuid
from collections.abc import Callable, Iterator, Mapping
from contextlib import AbstractContextManager, contextmanager, nullcontext
from contextvars import ContextVar
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal, TypeVar

from core.errors import ToolError
from core.file_lock import exclusive_file_lock
from core.recovery_models import (
    Evidence,
    ExternalOperationMetadata,
    ExternalRequestCorrelation,
    NativeUIATarget,
    OperationState,
    Postcondition,
)

JOURNAL_SCHEMA_VERSION = 1
EXTERNAL_METADATA_VERSION = 1
COMPACT_AFTER_RECORDS = 4_096
MAX_UNRESOLVED_EXTERNAL_OPERATIONS = 128
MAX_UNRESOLVED_WORKSPACE_EDITS = 64
MAX_EXTERNAL_TERMINAL_HISTORY = 1_000
MAX_EXTERNAL_TERMINAL_DAYS = 30
_MAX_LINE_BYTES = 32_768
_MAX_TARGET_CHARS = 1_000
_MAX_WORKSPACE_EDIT_FILES = 100
_MAX_WORKSPACE_EVIDENCE_BYTES = 32 * 1024
_MAX_WORKSPACE_PROTECTED_PATHS = 10_000
_WORKSPACE_EDIT_EVIDENCE_VERSION = 1
_WORKSPACE_EDIT_STAGES = frozenset({"prepared", "may_dispatch"})
_SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")
_JOURNAL_LOCK_WAIT_SEC = 10.0
_EXTERNAL_DISPATCH_DEADLINE: ContextVar[float | None] = ContextVar("external_dispatch_deadline", default=None)


@contextmanager
def external_dispatch_deadline(deadline: float) -> Iterator[None]:
    token = _EXTERNAL_DISPATCH_DEADLINE.set(deadline)
    try:
        yield
    finally:
        _EXTERNAL_DISPATCH_DEADLINE.reset(token)

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
_NATIVE_UIA_OPERATIONS = frozenset({"ui_invoke", "ui_set_value", "ui_select"})
_NATIVE_UIA_DISPATCH_PROTOCOL_VERSION = 1
_NATIVE_UIA_DISPATCH_RULE = "may_dispatch_checkpoint_before_effect"
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


def uses_native_uia_dispatch_protocol(operation: str) -> bool:
    """Return whether a tool must checkpoint native UIA identity before any effect."""

    return operation in _NATIVE_UIA_OPERATIONS


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
        timeout_sec=min(_JOURNAL_LOCK_WAIT_SEC, max(0.0, deadline - time.monotonic()))
        if (deadline := _EXTERNAL_DISPATCH_DEADLINE.get()) is not None else _JOURNAL_LOCK_WAIT_SEC,
        label=f"recovery journal {path}",
    )

@dataclass(frozen=True, slots=True)
class OperationHandle:
    operation_id: str
    runtime_id: str
    operation_type: str
    target: str | None
    started_at: str


@dataclass(frozen=True, slots=True)
class WorkspaceEditFile:
    path: str
    before_sha256: str
    after_sha256: str
    backup_path: str


@dataclass(frozen=True, slots=True)
class WorkspaceEditEvidence:
    files: tuple[WorkspaceEditFile, ...]


@dataclass(slots=True)
class _OperationRecoveryScope:
    handle: OperationHandle | None
    owner: tuple[int, int | None]
    active: bool = True
    rolled_back: bool = False
    parent: _OperationRecoveryScope | None = None
    native_test_dispatch: threading.Event = field(default_factory=threading.Event)


def _execution_owner() -> tuple[int, int | None]:
    try:
        task = asyncio.current_task()
    except RuntimeError:
        task = None
    return threading.get_ident(), id(task) if task is not None else None


_CURRENT_OPERATION_SCOPE: ContextVar[_OperationRecoveryScope | None] = ContextVar(
    "current_operation_recovery_scope",
    default=None,
)
_T = TypeVar("_T")


def _scope_live(scope: _OperationRecoveryScope) -> bool:
    current: _OperationRecoveryScope | None = scope
    while current is not None:
        if not current.active:
            return False
        current = current.parent
    return True


@contextmanager
def operation_recovery_context(
    handle: OperationHandle | None, *, _parent: _OperationRecoveryScope | None = None,
) -> Iterator[_OperationRecoveryScope]:
    scope = _OperationRecoveryScope(handle, _execution_owner(), parent=_parent)
    token = _CURRENT_OPERATION_SCOPE.set(scope)
    try:
        yield scope
    finally:
        scope.active = False
        _CURRENT_OPERATION_SCOPE.reset(token)


def bind_operation_recovery(call: Callable[[], _T]) -> Callable[[], _T]:
    """Explicitly transfer the current owning call's recovery context to a worker."""
    scope = _CURRENT_OPERATION_SCOPE.get()
    if scope is None or not _scope_live(scope) or scope.owner != _execution_owner():
        return call

    def run() -> _T:
        if not _scope_live(scope):
            raise ToolError("invalid_operation_handle", "The enclosing recovery operation has finished.")
        with operation_recovery_context(scope.handle, _parent=scope):
            return call()

    return run


def native_test_dispatched() -> bool:
    scope = _CURRENT_OPERATION_SCOPE.get()
    if scope is None or not _scope_live(scope) or scope.owner != _execution_owner():
        raise ToolError("invalid_operation_handle", "Native test dispatch requires a live owning recovery scope.")
    return scope.native_test_dispatch.is_set()


def mark_native_test_dispatch() -> None:
    """Record possible dispatch in this owned attempt and its explicitly bound ancestors."""
    native_test_dispatched()
    scope = _CURRENT_OPERATION_SCOPE.get()
    while scope is not None:
        scope.native_test_dispatch.set()
        scope = scope.parent
    native_test_dispatched()


def run_native_operation(operation: str, target: str | None, call: Callable[[], _T]) -> _T:
    """Guard native verification while retaining an enclosing mutation's admission."""
    from core.lifecycle import RUNTIME_LIFECYCLE

    parent = _CURRENT_OPERATION_SCOPE.get()
    reuse = parent is not None and parent.owner == _execution_owner()
    if reuse and parent is not None and not _scope_live(parent):
        raise ToolError("invalid_operation_handle", "The enclosing recovery operation has finished.")
    with nullcontext() if reuse else RUNTIME_LIFECYCLE.mutation(operation):
        handle = parent.handle if reuse and parent is not None else OPERATION_RECOVERY.begin(operation, target)
        with operation_recovery_context(handle, _parent=parent if reuse else None) as current:
            try:
                result = call()
            except BaseException:
                try:
                    if handle is not None and handle.operation_type == "verify_changes" and current.native_test_dispatch.is_set():
                        OPERATION_RECOVERY.mark_uncertain(handle, "native_test_execution_outcome_unknown")
                    elif not reuse:
                        OPERATION_RECOVERY.finish(handle, known_result="raised")
                except Exception:
                    pass  # Leave the begin pending if uncertain settlement cannot be persisted.
                raise
        if not reuse:
            OPERATION_RECOVERY.finish_returned(handle, result)
        return result


def _parse_iso_utc(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except ValueError:
        return None


def _native_uia_metadata(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict) or set(value) != {
        "native_metadata_version", "kind", "dispatch_stage", "target",
    }:
        return None
    if (
        type(value["native_metadata_version"]) is not int or value["native_metadata_version"] != 1
        or value["kind"] != "windows_uia" or value["dispatch_stage"] not in ("prepared", "may_dispatch")
    ):
        return None
    raw = value["target"]
    if not isinstance(raw, dict) or set(raw) != {
        "pid", "process_created", "window_handle", "element_handle", "element_runtime_id", "identity_status",
    } or not isinstance(raw["element_runtime_id"], list):
        return None
    try:
        target = NativeUIATarget(
            pid=raw["pid"], process_created=raw["process_created"],
            window_handle=raw["window_handle"], element_handle=raw["element_handle"],
            element_runtime_id=tuple(raw["element_runtime_id"]),
        )
    except (TypeError, ValueError, OverflowError):
        return None
    return {**value, "target": target.as_dict()}


def _native_uia_view(checkpoint: dict[str, Any]) -> dict[str, Any]:
    native = _native_uia_metadata(checkpoint.get("native"))
    if native is None:
        return {}
    identity = dict(native["target"])
    identity.pop("identity_status")
    identity["element_runtime_id"] = tuple(identity["element_runtime_id"])
    return {"native": native, "target": NativeUIATarget(**identity).label()}


def _workspace_path(value: Any) -> str | None:
    if not isinstance(value, str) or not value or "\x00" in value or len(value) > 32_767:
        return None
    candidate = Path(value)
    if not candidate.is_absolute():
        return None
    return str(candidate.resolve(strict=False))


def _workspace_edit_metadata(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict) or set(value) != {"evidence_version", "dispatch_stage", "files"}:
        return None
    stage = value["dispatch_stage"]
    if (
        type(value["evidence_version"]) is not int
        or value["evidence_version"] != _WORKSPACE_EDIT_EVIDENCE_VERSION
        or not isinstance(stage, str)
        or stage not in _WORKSPACE_EDIT_STAGES
    ):
        return None
    raw_files = value["files"]
    if not isinstance(raw_files, list) or not 1 <= len(raw_files) <= _MAX_WORKSPACE_EDIT_FILES:
        return None
    files: list[dict[str, str]] = []
    for item in raw_files:
        if not isinstance(item, dict) or set(item) != {"path", "before_sha256", "after_sha256", "backup_path"}:
            return None
        path = _workspace_path(item["path"])
        backup_path = _workspace_path(item["backup_path"])
        before = item["before_sha256"]
        after = item["after_sha256"]
        if (
            path is None or backup_path is None
            or not isinstance(before, str) or not _SHA256_HEX.fullmatch(before)
            or not isinstance(after, str) or not _SHA256_HEX.fullmatch(after)
        ):
            return None
        files.append({
            "path": path,
            "before_sha256": before,
            "after_sha256": after,
            "backup_path": backup_path,
        })
    return {
        "evidence_version": _WORKSPACE_EDIT_EVIDENCE_VERSION,
        "dispatch_stage": value["dispatch_stage"],
        "files": files,
    }


def _workspace_edit_record(evidence: WorkspaceEditEvidence, stage: str) -> dict[str, Any]:
    if stage not in _WORKSPACE_EDIT_STAGES:
        raise ToolError("invalid_workspace_edit_evidence", "Invalid workspace edit recovery stage.")
    if not isinstance(evidence, WorkspaceEditEvidence):
        raise ToolError("invalid_workspace_edit_evidence", "Invalid workspace edit evidence.")
    files: list[dict[str, str]] = []
    for item in evidence.files:
        if not isinstance(item, WorkspaceEditFile):
            raise ToolError("invalid_workspace_edit_evidence", "Invalid workspace edit evidence.")
        path = _workspace_path(item.path)
        backup_path = _workspace_path(item.backup_path)
        if (
            path is None or backup_path is None
            or not isinstance(item.before_sha256, str) or not _SHA256_HEX.fullmatch(item.before_sha256)
            or not isinstance(item.after_sha256, str) or not _SHA256_HEX.fullmatch(item.after_sha256)
        ):
            raise ToolError("invalid_workspace_edit_evidence", "Invalid workspace edit evidence.")
        files.append({
            "path": path,
            "before_sha256": item.before_sha256,
            "after_sha256": item.after_sha256,
            "backup_path": backup_path,
        })
    if not 1 <= len(files) <= _MAX_WORKSPACE_EDIT_FILES:
        raise ToolError("invalid_workspace_edit_evidence", "Workspace edit evidence file count is out of bounds.")
    record = {
        "evidence_version": _WORKSPACE_EDIT_EVIDENCE_VERSION,
        "dispatch_stage": stage,
        "files": files,
    }
    payload = json.dumps(record, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
    if len(payload) > _MAX_WORKSPACE_EVIDENCE_BYTES:
        raise ToolError("lsp_edit_evidence_too_large", "Workspace edit recovery evidence exceeded 32 KiB.")
    return record


def _workspace_edit_view(checkpoint: dict[str, Any]) -> dict[str, Any]:
    workspace = _workspace_edit_metadata(checkpoint.get("workspace_edit"))
    if workspace is None:
        return {}
    return {"workspace_edit": workspace}


def validate_workspace_edit_evidence(evidence: WorkspaceEditEvidence) -> None:
    """Reject unbounded or malformed workspace evidence before any backup or write."""
    _workspace_edit_record(evidence, "prepared")


def mark_workspace_edit_rolled_back() -> None:
    """Record that the active workspace edit restored every file it wrote."""
    scope = _CURRENT_OPERATION_SCOPE.get()
    if scope is not None:
        scope.rolled_back = True


class OperationRecoveryJournal:
    """Append-only fsync journal; incomplete previous-runtime mutations become uncertain."""

    def __init__(
        self,
        *,
        max_unresolved_external: int = MAX_UNRESOLVED_EXTERNAL_OPERATIONS,
        max_unresolved_workspace: int = MAX_UNRESOLVED_WORKSPACE_EDITS,
        max_external_terminal_history: int = MAX_EXTERNAL_TERMINAL_HISTORY,
        max_external_terminal_days: int = MAX_EXTERNAL_TERMINAL_DAYS,
    ) -> None:
        self._lock = threading.RLock()
        self._path: Path | None = None
        self._runtime_id: str | None = None
        self._enabled = False
        self._record_count = 0
        self._next_compact_at = COMPACT_AFTER_RECORDS
        self._hmac_key: bytes = os.urandom(32)
        self._hmac_key_id: str = os.urandom(8).hex()
        self._max_unresolved_external = max_unresolved_external
        self._max_unresolved_workspace = max_unresolved_workspace
        self._max_external_terminal_history = max_external_terminal_history
        self._max_external_terminal_days = max_external_terminal_days
        self._cached_stat_key: tuple[int, int] | None = None
        self._cached_unresolved_external_count: int = 0

    def configure(self, path: Path | None, runtime_id: str | None) -> None:
        with self._lock:
            self._path = path
            self._runtime_id = runtime_id
            self._enabled = path is not None and bool(runtime_id)
            self._record_count = 0
            self._next_compact_at = COMPACT_AFTER_RECORDS
            self._hmac_key = os.urandom(32)
            self._hmac_key_id = os.urandom(8).hex()
            self._cached_stat_key = None
            self._cached_unresolved_external_count = 0
            if self._enabled:
                assert path is not None
                path.parent.mkdir(parents=True, exist_ok=True)
                with self._journal_transaction() as records:
                    if path.exists():
                        records = list(self._iter_records_locked(strict=True))
                        self._validate_workspace_records(records)
                    self._record_count = len(records)
                    self._recover_previous_unlocked(records)
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
        # Admission hint; durable methods recheck configuration while holding the journal lock.
        return self._enabled

    @property
    def external_mutation_limit(self) -> int:
        return self._max_unresolved_external

    @staticmethod
    def _is_unresolved_external_item(item: Mapping[str, Any]) -> bool:
        begin = item.get("begin")
        if not isinstance(begin, dict) or not isinstance(begin.get("external"), dict):
            return False
        return (
            item.get("result") is None
            and item.get("reconciliation") is None
            and item.get("acknowledged") is None
        )

    def unresolved_external_count(self) -> int:
        with self._lock:
            if not self._enabled or self._path is None:
                return 0
            try:
                st = self._path.stat()
                stat_key = (st.st_mtime_ns, st.st_size)
            except OSError:
                self._cached_stat_key = None
                self._cached_unresolved_external_count = 0
                return 0
            if stat_key == self._cached_stat_key:
                return self._cached_unresolved_external_count
            state = self._read_state_locked(unresolved_external_only=True)
            count = sum(1 for item in state.values() if self._is_unresolved_external_item(item))
            self._cached_stat_key = stat_key
            self._cached_unresolved_external_count = count
            return count

    def has_external_mutation_capacity(self) -> bool:
        if not self._lock.acquire(blocking=False):
            return False
        try:
            if not self._enabled or self._path is None:
                return False
            try:
                return self.unresolved_external_count() < self._max_unresolved_external
            except ToolError as exc:
                if exc.code == "operation_limit":
                    return False
                raise
        finally:
            self._lock.release()

    @staticmethod
    def _is_unresolved_workspace_item(item: Mapping[str, Any]) -> bool:
        begin = item.get("begin")
        if not isinstance(begin, dict):
            return False
        if item.get("result") is not None or item.get("reconciliation") is not None or item.get("acknowledged") is not None:
            return False
        checkpoint = item.get("checkpoint")
        if isinstance(checkpoint, dict) and "workspace_edit" in checkpoint:
            return True
        return begin.get("operation_type") == "workspace_edit"

    def unresolved_workspace_count(self) -> int:
        with self._lock:
            if not self._enabled or self._path is None:
                return 0
            state = self._state(self._load_locked())
            return sum(1 for item in state.values() if self._is_unresolved_workspace_item(item))

    def has_workspace_edit_capacity(self) -> bool:
        if not self._lock.acquire(blocking=False):
            return False
        try:
            if not self._enabled or self._path is None:
                return False
            return self.unresolved_workspace_count() < self._max_unresolved_workspace
        finally:
            self._lock.release()

    def ensure_workspace_edit_capacity(self) -> None:
        if not self._enabled:
            return
        if not self.has_workspace_edit_capacity():
            raise ToolError(
                "workspace_edit_limit",
                f"Unresolved workspace-edit limit ({self._max_unresolved_workspace}) reached; reconcile before retrying.",
            )

    def _append_unlocked_file(self, record: dict[str, Any]) -> None:
        path = self._path
        if not self._enabled or path is None:
            return
        payload = (json.dumps(record, ensure_ascii=False, separators=(",", ":"), sort_keys=True) + "\n").encode("utf-8")
        if len(payload) > _MAX_LINE_BYTES:
            raise ToolError("operation_journal_record_too_large", "Recovery journal metadata exceeded its bounded record size.")
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
        self._cached_stat_key = None

    def _append_locked(self, record: dict[str, Any]) -> None:
        path = self._path
        if not self._enabled or path is None:
            return
        try:
            with _interprocess_journal_lock(path):
                self._append_unlocked_file(record)
        except ToolError:
            raise
        except (OSError, TimeoutError) as exc:
            raise ToolError(
                "operation_journal_unavailable",
                "Could not durably update the operation recovery journal.",
                hint="Do not retry a possibly-started mutation blindly; inspect runtime storage first.",
            ) from exc

    @contextmanager
    def _journal_transaction(
        self, *, operation_id: str | None = None, external_admission: bool = False,
    ) -> Iterator[list[dict[str, Any]]]:
        path = self._path
        if not self._enabled or path is None:
            raise ToolError("recovery_unavailable", "Operation recovery journal is not enabled.")
        try:
            with _interprocess_journal_lock(path):
                if operation_id is not None or external_admission:
                    state = self._read_state_locked(operation_id=operation_id, unresolved_external_only=external_admission)
                    if (deadline := _EXTERNAL_DISPATCH_DEADLINE.get()) is not None and time.monotonic() >= deadline:
                        raise TimeoutError("External dispatch journal deadline expired.")
                    yield [record for item in state.values() for record in item.values()]
                else:
                    yield self._load_locked()
        except ToolError:
            raise
        except (OSError, TimeoutError) as exc:
            raise ToolError(
                "operation_journal_unavailable",
                "Could not durably update the operation recovery journal.",
                hint="Do not retry a possibly-started mutation blindly; inspect runtime storage first.",
            ) from exc

    def _iter_records_locked(self, *, strict: bool = False) -> Iterator[dict[str, Any]]:
        path = self._path
        if path is None or not path.is_file():
            if strict:
                raise ToolError("operation_journal_unavailable", "Could not read the operation recovery journal.")
            return
        try:
            with path.open("rb") as handle:
                while raw := handle.readline(_MAX_LINE_BYTES + 1):
                    if len(raw) > _MAX_LINE_BYTES:
                        if strict:
                            raise ToolError("operation_journal_invalid", "The operation recovery journal contains an invalid record.")
                        while not raw.endswith(b"\n"):
                            raw = handle.readline(_MAX_LINE_BYTES + 1)
                            if not raw:
                                break
                        continue
                    try:
                        record = json.loads(raw.decode("utf-8"))
                    except (UnicodeDecodeError, json.JSONDecodeError):
                        # A crash can leave only the final append truncated.
                        if strict:
                            raise ToolError(
                                "operation_journal_invalid", "The operation recovery journal contains an invalid record.",
                            ) from None
                        continue
                    if isinstance(record, dict) and record.get("schema_version") == JOURNAL_SCHEMA_VERSION:
                        if strict and not raw.endswith(b"\n"):
                            raise ToolError("operation_journal_invalid", "The operation recovery journal contains an incomplete record.")
                        yield record
                    elif strict:
                        raise ToolError("operation_journal_invalid", "The operation recovery journal contains an invalid record.")
        except OSError as exc:
            raise ToolError(
                "operation_journal_unavailable",
                "Could not read the operation recovery journal.",
            ) from exc

    def _load_locked(self) -> list[dict[str, Any]]:
        return list(self._iter_records_locked())

    def _read_state_locked(
        self, *, operation_id: str | None = None, unresolved_external_only: bool = False,
    ) -> dict[str, dict[str, Any]]:
        state: dict[str, dict[str, Any]] = {}
        for record in self._iter_records_locked():
            op_id = record.get("operation_id")
            kind = record.get("type")
            if not isinstance(op_id, str) or (operation_id is not None and op_id != operation_id):
                continue
            if kind == "begin":
                if unresolved_external_only and not isinstance(record.get("external"), dict):
                    continue
                state[op_id] = {"begin": record}
            elif op_id in state and kind in {"checkpoint", "result", "uncertain", "reconciliation", "acknowledged"}:
                if unresolved_external_only and kind in {"result", "reconciliation", "acknowledged"}:
                    state.pop(op_id)
                else:
                    state[op_id][kind] = record
            if unresolved_external_only and len(state) > self._max_unresolved_external:
                raise ToolError("operation_limit", "Retained external operation admission exceeds the current quota.")
        return state

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
            elif record_key in {"reconciliation", "acknowledged"} and isinstance(record.get("state"), str):
                merged["external_outcome"] = record["state"]
            elif record_key == "uncertain" and "external_outcome" not in merged:
                merged["external_outcome"] = "uncertain"
        return merged

    def _recover_previous_unlocked(self, records: list[dict[str, Any]]) -> None:
        runtime_id = self._runtime_id
        if runtime_id is None:
            return
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
            checkpoint = item.get("checkpoint")
            workspace_raw_present = isinstance(checkpoint, dict) and "workspace_edit" in checkpoint
            workspace = (
                _workspace_edit_metadata(checkpoint.get("workspace_edit"))
                if isinstance(checkpoint, dict)
                else None
            )
            workspace_before_dispatch = (
                begin.get("operation_type") == "workspace_edit"
                and (checkpoint is None or (isinstance(checkpoint, dict) and not workspace_raw_present))
            ) or (
                workspace is not None and workspace["dispatch_stage"] == "prepared"
            )
            if workspace_before_dispatch:
                self._append_unlocked_file({
                    "schema_version": JOURNAL_SCHEMA_VERSION,
                    "type": "result",
                    "status": "completed",
                    "operation_id": operation_id,
                    "runtime_id": begin.get("runtime_id"),
                    "recovered_by_runtime_id": runtime_id,
                    "finished_at": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
                    "known_result": "returned_error",
                    "reason": "runtime_ended_before_workspace_edit_dispatch",
                })
                continue
            native_protocol = begin.get("native_recovery")
            if (
                begin.get("operation_type") in _NATIVE_UIA_OPERATIONS
                and native_protocol == {
                    "version": _NATIVE_UIA_DISPATCH_PROTOCOL_VERSION,
                    "kind": "windows_uia",
                    "dispatch_rule": _NATIVE_UIA_DISPATCH_RULE,
                }
            ):
                checkpoint = item.get("checkpoint")
                native_raw_present = isinstance(checkpoint, dict) and "native" in checkpoint
                native = (
                    _native_uia_metadata(checkpoint.get("native"))
                    if isinstance(checkpoint, dict)
                    else None
                )
                before_dispatch = (
                    checkpoint is None
                    or (not native_raw_present and isinstance(checkpoint, dict))
                    or (native is not None and native["dispatch_stage"] == "prepared")
                )
                if before_dispatch:
                    self._append_unlocked_file({
                        "schema_version": JOURNAL_SCHEMA_VERSION,
                        "type": "result",
                        "status": "completed",
                        "operation_id": operation_id,
                        "runtime_id": begin.get("runtime_id"),
                        "recovered_by_runtime_id": runtime_id,
                        "finished_at": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
                        "known_result": "returned_error",
                        "reason": "runtime_ended_before_native_uia_dispatch",
                    })
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
            self._append_unlocked_file(uncertain_record)

    def _recover_previous_locked(self, records: list[dict[str, Any]] | None = None) -> None:
        if not self._enabled or self._path is None:
            return
        if records is not None:
            try:
                with _interprocess_journal_lock(self._path):
                    self._recover_previous_unlocked(records)
            except (OSError, TimeoutError) as exc:
                raise ToolError(
                    "operation_journal_unavailable",
                    "Could not durably update the operation recovery journal.",
                ) from exc
            return
        with self._journal_transaction() as loaded:
            self._recover_previous_unlocked(loaded)

    def _compact_locked(self) -> None:
        """Drop ordinary completed history and bound resolved external history while preserving all unresolved and native evidence."""
        path = self._path
        if not self._enabled or path is None:
            return
        retained: list[dict[str, Any]] = []
        temporary = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
        try:
            with _interprocess_journal_lock(path):
                if not path.exists():
                    return
                records = list(self._iter_records_locked(strict=True))
                self._validate_workspace_records(records)
                state = self._state(records)
                now = datetime.now(timezone.utc)
                max_age = timedelta(days=max(0, self._max_external_terminal_days))

                external_terminal_candidates: list[tuple[str, int, str]] = []
                for idx, (op_id, item) in enumerate(state.items()):
                    begin = item.get("begin")
                    if begin is None or item.get("result") is not None:
                        continue
                    terminal = item.get("acknowledged") or item.get("reconciliation")
                    if terminal is None:
                        # Unresolved operations (running or uncertain) are NEVER evicted.
                        continue
                    if not isinstance(begin.get("external"), dict):
                        # Native resolved operations are always preserved.
                        continue
                    ts = _parse_iso_utc(terminal.get("recorded_at")) or _parse_iso_utc(begin.get("started_at"))
                    if ts is not None and (now - ts) > max_age:
                        continue
                    ts_key = str(terminal.get("recorded_at") or begin.get("started_at") or "")
                    external_terminal_candidates.append((ts_key, idx, op_id))

                if self._max_external_terminal_history <= 0:
                    allowed_external_terminal_ids: set[str] = set()
                elif len(external_terminal_candidates) > self._max_external_terminal_history:
                    sorted_candidates = sorted(external_terminal_candidates, key=lambda entry: (entry[0], entry[1]))
                    allowed_external_terminal_ids = {
                        entry[2] for entry in sorted_candidates[-self._max_external_terminal_history:]
                    }
                else:
                    allowed_external_terminal_ids = {entry[2] for entry in external_terminal_candidates}

                for op_id, item in state.items():
                    begin = item.get("begin")
                    if begin is None or item.get("result") is not None:
                        continue
                    terminal = item.get("acknowledged") or item.get("reconciliation")
                    if (
                        terminal is not None
                        and isinstance(begin.get("external"), dict)
                        and op_id not in allowed_external_terminal_ids
                    ):
                        continue
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
        self._cached_stat_key = None

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
        view.update(_native_uia_view(checkpoint or {}))
        view.update(_workspace_edit_view(checkpoint or {}))
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
            item = self._read_state_locked(operation_id=operation_id).get(operation_id)
            if item is None or item.get("begin") is None:
                raise ToolError("operation_not_found", f"Operation {operation_id!r} was not found.")
            return self._operation_view(operation_id, item)

    def history(self, operation_id: str, offset: int = 0, max_items: int = 100) -> dict[str, Any]:
        offset, max_items = self._pagination(offset, max_items)
        with self._lock:
            items = [record for record in self._iter_records_locked() if record.get("operation_id") == operation_id]
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
            with self._journal_transaction(operation_id=operation_id) as records:
                current = self._state(records).get(operation_id)
                if current is None or current.get("begin") is None:
                    raise ToolError("operation_not_found", f"Operation {operation_id!r} was not found.")
                if current.get("result") is not None:
                    raise ToolError("operation_already_completed", "Operation already has a known runtime result.")
                if current.get("reconciliation") is not None:
                    raise ToolError("operation_already_reconciled", "Operation already has reconciliation evidence.")
                existing = current.get("acknowledged")
                serialized = asdict(evidence)
                if existing is not None:
                    if existing.get("state") == state.value and existing.get("evidence") == serialized:
                        return self._operation_view(operation_id, current)
                    raise ToolError("operation_already_acknowledged", "Operation already has a different acknowledgment.")
                record: dict[str, Any] = {
                    "schema_version": JOURNAL_SCHEMA_VERSION,
                    "type": "acknowledged",
                    "status": "completed",
                    "state": state.value,
                    "operation_id": operation_id,
                    "runtime_id": self._runtime_id,
                    "recorded_at": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
                    "evidence": serialized,
                }
                merged_ext = self._merged_external_metadata(current)
                if merged_ext is not None:
                    merged_ext["external_outcome"] = state.value
                    record["external"] = merged_ext
                self._append_unlocked_file(record)
                current["acknowledged"] = record
            self._maybe_compact_locked()
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
            with self._journal_transaction(operation_id=operation_id) as records:
                current = self._state(records).get(operation_id)
                if current is None or current.get("begin") is None:
                    raise ToolError("operation_not_found", f"Operation {operation_id!r} was not found.")
                if current.get("result") is not None:
                    raise ToolError("operation_already_completed", "Operation already has a known runtime result.")
                if current.get("acknowledged") is not None:
                    raise ToolError("operation_already_acknowledged", "Operation is already acknowledged.")
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
                record: dict[str, Any] = {
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
                merged_ext = self._merged_external_metadata(current)
                if merged_ext is not None:
                    merged_ext["external_outcome"] = state.value
                    record["external"] = merged_ext
                self._append_unlocked_file(record)
                current["reconciliation"] = record
            self._maybe_compact_locked()
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
        native_uia_protocol: bool = False,
    ) -> OperationHandle | None:
        if native_uia_protocol and not uses_native_uia_dispatch_protocol(operation_type):
            raise ToolError(
                "invalid_native_recovery_metadata",
                "Native UIA dispatch protocol can only be enabled for native UIA mutations.",
            )
        normalized_external = (
            _normalize_external_metadata(external_metadata)
            if external_metadata is not None
            else None
        )
        with self._lock:
            if not self._enabled:
                if normalized_external is not None:
                    raise ToolError(
                        "recovery_unavailable",
                        "Operation recovery journal must be enabled for external mutations.",
                    )
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
            if native_uia_protocol:
                record["native_recovery"] = {
                    "version": _NATIVE_UIA_DISPATCH_PROTOCOL_VERSION,
                    "kind": "windows_uia",
                    "dispatch_rule": _NATIVE_UIA_DISPATCH_RULE,
                }
            if normalized_external is not None:
                record["external"] = normalized_external
                with self._journal_transaction(external_admission=True) as records:
                    state = self._state(records)
                    unresolved_external = sum(
                        1 for item in state.values() if self._is_unresolved_external_item(item)
                    )
                    if unresolved_external >= self._max_unresolved_external:
                        raise ToolError(
                            "operation_limit",
                            f"Unresolved external operation limit ({self._max_unresolved_external}) reached; "
                            "reconcile or acknowledge uncertain operations before starting new external mutations.",
                        )
                    self._append_unlocked_file(record)
                return handle
            self._append_locked(record)
            return handle

    def checkpoint_external_dispatch(self, handle: OperationHandle | None) -> None:
        if handle is None:
            raise ToolError(
                "recovery_unavailable",
                "Operation recovery handle is required before external mutation dispatch.",
            )
        with self._lock:
            if not self._enabled:
                raise ToolError(
                    "recovery_unavailable",
                    "Operation recovery journal must be enabled before external mutation dispatch.",
                )
            with self._journal_transaction(operation_id=handle.operation_id) as records:
                current = self._state(records).get(handle.operation_id)
                if current is None or current.get("begin") is None:
                    raise ToolError("operation_not_found", f"Operation {handle.operation_id!r} was not found.")
                if (
                    current.get("result") is not None
                    or current.get("uncertain") is not None
                    or current.get("reconciliation") is not None
                    or current.get("acknowledged") is not None
                ):
                    raise ToolError(
                        "operation_already_completed",
                        "Operation already has a terminal or uncertain state.",
                    )
                merged_ext = self._merged_external_metadata(current)
                if merged_ext is None:
                    raise ToolError(
                        "invalid_external_recovery_metadata",
                        "checkpoint_external_dispatch requires an external operation.",
                    )
                existing = current.get("checkpoint")
                if existing is not None:
                    existing_ext = existing.get("external")
                    if isinstance(existing_ext, dict) and existing_ext.get("dispatch_stage") == "may_dispatch":
                        return
                    raise ToolError(
                        "operation_checkpoint_conflict",
                        "Operation already has a conflicting recovery checkpoint.",
                    )
                merged_ext["dispatch_stage"] = "may_dispatch"
                self._append_unlocked_file({
                    "schema_version": JOURNAL_SCHEMA_VERSION,
                    "type": "checkpoint",
                    "status": "in_progress",
                    "operation_id": handle.operation_id,
                    "runtime_id": handle.runtime_id,
                    "recorded_at": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
                    "external": merged_ext,
                })
            self._maybe_compact_locked()

    def finish_not_dispatched(self, handle: OperationHandle | None, reason_code: str) -> None:
        if handle is None:
            return
        if not isinstance(reason_code, str) or _has_forbidden_text(reason_code, max_len=128):
            raise ToolError("invalid_external_recovery_metadata", "Invalid not_dispatched reason_code.")
        with self._lock:
            if not self._enabled:
                raise ToolError("recovery_unavailable", "Operation recovery journal is not enabled.")
            with self._journal_transaction(operation_id=handle.operation_id) as records:
                current = self._state(records).get(handle.operation_id)
                if current is None or current.get("begin") is None:
                    raise ToolError("operation_not_found", f"Operation {handle.operation_id!r} was not found.")
                if current.get("result") is not None:
                    raise ToolError("operation_already_completed", "Operation already has a known runtime result.")
                if current.get("uncertain") is not None:
                    raise ToolError("operation_already_uncertain", "Operation is already marked uncertain.")
                merged_ext = self._merged_external_metadata(current)
                if merged_ext is None:
                    raise ToolError(
                        "invalid_external_recovery_metadata",
                        "finish_not_dispatched requires an external operation.",
                    )
                if merged_ext.get("dispatch_stage") == "may_dispatch":
                    raise ToolError(
                        "operation_already_dispatched",
                        "Cannot record not_dispatched after may_dispatch checkpoint is durable.",
                    )
                merged_ext["dispatch_stage"] = "prepared"
                merged_ext["external_outcome"] = "not_dispatched"
                merged_ext["reason_code"] = reason_code
                self._append_unlocked_file({
                    "schema_version": JOURNAL_SCHEMA_VERSION,
                    "type": "result",
                    "status": "completed",
                    "operation_id": handle.operation_id,
                    "runtime_id": handle.runtime_id,
                    "finished_at": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
                    "known_result": "returned_error",
                    "external_outcome": "not_dispatched",
                    "external": merged_ext,
                })
            self._maybe_compact_locked()

    def mark_uncertain(
        self,
        handle: OperationHandle | None,
        reason_code: str,
        *,
        receipt_id: str | None = None,
    ) -> None:
        if handle is None:
            return
        if not isinstance(reason_code, str) or _has_forbidden_text(reason_code, max_len=128):
            raise ToolError("invalid_external_recovery_metadata", "Invalid uncertain reason_code.")
        if receipt_id is not None and (not isinstance(receipt_id, str) or _has_forbidden_text(receipt_id, max_len=128)):
            raise ToolError("invalid_external_recovery_metadata", "Invalid uncertain receipt_id.")
        with self._lock:
            if not self._enabled:
                raise ToolError("recovery_unavailable", "Operation recovery journal is not enabled.")
            with self._journal_transaction(operation_id=handle.operation_id) as records:
                current = self._state(records).get(handle.operation_id)
                if current is None or current.get("begin") is None:
                    raise ToolError("operation_not_found", f"Operation {handle.operation_id!r} was not found.")
                if current.get("result") is not None:
                    raise ToolError("operation_already_completed", "Operation already has a known runtime result.")
                if current.get("uncertain") is not None:
                    return
                record: dict[str, Any] = {
                    "schema_version": JOURNAL_SCHEMA_VERSION,
                    "type": "uncertain",
                    "status": "uncertain",
                    "operation_id": handle.operation_id,
                    "runtime_id": handle.runtime_id,
                    "marked_by_runtime_id": self._runtime_id,
                    "marked_at": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
                    "reason": reason_code,
                }
                merged_ext = self._merged_external_metadata(current)
                if merged_ext is not None:
                    merged_ext["external_outcome"] = "uncertain"
                    merged_ext["reason_code"] = reason_code
                    if receipt_id is not None:
                        merged_ext["receipt_id"] = receipt_id
                    record["external"] = merged_ext
                self._append_unlocked_file(record)
            self._maybe_compact_locked()

    def checkpoint_native_uia(
        self, handle: OperationHandle | None, target: NativeUIATarget, *,
        stage: Literal["prepared", "may_dispatch"],
    ) -> None:
        if handle is None:
            return
        if not isinstance(target, NativeUIATarget) or stage not in ("prepared", "may_dispatch"):
            raise ToolError("invalid_native_recovery_metadata", "Invalid native UIA recovery metadata.")
        native = {
            "native_metadata_version": 1, "kind": "windows_uia", "dispatch_stage": stage,
            "target": target.as_dict(),
        }
        with self._lock:
            if not self._enabled:
                raise ToolError("operation_journal_unavailable", "Native recovery journal became unavailable.")
            with self._journal_transaction(operation_id=handle.operation_id) as records:
                current = self._state(records).get(handle.operation_id)
                begin = current.get("begin") if current else None
                if (
                    not begin or handle.runtime_id != self._runtime_id
                    or begin.get("runtime_id") != handle.runtime_id
                    or begin.get("operation_type") != handle.operation_type
                    or handle.operation_type not in _NATIVE_UIA_OPERATIONS or begin.get("external") is not None
                ):
                    raise ToolError("invalid_operation_handle", "Native UIA recovery requires its active operation.")
                assert current is not None
                if any(current.get(key) is not None for key in ("result", "uncertain", "acknowledged", "reconciliation")):
                    raise ToolError("operation_already_completed", "Operation already has a known or uncertain outcome.")
                previous = current.get("checkpoint") or {}
                old_native = _native_uia_metadata(previous.get("native"))
                if "native" in previous and (
                    old_native is None or old_native["target"] != native["target"]
                    or (old_native["dispatch_stage"] == "may_dispatch" and stage == "prepared")
                ):
                    raise ToolError("operation_checkpoint_conflict", "Native UIA checkpoint identity or stage changed.")
                if old_native == native:
                    return
                if old_native is None and stage != "prepared":
                    raise ToolError("operation_checkpoint_conflict", "Prepare native UIA identity before dispatch.")
                self._append_unlocked_file({
                    **previous,
                    "schema_version": JOURNAL_SCHEMA_VERSION, "type": "checkpoint", "status": "in_progress",
                    "operation_id": handle.operation_id, "runtime_id": handle.runtime_id,
                    "recorded_at": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
                    "target": target.label(), "native": native,
                })
            self._maybe_compact_locked()

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
            if not self._enabled or self._path is None:
                return
            with self._journal_transaction(operation_id=handle.operation_id) as records:
                current = self._state(records).get(handle.operation_id)
                if current is None or current.get("begin") is None:
                    raise ToolError("operation_not_found", f"Operation {handle.operation_id!r} was not found.")
                if current.get("result") is not None:
                    raise ToolError("operation_already_completed", "Operation already has a known runtime result.")
                existing = current.get("checkpoint")
                if existing is not None and existing.get("postcondition") is not None:
                    if existing.get("postcondition") == bounded:
                        return
                    raise ToolError(
                        "operation_checkpoint_conflict",
                        "Operation already has a different recovery checkpoint.",
                    )
                self._append_unlocked_file({
                    **(existing or {}),
                    "schema_version": JOURNAL_SCHEMA_VERSION,
                    "type": "checkpoint",
                    "status": "in_progress",
                    "operation_id": handle.operation_id,
                    "runtime_id": handle.runtime_id,
                    "recorded_at": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
                    "postcondition": bounded,
                })
            self._maybe_compact_locked()

    def checkpoint_workspace_edit(
        self,
        handle: OperationHandle | None,
        evidence: WorkspaceEditEvidence,
        *,
        stage: Literal["prepared", "may_dispatch"],
    ) -> None:
        if handle is None:
            raise ToolError(
                "recovery_unavailable",
                "Workspace edits require an enabled operation recovery journal.",
            )
        record = _workspace_edit_record(evidence, stage)
        with self._lock:
            if not self._enabled or self._path is None:
                raise ToolError(
                    "recovery_unavailable",
                    "Workspace edits require an enabled operation recovery journal.",
                )
            with self._journal_transaction(operation_id=handle.operation_id) as records:
                state = self._state(records)
                current = state.get(handle.operation_id)
                begin = current.get("begin") if current else None
                if (
                    not begin or handle.runtime_id != self._runtime_id
                    or begin.get("runtime_id") != handle.runtime_id
                ):
                    raise ToolError("invalid_operation_handle", "Workspace edit recovery requires its active operation.")
                assert current is not None
                if any(current.get(key) is not None for key in ("result", "uncertain", "reconciliation", "acknowledged")):
                    raise ToolError("operation_already_completed", "Operation already has a known or uncertain outcome.")
                previous = current.get("checkpoint") or {}
                existing_raw = previous.get("workspace_edit")
                existing = _workspace_edit_metadata(existing_raw)
                if existing_raw is not None and existing is None:
                    raise ToolError("operation_checkpoint_conflict", "Workspace edit checkpoint is invalid.")
                if existing is not None:
                    if existing["files"] != record["files"]:
                        raise ToolError("operation_checkpoint_conflict", "Workspace edit checkpoint identity changed.")
                    if existing["dispatch_stage"] == record["dispatch_stage"]:
                        return
                    if existing["dispatch_stage"] == "may_dispatch" or record["dispatch_stage"] == "prepared":
                        raise ToolError("operation_checkpoint_conflict", "Workspace edit checkpoint stage changed.")
                elif record["dispatch_stage"] != "prepared":
                    raise ToolError("operation_checkpoint_conflict", "Prepare workspace edit evidence before dispatch.")
                if existing is None and record["dispatch_stage"] == "prepared":
                    unresolved = sum(
                        1
                        for operation_id, item in self._state(self._load_locked()).items()
                        if operation_id != handle.operation_id and self._is_unresolved_workspace_item(item)
                    )
                    if unresolved >= self._max_unresolved_workspace:
                        raise ToolError(
                            "workspace_edit_limit",
                            f"Unresolved workspace-edit limit ({self._max_unresolved_workspace}) reached; reconcile before retrying.",
                        )
                self._append_unlocked_file({
                    **previous,
                    "schema_version": JOURNAL_SCHEMA_VERSION,
                    "type": "checkpoint",
                    "status": "in_progress",
                    "operation_id": handle.operation_id,
                    "runtime_id": handle.runtime_id,
                    "recorded_at": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
                    "workspace_edit": record,
                })
            self._maybe_compact_locked()

    def workspace_edit_stage(self, handle: OperationHandle | None) -> str | None:
        if handle is None:
            return None
        with self._lock:
            if not self._enabled or self._path is None:
                return None
            with self._journal_transaction(operation_id=handle.operation_id) as records:
                current = self._state(records).get(handle.operation_id)
                if current is None:
                    return None
                if any(current.get(key) is not None for key in ("result", "uncertain", "reconciliation", "acknowledged")):
                    return "completed"
                checkpoint = current.get("checkpoint") or {}
                workspace = _workspace_edit_metadata(checkpoint.get("workspace_edit"))
                return workspace["dispatch_stage"] if workspace is not None else None

    @staticmethod
    def _validate_workspace_records(records: list[dict[str, Any]]) -> None:
        known: set[str] = set()
        workspace_evidence: dict[str, dict[str, Any]] = {}
        terminal: set[str] = set()
        for record in records:
            operation_id = record.get("operation_id")
            kind = record.get("type")
            if not isinstance(operation_id, str) or kind not in {
                "begin", "checkpoint", "result", "uncertain", "reconciliation", "acknowledged",
            }:
                raise ToolError("operation_journal_invalid", "The operation recovery journal contains an invalid record.")
            if kind == "begin":
                if operation_id in known:
                    raise ToolError("operation_journal_invalid", "The operation recovery journal contains a duplicate operation.")
                known.add(operation_id)
            elif operation_id not in known:
                raise ToolError("operation_journal_invalid", "The operation recovery journal contains an incomplete operation.")
            if kind == "checkpoint":
                if operation_id in terminal:
                    raise ToolError("operation_journal_invalid", "A completed operation has a later checkpoint.")
                workspace = _workspace_edit_metadata(record.get("workspace_edit"))
                previous = workspace_evidence.get(operation_id)
                if ("workspace_edit" in record and workspace is None) or (
                    previous is not None and (workspace is None or workspace["files"] != previous["files"]
                    or (previous["dispatch_stage"] == "may_dispatch" and workspace["dispatch_stage"] != "may_dispatch"))
                ):
                    raise ToolError("operation_journal_invalid", "The workspace recovery checkpoint is invalid.")
                if workspace is not None:
                    workspace_evidence[operation_id] = workspace
            if kind in {"result", "reconciliation", "acknowledged"} and operation_id in workspace_evidence:
                valid = record.get("status") == "completed" and all(
                    isinstance(record.get(field), str) and record[field]
                    for field in ("runtime_id", "finished_at" if kind == "result" else "recorded_at")
                )
                if kind == "result":
                    valid = valid and isinstance(record.get("known_result"), str) and bool(record["known_result"])
                else:
                    evidence = record.get("evidence")
                    states = {"succeeded", "failed", "unresolvable"} if kind == "reconciliation" else {
                        "acknowledged", "unresolvable",
                    }
                    valid = valid and record.get("state") in states and isinstance(evidence, dict) and bool(
                        evidence.get("source") and isinstance(evidence.get("data"), dict) and evidence["data"]
                    )
                    if kind == "reconciliation":
                        valid = valid and isinstance(record.get("postcondition"), dict)
                        if record.get("state") in {"succeeded", "failed"} and isinstance(evidence, dict):
                            valid = valid and isinstance(evidence.get("data"), dict) and evidence["data"].get("conclusive") is True
                if not valid:
                    raise ToolError("operation_journal_invalid", "The workspace recovery outcome is invalid.")
            if kind in {"result", "reconciliation", "acknowledged"}:
                terminal.add(operation_id)

    def protected_workspace_backups(self, *, expected_journal: Path | None = None) -> set[Path]:
        with self._lock:
            if expected_journal is not None and os.path.lexists(expected_journal) and (
                not self._enabled or self._path is None
                or self._path.resolve(strict=False) != expected_journal.resolve(strict=False)
            ):
                raise ToolError("recovery_unavailable", "The backup recovery journal is not configured.")
            if not self._enabled or self._path is None:
                return set()
            with _interprocess_journal_lock(self._path):
                records = list(self._iter_records_locked(strict=True))
            self._validate_workspace_records(records)
            state = self._state(records)
            protected: set[Path] = set()
            for item in state.values():
                begin = item.get("begin")
                reconciliation = item.get("reconciliation") or {}
                if not begin or item.get("result") or reconciliation.get("state") in {"succeeded", "failed"}:
                    continue
                checkpoint = item.get("checkpoint") or {}
                workspace = _workspace_edit_metadata(checkpoint.get("workspace_edit"))
                if workspace is None:
                    continue
                for item_file in workspace["files"]:
                    protected.add(Path(item_file["backup_path"]))
                    if len(protected) > _MAX_WORKSPACE_PROTECTED_PATHS:
                        raise ToolError(
                            "workspace_backup_protection_limit",
                            "Too many unresolved workspace-edit backups to clean safely.",
                        )
            return protected

    def finish(
        self,
        handle: OperationHandle | None,
        *,
        known_result: str,
        postcondition: dict[str, Any] | None = None,
        external_outcome: str | None = None,
        receipt_id: str | None = None,
    ) -> None:
        if handle is None:
            return
        if external_outcome is not None and external_outcome not in _ALLOWED_EXTERNAL_OUTCOMES:
            raise ToolError("invalid_external_recovery_metadata", "Invalid external_outcome.")
        if receipt_id is not None and (not isinstance(receipt_id, str) or _has_forbidden_text(receipt_id, max_len=128)):
            raise ToolError("invalid_external_recovery_metadata", "Invalid receipt_id.")
        with self._lock:
            if not self._enabled or self._path is None:
                return
            with self._journal_transaction(operation_id=handle.operation_id) as records:
                current = self._state(records).get(handle.operation_id)
                if current is not None and current.get("uncertain") is not None:
                    raise ToolError(
                        "operation_already_uncertain",
                        "Cannot mark an uncertain operation as completed without reconciliation or acknowledgment.",
                    )
                checkpoint = (current or {}).get("checkpoint") or {}
                workspace = _workspace_edit_metadata(checkpoint.get("workspace_edit"))
                if (
                    workspace is not None and workspace["dispatch_stage"] == "may_dispatch"
                    and known_result == "raised"
                ):
                    self._append_unlocked_file({
                        "schema_version": JOURNAL_SCHEMA_VERSION, "type": "uncertain", "status": "uncertain",
                        "operation_id": handle.operation_id, "runtime_id": handle.runtime_id,
                        "marked_at": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
                        "reason": "workspace_edit_dispatch_without_known_result",
                    })
                    return
                native = _native_uia_metadata(checkpoint.get("native"))
                if (
                    native is not None and native["dispatch_stage"] == "may_dispatch"
                    and known_result in {"raised", "returned_error"}
                ):
                    self._append_unlocked_file({
                        "schema_version": JOURNAL_SCHEMA_VERSION, "type": "uncertain", "status": "uncertain",
                        "operation_id": handle.operation_id, "runtime_id": handle.runtime_id,
                        "marked_at": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
                        "reason": "native_uia_dispatch_without_known_result",
                    })
                    return
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
                merged_ext = self._merged_external_metadata(current) if current is not None else None
                if merged_ext is not None:
                    resolved_outcome = external_outcome or (
                        "failed" if known_result in {"raised", "returned_error"} else "succeeded"
                    )
                    merged_ext["external_outcome"] = resolved_outcome
                    if receipt_id is not None:
                        merged_ext["receipt_id"] = receipt_id
                    record["external_outcome"] = resolved_outcome
                    record["external"] = merged_ext
                elif external_outcome is not None:
                    record["external_outcome"] = external_outcome
                self._append_unlocked_file(record)
            self._maybe_compact_locked()

    def finish_returned(self, handle: OperationHandle | None, result: Any) -> None:
        if (
            handle is not None and handle.operation_type == "verify_changes"
            and isinstance(result, dict) and result.get("execution_uncertain") is True
        ):
            self.mark_uncertain(handle, "native_test_execution_outcome_unknown")
            return
        if isinstance(result, dict) and isinstance(result.get("ok"), bool):
            known_result = "ok" if result["ok"] else "returned_error"
        else:
            known_result = "returned"
        try:
            self.finish(
                handle,
                known_result=known_result,
                postcondition=_bounded_result_postcondition(result),
            )
        except Exception:
            if handle is None or handle.operation_type not in _NATIVE_UIA_OPERATIONS:
                raise
            try:
                self.finish(handle, known_result="raised")
            except Exception:
                pass  # The durable may_dispatch checkpoint survives even when storage stays unavailable.
            raise ToolError(
                "operation_outcome_uncertain", "The UIA effect may have completed; do not replay it.",
                hint=f"Inspect recovery operation {handle.operation_id}.",
            ) from None

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
                checkpoint = item.get("checkpoint") or {}
                entry.update(_native_uia_view(checkpoint))
                entry.update(_workspace_edit_view(checkpoint))
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


@contextmanager
def native_uia_mutation(operation: str, target: NativeUIATarget) -> Iterator[None]:
    """Guard direct UIA calls, reusing the enclosing MCP mutation when present."""
    from core.lifecycle import RUNTIME_LIFECYCLE

    if operation not in _NATIVE_UIA_OPERATIONS:
        raise ToolError("invalid_native_recovery_metadata", "Unsupported native UIA mutation.")
    scope = _CURRENT_OPERATION_SCOPE.get()
    # Copied child-task/thread contexts are not the parent's live admission.
    owned = scope is None or not scope.active or scope.owner != _execution_owner()
    handle = scope.handle if scope is not None and not owned else None
    admission = RUNTIME_LIFECYCLE.mutation(operation) if owned else nullcontext()
    with admission:
        if owned:
            handle = OPERATION_RECOVERY.begin(
                operation,
                target.label(),
                native_uia_protocol=True,
            )
        with operation_recovery_context(handle):
            dispatched = False
            try:
                OPERATION_RECOVERY.checkpoint_native_uia(handle, target, stage="prepared")
                OPERATION_RECOVERY.checkpoint_native_uia(handle, target, stage="may_dispatch")
                dispatched = True
                yield
                if owned:
                    OPERATION_RECOVERY.finish(handle, known_result="returned")
            except BaseException as exc:
                if owned or dispatched:
                    try:
                        OPERATION_RECOVERY.finish(handle, known_result="raised")
                    except Exception:
                        pass  # A durable may_dispatch checkpoint remains unresolved if storage fails.
                if dispatched and handle is not None and isinstance(exc, Exception):
                    raise ToolError(
                        "operation_outcome_uncertain", "The UIA effect may have completed; do not replay it.",
                        hint=f"Inspect recovery operation {handle.operation_id}.",
                    ) from None
                raise


@contextmanager
def workspace_edit_mutation(paths: tuple[Path, ...]) -> Iterator[OperationHandle]:
    """Guard direct workspace edits, reusing the enclosing live mutation when present."""
    from core.lifecycle import RUNTIME_LIFECYCLE

    if not paths or len(paths) > _MAX_WORKSPACE_EDIT_FILES or any(not isinstance(path, Path) for path in paths):
        raise ToolError("invalid_workspace_edit_evidence", "Invalid workspace edit recovery paths.")
    OPERATION_RECOVERY.ensure_workspace_edit_capacity()
    scope = _CURRENT_OPERATION_SCOPE.get()
    owner = _execution_owner()
    owned = scope is None or not scope.active or scope.owner != owner
    admission = RUNTIME_LIFECYCLE.mutation("workspace_edit") if owned else nullcontext()
    with admission:
        handle = scope.handle if scope is not None and not owned else None
        if owned:
            handle = OPERATION_RECOVERY.begin("workspace_edit", f"{len(paths)} file(s)")
        if handle is None:
            raise ToolError(
                "recovery_unavailable",
                "Workspace edits require an enabled operation recovery journal.",
                hint="Run through the supervised runtime or configure operation recovery before editing.",
            )
        with operation_recovery_context(handle) as current:
            try:
                yield handle
            except BaseException:
                if owned:
                    try:
                        stage = OPERATION_RECOVERY.workspace_edit_stage(handle)
                        if stage == "may_dispatch" and not current.rolled_back:
                            OPERATION_RECOVERY.mark_uncertain(handle, "workspace_edit_outcome_uncertain")
                        elif stage != "completed":
                            OPERATION_RECOVERY.finish(handle, known_result="returned_error")
                    except Exception:
                        pass  # A durable prepared/may_dispatch checkpoint remains unresolved.
                raise
            else:
                if owned:
                    try:
                        OPERATION_RECOVERY.finish(handle, known_result="ok")
                    except Exception:
                        pass  # The verified effect is complete; the may_dispatch checkpoint remains for reconciliation.


def checkpoint_current_postcondition(postcondition: dict[str, Any]) -> None:
    """Durably checkpoint the current mutation's file hash before its side effect."""

    scope = _CURRENT_OPERATION_SCOPE.get()
    if scope is not None and not scope.active:
        raise ToolError("invalid_operation_handle", "The enclosing recovery operation has finished.")
    OPERATION_RECOVERY.checkpoint_postcondition(scope.handle if scope is not None else None, postcondition)
