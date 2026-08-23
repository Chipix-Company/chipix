"""Resolve session-scoped values for agent tool calls."""

from __future__ import annotations

import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)

CADENCE_CONTEXT_TOOLS = frozenset(
    {
        "checkCadenceStatus",
        "runCadenceSimulation",
        "getCadenceRunStatus",
        "applyCadenceFixes",
    }
)

PROJECT_SCOPED_TOOLS = frozenset(
    {
        "runCadenceSimulation",
        "getCadenceRunStatus",
        "applyCadenceFixes",
        "generateUVMEnvironment",
        "runSimulation",
        "analyzeDesign",
        "runVerification",
        "buildMentalModel",
        "queryMentalModel",
        "scanProject",
    }
)


def resolve_project_id(
    args: dict[str, Any],
    context: dict[str, Any],
    *,
    tool_name: str = "",
    required: bool = True,
) -> Optional[str]:
    """Return the active project_id, preferring session context over LLM args."""
    session_id = str(context.get("project_id") or "").strip()
    arg_id = str(args.get("project_id") or "").strip()

    if session_id and arg_id and session_id != arg_id:
        logger.warning(
            "Tool %s: overriding LLM project_id=%r with session project_id=%r",
            tool_name or "unknown",
            arg_id,
            session_id,
        )

    resolved = session_id or arg_id or None
    if required and not resolved:
        raise ValueError(
            "No active project in session. Open a project workspace and retry."
        )
    return resolved


def merge_verification_args_from_context(
    tool_name: str,
    args: dict[str, Any],
    context: dict[str, Any],
) -> dict[str, Any]:
    """Inject session context into verification tool arguments before execution."""
    merged = dict(args or {})
    ctx = context if isinstance(context, dict) else {}

    if tool_name in PROJECT_SCOPED_TOOLS or tool_name in CADENCE_CONTEXT_TOOLS:
        merged["project_id"] = resolve_project_id(
            merged,
            ctx,
            tool_name=tool_name,
            required=tool_name in PROJECT_SCOPED_TOOLS
            and tool_name != "checkCadenceStatus",
        )

    if tool_name in CADENCE_CONTEXT_TOOLS:
        if not merged.get("current_user") and ctx.get("current_user"):
            merged["current_user"] = ctx["current_user"]

        if tool_name == "runCadenceSimulation":
            if not merged.get("generated_artifact_ids") and not merged.get("artifact_ids"):
                ctx_ids = ctx.get("generated_artifact_ids")
                if isinstance(ctx_ids, list) and ctx_ids:
                    merged["generated_artifact_ids"] = ctx_ids

            if not merged.get("top_module"):
                top = ctx.get("uvm_top_module") or ctx.get("top_module")
                if top:
                    merged["top_module"] = top

            if not merged.get("uvm_testname"):
                test = ctx.get("uvm_testname")
                if test:
                    merged["uvm_testname"] = test

    return merged
