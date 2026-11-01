# Linux Deep Integration, Stress and Reliability Audit

Scope: real-runtime Linux (Ubuntu) compatibility audit of ComputerPilot MCP v0.9.0, beyond the
existing automated suite. Evidence below comes from the live MCP server (stdio and
streamable-HTTP transports), real subprocesses, real SQLite state, and disposable fixtures.

Status: **one confirmed runtime defect found and fixed** (cancelled native commands leaked their
POSIX process group and kept host-wide command ownership). Everything else exercised behaved as
designed, with the measured limitations listed in [Remaining risks](#11-remaining-risks-and-limitations).

## 1. Environment

| Item | Value |
| --- | --- |
| OS | Ubuntu 24.04.5 LTS (noble) |
| Kernel | Linux 6.1.102 x86_64 |
| CPU / RAM | 2 vCPU / 3.9 GiB |
| Python | 3.12.3 (`.venv`, CPython) |
| Git | 2.43.0 |
| Branch / HEAD | `freebuff/4dbed630eece01ec3581ac72` / `b88a2e17040036181ed99be3a0a0122c4dcb5e88` (post-`v0.9.0`) |
| Runtime deps | mcp 2.0.1, mcp-types 2.0.1, pydantic 2.14.2, psutil 7.2.2, charset-normalizer 3.4.x, Pillow 12.3.0, openpyxl 3.1.5, python-docx 1.2.0, pypdf 6.21.4, reportlab 4.5.1, anyio 4.16.0 |
| Quality deps | pytest 9.2.0, ruff 0.17.3, mypy 1.20.2 |
| Not installed | PowerShell (`pwsh`/`powershell`), Playwright and browsers, Windows UI Automation |

Detected capabilities (`core.platform.detect_capabilities()`):

```json
{"system":"linux","architecture":"amd64","process_tree_ownership":true,"desktop_screenshot":false,
 "desktop_input":false,"semantic_ui":false,"system_services":true,"installed_software":true,
 "powershell":false,"posix_shell":true,"secure_tunnel":true,"browser":false}
```

## 2. Method and evidence tiers

| Tier | What it proves | Used for |
| --- | --- | --- |
| A. Mocked/unit tests | Internal contracts with stubbed providers | Existing suite (2 480 tests) |
| B. In-process MCP client (`Client(create_server())`) | Schema validation, envelopes, executor behavior | New regression tests |
| C. Real MCP over stdio and streamable-HTTP to `main.py` | Actual transport, dispatch, cancellation, restart, live state | Audit probes below |

The server was started as documented (`python main.py --transport streamable-http`) and driven by
the official `mcp` client; a second server was spawned over stdio for cancellation and crash tests.
All fixtures were disposable (`/tmp/cpm-audit*`); no project source or user data was mutated.

## 3. Real MCP execution results

137 tools registered, unique names, structured envelopes, `isError` set for unknown tools.

| Probe | Result | Evidence |
| --- | --- | --- |
| Catalog + uniqueness | PASS | 137 tools |
| Unknown tool / missing arg / bad enum / timeout bounds | PASS | tool error envelope, no dispatch |
| Create/read/replace/move/copy with spaces + Unicode path | PASS | exact byte round-trip |
| `apply_patch` unified diff | PASS | 1 file, 1 hunk, backup written |
| Malformed patch / missing `old` token | PASS (rejected) | `invalid_patch`, `match_count_mismatch`, file unchanged |
| Atomic write debris | PASS | no `.tmp`/hidden residue after repeated writes |
| Permission denied read | PASS | `permission_denied` |
| Exit code, stderr, pipeline | PASS | exit 7 + stderr captured, `posix_process_group` |
| Timeout reaps descendants | PASS | `sleep` children gone, no leftovers |
| Argument injection | PASS | `; touch x` not shell-interpreted |
| Large output (2.7 MB) | PASS | `total_bytes` 2 688 895, inline capped at 4 000 |
| SIGINT exit reporting | PASS | exit `-2`, not reported as timeout |
| Background process capture | PASS | PID + `line1` output |
| Jobs: submit / dedupe / wait / output / cancel / list | PASS | `idempotency_conflict` for a reused key with a different command |
| Workflow plan + `implement_and_verify` | PASS | workflow created, versioned |
| Memory update/read | PASS | revision 1 round-trip |
| XLSX inspect/write/read, DOCX create/inspect, PDF create/extract | PASS | values and text round-trip |
| Git status/diff/commit (`empty_git_index` guard) | PASS | untracked/staged semantics correct |
| Project summary, affected tests | PASS | `pkg/test_mod.py` selected via import evidence |
| Execution router / domain discovery | PASS | advisory recommendation, unknown intents rejected |
| Health snapshot | PASS | `health_status: healthy`, 137 tools |

## 4. Confirmed defect, root cause, fix

### 4.1 Cancelling an in-flight command leaked the owned POSIX process group

**Reproduction (real stdio MCP server):**

1. Client calls `run_shell` with `sleep 331; echo done` and cancels the request after 2 s.
2. Client keeps the session alive and inspects the host.

**Observed before the fix:**

```
stdio-cancel leftovers after 5s: ['19794 /usr/bin/sh -c sleep 331; echo done', '19797 sleep 331']
STDIO_CANCEL_LEAK
```

The abandoned command also held `coordination_claim(exclusive=True)` on the host scope for the
whole command timeout. A later, unrelated `sleep 4` call took **30.02 s** (its admission budget)
instead of ~4 s, and a fresh `run_shell` blocked behind it. Default timeouts (60 s, up to 3600 s)
turn one cancelled request into a host-wide command stall.

**Root cause.** `run_bounded` is synchronous and waits inside `process.wait(timeout)`; the MCP SDK
dispatches sync handlers through `anyio.to_thread.run_sync` (default `abandon_on_cancel=False`), so
a cancelled request cannot reach the worker. Nothing signalled the owned process, and the claim was
released only when the command finally exited or timed out.

**Fix (smallest complete change):**

- `core/executor.py`: `run_bounded(..., cancel_event=threading.Event)` waits in ≤100 ms slices and
  terminates the owned tree (`terminate_tree(force=True)`), then raises `command_cancelled`.
- `core/resource_claims.py`: `managed_resource_claims(..., cancel_event=...)` aborts admission with
  `resource_admission_cancelled` instead of waiting for the claim.
- `tools/terminal/registry.py`: `run_process`, `run_powershell`, `run_shell`, `run_cmd` are now
  async handlers that run the blocking command through
  `anyio.to_thread.run_sync(..., abandon_on_cancel=True)` and set the cancel event when the owning
  task is cancelled.
- `anyio` is now a declared dependency (`pyproject.toml`, `requirements-runtime.txt`) because it is
  imported directly.

**Verification after the fix:**

```
stdio-cancel leftovers after 5s: none
STDIO_CANCEL_CLEAN
```

Regression coverage: `tests/test_terminal_cancellation.py`

- `test_run_bounded_cancel_event_reaps_owned_tree` — a set cancel event reaps the tree and raises
  `command_cancelled`.
- `test_cancelled_mcp_call_reaps_owned_process_group` (POSIX) — a cancelled MCP `run_shell` call
  leaves no owned process behind, and the process must have started first.

## 5. Linux compatibility findings

Verified working on POSIX:

- Process ownership through `start_new_session` + `killpg`, `posix_process_group` reported by every
  command result; nested descendants are reaped on timeout and on cancellation.
- Signal reporting: `SIGINT` surfaces as exit `-2`, not as a timeout.
- `fcntl.flock` inter-process locking, SQLite WAL + `busy_timeout` under 40 concurrent submissions.
- Unicode (`straße ☃`), spaces, and nested directories round-trip through create/read/write/patch.
- Executable bits preserved on copy (`0o750`), atomic replace leaves no debris, permission errors
  are reported as `permission_denied`.
- Capability gating is honest: `desktop_*`, `semantic_ui`, `browser`, `powershell` report false and
  those tools are not registered or fail closed (`powershell_not_found`); POSIX shell, systemd and
  dpkg detection report true.

By design, not defects (confirmed against docs/source):

- **Broad filesystem access.** `resolve_path` deliberately does not confine paths; a symlink to
  `/etc/passwd` is readable and writable. `SECURITY.md` states endpoint access equals OS-account
  access. No sandbox is claimed.
- **Exclusive command coordination.** Every mutating native command and every durable job takes
  `coordination_claim(exclusive=True)` on host scope, so commands serialize by design
  (`docs/tooling.md`); `max_running_jobs` is admission capacity, not a concurrency guarantee.
- **`job_wait` long-poll semantics.** It returns as soon as state differs from `after_version`;
  callers loop. An idempotency key reused with a different command is rejected, not deduplicated.
- **`git_commit` requires a staged index** (`empty_git_index`).

## 6. Stress metrics and resource usage

| Measurement | Result |
| --- | --- |
| MCP startup + dependency/doctor phases (platform validation) | 6.36 s launcher validation, 5.43 s Full Doctor, 19.21 s benchmark, 10.58 s workflow restart soak |
| 6 concurrent `read_file` | 0.15 s (parallel) |
| 8 concurrent `run_shell` (`sleep 0.4`) | 3.63 s — serialized by the exclusive host claim |
| 4 concurrent `run_shell` (`sleep 1.0`) | 4.28 s — same serialization |
| 3 durable jobs (`sleep 1.5`) | 6.0 s — jobs serialize on the same claim |
| 12 independent file writes in parallel | 0.24 s |
| Soak: 150 command + 150 read pairs | 12.7 s total |
| Server file descriptors | 8 → 27 after first 200 calls, **stable at 27** through 400+ further calls (pool warm-up, no growth) |
| Server RSS | 122.0 → 127.6 MiB after 300 calls (+5.6 MiB, then flat) |
| 40 concurrent job submissions | 40 accepted, 5/5 sampled reached terminal state; no SQLite "database is locked" |
| Concurrent same-file `replace_exact` (6 racers) | consistent single-version result; losers fail with `match_count_mismatch` |
| Cancel → recovery (after fix) | no leftover process, next command admitted immediately |
| `kill -9` of MCP server during a durable job | worker survived, job reached `succeeded`, executed exactly once (`begin` once) after reconnect |

## 7. Skipped tests and reasons

Final suite: **2 480 passed, 24 skipped**. All skips are platform-gated:

| Count | Reason |
| --- | --- |
| 10 | `tests/test_windows_job_backend.py` — Windows Job Object integration requires Windows |
| 4 | `tests/test_process_ownership.py` — Windows Job Object tests require Windows |
| 2 | `tests/test_launcher.py` — Windows batch launcher |
| 2 | `tests/test_launcher.py` — PowerShell bootstrap |
| 2 | `tests/test_resource_health.py` — Windows junction semantics only |
| 1 | `tests/test_core_services.py` — Windows transient replace retry policy |
| 1 | `tests/test_media.py` — native desktop screenshot domain is Windows-only |
| 1 | `tests/test_native_batch.py` — Windows delete-sharing contract |
| 1 | `tests/test_native_batch_activation_script.py` — PowerShell activation is Windows-only |
| 1 | `tests/test_terminal_platform.py` — PowerShell is not installed on this host |

The last one is the portability correction from the previous session: `run_powershell` resolves
`pwsh` lazily, so the resource-admission test cannot run where no PowerShell exists (hosted
`ubuntu-latest` images ship `pwsh`, which is why CI never hit it).

## 8. Untested or unsupported in this environment

- **Browser automation** — Playwright is not installed (`browser: false`); no browser probes were
  run. This is a missing optional dependency, not a Linux defect.
- **Windows desktop / UI Automation** — Windows-only by design.
- **PowerShell** — not installed; not installed deliberately, since it is not required by any
  supported Linux capability.
- **LSP code intelligence** — no language server is installed; provider-backed probes were not run.
- **External MCP extensions** — no extension configured; zero-extension behavior was used.
- **Secure Tunnel mode** — requires operator credentials; not exercised.
- **macOS and Windows execution** — only verifiable on those hosts.

## 9. Final verification (this checkout)

| Command | Result |
| --- | --- |
| `python -m compileall -q core tools scripts tests` | pass |
| `python -m ruff check .` | pass |
| `python -m mypy core tools scripts tests` | pass |
| `python main.py --check` | pass — 137 tools, unique names |
| `python -m scripts.health_check --json` | pass — import/startup/registration/filesystem/terminal |
| `python -m scripts.platform_validation --json` | pass — `linux-amd64`, `ok: true` |
| `python -m pytest -o addopts= -q --basetemp=.pytest-tmp` | **2 480 passed, 24 skipped** (486.89 s) |
| `tests/test_terminal_cancellation.py` | 2 passed |
| Real stdio cancellation probe | clean (no leftovers) |
| Real server-crash + restart probe | job `succeeded`, single execution |

## 10. Files modified

- `core/executor.py` — cancellable bounded execution.
- `core/resource_claims.py` — cancellation-aware admission.
- `tools/terminal/registry.py` — async, cancellation-safe terminal tools.
- `tests/test_terminal_cancellation.py` — new regression tests.
- `tests/test_terminal_platform.py` — PowerShell-absent skip (test portability only).
- `pyproject.toml`, `requirements-runtime.txt` — declare `anyio`.
- `docs/LINUX-DEEP-INTEGRATION-AUDIT.md` — this report.

Commits: see `git log` on the audit branch (one test-portability commit, one runtime-fix commit,
one audit-report commit).

## 11. Remaining risks and limitations

1. **Serialized native execution.** All mutating commands and durable jobs share one exclusive
   host claim; parallel throughput is bounded to one command at a time. This is deliberate safety
   policy, but it means `max_running_jobs` does not deliver real concurrency for commands.
2. **Stateless HTTP drops cancellation.** With `stateless_http=True` the server cannot observe a
   client cancel; the fix is effective on stdio (tunnel/stdio clients). HTTP clients still rely on
   the command timeout.
3. **Abrupt death can leave fenced claims.** A SIGKILL while a command holds the coordination claim
   leaves an `active` request in `workflows.sqlite3`; startup recovery quarantines it when the owner
   is gone, and conflicting work then fails closed with `resource_quarantined` until reconciled.
   This preserves evidence, but an operator must reconcile.
4. **No OS sandbox.** Filesystem and process access equal the server account's access; symlinks and
   absolute paths are followed by design.
5. **Windows/macOS parity** is inferred from capability-gated code paths and the existing suite, not
   executed here. Windows Job Object behavior (10+ skipped tests) remains unverified in this audit.
6. Browser, LSP, extension, and tunnel paths were not exercised for the reasons in section 8.
