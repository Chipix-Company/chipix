"""
Debug Tools — Failure classification, root-cause analysis, fix proposals.

Uses NEW debug_service.py for structured failure analysis with mental
model awareness. Falls back to pattern-only classification if LLM
is unavailable.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from agent_tools import register_tool

logger = logging.getLogger(__name__)


async def handle_debug_failure(
    project_id: str,
    error_log: str = "",
    db_session: Any = None,
    ai_client: Any = None,
    **kwargs: Any,
) -> str:
    """Classify a failure, diagnose root cause, and propose fixes."""
    if not error_log:
        return json.dumps({"error": "No error_log provided"})

    # ── Try new debug service ─────────────────────────────────────
    try:
        from services.verification.debug_service import diagnose_failure

        # Load mental model for signal correlation
        model = kwargs.get("mental_model")
        if not model and db_session:
            try:
                from agent_tools.mental_model_tools import _load_mental_model
                model = await _load_mental_model(project_id, db_session)
            except Exception:
                model = None

        # Get RTL/testbench content if available
        rtl_content = kwargs.get("rtl_content", "")
        testbench_content = kwargs.get("testbench_content", "")

        result = await diagnose_failure(
            error_log=error_log,
            rtl_content=rtl_content,
            testbench_content=testbench_content,
            mental_model=model,
            ai_client=ai_client,
        )

        response = result.to_dict()
        response["status"] = "analyzed"
        response["engine"] = "debug_service"
        response["recommended_action"] = result.explanation

        # If a fix was proposed, include instructions for the LLM
        if result.proposed_fix or result.diff_text:
            response["_instruction_for_llm"] = (
                "A fix has been proposed. Show the diff to the user and ask: "
                "'Should I apply this fix and re-run verification?'"
            )

        return json.dumps(response, indent=2)

    except ImportError:
        logger.info("debug_service not available, using pattern-only classification")

    # ── Fallback: pattern-only classification ─────────────────────
    try:
        from services.verification.debug_service import classify_failure, _recommend_action
        classification = classify_failure(error_log)
    except ImportError:
        # Even the classify function isn't available — inline it
        classification = {"classification": "unknown", "confidence": 0.0, "all_scores": {}}

    return json.dumps({
        "status": "analyzed",
        "classification": classification.get("classification", "unknown"),
        "confidence": classification.get("confidence", 0.0),
        "all_scores": classification.get("all_scores", {}),
        "error_excerpt": error_log[:1000],
        "fix_suggestion": "Manual review recommended — no LLM available for diagnosis.",
        "recommended_action": "Check the error log for specific failure patterns.",
        "engine": "pattern_only",
    }, indent=2)


# ─── Diagnose-and-Fix Tool (Auto-Healing Entry Point) ────────────────

async def handle_diagnose_and_fix(
    project_id: str,
    error_log: str = "",
    verification_type: str = "unitsim",
    db_session: Any = None,
    ai_client: Any = None,
    **kwargs: Any,
) -> str:
    """
    Auto-healing tool: diagnose failure → propose patch → wait for approval.

    This is the ChipStack-style self-healing loop entry point.
    The LLM calls this when verification fails, and the result includes
    a patch that the user can approve via the PatchApprovalPanel.
    """
    if not error_log:
        return json.dumps({"error": "No error_log provided"})

    try:
        from services.verification.debug_service import diagnose_failure
        from services.verification.patch_service import create_patch_proposal

        # Load mental model
        model = kwargs.get("mental_model")
        if not model and db_session:
            try:
                from agent_tools.mental_model_tools import _load_mental_model
                model = await _load_mental_model(project_id, db_session)
            except Exception:
                model = None

        rtl_content = kwargs.get("rtl_content", "")
        testbench_content = kwargs.get("testbench_content", "")

        # Step 1: Diagnose
        diagnosis = await diagnose_failure(
            error_log=error_log,
            rtl_content=rtl_content,
            testbench_content=testbench_content,
            mental_model=model,
            ai_client=ai_client,
        )

        # Step 2: Create patch proposal in DB
        patch_id = ""
        if diagnosis.proposed_fix or diagnosis.diff_text:
            try:
                proposal = create_patch_proposal(
                    project_id=project_id,
                    title=f"Fix: {diagnosis.root_cause[:80]}",
                    description=diagnosis.explanation,
                    diff_text=diagnosis.diff_text or diagnosis.proposed_fix,
                    fix_type=diagnosis.fix_type,
                    classification=diagnosis.classification,
                    affected_signals=diagnosis.affected_signals,
                    db_session=db_session,
                )
                patch_id = proposal.id if hasattr(proposal, "id") else str(proposal)
            except Exception as e:
                logger.warning(f"Failed to persist patch proposal: {e}")

        return json.dumps({
            "status": "fix_proposed" if diagnosis.proposed_fix else "diagnosed",
            "classification": diagnosis.classification,
            "confidence": diagnosis.confidence,
            "root_cause": diagnosis.root_cause,
            "fix_type": diagnosis.fix_type,
            "patch_id": patch_id,
            "diff": diagnosis.diff_text,
            "explanation": diagnosis.explanation,
            "affected_signals": diagnosis.affected_signals,
            "_instruction_for_llm": (
                "Show this patch to the user using the diff view. "
                "Ask: 'Should I apply this fix and re-run verification?' "
                "If user approves, apply the patch and call runVerification again."
            ) if diagnosis.proposed_fix else (
                "Show the diagnosis to the user and ask if they want to "
                "manually fix the issue or provide more context."
            ),
        }, indent=2)

    except ImportError as e:
        logger.warning(f"Debug service not available: {e}")
        # Fallback to basic classification
        result_str = await handle_debug_failure(
            project_id=project_id,
            error_log=error_log,
            db_session=db_session,
            ai_client=ai_client,
            **kwargs,
        )
        return result_str


# Register handlers
register_tool("debugFailure", handle_debug_failure)
register_tool("diagnoseAndFix", handle_diagnose_and_fix)
