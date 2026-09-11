"""MCP registration for project summaries and Python AST intelligence."""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Annotated, Any, Literal

from mcp.server import MCPServer
from pydantic import Field

from core.config import resolve_path
from core.response import page
from core.tooling import READ_ONLY, PathArg, compact_errors
from tools.project import service


def _matches(candidate: str, query: str, mode: Literal["exact", "contains"]) -> bool:
    return candidate == query or candidate.rsplit(".", 1)[-1] == query if mode == "exact" else query.casefold() in candidate.casefold()


def _scan_symbols(
    root: Path,
    *,
    name: str,
    match: Literal["exact", "contains"],
    kind: Literal["function", "class"],
    max_files: int,
    offset: int,
    limit: int,
) -> tuple[list[dict[str, Any]], int, list[dict[str, Any]], bool]:
    discovered_files, truncated = service.iter_python_files(root, max_files)
    files = sorted(discovered_files, key=lambda path: str(path).casefold())
    results: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    total = 0
    base = root if root.is_dir() else root.parent
    for path in files:
        try:
            tree = service.parse_python(path)
        except Exception as exc:
            if len(errors) < 10:
                errors.append({"file": str(path), "error": str(exc)})
            continue
        for qualified, node in service.walk_qualified(tree):
            if kind == "class" and not isinstance(node, ast.ClassDef):
                continue
            if kind == "function" and not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if not _matches(qualified, name, match):
                continue
            if offset <= total < offset + limit:
                relative = path.relative_to(base).as_posix() if path.is_relative_to(base) else str(path)
                row: dict[str, Any] = {
                    "file": relative,
                    "qualified_name": qualified,
                    "line": node.lineno,
                    "end_line": node.end_lineno,
                }
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    row["async"] = isinstance(node, ast.AsyncFunctionDef)
                    try:
                        row["signature"] = f"({ast.unparse(node.args)})"
                    except Exception:
                        row["signature"] = None
                elif isinstance(node, ast.ClassDef):
                    row["bases"] = [ast.unparse(base_node) for base_node in node.bases[:10]]
                    row["method_count"] = sum(isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) for child in node.body)
                results.append(row)
            total += 1
    return results, total, errors, truncated


def register(mcp: MCPServer) -> None:
    @mcp.tool(annotations=READ_ONLY, structured_output=True)
    @compact_errors("project_summary")
    def project_summary(
        path: PathArg,
        max_files: Annotated[int, Field(ge=100, le=100_000)] = 10_000,
    ) -> dict[str, Any]:
        """Detect languages, frameworks, dependencies, entry points, databases, and tests."""

        return service.project_summary(resolve_path(path), max_files)

    @mcp.tool(annotations=READ_ONLY, structured_output=True)
    @compact_errors("find_function")
    def find_function(
        path: PathArg,
        function_name: Annotated[str, Field(min_length=1, max_length=500)],
        match: Literal["exact", "contains"] = "exact",
        offset: Annotated[int, Field(ge=0, le=1_000_000)] = 0,
        max_results: Annotated[int, Field(ge=1, le=200)] = 20,
        max_files: Annotated[int, Field(ge=1, le=50_000)] = 5_000,
    ) -> dict[str, Any]:
        """Find Python functions and methods through AST parsing, with qualified names."""

        root = resolve_path(path)
        rows, total, errors, truncated = _scan_symbols(
            root,
            name=function_name,
            match=match,
            kind="function",
            max_files=max_files,
            offset=offset,
            limit=max_results,
        )
        result = page(rows, total=total, offset=offset, limit=max_results)
        result["truncated"] = bool(result["truncated"] or truncated)
        return {"ok": True, "root": str(root), "scan_truncated": truncated, "parse_errors": errors, **result}

    @mcp.tool(annotations=READ_ONLY, structured_output=True)
    @compact_errors("find_class")
    def find_class(
        path: PathArg,
        class_name: Annotated[str, Field(min_length=1, max_length=500)],
        match: Literal["exact", "contains"] = "exact",
        offset: Annotated[int, Field(ge=0, le=1_000_000)] = 0,
        max_results: Annotated[int, Field(ge=1, le=200)] = 20,
        max_files: Annotated[int, Field(ge=1, le=50_000)] = 5_000,
    ) -> dict[str, Any]:
        """Find Python classes through AST parsing, including bases and method counts."""

        root = resolve_path(path)
        rows, total, errors, truncated = _scan_symbols(
            root,
            name=class_name,
            match=match,
            kind="class",
            max_files=max_files,
            offset=offset,
            limit=max_results,
        )
        result = page(rows, total=total, offset=offset, limit=max_results)
        result["truncated"] = bool(result["truncated"] or truncated)
        return {"ok": True, "root": str(root), "scan_truncated": truncated, "parse_errors": errors, **result}

    @mcp.tool(annotations=READ_ONLY, structured_output=True)
    @compact_errors("find_imports")
    def find_imports(
        path: PathArg,
        module_filter: Annotated[str | None, Field(max_length=500)] = None,
        offset: Annotated[int, Field(ge=0, le=1_000_000)] = 0,
        max_results: Annotated[int, Field(ge=1, le=500)] = 50,
        max_files: Annotated[int, Field(ge=1, le=50_000)] = 5_000,
    ) -> dict[str, Any]:
        """List Python import statements from AST without full-text scanning."""

        root = resolve_path(path)
        discovered_files, truncated = service.iter_python_files(root, max_files)
        files = sorted(discovered_files, key=lambda path: str(path).casefold())
        base = root if root.is_dir() else root.parent
        needle = module_filter.casefold() if module_filter else None
        rows: list[dict[str, Any]] = []
        errors: list[dict[str, Any]] = []
        total = 0
        for file_path in files:
            try:
                tree = service.parse_python(file_path)
            except Exception as exc:
                if len(errors) < 10:
                    errors.append({"file": str(file_path), "error": str(exc)})
                continue
            relative = file_path.relative_to(base).as_posix() if file_path.is_relative_to(base) else str(file_path)
            import_nodes = [node for node in ast.walk(tree) if isinstance(node, (ast.Import, ast.ImportFrom))]
            for node in sorted(import_nodes, key=lambda item: item.lineno):
                if isinstance(node, ast.Import):
                    modules = [alias.name for alias in node.names]
                    source = None
                    level = 0
                elif isinstance(node, ast.ImportFrom):
                    source = node.module
                    modules = [alias.name for alias in node.names]
                    level = node.level
                else:
                    continue
                searchable = f"{source or ''} {' '.join(modules)}".casefold()
                if needle and needle not in searchable:
                    continue
                if offset <= total < offset + max_results:
                    rows.append({"file": relative, "line": node.lineno, "from": source, "names": modules, "level": level})
                total += 1
        result = page(rows, total=total, offset=offset, limit=max_results)
        result["truncated"] = bool(result["truncated"] or truncated)
        return {"ok": True, "root": str(root), "scan_truncated": truncated, "parse_errors": errors, **result}

    @mcp.tool(annotations=READ_ONLY, structured_output=True)
    @compact_errors("dependency_graph")
    def dependency_graph(
        path: PathArg,
        include_external: bool = False,
        offset: Annotated[int, Field(ge=0, le=1_000_000)] = 0,
        max_edges: Annotated[int, Field(ge=1, le=1_000)] = 100,
        max_files: Annotated[int, Field(ge=1, le=20_000)] = 2_000,
    ) -> dict[str, Any]:
        """Build a compact Python module import graph with paginated edges."""

        root = resolve_path(path)
        if not root.is_dir():
            raise NotADirectoryError(f"Project directory not found: {root}")
        files, scan_truncated = service.iter_python_files(root, max_files)
        modules = {service.module_name(root, file_path): file_path for file_path in files}
        internal = set(modules)
        internal_by_specificity = sorted(internal, key=len, reverse=True)
        edges: set[tuple[str, str, str]] = set()
        parse_errors: list[dict[str, Any]] = []
        for source, file_path in modules.items():
            try:
                tree = service.parse_python(file_path)
            except Exception as exc:
                if len(parse_errors) < 10:
                    parse_errors.append({"file": str(file_path), "error": str(exc)})
                continue
            for node in ast.walk(tree):
                targets: list[str] = []
                if isinstance(node, ast.Import):
                    targets = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom):
                    base_parts = source.split(".")[:-1]
                    if node.level:
                        keep = max(len(base_parts) - node.level + 1, 0)
                        prefix = base_parts[:keep]
                        if node.module:
                            prefix.extend(node.module.split("."))
                        targets = [".".join(prefix)] if prefix else []
                    elif node.module:
                        targets = [node.module]
                for target in targets:
                    internal_target = next(
                        (
                            candidate
                            for candidate in internal_by_specificity
                            if target == candidate or target.startswith(candidate + ".")
                        ),
                        None,
                    )
                    if internal_target:
                        edges.add((source, internal_target, "internal"))
                    elif include_external and target:
                        edges.add((source, target.split(".")[0], "external"))
        rows = [{"from": source, "to": target, "type": kind} for source, target, kind in sorted(edges)]
        total = len(rows)
        result = page(rows[offset : offset + max_edges], total=total, offset=offset, limit=max_edges)
        result["truncated"] = bool(result["truncated"] or scan_truncated)
        return {
            "ok": True,
            "root": str(root),
            "module_count": len(modules),
            "modules": sorted(modules)[:50],
            "modules_truncated": len(modules) > 50,
            "scan_truncated": scan_truncated,
            "parse_errors": parse_errors,
            **result,
        }
