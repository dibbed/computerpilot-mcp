# Five-language LSP architecture review

Status: baseline delivered for static cross-language references and opt-in HTML/CSS LSP; advanced semantics remain proposals.
Observed on Windows 10, ComputerPilot 0.9.0, source commit `315effb`.
Review scope: Python, JavaScript, TypeScript, HTML, CSS, and shared LSP reliability.

## 1. Verified failure and narrowly scoped repair

The persistent Pyright session for the real repository root failed with
`lsp_resource_exceeded`. A traced process-tree sample reached **537.01 MiB**
across two processes at **13.44 s**. The existing `_MAX_RSS_BYTES`
limit is 512 MiB. CPU consumption was about 14.52 s and handle count
was 260, so the direct cause was RSS, not CPU or handle admission.

The checkout contained about **19,670 Python files in ignored `output/`**
and about 320 Git-tracked first-party Python files. Pyright had no
`pyrightconfig.json` at the repository root. Git ignore rules do not
replace a Pyright source inventory. Adding a source-focused
`pyrightconfig.json` with tracked first-party directories and explicit
artifact exclusions made live `language_diagnostics` and
`document_symbols` succeed on `core/jobs.py`. The active LSP health
snapshot then showed **167,182,336 bytes (~159 MiB)** of RSS, one
ready session, and no quarantine. The 512 MiB safety limit was preserved.

The fix is repository-specific: it does not automatically correct other
unconfigured large workspaces. The bounded source inventory requires
updating when new top-level Python source roots are added. A regression
test checks source coverage and exclusion policy.

## 2. Current implementation inventory

| Language | Provider configured/eligible today | Live checks | Scope |
| --- | --- | --- | --- |
| Python | Pinned Pyright | Real repo diagnostics and document symbols passed after inventory fix | Persistent sessions supported |
| JavaScript | Pinned TypeScript language server | Disposable `.js` diagnostics, symbols and JSDoc `TS2345` error matched offline `tsc` | Native LSP supported |
| TypeScript | Pinned TypeScript language server | Disposable `.ts` type error (2322) and symbols verified | Native LSP supported |
| HTML | Opt-in pinned Node provider available | Real Windows Symbols and Push Diagnostics passed | Baseline read-only supported after runtime reload |
| CSS | Opt-in pinned Node provider available; SCSS/LESS routing | Real Windows CSS Symbols and Push Diagnostics passed | Baseline read-only supported after runtime reload |

JSX and TSX are already included in the TypeScript provider's language
set. PHP has separate providers and should retain its current behavior.

Relevant current implementation boundaries:

- `tools/language/providers.py`: `ProviderId` allowlist, provider SHA-256
  pinning, trusted workspace roots and `MAX_PROVIDERS = 6`.
- `tools/language/registry.py`: typed `LanguageId`, exposed operations
  and dispatch rules.
- `tools/language/lsp.py` and `requests.py`: extension-to-language
  mapping and bounded request validation.
- `tools/language/sessions.py`: `SessionKey` currently includes
  `language_id`, so JavaScript and TypeScript in one project start
  **distinct** sessions, even if the provider executable is identical.
  Six active sessions is the current global limit, bounded by 3 GiB aggregate RSS.
- `LspSessionManager.arequest`: uses `pyrightconfig.json` for Python,
  `composer.json` for PHP, and `tsconfig.json` for all other languages
  before attempting read-result fingerprint caching. This misses
  `jsconfig.json` for JavaScript-only projects.
- `_own` in `sessions.py`: TypeScript/Intelephense use the existing push fallback;
  HTML/CSS explicitly consume authentic Push Diagnostics despite falsely
  advertising pull support and returning -32601 for pull requests.
- `LspTransport._notification`: replies to
  `workspace/configuration` with bounded `null` entries. Some HTML/CSS
  servers require correctly typed safe configuration defaults.
- `_resolve_language` now infers HTML/CSS/SCSS/LESS from their suffixes;
  unknown extensions still retain the legacy Python default and need
  clearer unsupported-language rejection in a future change.
- Native process ownership, restricted launch environment, provider
  identity verification, request/data size bounds, cancellation,
  file-version guards and guarded workspace edits are existing
  security properties. Preserve them.

## 3. Recommendations, prioritized

### P0: Make source inventory and failure mode observable

1. Preserve the Pyright source-scoping fix and tracked-source regression.
   The memory ceiling was separately increased to 768 MiB per session,
   with 3 GiB aggregate RSS retained as a hard safety limit.
2. Return a safe, bounded resource-denial category such as
   `rss_limit`, `handle_limit`, `cpu_budget`, `index_limit`,
   without exposing file paths, source text, server stderr or tokens.
   Expose last category and timestamp in `server_health.lsp`, not
   a verbose provider trace.
3. Add an optional read-only preflight that reports approximate
   source counts, config presence, excluded generated roots, the
   chosen provider and limits. It must not modify another user's
   workspace, infer trust from `.gitignore`, or execute arbitrary
   config scripts.
4. On resource failure, fail closed and kill the owned provider
   process tree as today. A future fallback should be explicit:
   narrower workspace root, revised approved config, or an
   opt-in document-scoped mode. Never silently claim full-workspace
   diagnostics were returned after switching to a partial mode.

### P1: Improve Python quality without increasing hidden costs

- Keep Pyright for semantic type errors, definitions, references,
  hovers and import resolution. Treat Ruff and mypy as separate
  verification stages, not as LSP semantic replacements.
- Add supported Pyright diagnostics-mode settings when there is a
  negotiated client configuration channel; prefer open-document
  diagnostics for interactive requests and independently labeled
  full-project checks. Keep the response's `complete` semantics truthful.
- For real monorepos, recognize user-authored
  `pyrightconfig.json` / `[tool.pyright]` without overriding
  user choices. Let the operator select trusted sub-workspaces and
  optional pinned interpreter paths.
- Regression gates: definition in imported first-party module,
  source edits invalidate results, Unicode/UTF-16 offsets,
  type error and no false `ok` after OOM or cancellation.

### P1: JavaScript / TypeScript correctness and efficiency

- Detect `jsconfig.json` when fingerprinting JavaScript read requests;
  `tsconfig.json` only is an incomplete cache gate.
- Respect `tsconfig.json` / `jsconfig.json` includes,
  excludes, references, path aliases, project boundaries,
  `checkJs`, JSX/TSX and JSDoc. Add `.mjs` / `.cjs` / `.mts` /
  `.cts` suffixes only after matching real-provider language-ID and
  module-resolution tests. Never scan arbitrary `dist/`,
  `coverage/`, generated bundles and unbounded `node_modules/`
  as first-party sources by default.
- Consider one **provider-owned session per workspace** for the
  TypeScript server, with per-document language IDs, rather than
  one separate Node tree for each of `javascript`,
  `javascriptreact`, `typescript`, `typescriptreact`.
  This requires deliberate session-key, admission, document-sync,
  cache-generation and cancellation tests: do not just remove
  `language_id` from the cache key.
- Expose a reviewed upper bound for TypeScript server memory
  (`maxTsServerMemory`) only if consistent with the owned process-tree
  RSS and total LSP budgets. Avoid raising a provider heap limit
  beyond the actual process-tree safety budget.
- Add stable real-provider fixtures verifying TS error `2322`,
  JSDoc-driven JS type errors, cross-file definition, TSX symbols,
  jsconfig/tsconfig project isolation and stale-result invalidation.

### P2: Extend baseline HTML and CSS providers safely

- Delivered: opt-in pinned HTML/CSS servers from
  `@t1ckbase/vscode-langservers-extracted@2.0.5`, installed outside
  tracked source and launched by absolute pinned Node/entrypoint under
  native ownership. Runtime startup never auto-downloads providers.
- Extend `ProviderId`, `PROVIDER_LANGUAGES`, `LanguageId`,
  suffix inference, request dispatch and test inventories. Include
  `.html`, `.css`, and optionally `.scss` / `.less` in a
  consciously reviewed rollout. HTML inside `.vue`, `.svelte`
  or JSX requires framework-aware virtual documents; do not
  treat them as ordinary HTML files.
- Implemented: `MAX_PROVIDERS = 6`, maximum six active sessions,
  768 MiB per-session RSS and 3 GiB aggregate RSS with owned process
  cleanup and bounded idle retirement. These are limits, not guarantees
  that all providers may simultaneously consume their full budget.
- Implemented for HTML/CSS: consume version-bound push publications
  after document synchronization, even when the provider advertises
  `diagnosticProvider` but rejects pull requests. Never fabricate
  an empty success when no publication arrives.
- Test actual `workspace/configuration` negotiation and
  schema-checked provider defaults. The existing generic `null`
  response is not sufficient evidence of compatibility.
- HTML acceptance: tag/attribute hover, document symbols,
  malformed markup diagnostics where supported, links, and
  embedded CSS in `<style>` with position translation.
- CSS acceptance: properties, selectors, variables, colors,
  hover, completion, diagnostics, SCSS/LESS only after a real
  provider passes corresponding fixtures.
- Keep parsing and mutation separate: read diagnostics first;
  extend guarded rename/edit only after multi-file checks
  and preimage verification work with new response formats.

### P2/P3: Shared LSP infrastructure

- Implement a provider capability table rather than hard-coded
  checks tied to provider names (pull/push diagnostics, document
  symbols, completion, code actions, server-initiated requests).
- Record reproducible provider-package integrity (offline lockfile or a
  reviewed installation manifest), because the current SHA-256 checks
  cover the launcher executable and entrypoint, not every transitive
  module imported from a Node package tree.
- Add vetted read-only completion/signature-help and incremental
  symbol-index APIs; they are not currently exposed. Cache by
  content fingerprint + provider generation + workspace config.
- Report usable status per language and provider (configured,
  ready, unsupported, resource-denied) in health/discovery without
  leaking pinned paths, credentials or user code.
- Resource fairness: priority to cheap document-local operations;
  queue bulk workspace indexing behind bounded admission.
  Cancel and release the entire owned process tree on timeout.
- Distinguish external-library references from workspace edits.
  Keep writes inside approved roots with content-hash preimages;
  external-definition previews need an explicit separate
  trust boundary and strict read-only policy.
- Do not promise all five language servers can run concurrently
  until the four-session cap and total RSS cap have a tested
  scheduling/reuse policy.
- Delivered: bounded class/ID static source evidence with roles and locations.
  Next: offer a richer cross-language source graph with verified links:
  HTML `id` / `class` references to CSS selectors, CSS Modules
  imports in TSX, and JavaScript DOM selector literals. Start with
  conservative static matching and preserve source-range provenance.
  A graph guess is not an authoritative LSP definition/reference;
  report ambiguity and dynamic-computed selectors as incomplete.
- When a Codebase Memory extension is connected, join its broad
  repository graph with fresh, verified LSP answers rather than
  spawning another redundant indexer. Cache by a versioned source
  fingerprint and invalidate on file changes.

## 4. Suggested delivery order and acceptance

| Milestone | Work | Acceptance evidence |
| --- | --- | --- |
| M0 | Keep bounded Pyright config and LSP failure diagnosis | Reproduce root failure before; root symbols and diagnostics pass after; RSS below existing cap |
| M1 | JS/TS project config and session reuse audit | JS, TS, JSX and TSX real-provider tests, no duplicate session leakage, stale results rejected |
| M2 | HTML and CSS opt-in read-only providers | Pinned launch, trusted roots, HTML/CSS live fixtures, push/pull handling, cleanup on timeout |
| M3 | Embedded CSS, CSS modules and framework documents | Safe virtual-document position mappings and source-version proofs |
| M4 | Richer completion, unified health and per-language QoS | Lower latency and bounded memory demonstrated in measured multi-repo soak |

Cross-platform gates: focused regressions; complete project test suite;
Ruff, mypy and compileall; actual pinned-provider smoke on Windows
and Linux; macOS behavior explicitly reported as verified or
unverified; stream framing, diagnostics correctness, process cleanup,
cancellation, restart and source-edit races.

## 5. Upstream documentation consulted

- Pyright include/exclude and source configuration:
  https://github.com/microsoft/pyright/blob/main/docs/configuration.md
- Pyright language-server diagnostic mode:
  https://github.com/microsoft/pyright/blob/main/docs/settings.md
- TypeScript language server initialize options, maxTsServerMemory
  and configuration:
  https://github.com/typescript-language-server/typescript-language-server/blob/master/docs/configuration.md
- VS Code embedded languages and virtual-document architecture:
  https://code.visualstudio.com/api/language-extensions/embedded-languages
- VS Code HTML language service:
  https://github.com/microsoft/vscode-html-languageservice
- VS Code CSS/SCSS/LESS language service:
  https://github.com/microsoft/vscode-css-languageservice
- Extracted VS Code HTML/CSS LSP:
  https://github.com/hrsh7th/vscode-langservers-extracted
