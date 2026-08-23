"""Project-scoped SystemVerilog lint API (svls-backed)."""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from database.database import get_db
from database.models import User
from routes.api import _check_license_validity, _get_current_user, _require_project_access
from services.sv_lint.config import (
    ensure_project_lint_config,
    project_artifacts_root,
    safe_project_relative_path,
)
from services.sv_lint.service import get_svls_lint_service
from services.sv_lint.toolchain import svls_available

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["sv-lint"])


class SvLintRequest(BaseModel):
    filepath: str = Field(..., min_length=1)
    content: str = ""
    version: Optional[int] = None


@router.post("/projects/{project_id}/lint")
async def lint_project_source(
    project_id: str,
    body: SvLintRequest,
    current_user: User = Depends(_get_current_user),
    db: Session = Depends(get_db),
):
    _check_license_validity()
    project = _require_project_access(db, current_user, project_id)

    if not svls_available():
        raise HTTPException(
            status_code=503,
            detail={
                "message": "svls is not installed. Install via `cargo install svls` or bundle runtime/bin/svls.exe.",
                "tool_available": False,
            },
        )

    project_root = project_artifacts_root(
        organization_id=project.organization_id,
        project_id=project.id,
    )
    ensure_project_lint_config(project_root)

    service = get_svls_lint_service()
    try:
        relative_path = safe_project_relative_path(body.filepath)
        result = await asyncio.to_thread(
            service.lint_source,
            filepath=relative_path.as_posix(),
            content=body.content,
            project_root=project_root,
            version=body.version or 1,
        )
    except Exception as exc:
        logger.exception("Lint failed for project %s", project_id)
        raise HTTPException(status_code=500, detail="SystemVerilog lint failed") from exc

    payload = result.to_dict()
    payload["version"] = body.version
    return payload
