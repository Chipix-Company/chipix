"""Typed structures for document context indexing and retrieval."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Optional


@dataclass
class DocumentPage:
    page_num: int
    text: str
    char_count: int
    content_hash: str
    source_line_start: int = 1
    source_line_end: int = 1


@dataclass
class DocumentSection:
    section_title: str
    page_start: int
    page_end: int
    summary: str
    keywords: list[str] = field(default_factory=list)
    subsections: list["DocumentSection"] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["subsections"] = [s.to_dict() for s in self.subsections]
        return data


@dataclass
class DocumentIndex:
    artifact_id: str
    checksum_sha256: str
    filename: str
    page_count: int
    sections: list[DocumentSection]
    indexer: str = "heuristic"
    built_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "artifact_id": self.artifact_id,
            "checksum_sha256": self.checksum_sha256,
            "filename": self.filename,
            "page_count": self.page_count,
            "sections": [s.to_dict() for s in self.sections],
            "indexer": self.indexer,
            "built_at": self.built_at,
        }


@dataclass
class DocumentContextStatus:
    artifact_id: str
    status: str  # pending | indexing | ready | error | not_needed
    page_count: int = 0
    section_count: int = 0
    error: Optional[str] = None
    index_path: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
