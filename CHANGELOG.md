# Changelog

All notable project-level changes are documented here.

Project releases are independent from the bundled upstream tunnel-client.exe version; binary runtime provenance remains in BINARY_PROVENANCE.md.

## [Unreleased]

### Fixed
- Correct Phase A benchmark fidelity so startup exercises the real bootstrap validation path and large output preserves the current default inline behavior.
- Measure peak RSS and I/O across the benchmark process tree so durable job workers and browser processes are included.
- Serialize the complete wire-style tool catalog and enforce search correctness expectations.
- Add a real `find_function` project lookup benchmark alongside raw AST parsing and age-bounded cleanup for stale benchmark temp directories.

### Changed
- Split opt-in tool profiling into validation, queue/dispatch wait, tool body, result conversion, request pipeline, and serialization phases.
- Coalesce timing JSONL writes through a bounded background writer to reduce observer I/O on the tool hot path.

## [0.0.12] - 2026-09-12

### Added
- Opt-in structured performance timing for MCP requests, tool bodies, process phases, output delivery, and artifact snapshots.
- Repeatable quick/full benchmark harness for catalog, startup, output, AST, search, durable jobs, and optional browser sessions.
- Benchmark tests plus latency, RSS, I/O, and serialization measurements.

### Changed
- Project MCP version metadata now follows the public release series and is 0.0.12.
- README documents timing and benchmark workflows.

### Validation
- 54 pytest tests passed.
- Ruff passed.
- mypy passed across 54 source files.
- compileall passed.
- Quick benchmark, 1/10-job benchmark, and one Chromium benchmark were validated.

## [0.0.11] - 2026-09-11

### Added
- Initial public Windows Developer Agent MCP baseline with 59 registered MCP tools.
