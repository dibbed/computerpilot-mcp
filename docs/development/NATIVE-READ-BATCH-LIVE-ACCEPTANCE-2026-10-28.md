# Native Read Batch: real plugin/Tunnel acceptance (2026-10-28)

The installed Windows service passed the real GPT plugin/Tunnel comparison on
production source `46cb97ae1e1974c812afb474951145a755e0347a`. The refreshed plugin
catalog exposed all 152 tools, including `native_read_batch`, and an actual
four-file Batch call returned the expected ordered, complete content.

The operator installation persists `MCP_TOOL_PROFILE=full` and
`MCP_NATIVE_READ_BATCH_ENABLED=1` in its existing runtime settings, so subsequent
normal starts retain Batch alongside the existing tools. The package default
remains opt-in; this record does not change default schemas for other installs.

## Method

- Six cases: 4/16 files, direct sequential, direct client parallelism 4, and one
  native Batch with parallelism 4. Each case had 5 warmups and 30 measured samples.
- A seeded order (1501) shuffled the six cases in every round. The 16 generated
  UTF-8 fixtures contained no personal data. All modes read the same windows
  (`start_line=1`, `offset=0`, `max_chars=4096`, UTF-8).
- The client adapter invoked the connected plugin tools directly. It measured
  wall-clock milliseconds from sending requests to receiving full tool results.
  The orchestration loop did not wait for model inference between calls. No
  simulated delay, shell replacement, local function dispatch, or custom Tunnel
  endpoint was used.
- Content equality, order, completeness and returned Batch SHA-256 values were
  checked. Request/response counts and per-call intervals were retained.
- Percentiles use nearest-rank. With 30 samples, p99 is exploratory only.

## Results

Times below are seconds, with 30 measured samples per row and zero errors.

| Files | Mode | p50 | p95 |
|---:|---|---:|---:|
| 4 | Direct sequential | 4.309 | 9.117 |
| 4 | Direct parallel 4 | 1.294 | 4.587 |
| 4 | Native Batch 4 | 0.917 | 1.680 |
| 16 | Direct sequential | 17.062 | 22.440 |
| 16 | Direct parallel 4 | 5.662 | 9.895 |
| 16 | Native Batch 4 | 1.083 | 1.646 |

Batch reduced N16 p95 by **83.37%** against the fastest direct mode, passing the
required 10% improvement. N4 p95 improved **63.37%** in this run, supporting Batch
for both measured group sizes on this connection. Continue using `read_file` for
one file; these measurements do not guarantee a speedup on every host or load.

There were **1,470 requests and 1,470 responses**, including warmups (1,260 of each
in measured rounds), across 210 completed cases. Every output was equivalent and
complete. Client parallel overlap reached four calls; internal host/server
dispatch parallelism was not independently instrumented.

## Limits and preserved state

The earlier quiet local paired single-file p95 overhead was 4.5592 ms in memory,
4.6566 ms over stdio, and 4.6430 ms over loopback HTTP, below the unchanged 5 ms
limit. The retained contended stdio result of 7.7113 ms remains a limitation;
neither the local gate nor the real-path comparison establishes a load guarantee.

The live runtime generation and all resource budgets were identical before and
after the matrix. The final service was healthy with 152 tools, no active Batch,
read, job or workflow lease, and no pending/active/quarantined resource request.
The installed Tunnel 0.0.18 binary and performance settings were preserved.
The live claim connection remains owned by the running service; no shutdown or
database deletion was performed for this benchmark.

Byte totals describe application JSON arguments and returned tool envelopes,
not raw SDK frames or HTTP/TCP/TLS overhead. Model tokens and inference latency
were not measured. Server CPU/peak RSS were not sampled inside this real-path
timing run; the separate local harness reports those scopes.

The raw transcript and summary are retained outside the checkout. Raw JSON
SHA-256: `9f2f101ee54859cacfa8c8a761ec4471c17a8d88e624a1a7ed9a59ba053c0ac8`.
This is feature acceptance evidence, not a new release or tag.
