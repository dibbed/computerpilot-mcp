# Platform Support

ComputerPilot MCP uses capability detection rather than registering every tool on every host.

## Current matrix

| Platform | Status | Native hosted CI | Package target | Native desktop/UIA |
| --- | --- | --- | --- | --- |
| Windows amd64 | Supported | Python 3.10 + 3.12 | Yes | Yes |
| Linux amd64 | Preview | Python 3.10 + 3.12 | Yes | No |
| macOS arm64 | Preview | Python 3.10 + 3.12 | Yes | No |
| Linux arm64 | Preview package target | No dedicated claim for v0.3.0 | Yes | No |
| macOS amd64 | Preview package target | No dedicated claim for v0.3.0 | Yes | No |

The CI matrix runs on `windows-latest`, `ubuntu-latest`, and `macos-latest` for Python 3.10 and 3.12. Platform Release Validation runs the three hosted OS targets on Python 3.12.

## Portable capabilities

The portable core includes:

- filesystem and editing;
- project analysis;
- Git;
- testing/diagnostics;
- durable jobs;
- recovery;
- workflows;
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

Native Windows desktop/UIA tools are not emulated on POSIX hosts. Browser automation is the portable UI path.

## Package targets vs native runtime validation

A package being generated for an architecture is not the same as that architecture receiving dedicated hosted-runner execution.

For v0.3.0:

- Linux arm64 and macOS amd64 are deterministic package/updater targets.
- The project does not claim dedicated native hosted-runner validation for those two architecture combinations.

See the versioned release validation document for release-specific evidence.
