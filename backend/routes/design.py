"""
Design Router — RTL Designer Integration
=========================================
Exposes the RTL_designer LangGraph pipeline as REST API endpoints so
the verification backend can trigger design generation independently
or as part of a "design → verify" flow.

Endpoints:
  POST /design/generate        Start design generation (returns design_id)
  GET  /design/{design_id}     Poll status of a design generation job
  GET  /design/{design_id}/stream  SSE stream of agent node events
  POST /design/{design_id}/send-to-verification  Push RTL code into a new verification run
  GET  /design/list            List recent design jobs (paginated)
"""

from __future__ import annotations

import os
import re
import sys
import uuid
import asyncio
import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import AsyncGenerator

from fastapi import APIRouter, HTTPException, Depends, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

# ── Add RTL_designer to sys.path so its modules are importable ──────────
import logging as _logging

_log = _logging.getLogger(__name__)


def _find_rtl_designer_dir() -> Path:
    """Find the RTL_designer directory using multiple strategies."""
    candidates = [
        # Strategy 1: sibling of backend/ (project root level) — our placement
        Path(__file__).resolve().parents[1].parent / "RTL_designer",
        # Strategy 2: relative to CWD (works when running from project root)
        Path.cwd() / "RTL_designer",
        # Strategy 3: relative to this file going up 2 levels (backend/routes -> backend -> project_root)
        Path(__file__).resolve().parents[2] / "RTL_designer",
        # Strategy 4: absolute Docker path
        Path("/app/RTL_designer"),
    ]
    for p in candidates:
        if (p / "agents" / "graph.py").exists():
            _log.info(f"[design] RTL_designer found at: {p}")
            return p
    # Return best guess even if not found so error is clear
    best = candidates[0]
    _log.warning(
        f"[design] RTL_designer NOT found. Tried: {[str(c) for c in candidates]}. Using: {best}"
    )
    return best


_RTL_DIR = _find_rtl_designer_dir()
if str(_RTL_DIR) not in sys.path:
    sys.path.insert(0, str(_RTL_DIR))
    _log.info(f"[design] Added to sys.path: {_RTL_DIR}")

from database.database import SessionLocal
from database.models import Run, RunEvent, Project, Organization, User


router = APIRouter(prefix="/design", tags=["RTL Designer"])


# ── In-memory design job store (augments DB for streaming) ─────────────
# { design_id: { "status": str, "rtl_code": str, "events": [...] } }
_design_jobs: dict[str, dict] = {}
_design_jobs_lock = threading.Lock()


# ── Pydantic schemas ────────────────────────────────────────────────────


class DesignRequest(BaseModel):
    prompt: str = Field(..., description="Natural language RTL design request")
    language: str = Field("systemverilog", description="'verilog' or 'systemverilog'")
    # None → resolver picks GEMINI_MODEL / MODEL_NAME / known-good fallback.
    # Hard-coding a model alias here was the prior 404 source.
    model: str | None = Field(None, description="Model to use (None = resolver default)")
    project_id: str | None = Field(
        None, description="Optional project to attach design to"
    )
    provider: str | None = Field(
        None, description="LLM provider: gemini, nim, openai, local"
    )


class DesignStatusResponse(BaseModel):
    design_id: str
    status: str  # planning | decomposing | coding | composing | reviewing | complete | error
    rtl_code: str
    spec: str
    review: str
    created_at: str
    events: list[dict]
    file_paths: list[str]


class SendToVerificationRequest(BaseModel):
    project_id: str
    rtl_code: str | None = Field(
        None, description="Override RTL code (defaults to generated code)"
    )
    strategy: str = Field(
        "uvm", description="Verification strategy: 'uvm' | 'directed' | 'random'"
    )
    description: str | None = Field(
        None, description="User-provided description override"
    )
    enable_coverage: bool = Field(True, description="Enable coverage analysis")
    enable_assertions: bool = Field(True, description="Enable SVA assertions")


class VerifyNowRequest(BaseModel):
    rtl_code: str = Field(..., min_length=4, description="RTL source code to verify")
    project_id: str = Field(..., description="Project UUID to associate the run with")
    description: str | None = Field(
        None, description="Short description of the design being verified"
    )
    strategy: str = Field(
        "uvm", description="Verification strategy: 'uvm' | 'directed' | 'random'"
    )
    enable_coverage: bool = Field(True, description="Enable coverage analysis")
    enable_assertions: bool = Field(True, description="Enable SVA assertions")
    language: str = Field(
        "systemverilog", description="RTL language: 'verilog' | 'systemverilog'"
    )


class VerifyPreviewResponse(BaseModel):
    design_id: str
    rtl_code: str
    spec: str
    prompt: str
    language: str
    line_count: int
    estimated_modules: int
    strategy_options: list[dict]


def _get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@router.post("/setup/default-project", status_code=200)
async def ensure_default_project(db: Session = Depends(_get_db)):
    """
    One-click setup: creates a default user + org + project if none exist.
    Returns the project_id ready to use in send-to-verification.
    Works without authentication.
    """
    import uuid as _uuid
    from passlib.context import CryptContext

    _pwd = CryptContext(schemes=["pbkdf2_sha256"], deprecated="auto")

    existing_project = db.query(Project).first()
    if existing_project:
        return {
            "project_id": existing_project.id,
            "project_name": existing_project.name,
            "created": False,
            "message": "Using existing project",
        }

    user_id = str(_uuid.uuid4())
    org_id = str(_uuid.uuid4())
    proj_id = str(_uuid.uuid4())

    db.add(
        User(
            id=user_id,
            email="studio@chipix.local",
            password_hash=_pwd.hash("chipix-studio-2024"),
            full_name="Chipix Studio",
            is_active=True,
            default_organization_id=org_id,
        )
    )
    db.add(
        Organization(
            id=org_id,
            owner_user_id=user_id,
            name="Chipix Default Org",
            slug="chipix-default-org",
        )
    )
    db.add(
        Project(
            id=proj_id,
            organization_id=org_id,
            owner_user_id=user_id,
            name="AI Studio Project",
            slug="ai-studio-project",
            description="Default project for RTL Designer → Verification",
            status="active",
        )
    )
    db.commit()
    return {
        "project_id": proj_id,
        "project_name": "AI Studio Project",
        "created": True,
        "message": "Default project created successfully",
    }


@router.get("/setup/projects")
async def list_all_projects(db: Session = Depends(_get_db)):
    """List all projects — no auth required, for the dashboard project picker."""
    return [
        {"id": p.id, "name": p.name, "description": p.description}
        for p in db.query(Project).filter(Project.status == "active").all()
    ]


# ── Helpers ─────────────────────────────────────────────────────────────


def _push_event(
    design_id: str,
    node: str,
    message: str,
    status: str | None = None,
    reasoning: str | None = None,
    content: str | None = None,
    step: str | None = None,
    progress: int | None = None,
):
    """Push an SSE-style event into the job's event list."""
    event = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "node": node,
        "message": message,
    }
    if reasoning:
        event["reasoning"] = reasoning
    if content:
        event["content"] = content
    if step:
        event["step"] = step
    if progress is not None:
        event["progress"] = progress
    with _design_jobs_lock:
        job = _design_jobs.get(design_id)
        if job is not None:
            job["events"].append(event)
            if status:
                job["status"] = status


def _gemini_call(
    model_name: str, system_prompt: str, user_message: str
) -> tuple[str, str]:
    """
    Call the configured LLM provider (Gemini, NVIDIA NIM, or any OpenAI-compatible endpoint).
    Returns tuple of (content, reasoning) where reasoning may be empty for non-thinking models.
    """
    from llm_provider import generate as llm_generate, PROVIDER

    return llm_generate(
        system_prompt=system_prompt,
        user_message=user_message,
        model=model_name,
        provider=PROVIDER,
    )


def _extract_code(text: str) -> str:
    """Extract code from markdown code fences."""
    import re

    # Try to find a fenced block
    match = re.search(r"```(?:verilog|systemverilog|sv|v)?\s*([\s\S]+?)```", text)
    if match:
        return match.group(1).strip()
    # Try any code block
    match = re.search(r"```([\s\S]+?)```", text)
    if match:
        return match.group(1).strip()
    return text.strip()


def _run_design_pipeline(
    design_id: str,
    prompt: str,
    language: str,
    model: str,
    output_dir: Path,
    provider: str | None = None,
):
    """
    Run the RTL design pipeline using the configured LLM provider.
    5 stages: Plan → Code → Review  (or Plan → Decompose → Code × N → Compose → Review)
    Streams node-level events into _design_jobs[design_id]["events"].
    """
    from llm_provider import generate as llm_generate, PROVIDER as DEFAULT_PROVIDER

    active_provider = provider or DEFAULT_PROVIDER
    # Inline prompts so this function has zero external deps beyond the Gemini SDK
    PLANNER_PROMPT = """You are an expert RTL Design Architect. Analyze the user's hardware design request and produce a precise, structured specification.

Output format:
### Module Name
### Parameters
### Ports (Direction | Width | Name | Description table)
### Functional Description
### Architecture Notes
### Complexity Estimate
State: `COMPLEXITY: SMALL|MEDIUM|LARGE|VERY_LARGE`

Rules: Be precise with bit widths. Default to synchronous reset. Keep it synthesizable."""

    lang_label = "Verilog" if language == "verilog" else "SystemVerilog"
    lang_block = "verilog" if language == "verilog" else "systemverilog"

    CODER_PROMPT = f"""You are an expert RTL designer writing synthesizable {lang_label}. Generate production-quality, synthesizable code from the specification. Output ONLY the code inside a ```{lang_block} ... ``` block. No explanation outside the code block."""

    REVIEWER_PROMPT = """You are a senior RTL verification engineer reviewing RTL code. Check: functional correctness, synthesizability, reset logic, latch prevention, naming conventions. 

Output EXACTLY:
### Decision: PASS or REVISE
### Summary
### Issues Found
### Suggested Fixes (if REVISE)

Only mark REVISE for CRITICAL/MAJOR issues."""

    try:
        # ── Stage 1: Planner ──────────────────────────────────────────
        _push_event(
            design_id,
            "planner",
            "📋 Generating RTL specification…",
            status="planning",
            step="planning",
            progress=10,
        )

        spec, reasoning = _gemini_call(
            model,
            PLANNER_PROMPT,
            f"Design request (target language: {lang_label}):\n\n{prompt}",
        )

        if reasoning:
            _push_event(
                design_id,
                "planner",
                "🧠 Analyzing requirements…",
                reasoning=reasoning,
                step="planning",
                progress=15,
            )

        with _design_jobs_lock:
            if design_id in _design_jobs:
                _design_jobs[design_id]["spec"] = (
                    spec[0] if isinstance(spec, tuple) else spec
                )

        # Detect complexity to decide if hierarchical needed
        complexity_match = re.search(
            r"\bCOMPLEXITY\s*:\s*(VERY[\s_-]*LARGE|LARGE|MEDIUM|SMALL)\b",
            spec,
            flags=re.IGNORECASE,
        )
        complexity = (
            re.sub(r"[\s-]+", "_", complexity_match.group(1).strip().lower())
            if complexity_match
            else "small"
        )
        is_large = complexity in {"large", "very_large"}
        spec_preview = spec[:200].replace("\n", " ")
        _push_event(
            design_id,
            "planner",
            f"📋 Spec ready — {'hierarchical' if is_large else 'single-module'} design — {spec_preview}…",
            content=spec,
            step="planning",
            progress=25,
        )

        # ── Stage 2: Decomposer (large designs only) ──────────────────
        rtl_code = ""
        if is_large:
            _push_event(
                design_id,
                "decomposer",
                "🔀 Decomposing into submodules…",
                status="decomposing",
            )

            DECOMPOSER_PROMPT = """You are an RTL architect. Break this large design into independent submodules (<400 lines each).

For each submodule output:
---SUBMODULE---
### Module Name: <name>
### Ports
### Functional Description
---END_SUBMODULE---

Last submodule must be the top-level that instantiates all others. Order: leaf→top."""

            decomp, decomp_reasoning = _gemini_call(
                model, DECOMPOSER_PROMPT, f"Specification:\n\n{spec}"
            )
            if decomp_reasoning:
                _push_event(
                    design_id,
                    "decomposer",
                    "🧠 Decomposing design…",
                    reasoning=decomp_reasoning,
                    step="decomposing",
                    progress=35,
                )
            marker_re = re.compile(
                r"^\s*(?:`{3}[a-zA-Z0-9_-]*\s*)?[-*_]{3,}\s*SUBMODULE"
                r"\s*[-*_]{0,}\s*(?:`{3})?\s*$",
                flags=re.IGNORECASE | re.MULTILINE,
            )
            end_marker_re = re.compile(
                r"^\s*(?:`{3}[a-zA-Z0-9_-]*\s*)?[-*_]{3,}\s*END[\s_-]*SUBMODULE"
                r"\s*[-*_]{0,}\s*(?:`{3})?\s*$",
                flags=re.IGNORECASE | re.MULTILINE,
            )
            chunks = marker_re.split(decomp)
            submodule_specs = []
            seen_names = set()
            for chunk_index, chunk in enumerate(chunks, start=1):
                chunk = end_marker_re.sub("", chunk).strip()
                name_m = re.search(
                    r"###\s*Module Name\s*:\s*([^\r\n]+)",
                    chunk,
                    flags=re.IGNORECASE,
                )
                if not name_m:
                    continue
                raw_name = re.sub(r"[`*#]", "", name_m.group(1).strip())
                clean_name_m = re.search(r"[A-Za-z_][A-Za-z0-9_]*", raw_name)
                name = (
                    clean_name_m.group(0)
                    if clean_name_m
                    else f"submodule_{chunk_index}"
                )
                if name in seen_names:
                    suffix = 2
                    unique_name = f"{name}_{suffix}"
                    while unique_name in seen_names:
                        suffix += 1
                        unique_name = f"{name}_{suffix}"
                    name = unique_name
                seen_names.add(name)
                submodule_specs.append({"name": name, "spec": chunk, "code": ""})

            if not submodule_specs:
                spec_str = spec[0] if isinstance(spec, tuple) else spec
                submodule_specs = [{"name": "top_module", "spec": spec_str, "code": ""}]

            _push_event(
                design_id,
                "decomposer",
                f"🔀 Split into {len(submodule_specs)} submodules: {', '.join(s['name'] for s in submodule_specs[:4])}",
                step="decomposing",
                progress=45,
            )

            # ── Stage 3: Coder (per submodule) ────────────────────────
            all_code_parts = []
            for i, sub in enumerate(submodule_specs):
                _push_event(
                    design_id,
                    "coder",
                    f"💻 Coding submodule {i + 1}/{len(submodule_specs)}: {sub['name']}…",
                    status="coding",
                    step="coding",
                    progress=50 + int(15 * i / len(submodule_specs)),
                )
                sub_code, sub_reasoning = _gemini_call(
                    model,
                    CODER_PROMPT,
                    f"Generate ONLY this submodule ({sub['name']}):\n\n{sub['spec']}",
                )
                if sub_reasoning:
                    _push_event(
                        design_id,
                        "coder",
                        "🧠 Generating code…",
                        reasoning=sub_reasoning,
                        step="coding",
                    )
                sub["code"] = sub_code
                clean = _extract_code(sub_code)
                all_code_parts.append(
                    f"// {'=' * 60}\n// Module: {sub['name']}\n// {'=' * 60}\n\n{clean}"
                )

            # ── Stage 4: Composer ─────────────────────────────────────
            _push_event(
                design_id,
                "composer",
                "🔗 Composing all submodules…",
                status="composing",
                step="composing",
                progress=70,
            )
            COMPOSER_PROMPT = f"""You are an RTL integration engineer. Combine these submodules into one clean file. Order: leaf first, top-level last. Fix port mismatches. Output ONLY code in ```{lang_block} ... ``` block."""
            combined_input = "\n\n".join(all_code_parts)
            rtl_code, composer_reasoning = _gemini_call(
                model,
                COMPOSER_PROMPT,
                (
                    "Use the original specification as the source of truth for "
                    "hierarchy, port names, widths, and connection intent.\n\n"
                    f"Original specification:\n{spec}\n\n"
                    f"Combine these submodules:\n\n```\n{combined_input}\n```"
                ),
            )
            if composer_reasoning:
                _push_event(
                    design_id,
                    "composer",
                    "🧠 Composing modules…",
                    reasoning=composer_reasoning,
                    step="composing",
                    progress=75,
                )
        else:
            # ── Stage 2B: Single-module coder ────────────────────────
            _push_event(
                design_id,
                "coder",
                "💻 Generating RTL code…",
                status="coding",
                step="coding",
                progress=35,
            )
            rtl_code, coder_reasoning = _gemini_call(
                model,
                CODER_PROMPT,
                f"Generate RTL based on this specification:\n\n{spec}",
            )
            if coder_reasoning:
                _push_event(
                    design_id,
                    "coder",
                    "🧠 Writing RTL…",
                    reasoning=coder_reasoning,
                    step="coding",
                    progress=45,
                )
            _push_event(
                design_id,
                "coder",
                f"💻 RTL generated — {_extract_code(rtl_code)[:100].replace(chr(10), ' ')}…",
                content=rtl_code,
                step="coding",
                progress=50,
            )

        # ── Stage 5: Reviewer ─────────────────────────────────────────
        _push_event(
            design_id,
            "reviewer",
            "🔍 Reviewing generated code…",
            status="reviewing",
            step="reviewing",
            progress=60,
        )
        clean_rtl = _extract_code(rtl_code)
        review, review_reasoning = _gemini_call(
            model,
            REVIEWER_PROMPT,
            f"Specification:\n\n{spec}\n\nCode:\n\n```\n{clean_rtl}\n```",
        )
        if review_reasoning:
            _push_event(
                design_id,
                "reviewer",
                "🧠 Analyzing code…",
                reasoning=review_reasoning,
                step="reviewing",
                progress=70,
            )
        decision = "pass" if "DECISION: PASS" in review.upper() else "revise"
        _push_event(
            design_id,
            "reviewer",
            f"🔍 Review: {decision.upper()} — {review[:120]}…",
            content=review,
            step="reviewing",
            progress=75,
        )

        # If revision needed, do one more coding pass
        if decision == "revise":
            _push_event(
                design_id,
                "coder",
                "🔄 Applying reviewer fixes…",
                status="coding",
                step="coding",
                progress=80,
            )
            rtl_code, revise_reasoning = _gemini_call(
                model,
                CODER_PROMPT,
                f"Spec:\n{spec}\n\nPrevious code:\n```\n{clean_rtl}\n```\n\nReviewer feedback:\n{review}\n\nFix ALL issues.",
            )
            if revise_reasoning:
                _push_event(
                    design_id,
                    "coder",
                    "🧠 Applying fixes…",
                    reasoning=revise_reasoning,
                    step="coding",
                    progress=85,
                )
            clean_rtl = _extract_code(rtl_code)

        # ── Finalise ─────────────────────────────────────────────────
        # Write files to output directory
        file_paths: list[str] = []
        try:
            # These imports are only needed if we actually write files, so keep them here
            from rtl_utils.helpers import extract_module_name
            from rtl_utils.file_manager import (
                create_project_workspace,
                auto_write_all_files,
            )

            module_name = extract_module_name(clean_rtl) or f"design_{design_id[:8]}"
            ws = create_project_workspace(str(output_dir), module_name)
            spec_text = spec
            actions = auto_write_all_files(ws, clean_rtl, spec_text, language)
            file_paths = [a.filepath for a in actions]
        except Exception as fe:
            _push_event(design_id, "file_writer", f"⚠️ File write warning: {fe}")

        with _design_jobs_lock:
            if design_id in _design_jobs:
                _design_jobs[design_id].update(
                    {
                        "status": "complete",
                        "rtl_code": clean_rtl,
                        "spec": spec[0] if isinstance(spec, tuple) else spec,
                        "review": review[0] if isinstance(review, tuple) else review,
                        "file_paths": file_paths,
                    }
                )

        _push_event(
            design_id, "complete", "✅ Design pipeline complete!", status="complete"
        )

    except Exception as exc:
        import traceback

        tb = traceback.format_exc()
        _log.error(f"[design] Pipeline error for {design_id}: {tb}")
        _push_event(design_id, "error", f"❌ Pipeline error: {exc}", status="error")
        with _design_jobs_lock:
            if design_id in _design_jobs:
                _design_jobs[design_id]["status"] = "error"
                _design_jobs[design_id]["error"] = str(exc)


# ── Routes ───────────────────────────────────────────────────────────────


@router.post("/generate", response_model=dict, status_code=202)
async def generate_design(req: DesignRequest):
    """
    Start an RTL design generation job.
    Returns immediately with a design_id; poll /design/{design_id} for results.
    """
    # Validate provider configuration
    from llm_provider import validate_provider

    valid, msg = validate_provider()
    if not valid and not req.provider:
        raise HTTPException(
            status_code=503,
            detail=f"RTL Designer requires a configured LLM provider. {msg}. Set MODEL_PROVIDER, GOOGLE_API_KEY, or NIM_API_KEY in .env",
        )

    # Set provider env var if provided in request
    if req.provider:
        os.environ["MODEL_PROVIDER"] = req.provider

    design_id = str(uuid.uuid4())
    output_dir = _RTL_DIR / "output"
    output_dir.mkdir(parents=True, exist_ok=True)

    with _design_jobs_lock:
        _design_jobs[design_id] = {
            "design_id": design_id,
            "status": "planning",
            "rtl_code": "",
            "spec": "",
            "review": "",
            "events": [],
            "file_paths": [],
            "created_at": datetime.now(timezone.utc).isoformat(),
            "prompt": req.prompt,
            "language": req.language,
            "project_id": req.project_id,
            "provider": req.provider,
        }

    # Run pipeline in a background thread (not async-native due to LangGraph sync API)
    thread = threading.Thread(
        target=_run_design_pipeline,
        args=(design_id, req.prompt, req.language, req.model, output_dir, req.provider),
        daemon=True,
    )
    thread.start()

    return {
        "design_id": design_id,
        "status": "planning",
        "message": "Design generation started",
        "poll_url": f"/design/{design_id}",
        "stream_url": f"/design/{design_id}/stream",
    }


@router.get("/{design_id}", response_model=DesignStatusResponse)
async def get_design_status(design_id: str):
    """Poll the status of a design generation job."""
    with _design_jobs_lock:
        job = _design_jobs.get(design_id)

    if not job:
        raise HTTPException(
            status_code=404, detail=f"Design job '{design_id}' not found"
        )

    return DesignStatusResponse(
        design_id=design_id,
        status=job["status"],
        rtl_code=job.get("rtl_code", ""),
        spec=job.get("spec", ""),
        review=job.get("review", ""),
        created_at=job.get("created_at", ""),
        events=job.get("events", []),
        file_paths=job.get("file_paths", []),
    )


@router.get("/{design_id}/stream")
async def stream_design_events(design_id: str, request: Request):
    """
    SSE stream of design pipeline events.
    Connect and receive real-time node updates as `data: <json>` lines.
    """
    with _design_jobs_lock:
        if design_id not in _design_jobs:
            raise HTTPException(
                status_code=404, detail=f"Design job '{design_id}' not found"
            )

    async def event_generator() -> AsyncGenerator[str, None]:
        last_idx = 0
        while True:
            # Client disconnected?
            if await request.is_disconnected():
                break

            with _design_jobs_lock:
                job = _design_jobs.get(design_id, {})
                events = job.get("events", [])
                status = job.get("status", "")

            # Emit new events
            for event in events[last_idx:]:
                payload = json.dumps(event)
                yield f"data: {payload}\n\n"
            last_idx = len(events)

            # Emit heartbeat every 2s to keep connection alive
            yield ": heartbeat\n\n"

            # Stop streaming when terminal state reached
            if status in ("complete", "error"):
                yield f"data: {json.dumps({'node': '__done__', 'status': status})}\n\n"
                break

            await asyncio.sleep(1.0)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/{design_id}/verify-preview")
async def get_verify_preview(design_id: str):
    """
    Return pre-filled verification configuration based on the RTL design output.
    Frontend uses this to populate the verification setup form before sending.
    """
    with _design_jobs_lock:
        job = _design_jobs.get(design_id)

    if not job:
        raise HTTPException(
            status_code=404, detail=f"Design job '{design_id}' not found"
        )

    rtl_code = job.get("rtl_code", "")
    line_count = len(rtl_code.splitlines()) if rtl_code else 0

    # Estimate module count from RTL
    module_count = rtl_code.lower().count("\nmodule ") + rtl_code.lower().count(
        "\nendmodule"
    )
    estimated_modules = max(1, module_count // 2)

    return {
        "design_id": design_id,
        "rtl_code": rtl_code[:500]
        + ("..." if len(rtl_code) > 500 else ""),  # preview only
        "spec": job.get("spec", ""),
        "prompt": job.get("prompt", ""),
        "language": job.get("language", "systemverilog"),
        "line_count": line_count,
        "estimated_modules": estimated_modules,
        "status": job.get("status"),
        "strategy_options": [
            {
                "id": "uvm",
                "label": "UVM Testbench",
                "desc": "Full Universal Verification Methodology with agents, scoreboards, coverage",
                "recommended": True,
            },
            {
                "id": "directed",
                "label": "Directed Testing",
                "desc": "Manual test vectors targeting specific behaviors",
                "recommended": False,
            },
            {
                "id": "random",
                "label": "Constrained Random",
                "desc": "Randomized stimulus with functional coverage",
                "recommended": False,
            },
        ],
    }


@router.post("/{design_id}/send-to-verification", status_code=202)
async def send_to_verification(
    design_id: str,
    req: SendToVerificationRequest,
    db: Session = Depends(_get_db),
):
    """
    Take the RTL output from a completed design job and create a
    new verification run in the ChipVerify pipeline.

    This is the KEY integration bridge: Designer → Verifier.
    Requires no authentication — project must exist in DB.
    """
    with _design_jobs_lock:
        job = _design_jobs.get(design_id)

    if not job:
        raise HTTPException(
            status_code=404, detail=f"Design job '{design_id}' not found"
        )

    if job["status"] not in ("complete",):
        raise HTTPException(
            status_code=409,
            detail=f"Design job not complete yet (status: {job['status']})",
        )

    rtl_code = req.rtl_code or job.get("rtl_code", "")
    if not rtl_code.strip():
        raise HTTPException(status_code=422, detail="No RTL code available to verify")

    # Find the project (and its owner for required FK fields)
    from database.models import Project, User

    project = db.query(Project).filter(Project.id == req.project_id).first()
    if not project:
        raise HTTPException(
            status_code=404, detail=f"Project '{req.project_id}' not found"
        )

    # Build prompt_text from design metadata
    design_prompt = job.get("prompt", "")
    description = req.description or design_prompt
    strategy_tag = f"[strategy:{req.strategy}]"
    coverage_tag = (
        "[coverage:enabled]" if req.enable_coverage else "[coverage:disabled]"
    )
    assertions_tag = (
        "[assertions:enabled]" if req.enable_assertions else "[assertions:disabled]"
    )
    prompt_text = f"{description} {strategy_tag} {coverage_tag} {assertions_tag}"

    # Create a new Run — populate ALL NOT NULL fields
    run_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)

    new_run = Run(
        id=run_id,
        organization_id=project.organization_id,  # borrow from project
        project_id=req.project_id,
        user_id=project.owner_user_id,  # borrow from project owner
        prompt_text=prompt_text,
        specification_type="rtl_designer",
        status="queued",
        created_at=now,
        rtl_snapshot=rtl_code,
        spec_snapshot=job.get("spec", ""),
        source="rtl_designer",
    )
    db.add(new_run)

    # Traceability events
    for seq, (phase, level, msg) in enumerate(
        [
            ("intake", "info", f"Run created from RTL Designer job {design_id[:8]}..."),
            ("intake", "info", f"Design prompt: {design_prompt[:120]}"),
            (
                "intake",
                "info",
                f"Strategy: {req.strategy} | Coverage: {req.enable_coverage} | Assertions: {req.enable_assertions}",
            ),
            (
                "intake",
                "info",
                f"RTL: {len(rtl_code.splitlines())} lines, language: {job.get('language', 'systemverilog')}",
            ),
        ],
        start=1,
    ):
        db.add(
            RunEvent(
                id=str(uuid.uuid4()),
                run_id=run_id,
                seq_no=seq,
                event_kind="log",
                phase=phase,
                level=level,
                message=msg,
            )
        )

    db.commit()

    return {
        "run_id": run_id,
        "design_id": design_id,
        "project_id": req.project_id,
        "organization_id": project.organization_id,
        "status": "queued",
        "strategy": req.strategy,
        "rtl_lines": len(rtl_code.splitlines()),
        "message": f"Verification run queued with {req.strategy} strategy. POST /design/runs/{run_id}/start to begin.",
        "start_url": f"/design/runs/{run_id}/start",
    }


@router.get("/list/recent", response_model=list[dict])
async def list_recent_designs(limit: int = 20):
    """List the most recently created design jobs (in-memory)."""
    with _design_jobs_lock:
        jobs = list(_design_jobs.values())

    jobs.sort(key=lambda j: j.get("created_at", ""), reverse=True)
    return [
        {
            "design_id": j["design_id"],
            "status": j["status"],
            "prompt": (j.get("prompt", "") or "")[:100],
            "language": j.get("language", ""),
            "created_at": j.get("created_at", ""),
            "file_count": len(j.get("file_paths", [])),
        }
        for j in jobs[:limit]
    ]


# ── File Generation & Download ────────────────────────────────────────────────


@router.post("/{design_id}/files", status_code=200)
async def generate_project_files(design_id: str):
    """
    Generate downloadable project files from a completed design.
    Creates: rtl/<module>.v, tb/tb_<module>.v, README.md, project.json
    """
    import tempfile, re as _re

    try:
        from rtl_utils.file_manager import (
            create_project_workspace,
            auto_write_all_files,
            format_file_tree,
        )
    except ImportError:
        # Fall back to RTL_designer path
        sys.path.insert(0, str(_RTL_DIR / "rtl_utils"))
        from file_manager import (
            create_project_workspace,
            auto_write_all_files,
            format_file_tree,
        )

    with _design_jobs_lock:
        job = _design_jobs.get(design_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Design '{design_id}' not found")
    if job.get("status") != "complete":
        raise HTTPException(
            status_code=400, detail="Design generation not complete yet"
        )

    rtl_code = job.get("rtl_code", "")
    spec = job.get("spec", "")
    language = job.get("language", "verilog")
    prompt = job.get("prompt", "design")

    if not rtl_code:
        raise HTTPException(
            status_code=400, detail="No RTL code available for this design"
        )

    out_dir = job.get("output_dir") or tempfile.mkdtemp(
        prefix=f"chipix_{design_id[:8]}_"
    )
    proj_name = _re.sub(r"[^a-z0-9_]", "_", prompt[:40].lower().strip())
    proj_name = _re.sub(r"_+", "_", proj_name).strip("_") or "rtl_design"

    workspace = create_project_workspace(out_dir, proj_name)
    actions = auto_write_all_files(workspace, rtl_code, spec, language)
    file_tree = format_file_tree(workspace)

    with _design_jobs_lock:
        _design_jobs[design_id]["output_dir"] = out_dir
        _design_jobs[design_id]["file_paths"] = [a.filepath for a in actions]
        _design_jobs[design_id]["workspace"] = workspace.to_dict()

    return {
        "design_id": design_id,
        "project_name": proj_name,
        "output_dir": out_dir,
        "file_tree": file_tree,
        "files": [
            {
                "filename": a.filename,
                "lines": a.lines,
                "type": (
                    "testbench"
                    if a.filename.startswith("tb_")
                    else (
                        "rtl"
                        if a.filename.endswith((".v", ".sv"))
                        else "docs" if a.filename.endswith(".md") else "manifest"
                    )
                ),
            }
            for a in actions
        ],
    }


@router.get("/{design_id}/files/{filename}")
async def download_project_file(design_id: str, filename: str):
    """Download an individual generated project file by name."""
    from fastapi.responses import FileResponse
    import mimetypes

    with _design_jobs_lock:
        job = _design_jobs.get(design_id)
    if not job or not job.get("file_paths"):
        raise HTTPException(
            status_code=404, detail="Files not generated yet — POST /files first"
        )
    for fp in job["file_paths"]:
        if Path(fp).name == filename:
            if not Path(fp).exists():
                raise HTTPException(
                    status_code=404, detail=f"File '{filename}' missing on disk"
                )
            mime, _ = mimetypes.guess_type(fp)
            return FileResponse(fp, filename=filename, media_type=mime or "text/plain")
    raise HTTPException(status_code=404, detail=f"File '{filename}' not in this design")


# ── Verification Run Management (no-auth, design-router bridge) ─────────────


@router.post("/runs/{run_id}/start", status_code=202)
async def start_verification_run(run_id: str, db: Session = Depends(_get_db)):
    """
    Start (or re-start) a queued/failed/interrupted verification run.
    No auth required — accessible directly from the AI Studio dashboard.
    """
    import tempfile, os
    from services.runner import start_job

    run = db.query(Run).filter(Run.id == run_id).first()
    if not run:
        raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found")

    # Allow re-running failed, interrupted, or cancelled runs
    restartable = {"queued", "failed", "interrupted", "cancelled"}
    if run.status == "running":
        raise HTTPException(status_code=409, detail="Run is already in progress.")
    if run.status == "completed":
        raise HTTPException(
            status_code=409,
            detail="Run already completed successfully. Create a new run to re-verify.",
        )
    if run.status not in restartable:
        raise HTTPException(
            status_code=409, detail=f"Cannot start run in state '{run.status}'."
        )

    # Create a fresh output directory for re-runs
    output_dir = tempfile.mkdtemp(prefix=f"chipverify_run_{run_id[:8]}_")

    # Write RTL snapshot
    rtl_ext = ".sv" if "systemverilog" in (run.specification_type or "") else ".v"
    rtl_path = os.path.join(output_dir, f"design{rtl_ext}")
    with open(rtl_path, "w", encoding="utf-8") as f:
        f.write(run.rtl_snapshot or "// empty RTL")

    # Write spec snapshot
    spec_path = os.path.join(output_dir, "spec.txt")
    with open(spec_path, "w", encoding="utf-8") as f:
        f.write(run.spec_snapshot or "No spec provided")

    # Reset run state
    run.status = "running"
    run.completed_at = None
    run.logs_path = os.path.join(output_dir, "logs.json")
    run.output_path = output_dir
    run.verification_summary = None
    db.commit()

    # Launch pipeline thread
    try:
        start_job(run_id, rtl_path, spec_path, output_dir)
    except Exception as e:
        run.status = "failed"
        run.verification_summary = f"Startup error: {e}"
        db.commit()
        raise HTTPException(status_code=500, detail=f"Failed to start pipeline: {e}")

    return {
        "run_id": run_id,
        "status": "running",
        "output_dir": output_dir,
        "message": "Verification pipeline started",
    }


@router.get("/runs/{run_id}/status")
async def get_run_status(run_id: str, db: Session = Depends(_get_db)):
    """Poll the status of a verification run."""
    from services.runner import get_job_status
    from database.models import RunEvent as RunEventModel

    run = db.query(Run).filter(Run.id == run_id).first()
    if not run:
        raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found")

    # Get in-memory job state for live logs
    job_state = get_job_status(run_id)

    # Get last 20 events from DB
    events = (
        db.query(RunEventModel)
        .filter(RunEventModel.run_id == run_id)
        .order_by(RunEventModel.seq_no.desc())
        .limit(20)
        .all()
    )
    events.reverse()

    return {
        "run_id": run_id,
        "status": run.status,
        "created_at": run.created_at.isoformat() if run.created_at else None,
        "completed_at": run.completed_at.isoformat() if run.completed_at else None,
        "execution_time": run.execution_time,
        "source": run.source,
        "verification_summary": run.verification_summary,
        "live_logs": job_state.get("logs", [])[-30:],
        "events": [
            {"seq": e.seq_no, "phase": e.phase, "level": e.level, "message": e.message}
            for e in events
        ],
    }


@router.get("/runs/{run_id}/stream")
async def stream_run_logs(run_id: str, db: Session = Depends(_get_db)):
    """
    SSE stream of live log events for a verification run.
    Polls RunEvent table every second and yields new entries.
    """
    from database.models import RunEvent as RunEventModel

    run = db.query(Run).filter(Run.id == run_id).first()
    if not run:
        raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found")
    db.close()

    async def generate() -> AsyncGenerator[str, None]:
        last_seq = 0
        idle_ticks = 0
        max_idle = 120  # 2 minutes of no new events → close

        while True:
            # Heartbeat every 15s
            if idle_ticks % 15 == 0:
                yield ": heartbeat\n\n"

            inner_db = _get_db().__next__()
            try:
                new_events = (
                    inner_db.query(RunEventModel)
                    .filter(
                        RunEventModel.run_id == run_id, RunEventModel.seq_no > last_seq
                    )
                    .order_by(RunEventModel.seq_no.asc())
                    .limit(50)
                    .all()
                )

                if new_events:
                    idle_ticks = 0
                    for ev in new_events:
                        last_seq = ev.seq_no
                        payload = json.dumps(
                            {
                                "seq": ev.seq_no,
                                "phase": ev.phase,
                                "level": ev.level,
                                "message": ev.message,
                            }
                        )
                        yield f"data: {payload}\n\n"

                # Check if run finished
                run_rec = inner_db.query(Run).filter(Run.id == run_id).first()
                if run_rec and run_rec.status in (
                    "completed",
                    "failed",
                    "cancelled",
                    "interrupted",
                ):
                    yield f"data: {json.dumps({'__done__': True, 'status': run_rec.status})}\n\n"
                    return
            except Exception as e:
                yield f"data: {json.dumps({'error': str(e)})}\n\n"
            finally:
                try:
                    inner_db.close()
                except Exception:
                    pass

            idle_ticks += 1
            if idle_ticks > max_idle:
                yield f"data: {json.dumps({'__timeout__': True})}\n\n"
                return

            await asyncio.sleep(1)

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/runs")
async def list_design_runs(
    limit: int = 20,
    source: str | None = None,
    db: Session = Depends(_get_db),
):
    """List verification runs, optionally filtered by source (e.g. source=rtl_designer)."""
    q = db.query(Run).order_by(Run.created_at.desc())
    if source:
        q = q.filter(Run.source == source)
    runs = q.limit(limit).all()
    return [
        {
            "id": r.id,
            "status": r.status,
            "source": r.source,
            "prompt_text": (r.prompt_text or "")[:120],
            "created_at": r.created_at.isoformat() if r.created_at else None,
            "completed_at": r.completed_at.isoformat() if r.completed_at else None,
            "execution_time": r.execution_time,
            "verification_summary": r.verification_summary,
            "rtl_lines": len((r.rtl_snapshot or "").splitlines()),
        }
        for r in runs
    ]


# ── Standalone Verification (no design_id required) ──────────────────────────


@router.post("/verify-now", status_code=202)
async def verify_now(req: VerifyNowRequest, db: Session = Depends(_get_db)):
    """
    Create and queue a verification run directly from pasted RTL code.
    No design generation step required — fully standalone.

    Returns run_id and start_url. Call POST /design/runs/{run_id}/start to execute.
    """
    from database.models import Project

    # Validate project exists
    project = db.query(Project).filter(Project.id == req.project_id).first()
    if not project:
        raise HTTPException(
            status_code=404,
            detail=f"Project '{req.project_id}' not found. Use POST /design/setup/default-project first.",
        )

    rtl_code = (req.rtl_code or "").strip()
    if not rtl_code:
        raise HTTPException(status_code=422, detail="rtl_code must not be empty")

    # Build tags for prompt_text
    description = (req.description or "Standalone RTL verification").strip()
    strategy_tag = f"[strategy:{req.strategy}]"
    coverage_tag = (
        "[coverage:enabled]" if req.enable_coverage else "[coverage:disabled]"
    )
    assertions_tag = (
        "[assertions:enabled]" if req.enable_assertions else "[assertions:disabled]"
    )
    lang_tag = f"[lang:{req.language}]"
    prompt_text = (
        f"{description} {strategy_tag} {coverage_tag} {assertions_tag} {lang_tag}"
    )

    run_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)

    new_run = Run(
        id=run_id,
        organization_id=project.organization_id,
        project_id=req.project_id,
        user_id=project.owner_user_id,
        prompt_text=prompt_text,
        specification_type=req.language,
        status="queued",
        created_at=now,
        rtl_snapshot=rtl_code,
        spec_snapshot=req.description or "",
        source="standalone",
    )
    db.add(new_run)

    # Initial traceability events
    line_count = len(rtl_code.splitlines())
    for seq, (phase, level, msg) in enumerate(
        [
            ("intake", "info", f"Standalone verification run created"),
            (
                "intake",
                "info",
                f"Strategy: {req.strategy} | Coverage: {req.enable_coverage} | Assertions: {req.enable_assertions}",
            ),
            ("intake", "info", f"RTL: {line_count} lines, language: {req.language}"),
            ("intake", "info", f"Description: {description[:120]}"),
        ],
        start=1,
    ):
        db.add(
            RunEvent(
                id=str(uuid.uuid4()),
                run_id=run_id,
                seq_no=seq,
                event_kind="log",
                phase=phase,
                level=level,
                message=msg,
            )
        )

    db.commit()

    return {
        "run_id": run_id,
        "project_id": req.project_id,
        "organization_id": project.organization_id,
        "status": "queued",
        "strategy": req.strategy,
        "rtl_lines": line_count,
        "source": "standalone",
        "message": f"Verification run queued with {req.strategy} strategy. POST /design/runs/{run_id}/start to begin.",
        "start_url": f"/design/runs/{run_id}/start",
    }
