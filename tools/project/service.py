"""Fast, bounded project discovery and Python AST analysis."""

from __future__ import annotations

import ast
import json
import os
import re
import tokenize
from collections import Counter
from itertools import islice
from pathlib import Path
from typing import Any

from core.errors import ToolError
from tools.project.index import PYTHON_METADATA_CACHE, PythonFileMetadata

EXCLUDED_DIRS = {
    ".git",
    ".venv",
    "venv",
    "node_modules",
    "__pycache__",
    ".agent_state",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "graphify-out",
    "build",
    "dist",
}
LANGUAGES = {
    ".py": "Python",
    ".pyi": "Python",
    ".js": "JavaScript",
    ".jsx": "JavaScript",
    ".mjs": "JavaScript",
    ".ts": "TypeScript",
    ".tsx": "TypeScript",
    ".go": "Go",
    ".rs": "Rust",
    ".java": "Java",
    ".kt": "Kotlin",
    ".cs": "C#",
    ".cpp": "C++",
    ".cc": "C++",
    ".c": "C",
    ".h": "C/C++ Header",
    ".php": "PHP",
    ".rb": "Ruby",
    ".swift": "Swift",
    ".ps1": "PowerShell",
    ".sql": "SQL",
}
FRAMEWORKS = {
    "fastapi": "FastAPI",
    "django": "Django",
    "flask": "Flask",
    "starlette": "Starlette",
    "aiogram": "aiogram",
    "mcp": "MCP Python SDK",
    "pytest": "pytest",
    "react": "React",
    "next": "Next.js",
    "vue": "Vue",
    "@angular/core": "Angular",
    "express": "Express",
    "nestjs": "NestJS",
}
DATABASES = {
    "sqlalchemy": "SQLAlchemy",
    "alembic": "Alembic",
    "psycopg": "PostgreSQL",
    "psycopg2": "PostgreSQL",
    "asyncpg": "PostgreSQL",
    "pymysql": "MySQL",
    "mysqlclient": "MySQL",
    "sqlite3": "SQLite",
    "aiosqlite": "SQLite",
    "redis": "Redis",
    "pymongo": "MongoDB",
    "motor": "MongoDB",
}


def iter_files(root: Path, max_files: int) -> tuple[list[Path], bool]:
    if root.is_file():
        return [root], False
    files: list[Path] = []
    for current, directories, names in os.walk(root):
        directories[:] = [name for name in directories if name not in EXCLUDED_DIRS]
        for name in names:
            files.append(Path(current) / name)
            if len(files) >= max_files:
                return files, True
    return files, False


def iter_python_files(root: Path, max_files: int) -> tuple[list[Path], bool]:
    files, truncated = iter_files(root, max_files * 10 if root.is_dir() else max_files)
    python_files = [path for path in files if path.suffix.lower() in {".py", ".pyi"}]
    if len(python_files) > max_files:
        return python_files[:max_files], True
    return python_files, truncated


def python_metadata(path: Path) -> PythonFileMetadata:
    """Return compact version-aware Python metadata from the bounded cache."""

    return PYTHON_METADATA_CACHE.get(path)


def parse_python(path: Path) -> ast.Module:
    """Parse one Python file without caching for raw parser benchmarks."""

    try:
        with tokenize.open(path) as handle:
            return ast.parse(handle.read(), filename=str(path))
    except (SyntaxError, UnicodeError, OSError) as exc:
        raise ToolError("python_parse_error", f"Cannot parse {path}: {exc}") from exc


def _dependency_name(value: str) -> str:
    value = value.strip()
    if not value or value.startswith(("#", "-r", "--")):
        return ""
    value = re.split(r"[<>=!~;\[\s]", value, maxsplit=1)[0]
    return value.strip().lower().replace("_", "-")


def _read_dependencies(root: Path) -> tuple[set[str], list[str]]:
    dependencies: set[str] = set()
    entry_points: list[str] = []
    for requirement in sorted(root.glob("requirements*.txt")):
        try:
            for line in requirement.read_text(encoding="utf-8", errors="replace").splitlines():
                name = _dependency_name(line)
                if name:
                    dependencies.add(name)
        except OSError:
            pass
    pyproject = root / "pyproject.toml"
    if pyproject.is_file():
        try:
            import tomllib

            data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
            project = data.get("project", {})
            for value in project.get("dependencies", []):
                name = _dependency_name(str(value))
                if name:
                    dependencies.add(name)
            for values in project.get("optional-dependencies", {}).values():
                for value in values:
                    name = _dependency_name(str(value))
                    if name:
                        dependencies.add(name)
            for name, target in project.get("scripts", {}).items():
                entry_points.append(f"{name}={target}")
            poetry = data.get("tool", {}).get("poetry", {})
            dependencies.update(str(name).lower() for name in poetry.get("dependencies", {}) if str(name).lower() != "python")
        except (OSError, ValueError):
            pass
    package = root / "package.json"
    if package.is_file():
        try:
            data = json.loads(package.read_text(encoding="utf-8"))
            dependencies.update(data.get("dependencies", {}).keys())
            dependencies.update(data.get("devDependencies", {}).keys())
            if data.get("main"):
                entry_points.append(str(data["main"]))
            if isinstance(data.get("bin"), str):
                entry_points.append(data["bin"])
            elif isinstance(data.get("bin"), dict):
                entry_points.extend(f"{name}={target}" for name, target in data["bin"].items())
        except (OSError, ValueError, TypeError):
            pass
    cargo = root / "Cargo.toml"
    if cargo.is_file():
        try:
            import tomllib

            data = tomllib.loads(cargo.read_text(encoding="utf-8"))
            dependencies.update(data.get("dependencies", {}).keys())
        except (OSError, ValueError):
            pass
    go_mod = root / "go.mod"
    if go_mod.is_file():
        try:
            for line in go_mod.read_text(encoding="utf-8", errors="replace").splitlines():
                stripped = line.strip()
                if stripped and not stripped.startswith(("module", "go ", "require", "replace", "exclude", "//", "(", ")")):
                    dependencies.add(stripped.split()[0])
        except OSError:
            pass
    return dependencies, entry_points


def project_summary(root: Path, max_files: int) -> dict[str, Any]:
    if not root.is_dir():
        raise NotADirectoryError(f"Project directory not found: {root}")
    files, truncated = iter_files(root, max_files)
    language_counts = Counter(LANGUAGES.get(path.suffix.lower()) for path in files)
    language_counts.pop(None, None)
    dependencies, declared_entries = _read_dependencies(root)
    normalized_dependencies = {name.lower().replace("_", "-") for name in dependencies}
    frameworks = sorted(label for package, label in FRAMEWORKS.items() if package.lower().replace("_", "-") in normalized_dependencies)
    databases = sorted(label for package, label in DATABASES.items() if package.lower().replace("_", "-") in normalized_dependencies)
    common_entries = {
        "main.py",
        "app.py",
        "manage.py",
        "server.py",
        "cli.py",
        "index.js",
        "index.ts",
        "src/main.rs",
        "cmd/main.go",
    }
    entries = list(declared_entries)
    test_paths: set[str] = set()
    for path in files:
        relative = path.relative_to(root).as_posix()
        if relative in common_entries or path.name in {"__main__.py", "manage.py"}:
            entries.append(relative)
        parts = {part.casefold() for part in path.parts}
        if "tests" in parts or "test" in parts or path.name.startswith("test_") or path.name.endswith("_test.py"):
            try:
                test_paths.add(path.relative_to(root).parts[0])
            except ValueError:
                pass
    markers = []
    for name in (
        "pyproject.toml",
        "requirements.txt",
        "package.json",
        "go.mod",
        "Cargo.toml",
        "Dockerfile",
        "docker-compose.yml",
        "alembic.ini",
    ):
        if (root / name).exists():
            markers.append(name)
    top_level: list[dict[str, str]] = []
    top_level_truncated = False
    try:
        candidates = list(islice(root.iterdir(), 31))
        top_level_truncated = len(candidates) > 30
        for item in sorted(candidates[:30], key=lambda path: path.name.casefold()):
            top_level.append({"name": item.name, "type": "directory" if item.is_dir() else "file"})
    except OSError:
        pass
    return {
        "ok": True,
        "path": str(root),
        "files_scanned": len(files),
        "scan_truncated": truncated,
        "languages": [{"name": name, "files": count} for name, count in language_counts.most_common(8)],
        "frameworks": frameworks[:15],
        "dependency_count": len(dependencies),
        "dependencies": sorted(dependencies)[:30],
        "dependencies_truncated": len(dependencies) > 30,
        "entry_points": sorted(set(entries))[:20],
        "databases": databases,
        "test_paths": sorted(test_paths)[:15],
        "markers": markers,
        "top_level": top_level,
        "top_level_truncated": top_level_truncated,
    }


def module_name(root: Path, path: Path) -> str:
    relative = path.relative_to(root).with_suffix("")
    parts = list(relative.parts)
    if parts and parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)
