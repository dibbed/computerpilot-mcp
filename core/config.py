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
    version: str = "0.0.15"
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
