"""
Formal Tools — SVA generation and formal verification agent tools.

Uses NEW formal_gen.py service to generate SVA + bind + sby files,
with fallback to legacy formal_agent.py.
"""

from __future__ import annotations

import json
import logging
import tempfile
from pathlib import Path
from typing import Any, List, Optional

from agent_tools import register_tool

logger = logging.getLogger(__name__)


async def handle_generate_formal(
    project_id: str,
    target_module: str = "",
    property_types: Optional[List[str]] = None,
    db_session: Any = None,
    ai_client: Any = None,
    **kwargs: Any,
) -> str:
    """Generate SVA assertions, bind modules, and sby scripts."""
    if property_types is None:
        property_types = ["assert", "assume", "cover"]

    try:
        model = kwargs.get("mental_model")
        if not model and db_session:
            from agent_tools.mental_model_tools import _load_mental_model
            model = await _load_mental_model(project_id, db_session)

        if not model:
            return json.dumps({"error": "No mental model. Run buildMentalModel first."})

        if not target_module:
            target_module = model.design.top_module

        # ── Try NEW formal_gen pipeline first ────────────────────
        try:
            from services.verification.formal_gen import generate_formal_from_model_async

            work_dir = kwargs.get("work_dir", "")
            if not work_dir:
                work_dir = tempfile.mkdtemp(prefix=f"formal_{target_module}_")

            output = await generate_formal_from_model_async(
                model,
                work_dir=work_dir,
                rtl_files=_rtl_files_from_model(model),
                ai_client=ai_client,
                require_llm=False,
            )

            return json.dumps({
                "status": "success",
                "target_module": target_module,
                "properties_count": output.property_count,
                "assumptions_count": getattr(output, "assumption_count", 0),
                "cover_count": getattr(output, "cover_count", 0),
                "property_types": property_types,
                "files_generated": len(getattr(output, "all_files", []) or []),
                "files": {
                    "sva": output.assertion_file,
                    "bind": output.bind_file,
                    "sby": output.sby_file,
                    "jasper_tcl": getattr(output, "jasper_tcl_file", ""),
                },
                "work_dir": work_dir,
                "rtl_source_files": len(getattr(output, "rtl_files", []) or []),
                "llm_called": getattr(output, "llm_called", False),
                "llm_used": getattr(output, "llm_used", False),
                "llm_enhanced_files": len(getattr(output, "llm_enhanced_files", []) or []),
                "engine": "formal_gen_llm_hybrid",
            }, indent=2)

        except ImportError:
            logger.info("formal_gen not available, trying legacy")

        # ── Fallback: legacy formal agent ────────────────────────
        try:
            from original_core.agents.formal_agent import FormalAgent
            from agent_tools.unitsim_tools import (
                _model_to_legacy_spec,
                _model_to_legacy_rtl,
                _model_to_legacy_mental_model,
            )

            legacy_spec = _model_to_legacy_spec(model)
            legacy_rtl = _model_to_legacy_rtl(model)
            legacy_mm = _model_to_legacy_mental_model(model)

            formal = FormalAgent(ai_client=ai_client)
            result = await formal.generate(
                spec=legacy_spec,
                rtl_analysis=legacy_rtl,
                mental_model=legacy_mm,
            )

            return json.dumps({
                "status": "success",
                "target_module": target_module,
                "properties_count": len(model.verification.formal_properties),
                "property_types": property_types,
                "files_generated": len(result) if result else 0,
                "engine": "legacy",
            }, indent=2)

        except (ImportError, AttributeError):
            # Neither available — return properties from model
            return json.dumps({
                "status": "success",
                "target_module": target_module,
                "properties_count": len(model.verification.formal_properties),
                "properties": [
                    {
                        "id": fp.id,
                        "name": fp.name,
                        "type": fp.property_type,
                        "description": fp.description,
                        "signals": fp.related_signals,
                    }
                    for fp in model.verification.formal_properties
                    if fp.property_type in property_types
                ],
                "message": "Formal properties planned from mental model.",
            }, indent=2)

    except Exception as e:
        logger.exception(f"Formal generation failed: {e}")
        return json.dumps({"error": f"Formal generation failed: {str(e)}"})


async def handle_run_formal(
    project_id: str,
    db_session: Any = None,
    ai_client: Any = None,
    **kwargs: Any,
) -> str:
    """Run formal verification using SymbiYosys."""
    try:
        from demo import demo_mode_enabled

        if demo_mode_enabled():
            from demo.scenario import formal_result

            return json.dumps(formal_result(project_id), indent=2)

        # ── Check for SymbiYosys ──────────────────────────────────
        from services.eda.symbiyosys import _find_sby_binary, run_formal_verification, run_sby

        sby_bin = _find_sby_binary()
        if not sby_bin:
            return json.dumps({
                "status": "error",
                "error": "SymbiYosys (sby) not installed or not on PATH.",
                "install_hint": "Install SymbiYosys: https://symbiyosys.readthedocs.io/",
                "supported_tools": ["SymbiYosys (open-source)", "Cadence Jasper"],
            })

        # ── Get work_dir, RTL files, and SVA file ────────────────
        work_dir = kwargs.get("work_dir", "")
        rtl_path = kwargs.get("rtl_path", "")
        sva_file = kwargs.get("sva_file", "")
        top_module = kwargs.get("target_module", "")
        mode = kwargs.get("mode", "bmc")
        depth = kwargs.get("depth", 20)
        model = kwargs.get("mental_model")
        generated_sby_file = ""

        # If no SVA file given, try to generate one
        if not sva_file:
            if not model and db_session:
                from agent_tools.mental_model_tools import _load_mental_model
                model = await _load_mental_model(project_id, db_session)

            if model:
                try:
                    from services.verification.formal_gen import generate_formal_from_model_async
                    if not work_dir:
                        work_dir = tempfile.mkdtemp(prefix=f"formal_{project_id[:8]}_")
                    output = await generate_formal_from_model_async(
                        model,
                        work_dir=work_dir,
                        rtl_files=_rtl_files_from_model(model),
                        ai_client=ai_client,
                        require_llm=False,
                    )
                    sva_file = output.assertion_file
                    generated_sby_file = getattr(output, "sby_file", "")
                    kwargs["generated_formal_files"] = getattr(output, "all_files", [])
                    if not top_module:
                        top_module = model.design.top_module if hasattr(model, 'design') else ""
                except Exception as e:
                    logger.warning(f"Auto SVA generation failed: {e}")

        if not sva_file:
            return json.dumps({
                "status": "error",
                "error": "No SVA file available. Run generateFormalProperties first.",
            })

        # ── Collect RTL files ─────────────────────────────────────
        rtl_files = []
        if rtl_path:
            rtl_p = Path(rtl_path)
            if rtl_p.is_dir():
                rtl_files = [str(f) for f in rtl_p.rglob("*.sv")] + \
                            [str(f) for f in rtl_p.rglob("*.v")]
            elif rtl_p.is_file():
                rtl_files = [str(rtl_p)]
        if not rtl_files and work_dir:
            # Try to find RTL in work dir
            wdir = Path(work_dir)
            rtl_files = [str(f) for f in wdir.rglob("*.sv") if "_sva" not in f.name] + \
                        [str(f) for f in wdir.rglob("*.v") if "_tb" not in f.name]
        if not rtl_files and model:
            rtl_files = _rtl_files_from_model(model)

        if not work_dir:
            work_dir = tempfile.mkdtemp(prefix=f"formal_{project_id[:8]}_")

        # ── Execute ───────────────────────────────────────────────
        if generated_sby_file:
            result = run_sby(generated_sby_file, work_dir, timeout_seconds=300)
        else:
            result = run_formal_verification(
                rtl_files=rtl_files,
                sva_file=sva_file,
                top_module=top_module or "top",
                work_dir=work_dir,
                mode=mode,
                depth=int(depth),
            )

        payload = result.to_dict()
        payload["rtl_source_files"] = len(rtl_files)
        payload["generated_formal_files"] = kwargs.get("generated_formal_files") or []
        return json.dumps(payload, indent=2)

    except ImportError:
        return json.dumps({
            "status": "error",
            "error": "SymbiYosys runner not available",
            "install_hint": "Install SymbiYosys: https://symbiyosys.readthedocs.io/",
        })
    except Exception as e:
        logger.exception(f"Formal run failed: {e}")
        return json.dumps({"error": f"Formal run failed: {str(e)}"})


def _rtl_files_from_model(model: Any) -> List[str]:
    """Resolve RTL files from the active mental model."""
    try:
        from services.verification.formal_gen import resolve_rtl_files_from_model

        return resolve_rtl_files_from_model(model)
    except Exception:
        return []


# Register handlers
register_tool("generateFormalProperties", handle_generate_formal)
register_tool("runFormalVerification", handle_run_formal)
