# Linux Reliability, Concurrency and Cancellation Hardening

Scope: follow-up hardening of ComputerPilot MCP v0.9.0 on Linux after the deep audit. Every claim
below is backed by real MCP execution (stdio and streamable HTTP), real subprocesses, real SQLite
state, and disposable fixtures. Windows/macOS behaviour was reviewed statically only.

Status: **four confirmed defects fixed** (HTTP cancellation contract, over-broad command
serialization, unreconciled stale claims after SIGKILL, cancellation-before-spawn race), with
regression tests and measured before/after behaviour.

## 1. Original defects and root causes

### D1 — No cancellation contract over streamable HTTP

`main.py` serves `streamable-http` with `stateless_http=True`. Every POST is an independent
transport, so the server never observes a client `notifications/cancelled` or disconnect. A client
that abandoned a long `run_shell` could not stop it; the command held the exclusive host claim until
the timeout. Root cause: stateless HTTP has no session to route a cancellation into, and no explicit
cancellation surface existed.

### D2 — Every command serialized on one host-wide exclusive claim

Terminal handlers took `coordination_claim(exclusive=True)` plus a write claim on the cwd subtree.
That is correct for opaque commands with unknown side effects, but it also serialized commands that
callers know are read-only, so four independent one-second commands took ~4.3 s and read-only
throughput was capped at one command. Root cause: no access classification and no separate
admission path for known-safe readers.

### D3 — Stale native claims required an operator after SIGKILL

`WorkflowStore.recover_interrupted()` ran only when a workflow store was lazily constructed. Native
commands use `WorkflowStore(..., recover=False)`, so a server killed while a command was active left
an `active` claim that no startup path reconciled. The next server blocked every conflicting command
until timeout (`resource_admission_timeout`, measured 30.12 s). Root cause: no startup recovery on
the native-command path, and no durable child identity to distinguish a live orphan from an exited
command.

### D4 — Cancellation race and operation-identity races

`run_bounded` checked the cancel flag only after spawning, so a cancellation arriving between
admission and spawn could still start a process. The first explicit-cancellation design also had no
duplicate-id guard and could never distinguish a completed command from a cancelled one. Root cause:
no reservation lifecycle for client-chosen operation identities.

## 2. Files changed

| File | Change |
| --- | --- |
| `core/operation_control.py` (new) | Bounded operation registry, id validation, explicit cancel, cancellable worker. |
| `core/command_concurrency.py` (new) | Bounded shared-command gate for read-only commands. |
| `tools/terminal/registry.py` | `operation_id` + `access` parameters on all terminal tools; `cancel_operation` tool; async cancellable dispatch. |
| `core/executor.py` | Pre-spawn cancellation check; durable dispatch identity recorded immediately after spawn. |
| `core/resource_claims.py` | `record_native_dispatch_identity()`; cancellation-aware admission (previous fix). |
| `core/workflow_store.py` | Schema v12 (`dispatch_pid`, `dispatch_process_created`, `dispatch_recorded_at`); `record_resource_dispatch()`; dispatch-aware startup reconciliation incl. process-group check. |
| `core/heartbeat.py` | Startup reconciliation before admitting work. |
| `core/health_snapshot.py` | `native_resource_recovery` counters in `workflow_health`. |
| `core/config.py` | `MCP_COMMAND_SHARED_CONCURRENCY` (default 4, range 1–32). |
| `core/tooling.py` | `cancel_operation` classified as a mutating tool operation. |
| `tests/test_operation_control.py` (new) | Registry, explicit cancel, duplicate ids, claim release. |
| `tests/test_command_concurrency.py` (new) | Read overlap vs exclusive serialization, bounded gate, writer conflict. |
| `tests/test_stale_claim_recovery.py` (new) | Dead/live dispatch, missing marker, live owner, SIGKILL. |
| `tests/test_http_operation_cancellation.py` (new) | Real streamable-HTTP explicit cancel and disconnect durability. |
| Existing tests | Catalogue counts (138 POSIX / 152 Windows), profile counts, schema v12, mutation-guard set. |

## 3. Implementation decisions

- **Explicit cancellation, not transport snooping.** `cancel_operation(operation_id)` is a normal
  tool call that works identically on stdio and stateless HTTP. Terminal tools accept an optional
  `operation_id`; when supplied, the identifier is the cancellation capability for that call.
- **Ids are bearer capabilities scoped to one server process.** They are validated to 8–128 safe
  characters, rejected while already in flight (`operation_id_in_use`), and bounded (256 terminal
  entries retained for 5 minutes). Cancellation never crosses processes; a restarted server reports
  `operation_not_found`, so nothing can be replayed or mis-cancelled.
- **Cancellation is a request, not a kill by name.** The registry only sets the event of an
  operation it owns. The executor polls every ≤100 ms, terminates the owned tree
  (`terminate_tree(force=True)`), closes ownership and pipes, then raises `command_cancelled`.
  Admission polling observes the same event and aborts before dispatch.
- **Read-only concurrency is opt-in and still fenced.** `access="read"` takes a read coordination
  claim plus a read claim on the cwd subtree. It therefore overlaps other readers but conflicts with
  any writer in that subtree and with every exclusive command/job. Defaults remain exclusive.
- **Bounded parallelism.** A process-local gate caps concurrent read-only commands at
  `MCP_COMMAND_SHARED_CONCURRENCY` (default 4). Waiters poll with cancellation and a timeout.
- **Dispatch identity is durable.** Immediately after spawn, the child PID and creation time are
  written to the active claim row. Recovery verifies PID **and** creation time, plus (POSIX) whether
  the owned process group still has members.

## 4. Cancellation behaviour per transport

| Transport | Client cancels request | Disconnect | Explicit `cancel_operation` |
| --- | --- | --- | --- |
| stdio | SDK cancels the handler task; worker signals and reaps the tree | session ends → task cancelled | supported |
| streamable HTTP (stateless, current default) | not observed by the server | **not observed**; command intentionally continues | **supported**, verified by a real HTTP client |
| durable jobs | `cancel_job` only | never cancels | not applicable (jobs keep their own semantics) |

A cancelled command reports `command_cancelled`, releases admission immediately (follow-up 1.03 s
including a `sleep 1`), and leaves no process behind. A disconnect during `sleep 2; echo ...` still
produced the side effect — durability is preserved and no uncertain command is replayed.

## 5. Concurrency model and safety constraints

- Exclusive commands and durable jobs: unchanged host-wide write claim → one at a time.
- Read-only commands (`access="read"`): concurrent with other readers and native readers; blocked by
  any writer to the same subtree and by any exclusive command.
- Native file/git writers: unchanged write path claims; no new bypass.
- Bounded by a per-process gate; no unbounded thread/process fan-out.
- Fairness stays with the existing ticket-ordered durable admission queue; readers never hold the
  exclusive claim while waiting.

## 6. Crash recovery guarantees and limitations

Guaranteed at startup (before work is admitted):

- A dead native owner with a recorded child that has **exited** (PID/creation-time mismatch and no
  live process group) → claim released as `stale_native_owner_command_exited`.
- A dead owner with a **live** orphan or live group → claim quarantined as
  `stale_native_owner_command_alive`; conflicting work fails closed. After the orphan exits, the next
  startup releases it automatically.
- A dead owner with **no dispatch marker** → quarantined as
  `stale_native_owner_dispatch_unknown` (small spawn/record window; uncertainty preserved).
- A pending (never admitted) claim → released as `stale_native_owner_never_dispatched`.
- A **live** owner identity → never touched.
- Counts are exposed in `workflow_health.native_resource_recovery`.

Limitations:

- Orphaned commands are not killed by recovery; they are fenced for explicit reconciliation. The
  server cannot know whether their side effects completed.
- Quarantines from other causes (capacity cleanup, workflow lease loss) still require the existing
  explicit reconciliation path.
- Process-group verification is POSIX-only; Windows relies on Job Object identity.

## 7. Benchmark comparisons (real HTTP server, 2 vCPU)

| Scenario | Before | After |
| --- | --- | --- |
| 4 × `sleep 1`, default (exclusive) | 4.28 s | 4.24 s (unchanged by design) |
| 4 × `sleep 1`, `access="read"` | n/a (serialized) | **1.13 s** |
| 60 mixed concurrent requests (shell read/exclusive, read_file, search, health) | n/a | **2.54 s, 0 errors** |
| 8 × cancel `sleep 61` + follow-up | claim held to timeout | **0.09–0.10 s each, 0 failures** |
| Post-cancel follow-up | 30.02 s admission timeout | **1.03 s (incl. `sleep 1`)** |
| SIGKILL recovery follow-up | 30.12 s timeout | **0.14 s** |

FD/RSS sampling via the sandboxed `/proc` was denied (`AccessDenied`), so this run reports no new
memory/FD numbers; the earlier audit measured 8→27 FDs (stable) and 122→127.6 MiB RSS.

## 8. Real MCP end-to-end results

- Explicit cancel over streamable HTTP: cancel response `{ok:true,cancelled:true}`, call returns
  `command_cancelled`, no owned process remains, next command admitted immediately.
- stdio: existing cancellation regression still passes (task cancellation reaps the group).
- Repeated cancellation: second request reports `already_requested`; after completion the id reports
  a terminal state, never a false cancellation.
- Duplicate operation id while running: second call fails `operation_id_in_use` without dispatch.
- Disconnect: side-effecting command completes; no replay.
- SIGKILL: claim transitions active → quarantined (orphan alive) → released (orphan exited).
- Durable job submitted before SIGKILL: after restart `status=succeeded`, single execution.

## 9. Stress results

- 60 concurrent mixed requests: 2.54 s, 0 errors.
- 8 sequential cancel/replace cycles: 0.09–0.10 s cancel latency, 0 failures, no leaked processes.
- Durable job survived server SIGKILL and reached `succeeded`.
- No `.agent_state` corruption; SQLite WAL handled concurrent claims and job submissions.

## 10. Final verification

| Command | Result |
| --- | --- |
| `python -m compileall -q core tools scripts tests` | pass |
| `python -m ruff check .` | pass |
| `python -m mypy core tools scripts tests` | pass (314 files) |
| `python main.py --check` | pass — 138 tools, unique |
| `python -m scripts.health_check --json` | pass |
| `python -m scripts.platform_validation --json` | pass (`ok: true`) |
| `python -m pytest -o addopts= -q --basetemp=.pytest-tmp` | 2 493 passed, 24 skipped; one racy recovery expectation updated afterwards and verified in isolation |
| New regression files | 14 passed |

## 11. Skipped and untested capabilities

- 24 platform-gated skips unchanged: Windows Job Objects, Windows batch/PowerShell bootstrap,
  junctions, delete-sharing, desktop screenshot domain, and the PowerShell-absent admission probe.
- Browser automation: Playwright and Chromium are not installed (`browser: false`); no browser or
  semantic-browser execution was verified here.
- LSP: no language server installed; persistent provider paths were not exercised.
- External MCP extensions: not configured for this run; the suite's deterministic local fakes ran,
  but no live third-party extension was connected.
- Secure Tunnel: no operator credentials; only offline/config paths are covered by the suite.
- Windows/macOS execution: static review only.

## 12. Remaining risks

1. Read-only concurrency depends on the caller's `access="read"` assertion. A lying caller can
   violate serialization; the classification is audited but not proven by the server.
2. Stateless HTTP still cannot observe disconnects; cancellation is explicit by design. A client that
   loses the operation id cannot cancel.
3. Recovery fences live orphans rather than killing them; they require operator action.
4. Operation ids are process-local: a restart loses in-flight identities (reported as not found).
5. No OS sandbox; filesystem/process access remains account-scoped by design.
6. Windows/macOS parity is inferred; the new Windows Job Object path for cancellation was not
   executed here.

## 13. Commits

Focused Conventional Commits on the working branch (see `git log`); no push, tag, or release.
