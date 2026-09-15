"""Runtime configuration and path handling."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return min(max(value, minimum), maximum)


@dataclass(frozen=True, slots=True)
class Settings:
    """Small immutable runtime configuration."""

    server_name: str = "ali_windows_agent_mcp"
    version: str = "0.0.16"
    default_list_limit: int = _env_int("MCP_DEFAULT_LIST_LIMIT", 50, 1, 500)
    max_list_limit: int = _env_int("MCP_MAX_LIST_LIMIT", 500, 10, 5_000)
    max_file_write_chars: int = _env_int("MCP_MAX_FILE_WRITE_CHARS", 2_000_000, 1_024, 20_000_000)
    ast_cache_max_files: int = _env_int("MCP_AST_CACHE_MAX_FILES", 10_000, 1, 50_000)
    ast_cache_max_bytes: int = _env_int("MCP_AST_CACHE_MAX_BYTES", 64 * 1_024 * 1_024, 1_024, 2 * 1_024 * 1_024 * 1_024)
    search_snapshot_ttl_sec: int = _env_int("MCP_SEARCH_SNAPSHOT_TTL_SEC", 180, 30, 3_600)
    search_snapshot_max_bytes: int = _env_int("MCP_SEARCH_SNAPSHOT_MAX_BYTES", 128 * 1_024 * 1_024, 1_024, 2 * 1_024 * 1_024 * 1_024)
    search_snapshot_max_count: int = _env_int("MCP_SEARCH_SNAPSHOT_MAX_COUNT", 32, 1, 1_024)
    browser_idle_sec: int = _env_int("MCP_BROWSER_IDLE_SEC", 900, 1, 86_400)
    browser_pool_idle_sec: int = _env_int("MCP_BROWSER_POOL_IDLE_SEC", 120, 1, 86_400)
    max_running_jobs: int = _env_int("MCP_MAX_RUNNING_JOBS", 4, 1, 256)
    supervisor_drain_sec: int = _env_int("MCP_SUPERVISOR_DRAIN_SEC", 15, 1, 300)
    supervisor_watchdog_drain_sec: int = _env_int("MCP_SUPERVISOR_WATCHDOG_DRAIN_SEC", 2, 1, 30)
    audit_batch_size: int = _env_int("MCP_AUDIT_BATCH_SIZE", 64, 1, 1_024)
    audit_flush_ms: int = _env_int("MCP_AUDIT_FLUSH_MS", 50, 1, 5_000)
    audit_queue_max: int = _env_int("MCP_AUDIT_QUEUE_MAX", 2_048, 64, 100_000)
    audit_max_file_bytes: int = _env_int("MCP_AUDIT_MAX_FILE_BYTES", 8 * 1_024 * 1_024, 65_536, 1_073_741_824)
    audit_keep_files: int = _env_int("MCP_AUDIT_KEEP_FILES", 5, 1, 100)
    backup_max_bytes: int = _env_int("MCP_BACKUP_MAX_BYTES", 256 * 1_024 * 1_024, 0, 100 * 1_024 * 1_024 * 1_024)
    backup_max_age_days: int = _env_int("MCP_BACKUP_MAX_AGE_DAYS", 30, 0, 3_650)
    backup_cleanup_interval_sec: int = _env_int("MCP_BACKUP_CLEANUP_INTERVAL_SEC", 5, 1, 3_600)
    artifact_max_bytes: int = _env_int("MCP_ARTIFACT_MAX_BYTES", 512 * 1_024 * 1_024, 0, 100 * 1_024 * 1_024 * 1_024)
    artifact_max_age_hours: int = _env_int("MCP_ARTIFACT_MAX_AGE_HOURS", 168, 0, 24 * 3_650)
    artifact_max_count: int = _env_int("MCP_ARTIFACT_MAX_COUNT", 512, 0, 1_000_000)
    artifact_cleanup_interval_sec: int = _env_int("MCP_ARTIFACT_CLEANUP_INTERVAL_SEC", 5, 1, 3_600)
    job_history_max_age_days: int = _env_int("MCP_JOB_HISTORY_MAX_AGE_DAYS", 30, 0, 3_650)
    job_history_max_count: int = _env_int("MCP_JOB_HISTORY_MAX_COUNT", 1_000, 0, 1_000_000)
    job_history_max_bytes: int = _env_int("MCP_JOB_HISTORY_MAX_BYTES", 1 * 1_024 * 1_024 * 1_024, 0, 100 * 1_024 * 1_024 * 1_024)
    job_history_cleanup_interval_sec: int = _env_int("MCP_JOB_HISTORY_CLEANUP_INTERVAL_SEC", 30, 1, 86_400)
    state_dir: Path = PROJECT_ROOT / ".agent_state"
    memory_dir: Path = PROJECT_ROOT / "memory"

    @property
    def audit_log(self) -> Path:
        return self.state_dir / "audit.jsonl"

    @property
    def backup_dir(self) -> Path:
        return self.state_dir / "backups"

    @property
    def screenshot_dir(self) -> Path:
        return self.state_dir / "screenshots"

    @property
    def search_snapshot_dir(self) -> Path:
        return self.state_dir / "search_snapshots"


SETTINGS = Settings()


def ensure_runtime_dirs() -> None:
    """Create local state directories without touching user project paths."""

    for path in (
        SETTINGS.state_dir,
        SETTINGS.backup_dir,
        SETTINGS.screenshot_dir,
        SETTINGS.search_snapshot_dir,
        SETTINGS.memory_dir,
    ):
        path.mkdir(parents=True, exist_ok=True)


def resolve_path(value: str | Path, *, base: str | Path | None = None) -> Path:
    """Resolve absolute or project-relative paths without restricting full access."""

    expanded = Path(os.path.expandvars(os.path.expanduser(str(value))))
    if not expanded.is_absolute():
        root = Path(base) if base is not None else PROJECT_ROOT
        expanded = root / expanded
    return expanded.resolve(strict=False)
