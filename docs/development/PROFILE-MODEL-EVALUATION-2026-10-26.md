# P05-02: Controlled model-profile evaluation, status and gates

Date: 2026-10-26. Branch: feature/coding-agent-evolution.
The initial evaluation source subject was ce311e2e7b9fe5bbdd039d3bb9f511ebc70524a4.
PR #82 is still Draft. This is **not** a claim that P05-02 is VERIFIED.

## Scope built and checked

- scripts/profile_eval.py creates a fixed 20-task x 3-repetition x 3-profile (180
  invocation) schedule for full, coding and coding_core. Each profile is first once
  per task across three repetitions (balanced crossover). Plans can be generated
  offline without model calls, and the result analyzer deliberately fails closed
  on missing samples, ambiguous tools, response-review gaps and measured regression.
- The workspace preparer uses git archive HEAD, and creates three isolated
  snapshots without copying untracked/operator modifications, live .agent_state,
  local credentials, or activated extensions. It refuses existing output paths.
  The output includes profile-specific minimal MCP configs and an identical small
  Python fixture. Initial new snapshots included 390 tracked files each from
  exact ce311e2. Config paths are canonical absolute paths; an initial relative
  path issue was caught by the safe preflight and fixed with a regression test.
- scripts/profile_eval_live.py is explicitly opt-in with --execute and requires
  limits on number of model calls, observable input tokens, and elapsed time.
  It grants only seven explicitly enumerated read-only MCP tools, and restores
  AGY settings after the invocation without wildcard permissions. If AGY settings
  change concurrently, it attempts removal of only its additions; do not confuse
  this with isolation from all potential concurrent CLI users.
- The native isolated runtime .agent_state/timings.jsonl is the authoritative
  source of actual MCP tool identity: phase=request_pipeline and
  method=tools/call within this single trial's newly appended records.
  AGY's built-in read_file and generic call_mcp_tool are NOT interpreted
  as proof that a specific ComputerPilot tool was dispatched.
- Each live result leaves response_is_correct unset pending independent human
  evaluation. The analyzer can return PILOT_ACCEPTABLE_NOT_PRODUCTION_GO, never
  an unconditional release or model-superiority decision.

## Actual evidence from local probes

| Trial | Observation |
| --- | --- |
| Older 3-profile AGY micro-pilot | All called project_summary, but the source clones were stale versus current HEAD, and there was no repeated model evaluation |
| New HEAD snapshots | All three shared identical key product source hashes; the full profile passed native main.py --check with 151 registered tools |
| First new full-profile call | TIMEOUT after 70.047 s, without trustworthy token usage. Batch stopped immediately; temporary grants removed |
| Separate no-tool AGY diagnostic | SUCCESS in 13.78 s, 29,620 reported input tokens. Not a valid MCP profile evaluation |
| New coding_core probe 1 | SUCCESS in 35.047 s, 35,341 reported input tokens. Runtime timing audit confirmed project_summary actually ran |
| New coding_core probe 2 | SUCCESS in 31.172 s, 35,341 reported input tokens. New runner recorded observed_tool_names=[project_summary] from the native MCP server-side request_pipeline trace. The AGY client trace alone was ambiguous |
| Permission safety | No temporary read-only AGY grants remained after these probes; original operator runtime .agent_state and unrelated user edits were preserved |

These are deliberately tiny investigations, not the required 180 actual model
invocations, no controlled p95 comparison, no meaningful wrong-tool-rate
assessment, and no independent review of final answer correctness. No material
speed or token savings are claimed. The --max-total-input-tokens flag is an
observational stop threshold, NOT a provider-enforced billing limit; one call
might exceed it before usage is reported. Cache tokens and uncached input tokens
are distinct, and must not be silently combined.

## Local commands

Use the validated Windows .venv in the repository root, and use a fresh ignored
directory every time. The results and snapshots are not committed.

    .\.venv\Scripts\python.exe -m pytest -o addopts= -q tests/test_profile_eval.py tests/test_profile_eval_live.py
    .\.venv\Scripts\python.exe -m ruff check scripts/profile_eval.py scripts/profile_eval_live.py tests/test_profile_eval.py tests/test_profile_eval_live.py
    .\.venv\Scripts\python.exe -m mypy scripts/profile_eval.py scripts/profile_eval_live.py

    # No model usage:
    .\.venv\Scripts\python.exe -m scripts.profile_eval --prepare-source . --prepare-output tests/.real_lsp_tools/profile-eval-NEW
    .\.venv\Scripts\python.exe -m scripts.profile_eval --fixtures tests/.real_lsp_tools/profile-eval-NEW --output tests/.real_lsp_tools/profile-eval-NEW/schedule.json

    # Paid/external service possible; only with separately approved quota:
    .\.venv\Scripts\python.exe -m scripts.profile_eval_live --base tests/.real_lsp_tools/profile-eval-NEW --execute --max-calls 1 --max-total-input-tokens 55000 --max-wall-seconds 95

    # Incomplete or unreviewed results exit nonzero:
    .\.venv\Scripts\python.exe -m scripts.profile_eval --results tests/.real_lsp_tools/profile-eval-NEW/live-results.jsonl

## Remaining acceptance requirements (BLOCKED)

1. Separate explicit budget and controlled client/model/version selection for the
   full 20 x 3 x 3 run. Use task-level controls, balanced order, cache accounting,
   legitimate roundtrip metrics, and record any failures. Full coding journey
   acceptance additionally needs read/context/patch/verify/Git on a disposable
   sandbox; the initial 20 tasks test read-only tool selection, not mutation.
2. Independent, repeatable correctness grading of model responses; trusted
   actual server tool identity and per-task wrong-tool rate; p50/p95 latency
   with natural variance; uncached and cached token counts reported separately.
3. coding_core must not have lower completion success or higher wrong-tool rate
   than coding, nor exceed its p95 by more than 5 percent.
4. P05-03 remains design blocked until real client ToolsListChanged/refresh/stale
   call evidence exists. P10/P13 remain blocked on grounding/delegation safety
   design, and optional P11/P12/P14/P15 extensions must not be activated merely
   to make a progress dashboard show 100 percent.

**Disposition**: new reproducible offline and opt-in live evaluation tooling
is locally verified. P05-02 is STILL BLOCKED on a controlled, independently
graded real-model comparison. No main merge, tag, release, current runtime
restart or tool-profile activation occurred.
