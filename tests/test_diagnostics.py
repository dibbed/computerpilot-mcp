from __future__ import annotations

from tools.testing.diagnostics import merge_diagnostics, normalize_diagnostic


def test_normalizes_lsp_severity_and_range() -> None:
    result = normalize_diagnostic(
        {"file": "C:\\repo\\a.py", "line": 2, "column": 4, "end_line": 2, "end_column": 8, "severity": 2, "message": "warn", "code": 7},
        source="lsp",
    )
    assert result == {
        "file": "C:\\repo\\a.py",
        "line": 2,
        "column": 4,
        "end_line": 2,
        "end_column": 8,
        "severity": "warning",
        "message": "warn",
        "code": "7",
        "source": "lsp",
    }


def test_merge_is_deduplicated_stable_and_capped() -> None:
    duplicate = {"file": "b.py", "line": 3, "severity": "error", "message": "bad", "source": "ruff"}
    diagnostics, truncated = merge_diagnostics(
        [[duplicate], [duplicate, {"file": "a.py", "line": 1, "severity": "note", "message": "first", "source": "mypy"}]], cap=1
    )
    assert diagnostics[0]["file"] == "a.py"
    assert truncated is True
