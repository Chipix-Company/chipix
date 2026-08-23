"""
UnitSim Tools — Unit-level simulation agent tools.

Uses the NEW Phase 1/2 services (test_plan, testbench_gen, unitsim_loop)
with fallback to legacy original_core agents.
"""

from __future__ import annotations

import json
import logging
import tempfile
from pathlib import Path
from typing import Any

from agent_tools import register_tool

logger = logging.getLogger(__name__)


async def handle_generate_testbench(
    project_id: str,
    target_module: str = "",
    test_type: str = "all",
    db_session: Any = None,
    ai_client: Any = None,
    **kwargs: Any,
) -> str:
    """Generate a SystemVerilog testbench using the mental model."""
    try:
        # Load mental model for context
        model = kwargs.get("mental_model")
        if not model and db_session:
            from agent_tools.mental_model_tools import _load_mental_model
            model = await _load_mental_model(project_id, db_session)

        if not model:
            return json.dumps({"error": "No mental model. Run buildMentalModel first."})

        if not target_module:
            target_module = model.design.top_module

        # ── Try NEW testbench pipeline first ─────────────────────
        try:
            from services.verification.test_plan import generate_test_plan_with_ai
            from services.verification.testbench_gen import generate_testbench_from_plan

            # Step 1: Generate an AI-backed test plan from the mental model
            plan = await generate_test_plan_with_ai(
                model,
                ai_client=ai_client,
                require_llm=False,
            )
            logger.info(
                f"Test plan: {plan.total_scenarios} scenarios for {target_module}"
            )

            # Step 2: Generate testbench from plan
            block_models = getattr(model, "block_models", {}) or {}
            design_block = block_models.get(target_module, model.design) if isinstance(block_models, dict) else model.design
            tb = generate_testbench_from_plan(plan, design_block)

            # Step 3: Write to work directory
            work_dir = kwargs.get("work_dir", "")
            if not work_dir:
                work_dir = tempfile.mkdtemp(prefix=f"unitsim_{target_module}_")
            work_path = Path(work_dir)
            work_path.mkdir(parents=True, exist_ok=True)

            tb_path = work_path / tb.filename
            tb_path.write_text(tb.content, encoding="utf-8")

            return json.dumps({
                "status": "success",
                "target_module": target_module,
                "files_generated": 1,
                "filenames": [tb.filename],
                "test_count": tb.test_count,
                "scenarios": plan.total_scenarios,
                "work_dir": str(work_path),
                "tb_path": str(tb_path),
                "plan_summary": plan.summary,
                "plan_engine": getattr(plan, "generation_engine", "unknown"),
                "llm_used": getattr(plan, "llm_used", False),
            }, indent=2)

        except ImportError as ie:
            logger.info(f"New pipeline not available ({ie}), falling back to legacy")
            # Fall through to legacy
            raise

    except ImportError:
        # ── Fallback: legacy file generator ──────────────────────
        try:
            from original_core.agents.file_generator import FileGenerator

            legacy_spec = _model_to_legacy_spec(model)
            legacy_rtl = _model_to_legacy_rtl(model)
            legacy_mm = _model_to_legacy_mental_model(model)

            generator = FileGenerator(ai_client=ai_client)
            files = await generator.generate_files(
                spec=legacy_spec,
                rtl_analysis=legacy_rtl,
                mental_model=legacy_mm,
            )

            return json.dumps({
                "status": "success",
                "target_module": target_module,
                "files_generated": len(files) if files else 0,
                "filenames": [f.filename for f in files] if files else [],
                "engine": "legacy",
            }, indent=2)

        except ImportError:
            return json.dumps({
                "status": "success",
                "target_module": target_module,
                "files_generated": 0,
                "message": "Testbench generation service not available",
            })

    except Exception as e:
        logger.exception(f"Testbench generation failed: {e}")
        return json.dumps({"error": f"Generation failed: {str(e)}"})


async def handle_run_simulation(
    project_id: str,
    db_session: Any = None,
    ai_client: Any = None,
    **kwargs: Any,
) -> str:
    """Run simulation using iverilog or Verilator."""
    try:
        # ── Try NEW unitsim_loop first ───────────────────────────
        model = kwargs.get("mental_model")
        work_dir = kwargs.get("work_dir", "")

        if model and work_dir:
            try:
                from services.verification.unitsim_loop import run_unitsim_loop

                result = await run_unitsim_loop(
                    project_id=project_id,
                    model=model,
                    work_dir=work_dir,
                    ai_client=ai_client,
                    db_session=db_session,
                )

                return json.dumps({
                    "status": result.status,
                    "run_id": result.run_id,
                    "tests_passed": result.tests_passed,
                    "tests_failed": result.tests_failed,
                    "assertion_failures": result.assertion_failures,
                    "duration_ms": result.duration_ms,
                    "patch_proposals": result.patch_proposals,
                    "log_excerpt": result.sim_log_excerpt[:2000],
                    "engine": "unitsim_loop",
                }, indent=2)

            except ImportError:
                logger.info("unitsim_loop not available, falling back")

        # ── Fallback: direct EDA adapter ─────────────────────────
        from services.eda.adapters.icarus import is_available as icarus_ok
        from services.eda.adapters.verilator import is_available as verilator_ok

        if not icarus_ok() and not verilator_ok():
            return json.dumps({
                "status": "error",
                "error": "No simulator found. Install iverilog or Verilator.",
                "install_hint": "apt install iverilog  OR  brew install icarus-verilog",
            })

        # Get project output path
        output_path = kwargs.get("output_path", "")
        if not output_path and db_session:
            output_path = await _resolve_output_path(project_id, db_session)

        if not output_path:
            return json.dumps({"error": "No output path available"})

        # Use legacy simulation service
        from services.eda.simulation import run_simulation
        result = await run_simulation(output_path)

        return json.dumps({
            "status": "success" if result.get("compile_success") else "failed",
            "compile_success": result.get("compile_success", False),
            "tests_passed": result.get("passed", 0),
            "tests_failed": result.get("failed", 0),
            "assertion_failures": result.get("assertion_failures", 0),
            "log_excerpt": result.get("log", "")[:2000],
            "errors": result.get("errors", [])[:10],
            "engine": "legacy",
        }, indent=2)

    except ImportError:
        return json.dumps({
            "status": "pending",
            "message": "Simulation service not available — check EDA tool installation",
        })
    except Exception as e:
        logger.exception(f"Simulation failed: {e}")
        return json.dumps({"error": f"Simulation failed: {str(e)}"})


# ═══════════════════════════════════════════════════════════════════════
# Conversion helpers (new schema → legacy format)
# ═══════════════════════════════════════════════════════════════════════


def _model_to_legacy_spec(model: Any) -> Any:
    """Convert MentalModelSchema to legacy DesignSpecification."""
    from original_core.core.models import DesignSpecification, PortDefinition, PortDirection

    ports = [
        PortDefinition(
            name=p.name,
            direction=PortDirection(p.direction),
            width=p.width,
            bus_range=p.bus_range,
            port_type=p.port_type,
            description=p.description,
        )
        for p in model.design.ports
    ]

    return DesignSpecification(
        module_name=model.design.top_module,
        description=model.design.description,
        ports=ports,
        functional_requirements=[r.text for r in model.requirements],
        clock_domains=[cd.name for cd in model.design.clock_domains],
        reset_strategy=(
            model.design.clock_domains[0].reset_type
            if model.design.clock_domains else ""
        ),
        edge_cases=[r.text for r in model.requirements if r.priority == "critical"],
    )


def _model_to_legacy_rtl(model: Any) -> Any:
    """Convert MentalModelSchema to legacy RTLAnalysis."""
    from original_core.core.models import RTLAnalysis, ModuleInfo, PortDefinition, PortDirection

    port_map = {
        p.name: PortDefinition(
            name=p.name,
            direction=PortDirection(p.direction),
            width=p.width,
            bus_range=p.bus_range,
        )
        for p in model.design.ports
    }

    modules = [
        ModuleInfo(name=mod_name)
        for mod_name in model.design.modules
    ]

    fsm_states = []
    for fsm in model.design.fsms:
        fsm_states.extend(fsm.states)

    return RTLAnalysis(
        modules=modules,
        top_module=model.design.top_module,
        port_map=port_map,
        fsm_states=fsm_states,
        parameters={p.name: p.default_value for p in model.design.parameters},
        code_style=model.design.code_style,
    )


def _model_to_legacy_mental_model(model: Any) -> Any:
    """Convert MentalModelSchema to legacy MentalModel."""
    from original_core.core.models import (
        MentalModel as LegacyMM,
        TestScenario,
        AssertionTarget,
        CoveragePoint,
    )

    verification_plan = [
        TestScenario(
            name=t.name,
            description=t.description,
            priority=t.priority,
        )
        for t in model.verification.unit_tests
    ]

    assertion_plan = [
        AssertionTarget(
            name=f.name,
            property_description=f.description,
            assertion_type=f.property_type,
            related_signals=f.related_signals,
        )
        for f in model.verification.formal_properties
    ]

    coverage_plan = [
        CoveragePoint(
            name=c.name,
            signal=c.signal,
            cover_type=c.cover_type,
            bins_description=c.bins_description,
        )
        for c in model.verification.coverage_points
    ]

    return LegacyMM(
        verification_plan=verification_plan,
        assertion_plan=assertion_plan,
        coverage_plan=coverage_plan,
    )


async def _resolve_output_path(project_id: str, db_session: Any) -> str:
    """Resolve output path for a project."""
    try:
        from sqlalchemy import select
        from database.models import Run
        stmt = select(Run.output_path).where(
            Run.project_id == project_id,
        ).order_by(Run.created_at.desc()).limit(1)
        result = db_session.execute(stmt)
        return result.scalar_one_or_none() or ""
    except Exception:
        return ""


# Register handlers
register_tool("generateTestbench", handle_generate_testbench)
register_tool("runUnitSimulation", handle_run_simulation)
