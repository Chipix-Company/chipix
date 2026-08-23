"""Orchestration: resolve corpus, enqueue background builds, expose status."""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Any, Optional

from sqlalchemy.orm import Session

from database.models import Project, ProjectArtifact, ProjectArtifactPointer
from services.codebase_graph.builder import build_codebase_graph, graphify_available
from services.codebase_graph.schemas import BuildStatus
from services.codebase_graph.store import build_status_dto, get_codebase_graph_dir
from services.rtl_project_package import find_extracted_rtl_root

logger = logging.getLogger(__name__)

_BUILD_LOCK = threading.Lock()
_ACTIVE_BUILDS: set[str] = set()


def resolve_project_corpus_roots(
    db: Session,
    project_id: str,
) -> dict[str, Any]:
    """Return RTL root, spec path, and org id for a project."""

    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        return {}

    pointer = (
        db.query(ProjectArtifactPointer)
        .filter(ProjectArtifactPointer.project_id == project_id)
        .first()
    )
    rtl_art = None
    spec_art = None
    if pointer:
        if pointer.active_rtl_artifact_id:
            rtl_art = (
                db.query(ProjectArtifact)
                .filter(ProjectArtifact.id == pointer.active_rtl_artifact_id)
                .first()
            )
        if pointer.active_spec_artifact_id:
            spec_art = (
                db.query(ProjectArtifact)
                .filter(ProjectArtifact.id == pointer.active_spec_artifact_id)
                .first()
            )

    rtl_root: Optional[Path] = None
    if rtl_art and rtl_art.file_path:
        rtl_path = Path(rtl_art.file_path)
        extracted = find_extracted_rtl_root(rtl_path)
        rtl_root = extracted or (rtl_path.parent if rtl_path.is_file() else rtl_path)

    spec_path: Optional[Path] = None
    if spec_art and spec_art.file_path:
        spec_path = Path(spec_art.file_path)

    repo_root = Path(__file__).resolve().parents[3]
    if rtl_root is None and spec_path is None:
        rtl_root = repo_root

    return {
        "organization_id": str(project.organization_id),
        "project_id": str(project.id),
        "rtl_root": rtl_root,
        "spec_path": spec_path,
        "repo_root": repo_root,
    }


def get_build_status(db: Session, project_id: str) -> dict[str, Any]:
    roots = resolve_project_corpus_roots(db, project_id)
    if not roots:
        return {"project_id": project_id, "status": BuildStatus.PENDING.value}
    graph_dir = get_codebase_graph_dir(
        roots["organization_id"],
        project_id,
    )
    return build_status_dto(project_id, graph_dir).to_dict()


def _run_build_thread(
    project_id: str,
    organization_id: str,
    rtl_root: Path,
    spec_path: Optional[Path],
    trigger: str,
) -> None:
    graph_dir = get_codebase_graph_dir(organization_id, project_id)
    try:
        build_codebase_graph(
            project_id=project_id,
            organization_id=organization_id,
            graph_dir=graph_dir,
            rtl_root=rtl_root,
            spec_path=spec_path,
            trigger=trigger,
        )
    finally:
        with _BUILD_LOCK:
            _ACTIVE_BUILDS.discard(project_id)


def enqueue_build(
    db: Session,
    project_id: str,
    *,
    trigger: str = "upload",
    force: bool = False,
) -> dict[str, Any]:
    """Start a background graph build if not already running."""

    # The codebase knowledge graph needs the optional `graphify` package. When it is not
    # bundled, skip cleanly — never spawn a worker thread that would raise an uncaught
    # ModuleNotFoundError (which Sentry would report as an error).
    if not graphify_available():
        return {"status": BuildStatus.PENDING.value, "message": "codebase_graph_unavailable"}

    roots = resolve_project_corpus_roots(db, project_id)
    if not roots:
        return {"status": BuildStatus.ERROR.value, "error": "project_not_found"}

    rtl_root = roots.get("rtl_root")
    if rtl_root is None or not Path(rtl_root).exists():
        return {"status": BuildStatus.PENDING.value, "message": "no_rtl_root"}

    with _BUILD_LOCK:
        if project_id in _ACTIVE_BUILDS and not force:
            return get_build_status(db, project_id)
        _ACTIVE_BUILDS.add(project_id)

    thread = threading.Thread(
        target=_run_build_thread,
        args=(
            project_id,
            roots["organization_id"],
            Path(rtl_root),
            Path(roots["spec_path"]) if roots.get("spec_path") else None,
            trigger,
        ),
        daemon=True,
        name=f"codebase-graph-{project_id[:8]}",
    )
    thread.start()
    return {"status": BuildStatus.BUILDING.value, "trigger": trigger}
