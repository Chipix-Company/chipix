"""
UVM Tools — UVM environment creation and update agent tools.

Uses NEW uvm_gen.py service to generate complete UVM environments
from the mental model. Falls back to legacy UVMGenerator if the
new service is unavailable.
"""

from __future__ import annotations

import json
import logging
import tempfile
from typing import Any

from agent_tools import register_tool

logger = logging.getLogger(__name__)


async def handle_generate_uvm(
    project_id: str,
    mode: str = "create",
    db_session: Any = None,
    ai_client: Any = None,
    **kwargs: Any,
) -> str:
    """Generate or update a UVM verification environment."""
    try:
        model = kwargs.get("mental_model")
        if not model and db_session:
            from agent_tools.mental_model_tools import _load_mental_model
            model = await _load_mental_model(project_id, db_session)

        if not model:
            return json.dumps({"error": "No mental model. Run buildMentalModel first."})

        if mode == "create":
            return await _create_uvm_env(model, project_id, ai_client, **kwargs)
        elif mode == "update":
            return await _update_uvm_env(model, project_id, ai_client, **kwargs)
        else:
            return json.dumps({"error": f"Unknown mode: {mode}. Use 'create' or 'update'."})

    except Exception as e:
        logger.exception(f"UVM generation failed: {e}")
        return json.dumps({"error": f"UVM generation failed: {str(e)}"})


async def _create_uvm_env(
    model: Any, project_id: str, ai_client: Any, **kwargs: Any
) -> str:
    """Create a new UVM environment."""

    # ── Try NEW uvm_gen service first ─────────────────────────────
    try:
        from services.verification.uvm_gen import generate_uvm_from_model_async

        work_dir = kwargs.get("work_dir", "")
        if not work_dir:
            top = ""
            if hasattr(model, "design"):
                top = model.design.top_module if hasattr(model.design, "top_module") else ""
            elif isinstance(model, dict):
                top = model.get("design", {}).get("top_module", "")
            work_dir = tempfile.mkdtemp(prefix=f"uvm_{top or project_id[:8]}_")

        target_module = kwargs.get("target_module", "")

        output = await generate_uvm_from_model_async(
            model=model,
            work_dir=work_dir,
            target_module=target_module,
            ai_client=ai_client,
            generation_mode="hybrid" if ai_client else "scaffold",
            require_llm=False,
        )

        return json.dumps({
            "status": "success",
            "mode": "create",
            "files_generated": len(output.all_files),
            "summary": output.summary,
            "files": output.to_dict(),
            "work_dir": work_dir,
            "engine": "uvm_gen_hybrid" if output.llm_called else "uvm_gen_scaffold",
            "llm_called": output.llm_called,
            "llm_enhanced_files": len(output.llm_enhanced_files),
            "llm_errors": output.llm_errors,
            "message": (
                "UVM environment generated. "
                "Requires a UVM-compatible simulator (VCS, Questa, Xcelium) to run."
            ),
        }, indent=2)

    except ImportError:
        logger.info("uvm_gen not available, trying legacy")

    # ── Fallback: legacy UVM generator ────────────────────────────
    try:
        from original_core.agents.uvm_generator import UVMGenerator
        from agent_tools.unitsim_tools import (
            _model_to_legacy_spec,
            _model_to_legacy_rtl,
            _model_to_legacy_mental_model,
        )

        legacy_spec = _model_to_legacy_spec(model)
        legacy_rtl = _model_to_legacy_rtl(model)
        legacy_mm = _model_to_legacy_mental_model(model)

        generator = UVMGenerator(ai_client=ai_client)
        files = await generator.generate(
            spec=legacy_spec,
            rtl_analysis=legacy_rtl,
            mental_model=legacy_mm,
        )

        return json.dumps({
            "status": "success",
            "mode": "create",
            "files_generated": len(files) if files else 0,
            "filenames": [f.filename for f in files] if files else [],
            "engine": "legacy",
            "message": "UVM environment generated (legacy engine).",
        }, indent=2)

    except (ImportError, AttributeError) as e:
        # Neither available — return planned file list
        top = ""
        if hasattr(model, "design"):
            top = model.design.top_module if hasattr(model.design, "top_module") else "dut"
        elif isinstance(model, dict):
            top = model.get("design", {}).get("top_module", "dut")

        return json.dumps({
            "status": "planned",
            "mode": "create",
            "files_generated": 0,
            "message": (
                f"UVM generation engines not available. "
                f"Environment would include 12 files for {top}."
            ),
            "planned_files": [
                f"{top}_pkg.sv", f"{top}_if.sv", f"{top}_seq_item.sv",
                f"{top}_driver.sv", f"{top}_monitor.sv", f"{top}_scoreboard.sv",
                f"{top}_agent.sv", f"{top}_env.sv", f"{top}_coverage.sv",
                f"{top}_test.sv", f"top_tb.sv", f"{top}_uvm.f",
            ],
        }, indent=2)


async def _update_uvm_env(
    model: Any, project_id: str, ai_client: Any, **kwargs: Any
) -> str:
    """Update an existing UVM environment with surgical diffs."""
    try:
        from original_core.agents.uvm_updater import UVMUpdater

        updater = UVMUpdater(ai_client=ai_client)
        uvm_files = model.project_scan.uvm_files if hasattr(model, 'project_scan') and model.project_scan else []

        if not uvm_files:
            return json.dumps({
                "status": "error",
                "error": "No existing UVM files found. Use mode='create' instead.",
            })

        return json.dumps({
            "status": "success",
            "mode": "update",
            "existing_uvm_files": uvm_files[:20],
            "message": "UVM updater ready. Specify which components to update.",
        }, indent=2)

    except ImportError:
        return json.dumps({
            "status": "error",
            "mode": "update",
            "message": "UVM updater module not available. Use mode='create' to regenerate.",
        })


# Register handler
register_tool("generateUVMEnvironment", handle_generate_uvm)
