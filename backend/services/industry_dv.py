from __future__ import annotations

from collections import Counter
from typing import Any


VPLAN_SCHEMA_VERSION = "dv.vplan.1"
SIGNOFF_SCHEMA_VERSION = "dv.signoff.1"


def build_vplan_from_mental_model(model: dict[str, Any]) -> dict[str, Any]:
    """Create an industry-style vPlan view from the source-grounded model.

    The vPlan is intentionally conservative: generated tests/assertions/coverage
    are marked as planned intent until a real tool result supplies evidence.
    """

    requirements = _as_list(model.get("requirements"))
    verification = _as_dict(model.get("verification_intent"))
    visualizer = _as_dict(model.get("visualizer"))
    matrix_by_req = {
        row.get("requirement_id"): row
        for row in _as_list(visualizer.get("requirement_matrix"))
        if row.get("requirement_id")
    }

    rows = []
    for requirement in requirements:
        req_id = str(requirement.get("id") or "")
        matrix_row = matrix_by_req.get(req_id) or _matrix_row_from_intent(
            verification, req_id
        )
        intent_count = sum(
            len(_as_list(matrix_row.get(key)))
            for key in (
                "unit_tests",
                "formal_properties",
                "coverage_points",
                "uvm_scenarios",
            )
        )
        closure_status = "planned" if intent_count else "missing_intent"
        rows.append(
            {
                "requirement_id": req_id,
                "text": requirement.get("text") or "",
                "source_ref": requirement.get("source_ref") or {},
                "rtl_refs": requirement.get("related_rtl") or [],
                "verification_hint": requirement.get("verification_hint") or "general",
                "confidence": requirement.get("confidence"),
                "owner": "unassigned",
                "priority": _priority_for_requirement(requirement),
                "status": closure_status,
                "unit_tests": _as_list(matrix_row.get("unit_tests")),
                "formal_properties": _as_list(matrix_row.get("formal_properties")),
                "coverage_points": _as_list(matrix_row.get("coverage_points")),
                "uvm_scenarios": _as_list(matrix_row.get("uvm_scenarios")),
                "evidence": [],
                "waivers": [],
            }
        )

    summary = Counter(row["status"] for row in rows)
    return {
        "schema_version": VPLAN_SCHEMA_VERSION,
        "scope": "block",
        "stage": _verification_stage(rows, model),
        "status": "ready_for_execution" if rows and not summary["missing_intent"] else "needs_planning",
        "requirements": rows,
        "summary": {
            "total_requirements": len(rows),
            "planned_requirements": int(summary["planned"]),
            "missing_intent_requirements": int(summary["missing_intent"]),
            "verified_requirements": 0,
            "waived_requirements": 0,
        },
        "required_evidence_types": [
            "simulation_result",
            "coverage_result",
            "formal_result",
            "debug_resolution",
            "signoff_decision",
        ],
    }


def build_industry_readiness(
    *,
    mental_model: dict[str, Any] | None,
    model_freshness: dict[str, Any] | None = None,
    toolchain: dict[str, dict[str, Any]] | None = None,
    active_spec_present: bool = False,
    active_rtl_present: bool = False,
    open_patch_count: int = 0,
    latest_run_status: str | None = None,
    latest_run_summary: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return conservative DV readiness gates.

    These gates are product-facing truth labels. They should block signoff until
    real measured evidence exists; planned collateral alone is not a pass.
    """

    toolchain = toolchain or {}
    model = mental_model or {}
    vplan = _as_dict(model.get("vplan")) or build_vplan_from_mental_model(model)
    open_questions = _as_list(model.get("open_questions"))
    planned_requirements = int(
        _as_dict(vplan.get("summary")).get("planned_requirements") or 0
    )
    total_requirements = int(
        _as_dict(vplan.get("summary")).get("total_requirements") or 0
    )

    simulator_profiles = _simulator_profiles(toolchain)
    has_real_simulator = any(
        profile["status"] in {"available", "configured"}
        for profile in simulator_profiles
    )
    has_static_tool = _tool_available(toolchain, "verible") or _tool_available(
        toolchain, "verilator"
    )
    has_synthesis_tool = _tool_available(toolchain, "yosys")
    latest_run_summary = latest_run_summary or {}
    block_smoke_passed = (
        latest_run_status == "completed"
        and latest_run_summary.get("reason") == "smoke_passed"
    )

    gates = [
        _gate(
            "sources.active_spec",
            "Active specification selected",
            "setup",
            "pass" if active_spec_present else "missing",
            "Required before any requirement-driven verification can start.",
        ),
        _gate(
            "sources.active_rtl",
            "Active RTL selected",
            "setup",
            "pass" if active_rtl_present else "missing",
            "Required before compile, simulation, lint, or formal checks can run.",
        ),
        _gate(
            "model.fresh",
            "Persisted mental model matches active sources",
            "planning",
            "pass"
            if model_freshness and model_freshness.get("fresh")
            else ("stale" if mental_model else "missing"),
            (model_freshness or {}).get("message")
            or "Build a source-grounded mental model from active artifacts.",
        ),
        _gate(
            "vplan.exists",
            "vPlan / requirement matrix exists",
            "planning",
            "pass" if total_requirements > 0 else "missing",
            f"{total_requirements} requirements, {planned_requirements} with planned verification intent.",
        ),
        _gate(
            "questions.closed",
            "No blocking open design questions",
            "planning",
            "pass"
            if not any(q.get("severity") == "blocking" for q in open_questions)
            else "blocked",
            f"{len(open_questions)} open questions in the current model.",
        ),
        _gate(
            "tools.simulator",
            "Real simulator profile available",
            "execution",
            "pass" if has_real_simulator else "blocked",
            "Use Icarus for directed SV, Verilator for supported block flows, or a commercial simulator for UVM.",
        ),
        _gate(
            "execution.rtl_plus_tb",
            "DUT RTL and testbench compiled together",
            "execution",
            "pass" if block_smoke_passed else "blocked",
            (
                f"Latest block smoke passed for top module {latest_run_summary.get('top_module')}."
                if block_smoke_passed
                else "No persisted compile evidence is attached to the vPlan yet."
            ),
        ),
        _gate(
            "coverage.measured",
            "Coverage collected and linked to requirements",
            "coverage",
            "blocked",
            "Need code, functional, assertion, or formal coverage artifacts before closure.",
        ),
        _gate(
            "formal.measured",
            "Formal properties run or waived",
            "formal",
            "blocked",
            "Generated SVA intent is not signoff evidence until a formal tool run is parsed.",
        ),
        _gate(
            "static.clean",
            "Lint/static/synthesis checks clean or waived",
            "static",
            "blocked" if not (has_static_tool or has_synthesis_tool) else "needs_run",
            "Run lint/static/synthesis adapters and attach results to the project.",
        ),
        _gate(
            "patches.approved",
            "No pending unreviewed patches",
            "review",
            "pass" if open_patch_count == 0 else "blocked",
            f"{open_patch_count} patch proposals are waiting for approval.",
        ),
        _gate(
            "regression.history",
            "Regression history exists",
            "regression",
            "needs_run" if latest_run_status else "missing",
            "Need smoke/focused/full regression records with tests, seeds, and failure signatures.",
        ),
    ]

    status_counts = Counter(gate["status"] for gate in gates)
    required_blockers = [
        gate
        for gate in gates
        if gate["required"]
        and gate["status"] in {"missing", "blocked", "fail", "stale"}
    ]
    overall_status = "signoff_ready" if not required_blockers else "not_signoff_ready"
    if any(gate["status"] == "blocked" for gate in required_blockers):
        overall_status = "blocked"

    return {
        "schema_version": SIGNOFF_SCHEMA_VERSION,
        "overall_status": overall_status,
        "stage": vplan.get("stage") or "V0",
        "score": round(status_counts["pass"] / len(gates), 2) if gates else 0.0,
        "gates": gates,
        "summary": {
            "pass": int(status_counts["pass"]),
            "needs_run": int(status_counts["needs_run"]),
            "missing": int(status_counts["missing"]),
            "blocked": int(status_counts["blocked"]),
            "stale": int(status_counts["stale"]),
            "total": len(gates),
        },
        "simulator_profiles": simulator_profiles,
        "next_actions": _next_actions(gates),
    }


def _matrix_row_from_intent(
    verification: dict[str, Any], requirement_id: str
) -> dict[str, list[str]]:
    return {
        "unit_tests": _ids_related_to(
            _as_list(verification.get("unit_tests")), requirement_id
        ),
        "formal_properties": _ids_related_to(
            _as_list(verification.get("formal_properties")), requirement_id
        ),
        "coverage_points": _ids_related_to(
            _as_list(verification.get("coverage_points")), requirement_id
        ),
        "uvm_scenarios": _ids_related_to(
            _as_list(verification.get("uvm_scenarios")), requirement_id
        ),
    }


def _ids_related_to(items: list[dict[str, Any]], requirement_id: str) -> list[str]:
    ids: list[str] = []
    for item in items:
        if requirement_id in set(_as_list(item.get("related_requirements"))):
            ids.append(str(item.get("id") or ""))
    return [item_id for item_id in ids if item_id]


def _priority_for_requirement(requirement: dict[str, Any]) -> str:
    hint = requirement.get("verification_hint")
    if hint in {"reset", "boundary", "protocol", "negative"}:
        return "high"
    if hint == "performance":
        return "medium"
    return "normal"


def _verification_stage(rows: list[dict[str, Any]], model: dict[str, Any]) -> str:
    if not model or not rows:
        return "V0"
    if any(row["status"] == "missing_intent" for row in rows):
        return "V0"
    return "V1"


def _simulator_profiles(toolchain: dict[str, dict[str, Any]]) -> list[dict[str, str]]:
    profiles = [
        {
            "id": "directed_sv_icarus",
            "label": "Directed SV / Icarus",
            "status": "available"
            if _tool_available(toolchain, "iverilog") and _tool_available(toolchain, "vvp")
            else "missing",
            "use_for": "small non-UVM smoke tests",
        },
        {
            "id": "block_verilator",
            "label": "Block Simulation / Verilator",
            "status": "available" if _tool_available(toolchain, "verilator") else "missing",
            "use_for": "synthesizable block RTL and cocotb-like harnesses",
        },
        {
            "id": "commercial_uvm",
            "label": "UVM / Questa, VCS, or Xcelium",
            "status": "configured"
            if any(
                _tool_available(toolchain, name)
                for name in ("questa", "vcs", "xcelium")
            )
            else "not_configured",
            "use_for": "full UVM environments",
        },
    ]
    return profiles


def _next_actions(gates: list[dict[str, Any]]) -> list[str]:
    actions = []
    by_id = {gate["id"]: gate for gate in gates}
    if by_id["sources.active_spec"]["status"] != "pass":
        actions.append("Select or upload an active specification artifact.")
    if by_id["sources.active_rtl"]["status"] != "pass":
        actions.append("Select or upload active RTL source or an RTL project archive.")
    if by_id["model.fresh"]["status"] != "pass":
        actions.append("Build or refresh the source-grounded mental model.")
    if by_id["vplan.exists"]["status"] != "pass":
        actions.append("Create a vPlan with requirements mapped to tests, assertions, and coverage.")
    if by_id["tools.simulator"]["status"] != "pass":
        actions.append("Configure a real simulator or use the directed SV/Verilator block flow.")
    if by_id["execution.rtl_plus_tb"]["status"] != "pass":
        actions.append("Run a block smoke test that compiles DUT RTL and testbench together.")
    return actions[:6]


def _gate(
    gate_id: str,
    label: str,
    category: str,
    status: str,
    detail: str | None = None,
    *,
    required: bool = True,
) -> dict[str, Any]:
    return {
        "id": gate_id,
        "label": label,
        "category": category,
        "status": status,
        "required": required,
        "detail": detail,
    }


def _tool_available(toolchain: dict[str, dict[str, Any]], name: str) -> bool:
    return bool(_as_dict(toolchain.get(name)).get("available"))


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []
