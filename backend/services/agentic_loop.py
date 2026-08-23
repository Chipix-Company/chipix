"""
Server-side Multi-Step Agentic Loop Engine (Pochi-inspired)
============================================================
True agentic loop where:
1. LLM is called in a continuous step-based loop
2. Todos are dynamically discovered and managed via findTodos/mergeTodos
3. Tool calls are executed server-side with retry logic
4. Loop continues until LLM calls attemptCompletion
5. Progress streamed to frontend in real time

Uses NATIVE function calling (not regex parsing).
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import os
import shutil
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Coroutine, Dict, List, Literal, Optional, Tuple

from common.paths import outputs_dir as resolve_outputs_dir
from database.models import ChatMessage, ChatThread
from llm_provider import (
    DEFAULT_MAX_TOKENS,
    DEFAULT_MODEL,
    DEFAULT_TEMPERATURE,
    PROVIDER,
    ChatResponse,
    TokenUsage,
    ToolCall,
    chat_with_tools,
    chat_with_tools_stream,
    get_model_max_output_tokens,
)
from services.context_budget import (
    CADENCE_MAX_CHARS_PER_MESSAGE,
    DEFAULT_MAX_TOOL_RESULT_CHARS,
    estimate_messages_tokens,
    is_context_length_error,
    prune_messages_for_context,
    select_tool_definitions,
    truncate_tool_result_content,
    trim_conversation_history,
)
from services.rtl_project_package import (
    RtlArchiveError,
    extract_rtl_archive,
    is_rtl_archive_filename,
)

logger = logging.getLogger(__name__)

# ── Tool Definitions (JSON Schema for native function calling) ─────────

TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "readFile",
            "description": "Read the content of a file/artifact by its ID",
            "parameters": {
                "type": "object",
                "properties": {
                    "artifact_id": {
                        "type": "string",
                        "description": "The UUID of the artifact to read",
                    }
                },
                "required": ["artifact_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "listFiles",
            "description": "List all files/artifacts in a project",
            "parameters": {
                "type": "object",
                "properties": {
                    "project_id": {
                        "type": "string",
                        "description": "The project UUID to list files for",
                    }
                },
                "required": ["project_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "createFile",
            "description": "Create a new file/artifact in a project",
            "parameters": {
                "type": "object",
                "properties": {
                    "project_id": {"type": "string"},
                    "filename": {"type": "string"},
                    "artifact_type": {
                        "type": "string",
                        "enum": ["spec", "rtl", "generated"],
                    },
                    "content": {"type": "string"},
                    "replace_existing": {
                        "type": "boolean",
                        "description": (
                            "Set true only when the user explicitly asked to replace an "
                            "existing file with the same filename. Default false."
                        ),
                    },
                },
                "required": ["project_id", "filename", "artifact_type", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "applyCodeToFile",
            "description": "Apply code changes to an existing file/artifact",
            "parameters": {
                "type": "object",
                "properties": {
                    "artifact_id": {"type": "string"},
                    "code": {"type": "string"},
                    "strategy": {
                        "type": "string",
                        "enum": ["replace_file", "replace_selection", "smart_insert"],
                    },
                    "old_content": {
                        "type": "string",
                        "description": "Content to replace when strategy is replace_selection",
                    },
                },
                "required": ["artifact_id", "code", "strategy"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "runSimulation",
            "description": "Run a verification simulation for a project using active spec and RTL artifacts",
            "parameters": {
                "type": "object",
                "properties": {
                    "project_id": {"type": "string"},
                },
                "required": ["project_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "readSpecPages",
            "description": (
                "Read raw text from specific pages of the active (or given) spec document. "
                "Max 30 pages per call. Use the document index to pick page ranges."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "project_id": {"type": "string"},
                    "artifact_id": {"type": "string"},
                    "start_page": {"type": "integer"},
                    "end_page": {"type": "integer"},
                },
                "required": ["start_page", "end_page"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "readSpecSection",
            "description": "Read all pages for a section title from the spec document index",
            "parameters": {
                "type": "object",
                "properties": {
                    "project_id": {"type": "string"},
                    "artifact_id": {"type": "string"},
                    "section_title": {"type": "string"},
                },
                "required": ["section_title"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "searchSpec",
            "description": "Local keyword search across indexed spec pages and sections",
            "parameters": {
                "type": "object",
                "properties": {
                    "project_id": {"type": "string"},
                    "artifact_id": {"type": "string"},
                    "query": {"type": "string"},
                    "limit": {"type": "integer"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "attemptCompletion",
            "description": "Call this when you believe the user's request has been fully satisfied. Provide a comprehensive summary of what was accomplished.",
            "parameters": {
                "type": "object",
                "properties": {
                    "summary": {
                        "type": "string",
                        "description": "A detailed summary of everything that was accomplished, including files created/modified and key outcomes",
                    },
                    "command": {
                        "type": "string",
                        "description": "Optional command for the user to run to verify the results",
                    },
                },
                "required": ["summary"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "findTodos",
            "description": "Discover new todo items from the current conversation context. This helps track what still needs to be done.",
            "parameters": {
                "type": "object",
                "properties": {
                    "todos": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "id": {"type": "string"},
                                "description": {"type": "string"},
                                "type": {
                                    "type": "string",
                                    "enum": [
                                        "code",
                                        "test",
                                        "research",
                                        "documentation",
                                        "other",
                                    ],
                                },
                            },
                            "required": ["id", "description", "type"],
                        },
                    }
                },
                "required": ["todos"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "mergeTodos",
            "description": "Update the status of existing todo items based on current progress",
            "parameters": {
                "type": "object",
                "properties": {
                    "completed": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "IDs of todos that have been completed",
                    },
                    "failed": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "IDs of todos that failed",
                    },
                },
            },
        },
    },
]

# Extend with verification tool definitions
try:
    from agent_tools import get_all_tool_definitions
    TOOL_DEFINITIONS.extend(get_all_tool_definitions())
except ImportError:
    pass  # Verification tools not yet installed

# ── System Prompt ─────────────────────────────────────────────────────

AGENT_SYSTEM_PROMPT = """\
You are an expert AI assistant for semiconductor chip design and UVM verification.
You can help with: SystemVerilog RTL coding, UVM testbench generation, verification planning, debugging, and code review.

You have ChipStack-style verification agent tools available:
- scanProject: Scan a project folder to understand its structure
- buildMentalModel: Build a mental model (design intent knowledge base) from RTL + spec
- queryMentalModel: Query the mental model for design information
- queryCodebaseGraph: Query the structural codebase map (modules, spec links, hierarchy)
- getModuleNeighbors / getAffectedByChange / getGraphGodNodes: Navigate the codebase graph
- getCodebaseGraphStatus: Check if the codebase map is ready
- verifyBlock: End-to-end verification of a design block (runs UnitSim + Formal + UVM)
- generateTestbench: Generate a SystemVerilog testbench for unit simulation
- runUnitSimulation: Compile and run unit-level simulation
- generateFormalProperties: Generate SVA assertions for formal verification
- runFormalVerification: Run formal verification using SymbiYosys
- generateUVMEnvironment: Generate or update a UVM verification environment
- checkCadenceStatus: Check Cadence Xcelium connection and license
- runCadenceSimulation: Run full Cadence compile/elaborate/simulate on generated UVM artifacts
- getCadenceRunStatus: Poll an in-flight Cadence simulation run
- applyCadenceFixes: Apply Cadence repair diffs to generated artifacts before re-run
- analyzeCoverage: Analyze coverage results and identify gaps
- debugFailure: Classify failures and propose fixes

When a user asks to "verify a block" or "verify this design", use the verifyBlock tool.
Always build or check the mental model FIRST before generating any verification code.

When answering questions about design structure, hierarchy, spec-to-RTL mapping, or module
instantiation, use codebase graph tools (queryCodebaseGraph, getModuleNeighbors) before
reading entire RTL file trees.

You have access to tools that you can call to interact with the project's files and run simulations.
Use tools when you need to read files, create files, modify files, list project files, or run simulations.

When you have completed all tasks and the user's request is fully addressed, call the attemptCompletion function with a summary of what was accomplished.

Think step by step. Plan your approach before acting. Use tools efficiently - read only what you need, make targeted edits.

You can also use findTodos to declare new tasks and mergeTodos to mark tasks as completed or failed.
"""

# ── Event Types ───────────────────────────────────────────────────────

EVENT_TASK_LIST_CREATED = "task_list_created"
EVENT_TASK_STARTED = "task_started"
EVENT_TASK_COMPLETED = "task_completed"
EVENT_TURN_START = "turn_start"
EVENT_TURN_END = "turn_end"
EVENT_TOOL_CALL_STARTED = "tool_call_started"
EVENT_TOOL_CALL_COMPLETED = "tool_call_completed"
EVENT_TEXT_DELTA = "assistant_delta"
EVENT_CONTINUE_PROMPT = "continue_prompt"
EVENT_CONVERSATION_ENDED = "conversation_ended"
EVENT_SUMMARY = "summary"
EVENT_DONE = "done"
EVENT_ERROR = "error"
EVENT_TODOS_UPDATED = "todos_updated"

# ── Tool Execution Registry ──────────────────────────────────────────


@dataclass
class ToolExecutionResult:
    success: bool
    result: Any = None
    error: Optional[str] = None
    summary: str = ""


ToolExecutionMode = Literal["parallel", "sequential"]


@dataclass
class PreparedToolCall:
    tool_call: ToolCall
    args: dict


@dataclass
class ExecutedToolCall:
    tool_call: ToolCall
    result: ToolExecutionResult


class ToolRegistry:
    """Registry that maps tool names to server-side execution functions."""

    def __init__(self, db_session, context: Optional[dict] = None):
        self._db = db_session
        self._context = context or {}
        self._handlers: Dict[str, Callable] = {}
        self._register_default_tools()

    def _register_default_tools(self):
        self._handlers["readFile"] = self._exec_read_file
        self._handlers["listFiles"] = self._exec_list_files
        self._handlers["createFile"] = self._exec_create_file
        self._handlers["applyCodeToFile"] = self._exec_apply_code_to_file
        self._handlers["runSimulation"] = self._exec_run_simulation
        self._handlers["readSpecPages"] = self._exec_read_spec_pages
        self._handlers["readSpecSection"] = self._exec_read_spec_section
        self._handlers["searchSpec"] = self._exec_search_spec
        self._handlers["attemptCompletion"] = self._exec_attempt_completion
        self._handlers["findTodos"] = self._exec_find_todos
        self._handlers["mergeTodos"] = self._exec_merge_todos
        # Register ChipStack verification agent tools
        self._register_verification_tools()

    def _register_verification_tools(self):
        """Register verification-specific tools from agent_tools package."""
        try:
            from agent_tools import VERIFICATION_TOOL_HANDLERS
            import agent_tools.mental_model_tools   # noqa: F401
            import agent_tools.orchestrator_tool     # noqa: F401
            import agent_tools.unitsim_tools         # noqa: F401
            import agent_tools.formal_tools          # noqa: F401
            import agent_tools.uvm_tools             # noqa: F401
            import agent_tools.debug_tools           # noqa: F401
            import agent_tools.coverage_tools        # noqa: F401
            import agent_tools.plan_tuning_tools     # noqa: F401
            import agent_tools.dashboard_tools       # noqa: F401
            import agent_tools.multimodal_tools      # noqa: F401
            import agent_tools.evolve_tools          # noqa: F401
            import agent_tools.codebase_graph_tools  # noqa: F401
            import agent_tools.cadence_tools         # noqa: F401
            for name, handler in VERIFICATION_TOOL_HANDLERS.items():
                self._handlers[name] = self._make_verification_handler(name, handler)
                logger.info(f"Registered verification tool: {name}")
        except ImportError as e:
            logger.warning(f"Could not load verification tools: {e}")

    def _make_verification_handler(self, name: str, handler):
        """Wrap async verification tool handler for ToolRegistry."""
        async def wrapper(args):
            try:
                from services.session_context import merge_verification_args_from_context

                args = merge_verification_args_from_context(
                    name, args or {}, self._context
                )
                args["db_session"] = self._db
                args["ai_client"] = self._context.get("ai_client")
                result = await handler(**args)
                return ToolExecutionResult(
                    success=True, result=result,
                    summary=f"Tool '{name}' completed",
                )
            except Exception as e:
                return ToolExecutionResult(
                    success=False, error=str(e),
                    summary=f"Tool '{name}' failed",
                )
        return wrapper

    def register(self, name: str, handler: Callable):
        self._handlers[name] = handler

    def has(self, name: str) -> bool:
        return name in self._handlers

    async def execute(self, tool_name: str, args: dict) -> ToolExecutionResult:
        handler = self._handlers.get(tool_name)
        if not handler:
            return ToolExecutionResult(
                success=False,
                error=f"Unknown tool: {tool_name}",
                summary=f"Tool '{tool_name}' not found",
            )
        try:
            if inspect.iscoroutinefunction(handler):
                result = await handler(args)
            else:
                loop = asyncio.get_running_loop()
                result = await loop.run_in_executor(None, lambda: handler(args))
                if inspect.isawaitable(result):
                    result = await result
            return result
        except Exception as e:
            logger.exception("Tool execution failed: %s", tool_name)
            return ToolExecutionResult(
                success=False,
                error=str(e),
                summary=f"Tool '{tool_name}' failed: {str(e)[:100]}",
            )

    def _require_artifact(self, artifact_id: str):
        from database.models import ProjectArtifact

        artifact = (
            self._db.query(ProjectArtifact)
            .filter(ProjectArtifact.id == artifact_id)
            .first()
        )
        if not artifact:
            raise ValueError(f"Artifact not found: {artifact_id}")
        return artifact

    def _require_project(self, project_id: str):
        from database.models import Project

        project = self._db.query(Project).filter(Project.id == project_id).first()
        if not project:
            raise ValueError(f"Project not found: {project_id}")
        return project

    def _read_artifact(self, artifact) -> str:
        path = Path(artifact.file_path)
        if not path.exists():
            raise ValueError(f"Artifact file missing: {artifact.file_path}")
        return path.read_text(encoding="utf-8", errors="replace")

    def _write_artifact(self, artifact, content: str):
        path = Path(artifact.file_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def _materialize_rtl_for_run(self, artifact, input_dir: Path) -> Path:
        safe_name = Path(artifact.filename or "rtl_context.sv").name or "rtl_context.sv"
        rtl_dest = input_dir / f"rtl_{safe_name}"
        shutil.copy2(artifact.file_path, rtl_dest)

        if not is_rtl_archive_filename(safe_name):
            return rtl_dest

        extract_dir = input_dir / "rtl_project"
        try:
            extract_rtl_archive(rtl_dest, extract_dir)
        except RtlArchiveError as exc:
            raise ValueError(str(exc)) from exc
        return extract_dir

    def _exec_read_file(self, args: dict) -> ToolExecutionResult:
        artifact_id = args.get("artifact_id")
        if not artifact_id:
            raise ValueError("artifact_id is required")
        artifact = self._require_artifact(artifact_id)
        content = self._read_artifact(artifact)
        read_cap = 8_000 if self._context.get("cadence_verification_mode") else 16_000
        truncated = len(content) > read_cap
        if truncated:
            content = (
                content[: read_cap // 2]
                + f"\n\n… [file truncated at {read_cap:,} chars — use readSpecPages for specs] …\n\n"
                + content[-(read_cap // 4) :]
            )
        artifact_type = artifact.artifact_type
        language_map = {
            "spec": "text",
            "rtl": "systemverilog",
            "generated": "systemverilog",
        }
        language = language_map.get(artifact_type, "text")
        return ToolExecutionResult(
            success=True,
            result={
                "success": True,
                "content": content,
                "filename": artifact.filename,
                "language": language,
                "truncated": truncated,
            },
            summary=f"Read file '{artifact.filename}' ({len(content)} chars)",
        )

    def _resolve_spec_artifact(self, args: dict):
        from database.models import ProjectArtifactPointer

        artifact_id = str(args.get("artifact_id") or "").strip()
        project_id = str(
            args.get("project_id") or self._context.get("project_id") or ""
        ).strip()
        if not project_id:
            raise ValueError("project_id is required (or set in session context)")
        project = self._require_project(project_id)
        if artifact_id:
            artifact = self._require_artifact(artifact_id)
            if artifact.project_id != project.id:
                raise ValueError("artifact_id does not belong to this project")
            if artifact.artifact_type != "spec":
                raise ValueError("artifact_id must be a spec artifact")
            return project, artifact
        pointer = (
            self._db.query(ProjectArtifactPointer)
            .filter(ProjectArtifactPointer.project_id == project.id)
            .first()
        )
        if not pointer or not pointer.active_spec_artifact_id:
            raise ValueError("No active spec artifact; pass artifact_id")
        artifact = self._require_artifact(pointer.active_spec_artifact_id)
        return project, artifact

    def _ensure_spec_index(self, project, artifact) -> None:
        from services.document_context import ensure_spec_document_index

        ensure_spec_document_index(
            organization_id=project.organization_id,
            project_id=project.id,
            artifact_id=artifact.id,
            file_path=artifact.file_path,
            filename=artifact.filename,
            checksum_sha256=artifact.checksum_sha256,
        )

    def _exec_read_spec_pages(self, args: dict) -> ToolExecutionResult:
        from services.document_context import read_spec_pages

        start_page = int(args.get("start_page") or 1)
        end_page = int(args.get("end_page") or start_page)
        project, artifact = self._resolve_spec_artifact(args)
        self._ensure_spec_index(project, artifact)
        result = read_spec_pages(
            organization_id=project.organization_id,
            project_id=project.id,
            artifact_id=artifact.id,
            start_page=start_page,
            end_page=end_page,
        )
        return ToolExecutionResult(
            success=True,
            result=result,
            summary=f"Read spec pages {start_page}-{end_page}",
        )

    def _exec_read_spec_section(self, args: dict) -> ToolExecutionResult:
        from services.document_context import read_spec_section

        section_title = str(args.get("section_title") or "").strip()
        if not section_title:
            raise ValueError("section_title is required")
        project, artifact = self._resolve_spec_artifact(args)
        self._ensure_spec_index(project, artifact)
        result = read_spec_section(
            organization_id=project.organization_id,
            project_id=project.id,
            artifact_id=artifact.id,
            section_title=section_title,
        )
        return ToolExecutionResult(
            success=True,
            result=result,
            summary=f"Read spec section '{section_title}'",
        )

    def _exec_search_spec(self, args: dict) -> ToolExecutionResult:
        from services.document_context import search_spec

        query = str(args.get("query") or "").strip()
        if not query:
            raise ValueError("query is required")
        limit = int(args.get("limit") or 12)
        project, artifact = self._resolve_spec_artifact(args)
        self._ensure_spec_index(project, artifact)
        result = search_spec(
            organization_id=project.organization_id,
            project_id=project.id,
            artifact_id=artifact.id,
            query=query,
            limit=limit,
        )
        hits = len(result.get("hits") or [])
        return ToolExecutionResult(
            success=True,
            result=result,
            summary=f"searchSpec '{query}' returned {hits} hit(s)",
        )

    def _exec_list_files(self, args: dict) -> ToolExecutionResult:
        from database.models import ProjectArtifact, ProjectArtifactPointer

        project_id = args.get("project_id")
        if not project_id:
            raise ValueError("project_id is required")
        project = self._require_project(project_id)

        artifacts = (
            self._db.query(ProjectArtifact)
            .filter(ProjectArtifact.project_id == project.id)
            .order_by(
                ProjectArtifact.artifact_type.asc(),
                ProjectArtifact.revision.desc(),
            )
            .all()
        )

        pointer = (
            self._db.query(ProjectArtifactPointer)
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
        return ToolExecutionResult(
            success=True,
            result={"success": True, "files": files},
            summary=f"Listed {len(files)} files in project",
        )

    def _exec_create_file(self, args: dict) -> ToolExecutionResult:
        import hashlib

        from database.models import ProjectArtifact, ProjectArtifactPointer
        from services.workspace_context import validate_create_file_policy

        project_id = args.get("project_id")
        filename = args.get("filename")
        artifact_type = args.get("artifact_type")
        content = args.get("content", "")
        replace_existing = bool(args.get("replace_existing"))

        if not project_id:
            raise ValueError("project_id is required")
        if not filename:
            raise ValueError("filename is required")
        if not artifact_type:
            raise ValueError("artifact_type is required")
        if artifact_type not in {"spec", "rtl", "generated"}:
            raise ValueError("artifact_type must be one of: spec, rtl, generated")

        validate_create_file_policy(
            self._db,
            project_id=str(project_id),
            filename=str(filename),
            artifact_type=str(artifact_type),
            context=self._context,
            replace_existing=replace_existing,
        )

        project = self._require_project(project_id)

        raw_bytes = content.encode("utf-8")
        checksum = hashlib.sha256(raw_bytes).hexdigest()

        max_rev = (
            self._db.query(
                __import__("sqlalchemy", fromlist=["func"]).func.max(
                    ProjectArtifact.revision
                )
            )
            .filter(
                ProjectArtifact.project_id == project.id,
                ProjectArtifact.artifact_type == artifact_type,
            )
            .scalar()
        )
        revision = int(max_rev or 0) + 1

        artifact_id = str(uuid.uuid4())
        safe_name = filename.strip() or f"{artifact_type}.txt"
        storage_filename = f"v{revision:04d}_{artifact_id}_{safe_name}"

        outputs_dir = resolve_outputs_dir()
        artifact_path = (
            outputs_dir
            / "orgs"
            / project.organization_id
            / "projects"
            / project.id
            / "artifacts"
            / artifact_type
            / storage_filename
        )
        artifact_path.parent.mkdir(parents=True, exist_ok=True)
        artifact_path.write_bytes(raw_bytes)

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
        self._db.add(artifact)

        if artifact_type in {"spec", "rtl"}:
            pointer = (
                self._db.query(ProjectArtifactPointer)
                .filter(ProjectArtifactPointer.project_id == project.id)
                .first()
            )
            if not pointer:
                pointer = ProjectArtifactPointer(project_id=project.id)
                self._db.add(pointer)
            if artifact_type == "spec":
                pointer.active_spec_artifact_id = artifact.id
            else:
                pointer.active_rtl_artifact_id = artifact.id

        self._db.flush()
        # Commit immediately so the artifact is visible to subsequent API queries
        # (the frontend polls /artifacts after each file is created)
        try:
            self._db.commit()
        except Exception:
            self._db.rollback()
            raise

        result_payload = {
            "success": True,
            "artifact": {
                "id": artifact.id,
                "filename": artifact.filename,
                "artifact_type": artifact.artifact_type,
                "revision": artifact.revision,
                "project_id": artifact.project_id,
                "file_path": str(artifact_path),
            },
        }

        from services.sv_lint.agent_gate import (
            lint_failure_error_payload,
            run_post_write_sv_lint,
        )

        lint_result = run_post_write_sv_lint(
            artifact_path,
            filename=safe_name,
            artifact_type=artifact_type,
        )
        if lint_result is not None:
            result_payload["lint"] = lint_result.to_dict()
            if lint_result.has_agent_blocking_issues:
                return ToolExecutionResult(
                    success=False,
                    error=lint_failure_error_payload(lint_result, filename=safe_name),
                    result=result_payload,
                    summary=f"Lint failed for `{safe_name}`",
                )

        return ToolExecutionResult(
            success=True,
            result=result_payload,
            # Use backtick format so frontend regex can extract the filename
            summary=f"Written `{safe_name}` ({len(content.splitlines())} lines)",
        )

    def _exec_apply_code_to_file(self, args: dict) -> ToolExecutionResult:
        import difflib

        artifact_id = args.get("artifact_id")
        code = args.get("code")
        strategy = args.get("strategy", "smart_insert")
        old_content = args.get("old_content")

        if not artifact_id:
            raise ValueError("artifact_id is required")
        if code is None:
            raise ValueError("code is required")
        if strategy not in {"replace_file", "replace_selection", "smart_insert"}:
            raise ValueError(
                "strategy must be one of: replace_file, replace_selection, smart_insert"
            )

        artifact = self._require_artifact(artifact_id)
        original_content = self._read_artifact(artifact)

        if strategy == "replace_file":
            new_content = code
        elif strategy == "replace_selection":
            if old_content is None:
                raise ValueError("old_content is required for replace_selection")
            if old_content not in original_content:
                raise ValueError("old_content not found in the current file")
            new_content = original_content.replace(old_content, code, 1)
        else:
            if original_content and not original_content.endswith("\n"):
                new_content = original_content + "\n" + code
            else:
                new_content = original_content + code

        self._write_artifact(artifact, new_content)

        new_bytes = new_content.encode("utf-8")
        import hashlib

        artifact.checksum_sha256 = hashlib.sha256(new_bytes).hexdigest()
        artifact.size_bytes = len(new_bytes)

        diff_lines = difflib.unified_diff(
            original_content.splitlines(keepends=True),
            new_content.splitlines(keepends=True),
            fromfile=artifact.filename,
            tofile=artifact.filename,
        )
        diff_text = "".join(diff_lines)

        result_payload = {
            "success": True,
            "new_content": new_content,
            "diff": diff_text,
            "artifact": {
                "id": artifact.id,
                "filename": artifact.filename,
                "artifact_type": artifact.artifact_type,
                "file_path": artifact.file_path,
            },
        }

        from services.sv_lint.agent_gate import (
            lint_failure_error_payload,
            run_post_write_sv_lint,
        )

        lint_result = run_post_write_sv_lint(
            artifact.file_path,
            filename=artifact.filename,
            artifact_type=artifact.artifact_type,
        )
        if lint_result is not None:
            result_payload["lint"] = lint_result.to_dict()
            if lint_result.has_agent_blocking_issues:
                return ToolExecutionResult(
                    success=False,
                    error=lint_failure_error_payload(
                        lint_result, filename=artifact.filename
                    ),
                    result=result_payload,
                    summary=f"Lint failed for '{artifact.filename}'",
                )

        return ToolExecutionResult(
            success=True,
            result=result_payload,
            summary=f"Applied code to '{artifact.filename}' using {strategy}",
        )

    def _exec_run_simulation(self, args: dict) -> ToolExecutionResult:
        import shutil

        from database.models import (
            ProjectArtifact,
            ProjectArtifactPointer,
            Run,
        )

        project_id = args.get("project_id")
        if not project_id:
            raise ValueError("project_id is required")
        project = self._require_project(project_id)

        pointer = (
            self._db.query(ProjectArtifactPointer)
            .filter(ProjectArtifactPointer.project_id == project.id)
            .first()
        )
        if not pointer or not pointer.active_spec_artifact_id:
            raise ValueError("No active spec artifact set for this project")
        if not pointer.active_rtl_artifact_id:
            raise ValueError("No active RTL artifact set for this project")

        spec_artifact = (
            self._db.query(ProjectArtifact)
            .filter(ProjectArtifact.id == pointer.active_spec_artifact_id)
            .first()
        )
        rtl_artifact = (
            self._db.query(ProjectArtifact)
            .filter(ProjectArtifact.id == pointer.active_rtl_artifact_id)
            .first()
        )

        if not spec_artifact:
            raise ValueError("Active spec artifact not found")
        if not rtl_artifact:
            raise ValueError("Active RTL artifact not found")

        if not Path(spec_artifact.file_path).exists():
            raise ValueError("Active spec artifact file is missing")
        if not Path(rtl_artifact.file_path).exists():
            raise ValueError("Active RTL artifact file is missing")

        run_id = str(uuid.uuid4())
        outputs_dir = resolve_outputs_dir()
        run_dir = (
            outputs_dir
            / "orgs"
            / project.organization_id
            / "projects"
            / project.id
            / "runs"
            / run_id
        )
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "inputs").mkdir(exist_ok=True)

        input_dir = run_dir / "inputs"
        spec_dest = input_dir / f"spec_{spec_artifact.filename}"
        shutil.copy2(spec_artifact.file_path, spec_dest)
        rtl_dest = self._materialize_rtl_for_run(rtl_artifact, input_dir)

        run = Run(
            id=run_id,
            organization_id=project.organization_id,
            project_id=project.id,
            user_id=project.owner_user_id,
            prompt_text=f"Agent-triggered run for project {project.name}",
            specification_type="artifact",
            status="running",
            gpu_used=True,
            logs_path=str(run_dir / "logs.json"),
            output_path=str(run_dir),
        )
        self._db.add(run)
        self._db.flush()

        try:
            from services.runner import start_job

            start_job(run_id, str(rtl_dest), str(spec_dest), str(run_dir))
        except Exception as e:
            logger.warning("Simulation job start failed: %s", e)

        return ToolExecutionResult(
            success=True,
            result={"success": True, "run_id": run_id},
            summary=f"Started simulation run {run_id}",
        )

    def _exec_attempt_completion(self, args: dict) -> ToolExecutionResult:
        summary = args.get("summary", "")
        command = args.get("command", "")
        return ToolExecutionResult(
            success=True,
            result={"success": True, "summary": summary, "command": command},
            summary=summary,
        )

    def _exec_find_todos(self, args: dict) -> ToolExecutionResult:
        todos = args.get("todos", [])
        return ToolExecutionResult(
            success=True,
            result={"success": True, "todos": todos},
            summary=f"Found {len(todos)} new todos",
        )

    def _exec_merge_todos(self, args: dict) -> ToolExecutionResult:
        completed = args.get("completed", [])
        failed = args.get("failed", [])
        return ToolExecutionResult(
            success=True,
            result={"success": True, "completed": completed, "failed": failed},
            summary=f"Updated todos: {len(completed)} completed, {len(failed)} failed",
        )


# ── Agentic Loop Engine ───────────────────────────────────────────────


@dataclass
class TodoItem:
    """A single todo item that can be dynamically created/modified by the LLM."""

    id: str
    description: str
    todo_type: str = "other"  # code, test, research, documentation, other
    status: str = "pending"  # pending, in_progress, completed, failed
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    completed_at: Optional[datetime] = None


class AgenticLoopEngine:
    """
    True server-side agentic loop inspired by Pochi's TaskRunner.

    Architecture:
    - No static task lists - todos are dynamically discovered via findTodos
    - Continuous step-based loop: LLM -> tool -> LLM -> ... until completion
    - Todo states updated via mergeTodos
    - Uses attemptCompletion for finalization
    - Retry logic for failed tool calls with exponential backoff
    """

    def __init__(
        self,
        db_session,
        thread_id: str,
        websocket_send_fn: Callable[[dict], Coroutine],
        model: Optional[str] = None,
        temperature: Optional[float] = None,
        max_iterations: int = 25,
        tool_timeout: int = 60,
        max_tool_retries: int = 3,
        tool_execution: ToolExecutionMode = "parallel",
        before_tool_call: Optional[Callable[..., Any]] = None,
        after_tool_call: Optional[Callable[..., Any]] = None,
        context: Optional[dict] = None,
        custom_system_prompt: Optional[str] = None,
    ):
        self._db = db_session
        self._thread_id = thread_id
        self._send = websocket_send_fn
        # Leave provider-specific fallback resolution to llm_provider. Using
        # DEFAULT_MODEL here can leak a Gemini MODEL_NAME into Azure/OpenAI
        # calls and produce DeploymentNotFound for the agentic loop.
        self._model = model
        self._temperature = (
            temperature if temperature is not None else DEFAULT_TEMPERATURE
        )
        self._max_iterations = max_iterations
        self._tool_timeout = tool_timeout
        self._max_tool_retries = max_tool_retries
        self._tool_execution: ToolExecutionMode = (
            "sequential" if tool_execution == "sequential" else "parallel"
        )
        self._before_tool_call = before_tool_call
        self._after_tool_call = after_tool_call
        self._context = context or {}
        self._custom_system_prompt = custom_system_prompt

        self._cancelled = False
        self._tool_registry = ToolRegistry(db_session, context)
        self._conversation_history: List[Dict[str, Any]] = []
        self._todos: List[TodoItem] = []
        self._iteration_count = 0
        self._total_tool_calls = 0
        self._completed = False
        self._final_summary = ""
        self._final_command = ""
        self._token_usage = TokenUsage(provider=PROVIDER, model=self._model or DEFAULT_MODEL)
        self._linked_task_id = str(self._context.get("active_task_id") or "").strip() or None

    def _todo_progress_pct(self) -> Optional[int]:
        if not self._todos:
            return None
        done = sum(
            1 for todo in self._todos if str(todo.status) in {"completed", "failed"}
        )
        return int(round(100 * done / max(1, len(self._todos))))

    def _agent_progress_pct(self) -> int:
        todo_pct = self._todo_progress_pct()
        if todo_pct is not None:
            return todo_pct
        if self._max_iterations > 0:
            return max(
                10,
                min(95, int(round(100 * self._iteration_count / self._max_iterations))),
            )
        return max(10, min(95, self._iteration_count * 8))

    def _resolve_linked_task(self):
        if not self._db or not self._thread_id:
            return None
        from database.models import ChatThreadState, ProjectTask, Run
        from services.project_tasks import get_task_for_thread, link_run_to_thread_task

        task = None
        if self._linked_task_id:
            task = (
                self._db.query(ProjectTask)
                .filter(ProjectTask.id == self._linked_task_id)
                .first()
            )
        if not task:
            task = get_task_for_thread(self._db, self._thread_id)
            if task:
                self._linked_task_id = task.id

        if not task:
            return None

        state = (
            self._db.query(ChatThreadState)
            .filter(ChatThreadState.thread_id == self._thread_id)
            .first()
        )
        context_run_id = getattr(state, "context_run_id", None) if state else None
        if context_run_id and not task.run_id:
            run = self._db.query(Run).filter(Run.id == context_run_id).first()
            if run:
                user_id = str(self._context.get("user_id") or "").strip()
                user = None
                if user_id:
                    from database.models import User

                    user = self._db.query(User).filter(User.id == user_id).first()
                linked = link_run_to_thread_task(
                    self._db,
                    thread_id=self._thread_id,
                    run=run,
                    user=user,
                )
                if linked:
                    task = linked
                    self._linked_task_id = linked.id

        return task

    def _link_run_to_task(self, run_id: str) -> None:
        if not self._db or not self._thread_id or not run_id:
            return
        try:
            from database.models import Run, User
            from services.project_tasks import link_run_to_thread_task

            run = self._db.query(Run).filter(Run.id == run_id).first()
            if not run:
                return
            user_id = str(self._context.get("user_id") or "").strip()
            user = None
            if user_id:
                user = self._db.query(User).filter(User.id == user_id).first()
            linked = link_run_to_thread_task(
                self._db,
                thread_id=self._thread_id,
                run=run,
                user=user,
            )
            if linked:
                self._linked_task_id = linked.id
        except Exception:
            logger.warning(
                "Failed to link run=%s to project task for thread=%s",
                run_id,
                self._thread_id,
                exc_info=True,
            )

    def _sync_linked_project_task(
        self,
        *,
        status: Optional[str] = None,
        progress_pct: Optional[int] = None,
        completed: bool = False,
    ) -> None:
        if not self._db or not self._thread_id:
            return
        try:
            from services.project_tasks import sync_run_to_linked_task

            task = self._resolve_linked_task()
            if not task:
                return

            if task.run_id:
                from database.models import Run

                run = self._db.query(Run).filter(Run.id == task.run_id).first()
                if run:
                    sync_run_to_linked_task(self._db, run)
                    if str(run.status) in {"queued", "running"}:
                        return

            terminal_statuses = {"completed", "blocked", "cancelled"}
            if completed and task.status not in {"in_verification", "blocked"}:
                task.status = "completed"
                task.progress_pct = 100
                task.completed_at = datetime.now(timezone.utc)
            elif status and task.status not in terminal_statuses:
                task.status = status
            resolved_progress = (
                progress_pct
                if progress_pct is not None
                else self._agent_progress_pct()
            )
            task.progress_pct = max(
                task.progress_pct,
                max(0, min(100, resolved_progress)),
            )
            if status == "in_progress":
                task.progress_pct = max(task.progress_pct, 10)

            task.updated_at = datetime.now(timezone.utc)
            self._db.commit()
        except Exception:
            logger.warning(
                "Failed to sync linked project task for thread=%s",
                self._thread_id,
                exc_info=True,
            )

    async def _emit_project_task_updated(self) -> None:
        if not self._db:
            return
        try:
            from services.project_tasks import serialize_project_task

            task = self._resolve_linked_task()
            if not task:
                return
            await self._send_event(
                "project_task_updated",
                {"task": serialize_project_task(task)},
            )
        except Exception:
            logger.warning(
                "Failed to emit project task update for thread=%s",
                self._thread_id,
                exc_info=True,
            )

    async def run(
        self,
        user_message: str,
        conversation_history: Optional[List[Dict[str, Any]]] = None,
    ):
        """Main entry point. Runs the continuous agentic loop."""
        self._conversation_history = list(conversation_history or [])

        mode = str(self._context.get("mode") or "").strip().lower()
        project_id = self._context.get("project_id")
        if (
            mode == "rtl_designer"
            and project_id
            and self._db is not None
        ):
            from services.workspace_context import enrich_rtl_designer_context

            enrich_rtl_designer_context(
                self._db,
                self._context,
                user_message=user_message,
            )

        try:
            cadence_mode = bool(self._context.get("cadence_verification_mode"))
            history_cap = 12 if cadence_mode else 40
            per_msg_cap = CADENCE_MAX_CHARS_PER_MESSAGE if cadence_mode else 12_000
            self._conversation_history = trim_conversation_history(
                self._conversation_history,
                max_messages=history_cap,
                max_chars_per_message=per_msg_cap,
            )

            system_prompt = self._build_system_prompt()
            # Build messages: system → prior conversation history → current user turn
            messages: List[Dict[str, Any]] = [{"role": "system", "content": system_prompt}]
            # Inject conversation history so the model remembers previous turns
            # (e.g. the plan it made, so "go" triggers correct file generation)
            for h in self._conversation_history:
                role = h.get("role", "user")
                # Skip system messages from history (we already have ours above)
                if role == "system":
                    continue
                messages.append({"role": role, "content": h.get("content", "")})
            messages.append({"role": "user", "content": user_message})

            while (
                self._iteration_count < self._max_iterations
                and not self._completed
                and not self._cancelled
            ):
                self._iteration_count += 1

                logger.info(
                    "Agentic loop step %d/%d, pending_todos=%d, total_tool_calls=%d",
                    self._iteration_count,
                    self._max_iterations,
                    len([t for t in self._todos if t.status == "pending"]),
                    self._total_tool_calls,
                )

                await self._send_event(
                    EVENT_TURN_START,
                    {
                        "iteration": self._iteration_count,
                        "tool_execution_mode": self._tool_execution,
                    },
                )
                self._sync_linked_project_task(status="in_progress")
                await self._emit_project_task_updated()

                response = await self._call_llm(messages)

                if response.content:
                    await self._stream_text(response.content)

                if response.tool_calls:
                    messages.append(
                        {
                            "role": "assistant",
                            "content": response.content or "",
                            "tool_calls": [
                                {
                                    "id": tc.id,
                                    "type": "function",
                                    "function": {
                                        "name": tc.name,
                                        "arguments": json.dumps(tc.arguments),
                                    },
                                }
                                for tc in response.tool_calls
                            ],
                        }
                    )

                    completed_early = await self._handle_tool_calls(
                        response.tool_calls,
                        messages,
                    )
                    if completed_early:
                        return
                else:
                    # No tool calls → model is presenting output to the user
                    # (a plan, a question, or a summary). Stop and let the
                    # user respond via the follow-up input.
                    logger.info(
                        "Agentic loop: text-only response on step %d, "
                        "breaking to wait for user input",
                        self._iteration_count,
                    )
                    self._completed = True

                await self._send_event(
                    EVENT_TURN_END,
                    {
                        "iteration": self._iteration_count,
                        "completed": self._completed,
                    },
                )
                if self._completed:
                    self._sync_linked_project_task(
                        progress_pct=self._agent_progress_pct(),
                    )
                    await self._emit_project_task_updated()

            if not self._completed:
                await self._force_complete_with_summary()

            await self._send_final_summary()

        except asyncio.CancelledError:
            logger.info("Agentic loop cancelled")
            await self._send_event(
                EVENT_ERROR,
                {"message": "Operation cancelled by user"},
            )
            raise
        except Exception as e:
            logger.exception("Agentic loop failed")
            await self._send_event(
                EVENT_ERROR,
                {"message": f"Agentic loop failed: {str(e)}"},
            )
            raise
        finally:
            try:
                self._db.commit()
            except Exception:
                logger.warning("DB commit failed in agentic loop cleanup", exc_info=True)
                self._db.rollback()

    async def cancel(self):
        """Cancel the running loop."""
        self._cancelled = True
        logger.info("Agentic loop cancellation requested")

    def _build_system_prompt(self) -> str:
        """Build the system prompt. Uses custom_system_prompt if provided,
        then always appends the live project context block."""
        base = self._custom_system_prompt if self._custom_system_prompt else AGENT_SYSTEM_PROMPT
        prompt = base

        # ── Inject live project context so the model never has to guess ─────
        project_id = self._context.get("project_id")
        workspace_name = self._context.get("workspace_name", "")
        language = self._context.get("language", "")
        mode = self._context.get("mode", "")

        context_lines = []
        if project_id:
            context_lines.append(f"- Active project_id (REQUIRED for project tools): {project_id}")
        if workspace_name:
            context_lines.append(f"- Project name: {workspace_name}")
        if language:
            context_lines.append(f"- Target language: {language}")
        if mode == "rtl_designer":
            context_lines.append("- Mode: RTL Designer (follow the Clarify→Plan→Execute workflow)")

        if context_lines:
            prompt += "\n\n## Active Session Context\n" + "\n".join(context_lines)
            if project_id:
                cadence_note = (
                    " Cadence tools (runCadenceSimulation, getCadenceRunStatus, applyCadenceFixes) "
                    "also use this project — the server injects it when omitted."
                    if self._context.get("cadence_verification_mode")
                    else ""
                )
                prompt += (
                    f"\n\nIMPORTANT: The project_id is `{project_id}`. "
                    "Always pass this exact value as `project_id` in every createFile, listFiles, "
                    f"and verification tool call when required.{cadence_note} "
                    "Never invent or guess a project_id."
                ).format(cadence_note=cadence_note)

        cadence_mode = bool(self._context.get("cadence_verification_mode"))
        manifest_cap = 4_000 if cadence_mode else 12_000
        spec_cap = 2_000 if cadence_mode else 12_000

        manifest_summary = self._context.get("workspace_manifest_summary")
        if isinstance(manifest_summary, str) and manifest_summary.strip():
            prompt += "\n\n" + manifest_summary.strip()[:manifest_cap]

        if not cadence_mode:
            spec_index = self._context.get("active_spec_document_index")
            if isinstance(spec_index, str) and spec_index.strip():
                prompt += (
                    "\n\n### Active specification document index\n"
                    + spec_index.strip()[:spec_cap]
                    + "\n\nFor spec questions use readSpecPages, readSpecSection, or searchSpec. "
                    "Cite page numbers. Do not readFile entire large PDF specs."
                )

        if self._todos:
            pending = [t for t in self._todos if t.status == "pending"]
            in_progress = [t for t in self._todos if t.status == "in_progress"]
            completed = [t for t in self._todos if t.status == "completed"]

            prompt += "\n\n## Current Todos:\n"
            if in_progress:
                prompt += "### In Progress:\n"
                for t in in_progress:
                    prompt += f"- [{t.id}] {t.description}\n"
            if pending:
                prompt += "### Pending:\n"
                for t in pending:
                    prompt += f"- [{t.id}] {t.description}\n"
            if completed:
                prompt += "### Completed:\n"
                for t in completed:
                    prompt += f"- [{t.id}] {t.description}\n"

        return prompt

    async def _send_event(self, event_type: str, payload: dict):
        """Send an event to the frontend via websocket."""
        event = {"type": event_type, **payload}
        try:
            await self._send(event)
        except Exception as e:
            logger.warning("Failed to send event %s: %s", event_type, e)

    def _is_cadence_mode(self) -> bool:
        return bool(self._context.get("cadence_verification_mode"))

    def _tool_definitions_for_run(self) -> List[Dict[str, Any]]:
        return select_tool_definitions(
            TOOL_DEFINITIONS,
            cadence_mode=self._is_cadence_mode(),
        )

    def _prepare_messages_for_llm(
        self, messages: List[Dict[str, Any]], max_output_tokens: int
    ) -> List[Dict[str, Any]]:
        per_msg_cap = (
            CADENCE_MAX_CHARS_PER_MESSAGE
            if self._is_cadence_mode()
            else 12_000
        )
        return prune_messages_for_context(
            messages,
            model=self._model or DEFAULT_MODEL,
            provider=PROVIDER,
            requested_output_tokens=max_output_tokens,
            max_chars_per_message=per_msg_cap,
        )

    async def _call_llm(self, messages: List[Dict[str, Any]]) -> ChatResponse:
        """Call the LLM with native function calling."""
        # Always use 'auto' — the system prompt enforces the Clarify/Plan/Execute
        # workflow. Forcing 'none' on iteration 1 would break Phase 3 when the
        # user's first message to a new engine instance is already 'go'.
        max_output_tokens = get_model_max_output_tokens(
            self._model or DEFAULT_MODEL,
            PROVIDER,
            DEFAULT_MAX_TOKENS,
        )
        prepared = self._prepare_messages_for_llm(messages, max_output_tokens)
        tools = self._tool_definitions_for_run()
        est_tokens = estimate_messages_tokens(prepared)
        logger.info(
            "Agentic LLM call: est_input_tokens=%d max_output=%d tools=%d cadence=%s",
            est_tokens,
            max_output_tokens,
            len(tools),
            self._is_cadence_mode(),
        )

        loop = asyncio.get_event_loop()

        def _invoke(msgs: List[Dict[str, Any]], out_tokens: int) -> ChatResponse:
            return chat_with_tools(
                messages=msgs,
                tools=tools,
                model=self._model,
                temperature=self._temperature,
                max_tokens=out_tokens,
                tool_choice="auto",
            )

        try:
            response = await loop.run_in_executor(
                None,
                lambda: _invoke(prepared, max_output_tokens),
            )
        except Exception as exc:
            if not is_context_length_error(exc):
                raise
            logger.warning(
                "Context overflow on LLM call — retrying with aggressive prune: %s",
                exc,
            )
            retry_output = max(1024, max_output_tokens // 2)
            retry_msgs = prune_messages_for_context(
                prepared,
                model=self._model or DEFAULT_MODEL,
                provider=PROVIDER,
                requested_output_tokens=retry_output,
                max_chars_per_message=2_000,
            )
            response = await loop.run_in_executor(
                None,
                lambda: _invoke(retry_msgs, retry_output),
            )
        self._record_token_usage(response.usage)
        return response

    @property
    def token_usage(self) -> TokenUsage:
        return self._token_usage

    def _record_token_usage(self, usage: Optional[TokenUsage]) -> None:
        if not usage:
            return
        self._token_usage.input_tokens += usage.input_tokens
        self._token_usage.output_tokens += usage.output_tokens
        self._token_usage.total_tokens += usage.total_tokens
        self._token_usage.provider = usage.provider or self._token_usage.provider
        self._token_usage.model = usage.model or self._token_usage.model
        self._token_usage.is_estimated = self._token_usage.is_estimated or usage.is_estimated
        calls = self._token_usage.details.setdefault("calls", [])
        calls.append(usage.to_dict())

    async def _stream_text(self, text: str):
        """Stream text content to frontend in chunks."""
        chunk_size = 180
        for i in range(0, len(text), chunk_size):
            if self._cancelled:
                break
            chunk = text[i : i + chunk_size]
            await self._send_event(EVENT_TEXT_DELTA, {"content": chunk})
            await asyncio.sleep(0.01)

    async def _execute_tool_with_retries(
        self, tool_call: ToolCall
    ) -> ToolExecutionResult:
        """Execute a tool call with retry logic and exponential backoff."""
        last_error = None

        for attempt in range(self._max_tool_retries):
            try:
                result = await asyncio.wait_for(
                    self._tool_registry.execute(tool_call.name, tool_call.arguments),
                    timeout=self._tool_timeout,
                )
                if result.success:
                    return result
                last_error = result.error
                from services.sv_lint.agent_gate import is_lint_failure_error

                if is_lint_failure_error(last_error):
                    return result
                if attempt < self._max_tool_retries - 1:
                    backoff = 2**attempt
                    logger.warning(
                        "Tool %s failed (attempt %d/%d): %s, retrying in %ds",
                        tool_call.name,
                        attempt + 1,
                        self._max_tool_retries,
                        last_error,
                        backoff,
                    )
                    await asyncio.sleep(backoff)
            except asyncio.TimeoutError:
                last_error = f"Timed out after {self._tool_timeout}s"
                if attempt < self._max_tool_retries - 1:
                    backoff = 2**attempt
                    logger.warning(
                        "Tool %s timed out (attempt %d/%d), retrying in %ds",
                        tool_call.name,
                        attempt + 1,
                        self._max_tool_retries,
                        backoff,
                    )
                    await asyncio.sleep(backoff)

        return ToolExecutionResult(
            success=False,
            error=last_error or "Unknown error",
            summary=f"Tool '{tool_call.name}' failed after {self._max_tool_retries} retries",
        )

    async def _resolve_args(
        self, tool_call: ToolCall
    ) -> Tuple[bool, dict, Optional[str]]:
        raw_args = tool_call.arguments
        if raw_args is None:
            return True, {}, None
        if isinstance(raw_args, dict):
            return True, raw_args, None
        if isinstance(raw_args, str):
            try:
                parsed = json.loads(raw_args)
                if isinstance(parsed, dict):
                    return True, parsed, None
                return False, {}, "Tool arguments must decode to an object"
            except Exception:
                return False, {}, "Tool arguments are not valid JSON"
        return False, {}, "Tool arguments must be an object"

    async def _run_before_tool_call(
        self,
        tool_call: ToolCall,
        args: dict,
    ) -> Optional[ToolExecutionResult]:
        if not self._before_tool_call:
            return None
        maybe = self._before_tool_call(
            tool_call=tool_call,
            args=args,
            context=self._context,
        )
        decision = await maybe if inspect.isawaitable(maybe) else maybe
        if isinstance(decision, ToolExecutionResult):
            return decision
        if isinstance(decision, dict) and decision.get("block"):
            return ToolExecutionResult(
                success=False,
                error=str(decision.get("error") or "Blocked by before_tool_call"),
                summary=str(decision.get("summary") or "Tool call blocked"),
            )
        return None

    async def _run_after_tool_call(
        self,
        tool_call: ToolCall,
        result: ToolExecutionResult,
    ) -> ToolExecutionResult:
        if not self._after_tool_call:
            return result
        maybe = self._after_tool_call(
            tool_call=tool_call,
            result=result,
            is_error=not result.success,
            context=self._context,
        )
        processed = await maybe if inspect.isawaitable(maybe) else maybe
        if isinstance(processed, ToolExecutionResult):
            return processed
        return result

    async def _finalize_tool_call(
        self,
        tool_call: ToolCall,
        result: ToolExecutionResult,
        messages: List[Dict[str, Any]],
    ):
        # Include result data so frontend can use artifact IDs from createFile, etc.
        _event_payload: dict = {
            "call_id": tool_call.id,
            "tool": tool_call.name,
            "result_summary": result.summary,
            "iteration": self._iteration_count,
        }
        if result.success and isinstance(result.result, dict):
            _event_payload["result"] = result.result
        elif isinstance(result.result, dict) and result.result.get("lint"):
            _event_payload["result"] = result.result
            _event_payload["lint"] = result.result.get("lint")
        await self._send_event(EVENT_TOOL_CALL_COMPLETED, _event_payload)

        if tool_call.name == "runSimulation" and result.success:
            run_id = ""
            if isinstance(result.result, dict):
                run_id = str(result.result.get("run_id") or "").strip()
            if run_id:
                self._link_run_to_task(run_id)
                self._sync_linked_project_task(status="in_verification", progress_pct=15)
                await self._emit_project_task_updated()

        tool_result_content = (
            json.dumps(result.result)
            if result.success
            else json.dumps({"error": result.error})
        )
        tool_result_content = truncate_tool_result_content(
            tool_call.name,
            tool_result_content,
            max_chars=DEFAULT_MAX_TOOL_RESULT_CHARS,
        )
        messages.append(
            {
                "role": "tool",
                "name": tool_call.name,
                "content": tool_result_content,
                "tool_call_id": tool_call.id,
            }
        )

        self._total_tool_calls += 1

    async def _execute_tool_calls_sequential(
        self,
        tool_calls: List[ToolCall],
    ) -> List[ExecutedToolCall]:
        executed: List[ExecutedToolCall] = []
        for tool_call in tool_calls:
            if self._cancelled:
                break

            ok, args, err = await self._resolve_args(tool_call)
            if not ok:
                executed.append(
                    ExecutedToolCall(
                        tool_call=tool_call,
                        result=ToolExecutionResult(
                            success=False,
                            error=err,
                            summary=f"Invalid arguments for '{tool_call.name}'",
                        ),
                    )
                )
                continue

            tool_call.arguments = args
            await self._send_event(
                EVENT_TOOL_CALL_STARTED,
                {
                    "call_id": tool_call.id,
                    "tool": tool_call.name,
                    "args": args,
                    "iteration": self._iteration_count,
                },
            )

            blocked_result = await self._run_before_tool_call(tool_call, args)
            if blocked_result is not None:
                final = await self._run_after_tool_call(tool_call, blocked_result)
                executed.append(ExecutedToolCall(tool_call=tool_call, result=final))
                continue

            result = await self._execute_tool_with_retries(tool_call)
            result = await self._run_after_tool_call(tool_call, result)
            executed.append(ExecutedToolCall(tool_call=tool_call, result=result))

        return executed

    async def _execute_tool_calls_parallel(
        self,
        tool_calls: List[ToolCall],
    ) -> List[ExecutedToolCall]:
        prepared: List[PreparedToolCall] = []
        executed: List[ExecutedToolCall] = []

        for tool_call in tool_calls:
            if self._cancelled:
                break

            ok, args, err = await self._resolve_args(tool_call)
            if not ok:
                executed.append(
                    ExecutedToolCall(
                        tool_call=tool_call,
                        result=ToolExecutionResult(
                            success=False,
                            error=err,
                            summary=f"Invalid arguments for '{tool_call.name}'",
                        ),
                    )
                )
                continue

            tool_call.arguments = args
            await self._send_event(
                EVENT_TOOL_CALL_STARTED,
                {
                    "call_id": tool_call.id,
                    "tool": tool_call.name,
                    "args": args,
                    "iteration": self._iteration_count,
                },
            )

            blocked_result = await self._run_before_tool_call(tool_call, args)
            if blocked_result is not None:
                final = await self._run_after_tool_call(tool_call, blocked_result)
                executed.append(ExecutedToolCall(tool_call=tool_call, result=final))
                continue

            prepared.append(PreparedToolCall(tool_call=tool_call, args=args))

        if prepared:
            tasks = [
                asyncio.create_task(self._execute_tool_with_retries(item.tool_call))
                for item in prepared
            ]
            raw_results = await asyncio.gather(*tasks, return_exceptions=True)

            for idx, raw in enumerate(raw_results):
                item = prepared[idx]
                if isinstance(raw, BaseException):
                    result = ToolExecutionResult(
                        success=False,
                        error=str(raw),
                        summary=f"Tool '{item.tool_call.name}' failed with exception",
                    )
                else:
                    result = raw
                result = await self._run_after_tool_call(item.tool_call, result)
                executed.append(
                    ExecutedToolCall(tool_call=item.tool_call, result=result)
                )

        return executed

    async def _handle_tool_calls(
        self,
        tool_calls: List[ToolCall],
        messages: List[Dict[str, Any]],
    ) -> bool:
        runnable_calls: List[ToolCall] = []

        for tool_call in tool_calls:
            if self._cancelled:
                break

            if tool_call.name == "attemptCompletion":
                await self._handle_attempt_completion(tool_call)
                return True

            if tool_call.name == "findTodos":
                await self._handle_find_todos(tool_call, messages)
                continue

            if tool_call.name == "mergeTodos":
                await self._handle_merge_todos(tool_call, messages)
                continue

            runnable_calls.append(tool_call)

        if not runnable_calls:
            return self._completed

        if self._tool_execution == "parallel" and len(runnable_calls) > 1:
            executed = await self._execute_tool_calls_parallel(runnable_calls)
        else:
            executed = await self._execute_tool_calls_sequential(runnable_calls)

        for item in executed:
            await self._finalize_tool_call(item.tool_call, item.result, messages)

        return self._completed

    async def _handle_find_todos(
        self, tool_call: ToolCall, messages: List[Dict[str, Any]]
    ):
        """Handle findTodos - dynamically discover new todo items."""
        todos_data = tool_call.arguments.get("todos", [])
        new_todos = []

        for td in todos_data:
            todo_id = td.get("id", f"todo_{uuid.uuid4().hex[:8]}")
            description = td.get("description", "Untitled task")
            todo_type = td.get("type", "other")

            if not any(t.id == todo_id for t in self._todos):
                new_todo = TodoItem(
                    id=todo_id,
                    description=description,
                    todo_type=todo_type,
                    status="pending",
                )
                self._todos.append(new_todo)
                new_todos.append(new_todo)

        await self._send_event(
            EVENT_TODOS_UPDATED,
            {
                "todos": [
                    {
                        "id": t.id,
                        "description": t.description,
                        "type": t.todo_type,
                        "status": t.status,
                    }
                    for t in self._todos
                ],
                "new_count": len(new_todos),
            },
        )
        progress = self._todo_progress_pct()
        self._sync_linked_project_task(
            progress_pct=progress if progress is not None else self._agent_progress_pct(),
        )
        await self._emit_project_task_updated()

        messages.append(
            {
                "role": "tool",
                "name": tool_call.name,
                "content": json.dumps({"success": True, "added": len(new_todos)}),
                "tool_call_id": tool_call.id,
            }
        )

    async def _handle_merge_todos(
        self, tool_call: ToolCall, messages: List[Dict[str, Any]]
    ):
        """Handle mergeTodos - update todo statuses."""
        completed_ids = set(tool_call.arguments.get("completed", []))
        failed_ids = set(tool_call.arguments.get("failed", []))

        updated = 0
        for todo in self._todos:
            if todo.id in completed_ids and todo.status != "completed":
                todo.status = "completed"
                todo.completed_at = datetime.now(timezone.utc)
                updated += 1
            elif todo.id in failed_ids and todo.status != "failed":
                todo.status = "failed"
                todo.completed_at = datetime.now(timezone.utc)
                updated += 1

        await self._send_event(
            EVENT_TODOS_UPDATED,
            {
                "todos": [
                    {
                        "id": t.id,
                        "description": t.description,
                        "type": t.todo_type,
                        "status": t.status,
                    }
                    for t in self._todos
                ],
                "updated_count": updated,
            },
        )
        progress = self._todo_progress_pct()
        self._sync_linked_project_task(
            progress_pct=progress if progress is not None else self._agent_progress_pct(),
        )
        await self._emit_project_task_updated()

        messages.append(
            {
                "role": "tool",
                "name": tool_call.name,
                "content": json.dumps({"success": True, "updated": updated}),
                "tool_call_id": tool_call.id,
            }
        )

    async def _handle_attempt_completion(self, tool_call: ToolCall):
        """Handle attemptCompletion - finalize the conversation."""
        summary = tool_call.arguments.get("summary", "")
        command = tool_call.arguments.get("command", "")

        self._completed = True
        self._final_summary = summary
        self._final_command = command

        await self._send_event(
            EVENT_CONVERSATION_ENDED, {"summary": summary, "command": command}
        )
        self._sync_linked_project_task(completed=True)
        await self._emit_project_task_updated()

        self._save_tool_message(
            "attemptCompletion",
            ToolExecutionResult(
                success=True,
                result={"summary": summary, "command": command},
                summary=summary,
            ),
        )

    async def _force_complete_with_summary(self):
        """Force completion with an auto-generated summary."""
        if self._final_summary:
            return

        messages = list(self._conversation_history)
        messages.append(
            {
                "role": "system",
                "content": (
                    "Summarize everything that was accomplished in this conversation. "
                    "Be concise but thorough. Call attemptCompletion with your summary."
                ),
            }
        )

        response = await self._call_llm(messages)

        if response.tool_calls:
            for tc in response.tool_calls:
                if tc.name == "attemptCompletion":
                    await self._handle_attempt_completion(tc)
                    return

        self._completed = True
        self._final_summary = response.content or "Conversation completed."
        await self._send_event(
            EVENT_CONVERSATION_ENDED, {"summary": self._final_summary}
        )
        self._sync_linked_project_task(progress_pct=self._agent_progress_pct())
        await self._emit_project_task_updated()

    async def _send_final_summary(self):
        """Send the final summary + done event to the frontend.
        Always includes the accumulated assistant text so the frontend
        can use it as a fallback if delta streaming was missed."""
        payload: dict = {"status": "completed"}
        if self._final_summary:
            await self._send_event(EVENT_SUMMARY, {"content": self._final_summary})
            payload["text"] = self._final_summary
        await self._send_event(EVENT_DONE, payload)

    def _save_tool_message(self, tool_name: str, result: ToolExecutionResult):
        """Persist a tool result message to the database."""
        try:
            tool_msg = ChatMessage(
                id=str(uuid.uuid4()),
                thread_id=self._thread_id,
                role="tool",
                content=json.dumps(
                    {
                        "tool": tool_name,
                        "success": result.success,
                        "result": result.result,
                        "error": result.error,
                        "summary": result.summary,
                    }
                ),
            )
            self._db.add(tool_msg)

            thread = (
                self._db.query(ChatThread)
                .filter(ChatThread.id == self._thread_id)
                .first()
            )
            if thread:
                thread.updated_at = datetime.now(timezone.utc)
                self._db.add(thread)
        except Exception as e:
            logger.warning("Failed to save tool message: %s", e)
