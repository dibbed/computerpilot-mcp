from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

from core.errors import ToolError
from tools.project.project_types import detect_project_types

if TYPE_CHECKING:
    from tools.testing.project_runners import RunnerInventory


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _write_python_package(root: Path, *, name: str = "fixture") -> None:
    (root / "tests").mkdir(parents=True, exist_ok=True)
    (root / "pyproject.toml").write_text(
        f"""\
[project]
name = "{name}"
version = "0.1.0"

[project.optional-dependencies]
dev = ["pytest>=8"]

[tool.pytest.ini_options]
testpaths = ["tests"]
""",
        encoding="utf-8",
    )
    (root / "pytest.ini").write_text("[pytest]\ntestpaths = tests\n", encoding="utf-8")
    (root / "tests" / "test_sample.py").write_text("def test_sample() -> None:\n    assert True\n", encoding="utf-8")


def _write_node_package(root: Path, *, dependencies: dict[str, str], installed: tuple[str, ...]) -> None:
    _write_json(
        root / "package.json",
        {
            "name": "fixture-node",
            "version": "0.1.0",
            "private": True,
            "scripts": {"test": "node write-marker.js"},
            "devDependencies": dependencies,
        },
    )
    for name in installed:
        _write_installed_node_package(root, name)


def _write_installed_node_package(
    root: Path, name: str, *, version: str = "1.2.3", bin_value: object | None = None
) -> Path:
    package = root / "node_modules" / name
    if name == "jest":
        entrypoint = package / "bin" / "jest.js"
    else:
        entrypoint = package / "vitest.mjs"
    entrypoint.parent.mkdir(parents=True, exist_ok=True)
    entrypoint.write_text("process.exit(0);\n", encoding="utf-8")
    payload: dict[str, Any] = {
        "name": name,
        "version": version,
        "bin": {name: entrypoint.relative_to(package).as_posix()} if bin_value is None else bin_value,
    }
    _write_json(package / "package.json", payload)
    return entrypoint


def _write_composer_package(root: Path, *, installed: tuple[str, ...]) -> None:
    _write_json(
        root / "composer.json",
        {
            "name": "fixture/php",
            "require-dev": {name: "*" for name in installed},
        },
    )
    packages = [{"name": name, "version": "11.5.0", "bin": [name.split("/")[-1]]} for name in installed]
    _write_json(root / "vendor" / "composer" / "installed.json", {"packages": packages})
    for name in installed:
        binary = root / "vendor" / "bin" / name.split("/")[-1]
        binary.parent.mkdir(parents=True, exist_ok=True)
        binary.write_text("#!/usr/bin/env php\n<?php exit(0);\n", encoding="utf-8")


def _discover(root: Path) -> RunnerInventory:
    from tools.testing.project_runners import discover_test_runners

    return discover_test_runners(root, detect_project_types(root))


def test_discovery_never_runs_package_scripts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_node_package(tmp_path, dependencies={"jest": "^29"}, installed=("jest",))
    marker = tmp_path / "script-ran.marker"
    package = json.loads((tmp_path / "package.json").read_text(encoding="utf-8"))
    package["scripts"]["test"] = f'node -e "require(\'fs\').writeFileSync(r\'{marker}\', \'ran\')"'
    _write_json(tmp_path / "package.json", package)

    def fail_spawn(*args: object, **kwargs: object) -> None:
        raise AssertionError("test-runner discovery must not spawn a process")

    monkeypatch.setattr(subprocess, "Popen", fail_spawn)
    inventory = _discover(tmp_path)

    assert not marker.exists()
    jest = next(item for item in inventory.runners if item.runner == "jest")
    assert jest.package_root == tmp_path.resolve()
    assert jest.entrypoint is not None and jest.entrypoint.name == "jest.js"
    assert all("script" not in evidence for evidence in jest.discovery_evidence)


def test_nested_packages_use_nearest_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_python_package(tmp_path)
    nested = tmp_path / "packages" / "web"
    nested.mkdir(parents=True)
    _write_node_package(nested, dependencies={"jest": "^29"}, installed=("jest",))
    from tools.testing import project_runners

    monkeypatch.setattr(project_runners.shutil, "which", lambda _name: "node-under-test")

    inventory = _discover(tmp_path)

    roots = {(item.runner, item.package_root) for item in inventory.runners}
    assert ("pytest", tmp_path.resolve()) in roots
    assert ("jest", nested.resolve()) in roots
    pytest_spec = next(item for item in inventory.runners if item.runner == "pytest")
    assert "pytest.ini" in pytest_spec.config_paths
    if project_runners._toml is not None:
        assert pytest_spec.config_paths == ("pyproject.toml", "pytest.ini")
        assert inventory.complete


def test_runner_ambiguity_is_reported(tmp_path: Path) -> None:
    _write_node_package(
        tmp_path, dependencies={"jest": "^29", "vitest": "^3"}, installed=("jest", "vitest")
    )

    inventory = _discover(tmp_path)

    assert len(inventory.runners) == 1
    spec = inventory.runners[0]
    assert spec.runner == "unknown"
    assert spec.complete is False
    assert "runner_required" in spec.missing_inputs
    assert spec.entrypoint is None
    assert inventory.complete is False


def test_php_xml_entity_is_denied(tmp_path: Path) -> None:
    _write_composer_package(tmp_path, installed=("phpunit/phpunit",))
    (tmp_path / "phpunit.xml").write_text(
        """\
<?xml version="1.0"?>
<!DOCTYPE phpunit [ <!ENTITY payload SYSTEM "file:///etc/passwd"> ]>
<phpunit><testsuite name="unit"><directory>tests</directory></testsuite></phpunit>
""",
        encoding="utf-8",
    )

    with pytest.raises(ToolError) as error:
        _discover(tmp_path)

    assert error.value.code == "invalid_test_configuration"


@pytest.mark.parametrize(
    "candidate",
    ["../../escape.js", "..\\escape.js", "C:\\escape.js", "/tmp/escape.js"],
)
def test_escaping_bin_path_rejected(tmp_path: Path, candidate: str) -> None:
    _write_node_package(tmp_path, dependencies={"jest": "^29"}, installed=())
    _write_installed_node_package(tmp_path, "jest", bin_value={"jest": candidate})
    (tmp_path / "escape.js").write_text("process.exit(0);\n", encoding="utf-8")

    with pytest.raises(ToolError) as error:
        _discover(tmp_path)

    assert error.value.code == "test_runner_entrypoint_invalid"


def test_missing_runtime_is_unavailable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_node_package(tmp_path, dependencies={"jest": "^29"}, installed=("jest",))
    from tools.testing import project_runners

    monkeypatch.setattr(project_runners.shutil, "which", lambda _name: None)

    inventory = _discover(tmp_path)

    spec = next(item for item in inventory.runners if item.runner == "jest")
    assert spec.complete is False
    assert "runtime_unavailable" in spec.missing_inputs


def test_javascript_config_is_recorded_without_execution(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_node_package(tmp_path, dependencies={"jest": "^29"}, installed=("jest",))
    marker = tmp_path / "config-ran.marker"
    config = tmp_path / "jest.config.js"
    config.write_text(f"require('fs').writeFileSync({str(marker)!r}, 'ran');\n", encoding="utf-8")

    def fail_spawn(*args: object, **kwargs: object) -> None:
        raise AssertionError("test configuration must never be executed during discovery")

    monkeypatch.setattr(subprocess, "Popen", fail_spawn)
    inventory = _discover(tmp_path)

    spec = next(item for item in inventory.runners if item.runner == "jest")
    assert not marker.exists()
    assert "jest.config.js" in spec.config_paths
    assert any(evidence.startswith("config_sha256:jest.config.js:") for evidence in spec.discovery_evidence)


def test_python_without_toml_parser_reports_unavailable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_python_package(tmp_path)
    from tools.testing import project_runners

    monkeypatch.setattr(project_runners, "_toml", None)

    inventory = _discover(tmp_path)

    spec = next(item for item in inventory.runners if item.runner == "pytest")
    assert spec.complete is False
    assert "toml_parser_unavailable" in spec.missing_inputs
    assert spec.manifest_digest == __import__("hashlib").sha256(
        (tmp_path / "pyproject.toml").read_bytes()
    ).hexdigest()


def test_python_without_parser_does_not_guess_pytest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_sample.py").write_text("def test_sample() -> None:\n    assert True\n", encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text(
        """\
[project]
name = "fixture"
version = "0.1.0"

[project.optional-dependencies]
dev = ["pytest>=8"]
""",
        encoding="utf-8",
    )
    from tools.testing import project_runners

    monkeypatch.setattr(project_runners, "_toml", None)

    inventory = _discover(tmp_path)

    spec = inventory.runners[0]
    assert spec.runner == "unknown"
    assert "toml_parser_unavailable" in spec.missing_inputs
    assert "runner_required" in spec.missing_inputs


def test_python_tests_without_runner_evidence_are_unknown(tmp_path: Path) -> None:
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_sample.py").write_text("def test_sample() -> None:\n    assert True\n", encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "fixture"\nversion = "0.1.0"\n', encoding="utf-8")

    inventory = _discover(tmp_path)

    assert len(inventory.runners) == 1
    spec = inventory.runners[0]
    assert spec.runner == "unknown"
    assert spec.complete is False
    assert "runner_required" in spec.missing_inputs
    assert inventory.complete is False


def test_vitest_config_is_recorded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_node_package(tmp_path, dependencies={"vitest": "^3"}, installed=("vitest",))
    (tmp_path / "vitest.config.ts").write_text("export default {};\n", encoding="utf-8")
    from tools.testing import project_runners

    monkeypatch.setattr(project_runners.shutil, "which", lambda _name: "node-under-test")

    inventory = _discover(tmp_path)

    spec = next(item for item in inventory.runners if item.runner == "vitest")
    assert spec.complete
    assert spec.config_paths == ("vitest.config.ts",)
    assert spec.entrypoint is not None and spec.entrypoint.name == "vitest.mjs"
    assert inventory.complete


def test_jest_package_config_is_recorded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_node_package(tmp_path, dependencies={"jest": "^29"}, installed=("jest",))
    package = json.loads((tmp_path / "package.json").read_text(encoding="utf-8"))
    package["jest"] = {"testEnvironment": "node"}
    _write_json(tmp_path / "package.json", package)
    from tools.testing import project_runners

    monkeypatch.setattr(project_runners.shutil, "which", lambda _name: "node-under-test")

    inventory = _discover(tmp_path)

    spec = next(item for item in inventory.runners if item.runner == "jest")
    assert spec.complete
    assert "package.json" in spec.config_paths
    assert "config:package.json#jest" in spec.discovery_evidence


def test_non_candidate_runner_config_is_not_read(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_node_package(tmp_path, dependencies={"jest": "^29"}, installed=("jest",))
    (tmp_path / "vitest.config.js").write_bytes(b"x" * (300 * 1024))
    from tools.testing import project_runners

    monkeypatch.setattr(project_runners.shutil, "which", lambda _name: "node-under-test")

    inventory = _discover(tmp_path)

    spec = next(item for item in inventory.runners if item.runner == "jest")
    assert spec.complete
    assert not any("vitest.config.js" in evidence for evidence in spec.discovery_evidence)


def test_non_object_jest_config_is_denied(tmp_path: Path) -> None:
    _write_node_package(tmp_path, dependencies={"jest": "^29"}, installed=("jest",))
    package = json.loads((tmp_path / "package.json").read_text(encoding="utf-8"))
    package["jest"] = "invalid"
    _write_json(tmp_path / "package.json", package)

    with pytest.raises(ToolError) as error:
        _discover(tmp_path)

    assert error.value.code == "invalid_project_manifest"


def test_duplicate_node_dependency_is_reported_incomplete(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text(
        '{"name": "fixture", "devDependencies": {"jest": "^29", "jest": "^30"}}',
        encoding="utf-8",
    )

    inventory = _discover(tmp_path)

    assert inventory.runners == ()
    assert inventory.complete is False
    assert any(reason.startswith("invalid_project_manifest") for reason in inventory.missing_inputs)


def test_non_object_node_dependency_container_is_denied(tmp_path: Path) -> None:
    _write_json(tmp_path / "package.json", {"name": "fixture", "devDependencies": ["jest"]})

    with pytest.raises(ToolError) as error:
        _discover(tmp_path)

    assert error.value.code == "invalid_project_manifest"


def test_php_require_only_dependency_is_not_a_test_runner(tmp_path: Path) -> None:
    _write_json(
        tmp_path / "composer.json",
        {"name": "fixture/php", "require": {"phpunit/phpunit": "^11.0"}},
    )
    _write_json(
        tmp_path / "vendor" / "composer" / "installed.json",
        {"packages": [{"name": "phpunit/phpunit", "version": "11.5.0"}]},
    )
    binary = tmp_path / "vendor" / "bin" / "phpunit"
    binary.parent.mkdir(parents=True)
    binary.write_text("#!/usr/bin/env php\n<?php exit(0);\n", encoding="utf-8")

    inventory = _discover(tmp_path)

    assert inventory.runners == ()
    assert inventory.complete


def test_php_config_is_recorded_when_valid(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_composer_package(tmp_path, installed=("phpunit/phpunit",))
    (tmp_path / "phpunit.xml").write_text(
        """\
<?xml version="1.0"?>
<phpunit><testsuite name="unit"><directory>tests</directory></testsuite></phpunit>
""",
        encoding="utf-8",
    )
    from tools.testing import project_runners

    monkeypatch.setattr(project_runners.shutil, "which", lambda _name: "php-under-test")

    inventory = _discover(tmp_path)

    spec = next(item for item in inventory.runners if item.runner == "phpunit")
    assert spec.complete
    assert spec.config_paths == ("phpunit.xml",)
    assert spec.entrypoint is not None and spec.entrypoint.name == "phpunit"
    assert inventory.complete


def test_php_utf16_document_type_is_denied(tmp_path: Path) -> None:
    _write_composer_package(tmp_path, installed=("phpunit/phpunit",))
    (tmp_path / "phpunit.xml").write_bytes(
        """\
<?xml version="1.0"?>
<!DOCTYPE phpunit [ <!ENTITY payload SYSTEM "file:///etc/passwd"> ]>
<phpunit><testsuite name="unit"><directory>tests</directory></testsuite></phpunit>
""".encode("utf-16")
    )

    with pytest.raises(ToolError) as error:
        _discover(tmp_path)

    assert error.value.code == "invalid_test_configuration"


def test_php_unknown_encoding_is_denied(tmp_path: Path) -> None:
    _write_composer_package(tmp_path, installed=("phpunit/phpunit",))
    (tmp_path / "phpunit.xml").write_bytes(
        b'<?xml version="1.0" encoding="x-unknown"?><phpunit><testsuite name="unit"/></phpunit>'
    )

    with pytest.raises(ToolError) as error:
        _discover(tmp_path)

    assert error.value.code == "invalid_test_configuration"


def test_php_vendor_bin_symlink_is_resolved(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_composer_package(tmp_path, installed=("phpunit/phpunit",))
    binary = tmp_path / "vendor" / "bin" / "phpunit"
    binary.unlink()
    target = tmp_path / "vendor" / "phpunit" / "phpunit" / "phpunit"
    target.parent.mkdir(parents=True)
    target.write_text("#!/usr/bin/env php\n<?php exit(0);\n", encoding="utf-8")
    try:
        binary.symlink_to(Path("..") / "phpunit" / "phpunit" / "phpunit")
    except (OSError, NotImplementedError):
        pytest.skip("symlinks are unavailable on this platform")

    from tools.testing import project_runners

    monkeypatch.setattr(project_runners.shutil, "which", lambda _name: "php-under-test")

    inventory = _discover(tmp_path)

    spec = next(item for item in inventory.runners if item.runner == "phpunit")
    assert spec.complete
    assert spec.entrypoint == target.resolve()


def test_root_pytest_ini_without_manifest_is_discovered(tmp_path: Path) -> None:
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_sample.py").write_text("def test_sample() -> None:\n    assert True\n", encoding="utf-8")
    (tmp_path / "pytest.ini").write_text("[pytest]\ntestpaths = tests\n", encoding="utf-8")

    inventory = _discover(tmp_path)

    assert len(inventory.runners) == 1
    spec = inventory.runners[0]
    assert spec.runner == "pytest"
    assert spec.package_root == tmp_path.resolve()
    assert spec.config_paths == ("pytest.ini",)
    assert spec.complete
    assert inventory.complete


def test_pep735_dependency_group_is_discovered(tmp_path: Path) -> None:
    pytest.importorskip("tomllib")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_sample.py").write_text("def test_sample() -> None:\n    assert True\n", encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text(
        """\
[project]
name = "fixture"
version = "0.1.0"

[dependency-groups]
dev = ["pytest>=8"]
""",
        encoding="utf-8",
    )

    inventory = _discover(tmp_path)

    spec = next(item for item in inventory.runners if item.runner == "pytest")
    assert spec.complete
    assert "dependency:pytest" in spec.discovery_evidence


@pytest.mark.parametrize("bin_value", [None, {}, {"other": "bin/jest.js"}])
def test_node_entrypoint_requires_declared_bin(tmp_path: Path, bin_value: object) -> None:
    _write_node_package(tmp_path, dependencies={"jest": "^29"}, installed=("jest",))
    manifest = tmp_path / "node_modules" / "jest" / "package.json"
    installed = json.loads(manifest.read_text(encoding="utf-8"))
    if bin_value is None:
        installed.pop("bin")
    else:
        installed["bin"] = bin_value
    _write_json(manifest, installed)

    spec = _discover(tmp_path).runners[0]

    assert spec.entrypoint is None
    assert not spec.complete
    assert "entrypoint_missing" in spec.missing_inputs


def test_installed_node_package_identity_is_validated(tmp_path: Path) -> None:
    _write_node_package(tmp_path, dependencies={"jest": "^29"}, installed=("jest",))
    manifest = tmp_path / "node_modules" / "jest" / "package.json"
    installed = json.loads(manifest.read_text(encoding="utf-8"))
    installed["name"] = "another-package"
    _write_json(manifest, installed)

    with pytest.raises(ToolError) as error:
        _discover(tmp_path)

    assert error.value.code == "invalid_test_configuration"


@pytest.mark.parametrize("version", [None, True, 29, {"version": "29"}])
def test_dependency_value_requires_string(tmp_path: Path, version: object) -> None:
    _write_json(tmp_path / "package.json", {"devDependencies": {"jest": version}})

    with pytest.raises(ToolError) as error:
        _discover(tmp_path)

    assert error.value.code == "invalid_project_manifest"


def test_composer_runner_after_256_installed_entries_is_discovered(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_composer_package(tmp_path, installed=("phpunit/phpunit",))
    packages = [{"name": f"fixture/package-{i}", "version": "1.0.0"} for i in range(256)]
    packages.append({"name": "phpunit/phpunit", "version": "11.5.0"})
    _write_json(tmp_path / "vendor" / "composer" / "installed.json", {"packages": packages})
    from tools.testing import project_runners

    monkeypatch.setattr(project_runners.shutil, "which", lambda _name: "php-under-test")

    spec = _discover(tmp_path).runners[0]

    assert spec.version == "11.5.0"
    assert spec.complete


def test_duplicate_installed_composer_packages_are_denied(tmp_path: Path) -> None:
    _write_composer_package(tmp_path, installed=("phpunit/phpunit",))
    packages = [{"name": "phpunit/phpunit", "version": value} for value in ("11.5.0", "12.0.0")]
    _write_json(tmp_path / "vendor" / "composer" / "installed.json", {"packages": packages})

    with pytest.raises(ToolError) as error:
        _discover(tmp_path)

    assert error.value.code == "invalid_test_configuration"


@pytest.mark.parametrize("content", ['{"testMatch": [], "testMatch": []}', "[]", "not json"])
def test_jest_json_config_is_strict(tmp_path: Path, content: str) -> None:
    _write_node_package(tmp_path, dependencies={"jest": "^29"}, installed=("jest",))
    (tmp_path / "jest.config.json").write_text(content, encoding="utf-8")

    with pytest.raises(ToolError) as error:
        _discover(tmp_path)

    assert error.value.code == "invalid_test_configuration"


@pytest.mark.parametrize(
    "content", [b"MZ\x00\xff\xfe", b"x" * (256 * 1024 + 1), b"plain text"],
    ids=["binary", "oversized", "not-php"],
)
def test_php_entrypoint_requires_bounded_php_text(tmp_path: Path, content: bytes) -> None:
    _write_composer_package(tmp_path, installed=("phpunit/phpunit",))
    (tmp_path / "vendor" / "bin" / "phpunit").write_bytes(content)

    with pytest.raises(ToolError):
        _discover(tmp_path)


def test_config_read_limit_is_enforced(tmp_path: Path) -> None:
    _write_node_package(tmp_path, dependencies={"jest": "^29"}, installed=("jest",))
    (tmp_path / "jest.config.js").write_bytes(b"x" * (256 * 1024 + 1))

    with pytest.raises(ToolError):
        _discover(tmp_path)


def test_aggregate_read_limit_is_shared(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_node_package(tmp_path, dependencies={"jest": "^29"}, installed=("jest",))
    (tmp_path / "jest.config.js").write_bytes(b"x" * 256)
    from tools.testing import project_runners

    monkeypatch.setattr(project_runners, "_MAX_AGGREGATE_BYTES", 300)

    with pytest.raises(ToolError):
        _discover(tmp_path)


def test_package_count_is_bounded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_node_package(tmp_path, dependencies={"jest": "^29"}, installed=())
    _write_node_package(tmp_path / "nested", dependencies={"jest": "^29"}, installed=())
    from tools.testing import project_runners

    monkeypatch.setattr(project_runners, "_MAX_PACKAGES", 1)

    with pytest.raises(ToolError) as error:
        _discover(tmp_path)

    assert error.value.code == "inventory_limit"


def test_installed_package_limit_is_explicit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_composer_package(tmp_path, installed=("phpunit/phpunit",))
    _write_json(tmp_path / "vendor" / "composer" / "installed.json", {"packages": [
        {"name": "fixture/other", "version": "1.0.0"},
        {"name": "phpunit/phpunit", "version": "11.5.0"},
    ]})
    from tools.testing import project_runners

    monkeypatch.setattr(project_runners, "_MAX_INSTALLED_PACKAGES", 1)

    with pytest.raises(ToolError) as error:
        _discover(tmp_path)

    assert error.value.code == "inventory_limit"


def test_manifest_changed_after_inventory_is_rejected(tmp_path: Path) -> None:
    _write_node_package(tmp_path, dependencies={"jest": "^29"}, installed=())
    inventory = detect_project_types(tmp_path)
    _write_json(tmp_path / "package.json", {"devDependencies": {"vitest": "^3"}})
    from tools.testing.project_runners import discover_test_runners

    with pytest.raises(ToolError) as error:
        discover_test_runners(tmp_path, inventory)

    assert error.value.code == "invalid_project_manifest"


@pytest.mark.parametrize("section", ["dependencies", "optional-dependencies", "dependency-groups", "poetry"])
def test_python_dependencies_are_not_silently_truncated(tmp_path: Path, section: str) -> None:
    pytest.importorskip("tomllib")
    if section == "dependencies":
        document = '[project]\ndependencies = [' + ','.join(['"a"'] * 10_000 + ['"pytest>=8"']) + ']\n'
    else:
        header = {
            "optional-dependencies": "project.optional-dependencies",
            "dependency-groups": "dependency-groups",
            "poetry": "tool.poetry.dependencies",
        }[section]
        value = '"*"' if section == "poetry" else '[]'
        tail = 'pytest = ">=8"' if section == "poetry" else 'last = ["pytest>=8"]'
        document = f'[{header}]\n' + ''.join(f'a{i} = {value}\n' for i in range(10_000)) + tail + '\n'
    assert len(document.encode("utf-8")) <= 256 * 1024
    (tmp_path / "pyproject.toml").write_text(document, encoding="utf-8")

    inventory = _discover(tmp_path)

    assert inventory.complete
    assert len(inventory.runners) == 1
    assert inventory.runners[0].runner == "pytest"


@pytest.mark.parametrize("document", [
    '[project]\ndependencies = "pytest"\n',
    '[project.optional-dependencies]\ndev = "pytest"\n',
    '[dependency-groups]\ndev = 12\n',
    '[tool.poetry]\ndependencies = ["pytest"]\n',
    '[tool.poetry.group]\ndev = 12\n',
    '[project]\ndependencies = [12]\n',
])
def test_python_dependency_metadata_types_are_validated(tmp_path: Path, document: str) -> None:
    pytest.importorskip("tomllib")
    (tmp_path / "pyproject.toml").write_text(document, encoding="utf-8")

    with pytest.raises(ToolError) as error:
        _discover(tmp_path)

    assert error.value.code == "invalid_project_manifest"


@pytest.mark.parametrize("backend", ["python", "auto"])
def test_javascript_change_without_edges_requires_full_suite(tmp_path: Path, backend: str) -> None:
    _write_node_package(tmp_path, dependencies={"jest": "^29"}, installed=("jest",))
    (tmp_path / "app.js").write_text("export const value = 1;\n", encoding="utf-8")
    from tools.testing.impact import select_affected_tests

    result = select_affected_tests(tmp_path, changed_paths=["app.js"], test_backend=backend)  # type: ignore[arg-type]

    assert result["decision"] == "full_suite"
    assert result["unknown_coverage"]
    assert not result["complete"]
    assert result["missing_inputs"]
    if backend == "auto":
        selection = result["package_selections"][0]
        assert selection["runner"] == "jest"
        assert selection["package_root"] == "."
        assert selection["full_suite"]
        assert selection["fallback_reason"]


def test_deleted_php_file_selects_package_suite(tmp_path: Path) -> None:
    package = tmp_path / "plugin"
    _write_composer_package(package, installed=("phpunit/phpunit",))
    from tools.testing.impact import select_affected_tests

    result = select_affected_tests(tmp_path, changed_paths=["plugin/deleted.php"], test_backend="auto")

    selection = result["package_selections"][0]
    assert selection["package_root"] == "plugin"
    assert selection["runner"] == "phpunit"
    assert "changed_path:plugin/deleted.php" in selection["evidence"]
    assert selection["full_suite"]


def test_renamed_file_marks_old_and_new_packages(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("old", "new"):
        _write_node_package(tmp_path / name, dependencies={"jest": "^29"}, installed=("jest",))
    from tools.testing import impact

    monkeypatch.setattr(impact, "discover_changed_paths", lambda *_args, **_kwargs: impact.ChangedPaths(
        (impact.ChangedPath("new/app.js", "R100", "old/app.js"),), None,
    ))

    result = impact.select_affected_tests(tmp_path, test_backend="auto")

    assert {item["package_root"] for item in result["package_selections"]} == {"old", "new"}
    assert result["changed_paths"] == ["new/app.js"]
    assert set(result["touched_paths"]) == {"old/app.js", "new/app.js"}


def test_cross_package_dynamic_import_broadens_scope(tmp_path: Path) -> None:
    for name in ("consumer", "provider"):
        _write_node_package(tmp_path / name, dependencies={"vitest": "^3"}, installed=("vitest",))
    (tmp_path / "consumer" / "load.ts").write_text("export const load = (name: string) => import(name);\n", encoding="utf-8")
    from tools.testing.impact import select_affected_tests

    result = select_affected_tests(tmp_path, changed_paths=["consumer/load.ts"], test_backend="auto")

    assert {item["package_root"] for item in result["package_selections"]} == {"consumer", "provider"}
    assert "cross_package_dependencies_unknown" in result["missing_inputs"]
    assert all(item["full_suite"] for item in result["package_selections"])


def test_unknown_runner_never_returns_all_ok(tmp_path: Path) -> None:
    _write_json(tmp_path / "package.json", {"name": "unknown"})
    from tools.testing.impact import select_affected_tests

    result = select_affected_tests(tmp_path, changed_paths=["app.ts"], test_backend="auto")

    assert not result["complete"]
    assert result["decision"] == "full_suite"
    assert result["package_selections"][0]["runner"] == "unknown"
    assert "runner_required" in result["missing_inputs"]


def test_stale_snapshot_not_used(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_node_package(tmp_path, dependencies={"jest": "^29"}, installed=("jest",))
    source = tmp_path / "app.js"
    source.write_text("export const value = 1;\n", encoding="utf-8")
    from tools.testing import impact

    detect = detect_project_types
    calls = 0

    def raced_inventory(*args: Any, **kwargs: Any) -> Any:
        nonlocal calls
        inventory = detect(*args, **kwargs)
        calls += 1
        if calls == 1:
            source.write_text("export const value = 2;\n", encoding="utf-8")
        return inventory

    monkeypatch.setattr(impact, "detect_project_types", raced_inventory)

    result = impact.select_affected_tests(tmp_path, changed_paths=["app.js"], test_backend="auto")

    assert not result["complete"]
    assert result["source_fingerprint"] is None
    assert "stale_snapshot" in result["missing_inputs"]
    assert result["decision"] == "full_suite"


def test_doc_only_remains_none(tmp_path: Path) -> None:
    _write_node_package(tmp_path, dependencies={"jest": "^29"}, installed=())
    (tmp_path / "README.md").write_text("Documentation only.\n", encoding="utf-8")
    from tools.testing.impact import select_affected_tests

    result = select_affected_tests(tmp_path, changed_paths=["README.md"], test_backend="auto")

    assert result["decision"] == "none"
    assert result["complete"]
    assert not result["unknown_coverage"]
    assert not result["package_selections"]


@pytest.mark.parametrize("path", ["../escape.php", "..\\escape.php"])
def test_explicit_changed_path_outside_is_denied(tmp_path: Path, path: str) -> None:
    from tools.testing.impact import select_affected_tests

    with pytest.raises(ToolError) as error:
        select_affected_tests(tmp_path, changed_paths=[path], test_backend="auto")

    assert error.value.code == "project_scope_escape"


def test_package_selection_result_limit_is_global(tmp_path: Path) -> None:
    for name in ("a", "b"):
        _write_node_package(tmp_path / name, dependencies={"jest": "^29"}, installed=("jest",))
    from tools.testing.impact import select_affected_tests

    result = select_affected_tests(tmp_path, changed_paths=["a/app.js"], test_backend="auto", max_results=1)

    assert len(result["tests"]) + len(result["package_selections"]) == 1
    assert result["results_truncated"]
    assert not result["complete"]
    assert result["missing_inputs"]


def test_deleted_paths_are_in_source_fingerprint(tmp_path: Path) -> None:
    _write_node_package(tmp_path, dependencies={"jest": "^29"}, installed=())
    from tools.testing.impact import select_affected_tests

    first = select_affected_tests(tmp_path, changed_paths=["a.js"], test_backend="auto")
    second = select_affected_tests(tmp_path, changed_paths=["b.js"], test_backend="auto")

    assert first["source_fingerprint"] is not None
    assert first["source_fingerprint"] != second["source_fingerprint"]


def test_default_python_backend_reports_foreign_coverage_unknown(tmp_path: Path) -> None:
    from tools.testing.impact import select_affected_tests

    result = select_affected_tests(tmp_path, changed_paths=["app.php"])

    assert result["test_backend"] == "python"
    assert result["decision"] == "full_suite"
    assert result["unknown_coverage"]
    assert not result["complete"]


def test_php_xml_config_and_excluded_source_are_fingerprinted(tmp_path: Path) -> None:
    _write_composer_package(tmp_path, installed=("phpunit/phpunit",))
    config = tmp_path / "phpunit.xml"
    config.write_text('<phpunit name="first"/>', encoding="utf-8")
    source = tmp_path / ".worktrees" / "app.php"
    source.parent.mkdir()
    source.write_text("<?php $a = 1;\n", encoding="utf-8")
    from tools.testing.impact import select_affected_tests

    first = select_affected_tests(tmp_path, changed_paths=[".worktrees/app.php"], test_backend="auto")
    config.write_text('<phpunit name="other"/>', encoding="utf-8")
    second = select_affected_tests(tmp_path, changed_paths=[".worktrees/app.php"], test_backend="auto")
    source.write_text("<?php $a = 2;\n", encoding="utf-8")
    third = select_affected_tests(tmp_path, changed_paths=[".worktrees/app.php"], test_backend="auto")

    fingerprints = {item["source_fingerprint"] for item in (first, second, third)}
    assert None not in fingerprints
    assert len(fingerprints) == 3


def test_public_affected_tests_auto_is_read_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from dataclasses import replace

    from mcp import Client
    from mcp.server import MCPServer

    from core import audit
    from tools.testing import registry

    _write_node_package(tmp_path, dependencies={"jest": "^29"}, installed=("jest",))
    monkeypatch.setattr(audit, "SETTINGS", replace(audit.SETTINGS, state_dir=tmp_path / "audit"))
    server = MCPServer("readonly-selection")
    registry.register(server)

    def fail_spawn(*args: object, **kwargs: object) -> None:
        raise AssertionError("selection must never spawn project code or an LSP provider")

    monkeypatch.setattr(subprocess, "Popen", fail_spawn)

    async def run() -> None:
        async with Client(server) as client:
            result = await client.call_tool("affected_tests", {
                "repo": str(tmp_path), "changed_paths": ["app.js"], "test_backend": "auto",
            })
        assert isinstance(result.structured_content, dict)
        assert result.structured_content["decision"] == "full_suite"
        assert result.structured_content["package_selections"][0]["runner"] == "jest"

    asyncio.run(run())


def test_deleted_python_test_is_not_selected_as_existing_test(tmp_path: Path) -> None:
    from tools.testing.impact import select_affected_tests

    result = select_affected_tests(tmp_path, changed_paths=["tests/test_deleted.py"])

    assert result["tests"] == []
    assert result["decision"] == "full_suite"
    assert "deleted_test_changed" in result["full_suite_reasons"]


def test_root_python_config_broadens_auto_to_all_packages(tmp_path: Path) -> None:
    _write_python_package(tmp_path)
    _write_node_package(tmp_path / "web", dependencies={"jest": "^29"}, installed=("jest",))
    from tools.testing.impact import select_affected_tests

    result = select_affected_tests(tmp_path, changed_paths=["pytest.ini"], test_backend="auto")

    assert {item["package_root"] for item in result["package_selections"]} == {".", "web"}
    assert all(item["full_suite"] for item in result["package_selections"])


def test_reverse_selected_python_test_owner_is_in_package_plan(tmp_path: Path) -> None:
    for name in ("a", "b"):
        _write_python_package(tmp_path / name, name=name)
    (tmp_path / "a" / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    (tmp_path / "b" / "tests" / "test_other.py").write_text("from a import app\n", encoding="utf-8")
    from tools.testing.impact import select_affected_tests

    result = select_affected_tests(tmp_path, changed_paths=["a/app.py"], test_backend="auto")

    assert "b/tests/test_other.py" in {row["path"] for row in result["tests"]}
    assert "b" in {item["package_root"] for item in result["package_selections"]}


def test_dependency_depth_cutoff_reports_unknown_coverage(tmp_path: Path) -> None:
    (tmp_path / "tests").mkdir()
    (tmp_path / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    (tmp_path / "middle.py").write_text("import app\n", encoding="utf-8")
    (tmp_path / "tests" / "test_direct.py").write_text("import app\n", encoding="utf-8")
    (tmp_path / "tests" / "test_deep.py").write_text("import middle\n", encoding="utf-8")
    from tools.testing.impact import select_affected_tests

    result = select_affected_tests(tmp_path, changed_paths=["app.py"], max_depth=1)

    assert not result["complete"]
    assert result["unknown_coverage"]
    assert "dependency_depth_truncated" in result["missing_inputs"]
    assert result["decision"] == "full_suite"


def test_nested_installed_php_metadata_is_in_fingerprint(tmp_path: Path) -> None:
    _write_composer_package(tmp_path / "plugin", installed=("phpunit/phpunit",))
    from tools.testing.impact import select_affected_tests

    first = select_affected_tests(tmp_path, changed_paths=["plugin/app.php"], test_backend="auto")
    _write_json(tmp_path / "plugin" / "vendor" / "composer" / "installed.json", {"packages": [
        {"name": "phpunit/phpunit", "version": "12.0.0"},
    ]})
    second = select_affected_tests(tmp_path, changed_paths=["plugin/app.php"], test_backend="auto")

    assert first["source_fingerprint"] is not None
    assert second["source_fingerprint"] is not None
    assert first["source_fingerprint"] != second["source_fingerprint"]


@pytest.mark.parametrize("without_toml", [False, True], ids=["host-default", "without-toml"])
def test_stale_python_snapshot_invalidates_package_scope(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, without_toml: bool,
) -> None:
    from tools.project import project_types
    from tools.testing import project_runners

    if without_toml:
        monkeypatch.setattr(project_types, "_toml", None)
        monkeypatch.setattr(project_runners, "_toml", None)
    for name in ("a", "b"):
        _write_python_package(tmp_path / name, name=name)
    (tmp_path / "a" / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    (tmp_path / "a" / "tests" / "test_direct.py").write_text("from a import app\n", encoding="utf-8")
    other_test = tmp_path / "b" / "tests" / "test_new.py"
    other_test.write_text("VALUE = 1\n", encoding="utf-8")
    from tools.testing import impact

    calls = 0

    def raced_inventory(*args: Any, **kwargs: Any) -> Any:
        nonlocal calls
        calls += 1
        if calls == 2:
            other_test.write_text("from a import app\n", encoding="utf-8")
        return detect_project_types(*args, **kwargs)

    monkeypatch.setattr(impact, "detect_project_types", raced_inventory)

    result = impact.select_affected_tests(tmp_path, changed_paths=["a/app.py"], test_backend="auto")

    reason = "source_fingerprint_unavailable" if project_types._toml is None else "stale_snapshot"
    assert reason in result["missing_inputs"]
    if project_types._toml is None:
        assert "toml_parser_unavailable" in result["missing_inputs"]
    assert not result["complete"]
    assert result["source_fingerprint"] is None
    assert {"a", "b"} <= {item["package_root"] for item in result["package_selections"]}
    assert all(item["full_suite"] for item in result["package_selections"])


def test_cross_package_fallback_includes_manifestless_root_runner(tmp_path: Path) -> None:
    (tmp_path / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
    _write_node_package(tmp_path / "web", dependencies={"jest": "^29"}, installed=("jest",))
    from tools.testing.impact import select_affected_tests

    result = select_affected_tests(tmp_path, changed_paths=["web/app.js"], test_backend="auto")

    assert {(".", "pytest"), ("web", "jest")} <= {
        (item["package_root"], item["runner"]) for item in result["package_selections"]
    }
