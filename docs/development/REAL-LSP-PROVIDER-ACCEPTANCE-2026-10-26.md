# Real LSP provider acceptance: Windows (2026-10-26)

Status: **Python/Pyright, TypeScript and PHP/Intelephense: VERIFIED_LOCAL**. All three standard real-provider probes exited **0** against the frozen local code. This records the six requested coding-agent capabilities; hosted CI, other operating systems, production activation and paid provider features remain separate gates.

## Executed acceptance

| Language / provider | Tested versions | Definition / references | Actual diagnostics in each generation | Edit / restart / cleanup |
|---|---|---|---|---|
| Python / Pyright | Pyright 1.1.403; Node 22.18.0 | Exact source-file symbol range; declaration and call included; 3 references | Clean 0 → invalid argument: 1 severity-1 `reportArgumentType` → repaired 0 | PASS; two complete generations; 4 observed process identities, no orphan; fixture removed |
| TypeScript | typescript-language-server 5.3.0; TypeScript 5.9.3; Node 22.18.0 | Exact source-file symbol range; declaration and call included; 3 references | Clean 0 → invalid argument: 1 severity-1 `TS2345` → repaired 0 | PASS; two complete generations; 6 observed process identities, no orphan; fixture removed |
| PHP / Intelephense | Intelephense 1.18.5; Node 22.18.0 | Exact source-file symbol range; declaration and call included; 2 references | Clean 0 → invalid argument: 1 severity-1 `P1006` → repaired 0 | PASS; two complete generations; 4 observed process identities, no orphan; fixture removed |

The runner checks real source and caller files, canonical URI identity, valid symbol ranges, references at the actual call site, changed on-disk SHA/document version, semantic error content at the changed line, repair, full drain, a new owner generation, and repetition of the whole cycle. PHP fixtures declare `strict_types=1`: weak PHP scalar coercion is not a valid fixture for requiring this particular type error. The actual PHP diagnostic was `Expected type 'int'. Found ''not-an-int''.`

Observed process counts include roots and children, keyed by PID and process creation time; they are not counts of separate server sessions. Missing required evidence, missing diagnostic publication, retained workspace or unresolved cleanup makes the runner return nonzero. Failed cleanup retains its fixture as evidence.

## Root causes and changes

- TypeScript previously advertised no publish-diagnostics capability, and its server has no pull-diagnostics implementation. The existing persistent owner now advertises the supported client capabilities and adapts **actual bounded push publications** to the existing diagnostic result shape. No publication produces a timeout, never a fabricated empty report.
- Windows providers encode drive letters/colons differently in file URIs. The transport matches normalized admitted document identities; it ignores foreign documents and explicitly stale versions, invalidates retained/already-queued reports on sync/close, bounds document/report retention, redacts opaque diagnostic data, and preserves request byte, notification and lifetime limits.
- TypeScript uses the semantic server (`useSyntaxServer=never`) and disables automatic typing acquisition. `tsserver.js` is resolved only beside the pinned provider installation, never from workspace-controlled `node_modules`. Its existing provider-specific 1024-handle allowance remains; other providers retain 512.
- Native Windows PHP uses the explicitly configured additive `intelephense` provider, with the same binary/entrypoint pins, trusted-root checks and ambiguity denial as existing providers. `phpactor` remains supported for suitable operator-selected runtimes. Current PHPActor's POSIX requirement was **not** repaired or bypassed; Windows PHP acceptance was achieved with Intelephense.
- Intelephense storage is private session-owned state: at most 16 cache directories per state root, each sampled with a 128 MiB / 4096-entry / 0.25-second scan budget. Admission uses the existing interprocess file lock and precedes spawn; symlink/reparse entries and exhausted quotas fail closed. These are admission/sampling limits, not an OS disk sandbox. Normal cleanup removes only the owned cache after the process drains; removal failure preserves quarantine.
- An explicit private `licenceKey` path prevents legacy home-directory licence fallback. The tested bundle defaults telemetry off; no licence, user-home configuration or arbitrary provider configuration is forwarded. A supplemental real-provider wrapper that throws/marks on `os.homedir()` also passed the full PHP cycle, and its access marker remained absent. This supplements the separately successful **unwrapped** provider probe.
- Independent review found cached-result cancellation/deadline bypass and lost tracking during close/reopen synchronization. Both were corrected and regression-tested before final acceptance.

Optional unversioned LSP publications retain standard latest-publication semantics. The client checks explicit versions when provided and disk snapshots before/after requests; it does not invent a provider version or claim proof of causal ordering for a late unversioned message. The real probes specifically demonstrate clean → error → repair after each edit and restart.

## Isolation and reproduction

Use the repo's `.venv/Scripts/python.exe`. Fixtures, private `.state`, and provider scratch `.tmp` directories live under ignored `tests/.real_lsp_runs/`; downloaded providers remain under ignored `tests/.real_lsp_tools/`. `TEMP`/`TMP` (Windows) or `TMPDIR` (POSIX) point to the owned fixture during each CLI run and are restored afterward. The runner never changes the main server's configuration or live operator state. All final probe fixtures and provider processes were removed; the pre-existing ignored `.cache` was preserved.

```powershell
& .\.venv\Scripts\python.exe scripts/real_lsp_acceptance.py python --executable 'C:\Program Files\nodejs\node.exe' --entrypoint (Join-Path $env:LOCALAPPDATA 'Programs\Python\Python312\Lib\site-packages\pyright\dist\dist\pyright-langserver.js')
& .\.venv\Scripts\python.exe scripts/real_lsp_acceptance.py typescript --executable 'C:\Program Files\nodejs\node.exe' --entrypoint (Resolve-Path 'tests/.real_lsp_tools/node_modules/typescript-language-server/lib/cli.mjs').Path
& .\.venv\Scripts\python.exe scripts/real_lsp_acceptance.py php --php-provider intelephense --executable 'C:\Program Files\nodejs\node.exe' --entrypoint (Resolve-Path 'tests/.real_lsp_tools/php/intelephense/node_modules/intelephense/lib/intelephense.js').Path
```

`--entrypoint` is absolute. No global npm installation or production provider enablement is implied. [Intelephense documentation](https://intelephense.net/docs) and [free capabilities](https://intelephense.net/) describe the chosen provider; premium rename/code-action features were not tested. [TypeScript server configuration](https://github.com/typescript-language-server/typescript-language-server/blob/master/docs/configuration.md) documents its initialization options.

### Executable evidence (SHA-256)

| Admitted file | SHA-256 |
|---|---|
| Node executable | `c22d1c59a1f767a1ed0178445a027f2257d318c55430fc819d48f269586822b7` |
| Pyright entrypoint | `e6d7b8e99b8f66acd2446b04db74a6dfa688121ada063549d11a4297aa182e07` |
| TypeScript LS entrypoint | `86ef128358dcd71d0c684d786d7751ee09fbbbbb2a521482b160007b8228be2e` |
| Intelephense entrypoint | `fb4593de29904c422ad2539463028f4dafaf8140365c2523958945b6058ae75c` |

These hashes pin the admitted executable and entrypoint, not every npm dependency file. Operator activation still requires review of the complete installed runtime/dependency supply chain.

## Verification and handoff

- Independent final focused suite: **243 passed, 1 skipped** in 68.05 s. The skip is an existing symlink-creation permission test. No remaining actionable finding in the reviewed LSP scope.
- Full-repository Ruff: **PASS**. Full mypy: **PASS on 297 source files**. Standard compileall: **PASS** after removing owned pytest fixture residue containing intentionally invalid Python; the initial residue-related compile failure was not a product-source failure.
- Startup/catalog: **PASS**, 151 unique native tools. Isolated health smoke: **PASS** on all five check groups.
- Full repository pytest: **2338 passed, 28 skipped in 518.06 s (0:08:38)**, exit 0; `python -m pytest -o addopts= -q --basetemp=.pytest-tmp-lsp-final`. No warning or failure was reported in the final summary.
- Tested branch/HEAD: `feature/coding-agent-evolution` @ `02796e9e7af9401bbe9fc5eda8ce2dde4980dad8` **plus the uncommitted local diff**. HEAD alone does not identify tested code. The SHA-256 of the sorted compact JSON path→SHA manifest for providers/sessions/transport, the acceptance script, provider tests, push tests, harness tests and existing policy tests is `323fda0b17ec1731cdce733ec497f766f1a55ebb65a4e7c1db9ba87b640e8c64`.
- Discovery used Graphify to locate the shared transport's persistent/legacy consumers, followed by current-source verification because the index predates these edits. Codebase Memory MCP returned `Connection closed`; no index rebuild was needed for this bounded change.
- The external canonical Coding Agent plan remains outside the checkout. Its 35 VERIFIED / 5 BLOCKED / 3 DEFERRED task states are unchanged; this supplemental local gate does not waive live-model/profile acceptance.
- Pre-existing worktree changes, panel bytes and `uv.lock` were preserved. No staging, commit, push, release or global provider activation occurred.

Remaining gates: hosted same-SHA CI, real providers on Linux/macOS, operator configuration/supply-chain review and a separate production activation decision. This local acceptance does not establish a release or paid-provider feature acceptance.

## Windows live MCP rollout follow-up (2026-10-26)

The previous table covers isolated local process acceptance. The additional operator-requested rollout is now **VERIFIED_LIVE on Windows**, not merely prepared: the installed candidate matches live SHA-256 `09c952ddde6e73eb0faa6f34847060423c2c28fa1f0d5225d61d69e79cd8c78e`. A separate guarded Task Scheduler controller retained the original config as a byte-for-byte backup, used the existing supervisor restart endpoint and verified pid 13292 to 25320 / runtime generation 1 to 2 / 151 tools in two stable observations. The token was not printed or persisted. The controller task removed itself.

Calls made **through the reconnected Ali Computer Agent MCP**, against disposable repo-local fixtures, returned source-file definition for Python/TypeScript/PHP, references 3/3/4 respectively, clean → one actual severity-1 type diagnostic → repaired to zero for each provider. For PHP, `document_symbols` primed the source before definition and before error diagnostics; the initial cold unindexed definition returned empty, so cold-start indexing is a documented operational caveat. New LSP health shows zero active/quarantined/failed after the idle drain, and the test workspace was removed.

Git publication: seven focused commits (41 related paths) pushed to PR #82, head `5f26cc96e70b9e41ba2c4380239be34be9596fc8`. Initial CI, Platform and Packaging runs started on this exact head, with results verified separately from this document. No merge/release or DAG activation. This Windows operational verification does **not** prove native Linux/macOS real-provider execution or paid provider features.
