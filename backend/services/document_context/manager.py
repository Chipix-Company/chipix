"""Orchestrate indexing, status, and retrieval for spec artifacts."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from common.paths import outputs_dir
from typing import Any, Optional

from services.document_context.extract import (
    extract_pages_from_file,
    read_pages_jsonl,
    write_pages_jsonl,
)
from services.document_context.indexer import build_heuristic_index
from services.document_context.models import DocumentContextStatus, DocumentIndex
from services.document_context.render import render_index_markdown
from services.document_context.retriever import SpecPageRetriever, load_index_json
from services.document_context.search_index import build_fts_index

logger = logging.getLogger(__name__)

_STATUS_FILE = "status.json"
_MIN_PAGES_FOR_INDEX = int(os.environ.get("CHIPVERIFY_DOC_INDEX_MIN_PAGES", "15"))
_MAX_MENTAL_MODEL_SPEC_CHARS = int(
    os.environ.get("CHIPVERIFY_MENTAL_MODEL_SPEC_CHARS", "120000")
)


def _outputs_dir() -> Path:
    return outputs_dir()


def get_spec_context_dir(
    organization_id: str,
    project_id: str,
    artifact_id: str,
) -> Path:
    return (
        _outputs_dir()
        / "orgs"
        / organization_id
        / "projects"
        / project_id
        / "artifacts"
        / "spec_context"
        / artifact_id
    )


def _write_status(context_dir: Path, payload: dict[str, Any]) -> None:
    context_dir.mkdir(parents=True, exist_ok=True)
    (context_dir / _STATUS_FILE).write_text(
        json.dumps(payload, indent=2),
        encoding="utf-8",
    )


def _read_status(context_dir: Path) -> dict[str, Any]:
    path = context_dir / _STATUS_FILE
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def get_document_context_status(
    *,
    organization_id: str,
    project_id: str,
    artifact_id: str,
    checksum_sha256: str = "",
) -> DocumentContextStatus:
    context_dir = get_spec_context_dir(organization_id, project_id, artifact_id)
    status = _read_status(context_dir)
    if status.get("checksum_sha256") and checksum_sha256:
        if status["checksum_sha256"] != checksum_sha256:
            return DocumentContextStatus(
                artifact_id=artifact_id,
                status="pending",
                error="checksum_mismatch",
            )

    index = load_index_json(context_dir)
    if index and (context_dir / "pages.jsonl").exists():
        return DocumentContextStatus(
            artifact_id=artifact_id,
            status="ready",
            page_count=index.page_count,
            section_count=len(index.sections),
            index_path=str(context_dir / "index.md"),
        )

    st = str(status.get("status") or "pending")
    return DocumentContextStatus(
        artifact_id=artifact_id,
        status=st,
        page_count=int(status.get("page_count") or 0),
        section_count=int(status.get("section_count") or 0),
        error=status.get("error"),
        index_path=status.get("index_path"),
    )


def ensure_spec_document_index(
    *,
    organization_id: str,
    project_id: str,
    artifact_id: str,
    file_path: str | Path,
    filename: str,
    checksum_sha256: str,
    force: bool = False,
) -> DocumentContextStatus:
    """
    Build or load page store + structural index + FTS for a spec artifact.
    """
    context_dir = get_spec_context_dir(organization_id, project_id, artifact_id)
    existing = get_document_context_status(
        organization_id=organization_id,
        project_id=project_id,
        artifact_id=artifact_id,
        checksum_sha256=checksum_sha256,
    )
    if existing.status == "ready" and not force:
        return existing

    _write_status(
        context_dir,
        {
            "artifact_id": artifact_id,
            "status": "indexing",
            "checksum_sha256": checksum_sha256,
        },
    )

    try:
        pages = extract_pages_from_file(Path(file_path), filename)
        write_pages_jsonl(pages, context_dir / "pages.jsonl")

        doc_index = build_heuristic_index(
            artifact_id=artifact_id,
            checksum_sha256=checksum_sha256,
            filename=filename,
            pages=pages,
        )
        (context_dir / "index.json").write_text(
            json.dumps(doc_index.to_dict(), indent=2),
            encoding="utf-8",
        )
        index_md = render_index_markdown(doc_index)
        (context_dir / "index.md").write_text(index_md, encoding="utf-8")

        build_fts_index(context_dir / "search.sqlite", pages=pages, index=doc_index)

        payload = {
            "artifact_id": artifact_id,
            "status": "ready",
            "checksum_sha256": checksum_sha256,
            "page_count": doc_index.page_count,
            "section_count": len(doc_index.sections),
            "index_path": str(context_dir / "index.md"),
        }
        _write_status(context_dir, payload)
        return DocumentContextStatus(
            artifact_id=artifact_id,
            status="ready",
            page_count=doc_index.page_count,
            section_count=len(doc_index.sections),
            index_path=str(context_dir / "index.md"),
        )
    except Exception as exc:
        logger.exception("Spec document indexing failed for %s", artifact_id)
        _write_status(
            context_dir,
            {
                "artifact_id": artifact_id,
                "status": "error",
                "checksum_sha256": checksum_sha256,
                "error": str(exc),
            },
        )
        return DocumentContextStatus(
            artifact_id=artifact_id,
            status="error",
            error=str(exc),
        )


def should_index_spec(page_count: int, size_bytes: int) -> bool:
    """Heuristic: index multi-page or large specs."""
    if page_count >= _MIN_PAGES_FOR_INDEX:
        return True
    max_parse = int(os.environ.get("CHIPVERIFY_SPEC_UPLOAD_PARSE_BYTES", str(20 * 1024 * 1024)))
    return size_bytes >= max_parse // 2


def _retriever_for(
    organization_id: str,
    project_id: str,
    artifact_id: str,
) -> tuple[SpecPageRetriever, DocumentIndex | None]:
    context_dir = get_spec_context_dir(organization_id, project_id, artifact_id)
    return SpecPageRetriever(context_dir), load_index_json(context_dir)


def read_spec_pages(
    *,
    organization_id: str,
    project_id: str,
    artifact_id: str,
    start_page: int,
    end_page: int,
) -> dict[str, Any]:
    retriever, index = _retriever_for(organization_id, project_id, artifact_id)
    if not (get_spec_context_dir(organization_id, project_id, artifact_id) / "pages.jsonl").exists():
        raise FileNotFoundError(
            "Spec document index not built. Upload spec and wait for indexing, "
            "or call POST .../artifacts/{id}/index-spec."
        )
    text = retriever.read_pages(start_page, end_page)
    return {
        "success": True,
        "start_page": start_page,
        "end_page": end_page,
        "char_count": len(text),
        "text": text,
        "page_count": index.page_count if index else None,
    }


def read_spec_section(
    *,
    organization_id: str,
    project_id: str,
    artifact_id: str,
    section_title: str,
) -> dict[str, Any]:
    retriever, index = _retriever_for(organization_id, project_id, artifact_id)
    if not index:
        raise FileNotFoundError("Spec document index not found.")
    text = retriever.read_section(section_title, index)
    section = next(
        (
            s
            for s in index.sections
            if s.section_title.strip().lower() == section_title.strip().lower()
        ),
        None,
    )
    return {
        "success": True,
        "section_title": section.section_title if section else section_title,
        "page_start": section.page_start if section else None,
        "page_end": section.page_end if section else None,
        "char_count": len(text),
        "text": text,
    }


def search_spec(
    *,
    organization_id: str,
    project_id: str,
    artifact_id: str,
    query: str,
    limit: int = 12,
) -> dict[str, Any]:
    retriever, index = _retriever_for(organization_id, project_id, artifact_id)
    hits = retriever.search(query, limit=limit)
    return {
        "success": True,
        "query": query,
        "hits": hits,
        "page_count": index.page_count if index else 0,
    }


def get_index_markdown_for_prompt(
    *,
    organization_id: str,
    project_id: str,
    artifact_id: str,
    max_chars: int = 12000,
) -> str:
    context_dir = get_spec_context_dir(organization_id, project_id, artifact_id)
    path = context_dir / "index.md"
    if not path.exists():
        index = load_index_json(context_dir)
        if index:
            text = render_index_markdown(index)
        else:
            return ""
    else:
        text = path.read_text(encoding="utf-8", errors="ignore")
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 20].rstrip() + "\n\n_(index truncated)_"


def spec_text_for_mental_model(
    *,
    organization_id: str,
    project_id: str,
    artifact_id: str,
    file_path: str | Path,
    filename: str,
    checksum_sha256: str,
) -> str:
    """
    Build a bounded spec excerpt for mental-model LLM prompts.
    Uses full index pages spread + requirement-rich slices instead of [:4000].
    """
    status = ensure_spec_document_index(
        organization_id=organization_id,
        project_id=project_id,
        artifact_id=artifact_id,
        file_path=file_path,
        filename=filename,
        checksum_sha256=checksum_sha256,
    )
    context_dir = get_spec_context_dir(organization_id, project_id, artifact_id)
    pages = read_pages_jsonl(context_dir / "pages.jsonl")
    index = load_index_json(context_dir)

    if status.status != "ready" or not pages:
        from original_core.parsers.pdf_parser import parse_pdf
        from original_core.parsers.docx_parser import parse_docx

        path = Path(file_path)
        ext = Path(filename).suffix.lower()
        try:
            if ext == ".pdf":
                fallback = parse_pdf(str(path))
            elif ext in {".doc", ".docx"}:
                fallback = parse_docx(str(path))
            else:
                fallback = path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            fallback = ""
        return fallback[:_MAX_MENTAL_MODEL_SPEC_CHARS]

    retriever = SpecPageRetriever(context_dir)
    parts: list[str] = []
    budget = _MAX_MENTAL_MODEL_SPEC_CHARS

    # Index overview for orientation
    if index:
        overview = render_index_markdown(index, max_sections=40)
        if len(overview) > 6000:
            overview = overview[:6000] + "\n..."
        parts.append("=== SPEC DOCUMENT INDEX ===\n" + overview)
        budget -= len(parts[-1])

    # Sample pages: first, middle, last + sections mentioning shall/must
    page_nums: list[int] = []
    n = len(pages)
    if n <= 8:
        page_nums = list(range(1, n + 1))
    else:
        page_nums = [1, 2, n // 4, n // 2, (3 * n) // 4, n - 1, n]
        page_nums = sorted(set(page_nums))

    for section in (index.sections if index else [])[:30]:
        lower = (section.summary + section.section_title).lower()
        if any(k in lower for k in ("shall", "must", "requirement", "req-")):
            for p in range(section.page_start, min(section.page_end, section.page_start + 2) + 1):
                page_nums.append(p)
    page_nums = sorted(set(p for p in page_nums if 1 <= p <= n))[:25]

    for start in page_nums:
        if budget <= 0:
            break
        chunk = retriever.read_pages(start, start)
        if len(chunk) > budget:
            chunk = chunk[:budget]
        parts.append(chunk)
        budget -= len(chunk)

    return "\n\n".join(parts)[:_MAX_MENTAL_MODEL_SPEC_CHARS]
