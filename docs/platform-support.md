# Platform Support

ComputerPilot MCP uses capability detection rather than registering every tool on every host.

## Current matrix

| Platform | Release label | Validation status | Native hosted CI | Package target | Native desktop/UIA |
| --- | --- | --- | --- | --- | --- |
| Windows amd64 | Supported | CI + Platform Validation + native interactive acceptance | Python 3.10 + 3.12 | Yes | Yes |
| Linux amd64 | Preview | CI + Platform Validation + native Linux acceptance | Python 3.10 + 3.12 | Yes | No |
| macOS arm64 | Preview | CI + Platform Validation | Python 3.10 + 3.12 | Yes | No |
| Linux arm64 | Preview package target | Packaging/updater mapping | No dedicated runner claim | Yes | No |
| macOS amd64 | Preview package target | Packaging/updater mapping | No dedicated runner claim | Yes | No |

The CI matrix runs on `windows-latest`, `ubuntu-latest`, and `macos-latest` for Python 3.10 and 3.12. Platform Release Validation runs the three hosted OS targets on Python 3.12.

## Validation scope

| Capability | Linux amd64 | Windows amd64 | macOS |
| --- | --- | --- | --- |
| Full automated suite (hosted CI at `c33004e`) | 2,516 passed, 24 skipped | 2,526 passed, 14 skipped | 2,516 passed, 24 skipped |
| Real MCP stdio/HTTP | Verified | Verified | Hosted CI coverage |
| Native terminal execution | Verified | Verified | Hosted CI coverage |
| Process cancellation/recovery | Verified | Verified | Hosted CI coverage |
| SQLite schema v12 migration tests | Verified (synthetic) | Verified (synthetic) | Hosted CI coverage |
| Real Chromium (local fixture) | Verified | Verified | Not independently verified |
| Real Pyright language server | Verified | Verified | Not independently verified |
| UI Automation | Not applicable | Verified with isolated fixture | Not applicable |
| Bounded MCP soak | Verified (181 s) | Verified (181 s) | Not independently verified |

The macOS result validates portable CI behavior and the platform release gate. It is not a claim that every interactive, hardware-dependent or desktop workflow was exercised on a native Mac. A local Windows grouped acceptance run recorded 2,505 passed and 35 skipped, and a final local ungrouped run recorded 2,508 passed and 35 skipped; the extra local skips are mostly symlink-privilege cases that the hosted runner grants. The final integration adds three Pyright launcher regression tests, so its own hosted logs are the authority for updated totals. The 181-second soaks are bounded observations, not multi-hour leak proofs. See the [cross-platform report](CROSS-PLATFORM-RUNTIME-HARDENING-REPORT.md) and [Windows native acceptance report](WINDOWS-NATIVE-ACCEPTANCE-REPORT.md).

## Portable capabilities

The portable core includes:

- filesystem and editing;
- project analysis;
- Git;
- testing/diagnostics;
- native XLSX/XLSM, DOCX, and PDF document tools;
- durable jobs;
- recovery;
- workflows;
- deterministic adaptive execution routing over the tools actually registered on that host/profile;
- project memory;
- process/system inspection with platform adapters;
- browser automation when Playwright is installed;
- Secure Tunnel managed runtime selection for supported OS/architecture mappings.

## Windows-specific capabilities

Windows can additionally expose:

- native desktop screenshots;
- mouse/keyboard input;
- semantic Windows UI Automation;
- `run_cmd`;
- Windows SCM service inspection;
- Registry-backed installed-software inspection;
- Windows Job Object process ownership.

These domains/tools are capability-gated.

## Linux/macOS differences

Linux/macOS use:

- POSIX `sh` for explicit shell execution;
- dedicated sessions/process groups for owned process trees;
- Linux service/software adapters such as systemd/SysV/package-manager discovery where available;
- macOS launchd/application/system metadata adapters.

Native Windows desktop/UIA tools are not emulated on POSIX hosts. Browser automation is the portable UI path. The execution router therefore excludes `semantic.windows_uia` and `raw.desktop` candidates on hosts where their required capabilities/tools are unavailable instead of pretending the fallback exists.

## Package targets vs native runtime validation

A package being generated for an architecture is not the same as that architecture receiving dedicated hosted-runner execution.

For the current source line:

- Linux arm64 and macOS amd64 are deterministic package/updater targets.
- The project does not claim dedicated native hosted-runner validation for those two architecture combinations.

See the versioned release validation document for published-release-specific evidence.
