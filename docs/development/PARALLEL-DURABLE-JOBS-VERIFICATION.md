# Parallel Durable Jobs: Verification and Handoff

## Implementation

Branch: `feat/resource-aware-parallel-jobs`

Commits:
- `a5e3b58` `feat(jobs): schedule durable workers by declared resource claims`
- `b104dd8` `feat(jobs): expose batch submission and independent stream snapshots`
- `b066a5a` `test(jobs): align tool catalog contracts with batch APIs`
- `6fe1222` `fix(jobs): include batch submissions in lifecycle mutation guards`

The design and API examples are documented in `PARALLEL-DURABLE-JOBS-PLAN.md`.

## Acceptance and integration evidence

Tests were run on the original working tree, an isolated committed-HEAD archive for focused checks, and finally on a detached Git worktree of commit `6fe1222` with real Git metadata. This separates feature correctness from unrelated pre-existing uncommitted work and preserves tests requiring Git history.

On **committed HEAD**, the focused Job Engine, scheduling, MCP profile and schema suites:
- **184 passed, 1 skipped, 0 failed**
- Additional adjacent recovery, Windows ownership, resource coordination and MCP integration suites: **196 passed, 4 skipped, 0 failed**
- Total across these **two non-overlapping test-file selections: 380 passed, 5 skipped, 0 failed**
- Ruff on changed source and catalog tests: **passed**
- mypy on `core/jobs.py`, `scripts/job_worker.py`, `tools/jobs/registry.py`, `core/tooling.py`, and `scripts/workflow_soak.py`: **passed**
- `git diff HEAD~4..HEAD --check`: **passed** (no whitespace errors)
- Both previously failing full-suite checks for mutation guard parity and MCP restart tool count passed in focused reruns after commit `6fe1222`

Verified behaviors include:
- Four declared, disjoint jobs simultaneously complete a shared start barrier
- Overlapping write claims do not execute concurrently
- Exclusive legacy commands remain serialized
- Queued blocked jobs do not monopolize running slots
- Pagination finds independent work after a long conflicting queue prefix
- Cancellation works for a job awaiting an external resource claim and does not cancel siblings
- Nonzero exit status of one job does not stop other jobs
- Per-job stdout/stderr and cursors remain separate, including across reopening the store
- Batch request replay deduplicates and altered declarations produce idempotency conflicts
- Batched incremental output has a 128 KiB aggregate byte limit
- Queued resource conflicts appear as advisory `queue_phase=waiting_resources`

## Full-suite validation

**PASS: 2,578 passed, 35 skipped, 0 failed, 0 errors (2,613 collected test outcomes).**

- Revision tested: detached Git worktree at `6fe1222` (full repository history available)
- Command: `<source-repo>/.venv/Scripts/python.exe -m pytest -q --disable-warnings --maxfail=12 tests --basetemp=<isolated-worktree>/verified_full_suite_final`, with the process working directory set to the detached committed worktree
- Execution: managed Windows process with independently retrievable output and verified terminal **exit code 0**
- Counts: 2,578 `.` and 35 `s` progress markers, 2,613 reported test outcomes. With quiet pytest configuration the usual end-of-run count line was suppressed; counts were derived from the complete progress stream.
- First clean full-suite run on commit `b066a5a` reached 100% and exposed two failures: `test_mutation_guard_set_matches_mcp_annotations` and `test_workflow_soak_smoke_covers_restarts_reconciliation_retention_and_leaks`. Both were addressed by commit `6fe1222` and confirmed in the full rerun.

## Known intentional limits

1. Only explicitly declared filesystem claims opt into parallel execution. Arbitrary undeclared commands remain exclusive. Claims are cooperative metadata, not a sandbox.
2. The durable status vocabulary remains unchanged. `waiting_resources` is an advisory `queue_phase` for queued jobs blocked by other durable jobs. A worker waiting for an external resource owner may still count as running until it acquires the claim or is cancelled.
3. Batched progress is polling-based; it does not provide push streaming.
4. A batch is **not** one atomic transaction. Retrying individual items with their original idempotency keys is safe.
5. The live MCP process needs to restart or reload to register newly added tools. No deployment, push, release tag or service restart was performed by these code changes.
6. The source repository already contained unrelated uncommitted work. Only feature-specific files/hunks were committed; unrelated work remains unchanged and unstaged.

## Execution note

Foreground `run_pytest` on the *entire* test tree sometimes returned prematurely while pytest child processes remained alive and Windows fixture cleanup hit `WinError 32`. The specific test processes started during verification were identified and terminated without touching unrelated server processes. A `git archive` test copy lacked `.git`, so it was not used as definitive whole-project evidence. The final passing full suite instead ran under a **managed background Windows process** in a detached **Git worktree** with isolated temporary test directories and a verified exit code of 0. This result supersedes the earlier inconclusive full-suite attempts.
