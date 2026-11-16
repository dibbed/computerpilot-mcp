# Cross-language references and HTML/CSS LSP implementation

## Delivered in November 2026

The new read-only tool cross_language_references searches HTML/CSS/JS/TS
(including JSX/TSX) for static class and ID evidence. For every match it
returns the source file, line, column, syntactic role and static_evidence
confidence. Dynamic selectors make complete=false and increment
dynamic_expression_count. This is not proof of runtime DOM identity.
No edits are performed.

Examples of supported evidence:

- HTML: a div with class="profile-card" and id="user"
- CSS: selectors .profile-card and #user
- JS: document.querySelector(".profile-card"), getElementById("user"),
  and classList.add("profile-card")
- JSX/TSX: className="profile-card"
- Inline HTML style and executable script tags: CSS selectors and static
  DOM calls are source-located within the original HTML file.

A query specifying path to a project, symbol="profile-card" and kind="class"
groups matching source locations into a single bounded response.
The scanner excludes symlinked files and generated output, .agent_state,
.worktrees, node_modules, build and dist. Hard limits are 1 MiB per file,
16 MiB of total content, 10,000 candidate files and 500 result rows per
page. It exposes truncation and unreadable-file counts. Dynamic
classList arguments, JSX spreads, interpolated HTML class/id values and
framework-style bindings mark the scan incomplete. CSS selector scanning
uses a single linear delimiter walk; static JS calls use a bounded
argument scanner, avoiding pathological rescanning of long unclosed
rules or calls. Windows junctions and symlinks are excluded. Some
advanced CSS syntax, Unicode/escaped identifiers, CSS Modules,
framework-specific components and runtime-computed JavaScript
expressions are not resolved. Unsupported syntax is not proof of
semantic absence.

## HTML and CSS providers

Reviewed package: @t1ckbase/vscode-langservers-extracted version 2.0.5.
Sources:
https://www.npmjs.com/package/@t1ckbase/vscode-langservers-extracted
https://github.com/T1ckbase/vscode-langservers-extracted

Installed in an ignored operator-managed directory, separately from the
existing TypeScript provider:

    npm.cmd install --prefix ".agent_state\lsp-html-css-providers" --ignore-scripts --no-audit --no-fund --save-exact "@t1ckbase/vscode-langservers-extracted@2.0.5"

The reviewed configuration uses the already pinned Node executable,
explicit trusted workspace roots, and SHA-256-pinned entrypoints:
- node_modules/@t1ckbase/vscode-langservers-extracted/bin/html.js
- node_modules/@t1ckbase/vscode-langservers-extracted/bin/css.js

Both launch using stdio with native process-tree ownership and minimal
environment. A local ignored operator configuration under
.agent_state/config/lsp-providers.json was expanded from 3 to 5 entries.
A dated original-config backup was saved alongside it. Node packages and
private operator config are not part of Git commits or bundled releases.

The HTML/CSS provider advertises pull diagnostics but rejects the pull
method with -32601. ComputerPilot now obtains actual version-bound
publishDiagnostics notifications instead. It never manufactures an empty
success when no publication is available. Real live acceptance on Windows
confirmed nonempty Symbols, genuine diagnostic publications and complete
process-tree cleanup for both HTML and CSS. SCSS/LESS IDs are registered
and have now passed independent live Windows document-symbol and
diagnostic-protocol smoke checks (responses may contain zero findings).

## Revised resource budgets

RAM measured on the machine: ~15.82 GiB total, ~5.83 GiB available.
These resource limits remain enforced, not bypassed:

| Parameter | Earlier | Now |
| --- | --- | --- |
| Per-LSP process-tree RSS | 512 MiB | 768 MiB |
| Total LSP RSS | 2 GiB | 3 GiB |
| Concurrent sessions | 4 | 6 |
| Allowed provider identities | 4 | 6 |
| Idle retirement | 120 seconds | 180 seconds |
| Windows handles per session | 512 | 768 |
| TypeScript handle cap | 1024 | 1536 |
| Max single document | 2 MiB | 4 MiB |
| Session document aggregate | 16 MiB | 32 MiB |
| Private cache size | 128 MiB | 192 MiB |
| Cache file entries | 4096 | 6144 |
| Cache directories | 16 | 24 |

No disabling of path-trust checks, hash pinning, cancellation, worker
ownership, protocol/request byte budgets or global resource caps.

## Verification and activation

- End-to-end MCP Client using fresh code and new operator config:
  158 native tools; new cross-language tool; HTML/CSS document symbols and
  diagnostics succeeded.
- Focused provider and resource regression suites passed on Windows.
- Adversarial frontend scanner regressions cover regex false positives,
  classList dynamic arguments, HTML template bindings and inline script
  evidence, malformed large inputs, pagination, symlink/junction
  boundaries and deterministic stress fixtures. Source-only scans are
  explicitly not equivalent to executable cross-file reference graphs.
- Real provider smoke exercised Python, JS, TS, HTML, CSS, SCSS and LESS.
  JS/TS test filenames are deliberately distinct because identical
  basename JavaScript and TypeScript files in one TypeScript project can
  shadow the .js file in the compiler's file inventory.
- Current live ChatGPT tunnel uses its original in-memory tool catalog
  until the supervised service is reloaded and the client reconnects.
  Do not launch an unmanaged duplicate server just to reload tools.
- Test commands: python -m pytest tests/test_cross_language_references.py
  tests/test_lsp_html_css_providers.py tests/test_lsp_resource_budgets.py

## Future work

1. Proper CSS/HTML/JS parsers and cross-file source maps with uncertainty
   and dynamic-expression evidence.
2. Shared JS/TS process per trusted workspace and jsconfig.json awareness.
3. Bounded provider-specific failures, telemetry and error categories.
4. Advanced source-map-aware HTML embedded CSS/JS analysis,
   cross-component links and guarded rename.
5. More acceptance for modern JS module suffixes, SCSS/LESS diagnostics
   with deliberately invalid fixtures, and macOS.
