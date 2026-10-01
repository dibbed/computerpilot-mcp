from __future__ import annotations

import asyncio
import hashlib
import importlib
from pathlib import Path

import pytest
from pypdf import PdfReader, PdfWriter
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

from core.errors import ToolError
from core.registry import create_server
from core.tooling import MUTATING_TOOL_OPERATIONS


def _pdf(path: Path) -> None:
    document = canvas.Canvas(str(path), pagesize=A4, invariant=1)
    document.setTitle("Sample")
    document.setAuthor("ComputerPilot Test")
    document.drawString(72, 760, "First page text")
    document.showPage()
    document.drawString(72, 760, "Second page text")
    document.save()


def _encrypted_pdf(path: Path) -> None:
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    writer.add_metadata({"/Title": "Secret"})
    writer.encrypt("secret")
    with path.open("wb") as handle:
        writer.write(handle)


def test_pdf_inspect_metadata_and_page_text_are_structured(tmp_path: Path) -> None:
    pdf = importlib.import_module("tools.documents.pdf")
    path = tmp_path / "sample.pdf"
    _pdf(path)

    inspected = pdf.inspect_pdf(path)
    assert inspected["page_count"] == 2
    assert inspected["encrypted"] is False
    assert inspected["metadata"]["title"] == "Sample"
    assert inspected["media_type"] == "application/pdf"
    assert inspected["artifact"]["path"] == str(path)

    metadata = pdf.pdf_metadata(path)
    assert metadata["metadata"]["author"] == "ComputerPilot Test"

    page = pdf.page_text(path, page_number=1, max_chars=100)
    assert page["text"].strip() == "First page text"
    assert page["page_number"] == 1
    assert page["truncated"] is False


def test_pdf_extract_text_is_bounded_and_can_publish_text_artifact(tmp_path: Path) -> None:
    pdf = importlib.import_module("tools.documents.pdf")
    path = tmp_path / "extract.pdf"
    _pdf(path)

    bounded = pdf.extract_text(
        path,
        start_page=1,
        end_page=2,
        max_pages=10,
        max_chars=12,
        delivery="inline",
    )
    assert bounded["text"] == "First page t"
    assert bounded["truncated"] is True
    assert bounded["pages_processed"] == 1
    assert bounded["chars"] == 12

    delivered = pdf.extract_text(
        path,
        start_page=1,
        end_page=2,
        max_pages=10,
        max_chars=1000,
        delivery="file",
    )
    output = delivered["output"]
    assert output["delivery"] == "file"
    artifact = Path(output["path"])
    assert artifact.is_file()
    assert "First page text" in artifact.read_text(encoding="utf-8")
    assert "Second page text" in artifact.read_text(encoding="utf-8")


def test_pdf_encrypted_and_invalid_inputs_fail_clearly(tmp_path: Path) -> None:
    pdf = importlib.import_module("tools.documents.pdf")

    encrypted = tmp_path / "encrypted.pdf"
    _encrypted_pdf(encrypted)
    inspected = pdf.inspect_pdf(encrypted)
    assert inspected["encrypted"] is True

    with pytest.raises(ToolError) as encrypted_error:
        pdf.extract_text(encrypted)
    assert encrypted_error.value.code == "encrypted_pdf"

    invalid = tmp_path / "invalid.pdf"
    invalid.write_bytes(b"not a pdf")
    with pytest.raises(ToolError) as invalid_error:
        pdf.inspect_pdf(invalid)
    assert invalid_error.value.code == "invalid_pdf"


def test_pdf_text_creation_is_deterministic_atomic_and_recoverable(tmp_path: Path) -> None:
    pdf = importlib.import_module("tools.documents.pdf")
    first = tmp_path / "first.pdf"
    second = tmp_path / "second.pdf"
    text = "Alpha\nBeta\nGamma"

    created = pdf.create_from_text(first, text=text, title="Created")
    duplicate = pdf.create_from_text(second, text=text, title="Created")

    assert created["created"] is True
    assert created["artifact"]["media_type"] == "application/pdf"
    assert created["postcondition"]["kind"] == "file_sha256"
    assert created["sha256"] == duplicate["sha256"]
    assert hashlib.sha256(first.read_bytes()).hexdigest() == created["sha256"]

    reader = PdfReader(str(first))
    assert reader.metadata is not None
    assert reader.metadata.title == "Created"
    assert "Alpha" in (reader.pages[0].extract_text() or "")

    with pytest.raises(FileExistsError):
        pdf.create_from_text(first, text="overwrite refused")


def test_pdf_markdown_creation_supports_explicit_subset(tmp_path: Path) -> None:
    pdf = importlib.import_module("tools.documents.pdf")
    path = tmp_path / "markdown.pdf"
    markdown = "# Heading\n\n- first item\n- second item\n\n```\nprint('x')\n```"

    created = pdf.create_from_markdown(path, markdown=markdown, title="Markdown")
    assert created["supported_markdown"] == ["headings", "unordered_lists", "fenced_code", "paragraphs"]

    reader = PdfReader(str(path))
    text = "\n".join((page.extract_text() or "") for page in reader.pages)
    assert "Heading" in text
    assert "first item" in text
    assert "print('x')" in text


def test_pdf_tools_are_registered_and_creation_is_recovery_guarded() -> None:
    names = {tool.name for tool in asyncio.run(create_server().list_tools())}
    assert {
        "pdf_inspect",
        "pdf_extract_text",
        "pdf_page_text",
        "pdf_metadata",
        "pdf_create_from_markdown",
        "pdf_create_from_text",
    } <= names
    assert {
        "pdf_create_from_markdown",
        "pdf_create_from_text",
    } <= MUTATING_TOOL_OPERATIONS
