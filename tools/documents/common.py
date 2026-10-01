"""Shared atomic publication and artifact helpers for document adapters."""

from __future__ import annotations

import hashlib
import os
import stat
import tempfile
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from core.backups import BACKUP_RETENTION
from core.config import SETTINGS, ensure_runtime_dirs, resolve_path
from core.errors import ToolError
from core.file_lock import exclusive_file_lock
from core.recovery import checkpoint_current_postcondition
from core.resource_locks import RESOURCE_LOCKS, canonical_path
from tools.filesystem import service as filesystem_service

DocumentWriter = Callable[[Path], None]
DocumentValidator = Callable[[Path], None]


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1_048_576), b""):
            digest.update(chunk)
    return digest.hexdigest()


def artifact_descriptor(path: Path, media_type: str) -> dict[str, Any]:
    return {
        "path": str(path),
        "sha256": file_sha256(path),
        "bytes": path.stat().st_size,
        "media_type": media_type,
    }


def _lock_path(path: Path) -> Path:
    ensure_runtime_dirs()
    identity = canonical_path(path).encode("utf-8", errors="surrogatepass")
    return SETTINGS.document_lock_dir / f"{hashlib.sha256(identity).hexdigest()}.lock"


@contextmanager
def document_write_lock(path: str | Path, *, timeout_sec: float = 30.0) -> Iterator[Path]:
    target = resolve_path(path)
    with RESOURCE_LOCKS.sync(target):
        with exclusive_file_lock(
            _lock_path(target),
            timeout_sec=timeout_sec,
            label=f"document {target}",
        ):
            yield target


def _postcondition(path: Path, digest: str) -> dict[str, Any]:
    return {
        "kind": "file_sha256",
        "expected": {"path": str(path), "sha256": digest},
    }


def publish_document(
    path: str | Path,
    *,
    writer: DocumentWriter,
    validator: DocumentValidator | None,
    media_type: str,
    backup: bool = True,
    expected_sha256: str | None = None,
    create_parents: bool = False,
    exclusive_create: bool = False,
) -> dict[str, Any]:
    """Stage, validate, back up, and atomically publish one binary document."""

    with document_write_lock(path) as target:
        existed = target.exists()
        if exclusive_create and (existed or target.is_symlink()):
            raise FileExistsError(f"Target already exists: {target}")
        if existed and not target.is_file():
            raise ToolError("not_a_file", f"Target is not a regular file: {target}")
        if create_parents:
            target.parent.mkdir(parents=True, exist_ok=True)
        if not target.parent.is_dir():
            raise FileNotFoundError(f"Parent directory not found: {target.parent}")

        before_hash = file_sha256(target) if existed else None
        if expected_sha256 is not None:
            normalized = expected_sha256.casefold()
            if before_hash is None or before_hash.casefold() != normalized:
                raise ToolError(
                    "stale_file",
                    "Document hash changed; refusing to overwrite stale content.",
                )

        before_size = target.stat().st_size if existed else 0
        original_mode = stat.S_IMODE(target.stat().st_mode) if existed else None
        descriptor, temp_name = tempfile.mkstemp(
            prefix=f".{target.stem}.",
            suffix=target.suffix or ".tmp",
            dir=str(target.parent),
        )
        os.close(descriptor)
        staged = Path(temp_name)
        backup_path: str | None = None
        try:
            writer(staged)
            if not staged.is_file():
                raise ToolError("document_stage_missing", "Document writer did not produce a staged file.")
            with staged.open("rb+") as handle:
                handle.flush()
                os.fsync(handle.fileno())
            if original_mode is not None:
                os.chmod(staged, original_mode)
            if validator is not None:
                validator(staged)

            staged_hash = file_sha256(staged)
            staged_size = staged.stat().st_size
            postcondition = _postcondition(target, staged_hash)
            checkpoint_current_postcondition(postcondition)
            if existed and staged_size == before_size and staged_hash == before_hash:
                artifact = artifact_descriptor(target, media_type)
                return {
                    "ok": True,
                    "path": str(target),
                    "changed": False,
                    "created": False,
                    "bytes_before": before_size,
                    "bytes_after": before_size,
                    "sha256_before": before_hash,
                    "sha256": before_hash,
                    "backup": None,
                    "artifact": artifact,
                    "postcondition": postcondition,
                }

            if existed and backup:
                backup_path = filesystem_service._backup_file(target, before_hash or "unknown")

            if exclusive_create:
                os.link(staged, target)
                staged.unlink(missing_ok=True)
            else:
                filesystem_service._replace_with_retry(staged, target)

            final_hash = file_sha256(target)
            final_size = target.stat().st_size
            if final_hash != staged_hash or final_size != staged_size:
                raise ToolError(
                    "document_postcondition_failed",
                    "Published document does not match the validated staged artifact.",
                )

            if backup_path is not None:
                BACKUP_RETENTION.schedule(Path(backup_path))

            artifact = {
                "path": str(target),
                "sha256": final_hash,
                "bytes": final_size,
                "media_type": media_type,
            }
            return {
                "ok": True,
                "path": str(target),
                "changed": True,
                "created": not existed,
                "bytes_before": before_size,
                "bytes_after": final_size,
                "sha256_before": before_hash,
                "sha256": final_hash,
                "backup": backup_path,
                "artifact": artifact,
                "postcondition": _postcondition(target, final_hash),
            }
        finally:
            staged.unlink(missing_ok=True)
