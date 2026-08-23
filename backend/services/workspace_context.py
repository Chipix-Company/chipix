"""
Project workspace manifest for RTL designer agent turns.

Ensures every agentic_chat turn sees existing spec/RTL artifacts even when the
frontend starts a new thread without composer attachments.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Optional

from sqlalchemy.orm import Session

from database.models import Project, ProjectArtifact, ProjectArtifactPointer

_OVERWRITE_HINTS = re.compile(
    r"\b(replace|overwrite|update|modify|change|edit|refactor|fix)\b"
    r".{0,40}\b(file|module|design|rtl|fifo|spec)\b|\b"
    r"(replace|overwrite|update)\s+(the\s+)?(existing|current|my)\b",
    re.IGNORECASE,
)


def user_message_requests_overwrite(user_message: str) -> bool:
    """Heuristic: user explicitly asked to change existing project files."""
    text = (user_message or "").strip()
    if not text:
        return False
    return bool(_OVERWRITE_HINTS.search(text))


def build_project_workspace_manifest(
    db: Session,
    project_id: str,
    *,
    max_artifacts: int = 48,
) -> dict[str, Any]:
    """Load artifact inventory for a project (latest revision per filename+type)."""
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        return {"project_id": project_id, "artifacts": [], "has_active_rtl": False, "has_active_spec": False}

    rows = (
        db.query(ProjectArtifact)
        .filter(ProjectArtifact.project_id == project.id)
        .order_by(
            ProjectArtifact.artifact_type.asc(),
            ProjectArtifact.filename.asc(),
            ProjectArtifact.revision.desc(),
        )
        .all()
    )

    pointer = (
        db.query(ProjectArtifactPointer)
        .filter(ProjectArtifactPointer.project_id == project.id)
        .first()
    )
    active_spec_id = pointer.active_spec_artifact_id if pointer else None
    active_rtl_id = pointer.active_rtl_artifact_id if pointer else None

    seen: set[tuple[str, str]] = set()
    artifacts: list[dict[str, Any]] = []
    for art in rows:
        key = (str(art.artifact_type or ""), str(art.filename or ""))
        if key in seen:
            continue
        seen.add(key)
        is_active = art.id in {active_spec_id, active_rtl_id}
        artifacts.append(
            {
                "id": art.id,
                "filename": art.filename,
                "artifact_type": art.artifact_type,
                "revision": int(art.revision or 0),
                "source": (art.source or "unknown")[:40],
                "is_active": is_active,
            }
        )
        if len(artifacts) >= max_artifacts:
            break

    active_rtl_names = [a["filename"] for a in artifacts if a.get("is_active") and a.get("artifact_type") == "rtl"]
    active_spec_names = [a["filename"] for a in artifacts if a.get("is_active") and a.get("artifact_type") == "spec"]

    document_context: dict[str, Any] = {}
    if active_spec_id:
        try:
            from services.document_context import get_document_context_status

            spec_row = next((a for a in artifacts if a.get("id") == active_spec_id), None)
            spec_art = (
                db.query(ProjectArtifact)
                .filter(ProjectArtifact.id == active_spec_id)
                .first()
            )
            checksum = getattr(spec_art, "checksum_sha256", "") if spec_art else ""
            document_context = get_document_context_status(
                organization_id=str(project.organization_id),
                project_id=str(project.id),
                artifact_id=str(active_spec_id),
                checksum_sha256=str(checksum or ""),
            ).to_dict()
            if spec_row:
                document_context["filename"] = spec_row.get("filename")
        except Exception:
            document_context = {"status": "unknown"}

    return {
        "project_id": project.id,
        "artifacts": artifacts,
        "has_active_rtl": bool(active_rtl_id),
        "has_active_spec": bool(active_spec_id),
        "active_rtl_artifact_id": active_rtl_id,
        "active_spec_artifact_id": active_spec_id,
        "active_rtl_filenames": active_rtl_names,
        "active_spec_filenames": active_spec_names,
        "artifact_count": len(artifacts),
        "document_context": document_context,
    }


def format_manifest_for_prompt(manifest: dict[str, Any]) -> str:
    """Human-readable block for system prompt / merged user turn."""
    artifacts = manifest.get("artifacts") or []
    if not artifacts:
        return (
            "### Project workspace\n"
            "No spec/RTL/generated artifacts in this project yet. "
            "Greenfield: create new files with unique names via createFile."
        )

    lines = [
        "### Project workspace (ground truth — same project across all chat threads)",
        f"- Artifacts in project: {manifest.get('artifact_count', len(artifacts))}",
    ]
    if manifest.get("has_active_spec"):
        lines.append(f"- Active spec: {', '.join(manifest.get('active_spec_filenames') or []) or '(set)'}")
    if manifest.get("has_active_rtl"):
        lines.append(f"- Active RTL: {', '.join(manifest.get('active_rtl_filenames') or []) or '(set)'}")
    doc_ctx = manifest.get("document_context") or {}
    if manifest.get("has_active_spec") and doc_ctx:
        st = doc_ctx.get("status", "unknown")
        pages = doc_ctx.get("page_count") or 0
        sections = doc_ctx.get("section_count") or 0
        lines.append(
            f"- Spec document index: **{st}** ({pages} pages, {sections} sections). "
            "Use readSpecPages / readSpecSection / searchSpec for large specs — do not readFile the whole PDF."
        )

    lines.append("\nFiles (do not recreate these names unless the user asked to replace/update):")
    for art in artifacts[:40]:
        active = " [ACTIVE]" if art.get("is_active") else ""
        lines.append(
            f"  - `{art.get('filename')}` ({art.get('artifact_type')}, id={art.get('id')}, "
            f"rev={art.get('revision')}, source={art.get('source')}){active}"
        )

    lines.append(
        "\nPolicy:\n"
        "- New user requests in an existing project default to **additive** work: new modules/files "
        "with **new filenames**, usually `artifact_type: generated` for TB/helpers.\n"
        "- Do **not** call createFile with the same filename as an existing spec/rtl artifact unless "
        "the user explicitly asked to replace or update that file (pass `replace_existing: true`).\n"
        "- To change existing RTL, use readFile + applyCodeToFile on the artifact id above, not createFile.\n"
        "- Call listFiles if this inventory seems stale."
    )
    return "\n".join(lines)


def enrich_project_context(
    db: Session,
    context: dict[str, Any],
    *,
    user_message: str = "",
) -> dict[str, Any]:
    """Attach server-built workspace/spec context to any project chat turn."""
    if not isinstance(context, dict):
        return context

    project_id = context.get("project_id")
    if not project_id:
        return context

    manifest = build_project_workspace_manifest(db, str(project_id))
    context["project_artifact_manifest"] = manifest
    context["workspace_manifest_summary"] = format_manifest_for_prompt(manifest)

    active_spec_id = manifest.get("active_spec_artifact_id")
    if active_spec_id:
        try:
            from database.models import Project, ProjectArtifact
            from services.document_context import (
                ensure_spec_document_index,
                get_index_markdown_for_prompt,
            )

            project = db.query(Project).filter(Project.id == str(project_id)).first()
            spec_art = (
                db.query(ProjectArtifact)
                .filter(ProjectArtifact.id == str(active_spec_id))
                .first()
            )
            if project and spec_art and Path(spec_art.file_path).exists():
                ensure_spec_document_index(
                    organization_id=str(project.organization_id),
                    project_id=str(project.id),
                    artifact_id=str(spec_art.id),
                    file_path=spec_art.file_path,
                    filename=spec_art.filename,
                    checksum_sha256=str(spec_art.checksum_sha256 or ""),
                )
                index_md = get_index_markdown_for_prompt(
                    organization_id=str(project.organization_id),
                    project_id=str(project.id),
                    artifact_id=str(active_spec_id),
                )
                if index_md:
                    context["active_spec_document_index"] = index_md
        except Exception:
            pass

    if not context.get("workspace_files"):
        context["workspace_files"] = [
            f"{a['filename']} ({a['artifact_type']}{' active' if a.get('is_active') else ''})"
            for a in (manifest.get("artifacts") or [])[:20]
        ]

    if manifest.get("has_active_rtl") or manifest.get("has_active_spec"):
        context.setdefault("workspace_mode", "extend_existing")
    else:
        context.setdefault("workspace_mode", "greenfield")

    if user_message and user_message_requests_overwrite(user_message):
        context["allow_overwrite_active_rtl"] = True

    try:
        from services.codebase_graph.manager import get_build_status

        graph_status = get_build_status(db, str(project_id))
        context["codebase_graph"] = graph_status
        if graph_status.get("status") == "ready" and graph_status.get("report_excerpt"):
            context["codebase_graph_report_excerpt"] = graph_status["report_excerpt"]
    except Exception:
        pass

    return context


def enrich_rtl_designer_context(
    db: Session,
    context: dict[str, Any],
    *,
    user_message: str = "",
) -> dict[str, Any]:
    """Attach server-built workspace manifest to RTL Designer context."""
    if not isinstance(context, dict):
        return context

    mode = str(context.get("mode") or "").strip().lower()
    if mode != "rtl_designer":
        return context

    return enrich_project_context(db, context, user_message=user_message)


def find_existing_artifact_by_name(
    db: Session,
    project_id: str,
    filename: str,
    artifact_type: str,
) -> Optional[ProjectArtifact]:
    safe = (filename or "").strip()
    if not safe:
        return None
    return (
        db.query(ProjectArtifact)
        .filter(
            ProjectArtifact.project_id == project_id,
            ProjectArtifact.artifact_type == artifact_type,
            ProjectArtifact.filename == safe,
        )
        .order_by(ProjectArtifact.revision.desc())
        .first()
    )


def validate_create_file_policy(
    db: Session,
    *,
    project_id: str,
    filename: str,
    artifact_type: str,
    context: Optional[dict[str, Any]] = None,
    replace_existing: bool = False,
) -> None:
    """
    Raise ValueError when createFile would supersede an existing workspace file
    without explicit replace permission.
    """
    ctx = dict(context) if isinstance(context, dict) else {}
    if not ctx.get("project_artifact_manifest"):
        ctx["project_artifact_manifest"] = build_project_workspace_manifest(db, project_id)
    existing = find_existing_artifact_by_name(db, project_id, filename, artifact_type)
    if not existing:
        return

    allow = bool(replace_existing or ctx.get("allow_overwrite_active_rtl"))
    if allow:
        return

    manifest = ctx.get("project_artifact_manifest") or {}
    active_ids = {
        manifest.get("active_rtl_artifact_id"),
        manifest.get("active_spec_artifact_id"),
    }
    is_active = existing.id in active_ids

    hint = (
        f"A file named `{filename}` already exists ({artifact_type}, id={existing.id}). "
        "Do not create a new revision unless the user asked to replace it. "
        "Use a new filename (e.g. `my_uart.sv`) with artifact_type `generated`, "
        "or use readFile + applyCodeToFile to edit the existing artifact, "
        "or pass replace_existing=true only after explicit user consent."
    )
    if is_active:
        hint += " This file is the ACTIVE workspace artifact — overwriting it changes what verification uses."
    raise ValueError(hint)


def manifest_json_for_context(manifest: dict[str, Any], *, max_chars: int = 8000) -> str:
    try:
        raw = json.dumps(manifest, indent=0)
    except (TypeError, ValueError):
        return ""
    return raw if len(raw) <= max_chars else raw[:max_chars] + "…"
