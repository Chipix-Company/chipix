import difflib
import json
import os
import shutil
import uuid
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from database.database import get_db
from database.models import (
    Project,
    ProjectArtifact,
    ProjectArtifactPointer,
    Run,
    User,
)
from routes.api import _get_current_user
from services.mental_model import ensure_fresh_mental_model_revision
from services.runner import append_run_event, start_job
from services.rtl_project_package import (
    RtlArchiveError,
    extract_rtl_archive,
    is_rtl_archive_filename,
)

router = APIRouter(prefix="/api/v1/tools", tags=["agent-tools"])

from common.paths import outputs_dir

OUTPUTS_DIR = outputs_dir()
OUTPUTS_DIR.mkdir(exist_ok=True, parents=True)

_ARTIFACT_TYPE_TO_LANGUAGE = {
    "spec": "text",
    "rtl": "systemverilog",
    "generated": "systemverilog",
}


# ---------------------------------------------------------------------------
# Request / Response schemas
# ---------------------------------------------------------------------------


class ReadFileRequest(BaseModel):
    artifact_id: str = Field(..., description="UUID of the artifact to read")


class ListFilesRequest(BaseModel):
    project_id: str = Field(..., description="UUID of the project")


class CreateFileRequest(BaseModel):
    project_id: str = Field(..., description="UUID of the project")
    filename: str = Field(..., min_length=1, description="Name of the file")
    artifact_type: str = Field(..., description="Artifact type: spec | rtl | generated")
    content: str = Field(..., description="File content as text")


class ApplyCodeToFileRequest(BaseModel):
    artifact_id: str = Field(..., description="UUID of the artifact to modify")
    code: str = Field(..., description="Code content to apply")
    strategy: str = Field(
        ...,
        description="Apply strategy: replace_file | replace_selection | smart_insert",
    )
    old_content: Optional[str] = Field(
        None,
        description="Content to replace when strategy is replace_selection",
    )


class RunSimulationRequest(BaseModel):
    project_id: str = Field(..., description="UUID of the project")


class ReadSpecPagesRequest(BaseModel):
    project_id: Optional[str] = Field(None, description="UUID of the project")
    artifact_id: Optional[str] = Field(None, description="Spec artifact UUID")
    start_page: int = Field(..., ge=1, description="First page to read")
    end_page: int = Field(..., ge=1, description="Last page to read")


class ReadSpecSectionRequest(BaseModel):
    project_id: Optional[str] = Field(None, description="UUID of the project")
    artifact_id: Optional[str] = Field(None, description="Spec artifact UUID")
    section_title: str = Field(..., min_length=1, description="Section title")


class SearchSpecRequest(BaseModel):
    project_id: Optional[str] = Field(None, description="UUID of the project")
    artifact_id: Optional[str] = Field(None, description="Spec artifact UUID")
    query: str = Field(..., min_length=1, description="Search query")
    limit: int = Field(12, ge=1, le=50, description="Maximum hits")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _require_artifact(db: Session, artifact_id: str) -> ProjectArtifact:
    artifact = (
        db.query(ProjectArtifact).filter(ProjectArtifact.id == artifact_id).first()
    )
    if not artifact:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Artifact not found"
        )
    return artifact


def _require_project(db: Session, project_id: str) -> Project:
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Project not found"
        )
    return project


def _resolve_spec_artifact(
    db: Session,
    *,
    project_id: Optional[str],
    artifact_id: Optional[str],
) -> tuple[Project, ProjectArtifact]:
    artifact: ProjectArtifact | None = None
    project: Project | None = None

    if artifact_id:
        artifact = _require_artifact(db, artifact_id)
        if artifact.artifact_type != "spec":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="artifact_id must reference a spec artifact",
            )
        project = _require_project(db, artifact.project_id)
        if project_id and project.id != project_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="artifact_id does not belong to project_id",
            )
        return project, artifact

    if not project_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="project_id or artifact_id is required",
        )

    project = _require_project(db, project_id)
    pointer = (
        db.query(ProjectArtifactPointer)
        .filter(ProjectArtifactPointer.project_id == project.id)
        .first()
    )
    if not pointer or not pointer.active_spec_artifact_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No active spec artifact set for this project",
        )
    artifact = _require_artifact(db, pointer.active_spec_artifact_id)
    return project, artifact


def _ensure_spec_index(project: Project, artifact: ProjectArtifact) -> None:
    from services.document_context import ensure_spec_document_index

    ensure_spec_document_index(
        organization_id=project.organization_id,
        project_id=project.id,
        artifact_id=artifact.id,
        file_path=artifact.file_path,
        filename=artifact.filename,
        checksum_sha256=artifact.checksum_sha256,
    )


def _read_artifact_content(artifact: ProjectArtifact) -> str:
    artifact_path = Path(artifact.file_path)
    if not artifact_path.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Artifact file is missing from disk",
        )
    return artifact_path.read_text(encoding="utf-8", errors="replace")


def _write_artifact_content(artifact: ProjectArtifact, content: str) -> None:
    artifact_path = Path(artifact.file_path)
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    artifact_path.write_text(content, encoding="utf-8")


def _infer_language(artifact: ProjectArtifact) -> str:
    return _ARTIFACT_TYPE_TO_LANGUAGE.get(artifact.artifact_type, "text")


def _artifact_metadata(artifact: ProjectArtifact) -> dict:
    if not artifact.metadata_json:
        return {}

    try:
        data = json.loads(artifact.metadata_json)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _serialize_tool_artifact(
    artifact: ProjectArtifact,
    *,
    is_active: bool = False,
) -> dict:
    metadata = _artifact_metadata(artifact)
    relative_path = (
        metadata.get("relative_path")
        or metadata.get("source_relative_path")
        or artifact.filename
    )

    return {
        "id": artifact.id,
        "filename": artifact.filename,
        "artifact_type": artifact.artifact_type,
        "is_active": is_active,
        "created_at": artifact.created_at.isoformat() if artifact.created_at else None,
        "revision": artifact.revision,
        "content_type": artifact.content_type,
        "relative_path": relative_path,
        "metadata": metadata,
    }


def _artifact_dir(organization_id: str, project_id: str, artifact_type: str) -> Path:
    return OUTPUTS_DIR / "orgs" / organization_id / "projects" / project_id / "artifacts" / artifact_type


def _safe_filename(name: str, fallback: str = "untitled.txt") -> str:
    cleaned = Path(name).name.strip()
    if not cleaned or cleaned.startswith("."):
        return fallback
    return cleaned


def _sha256_bytes(data: bytes) -> str:
    import hashlib

    return hashlib.sha256(data).hexdigest()


def _materialize_rtl_for_run(source_path: Path, filename: str, input_dir: Path) -> Path:
    safe_name = _safe_filename(filename, "rtl_context.sv")
    stored_rtl_path = input_dir / f"rtl_{safe_name}"
    shutil.copy2(source_path, stored_rtl_path)

    if not is_rtl_archive_filename(safe_name):
        return stored_rtl_path

    extract_dir = input_dir / "rtl_project"
    try:
        extract_rtl_archive(stored_rtl_path, extract_dir)
    except RtlArchiveError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="RTL archive extraction failed") from exc
    return extract_dir


def _next_artifact_revision(db: Session, project_id: str, artifact_type: str) -> int:
    from sqlalchemy import func

    max_rev = (
        db.query(func.max(ProjectArtifact.revision))
        .filter(
            ProjectArtifact.project_id == project_id,
            ProjectArtifact.artifact_type == artifact_type,
        )
        .scalar()
    )
    return int(max_rev or 0) + 1


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("/readFile", summary="Read artifact file content")
def read_file(
    req: ReadFileRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(_get_current_user),
):
    """Read the content of an artifact from the database and return it as text."""
    artifact = _require_artifact(db, req.artifact_id)
    content = _read_artifact_content(artifact)
    metadata = _artifact_metadata(artifact)
    return {
        "success": True,
        "content": content,
        "filename": artifact.filename,
        "artifact_type": artifact.artifact_type,
        "relative_path": (
            metadata.get("relative_path")
            or metadata.get("source_relative_path")
            or artifact.filename
        ),
        "metadata": metadata,
        "language": _infer_language(artifact),
    }


@router.get("/projects/{project_id}/artifacts", summary="List project artifacts")
def list_project_artifacts(
    project_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(_get_current_user),
):
    """List all artifacts for a given project, sorted by created_at descending."""
    project = _require_project(db, project_id)

    pointer = (
        db.query(ProjectArtifactPointer)
        .filter(ProjectArtifactPointer.project_id == project.id)
        .first()
    )
    active_ids = set()
    if pointer:
        if pointer.active_spec_artifact_id:
            active_ids.add(pointer.active_spec_artifact_id)
        if pointer.active_rtl_artifact_id:
            active_ids.add(pointer.active_rtl_artifact_id)

    artifacts = (
        db.query(ProjectArtifact)
        .filter(ProjectArtifact.project_id == project.id)
        .order_by(ProjectArtifact.created_at.desc())
        .all()
    )

    files = [_serialize_tool_artifact(a, is_active=a.id in active_ids) for a in artifacts]

    return {"success": True, "files": files}


@router.post("/listFiles", summary="List project artifacts")
def list_files(
    req: ListFilesRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(_get_current_user),
):
    """List all artifacts for a given project."""
    project = _require_project(db, req.project_id)

    artifacts = (
        db.query(ProjectArtifact)
        .filter(ProjectArtifact.project_id == project.id)
        .order_by(ProjectArtifact.artifact_type.asc(), ProjectArtifact.revision.desc())
        .all()
    )

    pointer = (
        db.query(ProjectArtifactPointer)
        .filter(ProjectArtifactPointer.project_id == project.id)
        .first()
    )
    active_ids = set()
    if pointer:
        if pointer.active_spec_artifact_id:
            active_ids.add(pointer.active_spec_artifact_id)
        if pointer.active_rtl_artifact_id:
            active_ids.add(pointer.active_rtl_artifact_id)

    files = [_serialize_tool_artifact(a, is_active=a.id in active_ids) for a in artifacts]

    return {"success": True, "files": files}


@router.post(
    "/createFile",
    summary="Create a new artifact file",
    status_code=status.HTTP_201_CREATED,
)
def create_file(
    req: CreateFileRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(_get_current_user),
):
    """Create a new artifact in the specified project."""
    project = _require_project(db, req.project_id)

    if req.artifact_type not in {"spec", "rtl", "generated"}:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="artifact_type must be one of: spec, rtl, generated",
        )

    raw_bytes = req.content.encode("utf-8")
    safe_name = _safe_filename(req.filename, f"{req.artifact_type}.txt")
    checksum = _sha256_bytes(raw_bytes)
    revision = _next_artifact_revision(db, project.id, req.artifact_type)
    artifact_id = str(uuid.uuid4())
    storage_filename = f"v{revision:04d}_{artifact_id}_{safe_name}"
    artifact_path = (
        _artifact_dir(project.organization_id, project.id, req.artifact_type)
        / storage_filename
    )

    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    with open(artifact_path, "wb") as fh:
        fh.write(raw_bytes)

    artifact = ProjectArtifact(
        id=artifact_id,
        organization_id=project.organization_id,
        project_id=project.id,
        user_id=project.owner_user_id,
        artifact_type=req.artifact_type,
        revision=revision,
        source="agent",
        file_path=str(artifact_path),
        checksum_sha256=checksum,
        filename=safe_name,
        content_type="text/plain",
        size_bytes=len(raw_bytes),
    )
    db.add(artifact)

    if req.artifact_type in {"spec", "rtl"}:
        pointer = (
            db.query(ProjectArtifactPointer)
            .filter(ProjectArtifactPointer.project_id == project.id)
            .first()
        )
        if not pointer:
            pointer = ProjectArtifactPointer(project_id=project.id)
            db.add(pointer)

        if req.artifact_type == "spec":
            pointer.active_spec_artifact_id = artifact.id
        else:
            pointer.active_rtl_artifact_id = artifact.id

    db.commit()
    db.refresh(artifact)

    from services.sv_lint.agent_gate import apply_lint_to_response

    response = apply_lint_to_response(
        {
            "success": True,
            "artifact": {
                "id": artifact.id,
                "filename": artifact.filename,
                "artifact_type": artifact.artifact_type,
                "revision": artifact.revision,
                "project_id": artifact.project_id,
                "file_path": str(artifact_path),
            },
        },
        file_path=artifact_path,
        filename=safe_name,
        artifact_type=req.artifact_type,
    )
    if not response.get("success"):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=response.get("error"),
        )
    return response


@router.post("/applyCodeToFile", summary="Apply code changes to an artifact")
def apply_code_to_file(
    req: ApplyCodeToFileRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(_get_current_user),
):
    """Apply code to an existing artifact using the specified strategy.

    Strategies:
    - replace_file: Replace entire file content with `code`.
    - replace_selection: Replace the first occurrence of `old_content` with `code`.
    - smart_insert: Append `code` to the end of the file.
    """
    artifact = _require_artifact(db, req.artifact_id)

    if req.strategy not in {"replace_file", "replace_selection", "smart_insert"}:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="strategy must be one of: replace_file, replace_selection, smart_insert",
        )

    original_content = _read_artifact_content(artifact)

    if req.strategy == "replace_file":
        new_content = req.code

    elif req.strategy == "replace_selection":
        if req.old_content is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="old_content is required for replace_selection strategy",
            )
        if req.old_content not in original_content:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="old_content not found in the current file",
            )
        new_content = original_content.replace(req.old_content, req.code, 1)

    elif req.strategy == "smart_insert":
        if original_content and not original_content.endswith("\n"):
            new_content = original_content + "\n" + req.code
        else:
            new_content = original_content + req.code

    _write_artifact_content(artifact, new_content)

    new_bytes = new_content.encode("utf-8")
    artifact.checksum_sha256 = _sha256_bytes(new_bytes)
    artifact.size_bytes = len(new_bytes)
    db.commit()

    diff_lines = difflib.unified_diff(
        original_content.splitlines(keepends=True),
        new_content.splitlines(keepends=True),
        fromfile=artifact.filename,
        tofile=artifact.filename,
    )
    diff_text = "".join(diff_lines)

    from services.sv_lint.agent_gate import apply_lint_to_response

    response = apply_lint_to_response(
        {
            "success": True,
            "new_content": new_content,
            "diff": diff_text,
            "artifact": {
                "id": artifact.id,
                "filename": artifact.filename,
                "artifact_type": artifact.artifact_type,
                "file_path": artifact.file_path,
            },
        },
        file_path=artifact.file_path,
        filename=artifact.filename,
        artifact_type=artifact.artifact_type,
    )
    if not response.get("success"):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=response.get("error"),
        )
    return response


@router.post("/readSpecPages", summary="Read page-bounded spec text")
def read_spec_pages_tool(
    req: ReadSpecPagesRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(_get_current_user),
):
    project, artifact = _resolve_spec_artifact(
        db,
        project_id=req.project_id,
        artifact_id=req.artifact_id,
    )
    _ensure_spec_index(project, artifact)

    from services.document_context import read_spec_pages

    return read_spec_pages(
        organization_id=project.organization_id,
        project_id=project.id,
        artifact_id=artifact.id,
        start_page=req.start_page,
        end_page=req.end_page,
    )


@router.post("/readSpecSection", summary="Read a spec section by title")
def read_spec_section_tool(
    req: ReadSpecSectionRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(_get_current_user),
):
    project, artifact = _resolve_spec_artifact(
        db,
        project_id=req.project_id,
        artifact_id=req.artifact_id,
    )
    _ensure_spec_index(project, artifact)

    from services.document_context import read_spec_section

    return read_spec_section(
        organization_id=project.organization_id,
        project_id=project.id,
        artifact_id=artifact.id,
        section_title=req.section_title,
    )


@router.post("/searchSpec", summary="Search indexed spec pages and sections")
def search_spec_tool(
    req: SearchSpecRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(_get_current_user),
):
    project, artifact = _resolve_spec_artifact(
        db,
        project_id=req.project_id,
        artifact_id=req.artifact_id,
    )
    _ensure_spec_index(project, artifact)

    from services.document_context import search_spec

    return search_spec(
        organization_id=project.organization_id,
        project_id=project.id,
        artifact_id=artifact.id,
        query=req.query,
        limit=req.limit,
    )


@router.post("/runSimulation", summary="Start a verification run")
def run_simulation(
    req: RunSimulationRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(_get_current_user),
):
    """Start a verification run using the active spec and RTL artifacts for the project."""
    project = _require_project(db, req.project_id)

    pointer = (
        db.query(ProjectArtifactPointer)
        .filter(ProjectArtifactPointer.project_id == project.id)
        .first()
    )

    if not pointer or not pointer.active_spec_artifact_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No active spec artifact set for this project",
        )
    if not pointer.active_rtl_artifact_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No active RTL artifact set for this project",
        )

    spec_artifact = (
        db.query(ProjectArtifact)
        .filter(ProjectArtifact.id == pointer.active_spec_artifact_id)
        .first()
    )
    rtl_artifact = (
        db.query(ProjectArtifact)
        .filter(ProjectArtifact.id == pointer.active_rtl_artifact_id)
        .first()
    )

    if not spec_artifact or not Path(spec_artifact.file_path).exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Active spec artifact file is missing",
        )
    if not rtl_artifact or not Path(rtl_artifact.file_path).exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Active RTL artifact file is missing",
        )

    run_id = str(uuid.uuid4())
    run_dir = OUTPUTS_DIR / "orgs" / project.organization_id / "projects" / project.id / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "inputs").mkdir(exist_ok=True)

    import shutil

    input_dir = run_dir / "inputs"
    spec_dest = input_dir / f"spec_{spec_artifact.filename}"
    shutil.copy2(spec_artifact.file_path, spec_dest)
    rtl_dest = _materialize_rtl_for_run(
        Path(rtl_artifact.file_path),
        rtl_artifact.filename,
        input_dir,
    )

    run = Run(
        id=run_id,
        organization_id=project.organization_id,
        project_id=project.id,
        user_id=project.owner_user_id,
        prompt_text=f"Tool-triggered run for project {project.name}",
        specification_type="artifact",
        status="running",
        gpu_used=True,
        logs_path=str(run_dir / "logs.json"),
        output_path=str(run_dir),
    )
    db.add(run)
    db.commit()

    owner = db.query(User).filter(User.id == project.owner_user_id).first()
    if not owner:
        run.status = "failed"
        run.verification_summary = "Project owner user is missing"
        db.add(run)
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Project owner user is missing",
        )

    try:
        append_run_event(
            run_id,
            "Checking source-grounded mental model freshness.",
            phase="mental_model.check",
        )
        mental_model, created, freshness, summary = ensure_fresh_mental_model_revision(
            db,
            project=project,
            user=owner,
            spec_artifact=spec_artifact,
            rtl_artifact=rtl_artifact,
        )
        run.mental_model_revision_id = mental_model.id
        db.add(run)
        db.commit()

        if created:
            append_run_event(
                run_id,
                (
                    "Mental model was rebuilt before verification "
                    f"({freshness.get('reason')})."
                ),
                phase="mental_model.stale",
                level="warning",
            )
            append_run_event(
                run_id,
                summary,
                phase="mental_model.generated",
            )
            append_run_event(
                run_id,
                f"Saved mental model revision {mental_model.revision} for verification run.",
                phase="mental_model.persisted",
            )
        else:
            append_run_event(
                run_id,
                (
                    f"Using mental model revision {mental_model.revision} "
                    "for this verification run."
                ),
                phase="mental_model.ready",
            )
    except Exception as exc:
        db.rollback()
        run = db.query(Run).filter(Run.id == run_id).first() or run
        run.status = "failed"
        run.verification_summary = "Mental model preparation failed"
        db.add(run)
        db.commit()
        append_run_event(
            run_id,
            f"Mental model preparation failed: {exc}",
            phase="mental_model.failed",
            level="error",
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Mental model preparation failed before verification run",
        ) from exc

    start_job(run_id, str(rtl_dest), str(spec_dest), str(run_dir))

    return {
        "success": True,
        "run_id": run_id,
        "mental_model_revision_id": run.mental_model_revision_id,
    }
