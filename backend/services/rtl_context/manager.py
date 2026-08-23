"""Orchestrate RTL artifact indexing and retrieval for completion RAG."""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path

from common.paths import outputs_dir
from typing import Any, Optional

from services.completion.schemas import Snippet
from services.rtl_context.chunker import chunk_rtl_text, iter_rtl_files_from_path
from services.rtl_context.search_index import build_rtl_fts_index, search_rtl_fts

logger = logging.getLogger(__name__)

_STATUS_FILE = "status.json"


@dataclass
class RtlContextStatus:
    artifact_id: str
    status: str
    chunk_count: int = 0
    file_count: int = 0
    error: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "artifact_id": self.artifact_id,
            "status": self.status,
            "chunk_count": self.chunk_count,
            "file_count": self.file_count,
            "error": self.error,
        }


def _outputs_dir() -> Path:
    return outputs_dir()


def get_rtl_context_dir(
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
        / "rtl_context"
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


def get_rtl_context_status(
    *,
    organization_id: str,
    project_id: str,
    artifact_id: str,
    checksum_sha256: str = "",
) -> RtlContextStatus:
    context_dir = get_rtl_context_dir(organization_id, project_id, artifact_id)
    status = _read_status(context_dir)
    if status.get("checksum_sha256") and checksum_sha256:
        if status["checksum_sha256"] != checksum_sha256:
            return RtlContextStatus(
                artifact_id=artifact_id,
                status="pending",
                error="checksum_mismatch",
            )
    if (context_dir / "search.sqlite").exists() and status.get("status") == "ready":
        return RtlContextStatus(
            artifact_id=artifact_id,
            status="ready",
            chunk_count=int(status.get("chunk_count") or 0),
            file_count=int(status.get("file_count") or 0),
        )
    st = str(status.get("status") or "pending")
    return RtlContextStatus(
        artifact_id=artifact_id,
        status=st,
        chunk_count=int(status.get("chunk_count") or 0),
        file_count=int(status.get("file_count") or 0),
        error=status.get("error"),
    )


def ensure_rtl_context_index(
    *,
    organization_id: str,
    project_id: str,
    artifact_id: str,
    file_path: str | Path,
    checksum_sha256: str,
    force: bool = False,
) -> RtlContextStatus:
    context_dir = get_rtl_context_dir(organization_id, project_id, artifact_id)
    existing = get_rtl_context_status(
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
        pairs = iter_rtl_files_from_path(Path(file_path))
        chunks = []
        for rel_path, text in pairs:
            chunks.extend(chunk_rtl_text(rel_path, text))

        build_rtl_fts_index(context_dir / "search.sqlite", chunks)
        payload = {
            "artifact_id": artifact_id,
            "status": "ready",
            "checksum_sha256": checksum_sha256,
            "chunk_count": len(chunks),
            "file_count": len(pairs),
        }
        _write_status(context_dir, payload)
        return RtlContextStatus(
            artifact_id=artifact_id,
            status="ready",
            chunk_count=len(chunks),
            file_count=len(pairs),
        )
    except Exception as exc:
        logger.exception("RTL context indexing failed for %s", artifact_id)
        _write_status(
            context_dir,
            {
                "artifact_id": artifact_id,
                "status": "error",
                "checksum_sha256": checksum_sha256,
                "error": str(exc),
            },
        )
        return RtlContextStatus(
            artifact_id=artifact_id,
            status="error",
            error=str(exc),
        )


def search_rtl_context(
    organization_id: str,
    project_id: str,
    artifact_id: str,
    query: str,
    *,
    limit: int = 4,
) -> list[Snippet]:
    context_dir = get_rtl_context_dir(organization_id, project_id, artifact_id)
    hits = search_rtl_fts(context_dir / "search.sqlite", query, limit=limit)
    snippets: list[Snippet] = []
    for hit in hits:
        body = str(hit.get("snippet") or "")[:400]
        if not body:
            continue
        filepath = str(hit.get("filepath") or "rtl")
        module = hit.get("module_name")
        if module:
            filepath = f"{filepath} ({module})"
        snippets.append(
            Snippet(
                filepath=filepath,
                body=body,
                kind="rtl",
                source="server_rtl_fts",
                score=float(hit.get("score") or 0),
            )
        )
    return snippets
