"""Structured PDF inspection, bounded extraction, and deterministic creation."""

from __future__ import annotations

import textwrap
from pathlib import Path
from typing import Any, Literal

from pypdf import PdfReader
from reportlab.lib.pagesizes import A4, LETTER
from reportlab.pdfgen import canvas

from core.artifacts import Delivery, deliver_text
from core.config import resolve_path
from core.errors import ToolError
from tools.documents.common import artifact_descriptor, publish_document

PDF_MEDIA_TYPE = "application/pdf"
_MAX_PDF_BYTES = 512 * 1024 * 1024
_MAX_EXTRACT_CHARS = 2_000_000
_MAX_CREATE_CHARS = 2_000_000
_PAGE_SIZES = {"A4": A4, "LETTER": LETTER}
PageSize = Literal["A4", "LETTER"]


def _pdf_target(path: str | Path, *, must_exist: bool) -> Path:
    target = resolve_path(path)
    if target.suffix.casefold() != ".pdf":
        raise ToolError("unsupported_pdf_format", "PDF tools support .pdf files only.")
    if must_exist and not target.is_file():
        raise FileNotFoundError(f"File not found: {target}")
    if target.exists() and not target.is_file():
        raise ToolError("not_a_file", f"Target is not a regular file: {target}")
    if must_exist:
        size = target.stat().st_size
        if size > _MAX_PDF_BYTES:
            raise ToolError("pdf_too_large", f"PDF file is {size} bytes; limit is {_MAX_PDF_BYTES}.")
    return target


def _reader(path: str | Path, *, require_unencrypted: bool = False) -> tuple[Path, PdfReader]:
    target = _pdf_target(path, must_exist=True)
    try:
        reader = PdfReader(str(target), strict=False)
    except Exception as exc:
        raise ToolError("invalid_pdf", f"PDF could not be opened: {target}") from exc
    if require_unencrypted and reader.is_encrypted:
        raise ToolError(
            "encrypted_pdf",
            "Encrypted PDFs are not supported for text extraction.",
            hint="Decrypt the PDF with an authorized tool and retry with the decrypted file.",
        )
    return target, reader


def _metadata(reader: PdfReader) -> dict[str, Any]:
    if reader.is_encrypted:
        return {
            "title": None,
            "author": None,
            "subject": None,
            "creator": None,
            "producer": None,
            "creation_date": None,
            "modification_date": None,
        }
    try:
        info = reader.metadata
    except Exception:
        info = None
    if info is None:
        return {
            "title": None,
            "author": None,
            "subject": None,
            "creator": None,
            "producer": None,
            "creation_date": None,
            "modification_date": None,
        }

    def value(name: str) -> Any:
        try:
            item = getattr(info, name, None)
        except Exception:
            return None
        if item is None:
            return None
        if hasattr(item, "isoformat"):
            try:
                return item.isoformat()
            except (TypeError, ValueError):
                return str(item)
        return str(item)

    return {
        "title": value("title"),
        "author": value("author"),
        "subject": value("subject"),
        "creator": value("creator"),
        "producer": value("producer"),
        "creation_date": value("creation_date"),
        "modification_date": value("modification_date"),
    }


def _page_count(reader: PdfReader) -> int | None:
    try:
        return len(reader.pages)
    except Exception:
        return None


def inspect_pdf(path: str | Path) -> dict[str, Any]:
    target, reader = _reader(path)
    descriptor = artifact_descriptor(target, PDF_MEDIA_TYPE)
    return {
        "ok": True,
        "path": str(target),
        "bytes": descriptor["bytes"],
        "sha256": descriptor["sha256"],
        "media_type": PDF_MEDIA_TYPE,
        "artifact": descriptor,
        "page_count": _page_count(reader),
        "encrypted": bool(reader.is_encrypted),
        "metadata": _metadata(reader),
        "unsupported_mutations": ["arbitrary_page_editing", "annotation_editing", "form_editing"],
    }


def pdf_metadata(path: str | Path) -> dict[str, Any]:
    target, reader = _reader(path)
    return {
        "ok": True,
        "path": str(target),
        "encrypted": bool(reader.is_encrypted),
        "metadata": _metadata(reader),
    }


def _extract_page(reader: PdfReader, index: int) -> str:
    try:
        text = reader.pages[index].extract_text()
    except Exception as exc:
        raise ToolError(
            "pdf_text_extraction_failed",
            f"Text extraction failed on page {index + 1}.",
        ) from exc
    return text or ""


def page_text(
    path: str | Path,
    *,
    page_number: int,
    max_chars: int = 250_000,
) -> dict[str, Any]:
    if not 1 <= max_chars <= _MAX_EXTRACT_CHARS:
        raise ToolError("invalid_max_chars", f"max_chars must be between 1 and {_MAX_EXTRACT_CHARS}.")
    target, reader = _reader(path, require_unencrypted=True)
    count = _page_count(reader)
    if count is None:
        raise ToolError("invalid_pdf", "PDF page count could not be resolved.")
    if page_number < 1 or page_number > count:
        raise ToolError(
            "pdf_page_out_of_range",
            f"page_number must be between 1 and {count}.",
        )
    raw = _extract_page(reader, page_number - 1)
    text = raw[:max_chars]
    return {
        "ok": True,
        "path": str(target),
        "page_number": page_number,
        "page_count": count,
        "text": text,
        "chars": len(text),
        "truncated": len(raw) > max_chars,
    }


def extract_text(
    path: str | Path,
    *,
    start_page: int = 1,
    end_page: int | None = None,
    max_pages: int = 100,
    max_chars: int = 250_000,
    delivery: Delivery = "inline",
) -> dict[str, Any]:
    if start_page < 1:
        raise ToolError("invalid_start_page", "start_page must be at least 1.")
    if end_page is not None and end_page < start_page:
        raise ToolError("invalid_page_range", "end_page must be greater than or equal to start_page.")
    if not 1 <= max_pages <= 10_000:
        raise ToolError("invalid_max_pages", "max_pages must be between 1 and 10000.")
    if not 1 <= max_chars <= _MAX_EXTRACT_CHARS:
        raise ToolError("invalid_max_chars", f"max_chars must be between 1 and {_MAX_EXTRACT_CHARS}.")

    target, reader = _reader(path, require_unencrypted=True)
    count = _page_count(reader)
    if count is None:
        raise ToolError("invalid_pdf", "PDF page count could not be resolved.")
    if start_page > count:
        raise ToolError(
            "pdf_page_out_of_range",
            f"start_page must be between 1 and {count}.",
        )

    requested_end = count if end_page is None else min(end_page, count)
    bounded_end = min(requested_end, start_page + max_pages - 1)
    pieces: list[str] = []
    chars = 0
    pages_processed = 0
    truncated = bounded_end < requested_end or (end_page is not None and end_page > count)

    for page_index in range(start_page - 1, bounded_end):
        raw = _extract_page(reader, page_index)
        remaining = max_chars - chars
        if remaining <= 0:
            truncated = True
            break
        if len(raw) > remaining:
            pieces.append(raw[:remaining])
            chars += remaining
            pages_processed += 1
            truncated = True
            break
        pieces.append(raw)
        chars += len(raw)
        pages_processed += 1

    if pages_processed < (bounded_end - start_page + 1):
        truncated = True

    text = "".join(pieces)
    result: dict[str, Any] = {
        "ok": True,
        "path": str(target),
        "page_count": count,
        "start_page": start_page,
        "end_page": start_page + pages_processed - 1 if pages_processed else None,
        "pages_processed": pages_processed,
        "chars": len(text),
        "truncated": truncated,
    }
    output = deliver_text(text, delivery)
    if output["delivery"] == "inline":
        result["text"] = output["text"]
    else:
        result["output"] = output
    return result


def _validate_staged_pdf(path: Path) -> None:
    try:
        reader = PdfReader(str(path), strict=False)
        if reader.is_encrypted:
            raise ToolError("invalid_pdf", "Created PDF unexpectedly requires decryption.")
        if len(reader.pages) < 1:
            raise ToolError("invalid_pdf", "Created PDF has no pages.")
    except ToolError:
        raise
    except Exception as exc:
        raise ToolError("invalid_pdf", "Staged PDF failed round-trip validation.") from exc


def _page_size(name: PageSize) -> tuple[float, float]:
    try:
        width, height = _PAGE_SIZES[name]
    except KeyError as exc:
        raise ToolError("unsupported_page_size", f"Unsupported page size: {name!r}.") from exc
    return float(width), float(height)


def _wrap_line(text: str, *, code: bool) -> list[str]:
    normalized = text.expandtabs(4)
    width = 84 if code else 92
    if not normalized:
        return [""]
    return textwrap.wrap(
        normalized,
        width=width,
        replace_whitespace=False,
        drop_whitespace=False,
        break_long_words=True,
        break_on_hyphens=False,
    ) or [""]


def _render_blocks(
    path: Path,
    *,
    blocks: list[tuple[str, str]],
    title: str | None,
    page_size: PageSize,
) -> int:
    width, height = _page_size(page_size)
    document = canvas.Canvas(str(path), pagesize=(width, height), invariant=1)
    document.setCreator("ComputerPilot MCP")
    document.setTitle(title or "")
    left = 54.0
    top = height - 54.0
    bottom = 54.0
    y = top
    page_count = 1

    def new_page() -> None:
        nonlocal y, page_count
        document.showPage()
        page_count += 1
        y = top

    for kind, raw in blocks:
        if kind == "heading1":
            font, size, leading = "Helvetica-Bold", 18.0, 24.0
        elif kind == "heading2":
            font, size, leading = "Helvetica-Bold", 15.0, 20.0
        elif kind == "heading3":
            font, size, leading = "Helvetica-Bold", 13.0, 18.0
        elif kind == "code":
            font, size, leading = "Courier", 9.0, 12.0
        else:
            font, size, leading = "Helvetica", 10.0, 14.0

        for line in _wrap_line(raw, code=kind == "code"):
            if y - leading < bottom:
                new_page()
            document.setFont(font, size)
            document.drawString(left, y, line)
            y -= leading
        if kind.startswith("heading"):
            y -= 4.0
    document.save()
    return page_count


def _text_blocks(text: str) -> list[tuple[str, str]]:
    return [("body", line) for line in text.splitlines()] or [("body", "")]


def _markdown_blocks(markdown: str) -> list[tuple[str, str]]:
    blocks: list[tuple[str, str]] = []
    in_code = False
    for line in markdown.splitlines():
        stripped = line.strip()
        if stripped.startswith("```"):
            in_code = not in_code
            continue
        if in_code:
            blocks.append(("code", line))
        elif line.startswith("### "):
            blocks.append(("heading3", line[4:]))
        elif line.startswith("## "):
            blocks.append(("heading2", line[3:]))
        elif line.startswith("# "):
            blocks.append(("heading1", line[2:]))
        elif line.startswith(("- ", "* ")):
            blocks.append(("body", "- " + line[2:]))
        else:
            blocks.append(("body", line))
    if in_code:
        raise ToolError("unclosed_markdown_fence", "Markdown contains an unclosed fenced code block.")
    return blocks or [("body", "")]


def _create_pdf(
    path: str | Path,
    *,
    blocks: list[tuple[str, str]],
    title: str | None,
    page_size: PageSize,
    overwrite: bool,
    expected_sha256: str | None,
    backup: bool,
    create_parents: bool,
) -> dict[str, Any]:
    target = _pdf_target(path, must_exist=False)
    if expected_sha256 is not None and not overwrite:
        raise ToolError(
            "expected_hash_requires_overwrite",
            "expected_sha256 is only meaningful when overwrite=True.",
        )
    state = {"page_count": 0}

    def writer(staged: Path) -> None:
        state["page_count"] = _render_blocks(
            staged,
            blocks=blocks,
            title=title,
            page_size=page_size,
        )

    result = publish_document(
        target,
        writer=writer,
        validator=_validate_staged_pdf,
        media_type=PDF_MEDIA_TYPE,
        backup=backup,
        expected_sha256=expected_sha256,
        create_parents=create_parents,
        exclusive_create=not overwrite,
    )
    return {
        **result,
        "page_count": state["page_count"],
        "page_size": page_size,
        "limitations": [
            "creation uses built-in PDF fonts",
            "complex-script shaping and arbitrary visual editing are not supported",
        ],
    }


def create_from_text(
    path: str | Path,
    *,
    text: str,
    title: str | None = None,
    page_size: PageSize = "A4",
    overwrite: bool = False,
    expected_sha256: str | None = None,
    backup: bool = True,
    create_parents: bool = False,
) -> dict[str, Any]:
    if len(text) > _MAX_CREATE_CHARS:
        raise ToolError("content_too_large", f"text exceeds {_MAX_CREATE_CHARS} characters.")
    return _create_pdf(
        path,
        blocks=_text_blocks(text),
        title=title,
        page_size=page_size,
        overwrite=overwrite,
        expected_sha256=expected_sha256,
        backup=backup,
        create_parents=create_parents,
    )


def create_from_markdown(
    path: str | Path,
    *,
    markdown: str,
    title: str | None = None,
    page_size: PageSize = "A4",
    overwrite: bool = False,
    expected_sha256: str | None = None,
    backup: bool = True,
    create_parents: bool = False,
) -> dict[str, Any]:
    if len(markdown) > _MAX_CREATE_CHARS:
        raise ToolError("content_too_large", f"markdown exceeds {_MAX_CREATE_CHARS} characters.")
    result = _create_pdf(
        path,
        blocks=_markdown_blocks(markdown),
        title=title,
        page_size=page_size,
        overwrite=overwrite,
        expected_sha256=expected_sha256,
        backup=backup,
        create_parents=create_parents,
    )
    return {
        **result,
        "supported_markdown": ["headings", "unordered_lists", "fenced_code", "paragraphs"],
    }
