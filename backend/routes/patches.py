"""
Patch Proposals API Routes — REST endpoints for patch management.

Endpoints:
    GET    /api/v1/projects/{id}/patches            — List pending patches
    GET    /api/v1/projects/{id}/patches/{pid}       — Get patch details
    POST   /api/v1/projects/{id}/patches/{pid}/approve — Approve + apply
    POST   /api/v1/projects/{id}/patches/{pid}/reject  — Reject with reason
"""

from __future__ import annotations

import json
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from database.database import get_db

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/projects", tags=["patches"])


class RejectRequest(BaseModel):
    reason: Optional[str] = ""


@router.get("/{project_id}/patches")
async def list_patches(
    project_id: str,
    db: Session = Depends(get_db),
):
    """List all pending patch proposals for a project."""
    from services.verification.patch_service import list_pending_patches
    return list_pending_patches(project_id, db)


@router.get("/{project_id}/patches/{patch_id}")
async def get_patch(
    project_id: str,
    patch_id: str,
    db: Session = Depends(get_db),
):
    """Get full patch details including diff."""
    try:
        from database.models import PatchProposal
        row = db.query(PatchProposal).filter(
            PatchProposal.id == patch_id,
            PatchProposal.project_id == project_id,
        ).first()
        if not row:
            raise HTTPException(status_code=404, detail="Patch not found")
        return {
            "id": row.id,
            "title": row.title,
            "source_agent": row.source_agent,
            "reason": row.reason,
            "status": row.status,
            "diff_text": row.diff_text,
            "metadata": json.loads(row.metadata_json) if row.metadata_json else {},
            "created_at": str(row.created_at),
            "updated_at": str(row.updated_at),
        }
    except HTTPException:
        raise
    except Exception:
        logger.exception("Patch proposal retrieval failed")
        raise HTTPException(status_code=500, detail="Patch proposal retrieval failed")


@router.post("/{project_id}/patches/{patch_id}/approve")
async def approve_patch(
    project_id: str,
    patch_id: str,
    db: Session = Depends(get_db),
):
    """Approve and auto-apply a patch."""
    from services.verification.patch_service import approve_patch as do_approve
    result = do_approve(patch_id, db)
    if result["status"] == "error":
        raise HTTPException(status_code=400, detail=result["message"])
    return result


@router.post("/{project_id}/patches/{patch_id}/reject")
async def reject_patch(
    project_id: str,
    patch_id: str,
    request: RejectRequest,
    db: Session = Depends(get_db),
):
    """Reject a patch with optional reason."""
    from services.verification.patch_service import reject_patch as do_reject
    return do_reject(patch_id, request.reason or "", db)
