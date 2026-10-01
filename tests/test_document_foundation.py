from __future__ import annotations

import hashlib
import importlib
import importlib.util
from pathlib import Path

from core import registry
from core.tool_profiles import ALL_DOMAINS, PROFILE_DOMAINS


def test_documents_domain_is_registered_as_portable_profile() -> None:
    assert "documents" in ALL_DOMAINS
    assert PROFILE_DOMAINS["documents"] == ("filesystem", "documents", "recovery", "workflows")
    assert "documents" in registry.REGISTRARS


def test_document_binary_foundation_module_exists() -> None:
    assert importlib.util.find_spec("tools.documents.common") is not None


def test_publish_document_is_atomic_recoverable_and_returns_artifact(tmp_path: Path) -> None:
    common = importlib.import_module("tools.documents.common")
    publish_document = getattr(common, "publish_document", None)
    assert callable(publish_document)

    target = tmp_path / "book.xlsx"
    target.write_bytes(b"before")

    def writer(staged: Path) -> None:
        staged.write_bytes(b"after")

    def validator(staged: Path) -> None:
        assert staged.read_bytes() == b"after"

    result = publish_document(
        target,
        writer=writer,
        validator=validator,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        backup=True,
    )

    digest = hashlib.sha256(b"after").hexdigest()
    assert target.read_bytes() == b"after"
    assert result["changed"] is True
    assert Path(result["backup"]).read_bytes() == b"before"
    assert result["artifact"] == {
        "path": str(target),
        "sha256": digest,
        "bytes": 5,
        "media_type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    }
    assert result["postcondition"] == {
        "kind": "file_sha256",
        "expected": {"path": str(target), "sha256": digest},
    }
