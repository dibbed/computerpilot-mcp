# Free Persistent LSP Efficiency: Local Completion (2026-10-26)

## Status and scope

**WINDOWS LIVE THREE-PROVIDER ACTIVATION VERIFIED. Hosted same-SHA CI and live-model profile acceptance remain separate gates.**

This report finishes the interrupted free-provider/session-reuse and safe read-request-cache task against the original checkout. It did not change the then-current master index, waive the blocked live-model pilot, or claim hosted/release readiness. Codebase Memory/Graphify references in prior work are supporting background; the current filesystem and black-box tests are the source of truth.

## Free language providers

The operator-reviewed candidate remains at `tests/.real_lsp_tools/free-defaults/lsp-providers.review.json` (ignored, repo-local). Candidate SHA-256: `09c952ddde6e73eb0faa6f34847060423c2c28fa1f0d5225d61d69e79cd8c78e`. Before activation the live file had SHA-256 `275a2f14981e2f02a192450e416315191cf1cf481bd39e93c60ec0ccf1cedcd2`; that exact content is backed up in ignored local evidence. At the time of the live check, the live file had candidate SHA-256 `09c952ddde6e73eb0faa6f34847060423c2c28fa1f0d5225d61d69e79cd8c78e` after controlled restart.

The candidate preserves the exact Pyright entry and adds SHA-256-pinned **TypeScript Language Server** and free **Intelephense for PHP**, all under the existing trusted checkout root. BasedPyright 1.40.2 passed the strict packaged fixture but required different behavior for implicit relative imports and showed no demonstrated speed benefit in a very small non-benchmark observation. **Retain Pyright** as the default. No premium/licensed features were activated.

Reran the actual isolated candidate validator `tests/.real_lsp_tools/free-defaults/validate_candidate.py` with this source: **151 tools**, **3 live provider processes**, **18 persistent session reuses**, Python/TypeScript/PHP definitions and references (**3/3/2**) and clean → real diagnostic → repaired diagnostic all PASS; final state **0 active, 0 quarantined**, provider fixture directory removed, exit **0**. The validator intentionally uses evidence-bearing requests, so `context_cache_hits=0` is expected and it is *not* a read-cache hit benchmark.

## Safe read-result reuse implementation

The existing `LspSessionManager` continues to own sessions and the bounded context cache. `arequest` permits read-result caching for **definition, references, hover, document symbols and workspace symbols only**. It does not cache diagnostics, edit/rename/mutation operations or calls requiring immutable per-dispatch evidence (`arequest_with_evidence`). A successfully admitted result is fenced by the provider/session identity, owner incarnation, synchronized document versions and hashes, full local input fingerprint, Python metadata invalidation epoch, and bounded context cache lifecycle/TTL. Missing/partial/failing fingerprints never authorize a cache hit.

`tools/project/project_types.py` adds a bounded input-aware inventory. It requires explicit self-contained local language config; the 0.25s/16MiB/entry limits, inaccessible sources, excluded secrets, external dependency references, symlinks, and other missing evidence fail closed. This means **large/unconfigured projects may get no result-cache hits**. Such requests still benefit from a persistent provider process once enabled; no model-token reduction, zero request count, or global speedup is claimed.

### Regression discovered and corrected

The inherited draft cache wrapper added extra preparation even for workspaces without eligible config; 10 previously passing lifecycle, cancellation, timeout and queue tests failed in the first broad check. The correction bypasses the wrapper in that case and invokes the original `arequest_with_evidence` path directly. Those **10 failures were rerun and all 10 passed**. Another draft branch silently retried a read after a missing mid-flight fingerprint, potentially replaying a provider request. Removed the retry: an uncertain request fails closed, never hidden replay.

Added `tests/test_lsp_request_cache.py` (6 PASS): actual fixture wire dispatch counts; 2 sequential identical reads → **1 dispatch**; 5 concurrent equivalent reads → **1 dispatch**; edited document forces fresh response; missing/out-of-scope config forces fresh dispatch; deleted document returns an error rather than a cached success; losing fingerprint after first dispatch never replays a second time. Context-cache hits/shared-waiter counters use the existing bounded `core/health_snapshot.py` projection.

## Verification performed on the final source

- Isolated candidate: **PASS**, three free real providers, 18 session reuses, drain and cleanup success.
- New request cache tests: **6 passed**.
- Focused existing LSP / project / health / policy plus new tests: **220 passed, 1 skipped**. The skip was already present. Initial 10-failure diagnostic run is not misreported as green; its cause and verification are above.
- Full Ruff: **PASS**. Full mypy: **PASS on 298 source files**.
- Full disposable-source test suite: **2345 passed, 28 skipped, 0 failed, 481.34s**, exit 0; generated repo-local ignored snapshot and automatically cleaned up. **386 source files** copied, manifest SHA-256 `ab3be3bfdccce017d6e6ec9542580d5e5e4c52b5af8a74a6b0a7318676cb5272`. No stdout warning was reported in the final summary. Test environment excluded live `.agent_state`, operational memory and `uv.lock`.
- Original isolated full-suite source used `02796e9e7af9401bbe9fc5eda8ce2dde4980dad8` plus a local diff; subsequent 7 scoped commits were pushed as `5f26cc96e70b9e41ba2c4380239be34be9596fc8` to PR #82. The 2345-pass historical full suite does not prove the later benchmark fix; focused isolated rerun: 84 passed, 6 skipped.
- **Performed in the subsequent rollout:** supervised live restart and three-provider activation (runtime pid13292→25320, generation1→2), 7 focused commits pushed, hosted CI/Platform/Packaging triggered. **Not performed:** merge, release, tag or DAG activation. The real-model UX gate remains blocked on model usage limit.

## Live activation: initial gate and subsequent verified rollout

The **pre-rollout** MCP process predated LSP source updates and initially had Pyright only. A restart of the **same MCP that supplies the computer connector** could have interrupted verification. That risk was addressed with a separate one-shot Windows Task Scheduler controller, guarded config/backup SHA validation, supervisor `/api/restart` and independent two-observation health/rollback logic. The controller's sanitized JSON evidence records `ACTIVATED_HEALTH_VERIFIED`, 151 tools, new pid25320 and generation2. From the reconnected MCP tools, actual Python/TypeScript/PHP definition, references3/3/4, and clean→error→repaired diagnostics (reportArgumentType, TS2345, P1006) all passed. PHP cold-start required `document_symbols` indexing the source before the first valid cross-file result. Owned fixtures were restored/removed and sessions drained to zero without quarantine.

Before any operator-approved publication/restart, recheck 0 active calls/mutations, 0 queued/running jobs/workflows, 0 pending/uncertain recovery; verify candidate and live SHA-256; preserve the prior config for rollback; review the dependency supply chain beyond executable/entrypoint pins; use the existing supervised restart; then independently verify current process generation, 151 tools, actual provider registration, test definition/references/diagnostics and cleanup. Do not display or persist the supervisor control token.

**Local implementation and Windows live activation are verified; Linux/macOS real-provider runs, model-powered `coding_core` acceptance, and any hosted merge/release decision remain independent gates.**
