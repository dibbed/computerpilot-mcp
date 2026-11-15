# Parallel Durable Jobs: Implementation Plan and Contract

## Goal

Accept multiple independently cancellable durable jobs in one MCP call, execute up to the configured `max_running_jobs` **when their declared resource claims permit**, and return separate status, stdout, stderr, and resumable byte cursors for each job.

## Decisions

1. **Keep existing infrastructure.** Reuse `JobStore`, `JobScheduler`, per-job workers, SQLite admission, process ownership, cancellation, recovery, and the managed resource-claim authority. Do not add a second job engine or global dispatcher.
2. **Preserve exclusive behavior by default.** Existing `submit_job` callers keep the global exclusive coordination claim and subtree write claim for `cwd`.
3. **Explicitly opt into resource-aware parallelism.** `execution_mode="declared"` requires 1 to 32 declared filesystem claims per job. Each claim has `path`, `mode` (`read` or `write`), and `scope` (`exact` or `subtree`). Paths are resolved relative to the job's `cwd`; claims are persisted in the job spec and fingerprinted for idempotency.
4. **Prefer runnable jobs at admission.** Before worker launch, the scheduler compares candidate job claims with running/orphaned/launch-reserved job claims, skipping conflicting candidates and filling free slots with independent jobs. Selection uses bounded 128-row keyset pages, continuing past blocked prefixes until all available slots are filled or the queue is exhausted. Only one page is held in memory at a time; scan time can grow with a very large blocked queue.
5. **Keep cancellation independent.** An already-queued job can be cancelled immediately; workers waiting on an externally held claim observe the durable cancellation flag during admission. Already launched owned process trees retain existing cancellation, timeout, and no-blind-replay semantics.
6. **Use bounded model-facing batch tools.** `submit_jobs` submits up to 16 independent items; each item retains its own idempotency key and job ID. `jobs_snapshot` reads up to 16 statuses and per-job incremental stdout/stderr with independent cursors, bounded to 128 KiB of combined stream bytes per response. The returned `progress_bytes_per_stream` describes the effective cap. Existing `cancel_job`, `job_wait`, `job_output`, and `job_status` remain available.

## API examples

```json
{
  "jobs": [
    {
      "executable": "python",
      "args": ["-c", "from pathlib import Path; Path('a.txt').write_text('A')"],
      "cwd": "C:/work/demo",
      "idempotency_key": "demo-a",
      "execution_mode": "declared",
      "resources": [{"path": "a.txt", "mode": "write", "scope": "exact"}]
    },
    {
      "executable": "python",
      "args": ["-c", "from pathlib import Path; Path('b.txt').write_text('B')"],
      "cwd": "C:/work/demo",
      "idempotency_key": "demo-b",
      "execution_mode": "declared",
      "resources": [{"path": "b.txt", "mode": "write", "scope": "exact"}]
    }
  ]
}
```

Call `submit_jobs` with this object. The submitted items have independent timeouts, cancellation, errors, and captured streams. In the status responses, a queued job additionally reports advisory `queue_phase=waiting_resources` when it conflicts with an admitted or launch-reserved durable job; otherwise it reports `awaiting_worker`. The persisted status remains `queued` to preserve the recovery contract. Use the returned `job_id` values with:

```json
{
  "jobs": [
    {"job_id": "32-character-lowercase-hex-id", "stdout_since_byte": 0, "stderr_since_byte": 0}
  ],
  "progress_bytes": 4096
}
```

Call `jobs_snapshot` again with each returned `stdout.next_byte` and `stderr.next_byte`. A batch is an aggregation convenience, **not** one atomic transaction: some items may be accepted while others fail; retry using the original per-item idempotency keys. Cancelling one job does not cancel siblings.

## Safety and limitations

- Resource declarations are a trusted-caller contract, **not OS sandboxing**. Arbitrary commands can access files and services outside their claims; the caller must declare every resource they could read or modify. Unknown commands should keep the default exclusive mode.
- `max_running_jobs` is a capacity ceiling, not a promise that N conflicting jobs will run at once. Different claimed resources can overlap safely under the cooperative contract.
- Pending resource conflicts **among jobs** are considered before launch and surfaced via the advisory `queue_phase` field. They don't occupy running capacity. External resource holders can still block a launched worker, which remains in the existing `running` state until admission or cancellation. This intentionally avoids a risky migration of persisted job states/schema. `queue_phase` is recomputed for each snapshot and is not an additional durable state version.
- No automatic replay after an uncertain external effect. Existing idempotency, ownership verification, quarantine, and recovery behavior stay authoritative.
- Group snapshots are **poll-based**, not push notifications. The host must call `jobs_snapshot` again to see fresh chunks. Aggregate incremental output is limited to 128 KiB across up to 16 jobs per call, so use each returned stream cursor to resume.
- Broad tests that exercise ComputerPilot's own managed tools should be run through `run_pytest` (or an isolated test runner without an outer exclusive job claim). Running them inside a default exclusive `submit_job` can block their own nested resource claims and produce a spurious `resource_admission_timeout`.
- No changes to local state directories, secrets, release tags, published commits, or the running Supervisor are part of this implementation.

## Acceptance checks

- At least four independent declared jobs in the same working directory pass a real concurrent barrier.
- Jobs with overlapping write claims never execute concurrently.
- The scheduler bypasses a resource-blocked queued job to fill capacity with an independent candidate.
- Cancelling a worker waiting behind an external claim reaches `cancelled` promptly without executing the command.
- Batch submit returns stable distinct IDs, replay deduplicates, and batch snapshot keeps stdout/stderr/cursors separate, including real execution of four jobs from one MCP call.
- Scheduler finds an independent job even behind more than 64 conflicting queued jobs; no fixed head-of-queue window causes starvation.
- A snapshot of 16 jobs never returns more than 128 KiB of combined stdout/stderr progress, regardless of requested per-stream cap.
- Default-exclusive jobs still execute sequentially; cancelling one parallel job or one parallel command failing with a nonzero exit code leaves siblings independent.
- Completed jobs retain their individual terminal state and stdout/stderr cursors when a new JobStore instance reopens the existing database.
- Existing scheduler, job worker, admission, cancellation, MCP integration, Ruff, mypy, and relevant regression checks stay green.
