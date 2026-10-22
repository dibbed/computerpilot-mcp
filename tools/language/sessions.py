"""Bounded, opt-in language sessions with one process and protocol owner per key."""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import queue
import threading
import time
import uuid
from collections import OrderedDict
from collections.abc import Awaitable, Callable, Mapping
from concurrent.futures import Future, InvalidStateError
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Literal

import psutil

from core.config import resolve_path
from core.errors import ToolError
from core.process_ownership import OwnedProcess
from core.resource_locks import canonical_path
from tools.language.lsp import _language_id, position_to_offset, uri_to_path
from tools.language.providers import LspProvider, SessionKey, make_session_key, validate_provider
from tools.language.requests import validate_request
from tools.language.transport import LspTransport, close_transport, open_transport

_IDLE_SEC = 120.0
_SAMPLE_SEC = 0.5
_MAX_WIRE_BYTES = 64 * 1024 * 1024
_MAX_RSS_BYTES = 512 * 1024 * 1024
_MAX_TOTAL_RSS_BYTES = 2 * 1024 * 1024 * 1024
_MAX_DOCUMENT_BYTES = 2 * 1024 * 1024
_MAX_DOCUMENT_TOTAL_BYTES = 16 * 1024 * 1024
_CONTEXT_MAX_RESULTS = 32
_CONTEXT_MAX_BYTES = 16 * 1024 * 1024
_CONTEXT_TTL_SEC = 30.0
_EVIDENCE_CAPABILITIES = (
    "workspaceSymbolProvider", "documentSymbolProvider", "definitionProvider",
    "referencesProvider", "callHierarchyProvider", "typeHierarchyProvider",
)
_HIERARCHY_ITEM_METHODS = frozenset({
    "callHierarchy/incomingCalls", "callHierarchy/outgoingCalls", "typeHierarchy/supertypes", "typeHierarchy/subtypes",
})
_DOCUMENT_ERRORS = frozenset({
    "lsp_document_unavailable", "lsp_document_changed", "lsp_document_encoding", "lsp_document_too_large",
    "lsp_document_capacity", "lsp_position_invalid", "lsp_sync_unsupported", "lsp_path_outside_workspace", "lsp_uri_unsupported",
})


@dataclass
class DocumentState:
    uri: str
    path: Path
    sha256: str
    version: int
    text: str
    last_used: float


class RenameResult(dict[str, object]):
    """Keep trusted preimages private while preserving the WorkspaceEdit JSON shape."""

    def __init__(self, result: dict[str, object], documents: Mapping[str, DocumentState]) -> None:
        super().__init__(result)
        self.snapshot_version: Mapping[str, int] = MappingProxyType({uri: doc.version for uri, doc in documents.items()})
        self.source_sha256: Mapping[str, str] = MappingProxyType({uri: doc.sha256 for uri, doc in documents.items()})


def _disk_document(uri: str, root: Path, deadline: float) -> tuple[Path, str, str, int]:
    for _attempt in range(3):
        if time.monotonic() >= deadline:
            raise _error("lsp_timeout")
        path = uri_to_path(uri, root)
        try:
            before = path.stat()
            if before.st_size > _MAX_DOCUMENT_BYTES:
                raise _error("lsp_document_too_large")
            with path.open("rb") as handle:
                opened = os.fstat(handle.fileno())
                data = handle.read(_MAX_DOCUMENT_BYTES + 1)
                read = os.fstat(handle.fileno())
            after = path.stat()
        except OSError:
            raise _error("lsp_document_unavailable") from None
        if len(data) > _MAX_DOCUMENT_BYTES:
            raise _error("lsp_document_too_large")
        signatures = {(info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns) for info in (before, opened, read, after)}
        if len(signatures) != 1 or len(data) != before.st_size or uri_to_path(uri, root) != path:
            continue
        try:
            text = data.decode("utf-8", errors="strict")
        except UnicodeError:
            raise _error("lsp_document_encoding") from None
        if time.monotonic() >= deadline:
            raise _error("lsp_timeout")
        return path, hashlib.sha256(data).hexdigest(), text, len(data)
    raise _error("lsp_document_changed")


@dataclass(frozen=True, slots=True)
class SessionReply:
    value: object
    key: SessionKey
    incarnation: str
    capabilities: frozenset[str]
    documents: tuple[tuple[str, int, str], ...]


def session_generation(
    key: SessionKey, incarnation: str, documents: tuple[tuple[str, int, str], ...] | None = None,
) -> str:
    """Project the exact owner identity without inspecting or activating a session."""
    identity = (key, incarnation) if documents is None else (key, incarnation, documents)
    return hashlib.sha256(repr(identity).encode("utf-8")).hexdigest()


_ContextKey = tuple[str, str, SessionKey | None, str, str | None, int, str]


@dataclass(frozen=True, slots=True)
class _ContextEntry:
    payload: bytes
    generation: str
    expires: float


@dataclass
class _ContextFlight:
    result: Future[tuple[bytes, str | None]] = field(default_factory=Future)
    task: asyncio.Task[None] | None = None


@dataclass
class RequestTicket:
    request_id: int
    payload: bytes
    deadline: float
    cancelled: threading.Event
    future: Future[SessionReply]
    phase: Literal["queued", "preparing", "may_dispatch", "completed"] = "queued"


@dataclass
class _Session:
    key: SessionKey
    provider: LspProvider
    incarnation: str = field(default_factory=lambda: uuid.uuid4().hex)
    state: str = "STARTING"
    queue: queue.Queue[RequestTicket] = field(default_factory=lambda: queue.Queue(maxsize=8))
    stop: threading.Event = field(default_factory=threading.Event)
    owner_thread: threading.Thread | None = None
    process: OwnedProcess | None = None
    transport: LspTransport | None = None
    current: RequestTicket | None = None
    next_id: int = 1
    documents: dict[str, DocumentState] = field(default_factory=dict)
    document_bytes: int = 0
    pending_document_closes: set[str] = field(default_factory=set)
    capabilities: dict[str, object] = field(default_factory=dict)
    last_activity: float = field(default_factory=time.monotonic)
    rss_bytes: int = 0
    cpu_seconds: float = 0.0
    cpu_samples: dict[tuple[int, float], float] = field(default_factory=dict)


def _error(code: str = "lsp_session_failed") -> ToolError:
    return ToolError(code, "The language session is unavailable or its request could not complete safely.")


def _finish(ticket: RequestTicket, *, result: SessionReply | None = None, error: ToolError | None = None) -> None:
    ticket.phase = "completed"
    if ticket.future.done():
        return
    try:
        if error is None:
            assert result is not None
            ticket.future.set_result(result)
        else:
            ticket.future.set_exception(error)
    except InvalidStateError:
        # Cancellation of the asyncio bridge can win between done() and publication.
        pass


class LspSessionManager:
    def __init__(self, providers: tuple[LspProvider, ...], *, generation: str) -> None:
        self._providers = providers
        self._generation = generation
        self._lock = threading.RLock()
        self._sessions: dict[SessionKey, _Session] = {}
        self._closed = False
        self._preparing_count = 0
        self._closing_roots: set[str] = set()
        self._starts: OrderedDict[SessionKey, list[float]] = OrderedDict()
        self._spawn_count = 0
        self._reuse_count = 0
        self._failed_count = 0
        self._restart_count = 0
        self._context_results: OrderedDict[_ContextKey, _ContextEntry] = OrderedDict()
        self._context_bytes = 0
        self._context_epoch = 0
        self._context_preparing = 0
        self._context_flights: dict[_ContextKey, _ContextFlight] = {}

    def _invalidate_context_locked(self) -> None:
        self._context_epoch += 1
        self._context_results.clear()
        self._context_bytes = 0

    def _cancel_context_tasks(self, root: str | None = None) -> None:
        with self._lock:
            tasks = tuple(flight.task for key, flight in self._context_flights.items() if root is None or key[0] == root)
        for task in tasks:
            if task is not None:
                try:
                    task.get_loop().call_soon_threadsafe(task.cancel)
                except RuntimeError:
                    # A closed producer loop has already run task cancellation/finally.
                    pass

    def _context_generation_live(self, key: SessionKey | None, generation: str | None) -> bool:
        # Caller holds the manager lock. A result's identity comes from its owner reply.
        session = self._sessions.get(key) if key is not None else None
        return bool(
            not self._closed and key is not None and key.workspace_root not in self._closing_roots
            and session is not None and session.state == "READY" and not session.stop.is_set()
            and generation == session_generation(
                key, session.incarnation,
                tuple(sorted((uri, doc.version, doc.sha256) for uri, doc in session.documents.items())),
            )
        )

    def _context_provider_key(self, root: Path, language_id: str) -> SessionKey | None:
        try:
            provider = validate_provider(root, language_id, providers=self._providers)
            return make_session_key(root, language_id, provider, self._generation)
        except ToolError:
            # The builder owns explicit incomplete/fallback output for unavailable providers.
            return None

    async def acontext_result(
        self, root: Path, language_id: str, request_key: str, *,
        fingerprint: Callable[[], Awaitable[str | None]],
        build: Callable[[], Awaitable[tuple[bytes, str | None]]], timeout_sec: float = 35,
    ) -> bytes:
        """Share/cache bounded semantic bytes, fenced by full-source and owner evidence."""
        if not isinstance(request_key, str) or len(request_key) != 64 or any(c not in "0123456789abcdef" for c in request_key):
            raise _error("lsp_params_invalid")
        if isinstance(timeout_sec, bool) or not math.isfinite(timeout_sec) or not 0 < timeout_sec <= 300:
            raise _error("lsp_timeout_invalid")
        try:
            return await asyncio.wait_for(
                self._context_result(root, language_id, request_key, fingerprint, build, timeout_sec), timeout_sec,
            )
        except asyncio.TimeoutError:
            raise _error("lsp_timeout") from None
        except ToolError:
            raise
        except Exception:
            raise _error("lsp_context_failed") from None

    async def _context_result(
        self, root: Path, language_id: str, request_key: str,
        fingerprint: Callable[[], Awaitable[str | None]],
        build: Callable[[], Awaitable[tuple[bytes, str | None]]], timeout_sec: float,
    ) -> bytes:
        with self._lock:
            if self._closed:
                raise _error()
            if self._context_preparing + len(self._context_flights) >= _CONTEXT_MAX_RESULTS:
                raise _error("lsp_capacity_exceeded")
            self._context_preparing += 1
            epoch = self._context_epoch

        async def prepare() -> tuple[Path, SessionKey | None, str | None]:
            try:
                resolved = await asyncio.to_thread(resolve_path, root)
                prepared_key = await asyncio.to_thread(self._context_provider_key, resolved, language_id)
                digest = await fingerprint()
                if digest is not None and (not isinstance(digest, str) or len(digest) > 256):
                    raise _error("lsp_params_invalid")
                return resolved, prepared_key, digest
            finally:
                with self._lock:
                    self._context_preparing -= 1

        preparation = asyncio.create_task(asyncio.wait_for(prepare(), timeout_sec))
        preparation.add_done_callback(lambda done: None if done.cancelled() else done.exception())
        root, provider_key, before = await asyncio.shield(preparation)
        canonical = canonical_path(root)
        result: tuple[bytes, str | None]
        with self._lock:
            if self._closed or canonical in self._closing_roots:
                raise _error()
            if epoch != self._context_epoch:
                raise _error("lsp_document_changed")
            now = time.monotonic()
            for old_key, old_entry in tuple(self._context_results.items()):
                if old_entry.expires <= now:
                    self._context_bytes -= len(self._context_results.pop(old_key).payload)
            cache_key: _ContextKey = (canonical, language_id, provider_key, request_key, before, epoch,
                                      "" if before is not None else uuid.uuid4().hex)
            entry = self._context_results.get(cache_key)
            if entry is not None and not self._context_generation_live(provider_key, entry.generation):
                self._context_bytes -= len(self._context_results.pop(cache_key).payload)
                entry = None
            if entry is not None:
                self._context_results.move_to_end(cache_key)
                result = (entry.payload, entry.generation)
                flight = None
                owns = False
            else:
                session = self._sessions.get(provider_key) if provider_key is not None else None
                owner_snapshot = "" if session is None else session_generation(
                    session.key, session.incarnation,
                    tuple(sorted((uri, doc.version, doc.sha256) for uri, doc in session.documents.items())),
                ) + ":" + session.state
                flight_key: _ContextKey = (
                    canonical, language_id, provider_key, request_key, before, epoch,
                    owner_snapshot if before is not None else cache_key[6],
                )
                flight = self._context_flights.get(flight_key)
                owns = flight is None
                if flight is None:
                    if len(self._context_flights) >= _CONTEXT_MAX_RESULTS:
                        raise _error("lsp_capacity_exceeded")
                    flight = _ContextFlight()
                    self._context_flights[flight_key] = flight
        if owns:
            assert flight is not None
            task = asyncio.create_task(self._produce_context(flight_key, flight, fingerprint, build, timeout_sec))
            task.add_done_callback(lambda _done: self._finish_context_flight(flight_key, flight))
            with self._lock:
                flight.task = task
        if flight is not None:
            # Each bridge is local to its waiter's event loop; cancellation cannot poison peers.
            result = await self._await_context_flight(flight)
        after = await fingerprint()
        fresh_key = await asyncio.to_thread(self._context_provider_key, root, language_id)
        with self._lock:
            if self._closed or epoch != self._context_epoch or before != after or fresh_key != provider_key:
                raise _error("lsp_document_changed")
            if result[1] is not None and not self._context_generation_live(provider_key, result[1]):
                raise _error("lsp_document_changed")
        return result[0]

    async def _await_context_flight(self, flight: _ContextFlight) -> tuple[bytes, str | None]:
        loop = asyncio.get_running_loop()
        bridge: asyncio.Future[tuple[bytes, str | None]] = loop.create_future()

        def complete(shared: Future[tuple[bytes, str | None]]) -> None:
            def publish() -> None:
                if bridge.done():
                    return
                try:
                    bridge.set_result(shared.result())
                except BaseException as error:
                    bridge.set_exception(error)
                    # Observe now: a shutting-down loop may not run another callback turn.
                    bridge.exception()
            try:
                loop.call_soon_threadsafe(publish)
            except RuntimeError:
                pass  # The waiter's loop closed; other loops still receive the shared result.

        flight.result.add_done_callback(complete)
        try:
            return await asyncio.shield(bridge)
        finally:
            bridge.cancel()  # This local bridge has no cancellation link to the shared Future.

    def _finish_context_flight(self, key: _ContextKey, flight: _ContextFlight) -> None:
        # Also runs if a task was cancelled before its coroutine entered its try/finally.
        if not flight.result.done():
            flight.result.set_exception(_error("lsp_context_failed"))
        with self._lock:
            if self._context_flights.get(key) is flight:
                self._context_flights.pop(key)

    async def _produce_context(
        self, key: _ContextKey, flight: _ContextFlight,
        fingerprint: Callable[[], Awaitable[str | None]],
        build: Callable[[], Awaitable[tuple[bytes, str | None]]], timeout_sec: float,
    ) -> None:
        try:
            async def produce() -> tuple[bytes, str | None]:
                with self._lock:
                    if self._closed or key[5] != self._context_epoch:
                        raise _error("lsp_document_changed")
                payload, generation = await build()
                if not isinstance(payload, bytes) or (generation is not None and not isinstance(generation, str)):
                    raise _error("lsp_params_invalid")
                after = await fingerprint()
                fresh_key = await asyncio.to_thread(self._context_provider_key, Path(key[0]), key[1])
                with self._lock:
                    if self._closed or key[5] != self._context_epoch or key[4] != after or key[2] != fresh_key:
                        raise _error("lsp_document_changed")
                    if generation is not None and not self._context_generation_live(key[2], generation):
                        raise _error("lsp_document_changed")
                    if key[4] is not None and len(payload) <= _CONTEXT_MAX_BYTES and generation is not None:
                        entry_key: _ContextKey = (key[0], key[1], key[2], key[3], key[4], key[5], "")
                        old_entry = self._context_results.pop(entry_key, None)
                        if old_entry is not None:
                            self._context_bytes -= len(old_entry.payload)
                        self._context_results[entry_key] = _ContextEntry(payload, generation, time.monotonic() + _CONTEXT_TTL_SEC)
                        self._context_bytes += len(payload)
                        while len(self._context_results) > _CONTEXT_MAX_RESULTS or self._context_bytes > _CONTEXT_MAX_BYTES:
                            _, old = self._context_results.popitem(last=False)
                            self._context_bytes -= len(old.payload)
                return payload, generation

            result = await asyncio.wait_for(produce(), timeout_sec)
            flight.result.set_result(result)
        except BaseException as error:
            # Always wake cross-loop waiters, including when the producer loop shuts down.
            flight.result.set_exception(error if isinstance(error, ToolError) else _error("lsp_context_failed"))
        finally:
            self._finish_context_flight(key, flight)

    async def arequest(
        self, root: Path, language_id: str, method: str, params: dict[str, object], *, timeout_sec: float,
    ) -> object:
        return (await self.arequest_with_evidence(root, language_id, method, params, timeout_sec=timeout_sec)).value

    async def arequest_with_evidence(
        self, root: Path, language_id: str, method: str, params: dict[str, object], *, timeout_sec: float,
    ) -> SessionReply:
        started = time.monotonic()
        if isinstance(timeout_sec, bool) or not math.isfinite(timeout_sec) or not 0 < timeout_sec <= 300:
            raise _error("lsp_timeout_invalid")
        deadline = started + timeout_sec
        with self._lock:
            if self._closed:
                raise _error()
            if self._preparing_count + sum(item.queue.qsize() for item in self._sessions.values()) >= 32:
                raise _error("lsp_capacity_exceeded")
            self._preparing_count += 1

        async def prepare() -> tuple[dict[str, object], LspProvider, SessionKey]:
            nonlocal root
            try:
                root = await asyncio.to_thread(resolve_path, root)
                return await asyncio.to_thread(self._prepare_admission, root, language_id, method, params, deadline)
            finally:
                with self._lock:
                    self._preparing_count -= 1

        preparation = asyncio.create_task(prepare())
        preparation.add_done_callback(lambda done: None if done.cancelled() else done.exception())
        try:
            validated, provider, key = await asyncio.wait_for(
                asyncio.shield(preparation), timeout=max(0, deadline - time.monotonic()),
            )
        except ToolError as error:
            if error.code in {"lsp_provider_unavailable", "lsp_provider_ambiguous", "lsp_provider_changed"}:
                await self._retire_obsolete(root, language_id)
            raise
        await self._retire_obsolete(root, language_id, keep=key)
        payload = json.dumps({"method": method, "params": validated}, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        if len(payload) > 64 * 1024:
            raise _error("lsp_params_invalid")
        if time.monotonic() >= deadline:
            raise _error("lsp_timeout")
        launch: threading.Thread | None = None
        with self._lock:
            if self._closed or key.workspace_root in self._closing_roots:
                raise _error()
            for old_key, old in list(self._sessions.items()):
                if old.state == "CLOSED":
                    del self._sessions[old_key]
            session = self._sessions.get(key)
            if session is not None and (session.stop.is_set() or session.state not in {"STARTING", "READY"}):
                raise _error()
            if self._preparing_count + sum(item.queue.qsize() for item in self._sessions.values()) >= 32:
                raise _error("lsp_capacity_exceeded")
            if session is None:
                if len(self._sessions) >= 4:
                    raise _error("lsp_capacity_exceeded")
                self._admit_start(key, time.monotonic())
                session = _Session(key, provider)
                launch = threading.Thread(target=self._own, args=(session, deadline), name="lsp-session-owner", daemon=True)
                session.owner_thread = launch
                self._sessions[key] = session
            session.next_id += 1
            ticket = RequestTicket(session.next_id, payload, deadline, threading.Event(), Future())
            try:
                session.queue.put_nowait(ticket)
            except queue.Full:
                raise _error("lsp_capacity_exceeded") from None
            if launch is None:
                self._reuse_count += 1
        if launch is not None:
            try:
                launch.start()
            except RuntimeError:
                with self._lock:
                    session.state = "CLOSED"
                    self._failed_count += 1
                    self._fail_queued(session)
        try:
            return await asyncio.wait_for(asyncio.wrap_future(ticket.future), timeout=max(deadline - time.monotonic(), 0))
        except (asyncio.CancelledError, asyncio.TimeoutError):
            ticket.cancelled.set()
            raise

    def _prepare_admission(
        self, root: Path, language_id: str, method: str, params: dict[str, object], deadline: float,
    ) -> tuple[dict[str, object], LspProvider, SessionKey]:
        if time.monotonic() >= deadline:
            raise _error("lsp_timeout")
        try:
            validated = validate_request(root, method, params)
        except ToolError as error:
            if error.code == "lsp_document_unavailable":
                self._forget_missing_document(root, language_id, params)
            raise
        provider = validate_provider(root, language_id, providers=self._providers)
        return validated, provider, make_session_key(root, language_id, provider, self._generation)

    def _admit_start(self, key: SessionKey, now: float) -> None:
        for old_key, times in list(self._starts.items()):
            retained = [stamp for stamp in times if now - stamp < 60]
            if retained:
                self._starts[old_key] = retained
            else:
                del self._starts[old_key]
        times = self._starts.get(key, [])
        if len(times) >= 2 or (times and now - times[-1] < 5):
            raise _error("lsp_restart_limited")
        if key not in self._starts and len(self._starts) >= 32:
            raise _error("lsp_capacity_exceeded")
        if times:
            self._restart_count += 1
        self._starts[key] = [*times, now]

    async def _retire_obsolete(self, root: Path, language_id: str, *, keep: SessionKey | None = None) -> None:
        canonical = keep.workspace_root if keep is not None else canonical_path(root)
        with self._lock:
            obsolete = [
                session for key, session in self._sessions.items()
                if key.workspace_root == canonical and key.language_id == language_id and key != keep and session.state != "CLOSED"
            ]
            if any(session.stop.is_set() for session in obsolete):
                raise _error()
            if obsolete:
                self._invalidate_context_locked()
            for session in obsolete:
                session.stop.set()
                if session.current is not None:
                    session.current.cancelled.set()
                self._fail_queued(session)
        if obsolete:
            self._cancel_context_tasks(canonical)
            await asyncio.to_thread(self._drain_sessions, obsolete, 2.0)
            with self._lock:
                if any(session.state != "CLOSED" for session in obsolete):
                    raise _error()

    def _forget_missing_document(self, root: Path, language_id: str, params: dict[str, object]) -> None:
        document = params.get("textDocument")
        uri = document.get("uri") if isinstance(document, dict) else None
        if not isinstance(uri, str):
            return
        try:
            canonical_uri = uri_to_path(uri, root).as_uri()
            canonical_root = canonical_path(root)
        except (ToolError, ValueError, OSError):
            return
        with self._lock:
            for key, session in self._sessions.items():
                if key.workspace_root == canonical_root and key.language_id == language_id and canonical_uri in session.documents:
                    session.pending_document_closes.intersection_update(session.documents)
                    session.pending_document_closes.add(canonical_uri)

    def _close_pending_documents(self, session: _Session) -> None:
        with self._lock:
            uris = tuple(session.pending_document_closes)
            session.pending_document_closes.clear()
        for uri in uris:
            self._remove_document(session, uri)

    def _remove_document(self, session: _Session, uri: str) -> None:
        document = session.documents.get(uri)
        if document is None:
            return
        assert session.transport is not None
        session.transport.send({"jsonrpc": "2.0", "method": "textDocument/didClose", "params": {"textDocument": {"uri": uri}}})
        size = len(document.text.encode("utf-8"))
        with self._lock:
            del session.documents[uri]
            self._context_results.clear()
            self._context_bytes = 0
            session.pending_document_closes.discard(uri)
            session.document_bytes -= size

    def _prepare_document(
        self, session: _Session, uri: str, deadline: float, *, protected: frozenset[str] = frozenset(),
    ) -> DocumentState:
        sync = session.capabilities.get("textDocumentSync")
        if not isinstance(sync, dict) or sync.get("openClose") is not True or type(sync.get("change")) is not int:
            raise _error("lsp_sync_unsupported")
        kind = sync["change"]
        if kind not in {0, 1, 2}:
            raise _error("lsp_sync_unsupported")
        try:
            path, digest, text, size = _disk_document(uri, Path(session.key.workspace_root), deadline)
        except ToolError as error:
            if error.code == "lsp_document_unavailable":
                self._remove_document(session, uri)
            raise
        canonical_uri = path.as_uri()
        old = session.documents.get(canonical_uri)
        if old is not None and old.sha256 == digest:
            old.last_used = time.monotonic()
            return old
        old_size = len(old.text.encode("utf-8")) if old is not None else 0
        while (
            len(session.documents) + (old is None) > 16
            or session.document_bytes - old_size + size > _MAX_DOCUMENT_TOTAL_BYTES
        ):
            candidates = [doc for key, doc in session.documents.items() if key != canonical_uri and key not in protected]
            if not candidates:
                raise _error("lsp_document_capacity")
            self._remove_document(session, min(candidates, key=lambda doc: doc.last_used).uri)
        version = old.version + 1 if old is not None else 1
        assert session.transport is not None
        channel = session.transport
        if old is None or kind == 0:
            if old is not None:
                self._remove_document(session, canonical_uri)
                old_size = 0
            language = _language_id(path)
            if language == "plaintext":
                language = session.key.language_id
            channel.send({
                "jsonrpc": "2.0", "method": "textDocument/didOpen",
                "params": {"textDocument": {"uri": canonical_uri, "languageId": language, "version": version, "text": text}},
            })
        else:
            change: dict[str, object] = {"text": text}
            if kind == 2:
                lines = old.text.split("\n")
                change["range"] = {
                    "start": {"line": 0, "character": 0},
                    "end": {"line": len(lines) - 1, "character": len(lines[-1].encode("utf-16-le")) // 2},
                }
            channel.send({
                "jsonrpc": "2.0", "method": "textDocument/didChange",
                "params": {"textDocument": {"uri": canonical_uri, "version": version}, "contentChanges": [change]},
            })
        save = sync.get("save")
        if old is not None and (save is True or isinstance(save, dict)):
            params: dict[str, object] = {"textDocument": {"uri": canonical_uri}}
            if isinstance(save, dict) and save.get("includeText") is True:
                params["text"] = text
            channel.send({"jsonrpc": "2.0", "method": "textDocument/didSave", "params": params})
        document = DocumentState(canonical_uri, path, digest, version, text, time.monotonic())
        with self._lock:
            if old is not None:
                self._context_results.clear()
                self._context_bytes = 0
            session.documents[canonical_uri] = document
            session.document_bytes += size - old_size
        return document

    def _prepare_documents(self, session: _Session, message: dict[str, object], deadline: float) -> dict[str, DocumentState]:
        params = message.get("params")
        raw_document = params.get("textDocument") if isinstance(params, dict) else None
        if message.get("method") in _HIERARCHY_ITEM_METHODS and isinstance(params, dict):
            raw_document = params.get("item")
        uri = raw_document.get("uri") if isinstance(raw_document, dict) else None
        primary_uri: str | None = None
        if isinstance(raw_document, dict) and isinstance(uri, str):
            document = self._prepare_document(session, uri, deadline)
            primary_uri = document.uri
            raw_document["uri"] = document.uri
            position = params.get("position") if isinstance(params, dict) else None
            if isinstance(position, dict):
                position_to_offset(document.text, position)
            if message.get("method") in _HIERARCHY_ITEM_METHODS:
                for span_name in ("range", "selectionRange"):
                    for endpoint in ("start", "end"):
                        position_to_offset(document.text, raw_document[span_name][endpoint])
        protected = frozenset(session.documents)
        for other_uri in list(session.documents):
            if other_uri != primary_uri:
                last_used = session.documents[other_uri].last_used
                refreshed = self._prepare_document(session, other_uri, deadline, protected=protected)
                refreshed.last_used = last_used
        return dict(session.documents)

    def _verify_documents(self, session: _Session, documents: Mapping[str, DocumentState], deadline: float) -> None:
        for uri, document in documents.items():
            try:
                _path, digest, _text, _size = _disk_document(uri, Path(session.key.workspace_root), deadline)
            except ToolError as error:
                if error.code == "lsp_timeout":
                    raise
                if error.code == "lsp_document_unavailable":
                    self._remove_document(session, uri)
                raise _error("lsp_result_stale") from None
            if digest != document.sha256:
                raise _error("lsp_result_stale")

    def _sample(self, session: _Session, *, cpu_baseline: float | None = None) -> None:
        assert session.process is not None
        if session.process.poll() is not None:
            raise _error()
        try:
            root = psutil.Process(session.process.pid)
            processes = [root, *root.children(recursive=True)]
            if len(processes) > 512:
                raise _error("lsp_resource_exceeded")
            rss = 0
            handles = 0
            consumed = 0.0
            samples: dict[tuple[int, float], float] = {}
            for process in processes:
                rss += process.memory_info().rss
                times = process.cpu_times()
                identity = (process.pid, process.create_time())
                cpu = times.user + times.system
                samples[identity] = cpu
                consumed += max(0.0, cpu - session.cpu_samples.get(identity, 0.0))
                handles += process.num_handles() if os.name == "nt" else process.num_fds()
        except (psutil.Error, OSError, AttributeError):
            raise _error("lsp_resource_measurement_unavailable") from None
        with self._lock:
            session.rss_bytes = rss
            session.cpu_seconds += consumed
            session.cpu_samples = samples
            total = sum(item.rss_bytes for item in self._sessions.values() if item.state != "CLOSED")
        if rss > _MAX_RSS_BYTES or total > _MAX_TOTAL_RSS_BYTES or handles > 512:
            raise _error("lsp_resource_exceeded")
        if cpu_baseline is not None and session.cpu_seconds - cpu_baseline > 30:
            raise _error("lsp_resource_exceeded")

    def _receive(
        self, session: _Session, deadline: float, cancel: threading.Event, *, cpu_baseline: float,
    ) -> dict[str, object]:
        assert session.transport is not None
        self._sample(session, cpu_baseline=cpu_baseline)
        while True:
            try:
                response = session.transport.receive(deadline=min(deadline, time.monotonic() + _SAMPLE_SEC), cancel=cancel)
                self._sample(session, cpu_baseline=cpu_baseline)
                return response
            except ToolError as error:
                if error.code != "lsp_timeout" or time.monotonic() >= deadline:
                    raise
                self._sample(session, cpu_baseline=cpu_baseline)

    def _own(self, session: _Session, startup_deadline: float) -> None:
        failed = False
        try:
            if session.stop.is_set() or time.monotonic() >= startup_deadline:
                return
            session.process = open_transport(session.provider, Path(session.key.workspace_root))
            with self._lock:
                self._spawn_count += 1
            if session.stop.is_set():
                raise _error("lsp_cancelled")
            channel = LspTransport(session.process)
            session.transport = channel
            self._sample(session)
            cpu_baseline = session.cpu_seconds
            if session.stop.is_set() or time.monotonic() >= startup_deadline:
                return
            channel.send({
                "jsonrpc": "2.0", "id": 1, "method": "initialize",
                "params": {"processId": None, "rootUri": Path(session.key.workspace_root).as_uri(),
                           "capabilities": {"workspace": {"configuration": True, "applyEdit": False}}},
            })
            initialized = self._receive(
                session, min(startup_deadline, time.monotonic() + 30), session.stop, cpu_baseline=cpu_baseline,
            )
            result = initialized.get("result")
            if "error" in initialized or not isinstance(result, dict) or not isinstance(result.get("capabilities"), dict):
                raise _error()
            session.capabilities = result["capabilities"]
            channel.send({"jsonrpc": "2.0", "method": "initialized", "params": {}})
            with self._lock:
                if session.stop.is_set():
                    return
                session.state = "READY"
            while not session.stop.is_set():
                if channel.wire_bytes >= _MAX_WIRE_BYTES:
                    break
                self._close_pending_documents(session)
                try:
                    ticket = session.queue.get(timeout=min(_SAMPLE_SEC, 0.5))
                except queue.Empty:
                    self._sample(session)
                    if time.monotonic() - session.last_activity >= _IDLE_SEC:
                        break
                    continue
                self._close_pending_documents(session)
                session.current = ticket
                if ticket.cancelled.is_set() or ticket.future.cancelled() or ticket.deadline <= time.monotonic():
                    _finish(ticket, error=_error("lsp_timeout"))
                    session.current = None
                    continue
                ticket.phase = "preparing"
                channel.begin_request()
                self._sample(session)
                cpu_baseline = session.cpu_seconds
                message = json.loads(ticket.payload)
                try:
                    documents = self._prepare_documents(session, message, ticket.deadline)
                except ToolError as error:
                    if error.code not in _DOCUMENT_ERRORS:
                        raise
                    self._sample(session, cpu_baseline=cpu_baseline)
                    _finish(ticket, error=_error(error.code))
                    session.current = None
                    continue
                self._sample(session, cpu_baseline=cpu_baseline)
                if ticket.cancelled.is_set() or session.stop.is_set() or ticket.deadline <= time.monotonic():
                    _finish(ticket, error=_error("lsp_cancelled"))
                    session.current = None
                    continue
                ticket.phase = "may_dispatch"
                channel.send({"jsonrpc": "2.0", "id": ticket.request_id, **message})
                try:
                    response = self._receive(session, ticket.deadline, ticket.cancelled, cpu_baseline=cpu_baseline)
                except ToolError as error:
                    if error.code in {"lsp_cancelled", "lsp_timeout"}:
                        channel.send({"jsonrpc": "2.0", "method": "$/cancelRequest", "params": {"id": ticket.request_id}})
                        try:
                            channel.receive(deadline=time.monotonic() + 0.25, cancel=threading.Event())
                        except ToolError:
                            pass
                    raise
                if "error" in response or response.get("complete") is False:
                    _finish(ticket, error=_error("lsp_request_failed"))
                else:
                    try:
                        self._verify_documents(session, documents, ticket.deadline)
                    except ToolError as error:
                        if error.code != "lsp_result_stale":
                            raise
                        _finish(ticket, error=_error("lsp_result_stale"))
                    else:
                        value = response.get("result")
                        if message.get("method") == "textDocument/rename" and isinstance(value, dict):
                            value = RenameResult(value, documents)
                        capabilities = frozenset(
                            name for name in _EVIDENCE_CAPABILITIES
                            if session.capabilities.get(name) is True or isinstance(session.capabilities.get(name), dict)
                        )
                        reply = SessionReply(
                            value, session.key, session.incarnation, capabilities,
                            tuple(sorted((uri, document.version, document.sha256) for uri, document in documents.items())),
                        )
                        _finish(ticket, result=reply)
                session.current = None
                session.last_activity = time.monotonic()
                if channel.wire_bytes >= _MAX_WIRE_BYTES:
                    break
            with self._lock:
                session.state = "DRAINING"
            self._shutdown(session)
        except Exception as error:
            failed = True
            with self._lock:
                self._failed_count += 1
                session.state = "FAILED"
            if session.current is not None:
                code = "lsp_session_failed"
                if isinstance(error, ToolError) and error.code in {
                    "lsp_resource_measurement_unavailable", "lsp_resource_exceeded", "lsp_timeout", "lsp_cancelled",
                    "lsp_ownership_unavailable",
                }:
                    code = error.code
                _finish(session.current, error=_error(code))
            if session.process is not None:
                try:
                    session.process.terminate_tree(force=True)
                except (ToolError, OSError):
                    pass
        finally:
            with self._lock:
                session.stop.set()
                self._fail_queued(session)
            closed = True
            try:
                if session.transport is not None:
                    closed = session.transport.close()
                elif session.process is not None:
                    closed = close_transport(session.process, timeout_sec=0.5)
            except Exception:
                closed = False
            with self._lock:
                session.current = None
                session.state = "CLOSED" if closed else "QUARANTINED"
                if closed:
                    session.rss_bytes = 0
                    session.documents.clear()
                    session.document_bytes = 0
                    session.pending_document_closes.clear()
                elif not failed:
                    self._failed_count += 1

    def _shutdown(self, session: _Session) -> None:
        assert session.transport is not None
        channel = session.transport
        for uri in list(session.documents):
            self._remove_document(session, uri)
        session.next_id += 1
        channel.send({"jsonrpc": "2.0", "id": session.next_id, "method": "shutdown", "params": None})
        response = channel.receive(deadline=time.monotonic() + 0.5, cancel=threading.Event())
        if "error" in response:
            raise _error()
        channel.send({"jsonrpc": "2.0", "method": "exit", "params": {}})

    @staticmethod
    def _fail_queued(session: _Session) -> None:
        while True:
            try:
                _finish(session.queue.get_nowait(), error=_error())
            except queue.Empty:
                return

    def close_all(self, *, timeout_sec: float = 5.0) -> None:
        if not math.isfinite(timeout_sec) or timeout_sec < 0:
            raise ValueError("Close timeout must be finite and non-negative.")
        with self._lock:
            self._closed = True
            self._invalidate_context_locked()
            sessions = list(self._sessions.values())
        self._cancel_context_tasks()
        self._drain_sessions(sessions, min(timeout_sec, 5.0))

    def close_workspace(self, root: Path, *, timeout_sec: float = 2.0) -> None:
        if not math.isfinite(timeout_sec) or timeout_sec < 0:
            raise ValueError("Close timeout must be finite and non-negative.")
        canonical = canonical_path(root)
        with self._lock:
            self._closing_roots.add(canonical)
            self._invalidate_context_locked()
            sessions = [item for key, item in self._sessions.items() if key.workspace_root == canonical]
        self._cancel_context_tasks(canonical)
        try:
            self._drain_sessions(sessions, min(timeout_sec, 2.0))
        finally:
            with self._lock:
                self._closing_roots.discard(canonical)

    def _drain_sessions(self, sessions: list[_Session], timeout_sec: float) -> None:
        deadline = time.monotonic() + timeout_sec
        with self._lock:
            for session in sessions:
                session.stop.set()
                if session.current is not None:
                    session.current.cancelled.set()
                self._fail_queued(session)
        try:
            for session in sessions:
                owner = session.owner_thread
                if owner is not None and owner.ident is not None:
                    owner.join(max(0, deadline - time.monotonic()))
        finally:
            with self._lock:
                for session in sessions:
                    if session.state != "CLOSED":
                        session.state = "QUARANTINED"

    def snapshot(self) -> dict[str, int]:
        with self._lock:
            sessions = list(self._sessions.values())
            return {
                "spawn_count": self._spawn_count, "reuse_count": self._reuse_count, "failed": self._failed_count,
                "restart_count": self._restart_count, "active": sum(item.state != "CLOSED" for item in sessions),
                "ready": sum(item.state == "READY" for item in sessions),
                "quarantined": sum(item.state == "QUARANTINED" for item in sessions),
                "queue_depth": sum(item.queue.qsize() for item in sessions),
                "preparing": self._preparing_count,
                "in_flight": sum(item.current is not None for item in sessions),
                "rss_bytes": sum(item.rss_bytes for item in sessions),
                "documents": sum(len(item.documents) for item in sessions),
                "document_bytes": sum(item.document_bytes for item in sessions),
            }
