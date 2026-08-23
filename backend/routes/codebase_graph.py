"""REST routes for project codebase knowledge graphs."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse, PlainTextResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from database.database import get_db
from database.models import Project, User
from routes.api import _get_current_user, _require_project_access
from services.codebase_graph.builder import graphify_available
from services.codebase_graph.manager import enqueue_build, get_build_status, resolve_project_corpus_roots
from services.codebase_graph.query import query_codebase_graph
from services.codebase_graph.store import get_codebase_graph_dir, read_report

router = APIRouter(tags=["codebase-graph"])


class GraphQueryRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=2000)
    depth: int = Field(default=3, ge=1, le=6)
    mode: str = Field(default="bfs")


@router.get("/projects/{project_id}/codebase-graph/status")
def codebase_graph_status(
    project_id: str,
    current_user: User = Depends(_get_current_user),
    db: Session = Depends(get_db),
):
    _require_project_access(db, current_user, project_id)
    return get_build_status(db, project_id)


@router.get("/projects/{project_id}/codebase-graph/report")
def codebase_graph_report(
    project_id: str,
    current_user: User = Depends(_get_current_user),
    db: Session = Depends(get_db),
):
    _require_project_access(db, current_user, project_id)
    roots = resolve_project_corpus_roots(db, project_id)
    if not roots:
        raise HTTPException(status_code=404, detail="Project not found")
    graph_dir = get_codebase_graph_dir(roots["organization_id"], project_id)
    text = read_report(graph_dir)
    if not text:
        raise HTTPException(status_code=404, detail="Graph report not available")
    return PlainTextResponse(text, media_type="text/markdown; charset=utf-8")


@router.get("/projects/{project_id}/codebase-graph/html")
def codebase_graph_html(
    project_id: str,
    current_user: User = Depends(_get_current_user),
    db: Session = Depends(get_db),
):
    _require_project_access(db, current_user, project_id)
    roots = resolve_project_corpus_roots(db, project_id)
    if not roots:
        raise HTTPException(status_code=404, detail="Project not found")
    html_path = get_codebase_graph_dir(roots["organization_id"], project_id) / "graph.html"
    if not html_path.exists():
        raise HTTPException(status_code=404, detail="Graph HTML not available")
    return FileResponse(html_path, media_type="text/html")


@router.get("/projects/{project_id}/codebase-graph/json")
def codebase_graph_json(
    project_id: str,
    current_user: User = Depends(_get_current_user),
    db: Session = Depends(get_db),
):
    _require_project_access(db, current_user, project_id)
    roots = resolve_project_corpus_roots(db, project_id)
    if not roots:
        raise HTTPException(status_code=404, detail="Project not found")
    json_path = get_codebase_graph_dir(roots["organization_id"], project_id) / "graph.json"
    if not json_path.exists():
        raise HTTPException(status_code=404, detail="Graph JSON not available")
    return FileResponse(json_path, media_type="application/json")


@router.post("/projects/{project_id}/codebase-graph/rebuild")
def codebase_graph_rebuild(
    project_id: str,
    current_user: User = Depends(_get_current_user),
    db: Session = Depends(get_db),
):
    _require_project_access(db, current_user, project_id)
    return enqueue_build(db, project_id, trigger="manual_rebuild", force=True)


@router.post("/projects/{project_id}/codebase-graph/query")
def codebase_graph_query(
    project_id: str,
    body: GraphQueryRequest,
    current_user: User = Depends(_get_current_user),
    db: Session = Depends(get_db),
):
    _require_project_access(db, current_user, project_id)
    if not graphify_available():
        raise HTTPException(
            status_code=503,
            detail="Codebase knowledge graph is unavailable in this build.",
        )
    roots = resolve_project_corpus_roots(db, project_id)
    if not roots:
        raise HTTPException(status_code=404, detail="Project not found")
    return query_codebase_graph(
        roots["organization_id"],
        project_id,
        body.question,
        depth=body.depth,
        mode=body.mode,
    )
