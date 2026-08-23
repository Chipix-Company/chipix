"""Extract page-level text from spec artifacts (PDF, DOCX, plain text)."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import List

from services.document_context.models import DocumentPage

# Approximate chars per "virtual page" for non-paginated formats
_VIRTUAL_PAGE_CHARS = 3500


def _page_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="ignore")).hexdigest()[:16]


def extract_pages_from_pdf(file_path: Path) -> List[DocumentPage]:
    pages: List[DocumentPage] = []
    try:
        import pdfplumber

        with pdfplumber.open(file_path) as pdf:
            for idx, page in enumerate(pdf.pages, start=1):
                parts: list[str] = []
                page_text = page.extract_text()
                if page_text:
                    parts.append(page_text)
                for table in page.extract_tables() or []:
                    for row in table:
                        row_text = " | ".join(
                            (cell.strip() if cell else "") for cell in row
                        )
                        if row_text.strip():
                            parts.append(row_text)
                text = "\n\n".join(parts).strip()
                pages.append(
                    DocumentPage(
                        page_num=idx,
                        text=text,
                        char_count=len(text),
                        content_hash=_page_hash(text),
                    )
                )
        if pages:
            return pages
    except ImportError:
        pass
    except Exception:
        pass

    try:
        from PyPDF2 import PdfReader

        reader = PdfReader(str(file_path))
        for idx, page in enumerate(reader.pages, start=1):
            text = (page.extract_text() or "").strip()
            pages.append(
                DocumentPage(
                    page_num=idx,
                    text=text,
                    char_count=len(text),
                    content_hash=_page_hash(text),
                )
            )
        return pages
    except ImportError as exc:
        raise ImportError(
            "Neither pdfplumber nor PyPDF2 is installed for page extraction."
        ) from exc


def _split_markdown_by_headings(text: str) -> List[DocumentPage] | None:
    """If markdown has multiple ## headings, treat each as one page."""
    import re

    parts = re.split(r"(?=^##\s+)", text, flags=re.MULTILINE)
    parts = [p.strip() for p in parts if p.strip()]
    if len(parts) < 2:
        return None
    pages: List[DocumentPage] = []
    search_from = 0
    for idx, chunk in enumerate(parts, start=1):
        offset = text.find(chunk, search_from)
        line_start = text.count("\n", 0, max(0, offset)) + 1
        line_end = line_start + max(0, len(chunk.splitlines()) - 1)
        search_from = max(search_from, offset + len(chunk))
        pages.append(
            DocumentPage(
                page_num=idx,
                text=chunk,
                char_count=len(chunk),
                content_hash=_page_hash(chunk),
                source_line_start=line_start,
                source_line_end=line_end,
            )
        )
    return pages


def _split_text_into_virtual_pages(text: str) -> List[DocumentPage]:
    if not text.strip():
        return [
            DocumentPage(
                page_num=1,
                text="",
                char_count=0,
                content_hash=_page_hash(""),
            )
        ]

    paragraphs = re.split(r"\n\s*\n", text)
    chunks: list[str] = []
    current: list[str] = []
    current_len = 0

    for para in paragraphs:
        para = para.strip()
        if not para:
            continue
        plen = len(para) + 2
        if current and current_len + plen > _VIRTUAL_PAGE_CHARS:
            chunks.append("\n\n".join(current))
            current = [para]
            current_len = plen
        else:
            current.append(para)
            current_len += plen
    if current:
        chunks.append("\n\n".join(current))

    if not chunks:
        chunks = [text[:_VIRTUAL_PAGE_CHARS]]

    pages: List[DocumentPage] = []
    source_cursor = 0
    for idx, chunk in enumerate(chunks, start=1):
        offset = text.find(chunk, source_cursor)
        line_start = text.count("\n", 0, max(0, offset)) + 1
        line_end = line_start + max(0, len(chunk.splitlines()) - 1)
        source_cursor = max(source_cursor, offset + len(chunk))
        pages.append(
            DocumentPage(
                page_num=idx,
                text=chunk,
                char_count=len(chunk),
                content_hash=_page_hash(chunk),
                source_line_start=line_start,
                source_line_end=line_end,
            )
        )
    return pages


def extract_pages_from_file(file_path: Path, filename: str) -> List[DocumentPage]:
    """Return ordered page records for a spec file."""
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"Spec file not found: {path}")

    ext = Path(filename or path.name).suffix.lower()
    if ext == ".pdf":
        return extract_pages_from_pdf(path)
    if ext in {".doc", ".docx"}:
        from original_core.parsers.docx_parser import parse_docx

        return _split_text_into_virtual_pages(parse_docx(str(path)))
    text = path.read_text(encoding="utf-8", errors="ignore")
    if ext in {".md", ".markdown", ".rst"}:
        by_heading = _split_markdown_by_headings(text)
        if by_heading:
            return by_heading
    return _split_text_into_virtual_pages(text)


def write_pages_jsonl(pages: List[DocumentPage], dest: Path) -> None:
    import json

    dest.parent.mkdir(parents=True, exist_ok=True)
    with dest.open("w", encoding="utf-8") as fh:
        for page in pages:
            fh.write(
                json.dumps(
                    {
                        "page_num": page.page_num,
                        "text": page.text,
                        "char_count": page.char_count,
                        "content_hash": page.content_hash,
                        "source_line_start": page.source_line_start,
                        "source_line_end": page.source_line_end,
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )


def read_pages_jsonl(path: Path) -> List[DocumentPage]:
    import json

    if not path.exists():
        return []
    pages: List[DocumentPage] = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            pages.append(
                DocumentPage(
                    page_num=int(row["page_num"]),
                    text=str(row.get("text") or ""),
                    char_count=int(row.get("char_count") or 0),
                    content_hash=str(row.get("content_hash") or ""),
                    source_line_start=int(row.get("source_line_start") or 1),
                    source_line_end=int(row.get("source_line_end") or row.get("source_line_start") or 1),
                )
            )
    return pages
