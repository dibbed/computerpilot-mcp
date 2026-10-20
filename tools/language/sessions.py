"""Bounded, opt-in language sessions with one process and protocol owner per key."""

from __future__ import annotations

import asyncio
import json
import math
import os
import queue
import threading
import time
from collections import OrderedDict
from concurrent.futures import Future, InvalidStateError
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import psutil

from core.errors import ToolError
from core.process_ownership import OwnedProcess
from core.resource_locks import canonical_path
from tools.language.providers import LspProvider, SessionKey, make_session_key, validate_provider
from tools.language.requests import validate_request
from tools.language.transport import LspTransport, close_transport, open_transport

_IDLE_SEC = 120.0
_SAMPLE_SEC = 0.5
_MAX_WIRE_BYTES = 64 * 1024 * 1024
_MAX_RSS_BYTES = 512 * 1024 * 1024
_MAX_TOTAL_RSS_BYTES = 2 * 1024 * 1024 * 1024


@dataclass
class RequestTicket:
    request_id: int
    payload: bytes
    deadline: float
    cancelled: threading.Event
    future: Future[object]
    phase: Literal["queued", "preparing", "may_dispatch", "completed"] = "queued"


@dataclass
class _Session:
    key: SessionKey
    provider: LspProvider
    state: str = "STARTING"
    queue: queue.Queue[RequestTicket] = field(default_factory=lambda: queue.Queue(maxsize=8))
    stop: threading.Event = field(default_factory=threading.Event)
    owner_thread: threading.Thread | None = None
    process: OwnedProcess | None = None
    transport: LspTransport | None = None
    current: RequestTicket | None = None
    next_id: int = 1
    documents: dict[str, object] = field(default_factory=dict)
    capabilities: dict[str, object] = field(default_factory=dict)
    last_activity: float = field(default_factory=time.monotonic)
    rss_bytes: int = 0
    cpu_seconds: float = 0.0


def _error(code: str = "lsp_session_failed") -> ToolError:
    return ToolError(code, "The language session is unavailable or its request could not complete safely.")


def _finish(ticket: RequestTicket, *, result: object = None, error: ToolError | None = None) -> None:
    ticket.phase = "completed"
    if ticket.future.done():
        return
    try:
        if error is None:
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
        self._closing_roots: set[str] = set()
        self._starts: OrderedDict[SessionKey, list[float]] = OrderedDict()
        self._spawn_count = 0
        self._reuse_count = 0
        self._failed_count = 0
        self._restart_count = 0

    async def arequest(
        self, root: Path, language_id: str, method: str, params: dict[str, object], *, timeout_sec: float,
    ) -> object:
        started = time.monotonic()
        if isinstance(timeout_sec, bool) or not math.isfinite(timeout_sec) or not 0 < timeout_sec <= 300:
            raise _error("lsp_timeout_invalid")
        deadline = started + timeout_sec
        validated = validate_request(root, method, params)
        provider = validate_provider(root, language_id, providers=self._providers)
        key = make_session_key(root, language_id, provider, self._generation)
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
            if sum(item.queue.qsize() for item in self._sessions.values()) >= 32:
                raise _error("lsp_capacity_exceeded")
            if session is None:
                if len(self._sessions) >= 4:
                    raise _error("lsp_capacity_exceeded")
                self._admit_start(key, started)
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

    def _sample(self, session: _Session, *, cpu_baseline: float | None = None) -> None:
        assert session.process is not None
        if session.process.poll() is not None:
            raise _error()
        try:
            root = psutil.Process(session.process.pid)
            processes = [root, *root.children(recursive=True)]
            rss = 0
            handles = 0
            cpu = 0.0
            for process in processes:
                rss += process.memory_info().rss
                times = process.cpu_times()
                cpu += times.user + times.system
                handles += process.num_handles() if os.name == "nt" else process.num_fds()
        except (psutil.Error, OSError, AttributeError):
            raise _error("lsp_resource_measurement_unavailable") from None
        with self._lock:
            session.rss_bytes = rss
            session.cpu_seconds = cpu
            total = sum(item.rss_bytes for item in self._sessions.values() if item.state != "CLOSED")
        if rss > _MAX_RSS_BYTES or total > _MAX_TOTAL_RSS_BYTES or handles > 512:
            raise _error("lsp_resource_exceeded")
        if cpu_baseline is not None and cpu - cpu_baseline > 30:
            raise _error("lsp_resource_exceeded")

    def _receive(self, session: _Session, deadline: float, cancel: threading.Event) -> dict[str, object]:
        assert session.transport is not None
        self._sample(session)
        baseline = session.cpu_seconds
        while True:
            try:
                response = session.transport.receive(deadline=min(deadline, time.monotonic() + _SAMPLE_SEC), cancel=cancel)
                self._sample(session, cpu_baseline=baseline)
                return response
            except ToolError as error:
                if error.code != "lsp_timeout" or time.monotonic() >= deadline:
                    raise
                self._sample(session, cpu_baseline=baseline)

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
            channel.send({
                "jsonrpc": "2.0", "id": 1, "method": "initialize",
                "params": {"processId": None, "rootUri": Path(session.key.workspace_root).as_uri(),
                           "capabilities": {"workspace": {"configuration": True, "applyEdit": False}}},
            })
            initialized = self._receive(session, min(startup_deadline, time.monotonic() + 30), session.stop)
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
                try:
                    ticket = session.queue.get(timeout=min(_SAMPLE_SEC, 0.5))
                except queue.Empty:
                    self._sample(session)
                    if time.monotonic() - session.last_activity >= _IDLE_SEC:
                        break
                    continue
                session.current = ticket
                if ticket.cancelled.is_set() or ticket.future.cancelled() or ticket.deadline <= time.monotonic():
                    _finish(ticket, error=_error("lsp_timeout"))
                    session.current = None
                    continue
                ticket.phase = "preparing"
                message = json.loads(ticket.payload)
                if ticket.cancelled.is_set() or session.stop.is_set() or ticket.deadline <= time.monotonic():
                    _finish(ticket, error=_error("lsp_cancelled"))
                    session.current = None
                    continue
                ticket.phase = "may_dispatch"
                channel.send({"jsonrpc": "2.0", "id": ticket.request_id, **message})
                try:
                    response = self._receive(session, ticket.deadline, ticket.cancelled)
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
                    _finish(ticket, result=response.get("result"))
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
                elif not failed:
                    self._failed_count += 1

    def _shutdown(self, session: _Session) -> None:
        assert session.transport is not None
        channel = session.transport
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

    def close_workspace(self, root: Path, *, timeout_sec: float = 2.0) -> None:
        if not math.isfinite(timeout_sec) or timeout_sec < 0:
            raise ValueError("Close timeout must be finite and non-negative.")
        canonical = canonical_path(root)
        deadline = time.monotonic() + min(timeout_sec, 2.0)
        with self._lock:
            self._closing_roots.add(canonical)
            sessions = [item for key, item in self._sessions.items() if key.workspace_root == canonical]
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
                with self._lock:
                    if owner is not None and owner.is_alive() and session.state != "CLOSED":
                        session.state = "QUARANTINED"
        finally:
            with self._lock:
                self._closing_roots.discard(canonical)

    def snapshot(self) -> dict[str, int]:
        with self._lock:
            sessions = list(self._sessions.values())
            return {
                "spawn_count": self._spawn_count, "reuse_count": self._reuse_count, "failed": self._failed_count,
                "restart_count": self._restart_count, "active": sum(item.state != "CLOSED" for item in sessions),
                "ready": sum(item.state == "READY" for item in sessions),
                "quarantined": sum(item.state == "QUARANTINED" for item in sessions),
                "queue_depth": sum(item.queue.qsize() for item in sessions),
                "in_flight": sum(item.current is not None for item in sessions),
                "rss_bytes": sum(item.rss_bytes for item in sessions),
                "documents": sum(len(item.documents) for item in sessions),
            }
