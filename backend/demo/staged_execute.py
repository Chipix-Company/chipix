"""Demo-mode staged verification execute stub (animated RunCard, fail→pass)."""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


async def run_demo_staged_execute(
    *,
    project_id: str,
    verification_type: str,
    db_session: Any = None,
    verification_task: Any = None,
) -> Dict[str, Any]:
    """
    Emit RunCard progress events and return a canned fail/pass result.

    Uses CHIPVERIFY_DEMO_SCENARIO via demo.scenario.
    """
    from demo.scenario import (
        advance_after_pass,
        ensure_fix_patch,
        formal_result,
        get_step,
        unitsim_result,
    )
    from services.verification.uvm_gen import publish_uvm_progress

    vtype = (verification_type or "unitsim").lower()
    run_id = f"demo-{uuid.uuid4().hex[:10]}"
    step = get_step(project_id)

    async def emit(event_type: str, message: str, **extra: Any) -> None:
        publish_uvm_progress(
            {
                "type": event_type,
                "phase": "execute",
                "message": message,
                "verification_type": vtype,
                "demo": True,
                "demo_step": step,
                **extra,
            }
        )
        await asyncio.sleep(0.35)

    await emit("started", "Starting staged verification execution")
    await emit("policy_selected", "Locking approved plan")
    await emit("scaffold_complete", "Scaffolding unit testbench collateral")
    await emit("compile_complete", "Compiling UnitSim testbench")
    await emit("run", "Running directed UnitSim scenarios…")

    unit = unitsim_result(project_id)
    formal = formal_result(project_id) if vtype in {"formal", "all", "unitsim"} else None

    # Always include unitsim for demo story; formal on pass path.
    results: Dict[str, Any] = {
        "unitsim": {
            "status": "failed" if str(unit.get("status")).upper() == "FAIL" else "passed",
            "tests_passed": unit.get("tests_passed"),
            "tests_failed": unit.get("tests_failed"),
            "assertion_failures": unit.get("assertion_failures") or [],
            "test_results": unit.get("test_results") or [],
            "sim_log_excerpt": unit.get("log_excerpt"),
            "coverage": unit.get("coverage") or {},
            "summary": unit.get("log_excerpt", "").split("\n")[-2]
            if unit.get("log_excerpt")
            else str(unit.get("status")),
            "demo": True,
        }
    }

    patch_info = None
    if str(unit.get("status")).upper() == "FAIL":
        await emit("result", "UnitSim FAIL — multi_block_padding")
        patch_info = ensure_fix_patch(project_id, db_session)
        if patch_info and not patch_info.get("error"):
            results["unitsim"]["patch_proposals"] = [patch_info]
            await emit(
                "result",
                "Proposed fix: assert msg_last on final padded block (awaiting Apply)",
            )
        status = "failed"
        summary = (
            "UnitSim failed on multi_block_padding (2/3). "
            "Review the DiffCard and Apply the msg_last fix, then re-run."
        )
    else:
        await emit("result", "UnitSim PASS — 3/3 scenarios")
        if formal is not None and str(formal.get("status")).upper() != "SKIPPED":
            await emit("run", "Running formal prove…")
            results["formal"] = {
                "status": "passed" if str(formal.get("status")).upper() == "PROVEN" else "failed",
                "proven": formal.get("proven"),
                "failed": formal.get("failed"),
                "properties": formal.get("properties") or {},
                "summary": "Formal PROVEN — reset, handshake, hash hold",
                "demo": True,
            }
            await emit("result", "Formal PROVEN — 3 properties")
        cov = unit.get("coverage") or {}
        line_pct = int(round(float(cov.get("line") or 0.94) * 100))
        status = "passed"
        summary = (
            f"VERIFICATION COMPLETE — UnitSim 3/3 PASS, Formal PROVEN, "
            f"coverage ~{line_pct}%."
        )
        advance_after_pass(project_id)

    await emit("complete", "Verification execution finished")

    result = {
        "status": status,
        "summary": summary,
        "run_id": run_id,
        "verification_run_id": run_id,
        "results": results,
        "demo": True,
        "demo_step": get_step(project_id),
        "project_id": project_id,
        "verification_type": vtype,
        "ts": time.time(),
    }
    if patch_info:
        result["patch_proposal"] = patch_info

    if verification_task is not None and db_session is not None:
        try:
            from services.project_tasks import finalize_staged_verification_task

            finalize_staged_verification_task(
                db_session,
                verification_task,
                "failed" if status == "failed" else "completed",
            )
        except Exception:
            logger.debug("demo finalize task skipped", exc_info=True)

    return result
