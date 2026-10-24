"""Bounded native verification requests submitted through the existing job store."""

from __future__ import annotations

import hashlib
import os
import stat
import subprocess
import sys
import time
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from core.config import PROJECT_ROOT
from core.jobs import JobStore
from tools.project.service import EXCLUDED_DIRS
from tools.testing.project_runners import RunnerSpec
from tools.testing.verification import Check, build_verification_plan

MAX_REQUEST_BYTES = 64 * 1024
MAX_EVENT_BYTES = 64 * 1024
MAX_STREAM_BYTES = 4 * 1024 * 1024
MAX_INPUT_BYTES = 64 * 1024 * 1024
MAX_INPUT_FILES = 10_000
PathText = Annotated[str, Field(min_length=1, max_length=32_767)]
_WORKER_BOOTSTRAP = (
    "import runpy,sys;sys.path.insert(0,sys.argv.pop(1));"
    "runpy.run_module('scripts.verification_worker',run_name='__main__',alter_sys=True)"
)


class VerificationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    repo: PathText
    changed_paths: Annotated[list[PathText] | None, Field(max_length=1_000)] = None
    base: Annotated[str | None, Field(min_length=1, max_length=500, pattern=r"^[a-zA-Z0-9_.\-/@^~:]+$")] = None
    checks: Annotated[list[Check] | None, Field(max_length=4)] = None
    test_backend: Literal["python", "auto"] = "python"
    affected_only: bool = True
    fallback_policy: Literal["report", "select_full_suite"] = "select_full_suite"
    fail_fast: bool = True
    maxfail: Annotated[int, Field(ge=1, le=100)] = 1
    stage_timeout_sec: Annotated[float, Field(gt=0, le=3_600)] = 600
    total_timeout_sec: Annotated[float, Field(gt=0, le=7_200)] = 1_800
    ruff_fix: bool = False
    diagnostic_cap: Annotated[int, Field(ge=1, le=200)] = 30
    max_parallel_checks: Annotated[int, Field(ge=1, le=3)] = 1
    source_fingerprint: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")] = "0" * 64
    snapshot_input_paths: Annotated[list[PathText] | None, Field(max_length=10_000)] = None
    runner_specs: Annotated[tuple[RunnerSpec, ...], Field(max_length=256)] = ()

    @model_validator(mode="after")
    def validate_modes(self) -> VerificationRequest:
        if self.max_parallel_checks > 1 and (self.fail_fast or self.ruff_fix):
            raise ValueError("parallel verification requires fail_fast=false and ruff_fix=false")
        if self.ruff_fix:
            raise ValueError("background verification cannot mutate files; run ruff fix first, then verify")
        if self.base is not None and self.base.startswith("-"):
            raise ValueError("base git ref cannot start with '-'")
        if self.test_backend == "auto" and (self.stage_timeout_sec > 600 or self.total_timeout_sec > 1800):
            raise ValueError("auto verification requires stage_timeout_sec<=600 and total_timeout_sec<=1800")
        return self

    def options(self) -> dict[str, Any]:
        return self.model_dump(exclude={"repo", "source_fingerprint", "max_parallel_checks", "snapshot_input_paths", "runner_specs"})


def request_root(request: VerificationRequest) -> Path:
    root = Path(request.repo).resolve(strict=True)
    if not root.is_dir():
        raise ValueError("Verification repository must be a directory")
    for path in [*(request.changed_paths or []), *(request.snapshot_input_paths or [])]:
        if Path(path).is_absolute() or not (root / path).resolve().is_relative_to(root):
            raise ValueError("Changed paths must stay inside the repository")
    return root


def _is_symlink_or_junction(path: Path) -> bool:
    try:
        return path.is_symlink() or getattr(path, "is_junction", lambda: False)()
    except OSError:
        return False


def source_fingerprint(repo: Path, *, changed_paths: list[str] | None = None) -> str:
    """Hash a bounded tree plus explicit files, including recursively excluded paths."""
    root = repo.resolve(strict=True)
    if not root.is_dir():
        raise ValueError("Verification repository must be a directory")
    digest = hashlib.sha256()
    count = total = 0
    deadline = time.monotonic() + 10
    seen: set[Path] = set()

    def walk_error(error: OSError) -> None:
        raise error

    def check_budget() -> None:
        if count > MAX_INPUT_FILES or total > MAX_INPUT_BYTES or time.monotonic() > deadline:
            raise ValueError("Verification input snapshot exceeds its count, byte or time budget")

    def hash_file(path: Path) -> None:
        nonlocal count, total
        check_budget()
        if _is_symlink_or_junction(path) or not path.resolve().is_relative_to(root):
            raise ValueError("Verification inputs must remain inside the repository")
        path = path.resolve()
        if path in seen:
            return
        seen.add(path)
        try:
            before = path.stat()
        except OSError as exc:
            raise ValueError("Verification input changed during snapshot") from exc
        if not stat.S_ISREG(before.st_mode):
            raise ValueError("Verification inputs must be regular files")
        count += 1
        total += before.st_size
        check_budget()
        content = hashlib.sha256()
        with path.open("rb") as stream:
            while chunk := stream.read(64 * 1024):
                content.update(chunk)
                if stream.tell() > before.st_size or time.monotonic() > deadline:
                    raise ValueError("Verification input changed or exceeded its time budget")
        try:
            after = path.stat()
        except OSError as exc:
            raise ValueError("Verification input changed during snapshot") from exc
        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            raise ValueError("Verification input changed during snapshot")
        digest.update(b"F" + path.relative_to(root).as_posix().encode("utf-8") + b"\0" + content.digest())

    for directory, dirs, names in os.walk(root, onerror=walk_error):
        check_budget()
        dirs[:] = sorted(
            name for name in dirs
            if name not in EXCLUDED_DIRS | {"vendor"} and not name.startswith((".pytest-tmp", ".codebase-memory"))
        )
        if any(_is_symlink_or_junction(Path(directory) / name) for name in dirs):
            raise ValueError("Verification inputs cannot contain directory symlinks")
        for name in sorted(names):
            if name == ".git" or name.endswith((".pyc", ".pyo")) or name.startswith(".coverage"):
                continue
            hash_file(Path(directory) / name)
    for relative in sorted(set(changed_paths or [])):
        check_budget()
        path = root / relative
        if Path(relative).is_absolute() or not path.resolve().is_relative_to(root):
            raise ValueError("Changed paths must stay inside the repository")
        if any(_is_symlink_or_junction(parent) for parent in (path, *path.parents) if parent != root and parent.is_relative_to(root)):
            raise ValueError("Verification inputs cannot contain directory symlinks")
        try:
            path.stat()
        except FileNotFoundError:
            digest.update(b"M" + path.resolve().relative_to(root).as_posix().encode("utf-8") + b"\0")
        else:
            hash_file(path)
    check_budget()
    return digest.hexdigest()


def submit_verification(
    store: JobStore, request: VerificationRequest, idempotency_key: str, queue_timeout_sec: float | None = None,
) -> dict[str, Any]:
    if not 1 <= len(idempotency_key) <= 200:
        raise ValueError("idempotency_key must contain 1 to 200 characters")
    if len(request.model_dump_json().encode("utf-8")) > MAX_REQUEST_BYTES:
        raise ValueError("Verification request exceeds its size limit")
    root = request_root(request)
    plan = build_verification_plan(
        root, changed_paths=request.changed_paths, base=request.base,
        affected_only=request.affected_only, fallback_policy=request.fallback_policy,
        test_backend=request.test_backend,
    )
    from tools.testing.verification import bind_verification_runners

    specs: tuple[RunnerSpec, ...] = ()
    if request.test_backend == "auto" and (request.checks is None or "pytest" in request.checks):
        specs = bind_verification_runners(root, plan)
    snapshot_paths = (
        set(plan["changed_paths"]) | set(plan["affected"].get("touched_paths", []))
        | set(plan.get("snapshot_input_paths", []))
    )
    selected = {(item["package_root"], item["runner"]) for item in plan.get("package_selections", [])}
    snapshot_specs = [*specs, *(item for item in plan.get("runner_catalog", ())
                              if (item.package_root.relative_to(root).as_posix(), item.runner) in selected)]
    for spec in snapshot_specs:
        snapshot_paths.update(spec.config_paths)
        snapshot_paths.update(item.removeprefix("installed:") for item in spec.discovery_evidence if item.startswith("installed:"))
        if spec.entrypoint is not None:
            snapshot_paths.add(spec.entrypoint.relative_to(root).as_posix())
    updated_data = request.model_dump()
    base_sha = plan["affected"].get("base_sha") if request.base is not None else None
    if request.base is not None and not isinstance(base_sha, str):
        raise ValueError("Verification base could not be pinned to a commit")
    updated_data.update({
        "repo": str(root),
        "changed_paths": sorted(set(plan.get("snapshot_input_paths", plan["changed_paths"]))),
        "base": base_sha,
        "snapshot_input_paths": sorted(snapshot_paths),
        "runner_specs": specs,
        "source_fingerprint": source_fingerprint(root, changed_paths=sorted(snapshot_paths)),
    })
    request = VerificationRequest.model_validate(updated_data)
    payload = request.model_dump_json()
    command = [sys.executable, "-I", "-c", _WORKER_BOOTSTRAP, str(PROJECT_ROOT), "--request-json", payload]
    if len(payload.encode("utf-8")) > MAX_REQUEST_BYTES or (
        os.name == "nt" and len(subprocess.list2cmdline(command).encode("utf-16-le")) // 2 >= 32_760
    ):
        raise ValueError("Verification request exceeds the native command size limit")
    result = store.submit(
        command, root, request.total_timeout_sec + 30,
        f"verification:{idempotency_key}", queue_timeout_sec=queue_timeout_sec,
    )
    return {**result, "accepted": True}
