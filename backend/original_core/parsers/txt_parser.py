"""
Plain text specification parser.

Handles .txt and .md specification files.
"""

from __future__ import annotations

from pathlib import Path

from core.logger import get_logger

logger = get_logger("Parser.TXT")


def parse_txt(file_path: str | Path) -> str:
    """Read a plain text or markdown specification file."""
    file_path = Path(file_path)
    if not file_path.exists():
        raise FileNotFoundError(f"Text file not found: {file_path}")

    text = file_path.read_text(encoding="utf-8", errors="replace")
    logger.info("Parsed TXT: %d chars, %d lines", len(text), len(text.splitlines()))
    return text
