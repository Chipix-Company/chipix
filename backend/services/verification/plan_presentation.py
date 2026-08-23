"""Presentation layer for staged verification plans.

The generated plan shown to users is the baseline contract between TruthCore,
the planning agents, and the generation agents. Keep it concise, but make it
read like a senior DV engineer's reviewable vPlan: scope, tests, checkers,
coverage, risks, and closure gates.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional


STRATEGY_LABELS = {
    "unitsim": "unit simulation",
    "formal": "formal verification",
    "uvm": "UVM environment",
    "all": "all verification strategies",
}

DEFAULT_DV_OBJECTIVES = [
    "Reset and initialization behavior reaches a known legal state.",
    "Nominal legal transactions or operations produce the expected observable outputs.",
    "Boundary values, counter limits, FIFO/register limits, and min/max data values are exercised.",
    "Protocol handshakes, ordering, backpressure, and stalled-transfer behavior are checked when present.",
    "Illegal/error/underflow/overflow paths are either checked or captured as open questions if the spec is ambiguous.",
    "Stress or randomized regression runs combine legal operations to expose ordering and state-transition bugs.",
]

DEFAULT_CLOSURE_GATES = [
    "Plan review approved before execution.",
    "Generated collateral passes static validation and compile/lint gate.",
    "Scoreboard or assertions check every high-priority requirement that is observable from the model.",
    "Functional coverage covers requirements, protocol states, legal/illegal transitions, and boundary bins.",
    "Simulator or formal logs feed back into TruthCore evidence before closure.",
]


def _collapse_text(value: Any, max_chars: int = 320) -> str:
    text = " ".join(str(value or "").split())
    if len(text) > max_chars:
        return text[: max_chars - 3].rstrip() + "..."
    return text


def _source_ref_label(source_ref: Any) -> str:
    if not isinstance(source_ref, dict):
        return ""
    filename = str(source_ref.get("file") or source_ref.get("path") or "").strip()
    line = source_ref.get("line")
    if not filename:
        return ""
    try:
        line_no = int(line or 0)
    except (TypeError, ValueError):
        line_no = 0
    return f"{filename}:{line_no}" if line_no > 0 else filename


def _first_text(item: Dict[str, Any], fields: tuple[str, ...], max_chars: int = 320) -> str:
    for field in fields:
        if item.get(field):
            text = _collapse_text(item.get(field), max_chars)
            if text:
                return text
    return ""


def _humanize_plan_item(value: Any) -> str:
    """Render model/plan metadata without leaking raw dict repr into the UI."""
    if value is None:
        return ""
    if isinstance(value, dict):
        question = _first_text(value, ("question",), 360)
        description = _first_text(
            value,
            ("description", "text", "summary", "risk", "message", "reason"),
            360,
        )
        context = _first_text(value, ("context", "module", "module_name", "section"), 120)
        kind = _first_text(value, ("type", "category", "severity", "id", "name"), 120)
        source = _source_ref_label(value.get("source_ref"))

        if question:
            prefix = f"{context}: " if context else ""
            text = f"{prefix}{question}"
            if value.get("blocking"):
                text = f"Blocking question: {text}"
        elif kind and description:
            text = f"{kind}: {description}"
        elif context and description:
            text = f"{context}: {description}"
        else:
            # Last-resort projection: keep user-facing scalar fields only.
            ignored = {
                "source_ref",
                "artifact_id",
                "answered",
                "answer",
                "suggested_answer",
                "blocking",
                "line",
                "page",
                "excerpt",
            }
            parts = []
            for key, raw in value.items():
                if key in ignored or raw in (None, "", [], {}):
                    continue
                if isinstance(raw, (dict, list, tuple)):
                    continue
                parts.append(f"{key}: {_collapse_text(raw, 160)}")
            text = "; ".join(parts)

        if source and text:
            text = f"{text} (source: {source})"
        return text.strip()
    if isinstance(value, (list, tuple)):
        return "; ".join(
            text for text in (_humanize_plan_item(item) for item in value) if text
        )
    return _collapse_text(value)


def _as_list(value: Any) -> List[str]:
    if not value:
        return []
    if isinstance(value, list):
        out: List[str] = []
        for item in value:
            if isinstance(item, (list, tuple)):
                out.extend(_as_list(list(item)))
                continue
            text = _humanize_plan_item(item)
            if text:
                out.append(text)
        return out
    text = _humanize_plan_item(value)
    return [text] if text else []


def _as_dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _dedupe(items: List[str], limit: int = 12) -> List[str]:
    result: List[str] = []
    seen: set[str] = set()
    for item in items:
        text = str(item or "").strip()
        if not text:
            continue
        key = text.lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(text)
        if len(result) >= limit:
            break
    return result


def _markdown_scalar(value: Any, max_chars: int = 220) -> str:
    text = _humanize_plan_item(value)
    if len(text) > max_chars:
        return text[: max_chars - 3].rstrip() + "..."
    return text


_GENERIC_REQUIREMENT_HEADINGS = {
    "technical requirement",
    "technical requirements",
    "project management need",
    "project management needs",
    "functional requirement",
    "functional requirements",
    "verification requirement",
    "verification requirements",
    "design requirement",
    "design requirements",
    "implementation requirement",
    "implementation requirements",
    "requirements",
    "overview",
    "introduction",
    "conclusion",
    "references",
    "appendix",
    "table of contents",
}


def _is_noise_requirement_text(text: Any) -> bool:
    cleaned = re.sub(r"\s+", " ", str(text or "")).strip()
    if not cleaned:
        return True
    lowered = cleaned.lower().strip(" .:-")
    if lowered in _GENERIC_REQUIREMENT_HEADINGS:
        return True
    if re.search(r"\.{3,}\s*\d+\s*$", cleaned):
        return True
    heading_page = re.fullmatch(r"[A-Za-z][A-Za-z0-9 /\-&]{2,80}\s+\d{1,3}", cleaned)
    if heading_page:
        return lowered.rsplit(" ", 1)[0] in _GENERIC_REQUIREMENT_HEADINGS
    if re.fullmatch(r"(?:chapter|section|table|figure|page)\s+\d+.*", lowered):
        return True
    if len(cleaned.split()) <= 4 and not re.search(
        r"\b(shall|must|should|will|verify|support|capture|drive|drives|assert|deassert|clear|clears|read|reads|write|writes|return|returns|translate|translates|latch|latches|generate|generates|hold|holds|stall|stalls)\b",
        lowered,
    ):
        return True
    return False


def _top_module_from_plan(plan: Dict[str, Any]) -> Optional[str]:
    for key in ("module_name", "top_module"):
        if plan.get(key):
            return str(plan[key])
    uvm = _as_dict(plan.get("uvm"))
    if uvm.get("top_module"):
        return str(uvm["top_module"])
    unitsim = _as_dict(plan.get("unitsim"))
    if unitsim.get("module_name"):
        return str(unitsim["module_name"])
    formal = _as_dict(plan.get("formal"))
    if formal.get("module_name"):
        return str(formal["module_name"])
    return None


def _requirements_snippet(model_content: Dict[str, Any], limit: int = 5) -> List[str]:
    reqs = model_content.get("requirements") or []
    if not isinstance(reqs, list):
        return []
    out: List[str] = []
    for req in reqs:
        if isinstance(req, dict):
            req_id = str(req.get("id") or "").strip()
            text = _markdown_scalar(req.get("text") or req.get("description"), 180)
            if text and not _is_noise_requirement_text(text):
                out.append(f"{req_id}: {text}" if req_id else text)
        elif req:
            text = _markdown_scalar(req, 180)
            if text and not _is_noise_requirement_text(text):
                out.append(text)
        if len(out) >= limit:
            break
    return out


def _name_and_description(item: Any, name_fields: tuple[str, ...]) -> str:
    if not isinstance(item, dict):
        return _markdown_scalar(item)
    name = ""
    for field in name_fields:
        if item.get(field):
            name = str(item[field]).strip()
            break
    desc = _markdown_scalar(item.get("description") or item.get("text"), 180)
    reqs = _as_list(item.get("requirement_ids"))
    suffix = f" [{', '.join(reqs[:3])}]" if reqs else ""
    if name and desc:
        return f"{name}: {desc}{suffix}"
    return f"{name or desc}{suffix}".strip()


def _uvm_test_cases(uvm: Dict[str, Any], limit: int = 8) -> List[str]:
    cases = [
        _name_and_description(item, ("name", "id"))
        for item in (uvm.get("sequences") or [])
    ]
    if not cases:
        cases = DEFAULT_DV_OBJECTIVES
    return _dedupe(cases, limit)


def _uvm_checking_strategy(uvm: Dict[str, Any], limit: int = 8) -> List[str]:
    checks = [
        _name_and_description(item, ("check", "name", "id"))
        for item in (uvm.get("scoreboard_checks") or [])
    ]
    if not checks:
        checks = [
            "Build a monitor-to-scoreboard path for observable outputs.",
            "Compare DUT behavior against expected behaviors and transaction flows from TruthCore.",
            "Escalate unobservable or ambiguous behavior into open questions instead of guessing.",
        ]
    return _dedupe(checks, limit)


def _uvm_coverage_strategy(uvm: Dict[str, Any], limit: int = 8) -> List[str]:
    coverage = [
        _name_and_description(item, ("point", "name", "id"))
        for item in (uvm.get("coverage_points") or [])
    ]
    if not coverage:
        coverage = [
            "Functional bins for reset, nominal transactions, boundary values, protocol states, and error/stress cases.",
            "Cross coverage between control intent and data/status response when the model exposes those signals.",
        ]
    return _dedupe(coverage, limit)


def _formal_plan_lines(formal: Dict[str, Any], limit: int = 6) -> List[str]:
    props = formal.get("properties") or formal.get("assertions") or []
    lines = [_name_and_description(item, ("name", "id", "property")) for item in props]
    if not lines and formal:
        lines.append("Generate safety/liveness assertions and cover properties from TruthCore facts.")
    return _dedupe(lines, limit)


def _unitsim_plan_lines(unitsim: Dict[str, Any], limit: int = 6) -> List[str]:
    scenarios = unitsim.get("scenarios") or []
    lines = [_name_and_description(item, ("name", "id")) for item in scenarios]
    if not lines and unitsim:
        lines.append("Run directed unit scenarios for reset, nominal, boundary, and error behavior.")
    return _dedupe(lines, limit)


def _summary_section(title: str, items: List[str]) -> List[str]:
    if not items:
        return []
    lines = ["", f"### {title}"]
    lines.extend(f"- {item}" for item in items)
    return lines


def _build_senior_dv_summary(
    plan: Dict[str, Any],
    *,
    verification_type: str,
    model_content: Dict[str, Any],
    spec_filename: Optional[str],
    rtl_filename: Optional[str],
    clarifier_answers: Optional[List[str]],
    refined_feedback: str = "",
) -> Dict[str, Any]:
    design = _as_dict(model_content.get("design"))
    top = _top_module_from_plan(plan) or design.get("top_module") or model_content.get("top_module")
    vlabel = STRATEGY_LABELS.get(verification_type, verification_type or "verification")
    sources = [n for n in (spec_filename, rtl_filename) if n]
    ports = design.get("ports") if isinstance(design.get("ports"), list) else []
    protocols = design.get("protocols") if isinstance(design.get("protocols"), list) else []
    fsms = design.get("fsms") if isinstance(design.get("fsms"), list) else []
    registers = design.get("register_fields") if isinstance(design.get("register_fields"), list) else []

    intro_bits: List[str] = []
    if top:
        intro_bits.append(f"I will verify the **{top}** design using **{vlabel}**.")
    else:
        intro_bits.append(f"I will verify the active RTL using **{vlabel}**.")
    if sources:
        intro_bits.append(f"Sources: {', '.join(sources)}.")
    intro_bits.append(
        "The plan is the execution baseline: generated agents, tests, scoreboards, "
        "coverage, and later log-repair decisions must trace back to this plan."
    )

    description = _markdown_scalar(
        design.get("description") or model_content.get("description") or "",
        500,
    )
    design_facts = []
    if ports:
        design_facts.append(f"{len(ports)} parsed ports")
    if protocols:
        names = [
            str(p.get("protocol") or p.get("name") or "protocol").strip()
            for p in protocols[:4]
            if isinstance(p, dict)
        ]
        design_facts.append(f"{len(protocols)} protocol view(s): {', '.join(names) if names else 'parsed'}")
    if fsms:
        design_facts.append(f"{len(fsms)} FSM/state-machine view(s)")
    if registers:
        design_facts.append(f"{len(registers)} register field(s)")

    requirements = _requirements_snippet(model_content)
    uvm = _as_dict(plan.get("uvm"))
    formal = _as_dict(plan.get("formal"))
    unitsim = _as_dict(plan.get("unitsim"))

    architecture = []
    if uvm:
        agents = uvm.get("agents") if isinstance(uvm.get("agents"), list) else []
        if agents:
            architecture.append(
                "UVM architecture: "
                + "; ".join(
                    _name_and_description(agent, ("name", "role", "type")) for agent in agents[:5]
                )
            )
        files = uvm.get("files_preview") if isinstance(uvm.get("files_preview"), list) else []
        if files:
            architecture.append(f"Generated collateral preview: {', '.join(str(f) for f in files[:10])}.")

    planned_cases = []
    if uvm:
        planned_cases.extend(_uvm_test_cases(uvm))
    planned_cases.extend(_unitsim_plan_lines(unitsim))

    checking = []
    if uvm:
        checking.extend(_uvm_checking_strategy(uvm))
    checking.extend(_formal_plan_lines(formal))

    coverage = _uvm_coverage_strategy(uvm) if uvm else []
    open_questions = _as_list(model_content.get("open_questions"))
    risks = _as_list(model_content.get("risks"))

    lines: List[str] = []
    lines.append(" ".join(intro_bits))
    if refined_feedback:
        lines.append(f"Refinement applied: {_markdown_scalar(refined_feedback, 220)}.")
    if description:
        lines.extend(["", "### Design understanding", f"- {description}"])
    if design_facts:
        lines.extend(_summary_section("Grounded facts", design_facts))
    if requirements:
        lines.extend(_summary_section("Requirements in scope", requirements))
    if clarifier_answers:
        lines.extend(_summary_section("User clarifications", clarifier_answers[:6]))
    lines.extend(_summary_section("Test objectives", DEFAULT_DV_OBJECTIVES))
    lines.extend(_summary_section("Planned test cases", planned_cases))
    lines.extend(_summary_section("Checking strategy", checking))
    lines.extend(_summary_section("Coverage strategy", coverage))
    lines.extend(_summary_section("Architecture and deliverables", architecture))
    if open_questions:
        lines.extend(_summary_section("Open questions to avoid guessing", open_questions[:6]))
    if risks:
        lines.extend(_summary_section("Known risks", risks[:6]))
    lines.extend(_summary_section("Closure gates", DEFAULT_CLOSURE_GATES))

    return {
        "summary": "\n".join(lines).strip(),
        "test_objectives": DEFAULT_DV_OBJECTIVES,
        "planned_test_cases": planned_cases,
        "checking_strategy": checking,
        "coverage_strategy": coverage,
        "architecture": architecture,
        "closure_criteria": DEFAULT_CLOSURE_GATES,
    }


def build_verification_plan_presentation(
    plan: Dict[str, Any],
    *,
    verification_type: str,
    model_content: Optional[Dict[str, Any]] = None,
    spec_filename: Optional[str] = None,
    rtl_filename: Optional[str] = None,
    readiness: Optional[Dict[str, Any]] = None,
    clarifier_answers: Optional[List[str]] = None,
    refined_feedback: str = "",
) -> Dict[str, Any]:
    """Attach presentation fields to a verification plan dict."""
    model_content = model_content if isinstance(model_content, dict) else {}
    design = _as_dict(model_content.get("design"))
    top = _top_module_from_plan(plan) or design.get("top_module") or model_content.get("top_module")

    details = _build_senior_dv_summary(
        plan,
        verification_type=verification_type,
        model_content=model_content,
        spec_filename=spec_filename,
        rtl_filename=rtl_filename,
        clarifier_answers=clarifier_answers,
        refined_feedback=refined_feedback,
    )

    assumptions: List[str] = []
    if top:
        assumptions.append(f"Top module under test: {top}")
    if spec_filename:
        assumptions.append(f"Spec artifact: {spec_filename}")
    if rtl_filename:
        assumptions.append(f"RTL artifact: {rtl_filename}")

    modules = design.get("modules") or model_content.get("modules") or []
    if isinstance(modules, list) and modules:
        names = [
            m if isinstance(m, str) else str(_as_dict(m).get("name") or "")
            for m in modules[:8]
        ]
        names = [n for n in names if n]
        if names:
            assumptions.append(f"Indexed modules: {', '.join(names)}")

    existing_presentation = _as_dict(plan.get("presentation"))
    constraints = _as_list(existing_presentation.get("constraints"))
    constraints.extend([
        "Every generated test, checker, assertion, and coverage item should map to TruthCore facts, requirements, or explicit open questions.",
        "Do not invent protocol behavior; ambiguous behavior remains an open item until the user/spec resolves it.",
        "Execution must preserve this approved plan as the baseline for generated files and log-repair iterations.",
    ])

    risks: List[str] = []
    if isinstance(readiness, dict):
        for w in readiness.get("warnings") or []:
            text = str(w).strip()
            if text:
                risks.append(text)
        for b in readiness.get("blockers") or []:
            text = str(b).strip()
            if text:
                risks.append(f"Blocker: {text}")

    presentation = {
        "summary": details["summary"],
        "assumptions": _dedupe(assumptions, 10),
        "constraints": _dedupe(constraints, 10),
        "risks": _dedupe(risks, 10),
        "readiness_warnings": list(readiness.get("warnings") or []) if isinstance(readiness, dict) else [],
        "test_objectives": details["test_objectives"],
        "planned_test_cases": details["planned_test_cases"],
        "checking_strategy": details["checking_strategy"],
        "coverage_strategy": details["coverage_strategy"],
        "architecture": details["architecture"],
        "closure_criteria": details["closure_criteria"],
    }
    if refined_feedback:
        presentation["refined_feedback"] = refined_feedback
    return presentation


def merge_presentation_into_plan(
    plan: Dict[str, Any],
    presentation: Dict[str, Any],
) -> Dict[str, Any]:
    """Return plan with presentation merged."""
    existing = plan.get("presentation") if isinstance(plan.get("presentation"), dict) else {}
    merged = {**existing, **presentation}
    out = dict(plan)
    out["presentation"] = merged
    return out


def refine_presentation_summary(
    plan: Dict[str, Any],
    feedback: str,
    *,
    verification_type: str = "",
    model_content: Optional[Dict[str, Any]] = None,
    spec_filename: Optional[str] = None,
    rtl_filename: Optional[str] = None,
    readiness: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Rebuild the human-readable plan summary after refinement.

    Kept as a compatibility helper for older call sites. New call sites should
    pass model context so the plan is regenerated instead of appending a note.
    """
    if model_content is not None:
        presentation = build_verification_plan_presentation(
            plan,
            verification_type=verification_type or str(plan.get("verification_type") or "verification"),
            model_content=model_content,
            spec_filename=spec_filename,
            rtl_filename=rtl_filename,
            readiness=readiness,
            refined_feedback=feedback,
        )
        return merge_presentation_into_plan(plan, presentation)

    presentation = plan.get("presentation") if isinstance(plan.get("presentation"), dict) else {}
    note = _markdown_scalar(feedback, 220)
    if note:
        presentation["refined_feedback"] = note
        presentation["summary"] = (
            str(presentation.get("summary") or "Plan refined.").strip()
            + f"\n\n**Refinement applied**\n- {note}"
        )
    return merge_presentation_into_plan(plan, presentation)
