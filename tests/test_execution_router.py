from __future__ import annotations

import json
from typing import TYPE_CHECKING

from core.execution_models import ExecutionIntent
from core.platform import PlatformCapabilities

if TYPE_CHECKING:
    from core.execution_router import ExecutionRouter


def _windows_caps() -> PlatformCapabilities:
    return PlatformCapabilities(
        system="windows",
        architecture="amd64",
        process_tree_ownership=True,
        desktop_screenshot=True,
        desktop_input=True,
        semantic_ui=True,
        system_services=True,
        installed_software=True,
        powershell=True,
        posix_shell=False,
        secure_tunnel=True,
        browser=True,
    )


def _linux_caps() -> PlatformCapabilities:
    return PlatformCapabilities(
        system="linux",
        architecture="amd64",
        process_tree_ownership=True,
        desktop_screenshot=False,
        desktop_input=False,
        semantic_ui=False,
        system_services=True,
        installed_software=True,
        powershell=False,
        posix_shell=True,
        secure_tunnel=True,
        browser=True,
    )


def _router(registered: set[str], *, caps: PlatformCapabilities | None = None) -> ExecutionRouter:
    from core.execution_router import ExecutionRouter
    from core.router_metrics import RouterMetrics

    return ExecutionRouter(
        registered_tools=registered,
        capabilities=caps or _windows_caps(),
        metrics=RouterMetrics(max_recent=8),
    )


def test_native_excel_is_preferred_over_semantic_and_raw_paths() -> None:
    router = _router({"excel_write_range", "ui_set_value", "keyboard_type"})
    result = router.recommend(ExecutionIntent("document.excel.write", allow_raw_desktop=True))

    assert result["selected_route"] == "native.excel"
    assert result["representative_tool"] == "excel_write_range"
    assert [item["route"] for item in result["candidates"]] == [
        "native.excel",
        "semantic.windows_uia",
        "raw.desktop",
    ]


def test_native_code_and_filesystem_beat_ui_fallbacks() -> None:
    router = _router({"rename_symbol", "ui_set_value", "keyboard_type", "read_file", "ui_invoke", "mouse_click"})

    rename = router.recommend(ExecutionIntent("code.rename", allow_raw_desktop=True))
    read = router.recommend(ExecutionIntent("filesystem.read", allow_raw_desktop=True))

    assert rename["selected_route"] == "native.code"
    assert rename["representative_tool"] == "rename_symbol"
    assert read["selected_route"] == "native.filesystem"
    assert read["representative_tool"] == "read_file"


def test_semantic_browser_beats_raw_desktop_when_both_are_available() -> None:
    router = _router({"browser_click_semantic", "mouse_click"})
    result = router.recommend(ExecutionIntent("browser.interact", allow_raw_desktop=True))

    assert result["selected_route"] == "semantic.browser"
    assert result["representative_tool"] == "browser_click_semantic"


def test_unregistered_and_platform_invalid_candidates_are_rejected() -> None:
    router = _router({"read_file", "ui_invoke", "mouse_click"}, caps=_linux_caps())
    result = router.candidates(ExecutionIntent("filesystem.read", allow_raw_desktop=True))

    assert result["valid_candidate_count"] == 1
    by_route = {item["route"]: item for item in result["items"]}
    assert by_route["native.filesystem"]["supported"] is True
    assert by_route["semantic.windows_uia"]["supported"] is False
    assert by_route["semantic.windows_uia"]["rejection_code"] == "capability_unavailable"
    assert by_route["raw.desktop"]["supported"] is False
    assert by_route["raw.desktop"]["rejection_code"] == "capability_unavailable"


def test_candidate_generation_is_bounded_and_reports_truncation() -> None:
    router = _router({"excel_write_range", "ui_set_value", "keyboard_type"})
    result = router.candidates(
        ExecutionIntent("document.excel.write", allow_raw_desktop=True),
        max_candidates=2,
    )

    assert result["count"] == 2
    assert result["total_candidates"] == 3
    assert result["truncated"] is True


def test_identical_inputs_have_stable_decision_fields() -> None:
    router = _router({"excel_write_range", "ui_set_value", "keyboard_type"})
    intent = ExecutionIntent("document.excel.write", allow_raw_desktop=True)

    first = router.recommend(intent)
    second = router.recommend(intent)

    assert first == second
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)


def test_router_metrics_are_bounded_and_classify_route_families() -> None:
    from core.router_metrics import RouterMetrics

    metrics = RouterMetrics(max_recent=2)
    router = _router({"read_file", "browser_click_semantic", "mouse_click"})
    router.metrics = metrics

    router.recommend(ExecutionIntent("filesystem.read"))
    router.recommend(ExecutionIntent("browser.interact", allow_raw_desktop=True))
    router.recommend(ExecutionIntent("filesystem.read"))

    snapshot = metrics.snapshot()
    assert snapshot["decision_count"] == 3
    assert snapshot["usage"]["native"] == 2
    assert snapshot["usage"]["semantic"] == 1
    assert snapshot["usage"]["raw"] == 0
    assert len(snapshot["recent_decisions"]) == 2
    assert set(snapshot["route_distribution"]) == {"native.filesystem", "semantic.browser"}
