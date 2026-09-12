from __future__ import annotations

import ast
import json
import sys
import time
from pathlib import Path

import pytest

from core.errors import ToolError
from core.executor import (
    SPOOL_MEMORY_BYTES,
    OutputCapture,
    background_output,
    close_background_captures,
    run_bounded,
    start_background,
    terminate_process_tree,
)
from core.response import bounded_text, failure
from core.timings import timing_span, tool_timing
from tools.browser.registry import _url_result
from tools.desktop.native import virtual_key
from tools.filesystem.registry import RefactorEdit
from tools.filesystem.service import (
    anchor_replace,
    apply_safe_refactor,
    diff_summary,
    read_window,
    replace_symbol_body,
)
from tools.testing.registry import _junit_summary, _pytest_counts


def test_timing_is_opt_in_and_does_not_create_a_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    target = tmp_path / "timings.jsonl"
    monkeypatch.delenv("MCP_TIMINGS", raising=False)
    monkeypatch.setenv("MCP_TIMINGS_FILE", str(target))
    with tool_timing("probe"):
        with timing_span("inner"):
            pass
    assert target.exists() is False


def test_timing_records_tool_context_phase_and_failures(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    target = tmp_path / "timings.jsonl"
    monkeypatch.setenv("MCP_TIMINGS", "1")
    monkeypatch.setenv("MCP_TIMINGS_FILE", str(target))
    with tool_timing("probe"):
        with timing_span("inner", metadata={"items": 2}):
            pass
    with pytest.raises(RuntimeError):
        with tool_timing("failed_probe"):
            raise RuntimeError("expected")
    records = [json.loads(line) for line in target.read_text(encoding="utf-8").splitlines()]
    assert any(record["phase"] == "inner" and record["tool"] == "probe" and record["items"] == 2 for record in records)
    assert any(record["phase"] == "tool_body" and record["tool"] == "probe" and record["ok"] is True for record in records)
    assert any(record["phase"] == "tool_body" and record["tool"] == "failed_probe" and record["ok"] is False for record in records)
    assert all(record["duration_ms"] >= 0 for record in records)


def test_limited_capture_both_mode_preserves_small_output() -> None:
    capture = OutputCapture(100, "both")
    capture.feed(b"a" * 30)
    capture.feed(b"b" * 40)
    result = capture.result("utf-8")
    assert result["text"] == "a" * 30 + "b" * 40
    assert result["truncated"] is False


def test_limited_capture_both_mode_includes_marker_inside_limit() -> None:
    capture = OutputCapture(40, "both")
    capture.feed(b"a" * 200)
    result = capture.result("utf-8")
    assert result["truncated"] is True
    assert result["bytes"] == 40
    assert len(result["text"]) == 40
    assert len(bounded_text("x" * 200, 40, "both")["text"]) == 40


def test_unlimited_capture_spools_to_disk_and_returns_every_byte() -> None:
    capture = OutputCapture(None, "tail")
    payload = b"z" * (SPOOL_MEMORY_BYTES + 123)
    capture.feed(payload[:500_000])
    capture.feed(payload[500_000:])
    assert capture.spooled_to_disk is True
    result = capture.result("utf-8")
    assert {key: result[key] for key in ("text", "bytes", "total_bytes", "truncated")} == {
        "text": payload.decode(),
        "bytes": len(payload),
        "total_bytes": len(payload),
        "truncated": False,
    }
    capture.close()
    assert capture.closed is True


def test_zero_capture_discards_output_but_reports_total() -> None:
    capture = OutputCapture(0, "tail")
    capture.feed(b"discarded")
    result = capture.result("utf-8")
    assert {key: result[key] for key in ("text", "bytes", "total_bytes", "truncated")} == {
        "text": "", "bytes": 0, "total_bytes": 9, "truncated": True,
    }
    capture.close()


def test_unlimited_text_and_error_helpers_do_not_slice() -> None:
    text = "x" * 5_000
    assert bounded_text(text, None)["text"] == text
    assert failure("probe", RuntimeError(text))["message"] == text
    url = "https://example.test/" + "a" * 5_000
    assert _url_result(url) == {"url": url, "url_truncated": False}


def test_bounded_process_timeout_kills_child(tmp_path: Path) -> None:
    result = run_bounded(
        [sys.executable, "-c", "import time; time.sleep(10)"],
        cwd=tmp_path,
        timeout_sec=0.2,
        stdout_limit=100,
        stderr_limit=100,
    )
    assert result["timed_out"] is True
    assert result["ok"] is False


def test_process_unlimited_stdout_stderr_and_utf8(tmp_path: Path) -> None:
    stdout_text = "x" * 600_000 + "ژ"
    stderr_text = "y" * 600_000 + "ش"
    script = (
        "import sys;"
        "sys.stdout.buffer.write(('x'*600000+'ژ').encode('utf-8'));"
        "sys.stderr.buffer.write(('y'*600000+'ش').encode('utf-8'))"
    )
    result = run_bounded(
        [sys.executable, "-c", script],
        cwd=tmp_path,
        timeout_sec=10,
        encoding="utf-8",
    )
    assert result["stdout"]["text"] == stdout_text
    assert result["stderr"]["text"] == stderr_text
    assert result["stdout"]["truncated"] is False
    assert result["stderr"]["truncated"] is False


def test_background_output_is_available_while_process_runs(tmp_path: Path) -> None:
    started = start_background(
        [sys.executable, "-u", "-c", "import time; print('READY'); time.sleep(30)"],
        cwd=tmp_path,
        capture_limit=100,
        output_mode="tail",
    )
    pid = started["pid"]
    try:
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            if "READY" in background_output(pid)["stdout"]["text"]:
                break
            time.sleep(0.05)
        else:
            pytest.fail("Background stdout was not available before process exit")
    finally:
        terminate_process_tree(pid, force=True)
        close_background_captures()


def test_background_output_is_unlimited_by_default(tmp_path: Path) -> None:
    expected = "b" * 600_000
    started = start_background(
        [sys.executable, "-u", "-c", "import sys; sys.stdout.write('b'*600000)"],
        cwd=tmp_path,
        output_mode="tail",
    )
    pid = started["pid"]
    try:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            result = background_output(pid)
            if not result["running"] and result["stdout"]["total_bytes"] == len(expected):
                break
            time.sleep(0.05)
        else:
            pytest.fail("Background process did not finish with complete output")
        assert result["stdout"]["text"] == expected
        assert result["stdout"]["truncated"] is False
    finally:
        try:
            terminate_process_tree(pid, force=True)
        except ToolError as exc:
            assert exc.code == "process_not_found"
        close_background_captures()
    with pytest.raises(ToolError) as caught:
        background_output(pid)
    assert caught.value.code == "output_unavailable"


def test_read_window_uses_lines_offsets_and_limits(tmp_path: Path) -> None:
    target = tmp_path / "lines.txt"
    target.write_text("one\ntwo\nthree\nfour\n", encoding="utf-8")
    result = read_window(
        str(target),
        start_line=2,
        end_line=4,
        offset=1,
        max_chars=8,
        encoding="auto",
    )
    assert result["content"].replace("\r\n", "\n") == "wo\nthre"
    assert len(result["content"]) == 8
    assert result["truncated"] is True


def test_read_window_streams_across_a_very_long_line(tmp_path: Path) -> None:
    target = tmp_path / "long-line.txt"
    target.write_text("a" * 70_000 + "\nend\n", encoding="utf-8", newline="")
    first = read_window(str(target), start_line=1, end_line=1, offset=65_530, max_chars=20, encoding="auto")
    second = read_window(str(target), start_line=2, end_line=2, offset=0, max_chars=20, encoding="auto")
    assert first["content"] == "a" * 20
    assert first["truncated"] is True
    assert second["content"] == "end\n"
    assert second["truncated"] is False


def test_read_window_is_unlimited_by_default(tmp_path: Path) -> None:
    target = tmp_path / "large.txt"
    expected = "a" * 300_000 + "\nend\n"
    target.write_text(expected, encoding="utf-8", newline="")
    result = read_window(str(target), start_line=1, end_line=None, offset=0, max_chars=None, encoding="auto")
    assert result["content"] == expected
    assert result["chars"] == len(expected)
    assert result["truncated"] is False
    assert result["next_offset"] is None


def test_diff_summary_is_complete_by_default(tmp_path: Path) -> None:
    before = "".join(f"old-{index}\n" for index in range(2_000))
    after = "".join(f"new-{index}\n" for index in range(2_000))
    result = diff_summary(before, after, tmp_path / "large.txt")
    assert len(result["preview"]) > 4_000
    assert result["preview_truncated"] is False


def test_anchor_and_ast_body_replacements_remain_parseable() -> None:
    anchored = anchor_replace("A<start>old<end>Z", "<start>", "<end>", "new", False)
    assert anchored == "A<start>new<end>Z"
    source = "class Example:\n    def value(self):\n        return 1\n"
    changed = replace_symbol_body(source, "Example.value", "return 2", "function")
    ast.parse(changed)
    assert "return 2" in changed
    assert "return 1" not in changed


def test_safe_refactor_rolls_back_failed_validation(tmp_path: Path) -> None:
    target = tmp_path / "module.py"
    original = "VALUE = 1\n"
    target.write_text(original, encoding="utf-8")
    edits = [RefactorEdit(mode="exact", old="1", new="2")]
    with pytest.raises(ToolError) as caught:
        apply_safe_refactor(
            str(target),
            edits,
            encoding="auto",
            validation_command=[sys.executable, "-c", "raise SystemExit(7)"],
            validation_cwd=str(tmp_path),
            timeout_sec=10,
        )
    assert caught.value.code == "validation_failed_rolled_back"
    assert target.read_text(encoding="utf-8") == original


def test_pytest_summary_parser_and_hotkey_mapping() -> None:
    counts = _pytest_counts("3 passed, 2 failed, 1 skipped in 0.12s")
    assert counts["passed"] == 3
    assert counts["failed"] == 2
    assert counts["skipped"] == 1
    assert virtual_key("ctrl") == 0x11
    assert virtual_key("F12") == 0x7B


def test_junit_summary_provides_exact_counts(tmp_path: Path) -> None:
    report = tmp_path / "report.xml"
    report.write_text(
        '<testsuites><testsuite tests="3" failures="1" errors="0" skipped="1">'
        '<testcase classname="tests.sample" name="passed" />'
        '<testcase classname="tests.sample" name="failed"><failure /></testcase>'
        '<testcase classname="tests.sample" name="skipped"><skipped /></testcase>'
        "</testsuite></testsuites>",
        encoding="utf-8",
    )
    result = _junit_summary(report)
    assert result is not None
    counts, failures = result
    assert counts == {"passed": 1, "failed": 1, "skipped": 1, "errors": 0}
    assert failures == ["tests.sample::failed"]
