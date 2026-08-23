"""
PDF specification parser.

Extracts text from PDF files using pdfplumber (preferred) or PyPDF2 (fallback).
"""

from __future__ import annotations

from pathlib import Path

from core.logger import get_logger

logger = get_logger("Parser.PDF")


def parse_pdf(file_path: str | Path) -> str:
    """Extract all text from a PDF file.

    Tries pdfplumber first (better with tables), falls back to PyPDF2.
    """
    file_path = Path(file_path)
    if not file_path.exists():
        raise FileNotFoundError(f"PDF file not found: {file_path}")

    # Try pdfplumber first (handles tables and layout better)
    try:
        import pdfplumber

        text_parts = []
        with pdfplumber.open(file_path) as pdf:
            for page in pdf.pages:
                page_text = page.extract_text()
                if page_text:
                    text_parts.append(page_text)

                # Also extract tables
                tables = page.extract_tables()
                for table in tables:
                    for row in table:
                        row_text = " | ".join(
                            cell.strip() if cell else "" for cell in row
                        )
                        text_parts.append(row_text)

        text = "\n\n".join(text_parts)
        logger.info("Parsed PDF with pdfplumber: %d chars from %d pages",
                     len(text), len(pdf.pages))
        return text

    except ImportError:
        logger.debug("pdfplumber not available, falling back to PyPDF2")
    except Exception as exc:
        logger.warning("pdfplumber failed for %s; falling back to PyPDF2: %s", file_path, exc)

    # Fallback to PyPDF2
    try:
        from PyPDF2 import PdfReader

        reader = PdfReader(str(file_path))
        text_parts = []
        for page in reader.pages:
            page_text = page.extract_text()
            if page_text:
                text_parts.append(page_text)

        text = "\n\n".join(text_parts)
        logger.info("Parsed PDF with PyPDF2: %d chars from %d pages",
                     len(text), len(reader.pages))
        return text

    except ImportError:
        raise ImportError(
            "Neither pdfplumber nor PyPDF2 is installed. "
            "Install one: pip install pdfplumber PyPDF2"
        )
