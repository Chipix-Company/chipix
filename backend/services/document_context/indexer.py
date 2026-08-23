"""Build hierarchical section index from extracted pages (heuristic, no LLM required)."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import List

from services.document_context.models import DocumentIndex, DocumentPage, DocumentSection

_HEADING_RE = re.compile(
    r"^(?:"
    r"(?:chapter|section|appendix)\s+[\d\.]+[^\n]{0,120}|"
    r"[\d]+\.[\d]+(?:\.[\d]+)*\s+.{3,120}|"
    r"[\d]+\.\s+.{5,120}|"
    r"[A-Z][A-Z0-9\s\-]{4,80}"
    r")\s*$",
    re.MULTILINE | re.IGNORECASE,
)

_KEYWORD_STOP = frozenset(
    {
        "the",
        "and",
        "for",
        "with",
        "shall",
        "must",
        "this",
        "that",
        "from",
        "page",
        "section",
    }
)


def _keywords_from_text(text: str, limit: int = 8) -> list[str]:
    tokens = re.findall(r"[A-Za-z][A-Za-z0-9_\-]{2,}", text.lower())
    seen: set[str] = set()
    out: list[str] = []
    for tok in tokens:
        if tok in _KEYWORD_STOP or tok in seen:
            continue
        seen.add(tok)
        out.append(tok)
        if len(out) >= limit:
            break
    return out


def _summarize_pages(pages: List[DocumentPage], max_chars: int = 400) -> str:
    combined = "\n".join(p.text for p in pages if p.text).strip()
    if not combined:
        return "(No extractable text in this section.)"
    combined = re.sub(r"\s+", " ", combined)
    if len(combined) <= max_chars:
        return combined
    return combined[: max_chars - 3].rstrip() + "..."


def _detect_section_breaks(pages: List[DocumentPage]) -> list[tuple[int, str, int]]:
    """
    Return list of (page_start, title, page_end) sections.
    Uses heading-like lines at page starts and every N pages as fallback windows.
    """
    breaks: list[tuple[int, str]] = []
    for page in pages:
        if not page.text:
            continue
        first_lines = page.text.strip().splitlines()[:6]
        for line in first_lines:
            line = line.strip()
            if len(line) < 4 or len(line) > 160:
                continue
            if _HEADING_RE.match(line):
                breaks.append((page.page_num, line[:120]))
                break

    page_count = len(pages)
    if page_count == 0:
        return []

    if not breaks:
        window = 20 if page_count > 40 else max(5, (page_count + 4) // 5)
        sections: list[tuple[int, str, int]] = []
        start = 1
        part = 1
        while start <= page_count:
            end = min(page_count, start + window - 1)
            sections.append((start, f"Pages {start}–{end}", end))
            start = end + 1
            part += 1
        return sections

    breaks = sorted({(p, t) for p, t in breaks}, key=lambda x: x[0])
    sections: list[tuple[int, str, int]] = []
    for idx, (page_start, title) in enumerate(breaks):
        page_end = (
            breaks[idx + 1][0] - 1 if idx + 1 < len(breaks) else page_count
        )
        if page_end < page_start:
            page_end = page_start
        sections.append((page_start, title, page_end))
    return sections


def build_heuristic_index(
    *,
    artifact_id: str,
    checksum_sha256: str,
    filename: str,
    pages: List[DocumentPage],
) -> DocumentIndex:
    raw_sections = _detect_section_breaks(pages)
    page_map = {p.page_num: p for p in pages}
    sections: list[DocumentSection] = []

    for page_start, title, page_end in raw_sections:
        slice_pages = [
            page_map[n]
            for n in range(page_start, page_end + 1)
            if n in page_map
        ]
        summary = _summarize_pages(slice_pages)
        keywords = _keywords_from_text(summary + " " + title)
        sections.append(
            DocumentSection(
                section_title=title,
                page_start=page_start,
                page_end=page_end,
                summary=summary,
                keywords=keywords,
            )
        )

    if not sections and pages:
        sections.append(
            DocumentSection(
                section_title="Document",
                page_start=1,
                page_end=pages[-1].page_num,
                summary=_summarize_pages(pages),
                keywords=_keywords_from_text(pages[0].text if pages else ""),
            )
        )

    return DocumentIndex(
        artifact_id=artifact_id,
        checksum_sha256=checksum_sha256,
        filename=filename,
        page_count=len(pages),
        sections=sections,
        indexer="heuristic",
        built_at=datetime.now(timezone.utc).isoformat(),
    )
