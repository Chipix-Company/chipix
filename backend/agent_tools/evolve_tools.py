"""
Evolve Tools — Design-change detection and auto-adaptation.

ChipStack "Evolve" feature: when RTL changes, automatically:
  1. Detect what changed (ports, FSMs, logic)
  2. Incrementally update the mental model
  3. Auto-heal testbenches (interface, driver, monitor, coverage)
  4. Predict which tests will fail
  5. Generate minimal regression suite

Wraps SelfHealingTestbench + LivingMentalModel + RegressionIntelligence.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from agent_tools import register_tool

logger = logging.getLogger(__name__)


async def handle_detect_design_changes(
    project_id: str,
    old_rtl: str = "",
    new_rtl: str = "",
    db_session: Any = None,
    ai_client: Any = None,
    **kwargs: Any,
) -> str:
    """
    Detect RTL changes and return structured change list.

    This is Step 1 of the Evolve flow:
      detectDesignChanges → evolveVerification
    """
    if not old_rtl or not new_rtl:
        return json.dumps({
            "error": "Both old_rtl and new_rtl are required",
            "hint": "Provide the old and new RTL content to compare",
        })

    try:
        from services.verification.evolve_service import SelfHealingTestbench, RTLChange

        # Use a lightweight instance (no LLM needed for detection)
        healer = SelfHealingTestbench(ai_client=ai_client)
        changes = healer.detect_rtl_changes(old_rtl, new_rtl)

        if not changes:
            return json.dumps({
                "status": "no_changes",
                "message": "No structural RTL changes detected",
                "changes_count": 0,
            })

        # Classify changes by impact
        port_changes = [c for c in changes if "port" in c.change_type]
        state_changes = [c for c in changes if "state" in c.change_type]
        logic_changes = [c for c in changes if c.change_type == "logic_changed"]

        return json.dumps({
            "status": "changes_detected",
            "changes_count": len(changes),
            "changes": [
                {
                    "type": c.change_type,
                    "name": c.name,
                    "old_value": c.old_value,
                    "new_value": c.new_value,
                    "impact": c.impact,
                }
                for c in changes
            ],
            "summary": {
                "port_changes": len(port_changes),
                "state_changes": len(state_changes),
                "logic_changes": len(logic_changes),
            },
            "recommendation": _recommend_action(port_changes, state_changes, logic_changes),
            "_instruction_for_llm": (
                "Design changes detected. Present the summary to the user and ask: "
                "'Should I evolve the verification environment to match these RTL changes?' "
                "If user agrees, call evolveVerification."
            ),
        }, indent=2)

    except ImportError:
        # Fallback: simple diff-based detection
        return _fallback_detect(old_rtl, new_rtl)
    except Exception as e:
        logger.exception(f"Change detection failed: {e}")
        return json.dumps({"error": f"Change detection failed: {str(e)}"})


async def handle_evolve_verification(
    project_id: str,
    old_rtl: str = "",
    new_rtl: str = "",
    db_session: Any = None,
    ai_client: Any = None,
    **kwargs: Any,
) -> str:
    """
    Auto-evolve the entire verification environment after RTL changes.

    This is the main Evolve tool that:
      1. Detects changes
      2. Heals testbenches
      3. Updates mental model
      4. Predicts test failures
      5. Returns what was updated and what to re-run
    """
    if not new_rtl:
        return json.dumps({"error": "new_rtl is required"})

    results = {
        "status": "evolved",
        "changes_detected": [],
        "testbench_healed": [],
        "model_updated": False,
        "test_predictions": [],
        "recommended_suite": [],
    }

    try:
        # ── Step 1: Detect changes ────────────────────────────────
        changes = []
        if old_rtl:
            try:
                from services.verification.evolve_service import SelfHealingTestbench
                healer = SelfHealingTestbench(ai_client=ai_client)
                changes = healer.detect_rtl_changes(old_rtl, new_rtl)
                results["changes_detected"] = [
                    {"type": c.change_type, "name": c.name, "impact": c.impact}
                    for c in changes
                ]
            except ImportError:
                logger.warning("SelfHealingTestbench not available")

        # ── Step 2: Heal testbenches ──────────────────────────────
        if changes:
            try:
                tb_files = kwargs.get("testbench_files", {})
                if tb_files and ai_client:
                    actions = await healer.auto_heal(changes, tb_files, new_rtl)
                    results["testbench_healed"] = [
                        {
                            "file": a.target_file,
                            "action": a.change_type,
                            "description": a.description,
                            "applied": a.applied,
                        }
                        for a in actions
                    ]
            except Exception as e:
                logger.warning(f"Auto-heal failed: {e}")

        # ── Step 3: Update mental model ───────────────────────────
        try:
            model = kwargs.get("mental_model")
            if not model and db_session:
                from agent_tools.mental_model_tools import _load_mental_model
                model = await _load_mental_model(project_id, db_session)

            if model:
                # Re-parse the RTL to rebuild structural info
                from services.mental_model.builder import parse_rtl_structure
                import tempfile
                from pathlib import Path

                # Write new RTL to temp file for parsing
                tmp = tempfile.NamedTemporaryFile(
                    mode="w", suffix=".sv", delete=False, encoding="utf-8"
                )
                tmp.write(new_rtl)
                tmp.close()

                new_parse = parse_rtl_structure(tmp.name)
                Path(tmp.name).unlink(missing_ok=True)

                if new_parse:
                    results["model_updated"] = True
                    results["new_structure"] = {
                        "modules": len(new_parse.get("modules", [])),
                        "line_count": new_parse.get("line_count", 0),
                    }
        except Exception as e:
            logger.warning(f"Model update failed: {e}")

        # ── Step 4: Predict test failures ─────────────────────────
        if old_rtl and changes:
            try:
                from services.verification.evolve_service import RegressionIntelligence
                import difflib

                regr = RegressionIntelligence()

                # Get test names from mental model or kwargs
                test_names = kwargs.get("test_names", [])
                if not test_names:
                    # Try to infer from changes
                    test_names = [
                        f"test_{c.name}" for c in changes
                        if c.change_type in ("port_added", "port_removed", "state_added")
                    ]
                    # Add standard tests
                    test_names.extend([
                        "test_reset", "test_basic_operation", "test_stress",
                        "test_boundary", "test_protocol",
                    ])

                # Compute diff for prediction
                rtl_diff = "\n".join(difflib.unified_diff(
                    old_rtl.splitlines(), new_rtl.splitlines(),
                    fromfile="old.sv", tofile="new.sv",
                ))

                predictions = regr.predict_failures(rtl_diff, test_names)
                results["test_predictions"] = [
                    {
                        "test": p.test_name,
                        "fail_probability": round(p.fail_probability, 2),
                        "reasoning": p.reasoning,
                        "priority": p.priority,
                    }
                    for p in predictions[:10]
                ]

                # Minimal regression suite
                suite = regr.generate_minimal_suite(predictions, max_tests=5)
                results["recommended_suite"] = suite

            except Exception as e:
                logger.warning(f"Regression prediction failed: {e}")

        # ── Summary ───────────────────────────────────────────────
        results["summary"] = (
            f"{len(results['changes_detected'])} changes detected, "
            f"{len(results['testbench_healed'])} files healed, "
            f"model {'updated' if results['model_updated'] else 'unchanged'}, "
            f"{len(results['test_predictions'])} test predictions"
        )

        results["_instruction_for_llm"] = (
            "Show the user: (1) what changed in the RTL, (2) what testbench files were healed, "
            "(3) which tests are predicted to fail. Ask: "
            "'Should I re-run the recommended regression suite?'"
        )

        return json.dumps(results, indent=2)

    except Exception as e:
        logger.exception(f"Evolve failed: {e}")
        return json.dumps({"error": f"Evolve failed: {str(e)}"})


# ─── Fallback Detection ──────────────────────────────────────────────

def _fallback_detect(old_rtl: str, new_rtl: str) -> str:
    """Simple diff-based change detection without SelfHealingTestbench."""
    import difflib
    import re

    diff = list(difflib.unified_diff(
        old_rtl.splitlines(), new_rtl.splitlines(),
        fromfile="old.sv", tofile="new.sv",
    ))

    added = sum(1 for l in diff if l.startswith("+") and not l.startswith("+++"))
    removed = sum(1 for l in diff if l.startswith("-") and not l.startswith("---"))

    # Detect port-level changes
    old_ports = set(re.findall(r"(?:input|output|inout)\s+\w+\s*(?:\[.*?\])?\s*(\w+)", old_rtl))
    new_ports = set(re.findall(r"(?:input|output|inout)\s+\w+\s*(?:\[.*?\])?\s*(\w+)", new_rtl))

    changes = []
    for p in new_ports - old_ports:
        changes.append({"type": "port_added", "name": p, "impact": "interface"})
    for p in old_ports - new_ports:
        changes.append({"type": "port_removed", "name": p, "impact": "interface"})

    return json.dumps({
        "status": "changes_detected" if changes or added > 0 else "no_changes",
        "changes_count": len(changes),
        "changes": changes,
        "diff_stats": {"added": added, "removed": removed},
        "engine": "fallback",
    }, indent=2)


def _recommend_action(port_changes, state_changes, logic_changes) -> str:
    """Generate a human-readable recommendation."""
    parts = []
    if port_changes:
        parts.append(f"Port changes require testbench interface + driver updates")
    if state_changes:
        parts.append(f"FSM state changes require coverage and test updates")
    if logic_changes:
        parts.append(f"Logic changes may affect scoreboard reference model")
    if not parts:
        return "No action needed"
    return ". ".join(parts) + ". Run evolveVerification to auto-adapt."


# Register tools
register_tool("detectDesignChanges", handle_detect_design_changes)
register_tool("evolveVerification", handle_evolve_verification)
