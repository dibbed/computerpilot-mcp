"""Read-only test-runner discovery that never executes project scripts or configs."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import xml.etree.ElementTree as ET
from dataclasses import dataclass, replace
from pathlib import Path, PurePosixPath
from typing import Any, Literal

try:
    import tomllib as _toml
except ImportError:  # Python 3.10 reports missing parser evidence instead of guessing config.
    _toml = None

from core.config import resolve_path
from core.errors import ToolError
from tools.project.project_types import ManifestInfo, PackageInfo, ProjectInventory, _read_bytes, detect_project_types

RunnerKind = Literal["pytest", "unittest", "jest", "vitest", "phpunit", "pest", "unknown"]

_MAX_FILE_BYTES = 256 * 1024
_MAX_AGGREGATE_BYTES = 4 * 1024 * 1024
_MAX_PACKAGES = 256
_MAX_INSTALLED_PACKAGES = 10_000
_JEST_CONFIGS = (
    "jest.config.js",
    "jest.config.cjs",
    "jest.config.mjs",
    "jest.config.ts",
    "jest.config.mts",
    "jest.config.cts",
    "jest.config.json",
)
_VITEST_CONFIGS = (
    "vitest.config.js",
    "vitest.config.cjs",
    "vitest.config.mjs",
    "vitest.config.ts",
    "vitest.config.mts",
    "vitest.config.cts",
    "vite.config.js",
    "vite.config.cjs",
    "vite.config.mjs",
    "vite.config.ts",
    "vite.config.mts",
    "vite.config.cts",
)
_PHP_CONFIGS = ("phpunit.xml", "phpunit.xml.dist")


@dataclass(frozen=True, slots=True)
class RunnerRuntime:
    """Identity selected by an authorized native invocation, never by project metadata."""

    path: Path
    sha256: str
    binary_path: Path


@dataclass(frozen=True, slots=True)
class RunnerSpec:
    runner: RunnerKind
    package_root: Path
    manifest_digest: str
    config_paths: tuple[str, ...]
    entrypoint: Path | None
    version: str | None
    discovery_evidence: tuple[str, ...]
    complete: bool
    missing_inputs: tuple[str, ...]
    workspace_root: Path | None = None
    workspace_id: str | None = None
    runtime: RunnerRuntime | None = None
    entrypoint_sha256: str | None = None


@dataclass(frozen=True, slots=True)
class RunnerInventory:
    root: Path
    runners: tuple[RunnerSpec, ...]
    complete: bool
    missing_inputs: tuple[str, ...]
    version: Literal[1] = 1


class _BoundedReader:
    """Share one aggregate byte budget across every bounded project read."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.consumed = 0

    def read(self, path: Path, maximum: int = _MAX_FILE_BYTES) -> bytes:
        remaining = _MAX_AGGREGATE_BYTES - self.consumed
        if remaining < 1:
            raise ToolError("inventory_limit", "Test runner discovery byte budget is exhausted.")
        data = _read_bytes(path, self.root, min(maximum, remaining))
        self.consumed += len(data)
        return data


def _error(code: str, path: Path, root: Path) -> ToolError:
    relative = path.relative_to(root).as_posix() if path.is_relative_to(root) else "outside_workspace"
    return ToolError(code, f"Test runner discovery input is unavailable or invalid: {relative}")


def _entrypoint_error() -> ToolError:
    return ToolError(
        "test_runner_entrypoint_invalid",
        "A test runner entrypoint must stay inside its installed package and be a direct executable file.",
    )


def _safe_resolve(path: Path) -> Path | None:
    try:
        return path.resolve(strict=False)
    except (OSError, RuntimeError):
        return None


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key")
        result[key] = value
    return result


def _reject_constant(value: str) -> Any:
    raise ValueError("Non-finite JSON number")


def _json_object(data: bytes, path: Path, root: Path, *, manifest: bool = False) -> dict[str, Any]:
    code = "invalid_project_manifest" if manifest else "invalid_test_configuration"
    try:
        parsed = json.loads(data.decode("utf-8", errors="strict"), object_pairs_hook=_unique_object, parse_constant=_reject_constant)
    except (ValueError, TypeError, RecursionError, UnicodeError):
        raise _error(code, path, root) from None
    if not isinstance(parsed, dict):
        raise _error(code, path, root)
    return parsed


def _version(value: object) -> str | None:
    return value if isinstance(value, str) and 1 <= len(value) <= 100 else None


def _dependency_names(value: object) -> set[str]:
    if value is None:
        return set()
    if not isinstance(value, list):
        raise ToolError("invalid_project_manifest", "Python dependency metadata must be a list.")
    names: set[str] = set()
    for item in value:
        if isinstance(item, dict) and set(item) == {"include-group"} and isinstance(item["include-group"], str):
            continue
        if not isinstance(item, str):
            raise ToolError("invalid_project_manifest", "Python dependency metadata has an unsupported value.")
        name = re.split(r"[<>=!~;\[\s]", item, maxsplit=1)[0].strip().lower().replace("_", "-")
        if name:
            names.add(name)
    return names


def _object(value: object) -> dict[str, Any] | None:
    return value if isinstance(value, dict) else None


def _python_table(value: object) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ToolError("invalid_project_manifest", "Python dependency metadata must be a table.")
    return value


def _python_dependencies(document: dict[str, Any]) -> set[str]:
    names: set[str] = set()
    project = _python_table(document.get("project"))
    names.update(_dependency_names(project.get("dependencies")))
    optional = _python_table(project.get("optional-dependencies"))
    for value in optional.values():
        names.update(_dependency_names(value))
    dependency_groups = _python_table(document.get("dependency-groups"))
    for value in dependency_groups.values():
        names.update(_dependency_names(value))
    tool = _python_table(document.get("tool"))
    poetry = _python_table(tool.get("poetry"))
    names.update(
        str(name).lower().replace("_", "-")
        for name in _python_table(poetry.get("dependencies"))
    )
    names.update(
        str(name).lower().replace("_", "-")
        for name in _python_table(poetry.get("dev-dependencies"))
    )
    groups = _python_table(poetry.get("group"))
    for group in groups.values():
        group_object = _python_table(group)
        dependencies = _python_table(group_object.get("dependencies"))
        names.update(
            str(name).lower().replace("_", "-")
            for name in dependencies
        )
    return names


def _manifest_data(reader: _BoundedReader, manifest: ManifestInfo) -> bytes:
    data = reader.read(manifest.path)
    if hashlib.sha256(data).hexdigest() != manifest.sha256:
        raise ToolError("invalid_project_manifest", f"Project manifest changed during test runner discovery: {manifest.path.name}")
    return data


def _existing_configs(package_root: Path, names: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(name for name in names if (package_root / name).is_file())


def _config_evidence(
    reader: _BoundedReader, root: Path, package_root: Path, names: tuple[str, ...]
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    paths: list[str] = []
    evidence: list[str] = []
    for name in names:
        path = package_root / name
        if not path.is_file():
            continue
        data = reader.read(path)
        if path.suffix == ".json":
            _json_object(data, path, root)
        relative = path.relative_to(root).as_posix()
        paths.append(relative)
        evidence.append(f"config_sha256:{relative}:{hashlib.sha256(data).hexdigest()}")
    return tuple(paths), tuple(evidence)


def _php_config_evidence(
    reader: _BoundedReader, root: Path, package_root: Path
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    paths: list[str] = []
    evidence: list[str] = []
    for name in _PHP_CONFIGS:
        path = package_root / name
        if not path.is_file():
            continue
        data = reader.read(path)
        try:
            text = data.decode("utf-8", errors="strict")
        except UnicodeError:
            raise ToolError(
                "invalid_test_configuration",
                "A PHP test configuration must be bounded UTF-8 XML.",
            ) from None
        if "\x00" in text:
            raise ToolError(
                "invalid_test_configuration",
                "A PHP test configuration must be bounded UTF-8 XML.",
            )
        lowered = text.casefold()
        if "<!doctype" in lowered or "<!entity" in lowered:
            raise ToolError(
                "invalid_test_configuration",
                "A PHP test configuration cannot declare a document type or entity.",
            )
        try:
            ET.fromstring(data)
        except (ET.ParseError, LookupError, ValueError):
            raise ToolError("invalid_test_configuration", "A PHP test configuration is not valid XML.") from None
        relative = path.relative_to(root).as_posix()
        paths.append(relative)
        evidence.append(f"config_sha256:{relative}:{hashlib.sha256(data).hexdigest()}")
    return tuple(paths), tuple(evidence)


def _python_spec(reader: _BoundedReader, root: Path, package: PackageInfo) -> RunnerSpec | None:
    manifests = tuple(item for item in package.manifests if item.kind == "python")
    if not manifests:
        return None
    manifest = manifests[0]
    data = _manifest_data(reader, manifest)
    missing: list[str] = []
    evidence: list[str] = []
    config_paths: list[str] = []
    pytest_configured = False
    if _toml is None:
        missing.append("toml_parser_unavailable")
    else:
        try:
            document = _toml.loads(data.decode("utf-8", errors="strict"))
        except (ValueError, TypeError, RecursionError, UnicodeError):
            raise _error("invalid_project_manifest", manifest.path, root) from None
        if not isinstance(document, dict):
            raise _error("invalid_project_manifest", manifest.path, root)
        tool = _object(document.get("tool")) or {}
        if isinstance(tool.get("pytest"), dict):
            pytest_configured = True
            config_paths.append(manifest.path.relative_to(root).as_posix())
            evidence.append(f"config_sha256:{manifest.path.relative_to(root).as_posix()}:{manifest.sha256}")
        if "pytest" in _python_dependencies(document):
            pytest_configured = True
            evidence.append("dependency:pytest")
    pytest_ini = package.root / "pytest.ini"
    if pytest_ini.is_file():
        pytest_configured = True
        ini_data = reader.read(pytest_ini)
        relative = pytest_ini.relative_to(root).as_posix()
        config_paths.append(relative)
        evidence.append(f"config_sha256:{relative}:{hashlib.sha256(ini_data).hexdigest()}")
    tests_directory = next((name for name in ("tests", "test") if (package.root / name).is_dir()), None)
    if tests_directory is not None:
        evidence.append(f"tests_directory:{tests_directory}")
    if not pytest_configured:
        if tests_directory is None:
            return None
        return RunnerSpec(
            runner="unknown",
            package_root=package.root,
            manifest_digest=manifest.sha256,
            config_paths=tuple(config_paths),
            entrypoint=None,
            version=None,
            discovery_evidence=tuple(dict.fromkeys(evidence)),
            complete=False,
            missing_inputs=tuple(dict.fromkeys((*missing, "runner_required"))),
        )
    return RunnerSpec(
        runner="pytest",
        package_root=package.root,
        manifest_digest=manifest.sha256,
        config_paths=tuple(config_paths),
        entrypoint=None,
        version=None,
        discovery_evidence=tuple(dict.fromkeys(evidence)),
        complete=not missing,
        missing_inputs=tuple(missing),
    )


def _node_spec(
    reader: _BoundedReader, root: Path, package: PackageInfo
) -> list[RunnerSpec]:
    manifests = tuple(item for item in package.manifests if item.kind == "node")
    if not manifests:
        return []
    manifest = manifests[0]
    data = _manifest_data(reader, manifest)
    document = _json_object(data, manifest.path, root, manifest=True)
    if "jest" in document and not isinstance(document["jest"], dict):
        raise _error("invalid_project_manifest", manifest.path, root)
    declared = _dependency_declarations(document, ("dependencies", "devDependencies"), manifest.path, root)
    candidates = [name for name in ("jest", "vitest") if name in declared]
    if not candidates:
        return []
    present = {
        "jest": _existing_configs(package.root, _JEST_CONFIGS),
        "vitest": _existing_configs(package.root, _VITEST_CONFIGS),
    }
    jest_package_config = isinstance(document.get("jest"), dict)
    configured = {
        "jest": bool(present["jest"]) or jest_package_config,
        "vitest": bool(present["vitest"]),
    }
    selected: RunnerKind
    if len(candidates) == 1:
        selected = "jest" if candidates[0] == "jest" else "vitest"
    elif configured["jest"] != configured["vitest"]:
        selected = "jest" if configured["jest"] else "vitest"
    else:
        all_evidence = tuple(
            f"config_present:{(package.root / name).relative_to(root).as_posix()}"
            for candidate in candidates
            for name in present[candidate]
        )
        return [
            RunnerSpec(
                runner="unknown",
                package_root=package.root,
                manifest_digest=manifest.sha256,
                config_paths=(),
                entrypoint=None,
                version=None,
                discovery_evidence=("dependency:jest", "dependency:vitest", *all_evidence),
                complete=False,
                missing_inputs=("runner_required",),
            )
        ]
    names = _JEST_CONFIGS if selected == "jest" else _VITEST_CONFIGS
    config_paths, config_evidence = _config_evidence(reader, root, package.root, names)
    if selected == "jest" and jest_package_config:
        manifest_path = manifest.path.relative_to(root).as_posix()
        config_paths = (*config_paths, manifest_path)
        config_evidence = (*config_evidence, f"config_sha256:{manifest_path}:{manifest.sha256}", "config:package.json#jest")
    return [
        _node_runner_spec(
            reader,
            root,
            package,
            manifest,
            selected,
            config_paths,
            config_evidence,
        )
    ]


def _dependency_declarations(
    document: dict[str, Any], keys: tuple[str, ...], path: Path, root: Path
) -> set[str]:
    names: set[str] = set()
    for key in keys:
        if key not in document:
            continue
        dependencies = _object(document.get(key))
        if dependencies is None:
            raise _error("invalid_project_manifest", path, root)
        if any(not name or not isinstance(value, str) for name, value in dependencies.items()):
            raise _error("invalid_project_manifest", path, root)
        names.update(dependencies.keys())
    return names


def _node_runner_spec(
    reader: _BoundedReader,
    root: Path,
    package: PackageInfo,
    manifest: ManifestInfo,
    runner: RunnerKind,
    config_paths: tuple[str, ...],
    config_evidence: tuple[str, ...],
) -> RunnerSpec:
    missing: list[str] = []
    evidence = [f"dependency:{runner}", *config_evidence]
    installed_dir = _node_installed_dir(package.root, root, runner)
    installed_manifest = installed_dir / "package.json" if installed_dir is not None else None
    installed: dict[str, Any] | None = None
    version: str | None = None
    if installed_manifest is not None and installed_manifest.is_file():
        installed = _json_object(reader.read(installed_manifest), installed_manifest, root)
        if installed.get("name") != runner:
            raise _error("invalid_test_configuration", installed_manifest, root)
        version = _version(installed.get("version"))
        evidence.append(f"installed:{installed_manifest.relative_to(root).as_posix()}")
        if version is None:
            missing.append("version_unavailable")
    else:
        missing.append("dependency_not_installed")
    entrypoint = (
        _node_entrypoint(installed_dir, runner, installed, missing)
        if installed_dir is not None and installed is not None else None
    )
    if shutil.which("node") is None:
        missing.append("runtime_unavailable")
    return RunnerSpec(
        runner=runner,
        package_root=package.root,
        manifest_digest=manifest.sha256,
        config_paths=config_paths,
        entrypoint=entrypoint,
        version=version,
        discovery_evidence=tuple(dict.fromkeys(evidence)),
        complete=not missing,
        missing_inputs=tuple(dict.fromkeys(missing)),
    )


def _node_installed_dir(package_root: Path, root: Path, runner: RunnerKind) -> Path | None:
    """Find a package-local or workspace-hoisted installed runner."""

    current = package_root
    while current.is_relative_to(root):
        candidate = current / "node_modules" / runner
        if (candidate / "package.json").is_file():
            return candidate
        if current == root:
            return None
        parent = current.parent
        if parent == current:
            return None
        current = parent
    return None


def _node_entrypoint(
    installed_dir: Path,
    runner: RunnerKind,
    installed: dict[str, Any],
    missing: list[str],
) -> Path | None:
    bin_value = installed.get("bin")
    candidate: object = None
    if isinstance(bin_value, str):
        candidate = bin_value
    elif isinstance(bin_value, dict):
        candidate = bin_value.get(runner)
        if runner in bin_value and not isinstance(candidate, str):
            raise _entrypoint_error()
    elif bin_value is not None:
        raise _entrypoint_error()
    if candidate is None:
        missing.append("entrypoint_missing")
        return None
    if not isinstance(candidate, str) or not 1 <= len(candidate) <= 500:
        missing.append("entrypoint_missing")
        return None
    pure = PurePosixPath(candidate)
    if (
        "\\" in candidate
        or ":" in candidate
        or "\x00" in candidate
        or pure.is_absolute()
        or ".." in pure.parts
    ):
        raise _entrypoint_error()
    candidate_path = installed_dir / candidate
    resolved = _safe_resolve(candidate_path)
    installed_resolved = _safe_resolve(installed_dir)
    if (
        resolved is None
        or installed_resolved is None
        or not resolved.is_relative_to(installed_resolved)
        or candidate_path.is_symlink()
    ):
        raise _entrypoint_error()
    if resolved.suffix.casefold() in {".cmd", ".bat", ".ps1"}:
        raise _entrypoint_error()
    try:
        info = resolved.lstat()
    except OSError:
        missing.append("entrypoint_missing")
        return None
    if not stat.S_ISREG(info.st_mode) or int(getattr(info, "st_file_attributes", 0)) & getattr(
        stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0
    ):
        missing.append("entrypoint_missing")
        return None
    return resolved


def _php_entrypoint(reader: _BoundedReader, package_root: Path, binary: Path) -> Path | None:
    if not binary.is_file():
        return None
    resolved = _safe_resolve(binary)
    package_resolved = _safe_resolve(package_root)
    if resolved is None or package_resolved is None or not resolved.is_relative_to(package_resolved):
        return None
    if resolved.suffix.casefold() in {".cmd", ".bat", ".ps1"}:
        return None
    try:
        info = resolved.stat()
    except OSError:
        return None
    if not stat.S_ISREG(info.st_mode):
        return None
    data = reader.read(resolved)
    try:
        text = data.decode("utf-8-sig", errors="strict")
    except UnicodeError:
        raise _entrypoint_error() from None
    if "\x00" in text or re.match(r"\A(?:\#![^\r\n]*\r?\n)?<\?php(?:\s|$)", text) is None:
        raise _entrypoint_error()
    return resolved


def _php_spec(reader: _BoundedReader, root: Path, package: PackageInfo) -> list[RunnerSpec]:
    manifests = tuple(item for item in package.manifests if item.kind == "php")
    if not manifests:
        return []
    manifest = manifests[0]
    data = _manifest_data(reader, manifest)
    document = _json_object(data, manifest.path, root, manifest=True)
    declared = _dependency_declarations(document, ("require-dev",), manifest.path, root)
    candidates = [name for name in ("phpunit/phpunit", "pestphp/pest") if name in declared]
    if not candidates:
        return []
    config_paths, config_evidence = _php_config_evidence(reader, root, package.root)
    installed_versions = _php_installed_versions(reader, root, package.root)
    if len(candidates) > 1:
        return [
            RunnerSpec(
                runner="unknown",
                package_root=package.root,
                manifest_digest=manifest.sha256,
                config_paths=config_paths,
                entrypoint=None,
                version=None,
                discovery_evidence=(
                    "dependency:phpunit/phpunit",
                    "dependency:pestphp/pest",
                    *config_evidence,
                ),
                complete=False,
                missing_inputs=("runner_required",),
            )
        ]
    package_name = candidates[0]
    runner: RunnerKind = "pest" if package_name == "pestphp/pest" else "phpunit"
    missing: list[str] = []
    evidence = [f"dependency:{package_name}", *config_evidence]
    version = installed_versions.get(package_name)
    if version is None:
        missing.append("installed_metadata_missing" if not installed_versions else "dependency_not_installed")
    else:
        installed_path = (package.root / "vendor" / "composer" / "installed.json").relative_to(root).as_posix()
        evidence.append(f"installed:{installed_path}")
    binary = package.root / "vendor" / "bin" / ("pest" if runner == "pest" else "phpunit")
    entrypoint = _php_entrypoint(reader, package.root, binary)
    if entrypoint is not None:
        evidence.append(f"entrypoint:vendor/bin/{binary.name}")
    if entrypoint is None:
        missing.append("entrypoint_missing")
    if shutil.which("php") is None:
        missing.append("runtime_unavailable")
    return [
        RunnerSpec(
            runner=runner,
            package_root=package.root,
            manifest_digest=manifest.sha256,
            config_paths=config_paths,
            entrypoint=entrypoint,
            version=version,
            discovery_evidence=tuple(dict.fromkeys(evidence)),
            complete=not missing,
            missing_inputs=tuple(dict.fromkeys(missing)),
        )
    ]


def _php_installed_versions(reader: _BoundedReader, root: Path, package_root: Path) -> dict[str, str]:
    path = package_root / "vendor" / "composer" / "installed.json"
    if not path.is_file():
        return {}
    try:
        document = json.loads(
            reader.read(path).decode("utf-8", errors="strict"),
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
    except (ValueError, TypeError, RecursionError, UnicodeError):
        raise _error("invalid_test_configuration", path, root) from None
    packages = document.get("packages") if isinstance(document, dict) else document
    if not isinstance(packages, list):
        raise _error("invalid_test_configuration", path, root)
    if len(packages) > _MAX_INSTALLED_PACKAGES:
        raise ToolError("inventory_limit", "Installed Composer metadata exceeded its package limit.")
    versions: dict[str, str] = {}
    for item in packages:
        package = _object(item)
        if package is None:
            raise _error("invalid_test_configuration", path, root)
        name = package.get("name")
        version = _version(package.get("version"))
        if not isinstance(name, str) or not name or version is None or name in versions:
            raise _error("invalid_test_configuration", path, root)
        versions[name] = version
    return versions


def _root_pytest_spec(reader: _BoundedReader, root: Path) -> RunnerSpec | None:
    """Discover a workspace pytest.ini that has no manifest package to own it."""

    ini = root / "pytest.ini"
    if not ini.is_file():
        return None
    data = reader.read(ini)
    relative = ini.relative_to(root).as_posix()
    return RunnerSpec(
        runner="pytest",
        package_root=root,
        manifest_digest="",
        config_paths=(relative,),
        entrypoint=None,
        version=None,
        discovery_evidence=(f"config_sha256:{relative}:{hashlib.sha256(data).hexdigest()}",),
        complete=True,
        missing_inputs=(),
    )


def discover_test_runners(root: Path, inventory: ProjectInventory) -> RunnerInventory:
    """Discover bounded runner metadata without dispatching any project code."""

    try:
        root = resolve_path(root)
        inventory_root = resolve_path(inventory.root)
    except (OSError, RuntimeError):
        raise ToolError("project_scope_escape", "The selected workspace cannot be resolved.") from None
    if (
        type(inventory.version) is not int
        or inventory.version != 1
        or inventory_root != root
    ):
        raise ToolError("project_scope_escape", "The inventory does not identify the selected workspace.")
    if len(inventory.packages) > _MAX_PACKAGES:
        raise ToolError("inventory_limit", "Test runner discovery exceeded its package limit.")
    reader = _BoundedReader(root)
    specs: list[RunnerSpec] = []
    root_has_python_manifest = False
    for package in inventory.packages:
        try:
            package_root = resolve_path(package.root)
        except (OSError, RuntimeError):
            raise ToolError("project_scope_escape", "A package cannot be resolved.") from None
        if not package_root.is_relative_to(root):
            raise ToolError("project_scope_escape", "A package leaves the selected workspace.")
        if package_root == root and any(item.kind == "python" for item in package.manifests):
            root_has_python_manifest = True
        python_spec = _python_spec(reader, root, package)
        if python_spec is not None:
            specs.append(python_spec)
        specs.extend(_node_spec(reader, root, package))
        specs.extend(_php_spec(reader, root, package))
    if not root_has_python_manifest:
        orphan_root_spec = _root_pytest_spec(reader, root)
        if orphan_root_spec is not None:
            specs.append(orphan_root_spec)
    specs.sort(key=lambda item: (item.package_root.as_posix().casefold(), item.runner))
    missing = list(inventory.missing_inputs)
    for spec in specs:
        if spec.complete:
            continue
        relative = spec.package_root.relative_to(root).as_posix() if spec.package_root.is_relative_to(root) else "outside_workspace"
        missing.extend(f"{relative}:{reason}" for reason in spec.missing_inputs)
    return RunnerInventory(
        root=root,
        runners=tuple(specs),
        complete=inventory.complete and all(spec.complete for spec in specs),
        missing_inputs=tuple(sorted(dict.fromkeys(missing))),
    )


def _execution_error(code: str = "stale_test_runner") -> ToolError:
    return ToolError(code, "The test runner identity is unavailable, unsupported, or changed; rediscover before execution.")


def _file_sha256(path: Path, maximum: int = 256 * 1024 * 1024) -> str:
    """Hash bounded regular files without accepting a replaced file or final symlink."""
    try:
        before = path.lstat()
        if (
            not stat.S_ISREG(before.st_mode) or before.st_size > maximum
            or int(getattr(before, "st_file_attributes", 0)) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
        ):
            raise _execution_error()
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(descriptor, "rb") as stream:
            opened = os.fstat(stream.fileno())
            digest = hashlib.sha256()
            consumed = 0
            while chunk := stream.read(min(65536, maximum + 1 - consumed)):
                consumed += len(chunk)
                if consumed > maximum:
                    raise _execution_error()
                digest.update(chunk)
            finished = os.fstat(stream.fileno())
        after = path.lstat()
        identities = {(item.st_dev, item.st_ino, item.st_size, item.st_mtime_ns) for item in (before, opened, finished, after)}
        if len(identities) != 1 or consumed != before.st_size:
            raise _execution_error()
        return digest.hexdigest()
    except (OSError, ValueError):
        raise _execution_error() from None


def _execution_runtime(spec: RunnerSpec, root: Path) -> Path:
    if spec.runner in {"pytest", "unittest"}:
        from tools.testing.runners import python_for

        candidate = Path(python_for(spec.package_root))
        if not candidate.is_absolute():
            raise _execution_error("runtime_unavailable")
        return candidate.parent.resolve(strict=True) / candidate.name
    name = "node" if spec.runner in {"jest", "vitest"} else "php"
    # Do not let Windows' implicit cwd search, PATHEXT scripts, or project PATH entries select a runtime.
    for value in os.environ.get("PATH", "").split(os.pathsep):
        directory = Path(value)
        if not value or not directory.is_absolute():
            continue
        try:
            directory = directory.resolve(strict=True)
            if directory.is_relative_to(root):
                continue
            candidate = directory / (name + ".exe" if os.name == "nt" else name)
            resolved = candidate.resolve(strict=True)
            if resolved.is_relative_to(root) or not resolved.is_file() or not os.access(resolved, os.X_OK):
                continue
            if resolved.suffix.casefold() in {".cmd", ".bat", ".ps1"}:
                continue
            return resolved
        except (OSError, RuntimeError):
            continue
    raise _execution_error("runtime_unavailable")


def _discovery_spec(spec: RunnerSpec) -> RunnerSpec:
    return replace(spec, workspace_root=None, workspace_id=None, runtime=None, entrypoint_sha256=None)


def _current_runner(spec: RunnerSpec, root: Path) -> ProjectInventory:
    inventory = detect_project_types(root)
    fresh = discover_test_runners(root, inventory)
    selected = _discovery_spec(spec)
    candidates = list(fresh.runners)
    if spec.runner == "unittest":
        candidates.extend(
            replace(item, runner="unittest", complete=True, missing_inputs=())
            for item in fresh.runners
            if item.runner == "unknown" and item.missing_inputs == ("runner_required",)
            and any(value.startswith("tests_directory:") for value in item.discovery_evidence)
            and any(manifest.kind == "python" for package in inventory.packages if package.root == item.package_root
                    for manifest in package.manifests)
        )
    if not inventory.complete or selected not in candidates or not spec.complete:
        raise _execution_error()
    return inventory


def _execution_workspace_id(root: Path, inventory: ProjectInventory) -> str:
    info = root.stat()
    return hashlib.sha256(f"{inventory.worktree_id}:{info.st_dev}:{info.st_ino}".encode()).hexdigest()


def bind_runner_runtime(
    spec: RunnerSpec, *, workspace_root: Path, runner: Literal["unittest"] | None = None,
) -> RunnerSpec:
    """Bind host runtime identity at the native authorization boundary; this grants no permission."""
    if spec.runtime is not None:
        validate_runner_execution(spec)
        if workspace_root.resolve(strict=True) != spec.workspace_root or (runner is not None and runner != spec.runner):
            raise _execution_error()
        return spec
    try:
        if runner is not None:
            if runner != "unittest" or spec.runner != "unknown" or spec.missing_inputs != ("runner_required",):
                raise _execution_error()
            spec = replace(spec, runner="unittest", complete=True, missing_inputs=())
        root = workspace_root.resolve(strict=True)
        if (
            not workspace_root.is_absolute() or spec.package_root.resolve(strict=True) != spec.package_root
            or not spec.package_root.is_relative_to(root)
        ):
            raise _execution_error()
        inventory = _current_runner(spec, root)
        runtime = _execution_runtime(spec, root)
        binary = runtime.resolve(strict=True)
        bound = replace(
            spec, workspace_root=root, workspace_id=_execution_workspace_id(root, inventory),
            runtime=RunnerRuntime(runtime, _file_sha256(binary), binary),
            entrypoint_sha256=_file_sha256(spec.entrypoint, _MAX_AGGREGATE_BYTES) if spec.entrypoint else None,
        )
        validate_runner_execution(bound)
        return bound
    except (OSError, RuntimeError) as exc:
        if isinstance(exc, ToolError):
            raise
        raise _execution_error() from None


def validate_runner_execution(spec: RunnerSpec) -> None:
    """Recheck current discovery and original pins immediately before every spawn."""
    try:
        root, runtime = spec.workspace_root, spec.runtime
        if (
            root is None or runtime is None or not root.is_absolute() or root.resolve(strict=True) != root
            or not spec.package_root.is_relative_to(root) or spec.package_root.resolve(strict=True) != spec.package_root
            or not runtime.path.is_absolute() or runtime.path.parent.resolve(strict=True) != runtime.path.parent
            or runtime.path.resolve(strict=True) != runtime.binary_path
            or (spec.runner not in {"pytest", "unittest"} and runtime.path != runtime.binary_path)
            or runtime.path.suffix.casefold() in {".cmd", ".bat", ".ps1"}
        ):
            raise _execution_error()
        inventory = _current_runner(spec, root)
        if _execution_workspace_id(root, inventory) != spec.workspace_id or _execution_runtime(spec, root) != runtime.path:
            raise _execution_error()
        if _file_sha256(runtime.binary_path) != runtime.sha256:
            raise _execution_error()
        if spec.entrypoint is not None:
            if _file_sha256(spec.entrypoint, _MAX_AGGREGATE_BYTES) != spec.entrypoint_sha256:
                raise _execution_error()
        elif spec.entrypoint_sha256 is not None:
            raise _execution_error()
    except (OSError, RuntimeError) as exc:
        if isinstance(exc, ToolError):
            raise
        raise _execution_error() from None
