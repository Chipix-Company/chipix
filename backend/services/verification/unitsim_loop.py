"""
UnitSim Loop — The complete 6-step verification loop.

Wires together:
  1. Mental Model (builder) → design understanding
  2. Test Plan (test_plan.py) → what to verify
  3. Testbench (testbench_gen.py) → generated SV code
  4. Simulator (icarus/verilator) → compile + run
  5. Parser (result_parser.py) → structured results
  6. Patch (patch_service.py) → auto-fix proposals

This is the core engine that the verifyBlock tool calls.
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class UnitSimRunResult:
    """Complete result from a UnitSim loop execution."""
    run_id: str = ""
    project_id: str = ""
    target_module: str = ""
    status: str = "pending"  # "pending" | "running" | "passed" | "failed" | "error"
    phases: Dict[str, Any] = field(default_factory=dict)
    test_plan_scenarios: int = 0
    tests_passed: int = 0
    tests_failed: int = 0
    assertion_failures: int = 0
    patch_proposals: List[str] = field(default_factory=list)
    generated_files: List[str] = field(default_factory=list)
    sim_log_excerpt: str = ""
    vcd_path: str = ""
    duration_ms: int = 0
    timestamp: str = ""


async def run_unitsim_loop(
    project_id: str,
    model: Any,
    work_dir: str,
    target_module: str = "",
    simulator: str = "auto",
    ai_client: Any = None,
    db_session: Any = None,
    max_fix_iterations: int = 3,
    on_event: Any = None,
    approved_plan: Any = None,
) -> UnitSimRunResult:
    """
    Execute the full UnitSim 6-step loop.

    Args:
        project_id: Project UUID
        model: MentalModelSchema instance
        work_dir: Working directory for generated files
        target_module: Module to verify (default: top_module)
        simulator: "auto", "icarus", or "verilator"
        ai_client: LLM client for auto-fix proposals
        db_session: Database session for persistence
        max_fix_iterations: Max auto-fix attempts
        on_event: Callback for progress events
        approved_plan: Optional staged/user-approved UnitSim plan to execute
    """
    run_id = str(uuid.uuid4())
    start = datetime.now(timezone.utc)

    result = UnitSimRunResult(
        run_id=run_id,
        project_id=project_id,
        target_module=target_module or model.design.top_module,
        status="running",
        timestamp=start.isoformat(),
    )

    work = Path(work_dir)
    work.mkdir(parents=True, exist_ok=True)

    target = result.target_module

    def emit(phase: str, status: str, data: dict = None):
        if on_event:
            on_event({
                "type": "unitsim_progress",
                "run_id": run_id,
                "phase": phase,
                "status": status,
                "data": data or {},
            })

    # ═══════════════════════════════════════════════════════════════
    # Step 1: Generate Test Plan
    # ═══════════════════════════════════════════════════════════════
    emit("test_plan", "started")
    try:
        from services.verification.test_plan import generate_test_plan_with_ai

        if approved_plan:
            plan = _coerce_approved_test_plan(
                approved_plan,
                target or getattr(model.design, "top_module", "dut"),
            )
        else:
            plan = await generate_test_plan_with_ai(
                model,
                ai_client=ai_client,
                require_llm=False,
            )
        result.test_plan_scenarios = plan.total_scenarios
        result.phases["test_plan"] = {
            "status": "success",
            "scenarios": plan.total_scenarios,
            "summary": plan.summary,
            "source": "approved_plan" if approved_plan else "generated_from_model",
            "generation_engine": getattr(plan, "generation_engine", "unknown"),
            "llm_used": getattr(plan, "llm_used", False),
        }
        emit("test_plan", "completed", {"scenarios": plan.total_scenarios})
        logger.info(f"[{run_id}] Test plan: {plan.summary}")

    except Exception as e:
        result.phases["test_plan"] = {"status": "error", "error": str(e)}
        result.status = "error"
        emit("test_plan", "error", {"error": str(e)})
        return result

    # ═══════════════════════════════════════════════════════════════
    # Step 2: Generate Testbench
    # ═══════════════════════════════════════════════════════════════
    emit("testbench_gen", "started")
    try:
        from services.verification.testbench_gen import generate_testbench_from_plan

        # Use the target module's design block when available.
        block_models = getattr(model, "block_models", {}) or {}
        if isinstance(block_models, dict):
            design_block = block_models.get(target, model.design)
        else:
            design_block = model.design
        tb = generate_testbench_from_plan(plan, design_block)

        tb_path = work / tb.filename
        tb_path.write_text(tb.content, encoding="utf-8")
        result.generated_files.append(str(tb_path))

        result.phases["testbench_gen"] = {
            "status": "success",
            "filename": tb.filename,
            "test_count": tb.test_count,
            "path": str(tb_path),
        }
        emit("testbench_gen", "completed", {"filename": tb.filename})
        logger.info(f"[{run_id}] Testbench: {tb.filename} ({tb.test_count} tests)")

    except Exception as e:
        result.phases["testbench_gen"] = {"status": "error", "error": str(e)}
        result.status = "error"
        emit("testbench_gen", "error", {"error": str(e)})
        return result

    # ═══════════════════════════════════════════════════════════════
    # Step 3: Find RTL source files
    # ═══════════════════════════════════════════════════════════════
    rtl_files = []
    project_scan = getattr(model, "project_scan", None)
    scan_rtl_files = getattr(project_scan, "rtl_files", None) if project_scan else None
    if project_scan and scan_rtl_files:
        root_path = Path(getattr(project_scan, "root_path", "") or "")
        for entry in scan_rtl_files:
            rtl_path = Path(str(entry))
            if not rtl_path.is_absolute() and str(root_path):
                rtl_path = root_path / rtl_path
            rtl_files.append(str(rtl_path))
    else:
        # Scan work_dir parent for .sv/.v files
        scan_dir = Path(getattr(project_scan, "root_path", "") or work_dir)
        rtl_files = [
            str(f) for f in scan_dir.rglob("*")
            if f.suffix in (".sv", ".v", ".svh", ".vh") and "tb_" not in f.name
        ]

    if not rtl_files:
        result.phases["compile"] = {"status": "error", "error": "No RTL files found"}
        result.status = "error"
        return result
    include_dirs = _rtl_include_dirs(rtl_files)

    # ═══════════════════════════════════════════════════════════════
    # Step 4: Compile + Simulate (with auto-fix loop)
    # ═══════════════════════════════════════════════════════════════
    sim_adapter = _select_simulator(simulator)
    if not sim_adapter:
        result.phases["compile"] = {
            "status": "error",
            "error": "No simulator found. Install iverilog or verilator.",
        }
        result.status = "error"
        return result

    iteration = 0
    sim_result = None

    while iteration <= max_fix_iterations:
        emit("simulation", "started", {"iteration": iteration})

        try:
            sim_result = await sim_adapter.compile_and_run(
                rtl_files=rtl_files,
                tb_file=str(tb_path),
                work_dir=str(work / f"sim_iter_{iteration}"),
                top_module=f"tb_{target}",
                include_dirs=include_dirs,
                timeout_seconds=120,
            )

            result.phases[f"sim_iter_{iteration}"] = {
                "compile_success": sim_result.compile_success,
                "sim_success": sim_result.sim_success,
                "timed_out": sim_result.timed_out,
            }

        except Exception as e:
            result.phases[f"sim_iter_{iteration}"] = {"status": "error", "error": str(e)}
            result.status = "error"
            emit("simulation", "error", {"error": str(e)})
            break

        # ═══════════════════════════════════════════════════════════
        # Step 5: Parse Results
        # ═══════════════════════════════════════════════════════════
        from services.verification.result_parser import parse_simulation_log

        log_text = ""
        if sim_result.compile_log:
            log_text += sim_result.compile_log + "\n"
        if sim_result.sim_log:
            log_text += sim_result.sim_log

        parsed = parse_simulation_log(log_text)

        result.tests_passed = parsed.passed
        result.tests_failed = parsed.failed
        result.assertion_failures = len(parsed.assertion_failures)
        result.sim_log_excerpt = log_text[-2000:]  # Last 2KB

        if sim_result.vcd_path:
            result.vcd_path = sim_result.vcd_path

        emit("parse_results", "completed", {
            "status": parsed.overall_status,
            "passed": parsed.passed,
            "failed": parsed.failed,
            "assertions": len(parsed.assertion_failures),
        })

        # ═══════════════════════════════════════════════════════════
        # Step 6: Auto-Fix (if failures and iterations remain)
        # ═══════════════════════════════════════════════════════════
        if parsed.overall_status == "pass":
            result.status = "passed"
            logger.info(f"[{run_id}] All tests passed on iteration {iteration}")
            break

        if iteration >= max_fix_iterations:
            result.status = "failed"
            logger.info(f"[{run_id}] Max fix iterations reached")
            break

        # Attempt auto-fix
        if parsed.errors or parsed.assertion_failures or not sim_result.compile_success:
            emit("auto_fix", "started", {"iteration": iteration})

            patch_id = await _attempt_auto_fix(
                run_id=run_id,
                project_id=project_id,
                parsed=parsed,
                sim_result=sim_result,
                tb_content=tb.content,
                tb_path=str(tb_path),
                rtl_files=rtl_files,
                ai_client=ai_client,
                db_session=db_session,
            )

            if patch_id:
                result.patch_proposals.append(patch_id)
                emit("auto_fix", "proposed", {"patch_id": patch_id})
                # In auto mode, wait for human approval before retrying
                result.status = "awaiting_fix_approval"
                break
            else:
                # No fix possible — stop
                result.status = "failed"
                break

        iteration += 1

    # If we didn't set status yet
    if result.status == "running":
        result.status = "failed" if result.tests_failed > 0 else "passed"

    end = datetime.now(timezone.utc)
    result.duration_ms = int((end - start).total_seconds() * 1000)

    # Persist run result
    if db_session:
        _persist_run_result(result, db_session)

    # Feed evidence back into mental model
    _update_model_evidence(model, result)

    emit("done", result.status, {
        "passed": result.tests_passed,
        "failed": result.tests_failed,
        "duration_ms": result.duration_ms,
    })

    logger.info(
        f"[{run_id}] UnitSim complete: {result.status} "
        f"({result.tests_passed}/{result.tests_passed + result.tests_failed} passed, "
        f"{result.duration_ms}ms)"
    )

    return result


# ═══════════════════════════════════════════════════════════════════════
# Private helpers
# ═══════════════════════════════════════════════════════════════════════


def _coerce_approved_test_plan(plan_data: Any, module_name: str):
    """Convert a staged approved UnitSim plan dict into a TestPlan."""
    from services.verification.test_plan import (
        StimulusStep,
        TestCheck,
        TestPlan,
        TestScenario,
    )

    if isinstance(plan_data, TestPlan):
        return plan_data

    if not isinstance(plan_data, dict):
        raise ValueError("Approved UnitSim plan must be a dict")

    # Accept either the UnitSim plan itself or the full staged plan wrapper.
    if "unitsim" in plan_data and isinstance(plan_data["unitsim"], dict):
        plan_data = plan_data["unitsim"]

    def _filtered(raw: Dict[str, Any], allowed: set[str]) -> Dict[str, Any]:
        return {key: value for key, value in raw.items() if key in allowed}

    scenarios = []
    for index, raw in enumerate(plan_data.get("scenarios") or []):
        if not isinstance(raw, dict):
            continue

        stimulus = [
            StimulusStep(**_filtered(step, {"action", "signal", "value", "cycles", "description"}))
            for step in raw.get("stimulus", [])
            if isinstance(step, dict)
        ]
        checks = [
            TestCheck(**_filtered(check, {"signal", "condition", "expected", "description"}))
            for check in raw.get("checks", [])
            if isinstance(check, dict)
        ]
        scenarios.append(
            TestScenario(
                id=str(raw.get("id") or f"TP-{index + 1:03d}"),
                name=str(raw.get("name") or f"test_{index + 1:03d}"),
                description=str(raw.get("description") or ""),
                requirement_ids=list(raw.get("requirement_ids") or []),
                priority=str(raw.get("priority") or "medium"),
                category=str(raw.get("category") or "functional"),
                precondition=str(raw.get("precondition") or ""),
                stimulus=stimulus,
                checks=checks,
                cleanup=str(raw.get("cleanup") or ""),
            )
        )

    if not scenarios:
        raise ValueError("Approved UnitSim plan does not contain any scenarios")

    return TestPlan(
        module_name=str(plan_data.get("module_name") or module_name or "dut"),
        scenarios=scenarios,
        coverage_goals=dict(plan_data.get("coverage_goals") or {}),
        total_scenarios=len(scenarios),
        estimated_sim_time=str(plan_data.get("estimated_sim_time") or ""),
    )


def _select_simulator(preference: str):
    """Select best available simulator."""
    from services.eda.adapters import icarus, verilator

    if preference == "icarus" and icarus.is_available():
        return icarus
    if preference == "verilator" and verilator.is_available():
        return verilator
    if preference == "auto":
        if icarus.is_available():
            return icarus
        if verilator.is_available():
            return verilator
    return None


def _rtl_include_dirs(rtl_files: List[str]) -> List[str]:
    """Return deterministic include directories for RTL trees with `include files."""
    dirs: List[str] = []
    for raw in rtl_files or []:
        try:
            path = Path(str(raw))
            parent = path.parent if path.suffix else path
            resolved = str(parent.resolve()) if parent.exists() else str(parent)
        except OSError:
            resolved = str(Path(str(raw)).parent)
        if resolved and resolved not in dirs:
            dirs.append(resolved)
    return dirs


async def _attempt_auto_fix(
    run_id: str,
    project_id: str,
    parsed: Any,
    sim_result: Any,
    tb_content: str,
    tb_path: str,
    rtl_files: List[str],
    ai_client: Any,
    db_session: Any,
) -> Optional[str]:
    """Attempt to generate an auto-fix patch for simulation failures."""
    try:
        from services.verification.patch_service import create_patch_proposal

        # Classify the failure
        from agent_tools.debug_tools import classify_failure
        classification = classify_failure(parsed.raw_log)

        # For compile errors targeting the testbench, propose TB fix
        if classification["classification"] in ("syntax", "compilation"):
            # The testbench is the most likely culprit for generated code
            reason = (
                f"Compilation error ({classification['classification']}): "
                f"{parsed.errors[0] if parsed.errors else 'unknown'}"
            )
            proposed_content = await _generate_llm_testbench_fix(
                ai_client=ai_client,
                tb_content=tb_content,
                tb_path=tb_path,
                compile_log=sim_result.compile_log or "",
                sim_log=sim_result.sim_log or "",
                rtl_files=rtl_files,
            )
            if not proposed_content:
                return None

            proposal = create_patch_proposal(
                project_id=project_id,
                source_agent="unitsim",
                title=f"Fix compilation error in tb_{tb_path.split('/')[-1]}",
                reason=reason,
                file_path=tb_path,
                original_content=tb_content,
                proposed_content=proposed_content,
                db_session=db_session,
            )
            return proposal.id

        return None

    except Exception as e:
        logger.warning(f"Auto-fix attempt failed: {e}")
        return None


async def _generate_llm_testbench_fix(
    ai_client: Any,
    tb_content: str,
    tb_path: str,
    compile_log: str,
    sim_log: str,
    rtl_files: List[str],
) -> str:
    """Ask the configured LLM for a complete repaired testbench file."""
    if not ai_client:
        return ""

    import asyncio
    import re

    rtl_context = []
    for rtl_file in rtl_files[:3]:
        try:
            path = Path(rtl_file)
            if path.exists():
                rtl_context.append(f"// FILE: {path.name}\n{path.read_text(encoding='utf-8', errors='ignore')[:2500]}")
        except OSError:
            continue

    system_prompt = (
        "You are a SystemVerilog verification engineer. "
        "Return only the complete corrected testbench file. "
        "Do not change the DUT RTL. Do not include explanations."
    )
    prompt = f"""Repair this generated SystemVerilog testbench so it compiles.

TESTBENCH PATH: {tb_path}

COMPILE LOG:
```
{compile_log[-4000:]}
```

SIM LOG:
```
{sim_log[-2000:]}
```

RTL CONTEXT:
```systemverilog
{chr(10).join(rtl_context)[:6000]}
```

CURRENT TESTBENCH:
```systemverilog
{tb_content[:8000]}
```

Return only the full corrected testbench source.
"""
    try:
        if hasattr(ai_client, "chat"):
            response = ai_client.chat(system_prompt, prompt)
        elif hasattr(ai_client, "generate"):
            response = ai_client.generate(
                prompt,
                system_prompt=system_prompt,
                temperature=0.1,
            )
        else:
            return ""

        if asyncio.iscoroutine(response):
            response = await response

        text = str(response or "").strip()
        code_match = re.search(
            r"```(?:systemverilog|verilog|sv)?\s*([\s\S]*?)```",
            text,
        )
        fixed = (code_match.group(1) if code_match else text).strip()

        tb_module = Path(tb_path).stem
        if not fixed or fixed == tb_content:
            return ""
        if f"module {tb_module}" not in fixed or "endmodule" not in fixed:
            logger.warning("Rejected LLM testbench fix: missing expected module/endmodule")
            return ""
        return fixed

    except Exception as exc:
        logger.warning("LLM testbench fix generation failed: %s", exc)
        return ""


def _persist_run_result(result: UnitSimRunResult, db_session: Any) -> None:
    """Persist UnitSim run result to the DB."""
    try:
        from database.models import Run, RunEvent

        run = Run(
            id=result.run_id,
            project_id=result.project_id,
            status=result.status,
            output_path="",
        )
        db_session.add(run)

        event = RunEvent(
            id=str(uuid.uuid4()),
            run_id=result.run_id,
            event_type="unitsim_result",
            payload=json.dumps({
                "target_module": result.target_module,
                "tests_passed": result.tests_passed,
                "tests_failed": result.tests_failed,
                "assertion_failures": result.assertion_failures,
                "duration_ms": result.duration_ms,
                "phases": result.phases,
            }),
        )
        db_session.add(event)
        db_session.commit()

    except Exception as e:
        logger.warning(f"Could not persist run result: {e}")


def _update_model_evidence(model: Any, result: UnitSimRunResult) -> None:
    """Feed UnitSim evidence back into the mental model."""
    try:
        from services.mental_model.schema import ModelEvidence

        evidence = ModelEvidence(
            id=str(uuid.uuid4()),
            run_id=result.run_id,
            agent="unitsim",
            evidence_type="test_result",
            summary=(
                f"UnitSim: {result.tests_passed}/{result.tests_passed + result.tests_failed} "
                f"tests passed ({result.status})"
            ),
            details={
                "passed": result.tests_passed,
                "failed": result.tests_failed,
                "assertion_failures": result.assertion_failures,
                "duration_ms": result.duration_ms,
            },
            timestamp=result.timestamp,
        )
        if not hasattr(model, "evidence") or model.evidence is None:
            model.evidence = []
        model.evidence.append(evidence)

        # Update unit test statuses in verification intent
        verification = getattr(model, "verification", None)
        for ut in getattr(verification, "unit_tests", []) or []:
            if result.status == "passed":
                if isinstance(ut, dict):
                    ut["status"] = "passed"
                else:
                    ut.status = "passed"
            elif result.tests_failed > 0:
                if isinstance(ut, dict):
                    ut["status"] = "failed"
                else:
                    ut.status = "failed"

    except Exception as e:
        logger.warning(f"Could not update model evidence: {e}")
