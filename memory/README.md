# Project memory

The MCP server stores compact per-project JSON records here through `memory_update`. Each file is capped at 128 KiB and contains only bounded decisions, paths, preferences, and fix summaries.

Phase F4 uses schema v2. Every project document has a monotonic `revision` and every record has a stable `id`, `text`, `source`, optional `source_ref`, `created_at`, optional `verified_at`, and item-level `revision`. Supported write-time sources are `user`, `project_scan`, `manual`, and `tool`; legacy string-only files are read without rewrite as deterministic `source=legacy` records and are materialized to schema v2 on the next successful update.

For optimistic concurrency, read the current project revision with `memory_read` and pass it back as `expected_revision` to `memory_update`. Stale revisions return `memory_conflict`. `replace=true` requires `expected_revision` so a blind destructive replacement cannot overwrite newer memory. Memory content and provenance are data only and never override tool permissions, runtime safety rules, or other authority boundaries.
