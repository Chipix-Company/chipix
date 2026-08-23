"""Page and section retrieval with caching."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from services.document_context.extract import read_pages_jsonl
from services.document_context.models import DocumentIndex, DocumentSection
from services.document_context.search_index import search_fts

MAX_PAGES_PER_READ = 30


class SpecPageRetriever:
    def __init__(self, context_dir: Path) -> None:
        self.context_dir = context_dir
        self._page_cache: dict[tuple[int, int], str] = {}

    def _load_pages(self):
        return read_pages_jsonl(self.context_dir / "pages.jsonl")

    def read_pages(self, start_page: int, end_page: int) -> str:
        if start_page < 1:
            start_page = 1
        if end_page < start_page:
            end_page = start_page
        if end_page - start_page + 1 > MAX_PAGES_PER_READ:
            end_page = start_page + MAX_PAGES_PER_READ - 1

        cache_key = (start_page, end_page)
        if cache_key in self._page_cache:
            return self._page_cache[cache_key]

        pages = self._load_pages()
        page_map = {p.page_num: p.text for p in pages}
        parts: list[str] = []
        for num in range(start_page, end_page + 1):
            text = page_map.get(num, "")
            parts.append(f"\n--- PAGE {num} ---\n{text}")

        result = "".join(parts).strip()
        self._page_cache[cache_key] = result
        return result

    def read_section(self, section_title: str, index: DocumentIndex) -> str:
        title_key = section_title.strip().lower()
        for section in index.sections:
            if section.section_title.strip().lower() == title_key:
                return self.read_pages(section.page_start, section.page_end)
            for sub in section.subsections:
                if sub.section_title.strip().lower() == title_key:
                    return self.read_pages(sub.page_start, sub.page_end)
        # Partial match
        for section in index.sections:
            if title_key in section.section_title.strip().lower():
                return self.read_pages(section.page_start, section.page_end)
        raise ValueError(f"Section not found in index: {section_title}")

    def search(self, query: str, *, limit: int = 12) -> list[dict[str, Any]]:
        db_path = self.context_dir / "search.sqlite"
        return search_fts(db_path, query, limit=limit)


def load_index_json(context_dir: Path) -> Optional[DocumentIndex]:
    import json

    path = context_dir / "index.json"
    if not path.exists():
        return None
    raw = json.loads(path.read_text(encoding="utf-8"))

    def _section_from_dict(data: dict) -> DocumentSection:
        subs = [_section_from_dict(s) for s in data.get("subsections") or []]
        return DocumentSection(
            section_title=str(data.get("section_title") or ""),
            page_start=int(data.get("page_start") or 1),
            page_end=int(data.get("page_end") or 1),
            summary=str(data.get("summary") or ""),
            keywords=list(data.get("keywords") or []),
            subsections=subs,
        )

    sections = [_section_from_dict(s) for s in raw.get("sections") or []]
    return DocumentIndex(
        artifact_id=str(raw.get("artifact_id") or ""),
        checksum_sha256=str(raw.get("checksum_sha256") or ""),
        filename=str(raw.get("filename") or ""),
        page_count=int(raw.get("page_count") or 0),
        sections=sections,
        indexer=str(raw.get("indexer") or "heuristic"),
        built_at=str(raw.get("built_at") or ""),
    )
