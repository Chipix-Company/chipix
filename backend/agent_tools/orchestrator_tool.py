"""
Orchestrator Tool — "Verify this block" entry point.

CONVERSATIONAL FLOW:
  Step 1: User says "verify the FIFO block"
  Step 2: analyzeDesign → scans, builds mental model, returns summary + options
  Step 3: LLM shows summary → asks user: "Which verification do you want?"
  Step 4: User picks → LLM calls the appropriate run tool
  Step 5: Results come back → LLM reports findings

This is human-in-the-loop by design — the user always chooses.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

from agent_tools import register_tool

logger = logging.getLogger(__name__)


async def handle_analyze_design(
    project_id: str,
    target_module: str = "",
    db_session: Any = None,
    ai_client: Any = None,
    **kwargs: Any,
) -> str:
    """
    Step 1: Analyze a design block — build mental model + return options.

    This does NOT run any verification. It only:
      1. Scans the project folder
      2. Builds/updates the mental model
      3. Returns a summary with available verification options

    The LLM should then present this to the user and ask what to do.
    """
    report: Dict[str, Any] = {
        "status": "analyzed",
        "project_id": project_id,
        "target_module": target_module,
    }

    # ── Build/Retrieve Mental Model ─────────────────────────────────
    try:
        from agent_tools.mental_model_tools import (
            handle_build_mental_model,
            handle_query_mental_model,
        )

        # Check if model already exists
        query_result = await handle_query_mental_model(
            project_id=project_id,
            query="summary",
            db_session=db_session,
            **kwargs,
        )
        query_data = json.loads(query_result)

        if "error" in query_data:
            # No model exists — build one
            logger.info("No mental model found — building one")
            build_result = await handle_build_mental_model(
                project_id=project_id,
                target_module=target_module,
                db_session=db_session,
                ai_client=ai_client,
                **kwargs,
            )
            build_data = json.loads(build_result)

            report["mental_model"] = {
                "status": "newly_built",
                "summary": build_data.get("summary", ""),
                "top_module": build_data.get("top_module", ""),
                "modules": build_data.get("modules", []),
                "ports_count": build_data.get("ports_count", 0),
                "requirements_count": build_data.get("requirements_count", 0),
                "open_questions": build_data.get("open_questions", []),
                "fsms": build_data.get("fsms", []),
                "protocols": build_data.get("protocols", []),
                "clock_domains": build_data.get("clock_domains", []),
            }
            if not target_module:
                target_module = build_data.get("top_module", "")
        else:
            report["mental_model"] = {
                "status": "existing",
                "revision": query_data.get("revision", 0),
                "summary": query_data.get("summary", ""),
                "top_module": query_data.get("top_module", ""),
                "modules": query_data.get("modules", []),
                "ports_count": query_data.get("ports_count", 0),
                "requirements_count": query_data.get("requirements_count", 0),
                "open_questions": query_data.get("open_questions", []),
            }
            if not target_module:
                target_module = query_data.get("top_module", "")

    except Exception as e:
        report["mental_model"] = {"status": "error", "error": str(e)}
        logger.exception(f"Mental model phase failed: {e}")

    report["target_module"] = target_module

    # ── Available Verification Options ──────────────────────────────
    report["available_verifications"] = [
        {
            "type": "unitsim",
            "name": "Unit Simulation (UnitSim)",
            "description": "Generates SV testbench with directed + boundary tests, runs with iverilog/Verilator. Best for quick functional checks.",
            "estimated_time": "1-5 minutes",
            "requires": "iverilog or Verilator installed",
        },
        {
            "type": "formal",
            "name": "Formal Verification",
            "description": "Generates SVA assertions (reset, protocol, FSM, safety properties) and SymbiYosys scripts. Proves correctness mathematically.",
            "estimated_time": "5-15 minutes",
            "requires": "SymbiYosys (open-source) or Jasper/VC Formal",
        },
        {
            "type": "uvm",
            "name": "UVM Environment",
            "description": "Generates a complete 18-file UVM verification environment with agents, drivers, monitors, scoreboard, and coverage.",
            "estimated_time": "2-5 minutes (generation only)",
            "requires": "Commercial simulator (VCS, Xcelium, Questa) for execution",
        },
        {
            "type": "all",
            "name": "All Verification",
            "description": "Run UnitSim + Formal + UVM together for maximum coverage.",
            "estimated_time": "10-20 minutes",
            "requires": "All tools above",
        },
    ]

    # ── Prompt hint for the LLM ─────────────────────────────────────
    report["_instruction_for_llm"] = (
        "Present the mental model summary to the user in a clear format. "
        "Show the design details (top module, ports, requirements, protocols, FSMs). "
        "Then ask: 'Which verification would you like to run?' and list the options. "
        "Wait for the user's choice before calling any verification tool."
    )

    return json.dumps(report, indent=2)


async def handle_run_verification(
    project_id: str,
    target_module: str = "",
    verification_type: str = "unitsim",
    db_session: Any = None,
    ai_client: Any = None,
    **kwargs: Any,
) -> str:
    """
    Step 2: Run a specific verification type after user has chosen.

    Called AFTER the user reviews the mental model and picks a verification.
    """
    report: Dict[str, Any] = {
        "status": "running",
        "project_id": project_id,
        "target_module": target_module,
        "verification_type": verification_type,
    }

    if verification_type in ("unitsim", "all"):
        report["unit_simulation"] = await _run_unitsim_phase(
            project_id, target_module, db_session, ai_client, **kwargs
        )

    if verification_type in ("formal", "all"):
        report["formal_verification"] = await _run_formal_phase(
            project_id, target_module, db_session, ai_client, **kwargs
        )

    if verification_type in ("uvm", "all"):
        report["uvm"] = await _run_uvm_phase(
            project_id, target_module, db_session, ai_client, **kwargs
        )

    # ── Overall Status ────────────────────────────────────────────
    phases = [v for k, v in report.items() if isinstance(v, dict) and "status" in v]
    has_errors = any(p.get("status") == "error" for p in phases)
    has_failures = any(p.get("tests_failed", 0) > 0 for p in phases)

    if has_errors:
        report["status"] = "completed_with_errors"
    elif has_failures:
        report["status"] = "completed_with_failures"
    else:
        report["status"] = "completed_all_passed"

    # ── Instruction for LLM ───────────────────────────────────────
    report["_instruction_for_llm"] = (
        "Present the verification results clearly. "
        "For each phase, show pass/fail counts. "
        "If there are failures, explain what went wrong and offer to debug or auto-fix. "
        "If there are patch proposals, ask the user if they want to review them."
    )

    return json.dumps(report, indent=2)


# ═══════════════════════════════════════════════════════════════════════
# Legacy wrapper — handle_verify_block still works for REST API
# ═══════════════════════════════════════════════════════════════════════

async def handle_verify_block(
    project_id: str,
    target_module: str = "",
    verification_types: Optional[List[str]] = None,
    db_session: Any = None,
    ai_client: Any = None,
    **kwargs: Any,
) -> str:
    """
    Legacy wrapper: runs full pipeline without user confirmation.
    Used by REST API endpoint POST /api/v1/projects/{id}/verify.
    For chat flow, use analyzeDesign + runVerification instead.
    """
    if verification_types is None:
        verification_types = ["all"]

    # Step 1: Analyze
    analysis = await handle_analyze_design(
        project_id=project_id,
        target_module=target_module,
        db_session=db_session,
        ai_client=ai_client,
        **kwargs,
    )
    analysis_data = json.loads(analysis)
    resolved_module = analysis_data.get("target_module", target_module)

    # Step 2: Run everything requested
    vtype = "all" if "all" in verification_types else verification_types[0]
    result = await handle_run_verification(
        project_id=project_id,
        target_module=resolved_module,
        verification_type=vtype,
        db_session=db_session,
        ai_client=ai_client,
        **kwargs,
    )
    result_data = json.loads(result)

    # Merge analysis + results
    result_data["mental_model"] = analysis_data.get("mental_model", {})
    return json.dumps(result_data, indent=2)


# ═══════════════════════════════════════════════════════════════════════
# Phase runners
# ═══════════════════════════════════════════════════════════════════════

async def _run_unitsim_phase(
    project_id, target_module, db_session, ai_client, **kwargs
) -> Dict:
    """Run the UnitSim phase."""
    try:
        from agent_tools.unitsim_tools import (
            handle_generate_testbench,
            handle_run_simulation,
        )

        tb_result = await handle_generate_testbench(
            project_id=project_id,
            target_module=target_module,
            db_session=db_session,
            ai_client=ai_client,
            **kwargs,
        )
        tb_data = json.loads(tb_result)

        sim_result_data: Dict[str, Any] = {}
        if tb_data.get("status") == "success":
            sim_result = await handle_run_simulation(
                project_id=project_id,
                db_session=db_session,
                ai_client=ai_client,
                **kwargs,
            )
            sim_result_data = json.loads(sim_result)

        return {
            "status": sim_result_data.get("status", "tb_generated"),
            "testbench": tb_data.get("status", "unknown"),
            "files_generated": tb_data.get("files_generated", 0),
            "tests_passed": sim_result_data.get("tests_passed", 0),
            "tests_failed": sim_result_data.get("tests_failed", 0),
            "assertion_failures": sim_result_data.get("assertion_failures", 0),
            "errors": sim_result_data.get("errors", []),
            "log_excerpt": sim_result_data.get("log_excerpt", ""),
        }

    except Exception as e:
        logger.exception(f"UnitSim phase failed: {e}")
        return {"status": "error", "error": str(e)}


async def _run_formal_phase(
    project_id, target_module, db_session, ai_client, **kwargs
) -> Dict:
    """Run the Formal verification phase."""
    try:
        from agent_tools.formal_tools import (
            handle_generate_formal,
            handle_run_formal,
        )

        formal_gen = await handle_generate_formal(
            project_id=project_id,
            target_module=target_module,
            db_session=db_session,
            ai_client=ai_client,
            **kwargs,
        )
        formal_gen_data = json.loads(formal_gen)

        formal_run_data: Dict[str, Any] = {}
        if formal_gen_data.get("status") == "success":
            formal_run = await handle_run_formal(
                project_id=project_id,
                db_session=db_session,
                ai_client=ai_client,
                **kwargs,
            )
            formal_run_data = json.loads(formal_run)

        return {
            "status": formal_run_data.get("status", "generated"),
            "properties_count": formal_gen_data.get("properties_count", 0),
            "proven": formal_run_data.get("proven", 0),
            "failed": formal_run_data.get("failed", 0),
            "bounded": formal_run_data.get("bounded", 0),
        }

    except Exception as e:
        logger.exception(f"Formal phase failed: {e}")
        return {"status": "error", "error": str(e)}


async def _run_uvm_phase(
    project_id, target_module, db_session, ai_client, **kwargs
) -> Dict:
    """Generate the UVM environment and, when Cadence is available, run it.

    This closes the loop: after generation we drive Xcelium on the generated
    collateral and return the parsed analysis + feedback_memory so the agent can
    fix the test and regenerate. When Cadence is not connected (no Linux host /
    license), we say so explicitly instead of silently stopping at generation.
    """
    try:
        from agent_tools.uvm_tools import handle_generate_uvm

        uvm_result = await handle_generate_uvm(
            project_id=project_id,
            mode="create",
            db_session=db_session,
            ai_client=ai_client,
            **kwargs,
        )
        uvm_data = json.loads(uvm_result)

        result: Dict = {
            "status": uvm_data.get("status", "unknown"),
            "files_generated": uvm_data.get("files_generated", 0),
            "message": uvm_data.get("message", ""),
        }

        work_dir = uvm_data.get("work_dir") or kwargs.get("work_dir") or ""
        if work_dir:
            try:
                simulation = _simulate_generated_uvm(work_dir, kwargs.get("uvm_testname", ""))
            except Exception as sim_exc:  # never let simulation break generation
                logger.warning("Cadence simulation step failed: %s", sim_exc, exc_info=True)
                simulation = {"status": "error", "cadence_connected": False, "error": str(sim_exc)}
            result["cadence_connected"] = bool(simulation.get("cadence_connected"))
            result["simulation"] = simulation
            # When Cadence actually ran, its verdict (passed/failed) is the phase outcome.
            if simulation.get("cadence_connected") and simulation.get("status") not in (None, "skipped"):
                result["status"] = simulation.get("status")
        else:
            result["cadence_connected"] = False

        return result

    except Exception as e:
        logger.exception(f"UVM phase failed: {e}")
        return {"status": "error", "error": str(e)}


def _ensure_filelist(work_dir):
    """Return a compile filelist for the generated UVM, building one if needed.

    Strongly prefers the filelist uvm_gen already emitted (``*_uvm.f``): it lists
    the DUT RTL (by original path) + interface + package + top_tb in the correct
    compile order. A rebuilt fallback from work_dir alone would be missing the
    DUT RTL, so it is only used when no generated filelist exists.
    """
    from pathlib import Path

    work_dir = Path(work_dir)
    uvm_filelists = sorted(work_dir.glob("*_uvm.f")) or sorted(work_dir.rglob("*_uvm.f"))
    if uvm_filelists:
        return uvm_filelists[0]
    other = [
        path
        for path in (sorted(work_dir.glob("*.f")) or sorted(work_dir.rglob("*.f")))
        if path.name != "agent_sim.f"
    ]
    if other:
        return other[0]

    # Fallback: no generated filelist — rebuild from work_dir. NOTE: this will not
    # include the DUT RTL (it lives outside work_dir), so it is parse-level only.
    logger.warning(
        "No uvm_gen filelist in %s; rebuilding from generated files only "
        "(DUT RTL will be absent).",
        work_dir,
    )
    sv_files = list(work_dir.rglob("*.sv")) + list(work_dir.rglob("*.svh"))
    if not sv_files:
        return None

    def rank(path) -> tuple[int, str]:
        name = path.name.lower()
        if name.endswith("_pkg.sv"):
            return (0, name)
        if name.endswith("_if.sv") or "interface" in name:
            return (1, name)
        if name.startswith("top_tb") or name.endswith("_tb.sv"):
            return (9, name)
        return (5, name)

    ordered = sorted(set(sv_files), key=rank)
    flist = work_dir / "agent_sim.f"
    flist.write_text("\n".join(str(p) for p in ordered) + "\n", encoding="utf-8")
    return flist


def _simulate_generated_uvm(work_dir: str, uvm_testname: str = "") -> Dict:
    """Run generated UVM through Cadence Xcelium and return agent-facing feedback."""
    from pathlib import Path

    try:
        from simulator_plugins.base import SimulatorRunRequest
        from simulator_plugins.xcelium import XceliumPlugin
        from simulator_plugins.xcelium_runner import XceliumRunner
    except Exception as exc:
        return {"status": "skipped", "cadence_connected": False, "reason": f"Simulator plugin unavailable: {exc}"}

    detection = XceliumPlugin().detect()
    details = (detection.to_dict() or {}).get("details", {}) or {}
    if not detection.available:
        return {
            "status": "skipped",
            "cadence_connected": False,
            "detection_status": details.get("status"),
            "guidance": detection.guidance,
            "message": "Cadence Xcelium is not connected — UVM was generated but not simulated here.",
        }

    wd = Path(work_dir)
    if not wd.exists():
        return {"status": "skipped", "cadence_connected": True, "reason": "Generated work_dir missing."}

    filelist = _ensure_filelist(wd)
    if filelist is None:
        return {"status": "skipped", "cadence_connected": True, "reason": "No SystemVerilog files to simulate."}

    request = SimulatorRunRequest(
        run_dir=wd / "xcelium_run",
        filelist=Path(filelist),
        top_module="top_tb",
        uvm_testname=uvm_testname or "",
    )
    run_result = XceliumRunner().run(request)
    closure = run_result.closure_report or {}
    return {
        "status": run_result.status,
        "cadence_connected": True,
        "license_ready": details.get("license_ready"),
        "analysis": run_result.analysis,
        "feedback_memory": closure.get("feedback_memory") or {},
        "repair_history": run_result.repair_history,
        "run_dir": str(request.run_dir),
        "next_step": _uvm_next_step(run_result.status),
    }


def _uvm_next_step(status: str) -> str:
    if status == "passed":
        return "UVM test passed under Cadence. Review coverage and the closure report."
    if status == "unavailable":
        return "Cadence is not available on this host; cannot simulate here."
    return (
        "Use simulation.feedback_memory.root_causes to fix the generated UVM "
        "collateral, then regenerate and re-run."
    )


# Register tools
register_tool("analyzeDesign", handle_analyze_design)
register_tool("runVerification", handle_run_verification)
register_tool("verifyBlock", handle_verify_block)  # Legacy/REST
