import asyncio
import hashlib
import io
import json
import logging
import mimetypes
import os
import shutil
import tempfile
import zipfile
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath
from typing import Any, List, Optional
import requests
from utils.desktop_activation import resolve_desktop_activation
from utils.hardware import get_machine_fingerprint


from fastapi import (
    APIRouter,
    Body,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
    status,
)
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from passlib.context import CryptContext
from pydantic import BaseModel, Field
from sqlalchemy import and_, func, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from database.database import SessionLocal, get_db
from database.enums import RunStatus
from database.models import (
    ChatMessage,
    ChatThread,
    ChatThreadState,
    ChatTokenUsage,
    MentalModelRevision,
    Organization,
    PatchProposal,
    Project,
    ProjectArtifact,
    ProjectArtifactPointer,
    Run,
    RunEvent,
    User,
)
from services.token_usage import (
    estimate_text_token_usage,
    get_organization_token_totals,
    get_project_token_usage,
    get_thread_token_usage,
    persist_chat_token_usage,
    serialize_token_usage,
    summarize_token_usage,
)
from llm_provider import DEFAULT_MODEL, PROVIDER
from services.chat_message_timestamps import chat_turn_timestamps
from services.runner import cancel_job, get_job_status, start_job
from services.eda import (
    create_simulation_job,
    detect_toolchain_status,
    refresh_toolchain_status,
    get_simulation_job,
    iter_simulation_events,
    read_waveform_slice,
    render_netlist_document,
)
from services.verilog_parser import build_module_graph, parse_verilog
from services.mental_model import (
    create_llm_enriched_mental_model_revision,
    create_mental_model_revision,
    evaluate_mental_model_freshness,
    get_latest_mental_model_revision,
    next_mental_model_revision,
)
from services.industry_dv import build_industry_readiness, build_vplan_from_mental_model
from services.rtl_project_package import (
    MAX_ARCHIVE_BYTES,
    MAX_ARCHIVE_FILES,
    RtlArchiveError,
    extract_rtl_archive,
    find_extracted_rtl_root,
    is_rtl_archive_filename,
    is_rtl_source_filename,
)
import original_core.core.ai_client as ai_client_module
from original_core.parsers.docx_parser import parse_docx
from original_core.parsers.pdf_parser import parse_pdf
from original_core.parsers.rtl_parser import parse_rtl
from original_core.parsers.txt_parser import parse_txt
from user_documentation import docs_index_sort_key, is_user_facing_doc

router = APIRouter(prefix="/api/v1", tags=["chipverify-studio"])
logger = logging.getLogger(__name__)

_DEFAULT_DEV_SECRET = "chipverify-local-dev-secret-change-me"
SECRET_KEY = (os.environ.get("CHIPVERIFY_SECRET_KEY") or "").strip()
if not SECRET_KEY or SECRET_KEY == _DEFAULT_DEV_SECRET:
    raise RuntimeError(
        "CHIPVERIFY_SECRET_KEY must be set to a non-default value before backend startup."
    )
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = int(
    os.environ.get("CHIPVERIFY_ACCESS_TOKEN_EXPIRE_MINUTES", "720")
)

# Use PBKDF2 to avoid runtime backend issues with bcrypt on some Python builds.
pwd_context = CryptContext(schemes=["pbkdf2_sha256"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login", auto_error=False)

from common.paths import outputs_dir

OUTPUTS_DIR = outputs_dir()
OUTPUTS_DIR.mkdir(exist_ok=True, parents=True)
DOCS_DIR = Path(__file__).parent.parent.parent / "Documentation"
MAX_ARTIFACT_PREVIEW_BYTES = int(
    os.environ.get("CHIPVERIFY_ARTIFACT_PREVIEW_BYTES", "200000")
)
MAX_SPEC_UPLOAD_PARSE_BYTES = int(
    os.environ.get("CHIPVERIFY_SPEC_UPLOAD_PARSE_BYTES", str(20 * 1024 * 1024))
)
MAX_CHAT_MESSAGE_PAGE_SIZE = max(
    1, int(os.environ.get("CHIPVERIFY_CHAT_MESSAGE_PAGE_SIZE_MAX", "200"))
)
DEFAULT_CHAT_MESSAGE_PAGE_SIZE = min(
    MAX_CHAT_MESSAGE_PAGE_SIZE,
    max(1, int(os.environ.get("CHIPVERIFY_CHAT_MESSAGE_PAGE_SIZE", "50"))),
)
RUN_SYNC_FINAL_STATUSES = {"completed", "failed", "blocked", "cancelled", "interrupted"}
GENERATED_SYNC_EXCLUDED_TOP_LEVEL_DIRS = {"inputs", "artifacts"}
MAX_RTL_DOT_SOURCE_CHARS = max(
    1000,
    int(os.environ.get("CHIPVERIFY_RTL_DOT_MAX_CHARS", "120000")),
)

AGENT_BLUEPRINTS = (
    {
        "id": "mental_model",
        "name": "Mental Model Agent",
        "initial": "M",
        "avatar_bg": "#58a6ff",
        "description": "Builds the source-grounded design-intent map used by every downstream agent.",
        "stage": "design-intent",
    },
    {
        "id": "unit_testing",
        "name": "Unit Testing Agent",
        "initial": "T",
        "avatar_bg": "#3fb950",
        "description": "Generates unit-level SystemVerilog tests and simulator-ready smoke flows.",
        "stage": "shift-left-simulation",
    },
    {
        "id": "formal",
        "name": "Formal Agent",
        "initial": "F",
        "avatar_bg": "#a371f7",
        "description": "Plans SVA assertions, assumptions, covers, and formal tool execution.",
        "stage": "formal-verification",
    },
    {
        "id": "design_reviewer",
        "name": "Spec Parser and Design Reviewer",
        "initial": "S",
        "avatar_bg": "#58a6ff",
        "description": "Parses active specs and validates architecture intent before simulation.",
        "stage": "pipeline-intelligence",
    },
    {
        "id": "auto_fixer",
        "name": "Debug Agent",
        "initial": "A",
        "avatar_bg": "#f85149",
        "description": "Performs failure triage, root-cause analysis, and patch proposal generation.",
        "stage": "debug-closure",
    },
    {
        "id": "uvm_generator",
        "name": "UVM Agent",
        "initial": "U",
        "avatar_bg": "#3fb950",
        "description": "Generates or upgrades UVM sequences, checkers, scoreboards, and coverage.",
        "stage": "verification-construction",
    },
    {
        "id": "coverage",
        "name": "Coverage Agent",
        "initial": "C",
        "avatar_bg": "#d29922",
        "description": "Maps requirements to functional coverage, gaps, waivers, and closure suggestions.",
        "stage": "coverage-closure",
    },
    {
        "id": "rtl_optimization",
        "name": "RTL Optimization Agent",
        "initial": "O",
        "avatar_bg": "#bc8cff",
        "description": "Uses lint, synthesis, and timing reports to propose safe PPA improvements.",
        "stage": "ppa-optimization",
    },
    {
        "id": "design_upgrade",
        "name": "Design Upgrade Agent",
        "initial": "D",
        "avatar_bg": "#39c5cf",
        "description": "Turns new requirements into RTL and verification collateral change proposals.",
        "stage": "requirement-delta",
    },
    {
        "id": "soc",
        "name": "SoC Agent",
        "initial": "S",
        "avatar_bg": "#ffab70",
        "description": "Plans SoC fabric, wrappers, register maps, and integration verification.",
        "stage": "soc-integration",
    },
    {
        "id": "signoff",
        "name": "Signoff Agent",
        "initial": "G",
        "avatar_bg": "#8ddb8c",
        "description": "Ranks structural RTL issues from lint, synthesis, CDC/RDC, and timing reports.",
        "stage": "pre-signoff",
    },
)

AGENT_EVENT_KEYWORDS = {
    "mental_model": (
        "mental",
        "intent",
        "architecture",
        "requirement",
        "context",
    ),
    "unit_testing": (
        "unit",
        "test",
        "simulation",
        "testbench",
        "vcd",
    ),
    "formal": (
        "formal",
        "sva",
        "assert",
        "assumption",
        "cover",
        "jasper",
    ),
    "design_reviewer": (
        "spec",
        "requirement",
        "design",
        "architecture",
        "parser",
    ),
    "auto_fixer": (
        "compile",
        "simulation",
        "assert",
        "error",
        "failed",
        "failure",
        "debug",
        "fix",
        "warning",
    ),
    "uvm_generator": (
        "uvm",
        "testbench",
        "driver",
        "monitor",
        "scoreboard",
        "sequence",
    ),
    "coverage": (
        "coverage",
        "coverpoint",
        "covergroup",
        "waiver",
        "hole",
    ),
    "rtl_optimization": (
        "ppa",
        "timing",
        "area",
        "power",
        "synthesis",
        "optimize",
    ),
    "design_upgrade": (
        "upgrade",
        "change",
        "requirement",
        "patch",
        "diff",
    ),
    "soc": (
        "soc",
        "fabric",
        "register",
        "wrapper",
        "interconnect",
    ),
    "signoff": (
        "signoff",
        "lint",
        "cdc",
        "rdc",
        "structural",
        "synthesis",
    ),
}

PLATFORM_PROFILE = {
    "product": "Chipix Studio",
    "mode": "local-first-on-prem",
    "theme": "eda-control-room",
    "positioning": "AI-native verification platform",
    "pipeline": [
        "Spec parsing",
        "RTL analysis",
        "UVM generation",
        "Compile and simulation",
        "Coverage and debug",
    ],
}


class RegisterRequest(BaseModel):
    email: str = Field(min_length=3, max_length=255)
    password: str = Field(min_length=8, max_length=128)
    full_name: str = Field(min_length=2, max_length=120)


class LoginRequest(BaseModel):
    email: str = Field(min_length=3, max_length=255)
    password: str = Field(min_length=8, max_length=128)


class ProjectCreateRequest(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    description: Optional[str] = Field(default=None, max_length=500)


class DebugRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=4000)


class ChatThreadCreateRequest(BaseModel):
    title: Optional[str] = Field(default=None, max_length=120)
    agent_name: Optional[str] = Field(default=None, max_length=120)
    thread_kind: Optional[str] = Field(default="main", max_length=32)
    parent_thread_id: Optional[str] = Field(default=None, max_length=64)


class ChatThreadUpdateRequest(BaseModel):
    title: Optional[str] = Field(default=None, max_length=120)
    agent_name: Optional[str] = Field(default=None, max_length=120)
    archived: Optional[bool] = None
    context_run_id: Optional[str] = Field(default=None, max_length=64)


class ChatAskRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=4000)
    context_run_id: Optional[str] = Field(default=None, max_length=64)
    message_history: Optional[list[dict[str, str]]] = Field(default=None)
    context: Optional[dict[str, Any]] = None


class ChatThreadRewindRequest(BaseModel):
    message_id: str = Field(min_length=1, max_length=64)
    artifact_ids: list[str] = Field(default_factory=list)


class ArtifactActivateRequest(BaseModel):
    spec_artifact_id: Optional[str] = Field(default=None, max_length=64)
    rtl_artifact_id: Optional[str] = Field(default=None, max_length=64)


class ArtifactRenameRequest(BaseModel):
    filename: str = Field(min_length=1, max_length=255)


class ArtifactContentUpdateRequest(BaseModel):
    content: str = Field(default="")


class ArtifactZipDownloadRequest(BaseModel):
    artifact_ids: list[str] = Field(default_factory=list)
    filename: Optional[str] = Field(default=None, max_length=255)


class RtlProjectMemberUpdateRequest(BaseModel):
    content: str = Field(default="")


class MentalModelBuildRequest(BaseModel):
    spec_artifact_id: Optional[str] = Field(default=None, max_length=64)
    rtl_artifact_id: Optional[str] = Field(default=None, max_length=64)
    target_module: Optional[str] = Field(default=None, max_length=128)


class PatchProposalCreateRequest(BaseModel):
    source_agent: str = Field(min_length=1, max_length=80)
    title: str = Field(min_length=1, max_length=180)
    reason: str = Field(min_length=1, max_length=4000)
    diff_text: str = Field(min_length=1)
    target_artifact_id: Optional[str] = Field(default=None, max_length=64)
    metadata: Optional[dict[str, Any]] = None


class PatchProposalDecisionRequest(BaseModel):
    status: str = Field(pattern="^(approved|rejected|superseded)$")
    note: Optional[str] = Field(default=None, max_length=1000)


class EdaNetlistRenderRequest(BaseModel):
    project_id: str = Field(min_length=1, max_length=64)
    filename: str = Field(min_length=1, max_length=255)
    source: str = Field(min_length=1)
    top_module: Optional[str] = Field(default=None, max_length=255)


class EdaSimulationCreateRequest(BaseModel):
    project_id: str = Field(min_length=1, max_length=64)
    filename: str = Field(default="design.sv", min_length=1, max_length=255)
    rtl_source: str = Field(min_length=1)
    testbench_source: Optional[str] = None
    top_module: str = Field(min_length=1, max_length=255)
    trace_format: str = Field(default="vcd", max_length=24)


def _slugify(value: str) -> str:
    return (
        value.lower()
        .replace("_", "-")
        .replace(" ", "-")
        .replace("/", "-")
        .replace("\\", "-")
        .strip("-")
    )


def _iso(value: Optional[datetime]) -> Optional[str]:
    return value.isoformat() if value else None


def _safe_filename(filename: Optional[str], default_name: str) -> str:
    if not filename:
        return default_name

    candidate = Path(filename).name.replace(" ", "_")
    return candidate or default_name


def _safe_archive_name(raw_name: Optional[str], default_name: str = "rtl_project.zip") -> str:
    name = _safe_filename(raw_name, default_name)
    if not name.lower().endswith(".zip"):
        name = f"{Path(name).stem or 'rtl_project'}.zip"
    return name


def _safe_rtl_project_relative_path(raw_path: Optional[str], fallback_name: str) -> str:
    candidate = (raw_path or fallback_name or "rtl_source.sv").replace("\\", "/").strip()
    if not candidate:
        candidate = fallback_name or "rtl_source.sv"

    pure = PurePosixPath(candidate)
    if pure.is_absolute():
        pure = PurePosixPath(Path(fallback_name or "rtl_source.sv").name)

    parts = tuple(part for part in pure.parts if part not in {"", "."})
    if not parts or any(part == ".." for part in parts):
        parts = (Path(fallback_name or "rtl_source.sv").name,)

    safe_parts = []
    for part in parts:
        safe = part.strip().replace(":", "_")
        if safe and safe not in {".", ".."}:
            safe_parts.append(safe)

    if not safe_parts:
        safe_parts = [Path(fallback_name or "rtl_source.sv").name]
    return "/".join(safe_parts)


def _normalize_rtl_project_member_path(raw_path: str) -> str:
    return _normalize_project_member_path(raw_path, require_rtl=True)


WORKSPACE_PROJECT_MEMBER_SUFFIXES = {
    ".md",
    ".markdown",
    ".txt",
    ".text",
    ".json",
    ".yaml",
    ".yml",
    ".sdc",
    ".xdc",
    ".tcl",
    ".cfg",
    ".ini",
    ".toml",
    ".csv",
    ".rst",
    ".log",
    ".xml",
    ".html",
    ".htm",
}


def _is_workspace_project_member_filename(filename: str | None) -> bool:
    return is_rtl_source_filename(filename) or Path(filename or "").suffix.lower() in WORKSPACE_PROJECT_MEMBER_SUFFIXES


def _normalize_project_member_path(raw_path: str, *, require_rtl: bool = False) -> str:
    candidate = str(raw_path or "").replace("\\", "/").strip().strip("/")
    pure = PurePosixPath(candidate)
    if not candidate or pure.is_absolute():
        raise HTTPException(status_code=400, detail="Invalid project member path")

    parts = tuple(part for part in pure.parts if part not in {"", "."})
    if not parts or any(part == ".." for part in parts):
        raise HTTPException(status_code=400, detail="Invalid project member path")

    normalized = "/".join(parts)
    if require_rtl and not is_rtl_source_filename(normalized):
        raise HTTPException(status_code=400, detail="RTL project member must be an RTL source file")
    if not require_rtl and not _is_workspace_project_member_filename(normalized):
        raise HTTPException(status_code=400, detail="Project member type is not supported for editing")
    return normalized


def _hash_password(password: str) -> str:
    return pwd_context.hash(password)


def _verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)


def _create_access_token(user_id: str) -> str:
    expire = datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    payload = {"sub": user_id, "exp": expire}
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def _serialize_user(user: User) -> dict:
    return {
        "id": user.id,
        "email": user.email,
        "full_name": user.full_name,
        "default_organization_id": user.default_organization_id,
        "created_at": _iso(user.created_at),
    }


def _serialize_project(project: Project) -> dict:
    return {
        "id": project.id,
        "organization_id": project.organization_id,
        "owner_user_id": project.owner_user_id,
        "name": project.name,
        "slug": project.slug,
        "description": project.description,
        "status": project.status,
        "created_at": _iso(project.created_at),
        "updated_at": _iso(project.updated_at),
    }


def _serialize_run(run: Run) -> dict:
    return {
        "id": run.id,
        "organization_id": run.organization_id,
        "project_id": run.project_id,
        "user_id": run.user_id,
        "prompt_text": run.prompt_text,
        "specification_type": run.specification_type,
        "status": run.status,
        "created_at": _iso(run.created_at),
        "completed_at": _iso(run.completed_at),
        "execution_time": run.execution_time,
        "gpu_used": run.gpu_used,
        "logs_path": run.logs_path,
        "output_path": run.output_path,
        "mental_model_revision_id": run.mental_model_revision_id,
        "verification_summary": run.verification_summary,
    }


def _serialize_chat_thread(
    thread: ChatThread, state: Optional[ChatThreadState] = None
) -> dict:
    archived_at = state.archived_at if state else None
    payload = {
        "id": thread.id,
        "project_id": thread.project_id,
        "user_id": thread.user_id,
        "title": thread.title,
        "agent_name": getattr(thread, "agent_name", None),
        "active_task_id": getattr(thread, "active_task_id", None),
        "thread_kind": getattr(thread, "thread_kind", None) or "main",
        "parent_thread_id": getattr(thread, "parent_thread_id", None),
        "archived": bool(archived_at),
        "archived_at": _iso(archived_at),
        "context_run_id": state.context_run_id if state else None,
        "created_at": _iso(thread.created_at),
        "updated_at": _iso(thread.updated_at),
    }
    return payload


def _enrich_chat_thread_payload(db: Session, payload: dict) -> dict:
    active_task_id = payload.get("active_task_id")
    if not active_task_id:
        return payload
    from database.models import ProjectTask
    from services.project_tasks import serialize_project_task

    task = db.query(ProjectTask).filter(ProjectTask.id == active_task_id).first()
    if not task:
        return payload
    serialized = serialize_project_task(task)
    payload["active_task"] = {
        "id": serialized["id"],
        "display_id": serialized["display_id"],
        "title": serialized["title"],
        "status": serialized["status"],
        "priority": serialized["priority"],
    }
    return payload


def _serialize_chat_message(
    message: ChatMessage, usage_by_message_id: Optional[dict[str, ChatTokenUsage]] = None
) -> dict:
    payload = {
        "id": message.id,
        "thread_id": message.thread_id,
        "role": message.role,
        "content": message.content,
        "created_at": _iso(message.created_at),
    }
    if usage_by_message_id and message.id in usage_by_message_id:
        payload["token_usage"] = serialize_token_usage(usage_by_message_id[message.id])
    return payload


_CHAT_MESSAGE_ROLE_RANK = {"user": 0, "assistant": 1, "tool": 2}


def _sort_chat_messages_chronologically(messages: list[ChatMessage]) -> list[ChatMessage]:
    """Stable order when rows have very close or equal created_at values."""

    return sorted(
        messages,
        key=lambda message: (
            message.created_at,
            _CHAT_MESSAGE_ROLE_RANK.get(message.role, 9),
            message.id,
        ),
    )


def _serialize_run_event(event: RunEvent) -> dict:
    return {
        "id": event.id,
        "run_id": event.run_id,
        "seq_no": event.seq_no,
        "phase": event.phase,
        "level": event.level,
        "message": event.message,
        "created_at": _iso(event.created_at),
    }


def _serialize_project_artifact(artifact: ProjectArtifact) -> dict:
    metadata = None
    if artifact.metadata_json:
        try:
            metadata = json.loads(artifact.metadata_json)
        except Exception:
            logger.warning(
                "Failed to parse artifact metadata_json for artifact %s",
                artifact.id,
                exc_info=True,
            )
            metadata = {"raw": artifact.metadata_json}
    if (
        artifact.artifact_type == "rtl"
        and is_rtl_archive_filename(artifact.filename or "")
    ):
        if not isinstance(metadata, dict):
            metadata = {}
        if not metadata.get("included_project_files"):
            try:
                metadata["included_project_files"] = _list_rtl_project_members(artifact)
                metadata["included_workspace_files"] = metadata["included_project_files"]
                metadata["included_project_file_count"] = len(metadata["included_project_files"])
            except Exception as exc:
                metadata["member_list_error"] = str(exc)
        if not metadata.get("included_rtl_files"):
            try:
                metadata["included_rtl_files"] = [
                    path for path in _list_rtl_project_members(artifact)
                    if is_rtl_source_filename(path)
                ]
                metadata["included_rtl_file_count"] = len(metadata["included_rtl_files"])
            except Exception as exc:
                metadata["member_list_error"] = str(exc)
        metadata.setdefault("provided_as", "rtl_project")

    return {
        "id": artifact.id,
        "organization_id": artifact.organization_id,
        "project_id": artifact.project_id,
        "user_id": artifact.user_id,
        "artifact_type": artifact.artifact_type,
        "revision": artifact.revision,
        "source": artifact.source,
        "file_path": artifact.file_path,
        "checksum_sha256": artifact.checksum_sha256,
        "filename": artifact.filename,
        "content_type": artifact.content_type,
        "size_bytes": artifact.size_bytes,
        "metadata": metadata,
        "created_at": _iso(artifact.created_at),
    }


def _serialize_mental_model_revision(
    model: MentalModelRevision,
    include_content: bool = False,
) -> dict:
    payload = {
        "id": model.id,
        "organization_id": model.organization_id,
        "project_id": model.project_id,
        "user_id": model.user_id,
        "revision": model.revision,
        "status": model.status,
        "schema_version": model.schema_version,
        "source_spec_artifact_id": model.source_spec_artifact_id,
        "source_rtl_artifact_id": model.source_rtl_artifact_id,
        "summary_text": model.summary_text,
        "created_at": _iso(model.created_at),
    }

    if include_content:
        try:
            payload["content"] = json.loads(model.content_json)
        except Exception:
            logger.warning(
                "Failed to parse mental model content_json for revision %s",
                model.id,
                exc_info=True,
            )
            payload["content"] = {"raw": model.content_json}

    return payload


def _load_mental_model_content(model: Optional[MentalModelRevision]) -> dict[str, Any]:
    if not model:
        return {}
    try:
        content = json.loads(model.content_json or "{}")
    except Exception:
        return {"raw": model.content_json}
    return content if isinstance(content, dict) else {"raw": content}


def _is_llm_enriched_mental_model(content: dict[str, Any]) -> bool:
    return (
        content.get("build_mode") in {"llm_enriched", "llm_attempted_source_grounded"}
        and content.get("llm_status") in {"parsed", "structure_with_llm_attempt"}
    )


def _serialize_patch_proposal(proposal: PatchProposal) -> dict:
    metadata = None
    if proposal.metadata_json:
        try:
            metadata = json.loads(proposal.metadata_json)
        except Exception:
            logger.warning(
                "Failed to parse patch proposal metadata_json for proposal %s",
                proposal.id,
                exc_info=True,
            )
            metadata = {"raw": proposal.metadata_json}

    return {
        "id": proposal.id,
        "organization_id": proposal.organization_id,
        "project_id": proposal.project_id,
        "user_id": proposal.user_id,
        "target_artifact_id": proposal.target_artifact_id,
        "source_agent": proposal.source_agent,
        "title": proposal.title,
        "reason": proposal.reason,
        "diff_text": proposal.diff_text,
        "status": proposal.status,
        "metadata": metadata,
        "created_at": _iso(proposal.created_at),
        "updated_at": _iso(proposal.updated_at),
    }


def _serialize_artifact_pointer(pointer: Optional[ProjectArtifactPointer]) -> dict:
    if not pointer:
        return {
            "active_spec_artifact_id": None,
            "active_rtl_artifact_id": None,
            "updated_at": None,
        }

    return {
        "active_spec_artifact_id": pointer.active_spec_artifact_id,
        "active_rtl_artifact_id": pointer.active_rtl_artifact_id,
        "updated_at": _iso(pointer.updated_at),
    }


def _load_run_event_messages(db: Session, run_id: str) -> list[str]:
    events = (
        db.query(RunEvent)
        .filter(RunEvent.run_id == run_id)
        .order_by(RunEvent.seq_no.asc())
        .all()
    )
    return [event.message for event in events]


def _append_run_event_record(
    db: Session,
    run_id: str,
    message: str,
    phase: str = "runtime",
    level: str = "info",
) -> RunEvent:
    max_seq = (
        db.query(func.max(RunEvent.seq_no)).filter(RunEvent.run_id == run_id).scalar()
    )
    next_seq = int(max_seq or 0) + 1
    event = RunEvent(
        id=str(uuid.uuid4()),
        run_id=run_id,
        seq_no=next_seq,
        phase=phase,
        level=level,
        message=message,
    )
    db.add(event)
    return event


def _truncate_agent_message(message: Optional[str], max_length: int = 110) -> str:
    compact = " ".join(str(message or "").split())
    if not compact:
        return ""
    if len(compact) <= max_length:
        return compact
    return f"{compact[: max_length - 3].rstrip()}..."


def _resolve_agent_event(
    agent_id: str,
    recent_events: Optional[list[RunEvent]],
    latest_event: Optional[RunEvent],
) -> Optional[RunEvent]:
    events = recent_events or []
    if not events:
        return latest_event

    keywords = AGENT_EVENT_KEYWORDS.get(agent_id, ())
    for event in events:
        event_text = f"{event.phase or ''} {event.message or ''}".lower()
        if any(keyword in event_text for keyword in keywords):
            return event

    return latest_event


_AGENT_STATUS_MESSAGES: dict[str, dict[str, tuple[str, str, str]]] = {
    "design_reviewer": {
        "running": (
            "Live spec review: {message}",
            "Parsing active spec and validating architecture intent",
            "event",
        ),
        "failed": (
            "Spec review blocked: {message}",
            "Summarizing architecture risks from failed assertions",
            "latest",
        ),
        "completed": (
            "Spec review complete. Last update: {message}",
            "Architecture checks completed for latest verification run",
            "latest",
        ),
        "queued": ("", "Preparing architecture baseline for upcoming pipeline", "none"),
        "interrupted": (
            "Spec review paused: {message}",
            "Waiting for pipeline resume context",
            "latest",
        ),
        "cancelled": (
            "Spec review paused: {message}",
            "Waiting for pipeline resume context",
            "latest",
        ),
        "_default": ("", "Waiting for active verification context", "none"),
    },
    "auto_fixer": {
        "failed": (
            "Analyzing failure: {message}",
            "Preparing remediation and patch suggestions from failure logs",
            "latest",
        ),
        "running": (
            "Live diagnostics: {message}",
            "Monitoring compile and simulation telemetry for anomalies",
            "event",
        ),
        "completed": (
            "Diagnostics complete. Last update: {message}",
            "No blocking failures detected in latest pipeline run",
            "latest",
        ),
        "queued": ("", "Standing by for compile and assertion results", "none"),
        "interrupted": (
            "Diagnostics paused: {message}",
            "Waiting for pipeline resume context",
            "latest",
        ),
        "cancelled": (
            "Diagnostics paused: {message}",
            "Waiting for pipeline resume context",
            "latest",
        ),
        "_default": ("", "Waiting for a failed-run debug context", "none"),
    },
    "uvm_generator": {
        "running": (
            "Live UVM generation: {message}",
            "Generating and validating UVM scaffolding",
            "event",
        ),
        "failed": (
            "UVM update required: {message}",
            "Recommending UVM updates for failing scenarios",
            "latest",
        ),
        "completed": (
            "UVM synced. Last update: {message}",
            "UVM collateral aligned with latest verification output",
            "latest",
        ),
        "queued": ("", "Planning UVM generation for upcoming execution", "none"),
        "interrupted": (
            "UVM generation paused: {message}",
            "Waiting for pipeline resume context",
            "latest",
        ),
        "cancelled": (
            "UVM generation paused: {message}",
            "Waiting for pipeline resume context",
            "latest",
        ),
        "_default": ("", "Standing by", "none"),
    },
}

_AGENT_STATUS_DEFAULTS: dict[str, tuple[str, str, str]] = {
    "running": (
        "Live work: {message}",
        "Monitoring current agent workflow",
        "event",
    ),
    "failed": (
        "Failure context available: {message}",
        "Ready to analyze failed verification context",
        "latest",
    ),
    "completed": (
        "Latest run complete. Last update: {message}",
        "Ready for follow-up planning",
        "latest",
    ),
    "queued": ("", "Queued for upcoming workflow", "none"),
    "interrupted": (
        "Paused: {message}",
        "Waiting for resumed context",
        "latest",
    ),
    "cancelled": (
        "Paused: {message}",
        "Waiting for resumed context",
        "latest",
    ),
    "_default": ("", "Waiting for mental model and active artifacts", "none"),
}


def _format_agent_status_message(
    template: tuple[str, str, str],
    *,
    agent_event_message: Optional[str],
    latest_message: Optional[str],
) -> str:
    with_message, without_message, source = template
    selected_message = None
    if source == "event":
        selected_message = agent_event_message
    elif source == "latest":
        selected_message = latest_message
    if selected_message and with_message:
        return with_message.format(message=selected_message)
    return without_message


def _resolve_agent_task(
    agent_id: str,
    run_status: str,
    latest_event: Optional[RunEvent] = None,
    recent_events: Optional[list[RunEvent]] = None,
) -> str:
    normalized_status = (run_status or RunStatus.IDLE.value).lower()
    agent_event = _resolve_agent_event(agent_id, recent_events, latest_event)
    agent_event_message = _truncate_agent_message(
        agent_event.message if agent_event else None
    )
    latest_message = _truncate_agent_message(
        latest_event.message if latest_event else None
    )

    status_templates = _AGENT_STATUS_MESSAGES.get(agent_id, _AGENT_STATUS_DEFAULTS)
    template = status_templates.get(
        normalized_status,
        status_templates.get("_default", _AGENT_STATUS_DEFAULTS["_default"]),
    )
    return _format_agent_status_message(
        template,
        agent_event_message=agent_event_message,
        latest_message=latest_message,
    )


def _get_project_artifact_pointer(
    db: Session, project_id: str
) -> Optional[ProjectArtifactPointer]:
    return (
        db.query(ProjectArtifactPointer)
        .filter(ProjectArtifactPointer.project_id == project_id)
        .first()
    )


def _ensure_project_artifact_pointer(
    db: Session, project_id: str
) -> ProjectArtifactPointer:
    pointer = _get_project_artifact_pointer(db, project_id)
    if pointer:
        return pointer

    pointer = ProjectArtifactPointer(project_id=project_id)
    db.add(pointer)
    db.commit()
    db.refresh(pointer)
    return pointer


def _get_chat_thread_state(db: Session, thread_id: str) -> Optional[ChatThreadState]:
    return (
        db.query(ChatThreadState).filter(ChatThreadState.thread_id == thread_id).first()
    )


def _ensure_chat_thread_state(db: Session, thread_id: str) -> ChatThreadState:
    state = _get_chat_thread_state(db, thread_id)
    if state:
        return state

    state = ChatThreadState(thread_id=thread_id)
    db.add(state)
    db.flush()
    return state


def _get_chat_thread_states(
    db: Session, thread_ids: list[str]
) -> dict[str, ChatThreadState]:
    if not thread_ids:
        return {}

    states = (
        db.query(ChatThreadState)
        .filter(ChatThreadState.thread_id.in_(thread_ids))
        .all()
    )
    return {state.thread_id: state for state in states}


def _next_artifact_revision(db: Session, project_id: str, artifact_type: str) -> int:
    max_revision = (
        db.query(func.max(ProjectArtifact.revision))
        .filter(
            ProjectArtifact.project_id == project_id,
            ProjectArtifact.artifact_type == artifact_type,
        )
        .scalar()
    )
    return int(max_revision or 0) + 1


def _next_mental_model_revision(db: Session, project_id: str) -> int:
    return next_mental_model_revision(db, project_id)


def _require_project_artifact(
    db: Session,
    project: Project,
    artifact_id: str,
    artifact_type: Optional[str] = None,
) -> ProjectArtifact:
    filters = [
        ProjectArtifact.id == artifact_id,
        ProjectArtifact.project_id == project.id,
        ProjectArtifact.organization_id == project.organization_id,
    ]
    if artifact_type:
        filters.append(ProjectArtifact.artifact_type == artifact_type)

    artifact = db.query(ProjectArtifact).filter(*filters).first()
    if not artifact:
        label = f"{artifact_type} artifact" if artifact_type else "Artifact"
        raise HTTPException(status_code=404, detail=f"{label} not found")
    return artifact


def _resolve_mental_model_sources(
    db: Session,
    project: Project,
    req: MentalModelBuildRequest,
) -> tuple[ProjectArtifact, ProjectArtifact]:
    pointer = _get_project_artifact_pointer(db, project.id)
    spec_artifact_id = req.spec_artifact_id or (
        pointer.active_spec_artifact_id if pointer else None
    )
    rtl_artifact_id = req.rtl_artifact_id or (
        pointer.active_rtl_artifact_id if pointer else None
    )

    if not spec_artifact_id:
        raise HTTPException(
            status_code=400,
            detail="A spec artifact is required to build a mental model",
        )
    if not rtl_artifact_id:
        raise HTTPException(
            status_code=400,
            detail="An RTL artifact is required to build a mental model",
        )

    spec_artifact = _require_project_artifact(
        db, project, spec_artifact_id, artifact_type="spec"
    )
    rtl_artifact = _require_project_artifact(
        db, project, rtl_artifact_id, artifact_type="rtl"
    )

    for artifact in (spec_artifact, rtl_artifact):
        if not Path(artifact.file_path).exists():
            raise HTTPException(
                status_code=404,
                detail=f"Artifact file is missing: {artifact.filename}",
            )

    return spec_artifact, rtl_artifact


def resolve_active_spec_rtl_artifacts(
    db: Session,
    project: Project,
    *,
    auto_activate: bool = False,
) -> tuple[Optional[ProjectArtifact], Optional[ProjectArtifact]]:
    """Return the project's active spec/RTL artifacts.

    Staged verification imports this helper directly.  When requested, promote
    the latest spec/RTL revisions to active pointers so older projects created
    before artifact pointers still work.
    """

    pointer = _get_project_artifact_pointer(db, project.id)

    if auto_activate:
        pointer_needs_update = False
        if not pointer:
            pointer = ProjectArtifactPointer(project_id=project.id)
            db.add(pointer)
            pointer_needs_update = True

        if not pointer.active_spec_artifact_id:
            latest_spec = (
                db.query(ProjectArtifact)
                .filter(
                    ProjectArtifact.project_id == project.id,
                    ProjectArtifact.organization_id == project.organization_id,
                    ProjectArtifact.artifact_type == "spec",
                )
                .order_by(ProjectArtifact.revision.desc())
                .first()
            )
            if latest_spec:
                pointer.active_spec_artifact_id = latest_spec.id
                pointer_needs_update = True

        if not pointer.active_rtl_artifact_id:
            latest_rtl = (
                db.query(ProjectArtifact)
                .filter(
                    ProjectArtifact.project_id == project.id,
                    ProjectArtifact.organization_id == project.organization_id,
                    ProjectArtifact.artifact_type == "rtl",
                )
                .order_by(ProjectArtifact.revision.desc())
                .first()
            )
            if latest_rtl:
                pointer.active_rtl_artifact_id = latest_rtl.id
                pointer_needs_update = True

        if pointer_needs_update:
            db.add(pointer)
            db.commit()
            db.refresh(pointer)

    spec_artifact = None
    rtl_artifact = None
    if pointer and pointer.active_spec_artifact_id:
        spec_artifact = (
            db.query(ProjectArtifact)
            .filter(
                ProjectArtifact.id == pointer.active_spec_artifact_id,
                ProjectArtifact.project_id == project.id,
                ProjectArtifact.organization_id == project.organization_id,
                ProjectArtifact.artifact_type == "spec",
            )
            .first()
        )
    if pointer and pointer.active_rtl_artifact_id:
        rtl_artifact = (
            db.query(ProjectArtifact)
            .filter(
                ProjectArtifact.id == pointer.active_rtl_artifact_id,
                ProjectArtifact.project_id == project.id,
                ProjectArtifact.organization_id == project.organization_id,
                ProjectArtifact.artifact_type == "rtl",
            )
            .first()
        )

    return spec_artifact, rtl_artifact


def _prepare_fresh_mental_model_revision_for_run(
    db: Session,
    *,
    run: Run,
    project: Project,
    current_user: User,
    spec_artifact: ProjectArtifact,
    rtl_artifact: ProjectArtifact,
) -> MentalModelRevision:
    _append_run_event_record(
        db,
        run.id,
        "Checking source-grounded mental model freshness.",
        phase="mental_model.check",
    )
    db.flush()

    latest_model = get_latest_mental_model_revision(db, project.id)
    freshness = evaluate_mental_model_freshness(
        latest_model,
        spec_artifact,
        rtl_artifact,
    )
    if freshness["fresh"] and latest_model is not None:
        run.mental_model_revision_id = latest_model.id
        db.add(run)
        _append_run_event_record(
            db,
            run.id,
            (
                f"Using mental model revision {latest_model.revision} "
                "for this verification run."
            ),
            phase="mental_model.ready",
        )
        db.flush()
        return latest_model

    _append_run_event_record(
        db,
        run.id,
        (
            "Mental model is not current "
            f"({freshness.get('reason')}); rebuilding before verification."
        ),
        phase="mental_model.stale",
        level="warning",
    )
    db.flush()

    record, _content, summary = create_mental_model_revision(
        db,
        project=project,
        user=current_user,
        spec_artifact=spec_artifact,
        rtl_artifact=rtl_artifact,
    )
    run.mental_model_revision_id = record.id
    db.add(run)
    _append_run_event_record(
        db,
        run.id,
        summary,
        phase="mental_model.generated",
    )
    db.flush()
    _append_run_event_record(
        db,
        run.id,
        f"Saved mental model revision {record.revision} for verification run.",
        phase="mental_model.persisted",
    )
    db.flush()
    return record


def _artifact_dir(organization_id: str, project_id: str, artifact_type: str) -> Path:
    directory = (
        OUTPUTS_DIR
        / "orgs"
        / organization_id
        / "projects"
        / project_id
        / "artifacts"
        / artifact_type
    )
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _sha256_bytes(content: bytes) -> str:
    digest = hashlib.sha256()
    digest.update(content)
    return digest.hexdigest()


def _normalize_relative_artifact_path(raw_path: str) -> str:
    normalized = (raw_path or "").replace("\\", "/").strip()
    normalized = normalized.lstrip("./")
    return normalized


def _guess_content_type(file_path: Path) -> str:
    guessed, _ = mimetypes.guess_type(file_path.name)
    return guessed or "application/octet-stream"


def _is_text_content_type(content_type: Optional[str]) -> bool:
    if not content_type:
        return False

    lowered = content_type.lower()
    return lowered.startswith("text/") or lowered in {
        "application/json",
        "application/xml",
        "application/javascript",
        "application/x-sh",
        "application/yaml",
    }


def _is_probably_text_bytes(raw_bytes: bytes) -> bool:
    if not raw_bytes:
        return True

    preview = raw_bytes[:4096]
    if b"\x00" in preview:
        return False

    try:
        preview.decode("utf-8")
        return True
    except UnicodeDecodeError:
        return False


def _iter_generated_run_files(run_dir: Path) -> list[tuple[Path, str]]:
    generated_files: list[tuple[Path, str]] = []
    for file_path in run_dir.rglob("*"):
        if not file_path.is_file():
            continue

        relative = file_path.relative_to(run_dir).as_posix()
        if not relative:
            continue

        top_level = relative.split("/", 1)[0]
        if top_level in GENERATED_SYNC_EXCLUDED_TOP_LEVEL_DIRS:
            continue

        generated_files.append((file_path, relative))

    generated_files.sort(key=lambda item: item[1])
    return generated_files


def _extract_generated_dedupe_keys(
    artifacts: list[ProjectArtifact],
) -> set[tuple[str, str, str]]:
    dedupe_keys: set[tuple[str, str, str]] = set()

    for artifact in artifacts:
        if artifact.artifact_type != "generated" or not artifact.metadata_json:
            continue

        try:
            metadata = json.loads(artifact.metadata_json)
        except Exception:
            continue

        run_id = str(metadata.get("source_run_id") or "").strip()
        relative_path = _normalize_relative_artifact_path(
            str(
                metadata.get("source_relative_path")
                or metadata.get("relative_path")
                or ""
            )
        )
        checksum = str(artifact.checksum_sha256 or "").strip()

        if run_id and relative_path and checksum:
            dedupe_keys.add((run_id, relative_path, checksum))

    return dedupe_keys


def _unique_org_slug(db: Session, base: str) -> str:
    slug = _slugify(base) or "organization"
    suffix = 2
    while db.query(Organization).filter(Organization.slug == slug).first():
        slug = f"{_slugify(base)}-{suffix}"
        suffix += 1

    return slug


def _unique_project_slug(db: Session, organization_id: str, base: str) -> str:
    slug = _slugify(base) or "project"
    suffix = 2
    while (
        db.query(Project)
        .filter(Project.organization_id == organization_id, Project.slug == slug)
        .first()
    ):
        slug = f"{_slugify(base)}-{suffix}"
        suffix += 1

    return slug


def _ensure_default_organization(db: Session, user: User) -> Organization:
    if user.default_organization_id:
        existing = (
            db.query(Organization)
            .filter(Organization.id == user.default_organization_id)
            .first()
        )
        if existing:
            return existing

    local_name = user.email.split("@", maxsplit=1)[0]
    org = Organization(
        id=str(uuid.uuid4()),
        owner_user_id=user.id,
        name=f"{local_name} organization",
        slug=_unique_org_slug(db, f"{local_name}-org"),
    )
    db.add(org)
    db.commit()
    db.refresh(org)

    user.default_organization_id = org.id
    db.add(user)
    db.commit()
    db.refresh(user)

    return org


def _ensure_starter_project(
    db: Session, user: User, organization: Organization
) -> Project:
    project = (
        db.query(Project)
        .filter(Project.organization_id == organization.id)
        .order_by(Project.created_at.asc())
        .first()
    )
    if project:
        return project

    starter = Project(
        id=str(uuid.uuid4()),
        organization_id=organization.id,
        owner_user_id=user.id,
        name="Starter Project",
        slug=_unique_project_slug(db, organization.id, "starter-project"),
        description="Default local workspace created for this desktop user.",
        status="active",
    )
    db.add(starter)
    db.commit()
    db.refresh(starter)
    return starter


def _load_logs(logs_path: Optional[str], limit: Optional[int] = None) -> list[str]:
    if not logs_path or not os.path.exists(logs_path):
        return []

    try:
        with open(logs_path, "r", encoding="utf-8") as file:
            logs = json.load(file).get("logs", [])
    except Exception:
        return []

    if limit is None:
        return logs

    return logs[-limit:]


def _create_project_artifact_revision(
    db: Session,
    project: Project,
    current_user: User,
    artifact_type: str,
    raw_bytes: bytes,
    filename: str,
    source: str,
    content_type: Optional[str],
    metadata: Optional[dict] = None,
    reuse_identical: bool = False,
) -> tuple[ProjectArtifact, Optional[ProjectArtifactPointer]]:
    if artifact_type not in {"spec", "rtl", "generated"}:
        raise HTTPException(status_code=400, detail="Unsupported artifact type")

    if not raw_bytes:
        raise HTTPException(status_code=400, detail="Artifact content is empty")

    safe_name = _safe_filename(filename, f"{artifact_type}.txt")
    checksum = _sha256_bytes(raw_bytes)
    if artifact_type in {"spec", "rtl"}:
        latest_artifact = (
            db.query(ProjectArtifact)
            .filter(
                ProjectArtifact.project_id == project.id,
                ProjectArtifact.artifact_type == artifact_type,
            )
            .order_by(ProjectArtifact.revision.desc())
            .first()
        )
        if (
            latest_artifact
            and latest_artifact.filename == safe_name
            and latest_artifact.checksum_sha256 == checksum
        ):
            if reuse_identical:
                _repair_reused_artifact_storage(
                    db,
                    project,
                    latest_artifact,
                    raw_bytes=raw_bytes,
                    safe_name=safe_name,
                    content_type=content_type,
                )
                pointer = _get_project_artifact_pointer(db, project.id)
                if not pointer:
                    pointer = ProjectArtifactPointer(project_id=project.id)
                    db.add(pointer)

                if artifact_type == "spec":
                    pointer.active_spec_artifact_id = latest_artifact.id
                else:
                    pointer.active_rtl_artifact_id = latest_artifact.id

                db.add(pointer)
                db.commit()
                db.refresh(latest_artifact)
                db.refresh(pointer)
                return latest_artifact, pointer

            raise HTTPException(
                status_code=409,
                detail=f"An identical {artifact_type.upper()} artifact already exists.",
            )

    revision = _next_artifact_revision(db, project.id, artifact_type)
    artifact_id = str(uuid.uuid4())
    storage_filename = f"v{revision:04d}_{artifact_id}_{safe_name}"
    artifact_path = (
        _artifact_dir(project.organization_id, project.id, artifact_type)
        / storage_filename
    )
    artifact_path.parent.mkdir(parents=True, exist_ok=True)

    with open(artifact_path, "wb") as file:
        file.write(raw_bytes)

    artifact = ProjectArtifact(
        id=artifact_id,
        organization_id=project.organization_id,
        project_id=project.id,
        user_id=current_user.id,
        artifact_type=artifact_type,
        revision=revision,
        source=(source or "upload").strip() or "upload",
        file_path=str(artifact_path),
        checksum_sha256=checksum,
        filename=safe_name,
        content_type=content_type,
        size_bytes=len(raw_bytes),
        metadata_json=json.dumps(metadata) if metadata else None,
    )

    pointer: Optional[ProjectArtifactPointer] = None
    if artifact_type in {"spec", "rtl"}:
        pointer = _get_project_artifact_pointer(db, project.id)
        if not pointer:
            pointer = ProjectArtifactPointer(project_id=project.id)
            db.add(pointer)

    db.add(artifact)
    db.flush()

    if pointer and artifact_type == "spec":
        pointer.active_spec_artifact_id = artifact.id
        db.add(pointer)
    elif pointer and artifact_type == "rtl":
        pointer.active_rtl_artifact_id = artifact.id
        db.add(pointer)

    db.commit()
    db.refresh(artifact)
    if pointer:
        db.refresh(pointer)
    return artifact, pointer


def _repair_reused_artifact_storage(
    db: Session,
    project: Project,
    artifact: ProjectArtifact,
    *,
    raw_bytes: bytes,
    safe_name: str,
    content_type: Optional[str],
) -> None:
    """Make reused artifact rows point at durable canonical storage.

    Older desktop builds could leave the active RTL artifact pointing at a file
    inside an extracted archive directory. That derived path can disappear after
    app restarts, while identical re-uploads keep reusing the same stale row.
    """

    current_path = Path(artifact.file_path or "")
    points_inside_extraction = find_extracted_rtl_root(current_path) is not None
    if current_path.exists() and current_path.is_file() and not points_inside_extraction:
        return

    storage_filename = f"v{artifact.revision:04d}_{artifact.id}_{safe_name}"
    repaired_path = (
        _artifact_dir(project.organization_id, project.id, artifact.artifact_type)
        / storage_filename
    )
    repaired_path.parent.mkdir(parents=True, exist_ok=True)
    repaired_path.write_bytes(raw_bytes)

    artifact.file_path = str(repaired_path)
    artifact.filename = safe_name
    artifact.content_type = content_type
    artifact.size_bytes = len(raw_bytes)
    artifact.checksum_sha256 = _sha256_bytes(raw_bytes)
    db.add(artifact)
    db.flush()


def _sync_run_output_artifacts_to_project(
    db: Session,
    project: Project,
    current_user: User,
    run: Run,
) -> dict:
    run_dir = Path(run.output_path or "")
    if not run.output_path or not run_dir.exists() or not run_dir.is_dir():
        raise HTTPException(status_code=404, detail="Run output directory not found")

    existing_generated = (
        db.query(ProjectArtifact)
        .filter(
            ProjectArtifact.project_id == project.id,
            ProjectArtifact.artifact_type == "generated",
        )
        .all()
    )
    dedupe_keys = _extract_generated_dedupe_keys(existing_generated)

    discovered_files = _iter_generated_run_files(run_dir)
    created_artifacts: list[dict] = []
    skipped_count = 0

    for file_path, relative_path in discovered_files:
        raw_bytes = file_path.read_bytes()
        checksum = _sha256_bytes(raw_bytes)
        dedupe_key = (run.id, relative_path, checksum)

        if dedupe_key in dedupe_keys:
            skipped_count += 1
            continue

        content_type = _guess_content_type(file_path)
        editable = _is_text_content_type(content_type) or _is_probably_text_bytes(
            raw_bytes
        )

        artifact, _ = _create_project_artifact_revision(
            db,
            project,
            current_user,
            artifact_type="generated",
            raw_bytes=raw_bytes,
            filename=file_path.name,
            source="run_output_sync",
            content_type=content_type,
            metadata={
                "provided_as": "run_output_sync",
                "source_run_id": run.id,
                "relative_path": relative_path,
                "source_relative_path": relative_path,
                "editable": editable,
            },
        )
        created_artifacts.append(_serialize_project_artifact(artifact))
        dedupe_keys.add(dedupe_key)

    return {
        "discovered_count": len(discovered_files),
        "created_count": len(created_artifacts),
        "skipped_count": skipped_count,
        "artifacts": created_artifacts,
    }


def _write_temp_upload_file(
    raw_bytes: bytes,
    filename: str,
    default_suffix: str,
) -> Path:
    suffix = Path(filename or "").suffix or default_suffix
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as file:
        file.write(raw_bytes)
        return Path(file.name)


def _resolve_spec_parser(filename: str):
    suffix = Path(filename or "").suffix.lower()
    if suffix == ".pdf":
        return parse_pdf, "pdf_parser", suffix
    if suffix in {".doc", ".docx"}:
        return parse_docx, "docx_parser", suffix
    if suffix in {".txt", ".md", ".text"}:
        return parse_txt, "txt_parser", suffix
    return None, "utf8_fallback", suffix


def _extract_spec_text(file_path: Path, filename: str) -> tuple[str, str, str]:
    parser, parser_name, suffix = _resolve_spec_parser(filename)
    if parser:
        return parser(file_path), parser_name, suffix
    return file_path.read_text(encoding="utf-8", errors="replace"), parser_name, suffix


def _extract_rtl_archive_for_run(
    archive_path: Path,
    original_filename: str,
    input_dir: Path,
    label: str,
) -> tuple[Path, dict | None]:
    if not is_rtl_archive_filename(original_filename):
        return archive_path, None

    extract_dir = input_dir / f"{label}_project"
    try:
        manifest = extract_rtl_archive(archive_path, extract_dir)
    except RtlArchiveError as exc:
        raise HTTPException(status_code=400, detail="RTL archive extraction failed") from exc

    archive_format = manifest.pop("kind", "archive")
    return extract_dir, {
        "kind": "archive",
        "archive_format": archive_format,
        "filename": original_filename,
        **manifest,
    }


def _build_text_preview(
    text: str,
    max_lines: int = 14,
    max_chars: int = 1600,
) -> tuple[str, bool]:
    if not text:
        return "", False

    lines = text.splitlines()
    preview = "\n".join(lines[:max_lines])
    truncated = len(lines) > max_lines

    if len(preview) > max_chars:
        preview = preview[:max_chars]
        truncated = True

    return preview, truncated


def _serialize_rtl_module_summary(module: Any, max_ports: int = 20) -> dict[str, Any]:
    ports = []
    for port in (getattr(module, "ports", []) or [])[:max_ports]:
        direction = getattr(port, "direction", None)
        direction_value = (
            direction.value
            if direction is not None and hasattr(direction, "value")
            else str(direction or "")
        )
        ports.append(
            {
                "name": getattr(port, "name", ""),
                "direction": direction_value,
                "width": int(getattr(port, "width", 1) or 1),
                "bus_range": getattr(port, "bus_range", "") or "",
                "port_type": getattr(port, "port_type", "logic") or "logic",
            }
        )

    line_range = getattr(module, "line_range", (0, 0)) or (0, 0)
    start_line = int(line_range[0] if len(line_range) > 0 else 0)
    end_line = int(line_range[1] if len(line_range) > 1 else start_line)

    return {
        "name": getattr(module, "name", ""),
        "port_count": len(getattr(module, "ports", []) or []),
        "ports": ports,
        "line_range": [start_line, end_line],
    }


def _index_spec_artifact_document_context(
    project: Project,
    artifact: ProjectArtifact,
    *,
    force: bool = False,
) -> dict[str, Any] | None:
    """Build page-level spec index for large or multi-page documents."""
    if artifact.artifact_type != "spec":
        return None
    try:
        from services.document_context import ensure_spec_document_index
        from services.document_context.extract import extract_pages_from_file
        from services.document_context.manager import should_index_spec

        path = Path(artifact.file_path)
        if not path.exists():
            return {"status": "pending", "error": "artifact_file_missing"}

        pages = extract_pages_from_file(path, artifact.filename)
        if not should_index_spec(len(pages), int(artifact.size_bytes or 0)):
            return {
                "status": "not_needed",
                "page_count": len(pages),
                "section_count": 0,
            }

        result = ensure_spec_document_index(
            organization_id=project.organization_id,
            project_id=project.id,
            artifact_id=artifact.id,
            file_path=path,
            filename=artifact.filename,
            checksum_sha256=artifact.checksum_sha256,
            force=force,
        )
        return result.to_dict()
    except Exception as exc:
        logger.warning(
            "Document context indexing failed for spec %s: %s",
            artifact.id,
            exc,
            exc_info=True,
        )
        return {"status": "error", "error": str(exc)}


def _index_rtl_artifact_context(
    project: Project,
    artifact: ProjectArtifact,
    *,
    force: bool = False,
) -> dict[str, Any] | None:
    """Build FTS index over RTL source for inline completion RAG."""
    if artifact.artifact_type != "rtl":
        return None
    try:
        from services.rtl_context import ensure_rtl_context_index

        path = Path(artifact.file_path)
        if not path.exists():
            return {"status": "pending", "error": "artifact_file_missing"}

        result = ensure_rtl_context_index(
            organization_id=project.organization_id,
            project_id=project.id,
            artifact_id=artifact.id,
            file_path=path,
            checksum_sha256=artifact.checksum_sha256,
            force=force,
        )
        return result.to_dict()
    except Exception as exc:
        logger.warning(
            "RTL context indexing failed for rtl %s: %s",
            artifact.id,
            exc,
            exc_info=True,
        )
        return {"status": "error", "error": str(exc)}


def _enqueue_codebase_graph_build(
    db: Session,
    project: Project,
    *,
    trigger: str,
) -> dict[str, Any] | None:
    """Background knowledge-graph build after artifact upload."""
    try:
        from services.codebase_graph.manager import enqueue_build

        return enqueue_build(db, str(project.id), trigger=trigger)
    except Exception as exc:
        logger.warning(
            "Codebase graph enqueue failed for project %s: %s",
            project.id,
            exc,
            exc_info=True,
        )
        return {"status": "error", "error": str(exc)}


def _analyze_spec_bytes(raw_bytes: bytes, filename: str) -> dict[str, Any]:
    if not raw_bytes:
        return {
            "status": "error",
            "error": "Artifact content is empty",
            "parser": "none",
            "detected_extension": Path(filename or "").suffix.lower() or None,
        }

    suffix = Path(filename or "").suffix.lower()
    if len(raw_bytes) > MAX_SPEC_UPLOAD_PARSE_BYTES:
        preview_text = ""
        if suffix in {".txt", ".md", ".markdown", ".text", ".json", ".yaml", ".yml", ".rst", ".csv", ".log", ".html", ".htm", ".xml"}:
            preview_text, _preview_truncated = _build_text_preview(
                raw_bytes[:MAX_ARTIFACT_PREVIEW_BYTES].decode("utf-8", errors="replace")
            )
        return {
            "status": "stored",
            "parser": "deferred_large_spec",
            "detected_extension": suffix or None,
            "byte_count": len(raw_bytes),
            "character_count": len(preview_text),
            "line_count": len(preview_text.splitlines()),
            "preview_text": preview_text,
            "preview_truncated": True,
            "warning": (
                "Spec file is large; upload succeeded and full text extraction "
                "is deferred to the mental-model build."
            ),
        }

    temp_path = _write_temp_upload_file(raw_bytes, filename, ".txt")
    try:
        parsed_text, parser_name, suffix = _extract_spec_text(temp_path, filename)
        preview_text, preview_truncated = _build_text_preview(parsed_text)
        return {
            "status": "parsed",
            "parser": parser_name,
            "detected_extension": suffix or None,
            "character_count": len(parsed_text),
            "line_count": len(parsed_text.splitlines()),
            "preview_text": preview_text,
            "preview_truncated": preview_truncated,
        }
    except Exception as exc:
        return {
            "status": "error",
            "parser": "spec_parser",
            "detected_extension": Path(filename or "").suffix.lower() or None,
            "error": str(exc),
        }
    finally:
        temp_path.unlink(missing_ok=True)


def _analyze_rtl_bytes(raw_bytes: bytes, filename: str) -> dict[str, Any]:
    if not raw_bytes:
        return {
            "status": "error",
            "error": "Artifact content is empty",
            "parser": "rtl_parser",
            "detected_extension": Path(filename or "").suffix.lower() or None,
        }

    if is_rtl_archive_filename(filename):
        temp_path = _write_temp_upload_file(raw_bytes, filename, ".zip")
        try:
            with tempfile.TemporaryDirectory(prefix="chipverify_rtl_project_") as temp_dir:
                manifest = extract_rtl_archive(temp_path, Path(temp_dir) / "rtl_project")
            return {
                "status": "parsed",
                "parser": "rtl_archive",
                "detected_extension": Path(filename or "").suffix.lower() or None,
                "provided_as": "project_archive",
                "file_count": manifest["file_count"],
                "rtl_file_count": manifest["rtl_file_count"],
                "rtl_files": manifest["rtl_files"],
                "total_uncompressed_bytes": manifest["total_uncompressed_bytes"],
            }
        except Exception as exc:
            return {
                "status": "error",
                "parser": "rtl_archive",
                "detected_extension": Path(filename or "").suffix.lower() or None,
                "error": str(exc),
            }
        finally:
            temp_path.unlink(missing_ok=True)

    temp_path = _write_temp_upload_file(raw_bytes, filename, ".sv")
    try:
        _raw_code, rtl_analysis = parse_rtl(temp_path)
        module_summaries = [
            _serialize_rtl_module_summary(module)
            for module in rtl_analysis.modules[:12]
        ]

        payload = {
            "status": "parsed",
            "parser": "rtl_parser",
            "detected_extension": Path(filename or "").suffix.lower() or None,
            "code_style": rtl_analysis.code_style,
            "top_module": rtl_analysis.top_module,
            "clock_signal": rtl_analysis.clock_signal,
            "reset_signal": rtl_analysis.reset_signal,
            "module_count": len(rtl_analysis.modules),
            "modules": module_summaries,
            "port_count": len(rtl_analysis.port_map),
            "signal_count": len(rtl_analysis.internal_signals),
            "always_block_count": len(rtl_analysis.always_blocks),
            "has_fsm": rtl_analysis.has_fsm,
            "fsm_states": rtl_analysis.fsm_states[:20],
        }

        if not module_summaries:
            payload["warning"] = (
                "No modules matched the current RTL parser pattern. "
                "Use ANSI style module headers with a parenthesized port list for best results."
            )

        # Attach AST-based structural graph metadata for desktop rendering.
        try:
            ast_modules, ast_metadata = parse_verilog(temp_path, return_metadata=True)
            if ast_modules:
                preferred_module = (
                    rtl_analysis.top_module
                    if rtl_analysis.top_module
                    and rtl_analysis.top_module in ast_modules
                    else next(iter(ast_modules.keys()))
                )

                with tempfile.TemporaryDirectory(
                    prefix="chipverify_rtl_graph_"
                ) as graph_dir:
                    dot_path = build_module_graph(
                        preferred_module,
                        ast_modules[preferred_module],
                        output_dir=graph_dir,
                        output_format="dot",
                    )
                    dot_source = Path(dot_path).read_text(encoding="utf-8")

                dot_truncated = False
                if len(dot_source) > MAX_RTL_DOT_SOURCE_CHARS:
                    dot_source = dot_source[:MAX_RTL_DOT_SOURCE_CHARS]
                    dot_truncated = True

                payload["ast_visualization"] = {
                    "status": "parsed",
                    "module": preferred_module,
                    "module_count": len(ast_modules),
                    "dot": dot_source,
                    "dot_truncated": dot_truncated,
                    "parser_backend": ast_metadata.get("parser_backend"),
                    "fallback_used": bool(ast_metadata.get("fallback_used")),
                    "sanitized": bool(ast_metadata.get("sanitized")),
                    "errors": list(ast_metadata.get("errors") or [])[:3],
                }
            else:
                payload["ast_visualization"] = {
                    "status": "error",
                    "error": "AST parser did not return any modules",
                }
        except Exception as ast_exc:
            payload["ast_visualization"] = {
                "status": "error",
                "error": str(ast_exc),
            }

        return payload
    except Exception as exc:
        return {
            "status": "error",
            "parser": "rtl_parser",
            "detected_extension": Path(filename or "").suffix.lower() or None,
            "error": str(exc),
        }
    finally:
        temp_path.unlink(missing_ok=True)


def _read_rtl_project_zip_entries(artifact: ProjectArtifact) -> list[zipfile.ZipInfo]:
    if artifact.artifact_type != "rtl" or not is_rtl_archive_filename(artifact.filename):
        raise HTTPException(status_code=400, detail="Artifact is not an RTL project archive")

    artifact_path = Path(artifact.file_path)
    if not artifact_path.exists():
        raise HTTPException(status_code=404, detail="RTL project artifact file is missing")

    try:
        with zipfile.ZipFile(artifact_path, "r") as archive:
            return [info for info in archive.infolist() if not info.is_dir()]
    except zipfile.BadZipFile as exc:
        raise HTTPException(status_code=400, detail=f"Unable to read RTL project archive: {exc}") from exc


def _list_rtl_project_members(artifact: ProjectArtifact) -> list[str]:
    members: list[str] = []
    for info in _read_rtl_project_zip_entries(artifact):
        try:
            normalized = _normalize_project_member_path(info.filename)
        except HTTPException:
            continue
        if normalized not in members:
            members.append(normalized)
    return members


def _read_rtl_project_member_content(artifact: ProjectArtifact, member_path: str) -> str:
    normalized_path = _normalize_project_member_path(member_path)
    artifact_path = Path(artifact.file_path)
    if not artifact_path.exists():
        raise HTTPException(status_code=404, detail="RTL project artifact file is missing")

    try:
        with zipfile.ZipFile(artifact_path, "r") as archive:
            names = set(archive.namelist())
            if normalized_path not in names:
                raise HTTPException(status_code=404, detail="RTL project member not found")
            raw = archive.read(normalized_path)
    except zipfile.BadZipFile as exc:
        raise HTTPException(status_code=400, detail=f"Unable to read RTL project archive: {exc}") from exc

    return raw.decode("utf-8", errors="replace")


def _replace_rtl_project_member_content(
    artifact: ProjectArtifact,
    member_path: str,
    content: str,
) -> tuple[bytes, list[str]]:
    normalized_path = _normalize_project_member_path(member_path)
    artifact_path = Path(artifact.file_path)
    if not artifact_path.exists():
        raise HTTPException(status_code=404, detail="RTL project artifact file is missing")

    replacement = (content or "").encode("utf-8")
    members: list[str] = []
    replaced = False
    total_bytes = 0

    try:
        with zipfile.ZipFile(artifact_path, "r") as source_archive:
            output = io.BytesIO()
            with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as target_archive:
                used_names: set[str] = set()
                for info in source_archive.infolist():
                    if info.is_dir():
                        continue

                    try:
                        safe_name = _normalize_project_member_path(info.filename)
                    except HTTPException:
                        continue

                    if safe_name in used_names:
                        continue
                    used_names.add(safe_name)

                    raw = replacement if safe_name == normalized_path else source_archive.read(info.filename)
                    if safe_name == normalized_path:
                        replaced = True

                    total_bytes += len(raw)
                    if total_bytes > MAX_ARCHIVE_BYTES:
                        raise HTTPException(
                            status_code=400,
                            detail=f"RTL project upload exceeds {MAX_ARCHIVE_BYTES // (1024 * 1024)} MB",
                        )

                    target_archive.writestr(safe_name, raw)
                    if is_rtl_source_filename(safe_name):
                        members.append(safe_name)

            raw_zip = output.getvalue()
    except zipfile.BadZipFile as exc:
        raise HTTPException(status_code=400, detail=f"Unable to read RTL project archive: {exc}") from exc

    if not replaced:
        raise HTTPException(status_code=404, detail="RTL project member not found")

    return raw_zip, members


def _build_offline_debug_response(user_prompt: str, log_lines: list[str]) -> str:
    lowered_logs = [line.lower() for line in log_lines]

    pipeline_failure_line = next(
        (line for line in log_lines if "pipeline failed:" in line.lower()),
        "Pipeline failed without a detailed terminal exception.",
    )

    findings = []
    actions = []

    if any("local_runtime_unreachable" in line for line in lowered_logs) or any(
        "connection refused" in line for line in lowered_logs
    ):
        findings.append("Local model runtime is unreachable from backend service.")
        actions.append(
            "Start llama-server and verify CHIPVERIFY_LLM_BASE_URL points to its OpenAI-compatible /v1 endpoint."
        )

    if any("local_runtime_auth_failed" in line for line in lowered_logs):
        findings.append("Backend authentication to local model runtime failed.")
        actions.append(
            "Verify CHIPVERIFY_LLM_API_KEY matches llama-server --api-key configuration."
        )

    if any(
        "chipverify_llm_api_key is required by policy" in line for line in lowered_logs
    ):
        findings.append(
            "Local runtime API key policy is enabled but CHIPVERIFY_LLM_API_KEY is missing."
        )
        actions.append(
            "Set CHIPVERIFY_LLM_API_KEY in the runtime/backend environment and restart services."
        )

    if any("local_runtime_model_not_found" in line for line in lowered_logs) or any(
        "model not found" in line for line in lowered_logs
    ):
        findings.append(
            "Configured model alias is not available in local model runtime."
        )
        actions.append(
            "Set CHIPVERIFY_LLM_MODEL_ALIAS to a model listed by the runtime /v1/models endpoint."
        )

    if any("local_runtime_loading" in line for line in lowered_logs) or any(
        "loading model" in line for line in lowered_logs
    ):
        findings.append("Local model runtime is still loading model weights.")
        actions.append(
            "Wait for runtime /health to return status ok before running the pipeline."
        )

    if any("local_runtime_timeout" in line for line in lowered_logs):
        findings.append("Local model runtime request timed out under current workload.")
        actions.append(
            "Increase CHIPVERIFY_LLM_TIMEOUT_SECONDS or reduce prompt size and concurrency."
        )

    if any("gemini_api_key" in line for line in lowered_logs):
        findings.append("Legacy provider configuration warning detected in logs.")
        actions.append(
            "Set CHIPVERIFY_LLM_PROVIDER=local to enforce on-prem local runtime mode."
        )

    if any("iverilog" in line and "not found" in line for line in lowered_logs):
        findings.append(
            "iverilog is missing from PATH, so simulator compilation checks are skipped."
        )
        actions.append(
            "Install Icarus Verilog and ensure iverilog is available on PATH."
        )

    if not findings:
        findings.append(
            "No known environment signature was detected from logs; inspect the final pipeline error."
        )
        actions.append(
            "Open run logs and trace the first failing phase in orchestrator output."
        )

    steps = "\n".join(f"- {action}" for action in actions)
    observed = "\n".join(f"- {item}" for item in findings)

    return (
        "Offline debug summary (runtime assistant unavailable):\n"
        f"User prompt: {user_prompt}\n\n"
        "Observed issues:\n"
        f"{observed}\n\n"
        "Most recent pipeline failure:\n"
        f"- {pipeline_failure_line}\n\n"
        "Recommended next steps:\n"
        f"{steps}"
    )


def _authenticate_user_from_token(token: str, db: Session) -> User:
    unauthorized = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid authentication credentials",
    )

    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id = payload.get("sub")
        if not user_id:
            raise unauthorized
    except JWTError as exc:
        raise unauthorized from exc

    user = db.query(User).filter(User.id == user_id, User.is_active.is_(True)).first()
    if not user:
        raise unauthorized

    return user


def _dev_auth_allowed() -> bool:
    return os.getenv("CHIPVERIFY_ALLOW_DEV_AUTH", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _get_or_create_local_dev_user(db: Session) -> User:
    user = (
        db.query(User)
        .filter(User.is_active.is_(True))
        .order_by(User.created_at.asc())
        .first()
    )
    if user:
        return user

    email = (
        (os.environ.get("CHIPVERIFY_DEV_EMAIL") or "local@chipverify.ai")
        .strip()
        .lower()
    )
    full_name = (os.environ.get("CHIPVERIFY_DEV_NAME") or "Local Desktop User").strip()

    existing_user = db.query(User).filter(User.email == email).first()
    if existing_user:
        if not existing_user.is_active:
            existing_user.is_active = True
            db.add(existing_user)
            db.commit()
            db.refresh(existing_user)
        return existing_user

    user = User(
        id=str(uuid.uuid4()),
        email=email,
        password_hash=_hash_password(str(uuid.uuid4())),
        full_name=full_name,
        is_active=True,
    )
    db.add(user)
    try:
        db.commit()
        db.refresh(user)
        return user
    except IntegrityError:
        db.rollback()
        existing_user = db.query(User).filter(User.email == email).first()
        if existing_user:
            if not existing_user.is_active:
                existing_user.is_active = True
                db.add(existing_user)
                db.commit()
                db.refresh(existing_user)
            return existing_user
        raise


def _get_current_user(
    token: Optional[str] = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
) -> User:
    cleaned_token = (token or "").strip()
    if cleaned_token:
        try:
            return _authenticate_user_from_token(cleaned_token, db)
        except HTTPException:
            if _dev_auth_allowed():
                return _get_or_create_local_dev_user(db)
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Authentication required",
            )
    if _dev_auth_allowed():
        return _get_or_create_local_dev_user(db)
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Authentication required",
    )


def _require_project_access(db: Session, user: User, project_id: str) -> Project:
    organization = _ensure_default_organization(db, user)
    project = (
        db.query(Project)
        .filter(
            Project.id == project_id,
            Project.organization_id == organization.id,
            Project.status == "active",
        )
        .first()
    )
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    return project


@router.post("/projects/{project_id}/artifacts/{artifact_id}/index-spec")
def index_spec_document_context_endpoint(
    project_id: str,
    artifact_id: str,
    force: bool = Query(default=False),
    current_user: User = Depends(_get_current_user),
    db: Session = Depends(get_db),
):
    """Build or rebuild structural page index + local search for a spec artifact."""
    project = _require_project_access(db, current_user, project_id)
    artifact = (
        db.query(ProjectArtifact)
        .filter(
            ProjectArtifact.id == artifact_id,
            ProjectArtifact.project_id == project.id,
            ProjectArtifact.artifact_type == "spec",
        )
        .first()
    )
    if not artifact:
        raise HTTPException(status_code=404, detail="Spec artifact not found")

    doc_ctx = _index_spec_artifact_document_context(project, artifact, force=force)
    if not doc_ctx:
        raise HTTPException(status_code=400, detail="Not a spec artifact")
    try:
        meta = json.loads(artifact.metadata_json or "{}")
    except Exception:
        meta = {}
    meta["document_context"] = doc_ctx
    artifact.metadata_json = json.dumps(meta)
    db.add(artifact)
    db.commit()
    return {"artifact_id": artifact.id, "document_context": doc_ctx}


def _require_run_access(db: Session, user: User, run_id: str) -> Run:
    organization = _ensure_default_organization(db, user)
    run = (
        db.query(Run)
        .filter(Run.id == run_id, Run.organization_id == organization.id)
        .first()
    )
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")

    return run


def _require_thread_access(db: Session, user: User, thread_id: str) -> ChatThread:
    organization = _ensure_default_organization(db, user)
    thread = (
        db.query(ChatThread)
        .join(Project, Project.id == ChatThread.project_id)
        .filter(
            ChatThread.id == thread_id,
            ChatThread.user_id == user.id,
            Project.organization_id == organization.id,
        )
        .first()
    )
    if not thread:
        logger.warning(
            "Chat thread access miss: thread_id=%s user_id=%s organization_id=%s",
            thread_id,
            user.id,
            organization.id,
        )
        raise HTTPException(status_code=404, detail="Chat thread not found")

    return thread


LICENSE_PUBLIC_KEY = """-----BEGIN PUBLIC KEY-----
MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAwcdqnLftZVHQTXe/3BaP
I5twTU5N/RN1rpM8d4+IhrM5EZekptmGL60Xs3fqyC4NHj9l+3nbV31AsnSmu9oE
aOn236tsiCbN0JpQfpdYVz8KsFogLv6s21uUrzDVxgfwxWTOx9XyAZ9pFAt6rrkU
H1y7vFA5NXGZkyc0MYdEJuoNQi5mpxLlklYzW/ZkVh7y6IkwmgyWkK/gFvPnY5P9
aCVAhwC7CbDGRDoUTR8dnH8AfHHBk5G/wmcF5w4tJLqO8xQa66keFcQGWWzAeJzQ
9rnuElFzVg9e5/9Hb7P6mcvJOmx4cVJgJ7Z1pswz7X7mkhCvTHpYBO8rR05MkJS7
IQIDAQAB
-----END PUBLIC KEY-----"""


def _verify_local_license(db: Session = None):
    """
    Verifies the Electron desktop activation or local license.key file.
    Uses RS256 Asymmetric Public Key Cryptography to verify the JWT signature mathematically,
    making keygens mathematically impossible without the isolated Convex Private Key.
    """
    desktop_activation = resolve_desktop_activation()
    if desktop_activation:
        return desktop_activation

    license_path = Path(__file__).parent.parent / "license.key"
    if not license_path.exists():
        return {
            "valid": False,
            "error": "LICENSE_MISSING",
            "message": "License file (license.key) is missing in the backend directory.",
        }

    try:
        with open(license_path, "r") as f:
            license_token = f.read().strip()

        # Mathematically verify using the embedded Public Key
        payload = jwt.decode(license_token, LICENSE_PUBLIC_KEY, algorithms=["RS256"])

        # 1. Check Machine Fingerprint
        mf = get_machine_fingerprint()
        if (
            payload.get("machine_fingerprint")
            and payload.get("machine_fingerprint") != mf
        ):
            return {
                "valid": False,
                "error": "LICENSE_MACHINE_MISMATCH",
                "message": "This license is bound to a different machine/server. Application locked.",
            }

        # 2. Check Seat Limits (if DB is provided)
        max_seats = payload.get("max_seats")
        if max_seats is not None and db:
            user_count = db.query(User).count()
            if user_count > max_seats:
                return {
                    "valid": False,
                    "error": "LICENSE_SEATS_EXCEEDED",
                    "message": f"Organization seat limit ({max_seats}) exceeded. Please upgrade your enterprise plan.",
                }

        # 3. Check Expiry Time
        exp = payload.get("exp")
        if exp and datetime.now(timezone.utc).timestamp() > exp:
            return {
                "valid": False,
                "error": "LICENSE_EXPIRED",
                "message": f"License expired on {datetime.fromtimestamp(exp, tz=timezone.utc).isoformat()}. Please contact vendor.",
            }

        return {"valid": True, "payload": payload}
    except JWTError:
        return {
            "valid": False,
            "error": "LICENSE_INVALID",
            "message": "License signature is invalid or tampered with.",
        }
    except Exception as e:
        return {
            "valid": False,
            "error": "LICENSE_CHECK_FAILED",
            "message": str(e),
        }


def _check_license_validity(db: Session = Depends(get_db)):
    """Dependency for routes that require a valid license."""
    check = _verify_local_license(db)
    if not check["valid"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": check["error"], "message": check["message"]},
        )
    return check["payload"]


async def _generate_debug_assistant_response(
    user_prompt: str, run: Optional[Run]
) -> str:
    return await _generate_debug_assistant_response_with_history(user_prompt, [], run)


async def _generate_debug_assistant_response_with_history(
    user_prompt: str,
    message_history: list[dict[str, str]],
    run: Optional[Run],
) -> str:
    log_lines = _load_logs(run.logs_path, limit=200) if run else []
    logs = "\n".join(log_lines)

    client = ai_client_module.AIClient()
    system_prompt = (
        "You are an expert IC Design and UVM Verification Debug AI Assistant. "
        "The user will provide a question about their verification session. "
        "Analyze available context and provide actionable root-cause and fix steps."
    )

    run_context = (
        f"Run ID: {run.id}\nRun status: {run.status}\nProject ID: {run.project_id}"
        if run
        else "No run context was selected for this question."
    )

    user_message = (
        f"User Request: {user_prompt}\n\n"
        f"{run_context}\n\n"
        "Logs context:\n"
        f"{logs if logs else 'No run logs were provided.'}"
    )

    if client.provider == "local":
        if not client.is_generation_configured():
            if run:
                return _build_offline_debug_response(user_prompt, log_lines)

            return (
                "Offline assistant summary (local runtime configuration incomplete):\n"
                "- Required local runtime settings are missing (CHIPVERIFY_LLM_BASE_URL, CHIPVERIFY_LLM_MODEL_ALIAS, and CHIPVERIFY_LLM_API_KEY when policy requires it).\n"
                "- No run context was selected for this prompt.\n"
                "- Configure local runtime env values and retry."
            )

        if not await client.is_runtime_ready():
            if run:
                return _build_offline_debug_response(user_prompt, log_lines)

            return (
                "Offline assistant summary (local runtime unavailable):\n"
                "- Backend could not reach the local model runtime health endpoint.\n"
                "- Verify llama-server is running and CHIPVERIFY_LLM_BASE_URL is correct.\n"
                "- No run context was selected for this prompt."
            )
    elif not client.api_key:
        if run:
            return _build_offline_debug_response(user_prompt, log_lines)

        return (
            "Offline assistant summary (alternate provider unavailable):\n"
            "- CHIPVERIFY_LLM_PROVIDER is set to a legacy provider, but required credentials are missing.\n"
            "- No run context was selected for this prompt.\n"
            "- Set CHIPVERIFY_LLM_PROVIDER=local for on-prem runtime or configure selected provider credentials."
        )

    try:
        if message_history:
            messages = [{"role": "system", "content": system_prompt}] + message_history
            messages.append({"role": "user", "content": user_message})
            return await client.generate_with_messages(messages)
        else:
            return await client.generate(user_message, system_prompt=system_prompt)
    except Exception as exc:
        if run:
            return _build_offline_debug_response(user_prompt, log_lines)

        if client.provider == "local":
            return (
                "Debug assistant is temporarily unavailable for this chat message. "
                f"Local runtime detail: {exc}. "
                "Verify runtime health, model alias, and API key configuration."
            )

        return (
            "Debug assistant is temporarily unavailable for this chat message. "
            "Retry shortly or provide a completed run context for offline analysis."
        )


def _is_mental_model_chat_context(context: Optional[dict[str, Any]]) -> bool:
    if not isinstance(context, dict):
        return False
    mode = str(
        context.get("chat_mode")
        or context.get("chatMode")
        or context.get("mode")
        or ""
    ).strip().lower()
    return mode in {"mental_model", "mental-model", "mentalmodel"} or bool(
        context.get("mental_model_chat")
    )


def _compact_mental_model_for_chat(content: dict[str, Any]) -> dict[str, Any]:
    design = content.get("design") if isinstance(content.get("design"), dict) else {}
    verification = (
        content.get("verification")
        if isinstance(content.get("verification"), dict)
        else {}
    )
    project_scan = (
        content.get("project_scan")
        if isinstance(content.get("project_scan"), dict)
        else {}
    )

    def take(value: Any, limit: int) -> list:
        return value[:limit] if isinstance(value, list) else []

    return {
        "schema_version": content.get("schema_version"),
        "status": content.get("status", "ready"),
        "design": {
            "top_module": design.get("top_module"),
            "modules": take(design.get("modules"), 40),
            "hierarchy_tree": design.get("hierarchy_tree") or {},
            "ports": take(design.get("ports"), 80),
            "parameters": take(design.get("parameters"), 40),
            "clock_domains": take(design.get("clock_domains"), 20),
            "sub_instances": take(design.get("sub_instances"), 40),
            "fsms": take(design.get("fsms"), 20),
            "protocols": take(design.get("protocols"), 20),
            "total_files": design.get("total_files"),
            "total_lines": design.get("total_lines"),
        },
        "project_scan": {
            "rtl_files": take(project_scan.get("rtl_files"), 60),
            "spec_files": take(project_scan.get("spec_files"), 20),
        },
        "requirements": take(content.get("requirements"), 60),
        "verification": {
            "unit_tests": take(verification.get("unit_tests"), 40),
            "formal_properties": take(verification.get("formal_properties"), 40),
            "coverage_points": take(verification.get("coverage_points"), 60),
            "uvm_scenarios": take(verification.get("uvm_scenarios"), 40),
            "scoreboard_checks": take(verification.get("scoreboard_checks"), 40),
        },
        "risks": take(content.get("risks"), 40),
        "open_questions": take(content.get("open_questions"), 40),
    }


async def _generate_mental_model_assistant_response_with_history(
    user_prompt: str,
    message_history: list[dict[str, str]],
    model: Optional[MentalModelRevision],
) -> str:
    if not model:
        return (
            "Mental Model Chat needs a saved mental model for this project first. "
            "Build or refresh the mental model from the selected spec and RTL, then ask again."
        )

    content = _load_mental_model_content(model)
    compact = _compact_mental_model_for_chat(content)
    compact["revision_metadata"] = {
        "id": model.id,
        "revision": model.revision,
        "status": model.status,
        "schema_version": model.schema_version,
        "source_spec_artifact_id": model.source_spec_artifact_id,
        "source_rtl_artifact_id": model.source_rtl_artifact_id,
        "summary_text": model.summary_text,
    }

    serialized = json.dumps(compact, indent=2)
    if len(serialized) > 18000:
        serialized = serialized[:18000] + "\n...<mental model context truncated>"

    client = ai_client_module.AIClient()
    system_prompt = (
        "You are the Mental Model Agent for the active chip design. "
        "Use the persisted mental model as the source of truth. "
        "Do not invent behavior, protocols, requirements, coverage, bugs, or UVM structure. "
        "If a detail is not captured, say it is not captured and suggest rebuilding/refining the model or inspecting source."
    )

    if client.provider == "local":
        if not client.is_generation_configured() or not await client.is_runtime_ready():
            top = compact.get("design", {}).get("top_module") or "unknown"
            ports = len(compact.get("design", {}).get("ports") or [])
            reqs = len(compact.get("requirements") or [])
            return (
                f"Mental model revision {model.revision} is loaded, but the LLM runtime is unavailable. "
                f"Captured summary: top module `{top}`, {ports} port(s), {reqs} requirement(s). "
                "Ask again after the runtime is healthy for a full natural-language answer."
            )
    elif not client.api_key:
        return (
            "Mental model is loaded, but the configured LLM provider is missing credentials. "
            "Configure the provider or switch to the local runtime, then ask again."
        )

    messages = [
        {
            "role": "system",
            "content": (
                f"{system_prompt}\n\nPersisted mental model context:\n"
                f"```json\n{serialized}\n```"
            ),
        }
    ]
    messages.extend(message_history[-20:])
    messages.append({"role": "user", "content": user_prompt})

    try:
        return await client.generate_with_messages(messages)
    except Exception as exc:
        return (
            "Mental Model Chat could not complete this response. "
            f"Runtime detail: {exc}"
        )


def _iter_text_chunks(text: str, chunk_size: int = 180):
    if chunk_size <= 0:
        yield text
        return

    index = 0
    while index < len(text):
        yield text[index : index + chunk_size]
        index += chunk_size


async def _send_ws_error_and_close(
    websocket: WebSocket,
    detail: str,
    close_code: int = 4400,
):
    try:
        await websocket.send_json({"type": "error", "detail": detail})
    except Exception:
        logger.warning("Failed to send websocket error payload", exc_info=True)

    try:
        await websocket.close(code=close_code)
    except Exception:
        logger.warning("Failed to close websocket", exc_info=True)


@router.get("/health")
def health_endpoint():
    return {
        "status": "ok",
        "product": PLATFORM_PROFILE["product"],
        "mode": PLATFORM_PROFILE["mode"],
        "storage": "local",
        "positioning": PLATFORM_PROFILE["positioning"],
    }


@router.get("/platform/profile")
def platform_profile_endpoint():
    return PLATFORM_PROFILE


def _parse_user_doc_markdown(content: str, fallback_name: str) -> dict[str, str]:
    title = Path(fallback_name).stem.replace("-", " ").replace("_", " ").title()
    description = ""
    lines = content.splitlines()
    past_title = False
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("#"):
            title = stripped.lstrip("#").strip() or title
            past_title = True
            continue
        if past_title and not description:
            description = stripped
            break
    return {"title": title, "description": description}


def _docs_index_sort_key(entry: dict[str, Any]) -> tuple[int, int]:
    return docs_index_sort_key(str(entry.get("name") or ""))


@router.get("/docs/index")
def docs_index_endpoint(current_user: User = Depends(_get_current_user)):
    _ = current_user
    docs = []
    if not DOCS_DIR.exists():
        return {"docs": docs}

    for path in sorted(DOCS_DIR.glob("*.md")):
        if not is_user_facing_doc(path.name):
            continue
        try:
            content = path.read_text(encoding="utf-8")
            meta = _parse_user_doc_markdown(content, path.name)
            updated_at = int(path.stat().st_mtime)
        except Exception:
            meta = {"title": path.stem.replace("-", " ").title(), "description": ""}
            updated_at = 0

        docs.append(
            {
                "name": path.name,
                "title": meta["title"],
                "description": meta["description"],
                "updated_at": updated_at,
            }
        )

    docs.sort(key=_docs_index_sort_key)
    return {"docs": docs}


@router.get("/docs/{doc_name}")
def docs_content_endpoint(
    doc_name: str, current_user: User = Depends(_get_current_user)
):
    _ = current_user
    safe_name = Path(doc_name).name
    if safe_name != doc_name or not safe_name.endswith(".md"):
        raise HTTPException(status_code=400, detail="Invalid documentation file name")

    if not is_user_facing_doc(safe_name):
        raise HTTPException(status_code=404, detail="Documentation file not found")

    docs_root = DOCS_DIR.resolve()
    doc_path = (DOCS_DIR / safe_name).resolve()
    if docs_root not in doc_path.parents or not doc_path.exists():
        raise HTTPException(status_code=404, detail="Documentation file not found")

    content = doc_path.read_text(encoding="utf-8")
    meta = _parse_user_doc_markdown(content, safe_name)

    return {
        "name": safe_name,
        "title": meta["title"],
        "description": meta["description"],
        "content": content,
        "updated_at": int(doc_path.stat().st_mtime),
    }


@router.post("/auth/register")
def register_endpoint(req: RegisterRequest, db: Session = Depends(get_db)):
    # Registration is disabled for on-prem enterprise deployments.
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Self-registration is disabled. Please contact your system administrator for credentials.",
    )


@router.post("/auth/login")
def login_endpoint(req: LoginRequest, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == req.email.lower()).first()
    if not user or not _verify_password(req.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid email or password")

    if not user.is_active:
        raise HTTPException(status_code=403, detail="User account is disabled")

    organization = _ensure_default_organization(db, user)
    _ensure_starter_project(db, user, organization)
    access_token = _create_access_token(user.id)

    return {
        "access_token": access_token,
        "token_type": "bearer",
        "user": _serialize_user(user),
        "organization": {
            "id": organization.id,
            "name": organization.name,
            "slug": organization.slug,
        },
    }


@router.post("/auth/auto-login")
def auto_login_endpoint(db: Session = Depends(get_db)):
    """AUTH BYPASS: Auto-login as the first active user without credentials."""
    user = _get_or_create_local_dev_user(db)
    organization = _ensure_default_organization(db, user)
    _ensure_starter_project(db, user, organization)
    access_token = _create_access_token(user.id)

    return {
        "access_token": access_token,
        "token_type": "bearer",
        "user": _serialize_user(user),
        "organization": {
            "id": organization.id,
            "name": organization.name,
            "slug": organization.slug,
        },
    }


@router.get("/auth/me")
def me_endpoint(
    current_user: User = Depends(_get_current_user),
    db: Session = Depends(get_db),
    _license: dict = Depends(_check_license_validity),
):
    organization = _ensure_default_organization(db, current_user)
    return {
        "user": _serialize_user(current_user),
        "organization": {
            "id": organization.id,
            "name": organization.name,
            "slug": organization.slug,
        },
    }


@router.get("/projects")
def list_projects_endpoint(
    current_user: User = Depends(_get_current_user),
    db: Session = Depends(get_db),
    _license: dict = Depends(_check_license_validity),
):
    organization = _ensure_default_organization(db, current_user)
    projects = (
        db.query(Project)
        .filter(Project.organization_id == organization.id, Project.status == "active")
        .order_by(Project.created_at.desc())
        .all()
    )
    return [_serialize_project(project) for project in projects]


@router.post("/projects")
def create_project_endpoint(
    req: ProjectCreateRequest,
    current_user: User = Depends(_get_current_user),
    db: Session = Depends(get_db),
):
    organization = _ensure_default_organization(db, current_user)
    name = req.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Project name is required")

    project = Project(
        id=str(uuid.uuid4()),
        organization_id=organization.id,
        owner_user_id=current_user.id,
        name=name,
        slug=_unique_project_slug(db, organization.id, name),
        description=req.description.strip() if req.description else None,
        status="active",
    )
    db.add(project)
    db.commit()
    db.refresh(project)

    return _serialize_project(project)


@router.get("/projects/{project_id}")
def get_project_endpoint(
    project_id: str,
    current_user: User = Depends(_get_current_user),
    db: Session = Depends(get_db),
):
    project = _require_project_access(db, current_user, project_id)
    return _serialize_project(project)


@router.delete("/projects/{project_id}")
def delete_project_endpoint(
    project_id: str,
    current_user: User = Depends(_get_current_user),
    db: Session = Depends(get_db),
):
    organization = _ensure_default_organization(db, current_user)
    project = (
        db.query(Project)
        .filter(
            Project.id == project_id,
            Project.organization_id == organization.id,
        )
        .first()
    )
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    if project.status != "active":
        return {
            "deleted": True,
            "project_id": project.id,
            "status": project.status,
            "message": "Project already archived",
        }

    project.status = "archived"
    db.add(project)
    db.commit()

    return {
        "deleted": True,
        "project_id": project.id,
        "status": project.status,
        "message": "Project archived",
    }


@router.post("/projects/{project_id}/artifacts/spec", status_code=201)
async def create_spec_artifact_endpoint(
    project_id: str,
    content: Optional[str] = Form(None),
    source: Optional[str] = Form("upload"),
    artifact_file: Optional[UploadFile] = File(None),
    current_user: User = Depends(_get_current_user),
    db: Session = Depends(get_db),
):
    project = _require_project_access(db, current_user, project_id)

    if bool(content) == bool(artifact_file):
        raise HTTPException(
            status_code=400,
            detail="Provide exactly one of content or artifact_file for spec artifact",
        )

    if artifact_file:
        raw_bytes = await artifact_file.read()
        filename = artifact_file.filename or "spec.txt"
        content_type = artifact_file.content_type
        metadata = {
            "provided_as": "file",
            "analysis": _analyze_spec_bytes(raw_bytes, filename),
        }
    else:
        inline_content = content or ""
        preview_text, preview_truncated = _build_text_preview(inline_content)
        raw_bytes = (content or "").encode("utf-8")
        filename = "spec.txt"
        content_type = "text/plain"
        metadata = {
            "provided_as": "text",
            "analysis": {
                "status": "parsed",
                "parser": "inline_text",
                "detected_extension": ".txt",
                "character_count": len(inline_content),
                "line_count": len(inline_content.splitlines()),
                "preview_text": preview_text,
                "preview_truncated": preview_truncated,
            },
        }

    artifact, pointer = _create_project_artifact_revision(
        db,
        project,
        current_user,
        artifact_type="spec",
        raw_bytes=raw_bytes,
        filename=filename,
        source=source or "upload",
        content_type=content_type,
        metadata=metadata,
        reuse_identical=True,
    )

    doc_ctx = _index_spec_artifact_document_context(project, artifact)
    if doc_ctx:
        try:
            meta = json.loads(artifact.metadata_json or "{}")
        except Exception:
            meta = {}
        meta["document_context"] = doc_ctx
        artifact.metadata_json = json.dumps(meta)
        db.add(artifact)
        db.commit()
        db.refresh(artifact)

    payload = _serialize_project_artifact(artifact)
    if doc_ctx:
        payload.setdefault("metadata", {})["document_context"] = doc_ctx

    graph_ctx = _enqueue_codebase_graph_build(db, project, trigger="spec_upload")
    if graph_ctx:
        payload.setdefault("metadata", {})["codebase_graph"] = graph_ctx

    return {
        "artifact": payload,
        "active": _serialize_artifact_pointer(pointer),
    }


@router.post("/projects/{project_id}/artifacts/rtl", status_code=201)
async def create_rtl_artifact_endpoint(
    project_id: str,
    content: Optional[str] = Form(None),
    source: Optional[str] = Form("upload"),
    artifact_file: Optional[UploadFile] = File(None),
    current_user: User = Depends(_get_current_user),
    db: Session = Depends(get_db),
):
    project = _require_project_access(db, current_user, project_id)

    if bool(content) == bool(artifact_file):
        raise HTTPException(
            status_code=400,
            detail="Provide exactly one of content or artifact_file for RTL artifact",
        )

    if artifact_file:
        raw_bytes = await artifact_file.read()
        filename = artifact_file.filename or "rtl_context.sv"
        content_type = artifact_file.content_type
        metadata = {
            "provided_as": "file",
            "analysis": _analyze_rtl_bytes(raw_bytes, filename),
        }
    else:
        raw_bytes = (content or "").encode("utf-8")
        filename = "rtl_context.sv"
        content_type = "text/plain"
        metadata = {
            "provided_as": "text",
            "analysis": _analyze_rtl_bytes(raw_bytes, filename),
        }

    artifact, pointer = _create_project_artifact_revision(
        db,
        project,
        current_user,
        artifact_type="rtl",
        raw_bytes=raw_bytes,
        filename=filename,
        source=source or "upload",
        content_type=content_type,
        metadata=metadata,
        reuse_identical=True,
    )

    rtl_ctx = _index_rtl_artifact_context(project, artifact)
    payload = _serialize_project_artifact(artifact)
    if rtl_ctx:
        try:
            meta = json.loads(artifact.metadata_json or "{}")
        except Exception:
            meta = {}
        meta["rtl_context"] = rtl_ctx
        artifact.metadata_json = json.dumps(meta)
        db.add(artifact)
        db.commit()
        db.refresh(artifact)
        payload.setdefault("metadata", {})["rtl_context"] = rtl_ctx

    graph_ctx = _enqueue_codebase_graph_build(db, project, trigger="rtl_upload")
    if graph_ctx:
        payload.setdefault("metadata", {})["codebase_graph"] = graph_ctx

    return {
        "artifact": payload,
        "active": _serialize_artifact_pointer(pointer),
    }


@router.post("/projects/{project_id}/artifacts/rtl-project", status_code=201)
async def create_rtl_project_artifact_endpoint(
    project_id: str,
    source: Optional[str] = Form("upload"),
    archive_name: Optional[str] = Form("rtl_project.zip"),
    relative_paths: Optional[List[str]] = Form(None),
    artifact_files: List[UploadFile] = File(...),
    current_user: User = Depends(_get_current_user),
    db: Session = Depends(get_db),
):
    project = _require_project_access(db, current_user, project_id)

    if not artifact_files:
        raise HTTPException(status_code=400, detail="Provide at least one RTL source file")
    if len(artifact_files) > MAX_ARCHIVE_FILES:
        raise HTTPException(
            status_code=400,
            detail=f"RTL project upload exceeds {MAX_ARCHIVE_FILES} files",
        )

    archive_filename = _safe_archive_name(archive_name)
    relative_paths = relative_paths or []
    total_bytes = 0
    included_paths: list[str] = []
    included_project_paths: list[str] = []

    with tempfile.NamedTemporaryFile(prefix="chipverify_rtl_project_", suffix=".zip", delete=False) as tmp:
        temp_zip_path = Path(tmp.name)

    try:
        with zipfile.ZipFile(temp_zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            used_paths: set[str] = set()
            for index, upload in enumerate(artifact_files):
                raw = await upload.read()
                if not raw:
                    continue

                fallback_name = _safe_filename(upload.filename, f"rtl_{index}.sv")
                rel_path = _safe_rtl_project_relative_path(
                    relative_paths[index] if index < len(relative_paths) else None,
                    fallback_name,
                )
                if not _is_workspace_project_member_filename(rel_path):
                    continue

                total_bytes += len(raw)
                if total_bytes > MAX_ARCHIVE_BYTES:
                    raise HTTPException(
                        status_code=400,
                        detail=f"RTL project upload exceeds {MAX_ARCHIVE_BYTES // (1024 * 1024)} MB",
                    )

                deduped_path = rel_path
                suffix = 1
                while deduped_path in used_paths:
                    rel = PurePosixPath(rel_path)
                    deduped_path = f"{rel.parent.as_posix().strip('/')}/{rel.stem}_{suffix}{rel.suffix}" if str(rel.parent) != "." else f"{rel.stem}_{suffix}{rel.suffix}"
                    suffix += 1

                used_paths.add(deduped_path)
                included_project_paths.append(deduped_path)
                if is_rtl_source_filename(deduped_path):
                    included_paths.append(deduped_path)
                archive.writestr(deduped_path, raw)

        if not included_paths:
            raise HTTPException(
                status_code=400,
                detail="RTL project upload did not include any .v, .sv, .svh, .vh, .vhd, or .vhdl files",
            )

        raw_bytes = temp_zip_path.read_bytes()
        analysis = _analyze_rtl_bytes(raw_bytes, archive_filename)
        if analysis.get("status") == "error":
            raise HTTPException(
                status_code=400,
                detail=analysis.get("error") or "Unable to analyze RTL project archive",
            )

        artifact, pointer = _create_project_artifact_revision(
            db,
            project,
            current_user,
            artifact_type="rtl",
            raw_bytes=raw_bytes,
            filename=archive_filename,
            source=source or "upload",
            content_type="application/zip",
            metadata={
                "provided_as": "rtl_project",
                "analysis": analysis,
                "uploaded_file_count": len(artifact_files),
                "included_project_file_count": len(included_project_paths),
                "included_project_files": included_project_paths[:200],
                "included_workspace_files": included_project_paths[:200],
                "included_rtl_file_count": len(included_paths),
                "included_rtl_files": included_paths[:100],
            },
            reuse_identical=True,
        )
    finally:
        temp_zip_path.unlink(missing_ok=True)

    rtl_ctx = _index_rtl_artifact_context(project, artifact)
    payload = _serialize_project_artifact(artifact)
    if rtl_ctx:
        try:
            meta = json.loads(artifact.metadata_json or "{}")
        except Exception:
            meta = {}
        meta["rtl_context"] = rtl_ctx
        artifact.metadata_json = json.dumps(meta)
        db.add(artifact)
        db.commit()
        db.refresh(artifact)
        payload.setdefault("metadata", {})["rtl_context"] = rtl_ctx

    graph_ctx = _enqueue_codebase_graph_build(db, project, trigger="rtl_project_upload")
    if graph_ctx:
        payload.setdefault("metadata", {})["codebase_graph"] = graph_ctx

    return {
        "artifact": payload,
        "active": _serialize_artifact_pointer(pointer),
    }


@router.post("/projects/{project_id}/artifacts/generated", status_code=201)
async def create_generated_artifact_endpoint(
    project_id: str,
    content: Optional[str] = Form(None),
    source: Optional[str] = Form("upload"),
    relative_path: Optional[str] = Form(None),
    artifact_file: Optional[UploadFile] = File(None),
    current_user: User = Depends(_get_current_user),
    db: Session = Depends(get_db),
):
    project = _require_project_access(db, current_user, project_id)

    if bool(content) == bool(artifact_file):
        raise HTTPException(
            status_code=400,
            detail="Provide exactly one of content or artifact_file for generated artifact",
        )

    if artifact_file:
        raw_bytes = await artifact_file.read()
        filename = artifact_file.filename or "generated.txt"
        content_type = artifact_file.content_type or _guess_content_type(Path(filename))
        provided_as = "file"
    else:
        inline_content = content or ""
        raw_bytes = inline_content.encode("utf-8")
        filename = Path(relative_path or "generated.txt").name or "generated.txt"
        content_type = "text/plain"
        provided_as = "text"

    normalized_relative_path = _normalize_relative_artifact_path(
        relative_path or filename
    )

    artifact, _ = _create_project_artifact_revision(
        db,
        project,
        current_user,
        artifact_type="generated",
        raw_bytes=raw_bytes,
        filename=filename,
        source=source or "upload",
        content_type=content_type,
        metadata={
            "provided_as": provided_as,
            "relative_path": normalized_relative_path,
            "editable": _is_text_content_type(content_type)
            or _is_probably_text_bytes(raw_bytes),
        },
    )

    pointer = _get_project_artifact_pointer(db, project.id)
    return {
        "artifact": _serialize_project_artifact(artifact),
        "active": _serialize_artifact_pointer(pointer),
    }


@router.get("/projects/{project_id}/artifacts")
def list_project_artifacts_endpoint(
    project_id: str,
    current_user: User = Depends(_get_current_user),
    db: Session = Depends(get_db),
    _license: dict = Depends(_check_license_validity),
):
    project = _require_project_access(db, current_user, project_id)
    artifacts = (
        db.query(ProjectArtifact)
        .filter(ProjectArtifact.project_id == project.id)
        .order_by(ProjectArtifact.artifact_type.asc(), ProjectArtifact.revision.desc())
        .all()
    )
    pointer = _get_project_artifact_pointer(db, project.id)

    spec_artifacts = [
        _serialize_project_artifact(artifact)
        for artifact in artifacts
        if artifact.artifact_type == "spec"
    ]
    rtl_artifacts = [
        _serialize_project_artifact(artifact)
        for artifact in artifacts
        if artifact.artifact_type == "rtl"
    ]
    generated_artifacts = [
        _serialize_project_artifact(artifact)
        for artifact in artifacts
        if artifact.artifact_type == "generated"
    ]

    return {
        "project_id": project.id,
        "active": _serialize_artifact_pointer(pointer),
        "artifacts": {
            "spec": spec_artifacts,
            "rtl": rtl_artifacts,
            "generated": generated_artifacts,
        },
    }


@router.get("/projects/{project_id}/artifacts/{artifact_id}/members/{member_path:path}")
def get_rtl_project_member_endpoint(
    project_id: str,
    artifact_id: str,
    member_path: str,
    current_user: User = Depends(_get_current_user),
    db: Session = Depends(get_db),
):
    project = _require_project_access(db, current_user, project_id)
    artifact = _require_project_artifact(db, project, artifact_id, artifact_type="rtl")
    content = _read_rtl_project_member_content(artifact, member_path)
    normalized_path = _normalize_project_member_path(member_path)
    return {
        "artifact_id": artifact.id,
        "member_path": normalized_path,
        "content": content,
        "content_source": "rtl_project_member",
    }


@router.patch("/projects/{project_id}/artifacts/{artifact_id}/members/{member_path:path}")
def update_rtl_project_member_endpoint(
    project_id: str,
    artifact_id: str,
    member_path: str,
    req: RtlProjectMemberUpdateRequest,
    current_user: User = Depends(_get_current_user),
    db: Session = Depends(get_db),
):
    project = _require_project_access(db, current_user, project_id)
    artifact = _require_project_artifact(db, project, artifact_id, artifact_type="rtl")
    normalized_path = _normalize_project_member_path(member_path)
    raw_zip, included_paths = _replace_rtl_project_member_content(
        artifact,
        normalized_path,
        req.content,
    )

    archive_filename = _safe_archive_name(artifact.filename or "rtl_project.zip")
    analysis = _analyze_rtl_bytes(raw_zip, archive_filename)
    if analysis.get("status") == "error":
        raise HTTPException(
            status_code=400,
            detail=analysis.get("error") or "Unable to analyze updated RTL project archive",
        )

    updated_artifact, pointer = _create_project_artifact_revision(
        db,
        project,
        current_user,
        artifact_type="rtl",
        raw_bytes=raw_zip,
        filename=archive_filename,
        source="rtl_project_member_edit",
        content_type="application/zip",
        metadata={
            "provided_as": "rtl_project",
            "analysis": analysis,
            "uploaded_file_count": len(included_paths),
            "included_rtl_file_count": len(included_paths),
            "included_rtl_files": included_paths[:100],
            "edited_member_path": normalized_path,
            "parent_artifact_id": artifact.id,
        },
        reuse_identical=True,
    )

    return {
        "artifact": _serialize_project_artifact(updated_artifact),
        "active": _serialize_artifact_pointer(pointer),
        "member_path": normalized_path,
    }


@router.get("/projects/{project_id}/artifacts/{artifact_id}")
def get_project_artifact_endpoint(
    project_id: str,
    artifact_id: str,
    include_content: bool = Query(default=False),
    current_user: User = Depends(_get_current_user),
    db: Session = Depends(get_db),
):
    project = _require_project_access(db, current_user, project_id)
    artifact = (
        db.query(ProjectArtifact)
        .filter(
            ProjectArtifact.id == artifact_id,
            ProjectArtifact.project_id == project.id,
            ProjectArtifact.organization_id == project.organization_id,
        )
        .first()
    )
    if not artifact:
        raise HTTPException(status_code=404, detail="Artifact not found")

    payload = _serialize_project_artifact(artifact)

    if include_content:
        artifact_path = Path(artifact.file_path)
        if not artifact_path.exists():
            raise HTTPException(status_code=404, detail="Artifact file is missing")

        content_text = ""
        content_source = "raw"

        if artifact.artifact_type == "rtl" and is_rtl_archive_filename(artifact.filename):
            members = _list_rtl_project_members(artifact)
            payload["content"] = ""
            payload["content_truncated"] = False
            payload["content_source"] = "rtl_project_archive"
            payload["content_metadata"] = {
                "members": members,
                "member_count": len(members),
            }
            return payload

        if artifact.artifact_type == "spec":
            try:
                parsed_text, parser_name, suffix = _extract_spec_text(
                    artifact_path,
                    artifact.filename,
                )
                content_text = parsed_text
                content_source = "parsed_spec_text"
                payload["content_metadata"] = {
                    "parser": parser_name,
                    "detected_extension": suffix or None,
                }
            except Exception as exc:
                payload["content_metadata"] = {
                    "parser": "raw_fallback",
                    "error": str(exc),
                }

        if not content_text:
            content_text = artifact_path.read_bytes().decode("utf-8", errors="replace")

        content_bytes = content_text.encode("utf-8")
        is_truncated = len(content_bytes) > MAX_ARTIFACT_PREVIEW_BYTES
        if is_truncated:
            content_text = content_bytes[:MAX_ARTIFACT_PREVIEW_BYTES].decode(
                "utf-8",
                errors="ignore",
            )

        payload["content"] = content_text
        payload["content_truncated"] = is_truncated
        payload["content_source"] = content_source

    return payload


@router.patch("/projects/{project_id}/artifacts/{artifact_id}/content")
def update_project_artifact_content_endpoint(
    project_id: str,
    artifact_id: str,
    req: ArtifactContentUpdateRequest,
    current_user: User = Depends(_get_current_user),
    db: Session = Depends(get_db),
):
    project = _require_project_access(db, current_user, project_id)
    artifact = (
        db.query(ProjectArtifact)
        .filter(
            ProjectArtifact.id == artifact_id,
            ProjectArtifact.project_id == project.id,
            ProjectArtifact.organization_id == project.organization_id,
        )
        .first()
    )
    if not artifact:
        raise HTTPException(status_code=404, detail="Artifact not found")
    if artifact.artifact_type != "generated":
        raise HTTPException(
            status_code=400,
            detail="Only generated artifacts can be edited through this endpoint",
        )

    raw_bytes = req.content.encode("utf-8")
    content_type = artifact.content_type or "text/plain"
    if not (_is_text_content_type(content_type) or _is_probably_text_bytes(raw_bytes)):
        raise HTTPException(status_code=400, detail="Artifact is not editable text")

    metadata: dict[str, Any] = {}
    if artifact.metadata_json:
        try:
            parsed = json.loads(artifact.metadata_json)
            if isinstance(parsed, dict):
                metadata.update(parsed)
        except Exception:
            metadata["previous_metadata_raw"] = artifact.metadata_json

    metadata.update(
        {
            "editable": True,
            "provided_as": "edited_text",
            "parent_artifact_id": metadata.get("parent_artifact_id") or artifact.id,
            "edited_from_artifact_id": artifact.id,
            "edited_at": _iso(datetime.now(timezone.utc)),
        }
    )

    updated_artifact, pointer = _create_project_artifact_revision(
        db,
        project,
        current_user,
        artifact_type="generated",
        raw_bytes=raw_bytes,
        filename=artifact.filename,
        source="editor",
        content_type=content_type,
        metadata=metadata,
    )
    return {
        "artifact": _serialize_project_artifact(updated_artifact),
        "active": _serialize_artifact_pointer(pointer),
    }


@router.get("/projects/{project_id}/artifacts/{artifact_id}/download")
def download_project_artifact_endpoint(
    project_id: str,
    artifact_id: str,
    current_user: User = Depends(_get_current_user),
    db: Session = Depends(get_db),
):
    project = _require_project_access(db, current_user, project_id)
    artifact = (
        db.query(ProjectArtifact)
        .filter(
            ProjectArtifact.id == artifact_id,
            ProjectArtifact.project_id == project.id,
            ProjectArtifact.organization_id == project.organization_id,
        )
        .first()
    )
    if not artifact:
        raise HTTPException(status_code=404, detail="Artifact not found")

    artifact_path = Path(artifact.file_path)
    if not artifact_path.exists():
        raise HTTPException(status_code=404, detail="Artifact file is missing")

    return FileResponse(
        str(artifact_path),
        filename=artifact.filename,
        media_type=artifact.content_type or "application/octet-stream",
    )


@router.post("/projects/{project_id}/artifacts/download-zip")
def download_project_artifacts_zip_endpoint(
    project_id: str,
    req: ArtifactZipDownloadRequest,
    current_user: User = Depends(_get_current_user),
    db: Session = Depends(get_db),
):
    project = _require_project_access(db, current_user, project_id)
    requested_ids = [str(item).strip() for item in (req.artifact_ids or []) if str(item).strip()]
    requested_ids = list(dict.fromkeys(requested_ids))
    if not requested_ids:
        raise HTTPException(status_code=400, detail="Provide at least one artifact id")
    if len(requested_ids) > 250:
        raise HTTPException(status_code=400, detail="Too many artifacts requested for one download")

    artifacts = (
        db.query(ProjectArtifact)
        .filter(
            ProjectArtifact.id.in_(requested_ids),
            ProjectArtifact.project_id == project.id,
            ProjectArtifact.organization_id == project.organization_id,
        )
        .all()
    )
    by_id = {artifact.id: artifact for artifact in artifacts}
    ordered = [by_id[item] for item in requested_ids if item in by_id]
    if not ordered:
        raise HTTPException(status_code=404, detail="No downloadable artifacts found")

    def _zip_member_name(artifact: ProjectArtifact, used: set[str]) -> str:
        metadata = {}
        if artifact.metadata_json:
            try:
                parsed = json.loads(artifact.metadata_json)
                if isinstance(parsed, dict):
                    metadata = parsed
            except Exception:
                metadata = {}
        candidate = str(
            metadata.get("relative_path")
            or metadata.get("source_relative_path")
            or artifact.filename
            or f"{artifact.id}.txt"
        ).replace("\\", "/").strip().strip("/")
        pure = PurePosixPath(candidate)
        parts = tuple(part for part in pure.parts if part not in {"", ".", ".."})
        if not parts or pure.is_absolute():
            parts = (artifact.filename or f"{artifact.id}.txt",)
        normalized = "/".join(part.replace(":", "_") for part in parts)
        if not normalized:
            normalized = artifact.filename or f"{artifact.id}.txt"
        final_name = normalized
        suffix = 1
        while final_name in used:
            path = PurePosixPath(normalized)
            stem = path.stem or "artifact"
            ext = path.suffix
            parent = "" if str(path.parent) == "." else path.parent.as_posix().strip("/")
            base = f"{stem}_{suffix}{ext}"
            final_name = f"{parent}/{base}" if parent else base
            suffix += 1
        used.add(final_name)
        return final_name

    archive_buffer = io.BytesIO()
    used_names: set[str] = set()
    added = 0
    with zipfile.ZipFile(archive_buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for artifact in ordered:
            artifact_path = Path(artifact.file_path or "")
            if not artifact_path.exists() or not artifact_path.is_file():
                continue
            archive.write(artifact_path, _zip_member_name(artifact, used_names))
            added += 1

    if added == 0:
        raise HTTPException(status_code=404, detail="Artifact files are missing from disk")

    archive_buffer.seek(0)
    safe_name = _safe_archive_name(req.filename or f"chipverify_project_{project.id}_artifacts.zip")
    headers = {"Content-Disposition": f'attachment; filename="{safe_name}"'}
    return StreamingResponse(
        archive_buffer,
        media_type="application/zip",
        headers=headers,
    )


@router.delete("/projects/{project_id}/artifacts/{artifact_id}")
def delete_project_artifact_endpoint(
    project_id: str,
    artifact_id: str,
    current_user: User = Depends(_get_current_user),
    db: Session = Depends(get_db),
):
    project = _require_project_access(db, current_user, project_id)
    artifact = (
        db.query(ProjectArtifact)
        .filter(
            ProjectArtifact.id == artifact_id,
            ProjectArtifact.project_id == project.id,
            ProjectArtifact.organization_id == project.organization_id,
        )
        .first()
    )
    if not artifact:
        raise HTTPException(status_code=404, detail="Artifact not found")

    pointer = _get_project_artifact_pointer(db, project.id)
    if pointer:
        if (
            artifact.artifact_type == "spec"
            and pointer.active_spec_artifact_id == artifact.id
        ):
            replacement_spec = (
                db.query(ProjectArtifact)
                .filter(
                    ProjectArtifact.project_id == project.id,
                    ProjectArtifact.artifact_type == "spec",
                    ProjectArtifact.id != artifact.id,
                )
                .order_by(ProjectArtifact.revision.desc())
                .first()
            )
            pointer.active_spec_artifact_id = (
                replacement_spec.id if replacement_spec else None
            )

        if (
            artifact.artifact_type == "rtl"
            and pointer.active_rtl_artifact_id == artifact.id
        ):
            replacement_rtl = (
                db.query(ProjectArtifact)
                .filter(
                    ProjectArtifact.project_id == project.id,
                    ProjectArtifact.artifact_type == "rtl",
                    ProjectArtifact.id != artifact.id,
                )
                .order_by(ProjectArtifact.revision.desc())
                .first()
            )
            pointer.active_rtl_artifact_id = (
                replacement_rtl.id if replacement_rtl else None
            )

        db.add(pointer)

    artifact_path = Path(artifact.file_path)
    db.delete(artifact)
    db.commit()

    if artifact_path.exists():
        try:
            artifact_path.unlink()
        except Exception:
            pass

    return {
        "deleted": True,
        "project_id": project.id,
        "artifact_id": artifact.id,
        "artifact_type": artifact.artifact_type,
        "active": _serialize_artifact_pointer(pointer),
    }


@router.patch("/projects/{project_id}/artifacts/{artifact_id}/rename")
def rename_project_artifact_endpoint(
    project_id: str,
    artifact_id: str,
    req: ArtifactRenameRequest,
    current_user: User = Depends(_get_current_user),
    db: Session = Depends(get_db),
):
    project = _require_project_access(db, current_user, project_id)
    artifact = (
        db.query(ProjectArtifact)
        .filter(
            ProjectArtifact.id == artifact_id,
            ProjectArtifact.project_id == project.id,
            ProjectArtifact.organization_id == project.organization_id,
        )
        .first()
    )
    if not artifact:
        raise HTTPException(status_code=404, detail="Artifact not found")

    safe_name = _safe_filename(req.filename, artifact.filename)
    if not safe_name:
        raise HTTPException(status_code=400, detail="Artifact filename is required")

    artifact.filename = safe_name

    if artifact.artifact_type == "generated":
        metadata = {}
        if artifact.metadata_json:
            try:
                metadata = json.loads(artifact.metadata_json)
            except Exception:
                metadata = {}

        source_relative_path = _normalize_relative_artifact_path(
            str(
                metadata.get("source_relative_path")
                or metadata.get("relative_path")
                or ""
            )
        )
        relative_path = _normalize_relative_artifact_path(
            str(metadata.get("relative_path") or source_relative_path)
        )
        if relative_path:
            parent = Path(relative_path).parent
            parent_path = "" if str(parent) == "." else str(parent).replace("\\", "/")
            metadata["relative_path"] = (
                f"{parent_path}/{safe_name}" if parent_path else safe_name
            )
        if source_relative_path:
            metadata["source_relative_path"] = source_relative_path

        artifact.metadata_json = json.dumps(metadata) if metadata else None

    db.add(artifact)
    db.commit()
    db.refresh(artifact)

    return {
        "project_id": project.id,
        "artifact": _serialize_project_artifact(artifact),
    }


@router.patch("/projects/{project_id}/artifacts/active")
def set_active_project_artifact_endpoint(
    project_id: str,
    req: ArtifactActivateRequest,
    current_user: User = Depends(_get_current_user),
    db: Session = Depends(get_db),
):
    project = _require_project_access(db, current_user, project_id)
    spec_field_provided = "spec_artifact_id" in req.model_fields_set
    rtl_field_provided = "rtl_artifact_id" in req.model_fields_set

    if not spec_field_provided and not rtl_field_provided:
        raise HTTPException(
            status_code=400,
            detail="Provide at least one artifact id to update active pointers",
        )

    pointer = _ensure_project_artifact_pointer(db, project.id)

    if spec_field_provided:
        if req.spec_artifact_id is None:
            pointer.active_spec_artifact_id = None
        else:
            spec_artifact = (
                db.query(ProjectArtifact)
                .filter(
                    ProjectArtifact.id == req.spec_artifact_id,
                    ProjectArtifact.project_id == project.id,
                    ProjectArtifact.artifact_type == "spec",
                )
                .first()
            )
            if not spec_artifact:
                raise HTTPException(status_code=404, detail="Spec artifact not found")
            pointer.active_spec_artifact_id = spec_artifact.id

    if rtl_field_provided:
        if req.rtl_artifact_id is None:
            pointer.active_rtl_artifact_id = None
        else:
            rtl_artifact = (
                db.query(ProjectArtifact)
                .filter(
                    ProjectArtifact.id == req.rtl_artifact_id,
                    ProjectArtifact.project_id == project.id,
                    ProjectArtifact.artifact_type == "rtl",
                )
                .first()
            )
            if not rtl_artifact:
                raise HTTPException(status_code=404, detail="RTL artifact not found")
            pointer.active_rtl_artifact_id = rtl_artifact.id

    db.add(pointer)
    db.commit()
    db.refresh(pointer)

    return {
        "project_id": project.id,
        "active": _serialize_artifact_pointer(pointer),
    }


@router.post("/projects/{project_id}/mental-models/build", status_code=201)
async def build_project_mental_model_endpoint(
    project_id: str,
    req: Optional[MentalModelBuildRequest] = Body(default=None),
    current_user: User = Depends(_get_current_user),
    db: Session = Depends(get_db),
):
    project = _require_project_access(db, current_user, project_id)
    req = req or MentalModelBuildRequest()
    spec_artifact, rtl_artifact = _resolve_mental_model_sources(db, project, req)

    pointer = _ensure_project_artifact_pointer(db, project.id)
    pointer.active_spec_artifact_id = spec_artifact.id
    pointer.active_rtl_artifact_id = rtl_artifact.id
    db.add(pointer)
    db.flush()

    run_id = str(uuid.uuid4())
    run_dir = (
        OUTPUTS_DIR
        / "orgs"
        / project.organization_id
        / "projects"
        / project.id
        / "mental-model-runs"
        / run_id
    )
    run_dir.mkdir(parents=True, exist_ok=True)

    started_at = datetime.now(timezone.utc)
    run = Run(
        id=run_id,
        organization_id=project.organization_id,
        project_id=project.id,
        user_id=current_user.id,
        prompt_text=(
            "Build LLM-enriched source-grounded mental model from "
            f"spec {spec_artifact.filename} and RTL {rtl_artifact.filename}"
        ),
        specification_type="mental_model",
        status="running",
        gpu_used=False,
        logs_path=str(run_dir / "events.json"),
        output_path=str(run_dir),
    )
    db.add(run)
    db.commit()

    try:
        _append_run_event_record(
            db,
            run.id,
            "LLM-enriched mental model build started.",
            phase="mental_model.start",
        )
        db.flush()
        _append_run_event_record(
            db,
            run.id,
            f"Resolved sources: spec={spec_artifact.filename}, rtl={rtl_artifact.filename}.",
            phase="mental_model.sources",
        )
        db.flush()

        latest_model = get_latest_mental_model_revision(db, project.id)
        freshness = evaluate_mental_model_freshness(
            latest_model,
            spec_artifact,
            rtl_artifact,
        )
        latest_content = _load_mental_model_content(latest_model)
        latest_is_llm_enriched = _is_llm_enriched_mental_model(latest_content)

        model_status = "rebuilt_llm_enriched"
        if freshness["fresh"] and latest_model is not None and latest_is_llm_enriched:
            record = latest_model
            summary = latest_model.summary_text or "Mental model is ready."
            model_status = "reused_llm_enriched"
            _append_run_event_record(
                db,
                run.id,
                f"Using existing LLM-enriched mental model revision {record.revision}.",
                phase="mental_model.ready",
            )
            db.flush()
        else:
            if freshness["fresh"] and latest_model is not None and not latest_is_llm_enriched:
                reason = "latest revision is parser-only"
            else:
                reason = freshness.get("reason") or "stale"
            _append_run_event_record(
                db,
                run.id,
                f"Rebuilding LLM-enriched mental model ({reason}).",
                phase="mental_model.rebuild",
                level="warning" if reason != "missing" else "info",
            )
            db.flush()

            record, _content, summary = await create_llm_enriched_mental_model_revision(
                db,
                project=project,
                user=current_user,
                spec_artifact=spec_artifact,
                rtl_artifact=rtl_artifact,
                target_module=req.target_module,
                require_llm=True,
            )
        _append_run_event_record(
            db,
            run.id,
            summary,
            phase="mental_model.generated",
        )
        db.flush()
        _append_run_event_record(
            db,
            run.id,
            f"Mental model revision {record.revision} is available.",
            phase="mental_model.persisted",
        )
        db.flush()

        run.status = "completed"
        run.mental_model_revision_id = record.id
        run.completed_at = datetime.now(timezone.utc)
        run.execution_time = f"{(run.completed_at - started_at).total_seconds():.2f}s"
        run.verification_summary = summary
        db.add(run)
        db.commit()
        db.refresh(record)
        db.refresh(run)
    except RuntimeError as exc:
        db.rollback()
        run = db.query(Run).filter(Run.id == run_id).first() or run
        _append_run_event_record(
            db,
            run.id,
            f"LLM mental model build failed: {exc}",
            phase="mental_model.failed",
            level="error",
        )
        run.status = "failed"
        run.completed_at = datetime.now(timezone.utc)
        run.execution_time = f"{(run.completed_at - started_at).total_seconds():.2f}s"
        run.verification_summary = "LLM mental model build failed"
        db.add(run)
        db.commit()
        raise HTTPException(status_code=503, detail="Mental model build unavailable") from exc
    except Exception as exc:
        db.rollback()
        run = db.query(Run).filter(Run.id == run_id).first() or run
        _append_run_event_record(
            db,
            run.id,
            f"Mental model build failed: {exc}",
            phase="mental_model.failed",
            level="error",
        )
        run.status = "failed"
        run.completed_at = datetime.now(timezone.utc)
        run.execution_time = f"{(run.completed_at - started_at).total_seconds():.2f}s"
        run.verification_summary = "Mental model build failed"
        db.add(run)
        db.commit()
        raise HTTPException(status_code=500, detail="Mental model build failed") from exc

    return {
        "project_id": project.id,
        "model_status": model_status,
        "run": _serialize_run(run),
        "run_id": run.id,
        "mental_model": _serialize_mental_model_revision(
            record, include_content=True
        ),
    }


@router.get("/projects/{project_id}/mental-models")
def list_project_mental_models_endpoint(
    project_id: str,
    current_user: User = Depends(_get_current_user),
    db: Session = Depends(get_db),
):
    project = _require_project_access(db, current_user, project_id)
    models = (
        db.query(MentalModelRevision)
        .filter(MentalModelRevision.project_id == project.id)
        .order_by(MentalModelRevision.revision.desc())
        .all()
    )
    return {
        "project_id": project.id,
        "mental_models": [
            _serialize_mental_model_revision(model, include_content=False)
            for model in models
        ],
    }


@router.get("/projects/{project_id}/mental-models/latest")
def get_latest_project_mental_model_endpoint(
    project_id: str,
    current_user: User = Depends(_get_current_user),
    db: Session = Depends(get_db),
):
    project = _require_project_access(db, current_user, project_id)
    model = (
        db.query(MentalModelRevision)
        .filter(MentalModelRevision.project_id == project.id)
        .order_by(MentalModelRevision.revision.desc())
        .first()
    )
    if not model:
        raise HTTPException(status_code=404, detail="Mental model not found")

    return {
        "project_id": project.id,
        "mental_model": _serialize_mental_model_revision(
            model, include_content=True
        ),
    }


@router.get("/projects/{project_id}/mental-models/status")
def get_project_mental_model_status_endpoint(
    project_id: str,
    current_user: User = Depends(_get_current_user),
    db: Session = Depends(get_db),
):
    project = _require_project_access(db, current_user, project_id)
    pointer = _get_project_artifact_pointer(db, project.id)
    active_spec_id = pointer.active_spec_artifact_id if pointer else None
    active_rtl_id = pointer.active_rtl_artifact_id if pointer else None
    latest_model = get_latest_mental_model_revision(db, project.id)

    active_build = (
        db.query(Run)
        .filter(
            Run.project_id == project.id,
            Run.organization_id == project.organization_id,
            Run.specification_type == "mental_model",
            Run.status.in_(["queued", "running"]),
        )
        .order_by(Run.created_at.desc())
        .first()
    )
    if active_build:
        return {
            "project_id": project.id,
            "state": "building",
            "fresh": False,
            "reason": "build_running",
            "message": "Mental model build is currently running.",
            "can_build": False,
            "run_id": active_build.id,
            "mental_model_revision_id": latest_model.id if latest_model else None,
            "revision": latest_model.revision if latest_model else None,
            "summary": latest_model.summary_text if latest_model else "",
            "active_spec_id": active_spec_id,
            "active_rtl_id": active_rtl_id,
        }

    if not active_spec_id or not active_rtl_id:
        missing = []
        if not active_spec_id:
            missing.append("spec")
        if not active_rtl_id:
            missing.append("RTL")
        return {
            "project_id": project.id,
            "state": "missing",
            "fresh": False,
            "reason": "missing_sources",
            "message": f"Select an active {' and '.join(missing)} artifact before building a mental model.",
            "can_build": False,
            "mental_model_revision_id": latest_model.id if latest_model else None,
            "revision": latest_model.revision if latest_model else None,
            "summary": latest_model.summary_text if latest_model else "",
            "active_spec_id": active_spec_id,
            "active_rtl_id": active_rtl_id,
        }

    spec_artifact = _require_project_artifact(
        db, project, active_spec_id, artifact_type="spec"
    )
    rtl_artifact = _require_project_artifact(
        db, project, active_rtl_id, artifact_type="rtl"
    )

    missing_files = [
        artifact.filename
        for artifact in (spec_artifact, rtl_artifact)
        if not Path(artifact.file_path).exists()
    ]
    if missing_files:
        return {
            "project_id": project.id,
            "state": "error",
            "fresh": False,
            "reason": "missing_files",
            "message": f"Artifact file is missing: {', '.join(missing_files)}",
            "can_build": False,
            "mental_model_revision_id": latest_model.id if latest_model else None,
            "revision": latest_model.revision if latest_model else None,
            "summary": latest_model.summary_text if latest_model else "",
            "active_spec_id": active_spec_id,
            "active_rtl_id": active_rtl_id,
            "active_spec_filename": spec_artifact.filename,
            "active_rtl_filename": rtl_artifact.filename,
        }

    freshness = evaluate_mental_model_freshness(
        latest_model,
        spec_artifact,
        rtl_artifact,
    )
    is_fresh = bool(freshness.get("fresh"))
    reason = str(freshness.get("reason") or "")
    state = "fresh" if is_fresh else ("missing" if reason == "missing" else "stale")

    return {
        "project_id": project.id,
        "state": state,
        "fresh": is_fresh,
        "reason": reason,
        "message": freshness.get("message") or (
            "Mental model is ready." if is_fresh else "Mental model needs refresh."
        ),
        "can_build": True,
        "mental_model_revision_id": latest_model.id if latest_model else None,
        "revision": latest_model.revision if latest_model else None,
        "schema_version": latest_model.schema_version if latest_model else None,
        "status": latest_model.status if latest_model else None,
        "summary": latest_model.summary_text if latest_model else "",
        "active_spec_id": active_spec_id,
        "active_rtl_id": active_rtl_id,
        "active_spec_filename": spec_artifact.filename,
        "active_rtl_filename": rtl_artifact.filename,
        "freshness": freshness,
    }


@router.get("/projects/{project_id}/mental-models/{mental_model_id}")
def get_project_mental_model_endpoint(
    project_id: str,
    mental_model_id: str,
    current_user: User = Depends(_get_current_user),
    db: Session = Depends(get_db),
):
    project = _require_project_access(db, current_user, project_id)
    model = (
        db.query(MentalModelRevision)
        .filter(
            MentalModelRevision.id == mental_model_id,
            MentalModelRevision.project_id == project.id,
        )
        .first()
    )
    if not model:
        raise HTTPException(status_code=404, detail="Mental model not found")

    return {
        "project_id": project.id,
        "mental_model": _serialize_mental_model_revision(
            model, include_content=True
        ),
    }


@router.get("/projects/{project_id}/vplan")
def get_project_vplan_endpoint(
    project_id: str,
    mental_model_id: Optional[str] = Query(None),
    current_user: User = Depends(_get_current_user),
    db: Session = Depends(get_db),
):
    project = _require_project_access(db, current_user, project_id)
    query = db.query(MentalModelRevision).filter(
        MentalModelRevision.project_id == project.id
    )
    if mental_model_id:
        query = query.filter(MentalModelRevision.id == mental_model_id)
    model = query.order_by(MentalModelRevision.revision.desc()).first()
    if not model:
        raise HTTPException(status_code=404, detail="Mental model not found")

    content = _load_mental_model_content(model)
    vplan = content.get("vplan") or build_vplan_from_mental_model(content)
    return {
        "project_id": project.id,
        "mental_model_id": model.id,
        "mental_model_revision": model.revision,
        "vplan": vplan,
    }


@router.get("/projects/{project_id}/chipstack-readiness")
def get_project_chipstack_readiness_endpoint(
    project_id: str,
    current_user: User = Depends(_get_current_user),
    db: Session = Depends(get_db),
):
    project = _require_project_access(db, current_user, project_id)
    pointer = _get_project_artifact_pointer(db, project.id)
    active_spec_artifact = None
    active_rtl_artifact = None
    if pointer and pointer.active_spec_artifact_id:
        active_spec_artifact = (
            db.query(ProjectArtifact)
            .filter(ProjectArtifact.id == pointer.active_spec_artifact_id)
            .first()
        )
    if pointer and pointer.active_rtl_artifact_id:
        active_rtl_artifact = (
            db.query(ProjectArtifact)
            .filter(ProjectArtifact.id == pointer.active_rtl_artifact_id)
            .first()
        )
    latest_model = (
        db.query(MentalModelRevision)
        .filter(MentalModelRevision.project_id == project.id)
        .order_by(MentalModelRevision.revision.desc())
        .first()
    )
    model_freshness = None
    if latest_model and active_spec_artifact and active_rtl_artifact:
        model_freshness = evaluate_mental_model_freshness(
            latest_model,
            active_spec_artifact,
            active_rtl_artifact,
        )
    mental_model_status = (
        "pass"
        if model_freshness and model_freshness.get("fresh")
        else ("stale" if latest_model else "missing")
    )
    toolchain = detect_toolchain_status()
    available_tools = {
        name: bool(details.get("available")) for name, details in toolchain.items()
    }
    latest_model_content = _load_mental_model_content(latest_model)
    open_patch_count = (
        db.query(PatchProposal)
        .filter(
            PatchProposal.project_id == project.id,
            PatchProposal.status == "awaiting_approval",
        )
        .count()
    )
    latest_run = (
        db.query(Run)
        .filter(Run.project_id == project.id)
        .order_by(Run.created_at.desc())
        .first()
    )
    latest_run_summary = {}
    if latest_run and latest_run.verification_summary:
        try:
            parsed_summary = json.loads(latest_run.verification_summary)
            if isinstance(parsed_summary, dict):
                latest_run_summary = parsed_summary
        except Exception:
            latest_run_summary = {"raw": latest_run.verification_summary}
    industry_readiness = build_industry_readiness(
        mental_model=latest_model_content if latest_model else None,
        model_freshness=model_freshness,
        toolchain=toolchain,
        active_spec_present=bool(pointer and pointer.active_spec_artifact_id),
        active_rtl_present=bool(pointer and pointer.active_rtl_artifact_id),
        open_patch_count=open_patch_count,
        latest_run_status=latest_run.status if latest_run else None,
        latest_run_summary=latest_run_summary,
    )

    checks = [
        {
            "id": "active_spec",
            "label": "Active spec artifact",
            "status": "pass" if pointer and pointer.active_spec_artifact_id else "missing",
        },
        {
            "id": "active_rtl",
            "label": "Active RTL artifact",
            "status": "pass" if pointer and pointer.active_rtl_artifact_id else "missing",
        },
        {
            "id": "mental_model",
            "label": "Source-grounded mental model",
            "status": mental_model_status,
            "detail": (model_freshness or {}).get("message"),
        },
        {
            "id": "lint_tool",
            "label": "Lint/static analysis tool",
            "status": "pass"
            if available_tools.get("verible") or available_tools.get("verilator")
            else "missing",
        },
        {
            "id": "synthesis_tool",
            "label": "Synthesis/structural tool",
            "status": "pass" if available_tools.get("yosys") else "missing",
        },
    ]
    passed = sum(1 for check in checks if check["status"] == "pass")

    return {
        "project_id": project.id,
        "score": round(passed / len(checks), 2),
        "checks": checks,
        "industry_readiness": industry_readiness,
        "vplan": latest_model_content.get("vplan")
        if latest_model_content
        else None,
        "latest_mental_model": (
            {
                **_serialize_mental_model_revision(
                    latest_model, include_content=False
                ),
                "freshness": model_freshness,
            }
            if latest_model
            else None
        ),
        "agents": list(AGENT_BLUEPRINTS),
        "toolchain": toolchain,
        "recommended_next_step": (
            "Build a mental model from active spec and RTL artifacts."
            if not latest_model
            else "Rebuild the mental model because active sources changed."
            if mental_model_status == "stale"
            else "Use the mental model to run unit testing, formal, UVM, debug, and coverage agents."
        ),
    }


@router.post("/projects/{project_id}/patch-proposals", status_code=201)
def create_patch_proposal_endpoint(
    project_id: str,
    req: PatchProposalCreateRequest,
    current_user: User = Depends(_get_current_user),
    db: Session = Depends(get_db),
):
    project = _require_project_access(db, current_user, project_id)
    target_artifact_id = None
    if req.target_artifact_id:
        target_artifact = _require_project_artifact(db, project, req.target_artifact_id)
        target_artifact_id = target_artifact.id

    proposal = PatchProposal(
        id=str(uuid.uuid4()),
        organization_id=project.organization_id,
        project_id=project.id,
        user_id=current_user.id,
        target_artifact_id=target_artifact_id,
        source_agent=req.source_agent.strip(),
        title=req.title.strip(),
        reason=req.reason.strip(),
        diff_text=req.diff_text,
        status="awaiting_approval",
        metadata_json=json.dumps(req.metadata) if req.metadata else None,
    )
    db.add(proposal)
    db.commit()
    db.refresh(proposal)

    return {
        "project_id": project.id,
        "patch_proposal": _serialize_patch_proposal(proposal),
    }


@router.get("/projects/{project_id}/patch-proposals")
def list_patch_proposals_endpoint(
    project_id: str,
    status_filter: Optional[str] = Query(default=None, alias="status"),
    current_user: User = Depends(_get_current_user),
    db: Session = Depends(get_db),
):
    project = _require_project_access(db, current_user, project_id)
    query = db.query(PatchProposal).filter(PatchProposal.project_id == project.id)
    if status_filter:
        query = query.filter(PatchProposal.status == status_filter)
    proposals = query.order_by(PatchProposal.created_at.desc()).all()
    return {
        "project_id": project.id,
        "patch_proposals": [
            _serialize_patch_proposal(proposal) for proposal in proposals
        ],
    }


@router.patch("/projects/{project_id}/patch-proposals/{proposal_id}")
def decide_patch_proposal_endpoint(
    project_id: str,
    proposal_id: str,
    req: PatchProposalDecisionRequest,
    current_user: User = Depends(_get_current_user),
    db: Session = Depends(get_db),
):
    project = _require_project_access(db, current_user, project_id)
    proposal = (
        db.query(PatchProposal)
        .filter(PatchProposal.id == proposal_id, PatchProposal.project_id == project.id)
        .first()
    )
    if not proposal:
        raise HTTPException(status_code=404, detail="Patch proposal not found")

    metadata = {}
    if proposal.metadata_json:
        try:
            metadata = json.loads(proposal.metadata_json)
        except Exception:
            metadata = {}
    metadata["decision"] = {
        "status": req.status,
        "note": req.note,
        "decided_by_user_id": current_user.id,
        "decided_at": datetime.now(timezone.utc).isoformat(),
    }
    proposal.status = req.status
    proposal.metadata_json = json.dumps(metadata)
    db.add(proposal)
    db.commit()
    db.refresh(proposal)

    return {
        "project_id": project.id,
        "patch_proposal": _serialize_patch_proposal(proposal),
    }


@router.get("/projects/{project_id}/chat/threads")
def list_project_chat_threads_endpoint(
    project_id: str,
    include_archived: bool = Query(default=False),
    exclude_ide: bool = Query(default=True),
    current_user: User = Depends(_get_current_user),
    db: Session = Depends(get_db),
    _license: dict = Depends(_check_license_validity),
):
    project = _require_project_access(db, current_user, project_id)
    query = db.query(ChatThread).filter(
        ChatThread.project_id == project.id,
        ChatThread.user_id == current_user.id,
    )
    if exclude_ide:
        query = query.filter(
            (ChatThread.thread_kind.is_(None))
            | (ChatThread.thread_kind == "main")
            | (ChatThread.thread_kind == "")
        )
    threads = query.order_by(
        ChatThread.updated_at.desc(), ChatThread.created_at.desc()
    ).all()

    states_by_thread_id = _get_chat_thread_states(db, [thread.id for thread in threads])
    if not include_archived:
        threads = [
            thread
            for thread in threads
            if not states_by_thread_id.get(thread.id)
            or states_by_thread_id[thread.id].archived_at is None
        ]

    return [
        _enrich_chat_thread_payload(
            db,
            _serialize_chat_thread(thread, states_by_thread_id.get(thread.id)),
        )
        for thread in threads
    ]


@router.post("/projects/{project_id}/chat/threads", status_code=201)
def create_project_chat_thread_endpoint(
    project_id: str,
    req: ChatThreadCreateRequest,
    current_user: User = Depends(_get_current_user),
    db: Session = Depends(get_db),
    _license: dict = Depends(_check_license_validity),
):
    project = _require_project_access(db, current_user, project_id)
    title = (req.title or "Project Chat").strip() or "Project Chat"
    kind = (req.thread_kind or "main").strip().lower() or "main"
    parent_id = (req.parent_thread_id or "").strip() or None
    if parent_id:
        parent_thread = _require_thread_access(db, current_user, parent_id)
        if parent_thread.project_id != project.id:
            raise HTTPException(status_code=400, detail="Parent thread belongs to another project")

    thread = ChatThread(
        id=str(uuid.uuid4()),
        project_id=project.id,
        user_id=current_user.id,
        title=title,
        agent_name=(req.agent_name or "").strip() or None,
        thread_kind=kind,
        parent_thread_id=parent_id,
    )
    db.add(thread)
    db.commit()
    db.refresh(thread)

    return _serialize_chat_thread(thread)


@router.post(
    "/projects/{project_id}/chat/threads/{main_thread_id}/ide-companion",
    status_code=200,
)
def ensure_ide_companion_thread_endpoint(
    project_id: str,
    main_thread_id: str,
    current_user: User = Depends(_get_current_user),
    db: Session = Depends(get_db),
    _license: dict = Depends(_check_license_validity),
):
    """Return or create the IDE-only chat thread linked to a main conversation."""

    project = _require_project_access(db, current_user, project_id)
    main_thread = _require_thread_access(db, current_user, main_thread_id)
    if main_thread.project_id != project.id:
        raise HTTPException(status_code=404, detail="Thread not found in this project")

    existing = (
        db.query(ChatThread)
        .filter(
            ChatThread.project_id == project.id,
            ChatThread.user_id == current_user.id,
            ChatThread.thread_kind == "ide",
            ChatThread.parent_thread_id == main_thread.id,
        )
        .order_by(ChatThread.updated_at.desc())
        .first()
    )
    if existing:
        state = _get_chat_thread_state(db, existing.id)
        return {
            "thread": _serialize_chat_thread(existing, state),
            "main_thread_id": main_thread.id,
            "created": False,
        }

    main_title = (main_thread.title or "Project Chat").strip()
    companion = ChatThread(
        id=str(uuid.uuid4()),
        project_id=project.id,
        user_id=current_user.id,
        title=f"IDE - {main_title}"[:120],
        thread_kind="ide",
        parent_thread_id=main_thread.id,
    )
    db.add(companion)
    db.commit()
    db.refresh(companion)

    return {
        "thread": _serialize_chat_thread(companion),
        "main_thread_id": main_thread.id,
        "created": True,
    }


@router.patch("/chat/threads/{thread_id}")
def update_chat_thread_endpoint(
    thread_id: str,
    req: ChatThreadUpdateRequest,
    current_user: User = Depends(_get_current_user),
    db: Session = Depends(get_db),
):
    thread = _require_thread_access(db, current_user, thread_id)
    fields_set = req.model_fields_set
    if not fields_set:
        raise HTTPException(status_code=400, detail="No update fields were provided")

    state = _ensure_chat_thread_state(db, thread.id)

    if "title" in fields_set:
        next_title = (req.title or "").strip()
        if not next_title:
            raise HTTPException(status_code=400, detail="Thread title cannot be empty")
        thread.title = next_title

    if "agent_name" in fields_set:
        next_agent = (req.agent_name or "").strip()
        thread.agent_name = next_agent or None

    if "archived" in fields_set:
        state.archived_at = datetime.now(timezone.utc) if req.archived else None

    if "context_run_id" in fields_set:
        if req.context_run_id is None:
            state.context_run_id = None
        else:
            run = _require_run_access(db, current_user, req.context_run_id)
            if run.project_id != thread.project_id:
                raise HTTPException(
                    status_code=400,
                    detail="Run context does not belong to this chat thread project",
                )
            state.context_run_id = run.id
            try:
                from services.project_tasks import link_run_to_thread_task

                link_run_to_thread_task(
                    db,
                    thread_id=thread.id,
                    run=run,
                    user=current_user,
                )
            except Exception:
                logger.warning(
                    "Failed to link run=%s to project task for thread=%s",
                    run.id,
                    thread.id,
                    exc_info=True,
                )

    thread.updated_at = datetime.now(timezone.utc)
    db.add(state)
    db.add(thread)
    db.commit()
    db.refresh(state)
    db.refresh(thread)

    return _serialize_chat_thread(thread, state)


@router.delete("/chat/threads/{thread_id}")
def delete_chat_thread_endpoint(
    thread_id: str,
    current_user: User = Depends(_get_current_user),
    db: Session = Depends(get_db),
):
    thread = _require_thread_access(db, current_user, thread_id)

    db.query(ChatTokenUsage).filter(ChatTokenUsage.thread_id == thread.id).delete(
        synchronize_session=False
    )
    db.query(ChatMessage).filter(ChatMessage.thread_id == thread.id).delete(
        synchronize_session=False
    )
    db.query(ChatThreadState).filter(ChatThreadState.thread_id == thread.id).delete(
        synchronize_session=False
    )
    db.delete(thread)
    db.commit()

    return {"deleted": True, "thread_id": thread.id}


@router.post("/chat/threads/{thread_id}/rewind")
def rewind_chat_thread_endpoint(
    thread_id: str,
    req: ChatThreadRewindRequest,
    current_user: User = Depends(_get_current_user),
    db: Session = Depends(get_db),
):
    """Delete chat messages from a selected user turn onward."""

    thread = _require_thread_access(db, current_user, thread_id)
    pivot = (
        db.query(ChatMessage)
        .filter(ChatMessage.id == req.message_id, ChatMessage.thread_id == thread.id)
        .first()
    )
    if not pivot:
        raise HTTPException(status_code=404, detail="Message not found")
    if pivot.role != "user":
        raise HTTPException(
            status_code=400,
            detail="message_id must reference a user message",
        )

    is_sqlite = bool(db.bind and db.bind.dialect.name == "sqlite")
    sort_key = (
        func.strftime("%Y-%m-%d %H:%M:%f", ChatMessage.created_at)
        if is_sqlite
        else ChatMessage.created_at
    )
    newest_first = (
        db.query(ChatMessage)
        .filter(ChatMessage.thread_id == thread.id)
        .order_by(sort_key.desc(), ChatMessage.id.desc())
        .all()
    )
    ordered = _sort_chat_messages_chronologically(list(reversed(newest_first)))

    pivot_idx = next((idx for idx, message in enumerate(ordered) if message.id == pivot.id), -1)
    if pivot_idx < 0:
        raise HTTPException(status_code=404, detail="Message not found")

    to_delete = ordered[pivot_idx:]
    message_ids = [message.id for message in to_delete]
    if message_ids:
        db.query(ChatTokenUsage).filter(
            ChatTokenUsage.thread_id == thread.id,
            or_(
                ChatTokenUsage.user_message_id.in_(message_ids),
                ChatTokenUsage.assistant_message_id.in_(message_ids),
            ),
        ).delete(synchronize_session=False)
    for message in to_delete:
        db.delete(message)

    thread.updated_at = datetime.now(timezone.utc)
    db.add(thread)
    db.commit()

    artifact_ids = [str(artifact_id) for artifact_id in (req.artifact_ids or []) if artifact_id]
    logger.info(
        "chat_threads.rewind thread_id=%s pivot_message_id=%s messages_deleted=%d artifact_ids=%d",
        thread.id,
        pivot.id,
        len(to_delete),
        len(artifact_ids),
    )

    return {
        "ok": True,
        "thread_id": thread.id,
        "pivot_message_id": pivot.id,
        "messages_deleted": len(to_delete),
        "artifact_revert": {
            "status": "deferred",
            "requested_artifact_ids": artifact_ids,
        },
    }


@router.get("/chat/threads/{thread_id}/messages")
def list_chat_thread_messages_endpoint(
    thread_id: str,
    limit: int = Query(
        default=DEFAULT_CHAT_MESSAGE_PAGE_SIZE, ge=1, le=MAX_CHAT_MESSAGE_PAGE_SIZE
    ),
    before_message_id: Optional[str] = Query(default=None, max_length=64),
    current_user: User = Depends(_get_current_user),
    db: Session = Depends(get_db),
):
    thread = _require_thread_access(db, current_user, thread_id)
    state = _get_chat_thread_state(db, thread.id)

    messages_query = db.query(ChatMessage).filter(ChatMessage.thread_id == thread.id)

    is_sqlite = bool(db.bind and db.bind.dialect.name == "sqlite")

    if is_sqlite:
        sort_key = func.strftime("%Y-%m-%d %H:%M:%f", ChatMessage.created_at)
    else:
        sort_key = ChatMessage.created_at
    if before_message_id:
        cursor_message = (
            db.query(ChatMessage)
            .filter(
                ChatMessage.id == before_message_id,
                ChatMessage.thread_id == thread.id,
            )
            .first()
        )
        if not cursor_message:
            raise HTTPException(status_code=404, detail="Message cursor was not found")

        if is_sqlite:
            cursor_sort_key = (
                db.query(func.strftime("%Y-%m-%d %H:%M:%f", ChatMessage.created_at))
                .filter(
                    ChatMessage.id == cursor_message.id,
                    ChatMessage.thread_id == thread.id,
                )
                .scalar()
            )

            messages_query = messages_query.filter(
                or_(
                    sort_key < cursor_sort_key,
                    and_(
                        sort_key == cursor_sort_key,
                        ChatMessage.id < cursor_message.id,
                    ),
                )
            )
        else:
            messages_query = messages_query.filter(
                or_(
                    sort_key < cursor_message.created_at,
                    and_(
                        sort_key == cursor_message.created_at,
                        ChatMessage.id < cursor_message.id,
                    ),
                )
            )

    newest_first = (
        messages_query.order_by(sort_key.desc(), ChatMessage.id.desc())
        .limit(limit + 1)
        .all()
    )

    has_more = len(newest_first) > limit
    if has_more:
        newest_first = newest_first[:limit]

    next_cursor = newest_first[-1].id if has_more and newest_first else None
    messages = _sort_chat_messages_chronologically(list(reversed(newest_first)))
    message_ids = [message.id for message in messages]
    usage_rows = (
        db.query(ChatTokenUsage)
        .filter(
            ChatTokenUsage.thread_id == thread.id,
            or_(
                ChatTokenUsage.user_message_id.in_(message_ids),
                ChatTokenUsage.assistant_message_id.in_(message_ids),
            ),
        )
        .all()
        if message_ids
        else []
    )
    usage_by_message_id: dict[str, ChatTokenUsage] = {}
    for row in usage_rows:
        if row.assistant_message_id:
            usage_by_message_id[row.assistant_message_id] = row
        if row.user_message_id and row.user_message_id not in usage_by_message_id:
            usage_by_message_id[row.user_message_id] = row

    return {
        "thread": _serialize_chat_thread(thread, state),
        "messages": [
            _serialize_chat_message(message, usage_by_message_id)
            for message in messages
        ],
        "pagination": {
            "limit": limit,
            "has_more": has_more,
            "next_cursor": next_cursor,
        },
    }


@router.get("/chat/threads/{thread_id}/token-usage")
def get_chat_thread_token_usage_endpoint(
    thread_id: str,
    current_user: User = Depends(_get_current_user),
    db: Session = Depends(get_db),
):
    thread = _require_thread_access(db, current_user, thread_id)
    rows = get_thread_token_usage(db, thread.id)
    return {
        "thread_id": thread.id,
        "project_id": thread.project_id,
        "summary": summarize_token_usage(rows),
        "entries": [serialize_token_usage(row) for row in rows],
    }


@router.get("/projects/{project_id}/token-usage")
def get_project_token_usage_endpoint(
    project_id: str,
    current_user: User = Depends(_get_current_user),
    db: Session = Depends(get_db),
):
    project = _require_project_access(db, current_user, project_id)
    rows = get_project_token_usage(db, project.id)
    return {
        "project_id": project.id,
        "summary": summarize_token_usage(rows),
        "entries": [serialize_token_usage(row) for row in rows],
    }


@router.post("/chat/threads/{thread_id}/ask")
async def ask_chat_thread_endpoint(
    thread_id: str,
    req: ChatAskRequest,
    current_user: User = Depends(_get_current_user),
    db: Session = Depends(get_db),
):
    thread = _require_thread_access(db, current_user, thread_id)
    user_prompt = req.prompt.strip()
    state = _get_chat_thread_state(db, thread.id)

    if not user_prompt:
        raise HTTPException(status_code=400, detail="Prompt is required")

    if state and state.archived_at is not None:
        raise HTTPException(
            status_code=409,
            detail="Chat thread is archived. Unarchive it before sending new messages.",
        )

    run = None
    context_field_provided = "context_run_id" in req.model_fields_set
    requested_context_run_id = req.context_run_id

    if context_field_provided:
        state = _ensure_chat_thread_state(db, thread.id)
        state.context_run_id = requested_context_run_id
        db.add(state)
        db.flush()

    effective_context_run_id = (
        requested_context_run_id
        if context_field_provided
        else (state.context_run_id if state else None)
    )

    if effective_context_run_id:
        run = _require_run_access(db, current_user, effective_context_run_id)
        if run.project_id != thread.project_id:
            raise HTTPException(
                status_code=400,
                detail="Run context does not belong to this chat thread project",
            )
        if run.status not in ["completed", "failed", "blocked"]:
            raise HTTPException(
                status_code=400,
                detail="Run context must be completed, failed, or blocked before chat analysis",
            )

    is_sqlite = bool(db.bind and db.bind.dialect.name == "sqlite")
    if is_sqlite:
        sort_key = func.strftime("%Y-%m-%d %H:%M:%f", ChatMessage.created_at)
    else:
        sort_key = ChatMessage.created_at

    if req.message_history and len(req.message_history) > 0:
        message_history = req.message_history
    else:
        recent_messages = (
            db.query(ChatMessage)
            .filter(ChatMessage.thread_id == thread.id)
            .order_by(sort_key.asc(), ChatMessage.id.asc())
            .limit(50)
            .all()
        )

        message_history = []
        for msg in recent_messages:
            if msg.role == "user":
                message_history.append({"role": "user", "content": msg.content})
            elif msg.role == "assistant":
                message_history.append({"role": "assistant", "content": msg.content})

    if _is_mental_model_chat_context(req.context):
        revision_id = str(
            (req.context or {}).get("mental_model_revision_id") or ""
        ).strip()
        model_query = db.query(MentalModelRevision).filter(
            MentalModelRevision.project_id == thread.project_id
        )
        if revision_id:
            model_query = model_query.filter(MentalModelRevision.id == revision_id)
        model = model_query.order_by(MentalModelRevision.revision.desc()).first()
        assistant_response = await _generate_mental_model_assistant_response_with_history(
            user_prompt,
            message_history,
            model,
        )
    else:
        assistant_response = await _generate_debug_assistant_response_with_history(
            user_prompt, message_history, run
        )

    user_at, assistant_at = chat_turn_timestamps()
    user_message = ChatMessage(
        id=str(uuid.uuid4()),
        thread_id=thread.id,
        role="user",
        content=user_prompt,
        created_at=user_at,
    )
    assistant_message = ChatMessage(
        id=str(uuid.uuid4()),
        thread_id=thread.id,
        role="assistant",
        content=assistant_response,
        created_at=assistant_at,
    )

    thread.updated_at = datetime.now(timezone.utc)
    db.add(user_message)
    db.add(assistant_message)
    db.add(thread)
    db.commit()
    usage_row = None
    try:
        usage_row = persist_chat_token_usage(
            db,
            thread_id=thread.id,
            user_id=current_user.id,
            usage=estimate_text_token_usage(
                input_text="\n".join(
                    str(item.get("content") or "") for item in message_history
                )
                + "\n"
                + user_prompt,
                output_text=assistant_response,
                provider=PROVIDER,
                model=DEFAULT_MODEL,
                details={"source": "legacy_chat_estimate"},
            ),
            user_message_id=user_message.id,
            assistant_message_id=assistant_message.id,
            source="chat_ask",
        )
        db.commit()
    except Exception:
        db.rollback()
        logger.warning("Failed to persist chat ask token usage", exc_info=True)

    if state:
        db.refresh(state)
    db.refresh(thread)
    db.refresh(user_message)
    db.refresh(assistant_message)
    if usage_row:
        db.refresh(usage_row)
    usage_by_message_id = (
        {assistant_message.id: usage_row, user_message.id: usage_row}
        if usage_row
        else {}
    )

    return {
        "thread": _serialize_chat_thread(thread, state),
        "user_message": _serialize_chat_message(user_message, usage_by_message_id),
        "assistant_message": _serialize_chat_message(
            assistant_message, usage_by_message_id
        ),
    }


@router.websocket("/ws/chat/threads/{thread_id}/ask")
async def ask_chat_thread_ws_endpoint(websocket: WebSocket, thread_id: str):
    await websocket.accept()

    token = (websocket.query_params.get("token") or "").strip()

    db = SessionLocal()
    try:
        if token:
            try:
                current_user = _authenticate_user_from_token(token, db)
            except HTTPException:
                current_user = _get_or_create_local_dev_user(db)
        else:
            current_user = _get_or_create_local_dev_user(db)
        thread = _require_thread_access(db, current_user, thread_id)
        state = _get_chat_thread_state(db, thread.id)

        incoming = await websocket.receive_json()
        if not isinstance(incoming, dict):
            raise HTTPException(status_code=400, detail="Invalid websocket payload")

        user_prompt = str(incoming.get("prompt") or "").strip()
        if not user_prompt:
            raise HTTPException(status_code=400, detail="Prompt is required")

        if state and state.archived_at is not None:
            raise HTTPException(
                status_code=409,
                detail="Chat thread is archived. Unarchive it before sending new messages.",
            )

        context_field_provided = "context_run_id" in incoming
        requested_context_run_id = incoming.get("context_run_id")
        if isinstance(requested_context_run_id, str):
            requested_context_run_id = requested_context_run_id.strip() or None
        elif requested_context_run_id is not None:
            raise HTTPException(
                status_code=400, detail="context_run_id must be a string"
            )

        run = None
        if context_field_provided:
            state = _ensure_chat_thread_state(db, thread.id)
            state.context_run_id = requested_context_run_id
            db.add(state)
            db.flush()

        effective_context_run_id = (
            requested_context_run_id
            if context_field_provided
            else (state.context_run_id if state else None)
        )

        if effective_context_run_id:
            run = _require_run_access(db, current_user, effective_context_run_id)
            if run.project_id != thread.project_id:
                raise HTTPException(
                    status_code=400,
                    detail="Run context does not belong to this chat thread project",
                )
            if run.status not in ["completed", "failed", "blocked"]:
                raise HTTPException(
                    status_code=400,
                    detail="Run context must be completed, failed, or blocked before chat analysis",
                )

        user_at, assistant_at = chat_turn_timestamps()
        user_message = ChatMessage(
            id=str(uuid.uuid4()),
            thread_id=thread.id,
            role="user",
            content=user_prompt,
            created_at=user_at,
        )
        thread.updated_at = datetime.now(timezone.utc)
        db.add(user_message)
        db.add(thread)
        db.commit()

        if state:
            db.refresh(state)
        db.refresh(thread)
        db.refresh(user_message)

        await websocket.send_json(
            {
                "type": "user_message",
                "thread": _serialize_chat_thread(thread, state),
                "user_message": _serialize_chat_message(user_message),
            }
        )

        assistant_response = await _generate_debug_assistant_response(user_prompt, run)

        assistant_message = ChatMessage(
            id=str(uuid.uuid4()),
            thread_id=thread.id,
            role="assistant",
            content=assistant_response,
            created_at=assistant_at,
        )
        thread.updated_at = datetime.now(timezone.utc)
        db.add(assistant_message)
        db.add(thread)
        db.commit()

        if state:
            db.refresh(state)
        db.refresh(thread)
        db.refresh(assistant_message)

        for chunk in _iter_text_chunks(assistant_response):
            await websocket.send_json({"type": "assistant_delta", "delta": chunk})
            await asyncio.sleep(0)

        await websocket.send_json(
            {
                "type": "assistant_message",
                "thread": _serialize_chat_thread(thread, state),
                "assistant_message": _serialize_chat_message(assistant_message),
            }
        )
        await websocket.send_json({"type": "done"})
        await websocket.close(code=1000)
    except HTTPException as exc:
        close_code = 4401
        if exc.status_code == 404:
            close_code = 4404
        elif exc.status_code in {400, 409}:
            close_code = 4400

        await _send_ws_error_and_close(websocket, str(exc.detail), close_code)
    except WebSocketDisconnect:
        return
    except Exception:
        await _send_ws_error_and_close(
            websocket,
            "Chat websocket stream failed",
            close_code=4400,
        )
    finally:
        db.close()


@router.get("/runs")
def list_runs_endpoint(
    current_user: User = Depends(_get_current_user),
    db: Session = Depends(get_db),
    _license: dict = Depends(_check_license_validity),
):
    organization = _ensure_default_organization(db, current_user)
    runs = (
        db.query(Run)
        .filter(Run.organization_id == organization.id)
        .order_by(Run.created_at.desc())
        .all()
    )
    return [_serialize_run(run) for run in runs]


@router.get("/projects/{project_id}/runs")
def list_project_runs_endpoint(
    project_id: str,
    current_user: User = Depends(_get_current_user),
    db: Session = Depends(get_db),
):
    project = _require_project_access(db, current_user, project_id)
    runs = (
        db.query(Run)
        .filter(Run.project_id == project.id)
        .order_by(Run.created_at.desc())
        .all()
    )
    return [_serialize_run(run) for run in runs]


@router.get("/projects/{project_id}/agents")
def list_project_agents_endpoint(
    project_id: str,
    current_user: User = Depends(_get_current_user),
    db: Session = Depends(get_db),
):
    project = _require_project_access(db, current_user, project_id)

    latest_run = (
        db.query(Run)
        .filter(Run.project_id == project.id)
        .order_by(Run.created_at.desc())
        .first()
    )
    latest_mental_model = (
        db.query(MentalModelRevision)
        .filter(MentalModelRevision.project_id == project.id)
        .order_by(MentalModelRevision.revision.desc())
        .first()
    )
    recent_events: list[RunEvent] = []
    latest_event = None
    if latest_run:
        recent_events = (
            db.query(RunEvent)
            .filter(RunEvent.run_id == latest_run.id)
            .order_by(RunEvent.seq_no.desc())
            .limit(40)
            .all()
        )
        latest_event = recent_events[0] if recent_events else None

    run_status = latest_run.status if latest_run else "idle"
    run_id = latest_run.id if latest_run else None
    last_updated_at = (
        latest_event.created_at
        if latest_event
        else (
            latest_run.completed_at
            if latest_run and latest_run.completed_at
            else (latest_run.created_at if latest_run else None)
        )
    )

    agents = []
    for blueprint in AGENT_BLUEPRINTS:
        status_text = _resolve_agent_task(
            blueprint["id"],
            run_status,
            latest_event=latest_event,
            recent_events=recent_events,
        )
        if blueprint["id"] == "mental_model":
            status_text = (
                f"Mental model v{latest_mental_model.revision} ready"
                if latest_mental_model
                else "Build a source-grounded mental model from active spec and RTL"
            )
        agents.append(
            {
                **blueprint,
                "status": status_text,
                "run_id": run_id,
                "run_status": run_status,
                "mental_model_id": latest_mental_model.id
                if latest_mental_model
                else None,
                "mental_model_revision": latest_mental_model.revision
                if latest_mental_model
                else None,
                "last_event_phase": latest_event.phase if latest_event else None,
                "last_event_message": latest_event.message if latest_event else None,
                "last_updated_at": _iso(last_updated_at),
            }
        )

    return {
        "project_id": project.id,
        "run_id": run_id,
        "run_status": run_status,
        "events_count": len(recent_events),
        "generated_at": _iso(datetime.now(timezone.utc)),
        "platform_profile": PLATFORM_PROFILE,
        "agents": agents,
    }


@router.get("/metrics")
def get_global_metrics_endpoint(
    current_user: User = Depends(_get_current_user),
    db: Session = Depends(get_db),
):
    organization = _ensure_default_organization(db, current_user)

    # Total projects
    total_projects = (
        db.query(Project)
        .filter(Project.organization_id == organization.id, Project.status == "active")
        .count()
    )

    # Total runs
    total_runs = db.query(Run).filter(Run.organization_id == organization.id).count()

    # Pass rate (completed / total finished)
    finished_runs = (
        db.query(Run)
        .filter(
            Run.organization_id == organization.id,
            Run.status.in_(["completed", "failed"]),
        )
        .all()
    )

    passed_runs = [r for r in finished_runs if r.status == "completed"]
    pass_rate = (len(passed_runs) / len(finished_runs) * 100) if finished_runs else 0

    # Total events (telemetry volume)
    total_events = (
        db.query(RunEvent)
        .join(Run)
        .filter(Run.organization_id == organization.id)
        .count()
    )
    token_usage = get_organization_token_totals(db, organization.id)

    return {
        "total_projects": total_projects,
        "total_runs": total_runs,
        "pass_rate": round(pass_rate, 1),
        "total_events": total_events,
        "token_usage": token_usage,
    }


@router.post("/projects/{project_id}/runs", status_code=201)
async def start_run_endpoint(
    project_id: str,
    prompt: Optional[str] = Form(None),
    spec_file: Optional[UploadFile] = File(None),
    rtl_file: Optional[UploadFile] = File(None),
    verification_profile: str = Form("legacy"),
    current_user: User = Depends(_get_current_user),
    db: Session = Depends(get_db),
    _license: dict = Depends(_check_license_validity),
):
    project = _require_project_access(db, current_user, project_id)
    verification_profile = (verification_profile or "legacy").strip().lower()
    if verification_profile not in {"legacy", "block_smoke", "auto"}:
        raise HTTPException(
            status_code=400,
            detail="verification_profile must be legacy, block_smoke, or auto",
        )
    pointer = _get_project_artifact_pointer(db, project.id)
    active_spec_artifact = None
    active_rtl_artifact = None

    if pointer and pointer.active_spec_artifact_id:
        active_spec_artifact = (
            db.query(ProjectArtifact)
            .filter(
                ProjectArtifact.id == pointer.active_spec_artifact_id,
                ProjectArtifact.project_id == project.id,
                ProjectArtifact.artifact_type == "spec",
            )
            .first()
        )

    if pointer and pointer.active_rtl_artifact_id:
        active_rtl_artifact = (
            db.query(ProjectArtifact)
            .filter(
                ProjectArtifact.id == pointer.active_rtl_artifact_id,
                ProjectArtifact.project_id == project.id,
                ProjectArtifact.artifact_type == "rtl",
            )
            .first()
        )

    if not prompt and not spec_file and not active_spec_artifact:
        raise HTTPException(
            status_code=400,
            detail=(
                "Provide specification text/file or upload a spec artifact and set it active"
            ),
        )

    run_id = str(uuid.uuid4())
    run_dir = (
        OUTPUTS_DIR
        / "orgs"
        / project.organization_id
        / "projects"
        / project.id
        / "runs"
        / run_id
    )
    input_dir = run_dir / "inputs"
    input_dir.mkdir(parents=True, exist_ok=True)

    prompt_text_saved = ""
    spec_artifact: ProjectArtifact

    if spec_file:
        spec_name = _safe_filename(spec_file.filename, "spec.txt")
        spec_raw = await spec_file.read()
        spec_artifact, _ = _create_project_artifact_revision(
            db,
            project,
            current_user,
            "spec",
            spec_raw,
            spec_name,
            source="run_upload",
            content_type=spec_file.content_type or _guess_content_type(Path(spec_name)),
            metadata={
                "run_input": True,
                "analysis": _analyze_spec_bytes(spec_raw, spec_name),
            },
            reuse_identical=True,
        )
        spec_path = input_dir / f"spec_{spec_name}"
        spec_path.write_bytes(spec_raw)
        specification_type = "file"
        prompt_text_saved = f"Uploaded specification file: {spec_name}"
    elif prompt:
        spec_raw = prompt.encode("utf-8")
        spec_artifact, _ = _create_project_artifact_revision(
            db,
            project,
            current_user,
            "spec",
            spec_raw,
            "prompt_spec.txt",
            source="run_prompt",
            content_type="text/plain",
            metadata={
                "run_input": True,
                "analysis": _analyze_spec_bytes(spec_raw, "prompt_spec.txt"),
            },
            reuse_identical=True,
        )
        spec_path = input_dir / "spec.txt"
        spec_path.write_text(prompt, encoding="utf-8")
        specification_type = "text"
        prompt_text_saved = prompt.strip()
    else:
        assert active_spec_artifact is not None
        spec_artifact = active_spec_artifact
        active_spec_path = Path(active_spec_artifact.file_path)
        if not active_spec_path.exists():
            raise HTTPException(
                status_code=404, detail="Active spec artifact file is missing"
            )

        active_spec_name = _safe_filename(active_spec_artifact.filename, "spec.txt")
        spec_path = input_dir / f"spec_active_{active_spec_name}"
        shutil.copy2(active_spec_path, spec_path)
        specification_type = "artifact"
        prompt_text_saved = f"Active spec artifact {active_spec_artifact.id} revision {active_spec_artifact.revision}"

    rtl_input_metadata: dict | None = None
    rtl_artifact: ProjectArtifact

    if rtl_file:
        rtl_name = _safe_filename(rtl_file.filename, "rtl.sv")
        rtl_raw = await rtl_file.read()
        rtl_artifact, _ = _create_project_artifact_revision(
            db,
            project,
            current_user,
            "rtl",
            rtl_raw,
            rtl_name,
            source="run_upload",
            content_type=rtl_file.content_type or _guess_content_type(Path(rtl_name)),
            metadata={
                "run_input": True,
                "analysis": _analyze_rtl_bytes(rtl_raw, rtl_name),
            },
            reuse_identical=True,
        )
        stored_rtl_path = input_dir / f"rtl_{rtl_name}"
        stored_rtl_path.write_bytes(rtl_raw)
        rtl_path, rtl_input_metadata = _extract_rtl_archive_for_run(
            stored_rtl_path,
            rtl_name,
            input_dir,
            "rtl_upload",
        )
    elif active_rtl_artifact:
        assert active_rtl_artifact is not None
        rtl_artifact = active_rtl_artifact
        active_rtl_path = Path(active_rtl_artifact.file_path)
        if not active_rtl_path.exists():
            raise HTTPException(
                status_code=404, detail="Active RTL artifact file is missing"
            )

        active_rtl_name = _safe_filename(active_rtl_artifact.filename, "rtl_context.sv")
        stored_rtl_path = input_dir / f"rtl_active_{active_rtl_name}"
        shutil.copy2(active_rtl_path, stored_rtl_path)
        rtl_path, rtl_input_metadata = _extract_rtl_archive_for_run(
            stored_rtl_path,
            active_rtl_name,
            input_dir,
            "rtl_active",
        )
    else:
        rtl_raw = b"// Autogenerated empty RTL from desktop prompt\n"
        rtl_artifact, _ = _create_project_artifact_revision(
            db,
            project,
            current_user,
            "rtl",
            rtl_raw,
            "rtl_context.sv",
            source="run_placeholder",
            content_type="text/plain",
            metadata={
                "run_input": True,
                "placeholder": True,
                "analysis": _analyze_rtl_bytes(rtl_raw, "rtl_context.sv"),
            },
            reuse_identical=True,
        )
        rtl_path = input_dir / "rtl_context.sv"
        rtl_path.write_bytes(rtl_raw)

    if not prompt_text_saved:
        prompt_text_saved = "Uploaded specification file"

    run = Run(
        id=run_id,
        organization_id=project.organization_id,
        project_id=project.id,
        user_id=current_user.id,
        prompt_text=prompt_text_saved,
        specification_type=specification_type,
        status="running",
        gpu_used=True,
        logs_path=str(run_dir / "logs.json"),
        output_path=str(run_dir),
    )
    db.add(run)
    db.commit()

    try:
        from services.project_tasks import create_task_for_run, derive_title_from_message

        create_task_for_run(
            db,
            run=run,
            user=current_user,
            title=derive_title_from_message(prompt_text_saved),
            description=prompt_text_saved,
            agent_name="Verification Agent",
            source="verification",
        )
    except Exception:
        logger.warning(
            "Failed to create project task for run=%s",
            run_id,
            exc_info=True,
        )

    try:
        mental_model = _prepare_fresh_mental_model_revision_for_run(
            db,
            run=run,
            project=project,
            current_user=current_user,
            spec_artifact=spec_artifact,
            rtl_artifact=rtl_artifact,
        )
        db.commit()
        db.refresh(run)
    except Exception as exc:
        db.rollback()
        run = db.query(Run).filter(Run.id == run_id).first() or run
        _append_run_event_record(
            db,
            run.id,
            f"Mental model preparation failed: {exc}",
            phase="mental_model.failed",
            level="error",
        )
        run.status = "failed"
        run.completed_at = datetime.now(timezone.utc)
        run.execution_time = "0s"
        run.verification_summary = "Mental model preparation failed"
        db.add(run)
        db.commit()
        raise HTTPException(
            status_code=500,
            detail="Mental model preparation failed before verification run",
        ) from exc

    mental_model_content = _load_mental_model_content(mental_model)
    selected_profile = "agent"

    # Inject mental model into runner's job state before the job starts
    from services.runner import JOBS, EVENT_LOCK
    with EVENT_LOCK:
        if run_id not in JOBS:
            JOBS[run_id] = {}
        JOBS[run_id]["mental_model"] = mental_model_content
        JOBS[run_id]["mental_model_revision_id"] = mental_model.id

    start_job(run_id, str(rtl_path), str(spec_path), str(run_dir))

    return {
        "run_id": run_id,
        "status": "started",
        "project_id": project.id,
        "verification_profile": selected_profile,
        "mental_model_revision_id": mental_model.id,
        "mental_model_revision": mental_model.revision,
        "rtl_input": rtl_input_metadata
        or {"kind": "file", "path": Path(rtl_path).name},
        "message": "Verification pipeline started in local-first mode",
    }


@router.post("/projects/{project_id}/runs/{run_id}/sync-artifacts")
def sync_run_generated_artifacts_endpoint(
    project_id: str,
    run_id: str,
    current_user: User = Depends(_get_current_user),
    db: Session = Depends(get_db),
):
    project = _require_project_access(db, current_user, project_id)
    run = _require_run_access(db, current_user, run_id)

    if run.project_id != project.id:
        raise HTTPException(status_code=404, detail="Run not found for this project")

    run_status = str(run.status or "").lower()
    if run_status not in RUN_SYNC_FINAL_STATUSES:
        raise HTTPException(
            status_code=409,
            detail="Run artifacts can be synced after run reaches a terminal status",
        )

    summary = _sync_run_output_artifacts_to_project(db, project, current_user, run)
    pointer = _get_project_artifact_pointer(db, project.id)

    return {
        "project_id": project.id,
        "run_id": run.id,
        "run_status": run.status,
        "active": _serialize_artifact_pointer(pointer),
        **summary,
    }


@router.post("/runs/{run_id}/cancel")
def cancel_run_endpoint(
    run_id: str,
    current_user: User = Depends(_get_current_user),
    db: Session = Depends(get_db),
):
    run = _require_run_access(db, current_user, run_id)
    current_status = (run.status or "").lower()

    if current_status in {"completed", "failed", "blocked", "cancelled", "interrupted"}:
        return {
            "run_id": run.id,
            "status": run.status,
            "cancelled": current_status in {"cancelled", "interrupted"},
            "message": "Run is already in a terminal state",
        }

    cancelled_in_runtime = cancel_job(run.id)
    if not cancelled_in_runtime:
        run.status = "cancelled"
        run.completed_at = datetime.now()
        if not run.execution_time:
            run.execution_time = "0s"
        run.verification_summary = "Run cancelled by user"
        _append_run_event_record(
            db,
            run.id,
            "Run cancelled by user.",
            phase="runtime",
            level="warning",
        )
        db.add(run)
        db.commit()
    else:
        db.refresh(run)

    return {
        "run_id": run.id,
        "status": run.status,
        "cancelled": run.status == "cancelled",
        "message": "Cancellation requested",
    }


@router.get("/runs/{run_id}/status")
def run_status_endpoint(
    run_id: str,
    current_user: User = Depends(_get_current_user),
    db: Session = Depends(get_db),
):
    run = _require_run_access(db, current_user, run_id)
    runtime_status = get_job_status(run_id)
    event_logs = _load_run_event_messages(db, run.id)

    if runtime_status.get("status") == "not_found":
        logs = event_logs or _load_logs(run.logs_path)
        return {
            "run_id": run.id,
            "status": run.status,
            "logs": logs,
            "error": None if run.status == "completed" else "Run not active in memory",
            "events_count": len(event_logs),
            "execution_time": run.execution_time,
            "completed_at": _iso(run.completed_at),
            "mental_model_revision_id": run.mental_model_revision_id,
            "verification_summary": run.verification_summary,
        }

    logs = event_logs or runtime_status.get("logs", [])
    return {
        "run_id": run.id,
        "status": runtime_status.get("status", run.status),
        "logs": logs,
        "error": runtime_status.get("error"),
        "events_count": len(event_logs),
        "execution_time": run.execution_time,
        "completed_at": _iso(run.completed_at),
        "mental_model_revision_id": run.mental_model_revision_id,
        "verification_summary": run.verification_summary,
    }


@router.get("/runs/{run_id}/events")
def run_events_endpoint(
    run_id: str,
    after_seq: int = Query(default=0, ge=0),
    limit: int = Query(default=200, ge=1, le=1000),
    current_user: User = Depends(_get_current_user),
    db: Session = Depends(get_db),
):
    run = _require_run_access(db, current_user, run_id)
    events = (
        db.query(RunEvent)
        .filter(RunEvent.run_id == run.id, RunEvent.seq_no > after_seq)
        .order_by(RunEvent.seq_no.asc())
        .limit(limit)
        .all()
    )

    return {
        "run_id": run.id,
        "status": run.status,
        "events": [_serialize_run_event(event) for event in events],
    }


@router.get("/runs/{run_id}/events/stream")
async def run_events_stream_endpoint(
    run_id: str,
    after_seq: int = Query(default=0, ge=0),
    current_user: User = Depends(_get_current_user),
    db: Session = Depends(get_db),
):
    run = _require_run_access(db, current_user, run_id)

    async def _event_streamer():
        last_seq = after_seq
        final_statuses = {"completed", "failed", "blocked", "cancelled", "interrupted"}

        while True:
            poll_db = SessionLocal()
            try:
                events = (
                    poll_db.query(RunEvent)
                    .filter(RunEvent.run_id == run.id, RunEvent.seq_no > last_seq)
                    .order_by(RunEvent.seq_no.asc())
                    .all()
                )
                run_state = poll_db.query(Run).filter(Run.id == run.id).first()
            finally:
                poll_db.close()

            if events:
                for event in events:
                    last_seq = event.seq_no
                    payload = json.dumps(_serialize_run_event(event))
                    yield f"event: run_event\ndata: {payload}\n\n"
            else:
                yield "event: keepalive\ndata: {}\n\n"

            if run_state and run_state.status in final_statuses and not events:
                end_payload = json.dumps(
                    {
                        "run_id": run_state.id,
                        "status": run_state.status,
                        "last_seq": last_seq,
                    }
                )
                yield f"event: end\ndata: {end_payload}\n\n"
                break

            await asyncio.sleep(1.0)

    return StreamingResponse(
        _event_streamer(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.websocket("/ws/runs/{run_id}/events")
async def run_events_ws_endpoint(websocket: WebSocket, run_id: str):
    await websocket.accept()

    token = (websocket.query_params.get("token") or "").strip()
    if not token:
        await _send_ws_error_and_close(
            websocket,
            "Missing authentication token",
            close_code=4401,
        )
        return

    after_seq_raw = (websocket.query_params.get("after_seq") or "0").strip()
    try:
        after_seq = max(0, int(after_seq_raw))
    except ValueError:
        await _send_ws_error_and_close(
            websocket,
            "after_seq must be a non-negative integer",
            close_code=4400,
        )
        return

    auth_db = SessionLocal()
    try:
        current_user = _authenticate_user_from_token(token, auth_db)
        run = _require_run_access(auth_db, current_user, run_id)
    except HTTPException as exc:
        close_code = 4401 if exc.status_code != 404 else 4404
        await _send_ws_error_and_close(websocket, str(exc.detail), close_code)
        return
    finally:
        auth_db.close()

    last_seq = after_seq
    final_statuses = {"completed", "failed", "blocked", "cancelled", "interrupted"}

    try:
        while True:
            poll_db = SessionLocal()
            try:
                events = (
                    poll_db.query(RunEvent)
                    .filter(RunEvent.run_id == run.id, RunEvent.seq_no > last_seq)
                    .order_by(RunEvent.seq_no.asc())
                    .all()
                )
                run_state = poll_db.query(Run).filter(Run.id == run.id).first()
            finally:
                poll_db.close()

            if events:
                for event in events:
                    last_seq = event.seq_no
                    await websocket.send_json(
                        {"type": "run_event", "event": _serialize_run_event(event)}
                    )
            else:
                await websocket.send_json(
                    {
                        "type": "keepalive",
                        "run_id": run.id,
                        "status": run_state.status if run_state else "not_found",
                        "last_seq": last_seq,
                    }
                )

            if run_state and run_state.status in final_statuses and not events:
                await websocket.send_json(
                    {
                        "type": "end",
                        "run_id": run_state.id,
                        "status": run_state.status,
                        "last_seq": last_seq,
                    }
                )
                await websocket.close(code=1000)
                break

            await asyncio.sleep(1.0)
    except WebSocketDisconnect:
        return
    except Exception:
        await _send_ws_error_and_close(
            websocket,
            "Run events websocket stream failed",
            close_code=4400,
        )


@router.delete("/runs/{run_id}")
def delete_run_endpoint(
    run_id: str,
    current_user: User = Depends(_get_current_user),
    db: Session = Depends(get_db),
):
    run = _require_run_access(db, current_user, run_id)

    db.query(RunEvent).filter(RunEvent.run_id == run.id).delete(
        synchronize_session=False
    )

    if run.output_path and os.path.exists(run.output_path):
        shutil.rmtree(run.output_path, ignore_errors=True)

    zip_path = Path(run.output_path).with_suffix(".zip") if run.output_path else None
    if zip_path and zip_path.exists():
        zip_path.unlink()

    db.delete(run)
    db.commit()
    return {"message": "Run deleted"}


@router.get("/runs/{run_id}/download")
def download_run_endpoint(
    run_id: str,
    current_user: User = Depends(_get_current_user),
    db: Session = Depends(get_db),
):
    run = _require_run_access(db, current_user, run_id)
    if not run.output_path:
        raise HTTPException(status_code=404, detail="Run output path missing")

    run_dir = Path(run.output_path)
    if not run_dir.exists():
        raise HTTPException(status_code=404, detail="Run output directory not found")

    zip_path = run_dir.with_suffix(".zip")
    if not zip_path.exists():
        shutil.make_archive(str(run_dir), "zip", str(run_dir))

    return FileResponse(
        str(zip_path),
        filename=f"chipverify_run_{run.id}.zip",
        media_type="application/zip",
    )


@router.post("/runs/{run_id}/debug")
async def debug_run_endpoint(
    run_id: str,
    req: DebugRequest,
    current_user: User = Depends(_get_current_user),
    db: Session = Depends(get_db),
):
    run = _require_run_access(db, current_user, run_id)

    if run.status not in ["completed", "failed", "blocked"]:
        raise HTTPException(
            status_code=400,
            detail="Debug assistant is available after run completion",
        )

    response = await _generate_debug_assistant_response(req.prompt, run)
    return {"response": response}


@router.get("/eda/toolchain/status")
def get_eda_toolchain_status_endpoint(
    refresh: bool = False,
    current_user: User = Depends(_get_current_user),
):
    _ = current_user
    # Detection is cached for the process lifetime; ?refresh=1 re-probes so a tool
    # installed/configured after the backend started is picked up without a restart.
    tools = refresh_toolchain_status() if refresh else detect_toolchain_status()
    return {
        "tools": tools,
    }


@router.post("/eda/netlist/render")
def render_eda_netlist_endpoint(
    req: EdaNetlistRenderRequest,
    current_user: User = Depends(_get_current_user),
    db: Session = Depends(get_db),
):
    _require_project_access(db, current_user, req.project_id)
    document = render_netlist_document(
        project_id=req.project_id,
        filename=req.filename,
        source=req.source,
        top_module=req.top_module,
    )
    return document


@router.post("/eda/simulations")
def create_eda_simulation_endpoint(
    req: EdaSimulationCreateRequest,
    current_user: User = Depends(_get_current_user),
    db: Session = Depends(get_db),
):
    _require_project_access(db, current_user, req.project_id)
    return create_simulation_job(
        project_id=req.project_id,
        rtl_source=req.rtl_source,
        filename=req.filename,
        top_module=req.top_module,
        testbench_source=req.testbench_source,
        trace_format=req.trace_format,
    )


@router.get("/eda/simulations/{simulation_id}")
def get_eda_simulation_status_endpoint(
    simulation_id: str,
    current_user: User = Depends(_get_current_user),
):
    _ = current_user
    job = get_simulation_job(simulation_id)
    if not job:
        raise HTTPException(status_code=404, detail="Simulation not found")
    return job


@router.get("/eda/simulations/{simulation_id}/events")
def stream_eda_simulation_events_endpoint(
    simulation_id: str,
    after_seq: int = Query(default=0, ge=0),
    current_user: User = Depends(_get_current_user),
):
    _ = current_user

    async def _event_stream():
        for payload in iter_simulation_events(simulation_id, after_seq=after_seq):
            yield f"data: {json.dumps(payload)}\n\n"

    return StreamingResponse(_event_stream(), media_type="text/event-stream")


@router.get("/eda/simulations/{simulation_id}/waveform")
def get_eda_waveform_endpoint(
    simulation_id: str,
    signals: list[str] = Query(default=[]),
    start_time: Optional[int] = Query(default=None, ge=0),
    end_time: Optional[int] = Query(default=None, ge=0),
    current_user: User = Depends(_get_current_user),
):
    _ = current_user
    try:
        return read_waveform_slice(
            simulation_id,
            signals=signals or None,
            start_time=start_time,
            end_time=end_time,
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Requested file not found") from exc
