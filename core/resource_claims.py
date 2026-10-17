"""Durable resource-claim models and process-local execution capability context."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import time
import uuid
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal
from urllib.parse import urlsplit

import psutil

from core.resource_locks import canonical_path

if TYPE_CHECKING:
    from core.workflow_store import WorkflowStore

ClaimMode = Literal["read", "write"]
ClaimScope = Literal["exact", "subtree"]


@dataclass(frozen=True, slots=True, order=True)
class ResourceClaim:
    kind: str
    key: str
    mode: ClaimMode
    scope: ClaimScope = "exact"

    def __post_init__(self) -> None:
        if not self.kind or len(self.kind) > 40:
            raise ValueError("resource claim kind must contain 1..40 characters")
        if not self.key or len(self.key) > 4_000:
            raise ValueError("resource claim key must contain 1..4000 characters")
        if self.mode not in {"read", "write"}:
            raise ValueError("resource claim mode must be read or write")
        if self.scope not in {"exact", "subtree"}:
            raise ValueError("resource claim scope must be exact or subtree")

    def public(self) -> dict[str, str]:
        return {"kind": self.kind, "key": self.key, "mode": self.mode, "scope": self.scope}


def path_claim(path: str | Path, mode: ClaimMode, *, scope: ClaimScope = "exact", kind: str = "fs") -> ResourceClaim:
    return ResourceClaim(kind, canonical_path(path), mode, scope)


def coordination_claim(*, exclusive: bool = False) -> ResourceClaim:
    return ResourceClaim("coordination", "host", "write" if exclusive else "read", "exact")


def endpoint_claim(url: str) -> ResourceClaim:
    parsed = urlsplit(url)
    origin = f"{parsed.scheme.casefold()}://{(parsed.hostname or '').casefold()}:{parsed.port or ''}"
    digest = hashlib.sha256(origin.encode("utf-8")).hexdigest()
    return ResourceClaim("http", digest, "read", "exact")


def _path_overlap(first: ResourceClaim, second: ResourceClaim) -> bool:
    left = os.path.normcase(os.path.normpath(first.key))
    right = os.path.normcase(os.path.normpath(second.key))
    if first.scope == "exact" and second.scope == "exact":
        return left == right
    try:
        common = os.path.commonpath([left, right])
    except ValueError:
        return False
    if first.scope == "subtree" and second.scope == "subtree":
        return common == left or common == right
    if first.scope == "subtree":
        return common == left
    if second.scope == "subtree":
        return common == right
    return left == right


def claims_overlap(first: ResourceClaim, second: ResourceClaim) -> bool:
    if first.kind != second.kind:
        return False
    if first.kind in {"fs", "git", "cache", "cwd"}:
        return _path_overlap(first, second)
    if first.scope == "exact" and second.scope == "exact":
        return first.key == second.key
    left = tuple(part for part in first.key.replace("\\", "/").split("/") if part)
    right = tuple(part for part in second.key.replace("\\", "/").split("/") if part)
    if first.scope == "subtree" and second.scope == "subtree":
        shortest = min(len(left), len(right))
        return left[:shortest] == right[:shortest]
    if first.scope == "subtree":
        return right[: len(left)] == left
    if second.scope == "subtree":
        return left[: len(right)] == right
    return left == right


def claims_conflict(first: ResourceClaim, second: ResourceClaim) -> bool:
    if not claims_overlap(first, second):
        return False
    return first.mode == "write" or second.mode == "write"


def claim_sets_conflict(first: Sequence[ResourceClaim], second: Sequence[ResourceClaim]) -> bool:
    return any(claims_conflict(left, right) for left in first for right in second)


def canonical_claims(claims: Sequence[ResourceClaim]) -> tuple[ResourceClaim, ...]:
    return tuple(sorted(set(claims), key=lambda claim: (claim.kind, claim.key, claim.scope, claim.mode)))


RESOURCE_CAPABILITY_ENV = "COMPUTERPILOT_INTERNAL_RESOURCE_CAPABILITY"
RESOURCE_CAPABILITY_MAX_LENGTH = 16_384
_RESOURCE_CAPABILITY: ContextVar[str | None] = ContextVar("computerpilot_resource_capability", default=None)


def make_resource_capability(request_id: str, owner_token: str, db_path: Path) -> str:
    payload = json.dumps(
        {
            "v": 1,
            "request_id": request_id,
            "owner_token": owner_token,
            "db_path": str(db_path.resolve(strict=False)),
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return "rc1." + base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def _parse_resource_capability(token: str) -> tuple[str, str, Path]:
    from core.errors import ToolError

    if len(token) > RESOURCE_CAPABILITY_MAX_LENGTH or not token.startswith("rc1."):
        raise ToolError("resource_capability_invalid", "Internal resource capability is malformed.")
    encoded = token[4:]
    try:
        raw = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
        payload = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ToolError("resource_capability_invalid", "Internal resource capability is malformed.") from exc
    request_id = payload.get("request_id") if isinstance(payload, dict) else None
    owner_token = payload.get("owner_token") if isinstance(payload, dict) else None
    db_path = payload.get("db_path") if isinstance(payload, dict) else None
    if (
        not isinstance(payload, dict)
        or payload.get("v") != 1
        or not isinstance(request_id, str)
        or not 1 <= len(request_id) <= 200
        or not isinstance(owner_token, str)
        or not 1 <= len(owner_token) <= 200
        or not isinstance(db_path, str)
        or not db_path
        or not Path(db_path).is_absolute()
    ):
        raise ToolError("resource_capability_invalid", "Internal resource capability is malformed.")
    return request_id, owner_token, Path(db_path).resolve(strict=False)


def current_resource_capability() -> str | None:
    return _RESOURCE_CAPABILITY.get() or os.getenv(RESOURCE_CAPABILITY_ENV)


@contextmanager
def resource_capability(token: str) -> Iterator[None]:
    reset = _RESOURCE_CAPABILITY.set(token)
    try:
        yield
    finally:
        _RESOURCE_CAPABILITY.reset(reset)


def _claim_covers(parent: ResourceClaim, child: ResourceClaim) -> bool:
    if parent.kind != child.kind:
        return False
    if parent.mode == "read" and child.mode == "write":
        return False
    if parent.scope == "exact":
        return child.scope == "exact" and parent.key == child.key
    if parent.kind in {"fs", "git", "cache", "cwd"}:
        left = os.path.normcase(os.path.normpath(parent.key))
        right = os.path.normcase(os.path.normpath(child.key))
        try:
            return os.path.commonpath([left, right]) == left
        except ValueError:
            return False
    parent_parts = tuple(part for part in parent.key.replace("\\", "/").split("/") if part)
    child_parts = tuple(part for part in child.key.replace("\\", "/").split("/") if part)
    return child_parts[: len(parent_parts)] == parent_parts


def validate_resource_capability(
    token: str,
    requested: Sequence[ResourceClaim],
    store: WorkflowStore | None = None,
) -> WorkflowStore:
    from core.errors import ToolError
    from core.workflow_store import WorkflowStore

    request_id, owner_token, authority_path = _parse_resource_capability(token)
    authority = (
        store if store is not None and store.path.resolve(strict=False) == authority_path
        else WorkflowStore(authority_path, recover=False)
    )
    try:
        resource = authority.resource_request(request_id, expected_owner_token=owner_token)
    except ToolError as exc:
        if exc.code == "resource_request_not_found":
            raise ToolError("resource_capability_invalid", "Internal resource capability is no longer valid.") from exc
        raise
    if resource["state"] != "active" or resource["owner_token"] != owner_token:
        raise ToolError("resource_capability_invalid", "Internal resource capability is no longer active.")
    parents = tuple(ResourceClaim(**claim) for claim in resource["claims"])
    for child in requested:
        if not any(_claim_covers(parent, child) for parent in parents):
            raise ToolError(
                "resource_capability_scope_violation",
                "Nested managed action requested resources outside its parent capability.",
            )
    return authority


def resource_requires_native_ownership(token: str | None = None, store: WorkflowStore | None = None) -> bool:
    from core.errors import ToolError

    inherited = token if token is not None else current_resource_capability()
    if inherited is None:
        return False
    authority = validate_resource_capability(inherited, (), store)
    request_id, owner_token, _ = _parse_resource_capability(inherited)
    resource = authority.resource_request(request_id, expected_owner_token=owner_token)
    if resource["workflow_id"] is not None:
        operation = authority.get_operation(str(resource["operation_id"]))
        if operation.get("dispatch_phase") != "may_dispatch":
            raise ToolError("workflow_dispatch_barrier_required", "Workflow process dispatch requires its durable barrier.")
    return True


@contextmanager
def managed_resource_claims(
    claims: Sequence[ResourceClaim],
    *,
    category: str,
    timeout_sec: float = 60.0,
    db_path: Path | None = None,
    store: WorkflowStore | None = None,
    quarantine_on_error: bool = False,
) -> Iterator[str]:
    """Coordinate one managed action or safely reuse an active parent capability."""

    from core.config import SETTINGS
    from core.errors import ToolError
    from core.workflow_store import WorkflowStore

    canonical = canonical_claims(claims)
    if not canonical:
        raise ToolError("empty_resource_claims", "Managed resource coordination requires at least one claim.")
    inherited = current_resource_capability()
    if inherited is not None:
        authority = validate_resource_capability(inherited, canonical, store)
        try:
            yield inherited
        except BaseException:
            if quarantine_on_error:
                request_id, owner_token, _ = _parse_resource_capability(inherited)
                authority.quarantine_resource_request(
                    request_id, owner_token=owner_token, reason="managed_effect_or_cleanup_unresolved",
                )
            raise
        return

    claim_store = store or WorkflowStore(db_path or SETTINGS.workflow_db, recover=False)
    request_id = f"native-resource-{uuid.uuid4().hex}"
    owner_token = f"native-owner-{os.getpid()}-{psutil.Process().create_time():.6f}-{uuid.uuid4().hex}"
    capability = make_resource_capability(request_id, owner_token, claim_store.path)
    deadline = time.monotonic() + max(0.001, timeout_sec)
    admitted = False
    release_on_exit = True
    try:
        while True:
            result = claim_store.reserve_resource_claims(
                request_id=request_id,
                owner_token=owner_token,
                claims=canonical,
                category=category,
            )
            if result["admitted"] is True:
                admitted = True
                break
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                claim_store.release_resource_request(request_id, owner_token=owner_token)
                raise ToolError(
                    "resource_admission_timeout",
                    "Managed resource admission did not become available before the timeout.",
                )
            time.sleep(min(0.05, remaining))
        with resource_capability(capability):
            yield capability
    except BaseException:
        if admitted and quarantine_on_error:
            # Do not release even if persisting quarantine fails: the active claim still fences conflicts.
            release_on_exit = False
            claim_store.quarantine_resource_request(
                request_id, owner_token=owner_token, reason="managed_effect_or_cleanup_unresolved",
            )
            raise
        raise
    finally:
        if release_on_exit:
            claim_store.release_resource_request(request_id, owner_token=owner_token)


@contextmanager
def managed_path_claims(
    *paths: str | Path, category: str, scope: ClaimScope = "exact", timeout_sec: float = 60.0,
) -> Iterator[str]:
    """Claim native writer paths before local locks and reject aliases changed while waiting."""

    from core.errors import ToolError

    claims = canonical_claims((coordination_claim(), *(path_claim(path, "write", scope=scope) for path in paths)))
    with managed_resource_claims(claims, category=category, timeout_sec=timeout_sec) as capability:
        current = canonical_claims((coordination_claim(), *(path_claim(path, "write", scope=scope) for path in paths)))
        if current != claims:
            raise ToolError("resource_path_changed", "Resource path changed while waiting for admission.")
        yield capability
