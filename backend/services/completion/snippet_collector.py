"""Server-side snippet retrieval for completion RAG."""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Optional

from sqlalchemy.orm import Session

from database.models import MentalModelRevision, Project, ProjectArtifactPointer
from services.completion.config import CompletionConfig
from services.completion.schemas import Segments, Snippet
from services.document_context.manager import get_spec_context_dir
from services.document_context.retriever import SpecPageRetriever
from services.mental_model.store import get_latest_mental_model_revision
from services.rtl_context.manager import search_rtl_context

logger = logging.getLogger(__name__)

_MODULE_RE = re.compile(
    r"\b(module|interface|package|program)\s+(\w+)",
    re.IGNORECASE,
)


def infer_target_module(segments: Segments) -> Optional[str]:
    if segments.target_module:
        return segments.target_module.strip()
    prefix = segments.prefix or ""
    matches = list(_MODULE_RE.finditer(prefix))
    if matches:
        return matches[-1].group(2)
    return None


def _resolve_pointers(
    db: Session,
    project_id: str,
) -> Optional[ProjectArtifactPointer]:
    return (
        db.query(ProjectArtifactPointer)
        .filter(ProjectArtifactPointer.project_id == project_id)
        .first()
    )


class SnippetCollector:
    def __init__(self, config: CompletionConfig) -> None:
        self.config = config

    def collect_server_snippets(
        self,
        db: Session,
        project: Project,
        segments: Segments,
        *,
        budget_remaining: int,
        disable_rag: bool = False,
    ) -> list[Snippet]:
        if disable_rag or budget_remaining <= self.config.server_search_reserve:
            return []

        snippets: list[Snippet] = []
        pointer = _resolve_pointers(db, project.id)
        spec_id = segments.spec_artifact_id or (
            pointer.active_spec_artifact_id if pointer else None
        )
        rtl_id = segments.rtl_artifact_id or (
            pointer.active_rtl_artifact_id if pointer else None
        )
        mm_id = segments.mental_model_revision_id

        query_text = (segments.prefix or "")[-400:]
        keywords = " ".join(re.findall(r"[A-Za-z_][A-Za-z0-9_]{2,}", query_text))[:200]

        if spec_id and keywords:
            snippets.extend(
                self._spec_snippets(project, spec_id, keywords, limit=2)
            )

        mm_revision = None
        if mm_id:
            mm_revision = (
                db.query(MentalModelRevision)
                .filter(
                    MentalModelRevision.id == mm_id,
                    MentalModelRevision.project_id == project.id,
                )
                .first()
            )
        if not mm_revision:
            mm_revision = get_latest_mental_model_revision(db, project.id)

        if mm_revision:
            snippets.extend(
                self._mental_model_snippets(mm_revision, infer_target_module(segments))
            )

        if rtl_id and keywords:
            snippets.extend(
                search_rtl_context(
                    project.organization_id,
                    project.id,
                    rtl_id,
                    keywords,
                    limit=2,
                )
            )

        return snippets

    def _spec_snippets(
        self,
        project: Project,
        spec_artifact_id: str,
        query: str,
        *,
        limit: int = 2,
    ) -> list[Snippet]:
        try:
            context_dir = get_spec_context_dir(
                project.organization_id,
                project.id,
                spec_artifact_id,
            )
            if not (context_dir / "search.sqlite").exists():
                return []
            retriever = SpecPageRetriever(context_dir)
            hits = retriever.search(query, limit=limit)
            out: list[Snippet] = []
            for hit in hits:
                body = str(hit.get("snippet") or hit.get("body") or "")[:400]
                if not body:
                    continue
                out.append(
                    Snippet(
                        filepath=f"spec:p{hit.get('page_num', '?')}",
                        body=body,
                        kind="spec",
                        source="server_fts",
                    )
                )
            return out
        except Exception:
            logger.debug("Spec snippet search failed", exc_info=True)
            return []

    def _mental_model_snippets(
        self,
        revision: MentalModelRevision,
        target_module: Optional[str],
    ) -> list[Snippet]:
        try:
            content = json.loads(revision.content_json or "{}")
        except json.JSONDecodeError:
            return []

        lines: list[str] = []
        symbol_table = content.get("symbol_table") or {}
        block_models = content.get("block_models") or {}

        module = target_module
        if module and module in symbol_table:
            symbols = symbol_table[module][:20]
            lines.append(f"module {module} symbols: {', '.join(symbols)}")

        if module and module in block_models:
            block = block_models[module]
            ports = block.get("ports") or block.get("interfaces") or []
            if ports:
                port_names = []
                for p in ports[:12]:
                    if isinstance(p, dict):
                        port_names.append(str(p.get("name") or p))
                    else:
                        port_names.append(str(p))
                lines.append(f"ports: {', '.join(port_names)}")

        reqs = content.get("requirements") or []
        for req in reqs[:3]:
            if not isinstance(req, dict):
                continue
            ref = req.get("source_ref") or {}
            if module and ref.get("module") and ref.get("module") != module:
                continue
            text = str(req.get("text") or req.get("description") or "")[:120]
            if text:
                lines.append(f"REQ: {text}")

        if not lines:
            design = content.get("design") or {}
            top = design.get("top_module")
            if top:
                lines.append(f"top_module: {top}")

        if not lines:
            return []

        body = "\n".join(lines)[:500]
        return [
            Snippet(
                filepath=f"mental_model:v{revision.revision}",
                body=body,
                kind="mental_model",
                source="server_mm",
            )
        ]
