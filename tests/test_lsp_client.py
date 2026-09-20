from __future__ import annotations

import sys
from pathlib import Path

import pytest

from core.errors import ToolError
from tools.language.lsp import LspClient, position_to_offset, uri_to_path

FIXTURE = Path(__file__).parent / "fixtures" / "fake_lsp_server.py"
STRICT_FIXTURE = Path(__file__).parent / "fixtures" / "strict_lsp_server.py"
OVERSIZE_FIXTURE = Path(__file__).parent / "fixtures" / "oversize_lsp_server.py"


def test_utf16_position_counts_astral_characters_as_two_units() -> None:
    assert position_to_offset("a😀b\n", {"line": 0, "character": 3}) == 2


def test_uri_must_stay_inside_workspace(tmp_path: Path) -> None:
    inside = tmp_path / "a.py"
    assert uri_to_path(inside.as_uri(), tmp_path) == inside
    with pytest.raises(ToolError, match="outside"):
        uri_to_path((tmp_path.parent / "outside.py").as_uri(), tmp_path)


def test_lsp_client_performs_initialize_request_and_shutdown(tmp_path: Path) -> None:
    client = LspClient([sys.executable, str(FIXTURE)], tmp_path, timeout_sec=5)
    response = client.request("workspace/symbol", {"query": "Service"})
    assert response == {"method": "workspace/symbol", "params": {"query": "Service"}}
    assert client.capabilities["definitionProvider"] is True


def test_lsp_client_follows_strict_handshake_and_opens_text_document(tmp_path: Path) -> None:
    target = tmp_path / "service.py"
    target.write_text("value = 1\n", encoding="utf-8")
    client = LspClient([sys.executable, str(STRICT_FIXTURE)], tmp_path, timeout_sec=5)

    response = client.request(
        "textDocument/hover",
        {
            "textDocument": {"uri": target.as_uri()},
            "position": {"line": 0, "character": 1},
        },
    )

    assert response == {
        "method": "textDocument/hover",
        "initialized": True,
        "opened_count": 1,
    }
    assert client.capabilities["hoverProvider"] is True


def test_lsp_client_rejects_oversized_response_before_body_is_buffered(tmp_path: Path) -> None:
    started = __import__("time").monotonic()
    with pytest.raises(ToolError, match="message limit"):
        LspClient(
            [sys.executable, str(OVERSIZE_FIXTURE)],
            tmp_path,
            timeout_sec=2,
            max_message_bytes=128,
            max_total_bytes=1_024,
        ).request("workspace/symbol", {"query": "x"})
    assert __import__("time").monotonic() - started < 1.5


def test_lsp_client_rejects_missing_executable(tmp_path: Path) -> None:
    with pytest.raises(ToolError, match="not found"):
        LspClient(["definitely-missing-lsp-executable"], tmp_path).request("workspace/symbol", {"query": "x"})


def test_lsp_client_bounds_timeout_and_malformed_output(tmp_path: Path) -> None:
    with pytest.raises(ToolError, match="timeout"):
        LspClient([sys.executable, "-c", "import time; time.sleep(2)"], tmp_path, timeout_sec=0.05).request(
            "workspace/symbol", {"query": "x"}
        )
    with pytest.raises(ToolError, match="malformed"):
        LspClient([sys.executable, "-c", "import sys; sys.stdout.buffer.write(b'bad'); sys.stdout.flush()"], tmp_path).request(
            "workspace/symbol", {"query": "x"}
        )
