"""
Mental Model API Routes — REST endpoints for mental model operations.

Endpoints:
    POST   /api/v1/projects/{id}/mental-models/build
    GET    /api/v1/projects/{id}/mental-models/latest     ← MentalModelViewer uses this
    GET    /api/v1/projects/{id}/mental-models/{revision}
    POST   /api/v1/projects/{id}/verify
    POST   /api/v1/projects/{id}/analyze
    POST   /api/v1/projects/{id}/verify/{type}
"""

from __future__ import annotations

import json
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from database.database import get_db
from database.models import MentalModelRevision

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/projects", tags=["mental-model"])


# ── Request/Response Models ───────────────────────────────────────


class BuildMentalModelRequest(BaseModel):
    target_module: Optional[str] = None
    root_path: Optional[str] = None
    spec_text: Optional[str] = None


class VerifyBlockRequest(BaseModel):
    target_module: Optional[str] = None
    verification_types: list[str] = ["all"]


class AnalyzeDesignRequest(BaseModel):
    target_module: Optional[str] = None


class RunVerificationRequest(BaseModel):
    target_module: Optional[str] = None
    verification_type: str = "unitsim"


# ── Helpers ───────────────────────────────────────────────────────


def _load_content(row: MentalModelRevision) -> dict:
    """Parse content_json safely."""
    try:
        content = json.loads(row.content_json or "{}")
    except Exception:
        content = {}
    # If wrapped as {content: {...}}, unwrap
    if isinstance(content, dict) and "content" in content and isinstance(content["content"], dict):
        inner = content["content"]
        merged = {k: v for k, v in content.items() if k != "content"}
        merged.update(inner)
        return merged
    return content


def _get_latest_revision(db: Session, project_id: str) -> Optional[MentalModelRevision]:
    return (
        db.query(MentalModelRevision)
        .filter(MentalModelRevision.project_id == project_id)
        .order_by(MentalModelRevision.revision.desc())
        .first()
    )


def _serialize_model_for_viewer(row: MentalModelRevision, content: dict) -> dict:
    """
    Return a shape that MentalModelViewer.normalizeMentalModelResponse() understands.

    The viewer does:
        const revision = raw.mental_model || raw;
        const content  = revision.content || raw.content || raw;
        const design   = content.design && ... ? content.design : content;

    So we return the content dict with top-level metadata merged in.
    """
    design = content.get("design", {})
    if not isinstance(design, dict):
        design = {}

    return {
        # Top-level metadata (viewer reads these directly from raw)
        "id": row.id,
        "revision": row.revision,
        "status": row.status,
        "summary_text": row.summary_text,
        "schema_version": row.schema_version,
        "created_at": str(row.created_at) if row.created_at else None,
        "parser_engine": content.get("parser_engine", "slang"),
        "parser_version": content.get("parser_version", ""),
        "parser_diagnostics": content.get("parser_diagnostics", []),

        # Design data (merged flat so normalizeMentalModelResponse works either way)
        "design": design,
        "top_module": design.get("top_module", ""),
        "modules": design.get("modules", []),
        "ports": design.get("ports", []),
        "parameters": design.get("parameters", []),
        "clock_domains": design.get("clock_domains", []),
        "fsms": design.get("fsms", []),
        "protocols": design.get("protocols", []),
        "hierarchy_tree": design.get("hierarchy_tree", {}),
        "sub_instances": design.get("sub_instances", []),
        "total_lines": design.get("total_lines", 0),
        "total_files": design.get("total_files", 0),
        "total_always_blocks": design.get("total_always_blocks", 0),
        "has_cdc_crossings": design.get("has_cdc_crossings", False),
        "arbitration_policies": design.get("arbitration_policies", []),
        "transaction_flows": design.get("transaction_flows", []),

        # Requirements / verification
        "requirements": content.get("requirements", []),
        "verification": content.get("verification", {}),
        "evidence": content.get("evidence", []),
        "open_questions": content.get("open_questions", []),
        "source_refs": design.get("source_refs", []),
        "source_files": (
            content.get("source_files")
            or design.get("source_files")
            or content.get("project_scan", {}).get("rtl_files", [])
            or []
        ),
        "risks": content.get("risks", []),
        "description": design.get("description", ""),
    }


# ── Endpoints ─────────────────────────────────────────────────────


@router.post("/{project_id}/mental-models/build")
async def build_mental_model_endpoint(
    project_id: str,
    request: BuildMentalModelRequest,
    db: Session = Depends(get_db),
):
    """Build a mental model from the project's RTL and spec files."""
    try:
        from agent_tools.mental_model_tools import handle_build_mental_model

        result = await handle_build_mental_model(
            project_id=project_id,
            target_module=request.target_module or "",
            db_session=db,
            root_path=request.root_path or "",
            spec_text=request.spec_text or "",
        )
        return json.loads(result)

    except Exception as e:
        logger.exception(f"Mental model build failed: {e}")
        try:
            from observability.sentry_config import capture_exception

            capture_exception(e, project_id=project_id, area="mental_model.build")
        except ImportError:
            pass
        raise HTTPException(status_code=500, detail="Mental model build failed")


@router.get("/{project_id}/mental-models/latest")
async def get_latest_mental_model(
    project_id: str,
    query: str = Query(default="overview", description="Query section"),
    db: Session = Depends(get_db),
):
    """
    Get the latest mental model for a project — used by MentalModelViewer.

    The `query` parameter is accepted but ignored; we always return the full
    content dict flattened into the shape the viewer expects. The viewer tabs
    (overview/modules/ports/etc.) filter the same response client-side.
    """
    row = _get_latest_revision(db, project_id)
    if not row:
        raise HTTPException(
            status_code=404,
            detail="No mental model found. Upload RTL and run 'Build Model' first.",
        )

    content = _load_content(row)
    return _serialize_model_for_viewer(row, content)


@router.get("/{project_id}/mental-models/{revision}")
async def get_mental_model_revision(
    project_id: str,
    revision: int,
    db: Session = Depends(get_db),
):
    """Get a specific mental model revision."""
    row = (
        db.query(MentalModelRevision)
        .filter(
            MentalModelRevision.project_id == project_id,
            MentalModelRevision.revision == revision,
        )
        .first()
    )
    if not row:
        raise HTTPException(status_code=404, detail=f"Revision {revision} not found")

    content = _load_content(row)
    return {
        "id": row.id,
        "revision": row.revision,
        "status": row.status,
        "summary": row.summary_text,
        "schema_version": row.schema_version,
        "created_at": str(row.created_at),
        "content": content,
    }


@router.post("/{project_id}/verify")
async def verify_block_endpoint(
    project_id: str,
    request: VerifyBlockRequest,
    db: Session = Depends(get_db),
):
    """Run end-to-end verification on a design block (legacy)."""
    try:
        from agent_tools.orchestrator_tool import handle_verify_block

        result = await handle_verify_block(
            project_id=project_id,
            target_module=request.target_module or "",
            verification_types=request.verification_types,
            db_session=db,
        )
        return json.loads(result)

    except Exception as e:
        logger.exception(f"Verification failed: {e}")
        raise HTTPException(status_code=500, detail="Verification failed")
async def analyze_design_endpoint(
    project_id: str,
    request: AnalyzeDesignRequest,
    db: Session = Depends(get_db),
):
    """Step 1: Analyze design — build mental model + return options."""
    try:
        from agent_tools.orchestrator_tool import handle_analyze_design

        result = await handle_analyze_design(
            project_id=project_id,
            target_module=request.target_module or "",
            db_session=db,
        )
        return json.loads(result)

    except Exception as e:
        logger.exception(f"Design analysis failed: {e}")
        raise HTTPException(status_code=500, detail="Design analysis failed")
async def run_verification_endpoint(
    project_id: str,
    verification_type: str,
    request: RunVerificationRequest,
    db: Session = Depends(get_db),
):
    """Step 2: Run a specific verification type after user has chosen."""
    if verification_type not in ("unitsim", "formal", "uvm", "all"):
        raise HTTPException(
            status_code=400,
            detail=f"Invalid type: {verification_type}. Use: unitsim, formal, uvm, all",
        )

    try:
        from agent_tools.orchestrator_tool import handle_run_verification

        result = await handle_run_verification(
            project_id=project_id,
            target_module=request.target_module or "",
            verification_type=verification_type,
            db_session=db,
        )
        return json.loads(result)

    except Exception as e:
        logger.exception(f"Verification run failed: {e}")
        raise HTTPException(status_code=500, detail="Verification run failed")
