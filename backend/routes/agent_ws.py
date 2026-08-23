import asyncio
import difflib
import hashlib
import json
import logging
import os
import re
import shutil
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect
from sqlalchemy import func
from sqlalchemy.orm import Session

from database.database import SessionLocal
from database.models import (
    ChatMessage,
    ChatThread,
    MentalModelRevision,
    Project,
    ProjectArtifact,
    ProjectArtifactPointer,
    Run,
    User,
)
from original_core.core.ai_client import AIClient
from services.chat_message_timestamps import chat_turn_timestamps
from routes.api import (
    _authenticate_user_from_token,
    _dev_auth_allowed,
    _get_or_create_local_dev_user,
    _require_thread_access,
    _send_ws_error_and_close,
)
from services.agentic_loop import AgenticLoopEngine
from services.project_tasks import (
    derive_agent_name_from_title,
    derive_title_from_message,
    get_task_for_thread,
    serialize_project_task,
    upsert_task_for_thread,
)
from services.token_usage import (
    estimate_text_token_usage,
    persist_chat_token_usage,
    serialize_token_usage,
)
from services.runner import start_job
from services.rtl_project_package import (
    RtlArchiveError,
    extract_rtl_archive,
    is_rtl_archive_filename,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["agent-ws"])

# Known agent tools the frontend can request
AGENT_TOOLS = [
    "readFile",
    "readFiles",
    "listFiles",
    "createFile",
    "applyCodeToFile",
    "runSimulation",
    "readSpecPages",
    "readSpecSection",
    "searchSpec",
]

_TOOL_ALIASES = {
    "read_file": "readFile",
    "readfile": "readFile",
    "get_file": "readFile",
    "getfile": "readFile",
    "read_files": "readFiles",
    "readfiles": "readFiles",
    "list_files": "listFiles",
    "listfiles": "listFiles",
    "list_dir": "listFiles",
    "listdir": "listFiles",
    "ls": "listFiles",
    "create_file": "createFile",
    "createfile": "createFile",
    "write_file": "createFile",
    "writefile": "createFile",
    "apply_code_to_file": "applyCodeToFile",
    "applycodetofile": "applyCodeToFile",
    "edit_file": "applyCodeToFile",
    "editfile": "applyCodeToFile",
    "update_file": "applyCodeToFile",
    "updatefile": "applyCodeToFile",
    "run_simulation": "runSimulation",
    "runsimulation": "runSimulation",
    "run_file": "runSimulation",
    "runfile": "runSimulation",
    "execute": "runSimulation",
    "read_spec_pages": "readSpecPages",
    "readspecpages": "readSpecPages",
    "read_pages": "readSpecPages",
    "readpages": "readSpecPages",
    "read_spec_section": "readSpecSection",
    "readspecsection": "readSpecSection",
    "search_spec": "searchSpec",
    "searchspec": "searchSpec",
}


def _normalize_tool_name(tool_name: str) -> str:
    if not isinstance(tool_name, str):
        return tool_name
    stripped = tool_name.strip()
    if stripped in AGENT_TOOLS:
        return stripped
    compact = stripped.lower().replace("-", "_").replace(" ", "_")
    return _TOOL_ALIASES.get(compact, stripped)


OUTPUTS_DIR = Path(
    os.environ.get("CHIPVERIFY_OUTPUTS_DIR")
    or (Path(__file__).parent.parent.parent / "outputs")
)
OUTPUTS_DIR.mkdir(exist_ok=True, parents=True)

_ARTIFACT_TYPE_TO_LANGUAGE = {
    "spec": "text",
    "rtl": "systemverilog",
    "generated": "systemverilog",
}


# ---------------------------------------------------------------------------
# Tool execution helpers
# ---------------------------------------------------------------------------


def _require_artifact(db: Session, artifact_id: str) -> ProjectArtifact:
    artifact = (
        db.query(ProjectArtifact).filter(ProjectArtifact.id == artifact_id).first()
    )
    if not artifact:
        raise ValueError(f"Artifact not found: {artifact_id}")
    return artifact


def _require_project(db: Session, project_id: str) -> Project:
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise ValueError(f"Project not found: {project_id}")
    return project


def _resolve_spec_artifact_id(
    db: Session,
    args: dict,
    context: Optional[dict],
) -> tuple[Project, ProjectArtifact]:
    """Resolve spec artifact from args or active project pointer."""
    artifact_id = str(args.get("artifact_id") or "").strip()
    project_id = str(
        args.get("project_id")
        or (context or {}).get("project_id")
        or ""
    ).strip()
    if not project_id:
        raise ValueError("project_id is required (or set in context)")

    project = _require_project(db, project_id)
    if artifact_id:
        artifact = _require_artifact(db, artifact_id)
        if artifact.project_id != project.id:
            raise ValueError("artifact_id does not belong to this project")
        if artifact.artifact_type != "spec":
            raise ValueError("artifact_id must reference a spec artifact")
        return project, artifact

    pointer = (
        db.query(ProjectArtifactPointer)
        .filter(ProjectArtifactPointer.project_id == project.id)
        .first()
    )
    if not pointer or not pointer.active_spec_artifact_id:
        raise ValueError("No active spec artifact; pass artifact_id explicitly")
    artifact = _require_artifact(db, pointer.active_spec_artifact_id)
    return project, artifact


def _read_artifact_content(artifact: ProjectArtifact) -> str:
    artifact_path = Path(artifact.file_path)
    if not artifact_path.exists():
        raise ValueError(f"Artifact file is missing from disk: {artifact.file_path}")
    return artifact_path.read_text(encoding="utf-8", errors="replace")


def _write_artifact_content(artifact: ProjectArtifact, content: str) -> None:
    artifact_path = Path(artifact.file_path)
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    artifact_path.write_text(content, encoding="utf-8")


def _infer_language(artifact: ProjectArtifact) -> str:
    return _ARTIFACT_TYPE_TO_LANGUAGE.get(artifact.artifact_type, "text")


def _artifact_dir(organization_id: str, project_id: str, artifact_type: str) -> Path:
    return OUTPUTS_DIR / "orgs" / organization_id / "projects" / project_id / "artifacts" / artifact_type


def _safe_filename(name: str, fallback: str = "untitled.txt") -> str:
    cleaned = Path(name).name.strip()
    if not cleaned or cleaned.startswith("."):
        return fallback
    return cleaned


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _next_artifact_revision(db: Session, project_id: str, artifact_type: str) -> int:
    max_rev = (
        db.query(func.max(ProjectArtifact.revision))
        .filter(
            ProjectArtifact.project_id == project_id,
            ProjectArtifact.artifact_type == artifact_type,
        )
        .scalar()
    )
    return int(max_rev or 0) + 1


def _materialize_rtl_for_run(source_path: Path, filename: str, input_dir: Path) -> Path:
    safe_name = _safe_filename(filename, "rtl_context.sv")
    rtl_dest = input_dir / f"rtl_{safe_name}"
    shutil.copy2(source_path, rtl_dest)

    if not is_rtl_archive_filename(safe_name):
        return rtl_dest

    extract_dir = input_dir / "rtl_project"
    try:
        extract_rtl_archive(rtl_dest, extract_dir)
    except RtlArchiveError as exc:
        raise ValueError(str(exc)) from exc
    return extract_dir


_MUTATING_AGENT_TOOLS = frozenset({"createFile", "applyCodeToFile"})


def _plan_write_allowed(context: Optional[dict], tool_name: str) -> tuple[bool, Optional[str]]:
    """Gate file mutations until the user approves an implementation plan (when requested)."""
    if tool_name not in _MUTATING_AGENT_TOOLS:
        return True, None
    if not isinstance(context, dict):
        return True, None
    if not context.get("require_plan_approval"):
        return True, None
    if context.get("plan_approved") or context.get("implement_plan"):
        return True, None
    return (
        False,
        "Implementation plan approval required. Wait for the user to click Implement "
        "on the plan card before calling createFile or applyCodeToFile.",
    )


async def _execute_tool_call(
    db: Session,
    user: User,
    tool_name: str,
    args: dict,
    context: Optional[dict] = None,
) -> dict:
    """Execute a tool call against the backend database and return the result."""
    tool_name = _normalize_tool_name(tool_name)
    allowed, reason = _plan_write_allowed(context, tool_name)
    if not allowed:
        return {"success": False, "error": reason, "plan_approval_required": True}
    if tool_name == "readFile":
        artifact_id = args.get("artifact_id")
        if not artifact_id:
            raise ValueError("artifact_id is required for readFile")
        artifact = _require_artifact(db, artifact_id)
        content = _read_artifact_content(artifact)
        return {
            "success": True,
            "content": content,
            "filename": artifact.filename,
            "language": _infer_language(artifact),
        }

    elif tool_name == "readFiles":
        artifact_ids = args.get("artifact_ids")
        if not isinstance(artifact_ids, list) or len(artifact_ids) == 0:
            raise ValueError("artifact_ids is required for readFiles")

        files = []
        all_success = True
        for artifact_id in artifact_ids[:50]:
            try:
                artifact = _require_artifact(db, str(artifact_id))
                content = _read_artifact_content(artifact)
                files.append(
                    {
                        "artifact_id": artifact.id,
                        "success": True,
                        "filename": artifact.filename,
                        "language": _infer_language(artifact),
                        "content": content,
                        "error": None,
                    }
                )
            except Exception as exc:
                all_success = False
                files.append(
                    {
                        "artifact_id": str(artifact_id),
                        "success": False,
                        "filename": None,
                        "language": None,
                        "content": None,
                        "error": str(exc),
                    }
                )

        return {
            "success": all_success,
            "files": files,
            "error": None if all_success else "One or more files failed to read",
        }

    elif tool_name == "listFiles":
        project_id = args.get("project_id")
        if not project_id:
            raise ValueError("project_id is required for listFiles")
        project = _require_project(db, project_id)
        artifacts = (
            db.query(ProjectArtifact)
            .filter(ProjectArtifact.project_id == project.id)
            .order_by(
                ProjectArtifact.artifact_type.asc(),
                ProjectArtifact.revision.desc(),
            )
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
        files = [
            {
                "id": a.id,
                "filename": a.filename,
                "artifact_type": a.artifact_type,
                "is_active": a.id in active_ids,
            }
            for a in artifacts
        ]
        return {"success": True, "files": files}

    elif tool_name == "createFile":
        project_id = args.get("project_id")
        filename = args.get("filename")
        artifact_type = args.get("artifact_type")
        content = args.get("content", "")
        replace_existing = bool(args.get("replace_existing"))
        if not project_id:
            raise ValueError("project_id is required for createFile")
        if not filename:
            raise ValueError("filename is required for createFile")
        if not artifact_type:
            raise ValueError("artifact_type is required for createFile")
        if artifact_type not in {"spec", "rtl", "generated"}:
            raise ValueError("artifact_type must be one of: spec, rtl, generated")
        from services.workspace_context import validate_create_file_policy

        validate_create_file_policy(
            db,
            project_id=str(project_id),
            filename=str(filename),
            artifact_type=str(artifact_type),
            context={},
            replace_existing=replace_existing,
        )
        project = _require_project(db, project_id)
        raw_bytes = content.encode("utf-8")
        safe_name = _safe_filename(filename, f"{artifact_type}.txt")
        checksum = _sha256_bytes(raw_bytes)
        revision = _next_artifact_revision(db, project.id, artifact_type)
        artifact_id = str(uuid.uuid4())
        storage_filename = f"v{revision:04d}_{artifact_id}_{safe_name}"
        artifact_path = (
            _artifact_dir(project.organization_id, project.id, artifact_type)
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
            artifact_type=artifact_type,
            revision=revision,
            source="agent",
            file_path=str(artifact_path),
            checksum_sha256=checksum,
            filename=safe_name,
            content_type="text/plain",
            size_bytes=len(raw_bytes),
        )
        db.add(artifact)
        if artifact_type in {"spec", "rtl"}:
            pointer = (
                db.query(ProjectArtifactPointer)
                .filter(ProjectArtifactPointer.project_id == project.id)
                .first()
            )
            if not pointer:
                pointer = ProjectArtifactPointer(project_id=project.id)
                db.add(pointer)
            if artifact_type == "spec":
                pointer.active_spec_artifact_id = artifact.id
            else:
                pointer.active_rtl_artifact_id = artifact.id
        db.flush()
        from services.sv_lint.agent_gate import apply_lint_to_response

        return apply_lint_to_response(
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
            artifact_type=artifact_type,
        )

    elif tool_name == "applyCodeToFile":
        artifact_id = args.get("artifact_id")
        code = args.get("code")
        strategy = args.get("strategy", "smart_insert")
        old_content = args.get("old_content")
        if not artifact_id:
            raise ValueError("artifact_id is required for applyCodeToFile")
        if code is None:
            raise ValueError("code is required for applyCodeToFile")
        if strategy not in {"replace_file", "replace_selection", "smart_insert"}:
            raise ValueError(
                "strategy must be one of: replace_file, replace_selection, smart_insert"
            )
        artifact = _require_artifact(db, artifact_id)
        original_content = _read_artifact_content(artifact)
        if strategy == "replace_file":
            new_content = code
        elif strategy == "replace_selection":
            if old_content is None:
                raise ValueError(
                    "old_content is required for replace_selection strategy"
                )
            if old_content not in original_content:
                raise ValueError("old_content not found in the current file")
            new_content = original_content.replace(old_content, code, 1)
        else:  # smart_insert
            if original_content and not original_content.endswith("\n"):
                new_content = original_content + "\n" + code
            else:
                new_content = original_content + code
        _write_artifact_content(artifact, new_content)
        new_bytes = new_content.encode("utf-8")
        artifact.checksum_sha256 = _sha256_bytes(new_bytes)
        artifact.size_bytes = len(new_bytes)
        diff_lines = difflib.unified_diff(
            original_content.splitlines(keepends=True),
            new_content.splitlines(keepends=True),
            fromfile=artifact.filename,
            tofile=artifact.filename,
        )
        diff_text = "".join(diff_lines)
        from services.sv_lint.agent_gate import apply_lint_to_response

        return apply_lint_to_response(
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

    elif tool_name == "runSimulation":
        project_id = args.get("project_id")
        if not project_id:
            raise ValueError("project_id is required for runSimulation")
        project = _require_project(db, project_id)
        pointer = (
            db.query(ProjectArtifactPointer)
            .filter(ProjectArtifactPointer.project_id == project.id)
            .first()
        )
        if not pointer or not pointer.active_spec_artifact_id:
            raise ValueError("No active spec artifact set for this project")
        if not pointer.active_rtl_artifact_id:
            raise ValueError("No active RTL artifact set for this project")
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
            raise ValueError("Active spec artifact file is missing")
        if not rtl_artifact or not Path(rtl_artifact.file_path).exists():
            raise ValueError("Active RTL artifact file is missing")
        run_id = str(uuid.uuid4())
        run_dir = OUTPUTS_DIR / "orgs" / project.organization_id / "projects" / project.id / "runs" / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "inputs").mkdir(exist_ok=True)
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
        db.flush()
        start_job(run_id, str(rtl_dest), str(spec_dest), str(run_dir))
        thread_id = str((context or {}).get("thread_id") or "").strip()
        if thread_id:
            try:
                from services.project_tasks import link_run_to_thread_task

                link_run_to_thread_task(
                    db,
                    thread_id=thread_id,
                    run=run,
                    user=user,
                )
            except Exception:
                logger.warning(
                    "Failed to link run=%s to project task for thread=%s",
                    run_id,
                    thread_id,
                    exc_info=True,
                )
        return {"success": True, "run_id": run_id}

    elif tool_name == "readSpecPages":
        start_page = int(args.get("start_page") or args.get("startPage") or 1)
        end_page = int(args.get("end_page") or args.get("endPage") or start_page)
        project, artifact = _resolve_spec_artifact_id(db, args, context)
        from services.document_context import ensure_spec_document_index, read_spec_pages

        ensure_spec_document_index(
            organization_id=project.organization_id,
            project_id=project.id,
            artifact_id=artifact.id,
            file_path=artifact.file_path,
            filename=artifact.filename,
            checksum_sha256=artifact.checksum_sha256,
        )
        return read_spec_pages(
            organization_id=project.organization_id,
            project_id=project.id,
            artifact_id=artifact.id,
            start_page=start_page,
            end_page=end_page,
        )

    elif tool_name == "readSpecSection":
        section_title = str(args.get("section_title") or args.get("sectionTitle") or "").strip()
        if not section_title:
            raise ValueError("section_title is required for readSpecSection")
        project, artifact = _resolve_spec_artifact_id(db, args, context)
        from services.document_context import ensure_spec_document_index, read_spec_section

        ensure_spec_document_index(
            organization_id=project.organization_id,
            project_id=project.id,
            artifact_id=artifact.id,
            file_path=artifact.file_path,
            filename=artifact.filename,
            checksum_sha256=artifact.checksum_sha256,
        )
        return read_spec_section(
            organization_id=project.organization_id,
            project_id=project.id,
            artifact_id=artifact.id,
            section_title=section_title,
        )

    elif tool_name == "searchSpec":
        query = str(args.get("query") or "").strip()
        if not query:
            raise ValueError("query is required for searchSpec")
        limit = int(args.get("limit") or 12)
        project, artifact = _resolve_spec_artifact_id(db, args, context)
        from services.document_context import ensure_spec_document_index, search_spec

        ensure_spec_document_index(
            organization_id=project.organization_id,
            project_id=project.id,
            artifact_id=artifact.id,
            file_path=artifact.file_path,
            filename=artifact.filename,
            checksum_sha256=artifact.checksum_sha256,
        )
        return search_spec(
            organization_id=project.organization_id,
            project_id=project.id,
            artifact_id=artifact.id,
            query=query,
            limit=limit,
        )

    else:
        raise ValueError(f"Unknown tool: {tool_name}")


# ---------------------------------------------------------------------------
# Thread title generation
# ---------------------------------------------------------------------------

_TECH_KEYWORDS = [
    "RTL",
    "UVM",
    "FIFO",
    "AXI",
    "AHB",
    "APB",
    "SPI",
    "I2C",
    "UART",
    "SystemVerilog",
    "Verilog",
    "VHDL",
    "testbench",
    "verification",
    "assertion",
    "SVA",
    "coverage",
    "constraint",
    "randomization",
    "interface",
    "modport",
    "clocking",
    "driver",
    "monitor",
    "sequencer",
    "scoreboard",
    "agent",
    "env",
    "sequence",
    "transaction",
    "packet",
    "pipeline",
    "arbiter",
    "decoder",
    "encoder",
    "mux",
    "counter",
    "ALU",
    "FSM",
    "state machine",
    "cache",
    "memory",
    "SRAM",
    "DRAM",
    "RISC-V",
    "ARM",
    "MIPS",
    "bus",
    "bridge",
    "DMA",
    "interrupt",
    "synthesis",
    "timing",
    "CDC",
    "RDC",
    "lint",
    "formal",
]

_GENERAL_GREETINGS = {
    "hi",
    "hello",
    "hey",
    "yo",
    "greetings",
    "good morning",
    "good afternoon",
    "good evening",
}

_DOMAIN_HINT_KEYWORDS = [kw.lower() for kw in _TECH_KEYWORDS] + [
    "systemverilog",
    "uvm",
    "verilog",
    "vhdl",
    "rtl",
    "testbench",
    "assertion",
    "synthesis",
    "timing",
    "coverage",
    "clock",
    "fsm",
    "pipeline",
]

_CODE_INTENT_KEYWORDS = {
    "code",
    "write",
    "create",
    "generate",
    "implement",
    "fix",
    "debug",
    "refactor",
    "edit",
    "file",
}


def _generate_thread_title(first_message: str) -> str:
    """Generate a short title from the first user message."""
    if not first_message:
        return "Project Chat"

    text_upper = first_message.upper()
    found = []
    for kw in _TECH_KEYWORDS:
        if kw.upper() in text_upper:
            found.append(kw)
            if len(found) >= 3:
                break

    if found:
        title = " | ".join(found)
        if len(title) <= 50:
            return title
        return title[:47] + "..."

    truncated = first_message[:50].strip()
    if len(first_message) > 50:
        return truncated + "..."
    return truncated


# ---------------------------------------------------------------------------
# Conversation history helpers
# ---------------------------------------------------------------------------


def _build_conversation_history(
    db: Session,
    thread_id: str,
    new_user_message: str,
    context: dict,
) -> list[dict]:
    """Query recent messages and build a conversation list for the LLM."""
    messages = (
        db.query(ChatMessage)
        .filter(ChatMessage.thread_id == thread_id)
        .order_by(ChatMessage.created_at.desc())
        .limit(30)
        .all()
    )

    history = []

    # RTL Designer mode: always include full history for multi-turn agentic loop
    mode = str(context.get("mode") or "").strip().lower() or None
    is_rtl_designer = mode == "rtl_designer"

    is_general_short = (not is_rtl_designer) and _is_general_short_query(new_user_message)
    recent_window = [] if is_general_short else messages[:20]
    recent_texts = [m.content for m in reversed(recent_window) if m.content]
    selected_system_prompt = _select_system_prompt(new_user_message, recent_texts, mode=mode)
    if _is_mental_model_chat(context):
        selected_system_prompt = _build_mental_model_system_prompt(db, context)

    system_parts = [selected_system_prompt]
    workspace_name = context.get("workspace_name") or context.get("project_name")
    if workspace_name:
        system_parts.append(f"Current workspace: {workspace_name}")
    project_id = context.get("project_id")
    if is_rtl_designer and project_id:
        system_parts.append(f"Active project ID (use this in all createFile tool calls): {project_id}")
    project_context = context.get("project_context")
    if project_context and not is_general_short:
        system_parts.append(f"Project context: {project_context}")
    manifest_summary = context.get("workspace_manifest_summary")
    if manifest_summary and not is_general_short:
        system_parts.append(manifest_summary)
    spec_index = context.get("active_spec_document_index")
    if spec_index and not is_general_short:
        system_parts.append(
            "### Active specification document index\n"
            + spec_index
            + "\n\nUse readSpecPages(start_page, end_page) or readSpecSection(section_title) "
            "before answering spec-specific questions. Cite page numbers."
        )

    history.append({"role": "system", "content": "\n".join(system_parts)})

    for msg in reversed(recent_window):
        if is_general_short and msg.role == "tool":
            continue
        if is_general_short and _contains_any_keyword(
            msg.content or "", _DOMAIN_HINT_KEYWORDS
        ):
            continue
        history.append({"role": msg.role, "content": msg.content})

    history.append({"role": "user", "content": new_user_message})

    return history


def _trim_conversation_history(history: list[dict], max_messages: int = 40) -> list[dict]:
    if len(history or []) <= max_messages:
        return list(history or [])
    items = list(history or [])
    system_messages = [msg for msg in items if msg.get("role") == "system"]
    first_system = system_messages[:1]
    non_system = [msg for msg in items if msg.get("role") != "system"]
    return first_system + non_system[-max(1, max_messages - len(first_system)):]


def _ensure_chat_thread_for_ws(
    db: Session,
    thread_id: str,
    *,
    project_id: Optional[str],
    user_id: str,
    title: str,
) -> ChatThread:
    thread = db.query(ChatThread).filter(ChatThread.id == thread_id).first()
    if thread:
        return thread
    thread = ChatThread(
        id=thread_id,
        project_id=project_id or "default",
        user_id=user_id,
        title=title or "Agent Chat",
    )
    db.add(thread)
    db.flush()
    return thread


async def _ensure_project_task_for_agent_chat(
    websocket: WebSocket,
    db: Session,
    *,
    thread_id: str,
    user: User,
    user_message: str,
    context: dict,
) -> Optional[dict]:
    project_id = str(context.get("project_id") or "").strip()
    if not project_id:
        return None

    title = derive_title_from_message(user_message)
    thread = _ensure_chat_thread_for_ws(
        db,
        thread_id,
        project_id=project_id,
        user_id=user.id,
        title=title,
    )
    if thread.project_id != project_id:
        return None

    existing = get_task_for_thread(db, thread.id)
    is_new = existing is None
    agent_name = (thread.agent_name or "").strip() or derive_agent_name_from_title(title)
    task = upsert_task_for_thread(
        db,
        thread=thread,
        user=user,
        title=title,
        description=user_message,
        agent_name=agent_name,
        source="agent_chat",
        status="in_progress",
    )
    if task.status == "todo":
        task.status = "in_progress"
        task.progress_pct = max(task.progress_pct, 5)
        db.commit()
        db.refresh(task)
    elif task.status in {"completed", "blocked", "cancelled"}:
        from database.models import ChatThreadState

        task.status = "in_progress"
        task.progress_pct = max(task.progress_pct, 5)
        task.completed_at = None
        task.run_id = None
        state = (
            db.query(ChatThreadState)
            .filter(ChatThreadState.thread_id == thread.id)
            .first()
        )
        if state and state.context_run_id:
            state.context_run_id = None
        db.commit()
        db.refresh(task)

    event_type = "project_task_created" if is_new else "project_task_updated"
    payload = serialize_project_task(task)
    await websocket.send_json({"type": event_type, "task": payload})
    context["active_task_id"] = task.id
    return payload


def _save_chat_messages(
    db: Session,
    thread_id: str,
    user_message: str,
    assistant_message: str,
    context: Optional[dict] = None,
) -> tuple[str, str]:
    """Save user and assistant messages to the database and update thread timestamp."""
    thread = db.query(ChatThread).filter(ChatThread.id == thread_id).first()
    if not thread:
        ctx = context or {}
        thread = ChatThread(
            id=thread_id,
            project_id=ctx.get("project_id", "default"),
            user_id=ctx.get("user_id", "default"),
            title=(user_message[:60] or "Agent Chat"),
        )
        db.add(thread)
        db.flush()

    user_at, assistant_at = chat_turn_timestamps()
    user_msg = ChatMessage(
        id=str(uuid.uuid4()),
        thread_id=thread_id,
        role="user",
        content=user_message,
        created_at=user_at,
    )
    assistant_msg = ChatMessage(
        id=str(uuid.uuid4()),
        thread_id=thread_id,
        role="assistant",
        content=assistant_message,
        created_at=assistant_at,
    )

    thread.updated_at = datetime.now(timezone.utc)
    db.add(user_msg)
    db.add(assistant_msg)
    db.add(thread)
    try:
        db.commit()
    except Exception:
        db.rollback()
        logger.warning("Failed to save chat messages for thread %s", thread_id, exc_info=True)
        raise
    return user_msg.id, assistant_msg.id


def _save_tool_message(
    db: Session,
    thread_id: str,
    tool_result: dict,
):
    """Save a tool result message to the database."""
    tool_msg = ChatMessage(
        id=str(uuid.uuid4()),
        thread_id=thread_id,
        role="tool",
        content=json.dumps(tool_result),
    )
    db.add(tool_msg)
    try:
        db.commit()
    except Exception:
        db.rollback()
        logger.warning("Failed to save tool message for thread %s", thread_id, exc_info=True)
        raise


SYSTEM_PROMPT = """\
You are an expert AI assistant for semiconductor chip design and UVM verification.
You can help with: SystemVerilog RTL coding, UVM testbench generation, verification planning, debugging, and code review.

When generating code, wrap it in <GENERATEDCODE lang="language">...</GENERATEDCODE> tags.
Use appropriate language identifiers: systemverilog, verilog, vhdl, python, tcl, makefile.

## LINT GATE (svls / svlint)
After `createFile` or `applyCodeToFile` on RTL/SystemVerilog (`.sv`, `.svh`, `.v`, `.vh`), the backend runs svls lint automatically.
- If the tool returns `LINT_FAILED`, read the `diagnostics` array (line, rule, message) and fix the file before continuing.
- Prefer `applyCodeToFile` with `strategy: "replace_file"` to fix lint issues.
- Do not proceed to the next file or `attemptCompletion` until lint passes.
- Follow svlint style: use `logic` not `reg`/`wire`, use `always_comb`/`always_ff`/`always_latch` not legacy `always @*`.

When you need to read or modify files, use tool calls. Available tools:
- readFile: { "artifact_id": "..." }
- readFiles: { "artifact_ids": ["...", "..."] }
- readSpecPages: { "project_id": "...", "start_page": 1, "end_page": 30 } — page-bounded spec text (max 30 pages)
- readSpecSection: { "project_id": "...", "section_title": "..." }
- searchSpec: { "project_id": "...", "query": "reset behavior" }
- listFiles: { "project_id": "..." }
- createFile: { "project_id": "...", "filename": "...", "artifact_type": "...", "content": "..." }
- applyCodeToFile: { "artifact_id": "...", "code": "...", "strategy": "smart_insert", "old_content": "..." }
- runSimulation: { "project_id": "..." }

## VERIFICATION FLOW (IMPORTANT)
When the user asks to "verify", "check", or "test" a design block, follow this CONVERSATIONAL flow:

**Step 1: Analyze** - Call `analyzeDesign` with the project_id and target module.
**Step 2: Present Mental Model** - Show the user what you found:
  - Design summary (top module, ports, hierarchy)
  - Key design features (FSMs, protocols, clock domains)
  - Requirements extracted from spec
  - Any open questions that need human answers
**Step 3: Generate Plan** - AUTOMATICALLY generate the test plan from the mental model.
  Include all relevant scenario types: reset, functional, boundary, protocol, FSM coverage, stress.
**Step 4: Display Plan + Recommend** - Show the plan as a TABLE (ID, Name, Category, Priority).
  Then SUGGEST which verification types to run based on design complexity:
  "Based on your design, I recommend:"
  - ✅ Unit Simulation (always recommended)
  - ✅/⬜ Formal Verification (recommended if FSMs or protocols detected)
  - ✅/⬜ UVM Environment (recommended if design is complex: CDC, many interfaces)
  "You can run any combination — just tell me which ones you'd like."
  The user makes the FINAL decision. They can override and choose any type they want.
**Step 5: Plan Tuning** - Ask: "Would you like to tune this plan? You can say things like 'Add an overflow test' or 'Remove the stress test'."
  - If user gives feedback, call `tuneTestPlan` with their feedback
  - Show the updated plan and ask again
  - Repeat until user says "looks good" or "generate testbench"
**Step 6: Execute** - Generate verification artifacts for the types the user approved:
  - Call `generateTestbench` for UnitSim testbench (if selected)
  - Call `generateFormalProperties` for formal verification (if selected)
  - Call `generateUVMEnvironment` for UVM (if selected)
  - Call `runVerification` to execute
**Step 7: Report** - Present results clearly with pass/fail status for each test.
**Step 8: Self-Heal** - If verification FAILS:
  a) Call `diagnoseAndFix` with the error log
  b) Show the proposed fix (diff) to the user
  c) Ask: "Should I apply this fix and re-run verification?"
  d) If user approves, apply the patch and call `runVerification` again
  e) Repeat until passing or user says stop
**Step 9: Coverage** - After passing tests, call `analyzeCoverage` to check coverage gaps
**Step 10: Summary** - Give a final verification summary with pass/fail, coverage grade, and any remaining gaps

## CADENCE FLOW (after UVM files exist)
When the user asks to run Cadence/Xcelium, or context has `cadence_verification_mode`:
1. Call `checkCadenceStatus` — if blocked, tell user to configure Cadence via the status bar
2. Call `runCadenceSimulation` with `generated_artifact_ids` from context when present
3. On failure: read `feedback_memory.root_causes` → `applyCadenceFixes` or `applyCodeToFile` → `runCadenceSimulation` again (max 3 rounds)
4. On pass: call `analyzeCoverage` and summarize the closure report
Do NOT regenerate UVM when staged artifacts already exist — simulate them with Cadence.

NEVER skip the plan display and tuning step. The user must see the plan before testbench generation.

## EVOLVE FLOW (Design Changes)
When the user says "I changed the RTL", "design was updated", or provides new RTL code:

**Step 1: Detect** — Call `detectDesignChanges` with old and new RTL
**Step 2: Present** — Show what changed: ports added/removed, FSM state changes, logic changes
**Step 3: Ask** — "Should I evolve the verification environment to match?"
**Step 4: Evolve** — If approved, call `evolveVerification` to auto-heal testbenches
**Step 5: Report** — Show: healed files, predicted test failures, recommended regression suite
**Step 6: Re-verify** — Ask if user wants to run the recommended regression suite

## MULTIMODAL FLOW (Design Diagrams)
When the user uploads or references an image file (.png, .jpg, .webp) containing a design diagram:

**Step 1: Parse** — Call `parseDesignImage` with the image path and any user context
**Step 2: Present** — Show what was extracted: modules, ports, FSMs, protocols, transaction flows
**Step 3: Confirm** — Ask: "I found these design elements. Should I merge this into the mental model?"
**Step 4: Merge** — If confirmed, call `analyzeDesign` to build the full mental model from extracted data
**Step 5: Continue** — Proceed with normal verification flow (UnitSim/Formal/UVM)

Supported diagram types: block diagrams, state machines, whiteboard sketches, pin tables, architecture visuals.

Use only these exact camelCase tool names. Do not use VS Code-style names such as list_dir, read_file, or create_file.

When issuing a tool call, output exactly one JSON object inside a fenced code block using this schema:
```json
{"tool":"createFile","args":{"project_id":"...","filename":"...","artifact_type":"rtl","content":"..."}}
```
Use context values when available (project_id, active_file_id).

For multi-step tasks, always plan internally and continue until complete:
1) discover files with listFiles if needed,
2) read relevant files (readFile/readFiles),
3) analyze results,
4) only then provide final explanation.
Do not stop after only one discovery call if the user requested full analysis.
"""

SYSTEM_PROMPT_GENERAL = """\
You are a helpful AI coding assistant in Chipix Studio, an AI-powered chip design and verification platform.

Your capabilities include:
- Answering questions about code, design, and verification
- Writing and editing SystemVerilog, UVM, Python, and other code
- Creating new files and editing existing files
- Explaining concepts and debugging issues
- Running simulations and analyzing results

When the user asks you to write code, provide complete, working code wrapped in <GENERATEDCODE lang="language">...</GENERATEDCODE> tags.
Use appropriate language identifiers: systemverilog, verilog, python, tcl, makefile, markdown.

When the user asks you to create a file or write code to a file, respond with the code block and specify the filename.

For file operations, emit a tool call JSON block that follows this exact schema:
```json
{"tool":"createFile","args":{"project_id":"...","filename":"...","artifact_type":"rtl","content":"..."}}
```
For file edits, use:
```json
{"tool":"applyCodeToFile","args":{"artifact_id":"...","code":"...","strategy":"smart_insert"}}
```
Use context values when available (project_id, active_file_id, attached_files).

Allowed tools are exactly: readFile, readFiles, listFiles, createFile, applyCodeToFile, runSimulation.
Do not call tools outside this list.

When the user asks for multi-file review/explanation, continue tool-calling iteratively until you have gathered enough file content. Do not stop after listFiles alone.

Respond clearly and directly. Be concise but thorough."""


SYSTEM_PROMPT_RTL_DESIGNER = """\
You are an expert RTL Design Agent inside Chipix Studio — a Cursor/Claude Code-style AI coding assistant specialized in Verilog and SystemVerilog.

Your workflow has three strict phases:

## PHASE 1 — CLARIFY (if needed)

### Non-negotiable: questions MUST use ```chipix:ask``` fences

Whenever you need **any** answer from the user (clarification, preference, parameter choice, yes/no phrased as options), the Chipix UI **only** shows clickable choices if you emit structured blocks.

**RULE:** Do not ask questions in plain markdown, numbered lists, or bold prose alone. Those render as dead text — the user cannot tap options.

Instead:
1. Optionally write **at most two short sentences** of intro (no questions in the intro — move questions into JSON only).
2. Then emit **one ```chipix:ask``` fenced JSON block per question** (1–3 blocks per response when multiple details are unknown).
3. **Never duplicate** the same question text outside the fence; the `question` field is what the UI displays.

Each fence must be EXACTLY this wrapper (three backticks, tag `chipix:ask`, newline, JSON object, newline, three backticks):

```chipix:ask
{
  "question": "<single clear question>",
  "sub": "<optional one-line hint>",
  "choices": [
    { "id": "<unique_id>", "label": "<short label>" },
    { "id": "<unique_id>", "label": "<short label>" }
  ]
}
```

Requirements:
- **choices**: 2–5 objects; every **id** unique; **label** is what the user taps.
- Include **{"id": "recommend", "label": "Recommend for me"}** when sensible; include **{"id": "custom", "label": "Other — I'll type details"}** with optional `"allowsText": true` when freeform may be needed.
- Use `"multi": true` only if the user may pick several options for that step.
- **Phase 1:** no tools, no plan, no code — only intro (optional) + ```chipix:ask``` block(s). Then stop.

If the user's request is already unambiguous and complete, **skip Phase 1 entirely** and go to Phase 2 with no questions.

Topics you might clarify when needed (each as its own ```chipix:ask``` if you ask):
- Target clock frequency / timing
- Bus protocol (AXI4, AHB, APB, custom)
- Reset polarity / sync vs async
- Data width / address width / FIFO depth thresholds (e.g. almost-full)
- Pipeline depth, FPGA vs ASIC, SystemVerilog vs Verilog

## PHASE 2 — PLAN (required `chipix:plan` fence)

After Phase 1 is complete (or skipped), emit **exactly one** structured plan fence. Do **not** output the plan as markdown-only prose — the UI only renders approval buttons from the fence.

**RULE:** Phase 2 = optional short intro (max 2 sentences, no file list in prose) + one ```chipix:plan``` block. No tools. No code. Then stop.

```chipix:plan
{
  "phase": "design",
  "title": "RTL design plan",
  "summary": "Plain English: what you understood from the user request and every clarification answer.",
  "assumptions": ["<decision or default>", "..."],
  "constraints": ["SystemVerilog", "..."],
  "risks": ["<optional open item>"],
  "steps": [
    {
      "id": "fifo_core",
      "label": "sync_fifo.sv — FIFO core",
      "why": "Implements gray-code pointers and dual-clock crossing",
      "deliverables": ["sync_fifo.sv"]
    }
  ]
}
```

Requirements:
- **summary** MUST reference each clarified decision (width, protocol, reset, etc.).
- **steps** lists every file you will create in Phase 3 with **deliverables** filenames.
- Include 2–8 **assumptions** when you inferred defaults.
- Wait for the user to click **Implement** on the plan card (or send explicit implement approval). Do not call createFile until then.

## PHASE 3 — EXECUTE (after user approves / clicks Implement)
Call the createFile tool for each file in the plan, ONE FILE AT A TIME.
Wait for a **lint-clean** tool result before proceeding to the next file.
If createFile or applyCodeToFile returns `LINT_FAILED`, fix that file immediately (applyCodeToFile replace_file) and do not start the next file until lint passes.

**While writing files (important):**
- Do NOT post per-file "Written …" lines, emoji checklists, or hourglass status in the chat.
- Tool results already show each file in the UI — repeating them in prose is noisy and confusing.
- Stay quiet in the chat during file generation unless the user asked a question mid-run.
- Use the task list / thinking status only; save all explanation for attemptCompletion.

After ALL files are done, call attemptCompletion with a **single readable summary** using this markdown shape:

```
## Done

**Design:** <one line>

### Files created
| File | Role |
|------|------|
| `foo.sv` | <one line> |
| `foo_tb.sv` | <one line> |

### Highlights
- <bullet: key capability or interface>
- <bullet: testbench / package notes if any>
```

Keep it short (under ~12 lines). No duplicate file lists, no inline emoji status lines.

## LINT GATE (svls / svlint)
Every RTL write is validated by svls immediately after createFile/applyCodeToFile.
- Tool failure with `LINT_FAILED` means parse or style-rule errors — fix before continuing.
- Read diagnostics: line number, rule name (e.g. `wire_reg`, `legacy_always`), and message.
- Fix with applyCodeToFile (`replace_file`) using lint-clean SystemVerilog.

## CODE QUALITY RULES
- Always synthesizable (no `#delays` in RTL, no `initial` blocks in RTL)
- Default to synchronous active-low reset unless specified otherwise
- Use SystemVerilog `logic` type (not `reg`/`wire` unless Verilog mode)
- Parameterize clock frequency, data width, and bus widths at the top
- Follow IEEE naming: `snake_case` for signals, `PascalCase` for modules
- Include file header comment: `// Module: <name>\\n// Description: <description>\\n// Author: Chipix AI`
- Testbenches must use UVM base classes (uvm_test, uvm_env, uvm_agent) if UVM is requested
- Testbenches must include at least: reset sequence, basic directed test, coverage group

## TOOL USAGE
You have native tool access. Use these tools:
- createFile: to create each RTL/testbench/package file
- listFiles: to check existing project files
- readFile: to read an existing file (avoid for large PDF specs)
- readSpecPages / readSpecSection / searchSpec: for large specification documents (use the document index in context)
- applyCodeToFile: to update an existing file
- runSimulation: to run a simulation after all files are created
- attemptCompletion: to signal you are done

Use project_id from context. Never ask for project_id — it is always available.
artifact_type must be: "rtl" for RTL modules, "generated" for testbenches/packages/interfaces.

## WORKSPACE AWARENESS (existing projects)
The UI injects a **Project workspace** inventory on every turn (all threads share the same project files).
- Before Phase 3, assume filenames in that inventory already exist — do not `createFile` with the same name unless the user asked to replace/update.
- **Additive default:** new designs → new filenames; use `artifact_type: "generated"` for testbenches and helper modules when the project already has active RTL.
- **Edit existing:** `readFile` → `applyCodeToFile` with the artifact id from the inventory.
- **Replace:** only when the user explicitly requests it — pass `replace_existing: true` on createFile.
- Call `listFiles` if the inventory is missing or you need to confirm ids.

## STEERING DURING EXECUTION
If the user sends a message while you are in Phase 3:
- "skip <file>": skip that file and continue
- "add <requirement>": incorporate it into remaining files
- "stop": stop and summarize what was completed
- A question: answer briefly, then continue from where you left off

Remember: You are an autonomous agent. Once approved, keep writing files until ALL are done.\
"""

SYSTEM_PROMPT_MENTAL_MODEL_CHAT = """\
You are the Mental Model Agent for the active chip design in Chipix Studio.

Use the persisted mental model context below as the source of truth for design-aware answers.
Treat RTL/spec facts outside that model as unknown unless you explicitly inspect project files with tools.

Rules:
- Answer design, verification, coverage, UVM, formal, and test-plan questions from the mental model first.
- If a detail is not captured in the mental model, say it is not captured and suggest rebuilding, refining, or inspecting the relevant source.
- Do not invent protocols, behavior, state machines, requirements, coverage points, or bugs.
- You may use readFile/readFiles/listFiles/queryMentalModel when more source evidence is needed.
- When the user explicitly asks to run Cadence/Xcelium simulation, you may call checkCadenceStatus and runCadenceSimulation.
- Do not create files, edit files, or start verification unless the user explicitly asks for that action.
- For requested implementation or generation work, first explain the plan and ask for approval.
- If the user asks something unrelated to the current design, ask them to switch to Normal Chat.
"""


def _is_mental_model_chat(context: Optional[dict]) -> bool:
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


CADENCE_VERIFICATION_SYSTEM_PROMPT = """\
You are the Cadence Verification Agent for Chipix Studio.

The user has generated UVM collateral and wants a full Cadence Xcelium simulation
(compile → elaborate → simulate) with automatic repair when possible.

Rules:
0. The active project_id is bound server-side from the user's session. Do NOT invent,
   guess, or substitute project IDs (mental_model_revision_id is NOT a project_id).
   Call runCadenceSimulation without project_id — the server uses the open project.
1. Call checkCadenceStatus first. If unavailable, explain how to configure Cadence.
2. Call runCadenceSimulation. Staged generated_artifact_ids are injected server-side
   when omitted. Only pass overrides if the user explicitly requests different artifacts.
3. On failure, use feedback_memory.root_causes. Apply fixes via applyCadenceFixes or
   applyCodeToFile, then re-run (max 3 agent repair rounds).
4. Report phase progress and final pass/fail clearly. Do not regenerate UVM unless files are missing.
5. After pass, summarize coverage and closure report highlights.
"""


def _build_cadence_verification_prompt(context: dict) -> str:
    from services.cadence_agent_prompt import build_cadence_verification_prompt

    return build_cadence_verification_prompt(context)


def _is_cadence_verification_mode(context: Optional[dict]) -> bool:
    if not isinstance(context, dict):
        return False
    return bool(context.get("cadence_verification_mode"))


def _as_list(value: Any) -> list:
    return value if isinstance(value, list) else []


def _compact_items(items: Any, limit: int = 40) -> list:
    return _as_list(items)[:limit]


def _compact_ports(ports: Any) -> list[dict[str, Any]]:
    compacted = []
    for port in _compact_items(ports, limit=80):
        if not isinstance(port, dict):
            continue
        compacted.append(
            {
                "name": port.get("name"),
                "direction": port.get("direction"),
                "width": port.get("width"),
                "bus_range": port.get("bus_range"),
                "port_type": port.get("port_type"),
            }
        )
    return compacted


def _compact_verification(verification: Any) -> dict[str, Any]:
    if not isinstance(verification, dict):
        return {}
    return {
        "unit_tests": _compact_items(verification.get("unit_tests"), limit=40),
        "formal_properties": _compact_items(
            verification.get("formal_properties"), limit=40
        ),
        "coverage_points": _compact_items(
            verification.get("coverage_points"), limit=60
        ),
        "uvm_scenarios": _compact_items(verification.get("uvm_scenarios"), limit=40),
        "scoreboard_checks": _compact_items(
            verification.get("scoreboard_checks") or verification.get("uvm_scoreboard_checks"), limit=40
        ),
        "uvm_scoreboard_checks": _compact_items(
            verification.get("uvm_scoreboard_checks") or verification.get("scoreboard_checks"), limit=40
        ),
        "uvm_coverage_points": _compact_items(
            verification.get("uvm_coverage_points"), limit=60
        ),
    }


def _unwrap_mental_model_content(content: Any) -> dict[str, Any]:
    if not isinstance(content, dict):
        return {}
    if isinstance(content.get("mental_model"), dict):
        nested = content["mental_model"]
        if isinstance(nested.get("content"), dict):
            return nested["content"]
        return nested
    if isinstance(content.get("content"), dict):
        return content["content"]
    return content


def _compact_mental_model_for_prompt(content: dict[str, Any]) -> dict[str, Any]:
    model = _unwrap_mental_model_content(content)
    design = model.get("design") if isinstance(model.get("design"), dict) else {}
    project_scan = (
        model.get("project_scan") if isinstance(model.get("project_scan"), dict) else {}
    )

    return {
        "schema_version": model.get("schema_version"),
        "status": model.get("status", "ready"),
        "summary": model.get("summary"),
        "design": {
            "top_module": design.get("top_module"),
            "modules": _compact_items(design.get("modules"), limit=40),
            "hierarchy_tree": design.get("hierarchy_tree") or {},
            "ports": _compact_ports(design.get("ports")),
            "parameters": _compact_items(design.get("parameters"), limit=40),
            "clock_domains": _compact_items(design.get("clock_domains"), limit=20),
            "sub_instances": _compact_items(design.get("sub_instances"), limit=40),
            "fsms": _compact_items(design.get("fsms"), limit=20),
            "protocols": _compact_items(design.get("protocols"), limit=20),
            "register_fields": _compact_items(design.get("register_fields"), limit=40),
            "register_map": _compact_items(design.get("register_map"), limit=40),
            "expected_behaviors": _compact_items(design.get("expected_behaviors"), limit=30),
            "transaction_flows": _compact_items(design.get("transaction_flows"), limit=30),
            "constraints": _compact_items(design.get("constraints"), limit=40),
            "total_files": design.get("total_files"),
            "total_lines": design.get("total_lines"),
            "total_always_blocks": design.get("total_always_blocks"),
        },
        "project_scan": {
            "rtl_files": _compact_items(project_scan.get("rtl_files"), limit=60),
            "spec_files": _compact_items(project_scan.get("spec_files"), limit=20),
            "total_files": project_scan.get("total_files"),
            "total_lines": project_scan.get("total_lines"),
        },
        "requirements": _compact_items(model.get("requirements"), limit=60),
        "verification": _compact_verification(model.get("verification")),
        "risks": _compact_items(model.get("risks"), limit=40),
        "open_questions": _compact_items(model.get("open_questions"), limit=40),
    }


def _bounded_mental_model_json(compact: dict[str, Any], max_chars: int = 18000) -> str:
    serialized = json.dumps(compact, indent=2, ensure_ascii=False)
    if len(serialized) <= max_chars:
        return serialized

    reduced = dict(compact)
    reduced["truncated"] = True
    reduced["truncation_note"] = (
        "Large mental model compacted before serialization to keep JSON valid."
    )
    design = dict(reduced.get("design") or {})
    project_scan = dict(reduced.get("project_scan") or {})
    verification = dict(reduced.get("verification") or {})

    design["ports"] = _compact_items(design.get("ports"), limit=24)
    design["modules"] = _compact_items(design.get("modules"), limit=24)
    design["parameters"] = _compact_items(design.get("parameters"), limit=16)
    design["sub_instances"] = _compact_items(design.get("sub_instances"), limit=20)
    design["fsms"] = _compact_items(design.get("fsms"), limit=10)
    design["protocols"] = _compact_items(design.get("protocols"), limit=10)
    design["register_fields"] = _compact_items(design.get("register_fields"), limit=16)
    design["register_map"] = _compact_items(design.get("register_map"), limit=16)
    design["expected_behaviors"] = _compact_items(design.get("expected_behaviors"), limit=12)
    design["transaction_flows"] = _compact_items(design.get("transaction_flows"), limit=12)
    design["constraints"] = _compact_items(design.get("constraints"), limit=16)
    project_scan["rtl_files"] = _compact_items(project_scan.get("rtl_files"), limit=24)
    project_scan["spec_files"] = _compact_items(project_scan.get("spec_files"), limit=8)

    for key in (
        "unit_tests",
        "formal_properties",
        "coverage_points",
        "uvm_scenarios",
        "scoreboard_checks",
        "uvm_scoreboard_checks",
        "uvm_coverage_points",
    ):
        verification[key] = _compact_items(verification.get(key), limit=16)

    reduced["design"] = design
    reduced["project_scan"] = project_scan
    reduced["requirements"] = _compact_items(reduced.get("requirements"), limit=24)
    reduced["verification"] = verification
    reduced["risks"] = _compact_items(reduced.get("risks"), limit=16)
    reduced["open_questions"] = _compact_items(reduced.get("open_questions"), limit=16)

    serialized = json.dumps(reduced, indent=2, ensure_ascii=False)
    if len(serialized) <= max_chars:
        return serialized

    fallback = {
        "truncated": True,
        "truncation_note": "Mental model exceeded prompt budget; high-signal summary retained.",
        "schema_version": reduced.get("schema_version"),
        "status": reduced.get("status"),
        "summary": reduced.get("summary"),
        "design": {
            "top_module": design.get("top_module"),
            "modules": _compact_items(design.get("modules"), limit=12),
            "ports": _compact_items(design.get("ports"), limit=12),
            "protocols": _compact_items(design.get("protocols"), limit=6),
            "fsms": _compact_items(design.get("fsms"), limit=6),
            "constraints": _compact_items(design.get("constraints"), limit=8),
            "expected_behaviors": _compact_items(design.get("expected_behaviors"), limit=6),
            "transaction_flows": _compact_items(design.get("transaction_flows"), limit=6),
            "total_files": design.get("total_files"),
            "total_lines": design.get("total_lines"),
        },
        "requirements": _compact_items(reduced.get("requirements"), limit=12),
        "verification": {
            key: _compact_items(verification.get(key), limit=8)
            for key in (
                "unit_tests",
                "formal_properties",
                "coverage_points",
                "uvm_scenarios",
                "scoreboard_checks",
                "uvm_scoreboard_checks",
                "uvm_coverage_points",
            )
        },
        "open_questions": _compact_items(reduced.get("open_questions"), limit=8),
    }
    return json.dumps(fallback, indent=2, ensure_ascii=False)


def _load_mental_model_for_chat(
    db: Session,
    context: dict,
) -> Optional[MentalModelRevision]:
    project_id = str(context.get("project_id") or "").strip()
    revision_id = str(context.get("mental_model_revision_id") or "").strip()

    query = db.query(MentalModelRevision)
    if revision_id:
        query = query.filter(MentalModelRevision.id == revision_id)
        if project_id:
            query = query.filter(MentalModelRevision.project_id == project_id)
        return query.first()

    if not project_id:
        return None

    return (
        query.filter(MentalModelRevision.project_id == project_id)
        .order_by(MentalModelRevision.revision.desc())
        .first()
    )


def _build_mental_model_system_prompt(db: Session, context: dict) -> str:
    model = _load_mental_model_for_chat(db, context)
    if not model:
        return (
            SYSTEM_PROMPT_MENTAL_MODEL_CHAT
            + "\n\nNo persisted mental model was attached to this chat turn. "
            "Tell the user to build or refresh the mental model before asking design-grounded questions."
        )

    try:
        content = json.loads(model.content_json or "{}")
    except Exception:
        content = {"raw": model.content_json or ""}

    compact = _compact_mental_model_for_prompt(content)
    compact["revision_metadata"] = {
        "id": model.id,
        "project_id": model.project_id,
        "revision": model.revision,
        "status": model.status,
        "schema_version": model.schema_version,
        "source_spec_artifact_id": model.source_spec_artifact_id,
        "source_rtl_artifact_id": model.source_rtl_artifact_id,
        "summary_text": model.summary_text,
    }
    serialized = _bounded_mental_model_json(compact)

    return (
        SYSTEM_PROMPT_MENTAL_MODEL_CHAT
        + "\n\nPersisted mental model context:\n"
        + "```json\n"
        + serialized
        + "\n```"
    )


def _contains_any_keyword(text: str, keywords: list[str]) -> bool:
    lowered = (text or "").lower()
    return any(kw in lowered for kw in keywords)


def _is_general_short_query(message: str) -> bool:
    msg = (message or "").strip().lower()
    if not msg:
        return False
    if msg in _GENERAL_GREETINGS:
        return True
    verification_terms = {
        "verify",
        "verification",
        "test",
        "simulate",
        "simulation",
        "analyze",
        "rtl",
        "uvm",
        "formal",
        "fifo",
        "bridge",
        "design",
        "testbench",
        "coverage",
    }
    if _contains_any_keyword(msg, list(_DOMAIN_HINT_KEYWORDS)) or _contains_any_keyword(msg, list(verification_terms)):
        return False
    if len(msg) <= 40 and not _contains_any_keyword(msg, list(_CODE_INTENT_KEYWORDS)):
        return True
    return False


def _should_use_domain_prompt(
    new_user_message: str, recent_messages: list[str]
) -> bool:
    if _contains_any_keyword(new_user_message, _DOMAIN_HINT_KEYWORDS):
        return True
    for msg in recent_messages[-4:]:
        if _contains_any_keyword(msg, _DOMAIN_HINT_KEYWORDS):
            return True
    return False


def _select_system_prompt(
    new_user_message: str,
    recent_messages: list[str],
    mode: str | None = None,
) -> str:
    # RTL Designer mode — uses the autonomous Cursor-style agent prompt
    if mode == "rtl_designer":
        return SYSTEM_PROMPT_RTL_DESIGNER
    if _is_general_short_query(new_user_message):
        return SYSTEM_PROMPT_GENERAL
    if _should_use_domain_prompt(new_user_message, recent_messages):
        return SYSTEM_PROMPT
    return SYSTEM_PROMPT_GENERAL


CHUNK_SIZE = 180


def _float_env(name: str, default: float, minimum: float = 0.0) -> float:
    raw = os.getenv(name)
    if raw is None or str(raw).strip() == "":
        return default
    try:
        return max(minimum, float(raw))
    except (TypeError, ValueError):
        logger.warning("Invalid %s=%r; using default %s", name, raw, default)
        return default


STREAM_CHUNK_DELAY_SECONDS = _float_env(
    "CHIPVERIFY_STREAM_CHUNK_DELAY_SECONDS", 0.005
)
CLIENT_PROTOCOL_VERSION = "1.0.0"
WS_MESSAGE_TIMEOUT_SECONDS = max(
    10, int(os.getenv("CHIPVERIFY_WS_MESSAGE_TIMEOUT_SECONDS", "300"))
)
REQUEST_TRACK_TTL_SECONDS = max(
    60, int(os.getenv("CHIPVERIFY_WS_REQUEST_TRACK_TTL_SECONDS", "900"))
)
RATE_LIMIT_WINDOW_SECONDS = max(
    10, int(os.getenv("CHIPVERIFY_WS_RATE_LIMIT_WINDOW_SECONDS", "60"))
)
RATE_LIMIT_MESSAGES_PER_WINDOW = max(
    1, int(os.getenv("CHIPVERIFY_WS_RATE_LIMIT_MESSAGES_PER_WINDOW", "120"))
)

_CODE_BLOCK_RE = re.compile(
    r"<GENERATEDCODE\s+lang=[\"']([^\"']+)[\"']\s*>(.*?)</GENERATEDCODE>",
    re.DOTALL,
)

_TOOL_CALL_RE = re.compile(
    r"```(?:json)?\s*\{\s*[\"']tool[\"']\s*:\s*[\"'](\w+)[\"']\s*,\s*[\"']args[\"']\s*:\s*(\{.*?\})\s*\}\s*```",
    re.DOTALL,
)

_TOOL_CALL_INLINE_RE = re.compile(
    r"\{\s*[\"']tool[\"']\s*:\s*[\"'](\w+)[\"']\s*,\s*[\"']args[\"']\s*:\s*(\{.*?\})\s*\}",
    re.DOTALL,
)

_END_CALL_RE = re.compile(
    r"```(?:json)?\s*(\{.*?[\"']function[\"']\s*:\s*[\"']endConversation[\"'].*?\})\s*```",
    re.DOTALL,
)

_MARKDOWN_CODE_BLOCK_RE = re.compile(
    r"```([a-zA-Z0-9_+-]*)\n(.*?)\n```",
    re.DOTALL,
)


def _iter_json_tool_call_objects(text: str):
    decoder = json.JSONDecoder()
    for match in re.finditer(r"\{", text or ""):
        start = match.start()
        try:
            payload, end_offset = decoder.raw_decode(text[start:])
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict):
            continue
        tool_name = payload.get("tool")
        args = payload.get("args")
        if not isinstance(tool_name, str) or not isinstance(args, dict):
            continue
        yield start, start + end_offset, _normalize_tool_name(tool_name), args


def _normalize_message_type(raw_type) -> Optional[str]:
    if raw_type is None:
        return None
    if not isinstance(raw_type, str):
        return str(raw_type)
    normalized = raw_type.strip()
    if not normalized:
        return None
    if normalized.lower() in {"none", "null", "undefined"}:
        return None
    return normalized


def _extract_request_id(payload: dict) -> str:
    request_id = str(payload.get("id") or payload.get("request_id") or "").strip()
    return request_id or str(uuid.uuid4())


def _extract_user_prompt(payload: dict) -> str:
    return str(
        payload.get("message") or payload.get("prompt") or payload.get("content") or ""
    ).strip()


def _validate_ws_client_message(
    payload: dict,
    *,
    initial: bool = False,
) -> tuple[bool, Optional[str], Optional[str]]:
    msg_type = _normalize_message_type(payload.get("type"))

    if initial and msg_type not in (None, "message", "chat_message", "agentic_chat"):
        return (
            False,
            "Initial message type must be 'chat_message', 'agentic_chat', or 'message'",
            "INVALID_INITIAL_MESSAGE_TYPE",
        )

    if msg_type in (None, "message", "chat_message", "agentic_chat"):
        if not _extract_user_prompt(payload):
            return False, "message field is required", "INVALID_CHAT_MESSAGE"
        return True, None, None

    if msg_type == "tool_result":
        call_id = payload.get("call_id") or payload.get("tool_call_id")
        if not call_id:
            return False, "tool_result requires call_id", "INVALID_TOOL_RESULT"
        if "result" not in payload:
            return (
                False,
                "tool_result requires result payload",
                "INVALID_TOOL_RESULT",
            )
        return True, None, None

    if msg_type == "execute_tool":
        call_id = payload.get("call_id")
        tool_name = _normalize_tool_name(payload.get("tool"))
        if not call_id or not tool_name:
            return (
                False,
                "execute_tool requires call_id and tool",
                "INVALID_EXECUTE_TOOL",
            )
        if tool_name not in AGENT_TOOLS:
            return False, f"Unsupported tool: {tool_name}", "UNKNOWN_TOOL"
        tool_args = payload.get("args", {})
        if tool_args is not None and not isinstance(tool_args, dict):
            return (
                False,
                "execute_tool args must be a JSON object",
                "INVALID_TOOL_ARGS",
            )
        return True, None, None

    if msg_type in {"cancel", "ping", "close", "steer"}:
        return True, None, None

    return False, f"Unknown message type: {msg_type}", "UNKNOWN_MESSAGE_TYPE"


async def _send_ws_error_event(
    websocket: WebSocket,
    message: str,
    *,
    request_id: Optional[str] = None,
    code: str = "BAD_REQUEST",
):
    event = {
        "type": "error",
        "message": message,
        "code": code,
    }
    if request_id:
        event["request_id"] = request_id
    await websocket.send_json(event)


def _cleanup_stale_id_map(store: dict[str, float], now: float, ttl_seconds: int):
    stale = [key for key, ts in store.items() if now - ts > ttl_seconds]
    for key in stale:
        store.pop(key, None)


def _enforce_rate_limit(window_timestamps: list[float], now: float):
    window_timestamps[:] = [
        ts for ts in window_timestamps if now - ts <= RATE_LIMIT_WINDOW_SECONDS
    ]
    if len(window_timestamps) >= RATE_LIMIT_MESSAGES_PER_WINDOW:
        raise ValueError("Rate limit exceeded for websocket message flow")
    window_timestamps.append(now)


async def _generate_with_cancel_wait(
    websocket: WebSocket,
    request_id: str,
    generation_coro,
    buffered_client_messages: list[dict],
):
    """Run generation while listening for cancel/steer without timeout polling."""
    generation_task = asyncio.create_task(generation_coro)
    receive_task = asyncio.create_task(websocket.receive_json())

    async def _cleanup_receive_task() -> None:
        if receive_task.done():
            return
        receive_task.cancel()
        try:
            await receive_task
        except (asyncio.CancelledError, WebSocketDisconnect, RuntimeError):
            pass
        except Exception:
            logger.debug("Ignored receive task cleanup error", exc_info=True)

    async def _cancel_generation_task() -> None:
        if generation_task.done():
            return
        generation_task.cancel()
        try:
            await generation_task
        except asyncio.CancelledError:
            pass

    try:
        while True:
            done, _pending = await asyncio.wait(
                {generation_task, receive_task},
                return_when=asyncio.FIRST_COMPLETED,
            )

            if generation_task in done:
                await _cleanup_receive_task()
                return await generation_task, False

            try:
                incoming = await receive_task
            except WebSocketDisconnect:
                await _cancel_generation_task()
                raise
            except asyncio.CancelledError:
                await _cancel_generation_task()
                raise
            except Exception:
                logger.debug(
                    "Ignored websocket message while generation was active",
                    exc_info=True,
                )
                incoming = None

            receive_task = asyncio.create_task(websocket.receive_json())

            if not isinstance(incoming, dict):
                continue

            msg_type = _normalize_message_type(incoming.get("type"))
            if msg_type == "cancel":
                await _cancel_generation_task()
                await _cleanup_receive_task()
                await websocket.send_json(
                    {
                        "type": "complete",
                        "request_id": request_id,
                        "status": "cancelled",
                    }
                )
                return None, True

            if msg_type == "steer":
                steer_text = incoming.get("message", incoming.get("content", ""))
                if steer_text:
                    buffered_client_messages.append(
                        {
                            "type": "message",
                            "role": "user",
                            "content": f"[STEERING] {steer_text}",
                            "_is_steering": True,
                        }
                    )
                    await websocket.send_json(
                        {
                            "type": "steering_ack",
                            "request_id": request_id,
                            "message": f"Steering received: {steer_text[:100]}",
                            "status": "queued",
                        }
                    )
                continue

            buffered_client_messages.append(incoming)
    finally:
        if not generation_task.done():
            generation_task.cancel()
        await _cleanup_receive_task()


async def _generate_with_cancel_support(
    websocket: WebSocket,
    request_id: str,
    generation_coro,
    buffered_client_messages: list[dict],
):
    return await _generate_with_cancel_wait(
        websocket,
        request_id,
        generation_coro,
        buffered_client_messages,
    )

    generation_task = asyncio.create_task(generation_coro)

    while not generation_task.done():
        try:
            incoming = await asyncio.wait_for(websocket.receive_json(), timeout=0.1)
        except asyncio.TimeoutError:
            continue
        except WebSocketDisconnect:
            generation_task.cancel()
            raise
        except Exception:
            continue

        if not isinstance(incoming, dict):
            continue

        msg_type = _normalize_message_type(incoming.get("type"))
        if msg_type == "cancel":
            generation_task.cancel()
            try:
                await generation_task
            except asyncio.CancelledError:
                pass
            await websocket.send_json(
                {
                    "type": "complete",
                    "request_id": request_id,
                    "status": "cancelled",
                }
            )
            return None, True

        # ── Mid-Run Steering ──────────────────────────────────────
        # User can send steering messages during generation:
        #   {"type": "steer", "message": "focus on formal only"}
        #   {"type": "steer", "message": "skip simulation"}
        #   {"type": "steer", "message": "add constraint: no X values"}
        # These are buffered and injected as user messages into the
        # next agent turn, so the LLM sees them and can adjust.
        if msg_type == "steer":
            steer_text = incoming.get("message", incoming.get("content", ""))
            if steer_text:
                # Buffer as a user message for the agent to see
                buffered_client_messages.append({
                    "type": "message",
                    "role": "user",
                    "content": f"[STEERING] {steer_text}",
                    "_is_steering": True,
                })
                # Acknowledge the steering
                await websocket.send_json({
                    "type": "steering_ack",
                    "request_id": request_id,
                    "message": f"Steering received: {steer_text[:100]}",
                    "status": "queued",
                })
            continue

        buffered_client_messages.append(incoming)

    return await generation_task, False


def _resolve_workspace_model_alias(context: dict) -> Optional[str]:
    requested = str(
        context.get("preferred_model")
        or context.get("model")
        or context.get("model_alias")
        or ""
    ).strip()

    def _is_local_style_alias(name: str) -> bool:
        lowered = (name or "").strip().lower()
        if not lowered:
            return False
        return any(
            token in lowered for token in ("quinn", "glm-", "chipix-v0.1", "local")
        )

    provider = (
        os.getenv("CHIPVERIFY_LLM_PROVIDER", os.getenv("MODEL_PROVIDER", "gemini"))
        .strip()
        .lower()
    )

    def _is_provider_compatible(name: str) -> bool:
        lowered = (name or "").strip().lower()
        if not lowered:
            return False
        if provider == "gemini":
            return lowered.startswith("gemini")
        if provider in {"nim", "openai"}:
            return not lowered.startswith("gemini")
        if provider == "azure_openai":
            return "gemini" not in lowered
        return True

    # Explicit non-Quinn model from the UI wins.
    if (
        requested
        and not _is_local_style_alias(requested)
        and _is_provider_compatible(requested)
    ):
        return requested

    env_override = os.getenv("CHIPVERIFY_WORKSPACE_MODEL_ALIAS", "").strip()
    if (
        env_override
        and not _is_local_style_alias(env_override)
        and _is_provider_compatible(env_override)
    ):
        return env_override

    return None


def _iter_text_chunks(text: str, chunk_size: int = CHUNK_SIZE):
    for i in range(0, len(text), chunk_size):
        yield text[i : i + chunk_size]


async def _stream_text(
    websocket: WebSocket,
    text: str,
    request_id: Optional[str] = None,
):
    for chunk in _iter_text_chunks(text):
        event = {"type": "text_delta", "content": chunk}
        if request_id:
            event["request_id"] = request_id
            event["type"] = "assistant_delta"
        await websocket.send_json(event)
        if STREAM_CHUNK_DELAY_SECONDS:
            await asyncio.sleep(STREAM_CHUNK_DELAY_SECONDS)


def _parse_and_stream_response(text: str):
    block_counter = 0
    last_end = 0

    matches: list[tuple[int, int, str, dict]] = []

    for m in _CODE_BLOCK_RE.finditer(text):
        block_counter += 1
        matches.append(
            (
                m.start(),
                m.end(),
                "code",
                {
                    "block_id": f"cb_{block_counter}",
                    "lang": m.group(1),
                    "code": m.group(2),
                },
            )
        )

    for m in _TOOL_CALL_RE.finditer(text):
        tool_name = _normalize_tool_name(m.group(1))
        try:
            args = json.loads(m.group(2))
        except json.JSONDecodeError:
            continue
        matches.append(
            (
                m.start(),
                m.end(),
                "tool",
                {
                    "call_id": f"tc_{uuid.uuid4().hex[:10]}",
                    "tool": tool_name,
                    "args": args,
                },
            )
        )

    if block_counter == 0:
        for m in _MARKDOWN_CODE_BLOCK_RE.finditer(text):
            lang = (m.group(1) or "text").strip().lower() or "text"
            code_body = m.group(2)
            # Avoid treating JSON tool-call payloads as code blocks.
            if lang == "json" and '"tool"' in code_body and '"args"' in code_body:
                continue
            block_counter += 1
            matches.append(
                (
                    m.start(),
                    m.end(),
                    "code",
                    {
                        "block_id": f"cb_{block_counter}",
                        "lang": lang,
                        "code": code_body,
                    },
                )
            )

    # Fallback parsing for responses that don't wrap JSON in fenced blocks.
    if not any(m[2] == "tool" for m in matches):
        for start, end, tool_name, args in _iter_json_tool_call_objects(text):
            matches.append(
                (
                    start,
                    end,
                    "tool",
                    {
                        "call_id": f"tc_{uuid.uuid4().hex[:10]}",
                        "tool": tool_name,
                        "args": args,
                    },
                )
            )
        if not any(m[2] == "tool" for m in matches):
            inline = _TOOL_CALL_INLINE_RE.search(text)
            if inline:
                tool_name = _normalize_tool_name(inline.group(1))
                try:
                    args = json.loads(inline.group(2))
                except json.JSONDecodeError:
                    args = None
                if args is None:
                    inline = None
            if inline:
                matches.append(
                    (
                        inline.start(),
                        inline.end(),
                        "tool",
                        {
                            "call_id": f"tc_{uuid.uuid4().hex[:10]}",
                            "tool": tool_name,
                            "args": args,
                        },
                    )
                )

    matches.sort(key=lambda x: x[0])

    for start, end, kind, payload in matches:
        if start > last_end:
            yield ("text", text[last_end:start])
        if kind == "code":
            yield ("code_block", payload)
        else:
            yield ("tool_call", payload)
        last_end = end

    if last_end < len(text):
        yield ("text", text[last_end:])


def _initialize_internal_task_state(user_message: str) -> dict:
    lowered = (user_message or "").lower()
    tasks = []

    wants_multi_file = any(
        token in lowered
        for token in ["all files", "each file", "every file", "read files", "read all"]
    )
    wants_multi_step = any(
        token in lowered
        for token in [
            "step by step",
            "then",
            "after that",
            "continue",
            "next",
            "multi-step",
            "iterate",
        ]
    )

    if not wants_multi_file and not wants_multi_step:
        # Keep legacy/simple chat single-pass; agentic_chat handles true orchestration.
        return {
            "active": False,
            "current_index": 0,
            "tasks": [],
            "max_iterations": 1,
        }

    if wants_multi_file:
        tasks = [
            {
                "id": "discover_files",
                "title": "Discover relevant files",
                "status": "pending",
            },
            {"id": "read_files", "title": "Read relevant files", "status": "pending"},
            {"id": "analyze", "title": "Analyze contents", "status": "pending"},
            {
                "id": "summarize",
                "title": "Provide final explanation",
                "status": "pending",
            },
        ]
    else:
        tasks = [
            {
                "id": "understand",
                "title": "Understand user request",
                "status": "pending",
            },
            {"id": "gather", "title": "Gather required context", "status": "pending"},
            {"id": "respond", "title": "Provide response", "status": "pending"},
        ]

    return {
        "active": True,
        "current_index": 0,
        "tasks": tasks,
        "max_iterations": 8,
    }


def _task_state_has_pending(task_state: Optional[dict]) -> bool:
    if not isinstance(task_state, dict):
        return False
    tasks = task_state.get("tasks") or []
    return any(str(task.get("status") or "") != "completed" for task in tasks)


def _advance_task_state(task_state: Optional[dict]) -> None:
    if not isinstance(task_state, dict):
        return
    tasks = task_state.get("tasks") or []
    for idx, task in enumerate(tasks):
        if str(task.get("status") or "") != "completed":
            task["status"] = "completed"
            task_state["current_index"] = idx + 1
            return


def _extract_end_conversation_summary(text: str) -> tuple[Optional[str], str]:
    if not text:
        return None, text

    match = _END_CALL_RE.search(text)
    if not match:
        return None, text

    payload_raw = match.group(1)
    summary = None
    try:
        payload = json.loads(payload_raw)
        if isinstance(payload, dict):
            summary = str(payload.get("summary") or "").strip() or None
    except Exception:
        summary = None

    cleaned = (text[: match.start()] + text[match.end() :]).strip()
    return summary, cleaned


def _is_placeholder_completion_text(text: Optional[str]) -> bool:
    normalized = str(text or "").strip().lower()
    placeholders = {
        "tool execution completed.",
        "tool execution completed",
        "continue with the task using available context and tool results.",
    }
    return normalized in placeholders


async def _generate_agent_response_with_history_recovery(
    conversation_history: list[dict],
    context: dict,
) -> str:
    response = await _generate_agent_response_with_history(
        conversation_history, context
    )
    if not _is_placeholder_completion_text(response):
        return response

    recovery_history = list(conversation_history)
    recovery_history.append(
        {
            "role": "system",
            "content": (
                "Previous response was non-substantive. Continue the task. "
                "If more file/tool work is required, emit the next tool call JSON. "
                "Only provide final answer when all requested steps are complete."
            ),
        }
    )
    second_response = await _generate_agent_response_with_history(
        recovery_history,
        context,
    )
    return (
        response
        if _is_placeholder_completion_text(second_response)
        else second_response
    )


async def _generate_agent_response_with_internal_loop(
    conversation_history: list[dict],
    context: dict,
) -> str:
    conversation_history = _trim_conversation_history(conversation_history)
    task_state = context.get("_internal_task_state")
    if not isinstance(task_state, dict) or not task_state.get("active"):
        return await _generate_agent_response_with_history_recovery(
            conversation_history,
            context,
        )

    max_iterations = 1
    if isinstance(task_state, dict):
        max_iterations = max(1, int(task_state.get("max_iterations") or 8))

    working_history = _trim_conversation_history(conversation_history)
    last_response = ""

    for _ in range(max_iterations):
        response = await _generate_agent_response_with_history_recovery(
            working_history,
            context,
        )
        last_response = response

        end_summary, cleaned_response = _extract_end_conversation_summary(response)
        if end_summary:
            if isinstance(task_state, dict):
                for task in task_state.get("tasks") or []:
                    task["status"] = "completed"
            return cleaned_response or end_summary

        has_tool_call = any(
            event_type == "tool_call"
            for event_type, _ in _parse_and_stream_response(response)
        )
        if has_tool_call:
            return cleaned_response or response

        if not _task_state_has_pending(task_state):
            return cleaned_response or response

        _advance_task_state(task_state)
        working_history.append(
            {"role": "assistant", "content": cleaned_response or response}
        )
        working_history.append(
            {
                "role": "system",
                "content": (
                    "Continue with the next pending internal task. "
                    "If all tasks are complete, emit endConversation JSON function call."
                ),
            }
        )
        working_history = _trim_conversation_history(working_history)

    return last_response


async def _generate_agent_response_with_history(
    conversation_history: list[dict],
    context: dict,
) -> str:
    conversation_history = _trim_conversation_history(conversation_history)
    client = AIClient()
    requested_model = str(
        context.get("preferred_model")
        or context.get("model")
        or context.get("model_alias")
        or ""
    ).strip()
    model_alias = _resolve_workspace_model_alias(context)
    if model_alias:
        client.model_alias = model_alias

    logger.info(
        "Agent model resolved: requested=%s selected=%s",
        requested_model or "<none>",
        getattr(client, "model_alias", "<unknown>"),
    )

    last_user_message = ""
    for msg in reversed(conversation_history):
        if msg.get("role") == "user":
            last_user_message = str(msg.get("content") or "")
            break
    simple_query = _is_general_short_query(last_user_message)

    has_file_context = bool(
        context.get("active_file_content")
        or context.get("active_file_name")
        or (
            isinstance(context.get("attached_file_contents"), list)
            and len(context.get("attached_file_contents") or []) > 0
        )
    )
    should_include_context = (not simple_query) or has_file_context

    context_parts = []
    if should_include_context:
        project_id = context.get("project_id")
        if project_id:
            context_parts.append(f"Project ID: {project_id}")
        thread_id = context.get("thread_id")
        if thread_id:
            context_parts.append(f"Thread ID: {thread_id}")
        workspace_files = context.get("workspace_files")
        if workspace_files:
            if isinstance(workspace_files, list):
                limited_files = workspace_files[:15]
                context_parts.append(
                    "Workspace files:\n" + "\n".join(f"  - {f}" for f in limited_files)
                )
            elif isinstance(workspace_files, str):
                context_parts.append(f"Workspace files:\n{workspace_files[:2000]}")
        active_file_name = context.get("active_file_name")
        if active_file_name:
            context_parts.append(f"Active file: {str(active_file_name)[:400]}")
        active_file_content = context.get("active_file_content")
        if active_file_content:
            context_parts.append(
                "Active file content:\n" + str(active_file_content)[:12000]
            )
        attached_file_contents = context.get("attached_file_contents")
        if isinstance(attached_file_contents, list) and attached_file_contents:
            snippets = []
            for item in attached_file_contents[:4]:
                if not isinstance(item, dict):
                    continue
                filename = str(item.get("filename") or item.get("id") or "attached")[
                    :200
                ]
                content = str(item.get("content") or "")[:4000]
                if content:
                    snippets.append(f"[{filename}]\n{content}")
            if snippets:
                context_parts.append(
                    "Attached file excerpts:\n\n" + "\n\n".join(snippets)
                )
        task_state = context.get("_internal_task_state")
        if isinstance(task_state, dict):
            tasks = task_state.get("tasks") or []
            if tasks:
                task_lines = []
                for idx, task in enumerate(tasks, start=1):
                    status = str(task.get("status") or "pending")
                    title = str(task.get("title") or task.get("id") or f"task_{idx}")
                    task_lines.append(f"  {idx}. [{status}] {title}")
                context_parts.append("Internal task list:\n" + "\n".join(task_lines))
                context_parts.append(
                    "If all tasks are complete, emit this JSON in a fenced block to end:\n"
                    '```json\n{"function":"endConversation","summary":"<final summary>"}\n```'
                )

    context_block = (
        "\n".join(context_parts) if context_parts else "No additional context provided."
    )

    messages = list(conversation_history)
    if messages and messages[0].get("role") == "system":
        original_system = messages[0]["content"]
        if not should_include_context:
            messages[0] = {
                "role": "system",
                "content": original_system,
            }
        else:
            messages[0] = {
                "role": "system",
                "content": f"{original_system}\n\nAdditional Context:\n{context_block}",
            }

    temperature = 0.2 if simple_query and not has_file_context else 0.4

    return await client.generate_with_messages(
        messages,
        temperature=temperature,
    )


async def _generate_agent_response(
    user_message: str,
    context: dict,
) -> str:
    client = AIClient()
    model_alias = _resolve_workspace_model_alias(context)
    if model_alias:
        client.model_alias = model_alias

    simple_query = _is_general_short_query(user_message)
    has_file_context = bool(
        context.get("active_file_content")
        or context.get("active_file_name")
        or (
            isinstance(context.get("attached_file_contents"), list)
            and len(context.get("attached_file_contents") or []) > 0
        )
    )
    should_include_context = (not simple_query) or has_file_context

    context_parts = []
    if should_include_context:
        project_id = context.get("project_id")
        if project_id:
            context_parts.append(f"Project ID: {project_id}")
        thread_id = context.get("thread_id")
        if thread_id:
            context_parts.append(f"Thread ID: {thread_id}")
        workspace_files = context.get("workspace_files")
        if workspace_files:
            if isinstance(workspace_files, list):
                limited_files = workspace_files[:15]
                context_parts.append(
                    "Workspace files:\n" + "\n".join(f"  - {f}" for f in limited_files)
                )
            elif isinstance(workspace_files, str):
                context_parts.append(f"Workspace files:\n{workspace_files[:2000]}")
        active_file_name = context.get("active_file_name")
        if active_file_name:
            context_parts.append(f"Active file: {str(active_file_name)[:400]}")
        active_file_content = context.get("active_file_content")
        if active_file_content:
            context_parts.append(
                "Active file content:\n" + str(active_file_content)[:12000]
            )
        attached_file_contents = context.get("attached_file_contents")
        if isinstance(attached_file_contents, list) and attached_file_contents:
            snippets = []
            for item in attached_file_contents[:4]:
                if not isinstance(item, dict):
                    continue
                filename = str(item.get("filename") or item.get("id") or "attached")[
                    :200
                ]
                content = str(item.get("content") or "")[:4000]
                if content:
                    snippets.append(f"[{filename}]\n{content}")
            if snippets:
                context_parts.append(
                    "Attached file excerpts:\n\n" + "\n\n".join(snippets)
                )
        task_state = context.get("_internal_task_state")
        if isinstance(task_state, dict):
            tasks = task_state.get("tasks") or []
            if tasks:
                task_lines = []
                for idx, task in enumerate(tasks, start=1):
                    status = str(task.get("status") or "pending")
                    title = str(task.get("title") or task.get("id") or f"task_{idx}")
                    task_lines.append(f"  {idx}. [{status}] {title}")
                context_parts.append("Internal task list:\n" + "\n".join(task_lines))
                context_parts.append(
                    "If all tasks are complete, emit this JSON in a fenced block to end:\n"
                    '```json\n{"function":"endConversation","summary":"<final summary>"}\n```'
                )

    context_block = (
        "\n".join(context_parts) if context_parts else "No additional context provided."
    )

    user_prompt = f"Context:\n{context_block}\n\nUser message:\n{user_message}"

    mode = str(context.get("mode") or "").strip().lower() or None
    if _is_mental_model_chat(context):
        system_prompt = SYSTEM_PROMPT_MENTAL_MODEL_CHAT
    else:
        recent_messages = []
        workspace_files = context.get("workspace_files")
        if isinstance(workspace_files, list):
            recent_messages.extend(str(item) for item in workspace_files[:8])
        system_prompt = _select_system_prompt(user_message, recent_messages, mode=mode)
    temperature = 0.2 if simple_query and not has_file_context else 0.4

    return await client.generate(
        user_prompt,
        system_prompt=system_prompt,
        temperature=temperature,
    )


def _fetch_run_digest_text(db: Optional[Session], run_id: Optional[Any]) -> Optional[str]:
    """Load persisted verification_summary for the active run id (pipeline runs)."""
    if db is None or not run_id:
        return None
    rid = str(run_id).strip()
    if len(rid) < 8:
        return None
    # Synthetic staged timeline ids are not DB run rows.
    if rid.startswith("staged-"):
        return None
    try:
        row = db.query(Run).filter(Run.id == rid).first()
        if not row:
            return None
        summary = row.verification_summary or ""
        return (
            f"database_run_id={row.id}\n"
            f"database_run_status={row.status}\n"
            f"verification_summary:\n{str(summary)[:10000]}"
        )
    except Exception:
        logger.debug("run digest lookup failed", exc_info=True)
        return None


def _merge_attachment_context_into_user_turn(
    user_message: str,
    context: dict,
    *,
    db: Optional[Session] = None,
) -> str:
    """Prepend structured workspace hints so AgenticLoopEngine sees rich session context.

    The frontend sends `workspace_files` / `attached_file_contents` on the WS
    payload; the RTL agentic engine previously forwarded only the bare user
    string to the model. Merge here so excerpts stay out of persisted chat rows.
    """
    ctx = context if isinstance(context, dict) else {}
    blocks: list[str] = []
    cadence_mode = bool(ctx.get("cadence_verification_mode"))
    has_staged_brief = isinstance(ctx.get("staged_execution_brief"), str) and bool(
        ctx.get("staged_execution_brief", "").strip()
    )

    if cadence_mode and ctx.get("project_id"):
        blocks.append(
            f"Active project_id (server-bound): {str(ctx.get('project_id'))[:140]}"
        )

    if not cadence_mode:
        snap = ctx.get("verification_turn_snapshot_json")
        if isinstance(snap, str) and snap.strip():
            blocks.append(
                "### Verification context (from Chipix UI — treat as ground truth)\n"
                + snap.strip()[:16000]
            )
    elif not has_staged_brief:
        snap = ctx.get("verification_turn_snapshot_json")
        if isinstance(snap, str) and snap.strip():
            blocks.append(
                "### Verification context (from Chipix UI — treat as ground truth)\n"
                + snap.strip()[:6000]
            )

    # Cadence fields live in the custom system prompt — avoid duplicating brief/IDs here.
    if cadence_mode and not has_staged_brief:
        cadence_block = ["### Cadence verification context"]
        artifact_ids = ctx.get("generated_artifact_ids")
        if isinstance(artifact_ids, list) and artifact_ids:
            cadence_block.append(
                "generated_artifact_ids:\n"
                + "\n".join(f"- {str(aid)}" for aid in artifact_ids[:40])
            )
        for key in ("uvm_top_module", "uvm_testname", "cadence_simulation_status", "selected_module"):
            val = ctx.get(key)
            if val:
                cadence_block.append(f"{key}: {val}")
        if len(cadence_block) > 1:
            blocks.append("\n".join(cadence_block))

    if not cadence_mode:
        db_digest = _fetch_run_digest_text(db, ctx.get("last_run_id"))
        if db_digest:
            blocks.append(
                "### Persisted verification record (database)\n" + db_digest.strip()[:12000]
            )

        lr_id = ctx.get("last_run_id")
        if lr_id:
            lr_stat = ctx.get("last_run_status")
            blocks.append(
                "Session hints:\n"
                f"- last_run_id: {str(lr_id)[:140]}\n"
                f"- last_run_status: {str(lr_stat or '')[:120]}"
            )

    if not cadence_mode:
        manifest_summary = ctx.get("workspace_manifest_summary")
        if isinstance(manifest_summary, str) and manifest_summary.strip():
            blocks.append(manifest_summary.strip()[:12000])
        else:
            manifest = ctx.get("project_artifact_manifest")
            if isinstance(manifest, dict) and manifest.get("artifacts"):
                from services.workspace_context import format_manifest_for_prompt

                blocks.append(format_manifest_for_prompt(manifest)[:12000])

    workspace_mode = ctx.get("workspace_mode")
    if workspace_mode:
        blocks.append(f"Workspace mode hint: {str(workspace_mode)[:80]}")

    workspace_files = ctx.get("workspace_files")
    if isinstance(workspace_files, list) and workspace_files:
        limited = [str(f)[:400] for f in workspace_files[:15]]
        blocks.append(
            "Workspace files referenced by the user:\n"
            + "\n".join(f"  - {f}" for f in limited)
        )
    elif isinstance(workspace_files, str) and workspace_files.strip():
        blocks.append(
            "Workspace files referenced by the user:\n"
            + workspace_files.strip()[:2000]
        )

    attached = ctx.get("attached_file_contents")
    if isinstance(attached, list) and attached:
        snippets: list[str] = []
        attach_cap = 6_000 if cadence_mode else 12_000
        max_files = 2 if cadence_mode else 4
        for item in attached[:max_files]:
            if not isinstance(item, dict):
                continue
            filename = str(item.get("filename") or item.get("id") or "attached")[
                :200
            ]
            content = str(item.get("content") or "")[:attach_cap]
            if content.strip():
                snippets.append(f"[{filename}]\n{content}")
        if snippets:
            blocks.append(
                "Attached file excerpts for this turn:\n\n" + "\n\n".join(snippets)
            )

    active_hint = ctx.get("active_file_name") or ctx.get("active_file_path")
    if active_hint and str(active_hint).strip():
        blocks.append(f"Active editor file (hint): {str(active_hint)[:400]}")
    active_file_content = ctx.get("active_file_content")
    if active_file_content and str(active_file_content).strip():
        blocks.append(
            "Active editor excerpt:\n" + str(active_file_content)[:12000]
        )

    source_thread_id = ctx.get("source_thread_id") or ctx.get("main_thread_id")
    source_thread_title = ctx.get("source_thread_title") or ctx.get("thread_title")
    if source_thread_title and str(source_thread_title).strip():
        tid = f" ({str(source_thread_id)[:80]})" if source_thread_id else ""
        blocks.append(
            "Main workspace conversation (read-only context"
            f"{tid}): {str(source_thread_title).strip()[:300]}"
        )
    elif source_thread_id:
        blocks.append(
            f"Main workspace conversation id (read-only context): {str(source_thread_id)[:140]}"
        )

    recap = ctx.get("thread_recap_json")
    if isinstance(recap, str) and recap.strip():
        recap_cap = 4_000 if cadence_mode else 10_000
        blocks.append(
            "### Prior conversation (memory only — do not repeat to the user)\n"
            "Below is a compact recap of earlier messages in this thread. Treat it as "
            "background you already remember from this session. Focus on the user's new "
            "message at the end; only reference prior turns when it directly helps answer "
            "that message. Do not dump recap, file excerpts, or JSON back in your reply.\n\n"
            + recap.strip()[:recap_cap]
        )

    hdl_lang = str(
        ctx.get("hdl_language") or ctx.get("code_style") or ctx.get("language") or ""
    ).strip().lower()
    if hdl_lang in ("verilog", "systemverilog"):
        ext = ".v" if hdl_lang == "verilog" else ".sv"
        blocks.append(
            "RTL language preference (user-selected for this design): "
            f"{hdl_lang}. Use {ext} file extensions, "
            + (
                "Verilog-2001 style (wire/reg, no SystemVerilog classes/interfaces/packages unless required)."
                if hdl_lang == "verilog"
                else "SystemVerilog (logic, interfaces/packages allowed when appropriate)."
            )
        )

    surface = str(ctx.get("conversation_surface") or "").lower()
    if surface == "ide" or ctx.get("ide_assistant"):
        blocks.append(
            "IDE assistant mode: answer questions about the open file and apply edits "
            "with file tools (createFile, applyCodeToFile, replaceFile) when the user "
            "asks for changes. Prefer minimal, targeted patches to the active editor file. "
            "Prior thread and file context are for your reasoning only — keep replies concise "
            "and do not paste full prior messages or large code blocks unless requested."
        )

    if not blocks:
        return user_message
    return (
        "\n\n".join(blocks)
        + "\n\n---\nUser message:\n"
        + (user_message or "").strip()
    )


async def _run_agentic_loop_v2(
    websocket: WebSocket,
    db: Session,
    thread_id: str,
    request_id: str,
    user_message: str,
    context: dict,
    user_id: Optional[str],
) -> str:
    assistant_chunks: list[str] = []
    final_summary = ""

    async def _send_event(event: dict):
        nonlocal final_summary
        payload = dict(event or {})
        payload.setdefault("request_id", request_id)
        payload.setdefault("thread_id", thread_id)

        event_type = payload.get("type")
        if event_type == "assistant_delta":
            chunk = payload.get("content")
            if chunk is None:
                chunk = payload.get("text")
            if chunk:
                assistant_chunks.append(str(chunk))
        elif event_type == "summary":
            final_summary = str(payload.get("content") or "")
        elif event_type == "conversation_ended" and not final_summary:
            final_summary = str(payload.get("summary") or "")
        elif event_type == "done":
            # Inject full accumulated text so frontend has a reliable fallback
            # even if some assistant_delta events were dropped
            full_text = "".join(assistant_chunks).strip()
            if full_text and "content" not in payload:
                payload["content"] = full_text

        await websocket.send_json(payload)

    requested_tool_execution = (
        str(context.get("tool_execution") or context.get("toolExecution") or "parallel")
        .strip()
        .lower()
    )
    tool_execution_mode = (
        "sequential" if requested_tool_execution == "sequential" else "parallel"
    )

    # RTL designer: always sequential (one file at a time) + specialised prompt
    mode = str(context.get("mode") or "").strip().lower()
    is_rtl_designer = mode == "rtl_designer"
    is_mental_model_chat = _is_mental_model_chat(context)
    is_cadence_mode = _is_cadence_verification_mode(context)
    if is_rtl_designer:
        tool_execution_mode = "sequential"

    if is_cadence_mode:
        custom_prompt = _build_cadence_verification_prompt(context)
    elif is_mental_model_chat:
        custom_prompt = _build_mental_model_system_prompt(db, context)
    elif is_rtl_designer:
        custom_prompt = SYSTEM_PROMPT_RTL_DESIGNER
    else:
        custom_prompt = None

    if not context.get("ai_client"):
        try:
            from services.mental_model.store import get_default_ai_client_adapter

            ai_client, ai_metadata = await get_default_ai_client_adapter(
                require_llm=False,
            )
            if ai_client:
                context["ai_client"] = ai_client
                context["ai_client_metadata"] = ai_metadata
        except Exception as exc:
            logger.warning("Could not attach backend AI client to agent tools: %s", exc)

    # Build conversation history from the thread's saved messages so the engine
    # has context about previous turns (e.g. the plan, clarifying Q&A).
    # This is the critical fix: without history the model forgets what it planned.
    prior_history: list[dict] = []
    history_limit = 12 if is_cadence_mode else 40
    history_char_cap = 4_000 if is_cadence_mode else 12_000
    try:
        from database.models import ChatMessage as DBChatMessage
        from services.context_budget import trim_conversation_history

        recent_msgs = (
            db.query(DBChatMessage)
            .filter(DBChatMessage.thread_id == thread_id)
            .order_by(DBChatMessage.created_at.asc())
            .limit(history_limit)
            .all()
        )
        raw_history: list[dict] = []
        for m in recent_msgs:
            if m.role in ("user", "assistant") and m.content:
                raw_history.append({"role": m.role, "content": m.content})
        prior_history = trim_conversation_history(
            raw_history,
            max_messages=history_limit,
            max_chars_per_message=history_char_cap,
        )
    except Exception as e:
        logger.warning("Failed to load thread history: %s", e)

    if current_user is not None:
        context["current_user"] = current_user

    engine = AgenticLoopEngine(
        db_session=db,
        thread_id=thread_id,
        websocket_send_fn=_send_event,
        tool_execution=tool_execution_mode,
        context=context,
        custom_system_prompt=custom_prompt,
    )
    merged_user_message = _merge_attachment_context_into_user_turn(
        user_message, context, db=db
    )
    cadence_unsub = None
    _pid = str(context.get("project_id") or "").strip()
    if _pid:

        async def _forward_cadence_during_run(event: dict) -> None:
            try:
                await websocket.send_json({**event, "request_id": request_id, "thread_id": thread_id})
            except Exception:
                pass

        from services.cadence_run.events import subscribe_project as subscribe_cadence

        cadence_unsub = subscribe_cadence(_pid, _forward_cadence_during_run)
    try:
        await engine.run(merged_user_message, conversation_history=prior_history)
    finally:
        if cadence_unsub:
            cadence_unsub()

    assistant_text = "".join(assistant_chunks).strip()
    if not assistant_text and final_summary:
        assistant_text = final_summary

    if assistant_text:
        user_message_id, assistant_message_id = _save_chat_messages(
            db,
            thread_id,
            user_message,
            assistant_text,
            {
                "project_id": context.get("project_id"),
                "user_id": user_id,
            },
        )
        try:
            usage_payload = engine.token_usage
            if getattr(usage_payload, "total_tokens", 0) <= 0:
                usage_payload = estimate_text_token_usage(
                    input_text=merged_user_message,
                    output_text=assistant_text,
                    provider="agent_ws",
                    model=None,
                    details={"source": "agent_ws_final_estimate"},
                )
            usage_row = persist_chat_token_usage(
                db,
                thread_id=thread_id,
                user_id=user_id,
                usage=usage_payload,
                user_message_id=user_message_id,
                assistant_message_id=assistant_message_id,
                source="agent_ws",
                metadata={
                    "mode": mode,
                    "tool_execution_mode": tool_execution_mode,
                },
            )
            db.commit()
            if usage_row:
                db.refresh(usage_row)
                await websocket.send_json(
                    {
                        "type": "token_usage_updated",
                        "thread_id": thread_id,
                        "project_id": context.get("project_id"),
                        "usage": serialize_token_usage(usage_row),
                    }
                )
        except Exception:
            db.rollback()
            logger.warning("Failed to persist agent token usage", exc_info=True)

    return assistant_text


@router.websocket("/ws/agent/{thread_id}/chat")
async def agent_chat_ws_endpoint(websocket: WebSocket, thread_id: str):
    token = (websocket.query_params.get("token") or "").strip()
    logger.info(
        "Agent WS connect attempt",
        extra={"has_token": bool(token), "thread_id": thread_id},
    )
    await websocket.accept()
    logger.info("Agent WS accepted: thread_id=%s", thread_id)

    db: Session = SessionLocal()
    graph_event_unsub = None
    cadence_event_unsub = None
    try:
        # --- Authenticate ---
        if token:
            try:
                current_user = _authenticate_user_from_token(token, db)
            except HTTPException:
                if _dev_auth_allowed():
                    current_user = _get_or_create_local_dev_user(db)
                else:
                    await _send_ws_error_and_close(
                        websocket,
                        "Authentication required",
                        close_code=4401,
                    )
                    return
        elif _dev_auth_allowed():
            current_user = _get_or_create_local_dev_user(db)
        else:
            await _send_ws_error_and_close(
                websocket,
                "Authentication required",
                close_code=4401,
            )
            return

        # --- Receive initial message with context ---
        try:
            incoming = await websocket.receive_json()
        except Exception:
            await _send_ws_error_and_close(websocket, "Failed to read initial message")
            return

        logger.info("Agent WS received initial message: keys=%s", list(incoming.keys()))

        if not isinstance(incoming, dict):
            await _send_ws_error_and_close(
                websocket, "Initial message must be a JSON object"
            )
            return

        # --- Validate required fields ---
        request_id = _extract_request_id(incoming)
        initial_type = _normalize_message_type(incoming.get("type"))
        if initial_type not in (None, "message", "chat_message", "agentic_chat"):
            await _send_ws_error_and_close(
                websocket,
                "Initial message type must be 'chat_message', 'agentic_chat', or 'message'",
            )
            return

        is_valid_initial, validation_message, validation_code = (
            _validate_ws_client_message(incoming, initial=True)
        )
        if not is_valid_initial:
            await _send_ws_error_and_close(
                websocket,
                validation_message or "Invalid initial websocket message",
            )
            return

        client_version = str(incoming.get("clientVersion") or "").strip()
        if client_version and client_version != CLIENT_PROTOCOL_VERSION:
            logger.info(
                "Agent WS client version mismatch: got=%s expected=%s",
                client_version,
                CLIENT_PROTOCOL_VERSION,
            )

        user_message = _extract_user_prompt(incoming)
        if not user_message:
            await _send_ws_error_and_close(websocket, "message field is required")
            return

        context = incoming.get("context") or {}
        context["thread_id"] = thread_id
        context["_internal_task_state"] = _initialize_internal_task_state(user_message)
        try:
            from services.workspace_context import enrich_project_context

            enrich_project_context(db, context, user_message=user_message)
        except Exception:
            logger.debug("Project context enrichment failed", exc_info=True)

        logger.info("Agent WS authenticated: user_id=%s", current_user.id)

        logger.info(
            "Agent WS connected: thread=%s user=%s msg=%s",
            thread_id,
            current_user.id,
            user_message[:80],
        )

        # --- Confirm connection ---
        await websocket.send_json(
            {
                "type": "connected",
                "thread_id": thread_id,
                "request_id": request_id,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        )
        logger.info("Agent WS sent connected confirmation")

        # --- Auto-generate thread title from first message ---
        thread = db.query(ChatThread).filter(ChatThread.id == thread_id).first()
        if thread and (thread.title or "") in ("Project Chat", "Agent Chat", ""):
            new_title = _generate_thread_title(user_message)
            thread.title = new_title
            thread.updated_at = datetime.now(timezone.utc)
            db.add(thread)
            db.commit()
            await websocket.send_json(
                {
                    "type": "thread_title_generated",
                    "title": new_title,
                }
            )

        if initial_type == "agentic_chat":
            try:
                await _ensure_project_task_for_agent_chat(
                    websocket,
                    db,
                    thread_id=thread_id,
                    user=current_user,
                    user_message=user_message,
                    context=context,
                )
            except Exception:
                logger.warning(
                    "Failed to ensure project task for thread=%s",
                    thread_id,
                    exc_info=True,
                )

        # --- Build conversation history with the new message ---
        conversation_history = _build_conversation_history(
            db, thread_id, user_message, context
        )

        logger.info(
            "Agent WS conversation history: %d messages for thread=%s",
            len(conversation_history),
            thread_id,
        )
        for idx, msg in enumerate(conversation_history):
            logger.info(
                "  [%d] role=%s content_len=%d",
                idx,
                msg.get("role"),
                len(str(msg.get("content", ""))),
            )

        buffered_client_messages: list[dict] = []

        # --- Generate and stream first response ---
        if initial_type == "agentic_chat":
            try:
                logger.info(
                    "Agent WS starting V2 agentic loop: thread=%s msg=%s",
                    thread_id,
                    user_message[:100],
                )
                response_text = await _run_agentic_loop_v2(
                    websocket,
                    db,
                    thread_id,
                    request_id,
                    user_message,
                    context,
                    current_user.id,
                )
            except Exception as exc:
                logger.exception(
                    "Agentic loop failed: thread=%s error=%s", thread_id, str(exc)
                )
                await websocket.send_json(
                    {
                        "type": "error",
                        "message": f"Agentic loop failed: {str(exc)}",
                        "request_id": request_id,
                        "code": "AGENTIC_LOOP_FAILED",
                    }
                )
                response_text = None
        else:
            try:
                logger.info(
                    "Agent WS starting LLM generation for message: %s",
                    user_message[:100],
                )
                response_text, was_cancelled = await _generate_with_cancel_support(
                    websocket,
                    request_id,
                    _generate_agent_response_with_internal_loop(
                        conversation_history,
                        context,
                    ),
                    buffered_client_messages,
                )
                if was_cancelled:
                    response_text = None
                logger.info(
                    "Agent WS LLM generation succeeded: response_len=%d",
                    len(response_text or ""),
                )
            except Exception as exc:
                logger.exception(
                    "LLM generation failed: thread=%s error=%s", thread_id, str(exc)
                )
                await websocket.send_json(
                    {
                        "type": "error",
                        "message": f"Generation failed: {str(exc)}",
                    }
                )
                response_text = None

            if response_text:
                for event_type, payload in _parse_and_stream_response(response_text):
                    logger.debug("Agent WS streaming: type=%s", event_type)
                    if event_type == "text":
                        await _stream_text(websocket, payload, request_id=request_id)
                    elif event_type == "code_block":
                        await websocket.send_json(
                            {
                                "type": "code_block",
                                "request_id": request_id,
                                **payload,
                            }
                        )
                        await asyncio.sleep(0.1)
                    elif event_type == "tool_call":
                        await websocket.send_json(
                            {
                                "type": "tool_call",
                                "request_id": request_id,
                                **payload,
                            }
                        )
                        await asyncio.sleep(0.1)

                await websocket.send_json({"type": "done", "request_id": request_id})

            # --- Save messages to database ---
            if response_text:
                _save_chat_messages(
                    db,
                    thread_id,
                    user_message,
                    response_text,
                    {
                        "project_id": context.get("project_id"),
                        "user_id": current_user.id,
                    },
                )

        # --- Track conversation for tool result continuation ---
        if response_text:
            conversation_history.append({"role": "assistant", "content": response_text})

        # --- Listen for tool results and additional messages from the client ---
        processed_request_ids: dict[str, float] = {}
        processed_tool_result_ids: dict[str, float] = {}
        processed_execute_tool_ids: dict[str, float] = {}
        message_window_timestamps: list[float] = []

        _ws_project_id = str(context.get("project_id") or "").strip()
        if _ws_project_id:

            async def _forward_codebase_graph_event(event: dict) -> None:
                try:
                    await websocket.send_json(event)
                except Exception:
                    pass

            async def _forward_cadence_run_event(event: dict) -> None:
                try:
                    await websocket.send_json(event)
                except Exception:
                    pass

            from services.codebase_graph.events import subscribe_project as subscribe_graph
            from services.cadence_run.events import subscribe_project as subscribe_cadence

            graph_event_unsub = subscribe_graph(_ws_project_id, _forward_codebase_graph_event)
            cadence_event_unsub = subscribe_cadence(_ws_project_id, _forward_cadence_run_event)

        while True:
            try:
                now = time.monotonic()
                _cleanup_stale_id_map(
                    processed_request_ids,
                    now,
                    REQUEST_TRACK_TTL_SECONDS,
                )
                _cleanup_stale_id_map(
                    processed_tool_result_ids,
                    now,
                    REQUEST_TRACK_TTL_SECONDS,
                )
                _cleanup_stale_id_map(
                    processed_execute_tool_ids,
                    now,
                    REQUEST_TRACK_TTL_SECONDS,
                )

                if buffered_client_messages:
                    client_msg = buffered_client_messages.pop(0)
                else:
                    try:
                        client_msg = await asyncio.wait_for(
                            websocket.receive_json(),
                            timeout=WS_MESSAGE_TIMEOUT_SECONDS,
                        )
                    except asyncio.TimeoutError:
                        await _send_ws_error_event(
                            websocket,
                            "No messages received within timeout window",
                            code="MESSAGE_TIMEOUT",
                        )
                        break
                    except WebSocketDisconnect:
                        break
                    except Exception:
                        break

                if not isinstance(client_msg, dict):
                    continue

                now_after_receive = time.monotonic()

                try:
                    _enforce_rate_limit(
                        message_window_timestamps,
                        now_after_receive,
                    )
                except ValueError as rate_err:
                    await _send_ws_error_event(
                        websocket,
                        str(rate_err),
                        code="RATE_LIMITED",
                    )
                    continue

                request_id = _extract_request_id(client_msg)
                msg_type = _normalize_message_type(client_msg.get("type"))

                is_valid_message, validation_message, validation_code = (
                    _validate_ws_client_message(client_msg)
                )
                if not is_valid_message:
                    await _send_ws_error_event(
                        websocket,
                        validation_message or "Invalid websocket message",
                        request_id=request_id,
                        code=validation_code or "BAD_REQUEST",
                    )
                    continue

                if msg_type in (None, "message", "chat_message", "agentic_chat"):
                    if request_id in processed_request_ids:
                        await websocket.send_json(
                            {
                                "type": "ack",
                                "request_id": request_id,
                                "status": "duplicate_request_ignored",
                            }
                        )
                        continue

                    followup_message = _extract_user_prompt(client_msg)
                    if not followup_message:
                        await _send_ws_error_event(
                            websocket,
                            "message field is required",
                            request_id=request_id,
                        )
                        continue

                    processed_request_ids[request_id] = time.monotonic()

                    followup_context = client_msg.get("context") or {}
                    followup_context["thread_id"] = thread_id
                    followup_context["_internal_task_state"] = (
                        _initialize_internal_task_state(followup_message)
                    )
                    try:
                        from services.workspace_context import enrich_project_context

                        enrich_project_context(
                            db,
                            followup_context,
                            user_message=followup_message,
                        )
                    except Exception:
                        logger.debug("Project context enrichment failed", exc_info=True)

                    # Rebuild history from persisted thread messages for each user turn.
                    conversation_history = _build_conversation_history(
                        db, thread_id, followup_message, followup_context
                    )

                    if msg_type == "agentic_chat":
                        try:
                            await _ensure_project_task_for_agent_chat(
                                websocket,
                                db,
                                thread_id=thread_id,
                                user=current_user,
                                user_message=followup_message,
                                context=followup_context,
                            )
                        except Exception:
                            logger.warning(
                                "Failed to ensure project task on follow-up thread=%s",
                                thread_id,
                                exc_info=True,
                            )
                        try:
                            continuation_text = await _run_agentic_loop_v2(
                                websocket,
                                db,
                                thread_id,
                                request_id,
                                followup_message,
                                followup_context,
                                current_user.id,
                            )
                        except Exception as exc:
                            logger.exception(
                                "Agentic follow-up failed: thread=%s",
                                thread_id,
                            )
                            await websocket.send_json(
                                {
                                    "type": "error",
                                    "message": f"Agentic loop failed: {str(exc)}",
                                    "request_id": request_id,
                                    "code": "AGENTIC_LOOP_FAILED",
                                }
                            )
                            continuation_text = None

                        if continuation_text:
                            conversation_history.append(
                                {"role": "assistant", "content": continuation_text}
                            )
                        continue

                    try:
                        continuation_text, was_cancelled = (
                            await _generate_with_cancel_support(
                                websocket,
                                request_id,
                                _generate_agent_response_with_internal_loop(
                                    conversation_history, followup_context
                                ),
                                buffered_client_messages,
                            )
                        )
                        if was_cancelled:
                            continuation_text = None
                    except Exception as exc:
                        logger.exception(
                            "LLM generation failed for follow-up message: thread=%s",
                            thread_id,
                        )
                        await websocket.send_json(
                            {
                                "type": "error",
                                "message": f"LLM generation failed: {str(exc)}",
                                "request_id": request_id,
                                "code": "LLM_GENERATION_FAILED",
                            }
                        )
                        continuation_text = None

                    if continuation_text:
                        for event_type, payload in _parse_and_stream_response(
                            continuation_text
                        ):
                            logger.debug("Agent WS streaming: type=%s", event_type)
                            if event_type == "text":
                                await _stream_text(
                                    websocket, payload, request_id=request_id
                                )
                            elif event_type == "code_block":
                                await websocket.send_json(
                                    {
                                        "type": "code_block",
                                        "request_id": request_id,
                                        **payload,
                                    }
                                )
                                await asyncio.sleep(0.1)
                            elif event_type == "tool_call":
                                await websocket.send_json(
                                    {
                                        "type": "tool_call",
                                        "request_id": request_id,
                                        **payload,
                                    }
                                )
                                await asyncio.sleep(0.1)

                        await websocket.send_json(
                            {"type": "done", "request_id": request_id}
                        )

                        _save_chat_messages(
                            db,
                            thread_id,
                            followup_message,
                            continuation_text,
                            {
                                "project_id": followup_context.get("project_id"),
                                "user_id": current_user.id,
                            },
                        )

                        conversation_history.append(
                            {"role": "assistant", "content": continuation_text}
                        )

                    continue

                if msg_type == "tool_result":
                    call_id = client_msg.get("call_id") or client_msg.get(
                        "tool_call_id"
                    )
                    if not call_id:
                        await _send_ws_error_event(
                            websocket,
                            "tool_result requires call_id",
                            request_id=request_id,
                        )
                        continue
                    if "result" not in client_msg:
                        await _send_ws_error_event(
                            websocket,
                            "tool_result requires result payload",
                            request_id=request_id,
                            code="INVALID_TOOL_RESULT",
                        )
                        continue
                    if call_id in processed_tool_result_ids:
                        await websocket.send_json(
                            {
                                "type": "tool_result_ack",
                                "request_id": request_id,
                                "call_id": call_id,
                                "status": "duplicate",
                            }
                        )
                        continue
                    processed_tool_result_ids[call_id] = time.monotonic()

                    result = client_msg.get("result")
                    logger.info(
                        "Agent WS tool_result: thread=%s call_id=%s result_keys=%s",
                        thread_id,
                        call_id,
                        (
                            list(result.keys())
                            if isinstance(result, dict)
                            else type(result).__name__
                        ),
                    )
                    await websocket.send_json(
                        {
                            "type": "tool_result_ack",
                            "request_id": request_id,
                            "call_id": call_id,
                            "status": "received",
                        }
                    )

                    # Append tool result to conversation
                    tool_message = {
                        "role": "tool",
                        "content": (
                            json.dumps(result)
                            if isinstance(result, (dict, list))
                            else str(result)
                        ),
                        "tool_call_id": call_id,
                    }
                    conversation_history.append(tool_message)

                    # Save tool message to database
                    _save_tool_message(
                        db, thread_id, {"call_id": call_id, "result": result}
                    )

                    # Re-call LLM with updated conversation
                    try:
                        continuation_text, was_cancelled = (
                            await _generate_with_cancel_support(
                                websocket,
                                request_id,
                                _generate_agent_response_with_internal_loop(
                                    conversation_history, context
                                ),
                                buffered_client_messages,
                            )
                        )
                        if was_cancelled:
                            continuation_text = None
                    except Exception as exc:
                        logger.exception(
                            "LLM continuation failed after tool result: thread=%s",
                            thread_id,
                        )
                        await websocket.send_json(
                            {
                                "type": "error",
                                "message": f"LLM continuation failed: {str(exc)}",
                                "request_id": request_id,
                                "code": "LLM_CONTINUATION_FAILED",
                            }
                        )
                        continuation_text = None

                    if continuation_text:
                        for event_type, payload in _parse_and_stream_response(
                            continuation_text
                        ):
                            logger.debug("Agent WS streaming: type=%s", event_type)
                            if event_type == "text":
                                await _stream_text(
                                    websocket, payload, request_id=request_id
                                )
                            elif event_type == "code_block":
                                await websocket.send_json(
                                    {
                                        "type": "code_block",
                                        "request_id": request_id,
                                        **payload,
                                    }
                                )
                                await asyncio.sleep(0.1)
                            elif event_type == "tool_call":
                                await websocket.send_json(
                                    {
                                        "type": "tool_call",
                                        "request_id": request_id,
                                        **payload,
                                    }
                                )
                                await asyncio.sleep(0.1)

                        await websocket.send_json(
                            {"type": "done", "request_id": request_id}
                        )

                        # Save assistant continuation to database
                        _save_chat_messages(
                            db,
                            thread_id,
                            f"[Tool result for {call_id}]",
                            continuation_text,
                            {
                                "project_id": context.get("project_id"),
                                "user_id": current_user.id,
                            },
                        )

                        # Append continuation to conversation history
                        conversation_history.append(
                            {"role": "assistant", "content": continuation_text}
                        )

                elif msg_type == "execute_tool":
                    call_id = client_msg.get("call_id")
                    tool_name = _normalize_tool_name(client_msg.get("tool"))
                    tool_args = client_msg.get("args", {})
                    if not call_id or not tool_name:
                        await _send_ws_error_event(
                            websocket,
                            "execute_tool requires call_id and tool",
                            request_id=request_id,
                        )
                        continue
                    if tool_name not in AGENT_TOOLS:
                        await _send_ws_error_event(
                            websocket,
                            f"Unsupported tool: {tool_name}",
                            request_id=request_id,
                            code="UNKNOWN_TOOL",
                        )
                        continue
                    if tool_args is None:
                        tool_args = {}
                    if not isinstance(tool_args, dict):
                        await _send_ws_error_event(
                            websocket,
                            "execute_tool args must be a JSON object",
                            request_id=request_id,
                            code="INVALID_TOOL_ARGS",
                        )
                        continue
                    if call_id in processed_execute_tool_ids:
                        await websocket.send_json(
                            {
                                "type": "tool_result",
                                "request_id": request_id,
                                "call_id": call_id,
                                "tool": tool_name,
                                "result": {
                                    "success": True,
                                    "duplicate": True,
                                    "message": "tool call already processed",
                                },
                            }
                        )
                        continue
                    processed_execute_tool_ids[call_id] = time.monotonic()

                    logger.info(
                        "Agent WS execute_tool: thread=%s call_id=%s tool=%s",
                        thread_id,
                        call_id,
                        tool_name,
                    )

                    try:
                        result = await _execute_tool_call(
                            db, current_user, tool_name, tool_args, context=context
                        )
                        db.commit()

                        if tool_name == "runSimulation" and result.get("success"):
                            run_id = str(result.get("run_id") or "").strip()
                            if run_id:
                                try:
                                    from database.models import Run
                                    from services.project_tasks import (
                                        get_task_by_run,
                                        serialize_project_task,
                                    )

                                    task = get_task_by_run(db, run_id)
                                    if task:
                                        await websocket.send_json(
                                            {
                                                "type": "project_task_updated",
                                                "task": serialize_project_task(task),
                                            }
                                        )
                                except Exception:
                                    logger.warning(
                                        "Failed to emit project task update for run=%s",
                                        run_id,
                                        exc_info=True,
                                    )

                        await websocket.send_json(
                            {
                                "type": "tool_result",
                                "request_id": request_id,
                                "call_id": call_id,
                                "tool": tool_name,
                                "result": result,
                            }
                        )

                        tool_message = {
                            "role": "tool",
                            "content": json.dumps(result),
                            "tool_call_id": call_id,
                        }
                        conversation_history.append(tool_message)

                        _save_tool_message(
                            db,
                            thread_id,
                            {"call_id": call_id, "tool": tool_name, "result": result},
                        )

                        try:
                            continuation_text, was_cancelled = (
                                await _generate_with_cancel_support(
                                    websocket,
                                    request_id,
                                    _generate_agent_response_with_internal_loop(
                                        conversation_history, context
                                    ),
                                    buffered_client_messages,
                                )
                            )
                            if was_cancelled:
                                continuation_text = None
                        except Exception as exc:
                            logger.exception(
                                "LLM continuation failed after tool execution: thread=%s",
                                thread_id,
                            )
                            await websocket.send_json(
                                {
                                    "type": "error",
                                    "message": f"LLM continuation failed: {str(exc)}",
                                    "request_id": request_id,
                                    "code": "LLM_CONTINUATION_FAILED",
                                }
                            )
                            continuation_text = None

                        if continuation_text:
                            for event_type, payload in _parse_and_stream_response(
                                continuation_text
                            ):
                                logger.debug("Agent WS streaming: type=%s", event_type)
                                if event_type == "text":
                                    await _stream_text(
                                        websocket, payload, request_id=request_id
                                    )
                                elif event_type == "code_block":
                                    await websocket.send_json(
                                        {
                                            "type": "code_block",
                                            "request_id": request_id,
                                            **payload,
                                        }
                                    )
                                    await asyncio.sleep(0.1)
                                elif event_type == "tool_call":
                                    await websocket.send_json(
                                        {
                                            "type": "tool_call",
                                            "request_id": request_id,
                                            **payload,
                                        }
                                    )
                                    await asyncio.sleep(0.1)

                            await websocket.send_json(
                                {"type": "done", "request_id": request_id}
                            )

                            _save_chat_messages(
                                db,
                                thread_id,
                                f"[Tool execution: {tool_name}]",
                                continuation_text,
                                {
                                    "project_id": context.get("project_id"),
                                    "user_id": current_user.id,
                                },
                            )

                            conversation_history.append(
                                {"role": "assistant", "content": continuation_text}
                            )

                    except Exception as exc:
                        logger.exception(
                            "Tool execution failed: thread=%s tool=%s",
                            thread_id,
                            tool_name,
                        )
                        db.rollback()
                        error_result = {
                            "success": False,
                            "error": str(exc),
                        }
                        await websocket.send_json(
                            {
                                "type": "tool_result",
                                "request_id": request_id,
                                "call_id": call_id,
                                "tool": tool_name,
                                "result": error_result,
                            }
                        )

                        tool_message = {
                            "role": "tool",
                            "content": json.dumps(error_result),
                            "tool_call_id": call_id,
                        }
                        conversation_history.append(tool_message)

                        _save_tool_message(
                            db,
                            thread_id,
                            {"call_id": call_id, "tool": tool_name, "error": str(exc)},
                        )

                        try:
                            continuation_text, was_cancelled = (
                                await _generate_with_cancel_support(
                                    websocket,
                                    request_id,
                                    _generate_agent_response_with_internal_loop(
                                        conversation_history, context
                                    ),
                                    buffered_client_messages,
                                )
                            )
                            if was_cancelled:
                                continuation_text = None
                        except Exception as exc2:
                            logger.exception(
                                "LLM continuation failed after tool error: thread=%s",
                                thread_id,
                            )
                            continuation_text = None

                        if continuation_text:
                            for event_type, payload in _parse_and_stream_response(
                                continuation_text
                            ):
                                logger.debug("Agent WS streaming: type=%s", event_type)
                                if event_type == "text":
                                    await _stream_text(
                                        websocket, payload, request_id=request_id
                                    )
                                elif event_type == "code_block":
                                    await websocket.send_json(
                                        {
                                            "type": "code_block",
                                            "request_id": request_id,
                                            **payload,
                                        }
                                    )
                                    await asyncio.sleep(0.1)
                                elif event_type == "tool_call":
                                    await websocket.send_json(
                                        {
                                            "type": "tool_call",
                                            "request_id": request_id,
                                            **payload,
                                        }
                                    )
                                    await asyncio.sleep(0.1)

                            await websocket.send_json(
                                {"type": "done", "request_id": request_id}
                            )

                            _save_chat_messages(
                                db,
                                thread_id,
                                f"[Tool error: {tool_name}]",
                                continuation_text,
                                {
                                    "project_id": context.get("project_id"),
                                    "user_id": current_user.id,
                                },
                            )

                            conversation_history.append(
                                {"role": "assistant", "content": continuation_text}
                            )

                elif msg_type == "ping":
                    await websocket.send_json(
                        {"type": "pong", "request_id": request_id}
                    )

                elif msg_type == "cancel":
                    processed_request_ids[request_id] = time.monotonic()
                    await websocket.send_json(
                        {
                            "type": "complete",
                            "request_id": request_id,
                            "status": "cancelled",
                        }
                    )

                elif msg_type == "close":
                    break

                else:
                    logger.warning(
                        "Agent WS unknown client message type: thread=%s type=%s payload_keys=%s",
                        thread_id,
                        msg_type,
                        list(client_msg.keys()),
                    )
                    await websocket.send_json(
                        {
                            "type": "error",
                            "message": f"Unknown message type: {msg_type}",
                            "request_id": request_id,
                            "code": "UNKNOWN_MESSAGE_TYPE",
                        }
                    )

            except Exception as e:
                error_message = f"Internal error: {str(e)}"
                logger.error("Agent WS error: %s", error_message)
                await _send_ws_error_and_close(websocket, error_message)
                break

    except WebSocketDisconnect:
        logger.info("Agent WS closed: thread_id=%s", thread_id)
    except Exception:
        logger.exception("Agent WS error: thread=%s", thread_id)
        await _send_ws_error_and_close(websocket, "Agent websocket stream failed")
    finally:
        if graph_event_unsub:
            graph_event_unsub()
        if cadence_event_unsub:
            cadence_event_unsub()
        db.close()
