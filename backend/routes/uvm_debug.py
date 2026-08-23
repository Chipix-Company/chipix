"""External UVM log upload, diagnosis, and repair proposal endpoints."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional
import uuid

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy.orm import Session

from database.database import get_db
from database.models import ProjectArtifact, Project, User
from routes.api import (
    _artifact_dir,
    _get_current_user,
    _guess_content_type,
    _next_artifact_revision,
    _require_project_access,
    _safe_filename,
    _serialize_patch_proposal,
    _serialize_project_artifact,
    _sha256_bytes,
)
from services.mental_model import get_latest_mental_model_revision
from services.mental_model.living_agent import observe_mental_model_event
from services.verification.uvm_log_parser import parse_uvm_logs
from services.verification.uvm_log_repair import (
    build_uvm_repair_candidates,
    persist_repair_patch_proposals,
)


router = APIRouter(prefix="/api/v1/projects", tags=["uvm-debug"])

MAX_LOG_UPLOAD_BYTES = 10 * 1024 * 1024


@router.post("/{project_id}/uvm-debug/logs")
async def upload_uvm_debug_logs(
    project_id: str,
    files: list[UploadFile] = File(...),
    run_id: Optional[str] = Form(default=None),
    generated_artifact_ids: Optional[str] = Form(default=None),
    simulator: str = Form(default="auto"),
    current_user: User = Depends(_get_current_user),
    db: Session = Depends(get_db),
):
    """Upload external simulator logs and create safe UVM repair proposals."""

    project = _require_project_access(db, current_user, project_id)
    if not files:
        raise HTTPException(status_code=400, detail="Upload at least one simulator log file.")

    log_payloads: list[tuple[str, bytes, str]] = []
    for upload in files:
        raw = await upload.read()
        if not raw:
            continue
        if len(raw) > MAX_LOG_UPLOAD_BYTES:
            raise HTTPException(
                status_code=413,
                detail=f"Log file {upload.filename or 'upload'} is too large.",
            )
        log_payloads.append(
            (
                _safe_filename(upload.filename or "uvm_simulator.log", "uvm_simulator.log"),
                raw,
                upload.content_type or "text/plain",
            )
        )

    if not log_payloads:
        raise HTTPException(status_code=400, detail="Uploaded log content is empty.")

    analysis = parse_uvm_logs((raw for _name, raw, _ctype in log_payloads), simulator=simulator)
    log_artifact = _create_log_artifact(
        db,
        project,
        current_user,
        log_payloads,
        metadata={
            "provided_as": "uvm_external_simulator_log",
            "run_id": run_id or "",
            "simulator": analysis.tool,
            "analysis": analysis.to_dict(),
        },
    )

    generated_artifacts = _resolve_generated_artifacts(
        db,
        project,
        generated_artifact_ids,
    )
    candidates = (
        build_uvm_repair_candidates(
            analysis=analysis,
            generated_artifacts=generated_artifacts,
        )
        if analysis.status == "failed"
        else []
    )
    proposals = persist_repair_patch_proposals(
        db,
        project=project,
        user=current_user,
        candidates=candidates,
        log_artifact_id=log_artifact.id,
        analysis=analysis,
    )

    latest_model = get_latest_mental_model_revision(db, project.id)
    observation = None
    if latest_model:
        event_type = "uvm.external_run.passed" if analysis.status == "passed" else "uvm.log.diagnosed"
        observation = await observe_mental_model_event(
            db,
            model_revision=latest_model,
            user=current_user,
            event_type=event_type,
            payload={
                "verification_type": "uvm",
                "agent": "uvm_log_repair",
                "run_id": run_id or "",
                "log_artifact_id": log_artifact.id,
                "analysis": analysis.to_dict(),
                "patch_proposal_ids": [proposal.id for proposal in proposals],
                "trusted": analysis.status == "passed",
                "file_count": len(generated_artifacts),
                "artifact_ids": [log_artifact.id],
            },
            apply_approved_intent=False,
        )

    closure_report = _build_closure_report(project, analysis, log_artifact, proposals)
    return {
        "project_id": project.id,
        "run_id": run_id,
        "log_artifact": _serialize_project_artifact(log_artifact),
        "analysis": analysis.to_dict(),
        "patch_proposals": [_serialize_patch_proposal(proposal) for proposal in proposals],
        "closure_report": closure_report,
        "mental_model_observation": observation,
        "next_action": (
            "No blocking UVM errors were found. Review the closure report."
            if analysis.status == "passed"
            else "Review and approve the proposed patches, rerun your simulator, then upload the next log."
        ),
    }


def _create_log_artifact(
    db: Session,
    project: Project,
    user: User,
    log_payloads: list[tuple[str, bytes, str]],
    *,
    metadata: dict,
) -> ProjectArtifact:
    if len(log_payloads) == 1:
        filename, raw_bytes, content_type = log_payloads[0]
    else:
        filename = "uvm_external_logs.txt"
        content_type = "text/plain"
        chunks = []
        for name, raw, _ctype in log_payloads:
            chunks.append(f"\n===== {name} =====\n".encode("utf-8") + raw)
        raw_bytes = b"\n".join(chunks)

    revision = _next_artifact_revision(db, project.id, "verification_log")
    artifact_id = str(uuid.uuid4())
    safe_name = _safe_filename(filename, "uvm_external.log")
    storage_filename = f"v{revision:04d}_{artifact_id}_{safe_name}"
    artifact_path = _artifact_dir(project.organization_id, project.id, "verification_log") / storage_filename
    artifact_path.write_bytes(raw_bytes)

    artifact = ProjectArtifact(
        id=artifact_id,
        organization_id=project.organization_id,
        project_id=project.id,
        user_id=user.id,
        artifact_type="verification_log",
        revision=revision,
        source="uvm_debug_upload",
        file_path=str(artifact_path),
        checksum_sha256=_sha256_bytes(raw_bytes),
        filename=safe_name,
        content_type=content_type or _guess_content_type(Path(safe_name)),
        size_bytes=len(raw_bytes),
        metadata_json=json.dumps(metadata, default=str),
    )
    db.add(artifact)
    db.commit()
    db.refresh(artifact)
    return artifact


def _resolve_generated_artifacts(
    db: Session,
    project: Project,
    generated_artifact_ids: Optional[str],
) -> list[ProjectArtifact]:
    ids = _parse_artifact_ids(generated_artifact_ids)
    query = db.query(ProjectArtifact).filter(
        ProjectArtifact.project_id == project.id,
        ProjectArtifact.artifact_type == "generated",
    )
    if ids:
        query = query.filter(ProjectArtifact.id.in_(ids))
    return query.order_by(ProjectArtifact.created_at.desc()).limit(300).all()


def _parse_artifact_ids(raw: Optional[str]) -> list[str]:
    if not raw:
        return []
    try:
        value = json.loads(raw)
    except Exception:
        value = raw.split(",")
    if not isinstance(value, list):
        return []
    ids: list[str] = []
    for item in value:
        text = str(item or "").split("::", 1)[0].strip()
        if text and text not in ids:
            ids.append(text)
    return ids


def _build_closure_report(
    project: Project,
    analysis,
    log_artifact: ProjectArtifact,
    proposals,
) -> dict:
    return {
        "project_id": project.id,
        "status": "externally_validated" if analysis.status == "passed" else "needs_repair",
        "simulator": analysis.tool,
        "log_artifact_id": log_artifact.id,
        "blocking_errors": analysis.blocking_errors,
        "root_causes": [cause.to_dict() for cause in analysis.root_causes],
        "patch_proposal_ids": [proposal.id for proposal in proposals],
        "summary": (
            "External UVM log has no blocking errors."
            if analysis.status == "passed"
            else analysis.summary
        ),
    }
