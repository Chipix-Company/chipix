"""
Formal verification generator.

This module turns the persisted mental model into formal collateral:
- a reviewable formal plan;
- SVA assertion/bind files;
- SymbiYosys and Jasper-style launch scripts.

The LLM is allowed to propose formal intent, but executable collateral is
validated against the mental model so generated properties cannot invent ports
or requirement IDs.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import asdict, dataclass, field, is_dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class FormalOutput:
    """Generated formal verification files and metadata."""

    assertion_file: str = ""
    bind_file: str = ""
    sby_file: str = ""
    jasper_tcl_file: str = ""
    assertion_content: str = ""
    bind_content: str = ""
    sby_content: str = ""
    jasper_tcl_content: str = ""
    property_count: int = 0
    assumption_count: int = 0
    cover_count: int = 0
    all_files: List[str] = field(default_factory=list)
    rtl_files: List[str] = field(default_factory=list)
    formal_plan: Dict[str, Any] = field(default_factory=dict)
    generation_mode: str = "rules"
    llm_called: bool = False
    llm_used: bool = False
    llm_enhanced_files: List[str] = field(default_factory=list)
    llm_errors: List[str] = field(default_factory=list)
    validation_errors: List[str] = field(default_factory=list)
    validation_warnings: List[str] = field(default_factory=list)
    summary: str = ""


def generate_formal_plan_from_model(model: Any) -> Dict[str, Any]:
    """Create a deterministic formal plan from the mental model."""

    context = _mental_model_formal_context(model)
    top = context["top_module"]
    ports = context["ports"]
    outputs = [p for p in ports if p.get("direction") == "output"]
    inputs = [p for p in ports if p.get("direction") == "input"]
    requirements = context["requirements"]
    allowed_requirements = {r.get("id", "") for r in requirements if r.get("id")}
    allowed_formal_signals = _context_formal_signal_names(context)

    assertions: List[Dict[str, Any]] = []
    assumptions: List[Dict[str, Any]] = []
    covers: List[Dict[str, Any]] = []
    open_questions: List[str] = []

    clk, rst, rst_active_low = _detect_clk_rst_from_context(context)
    if not clk:
        open_questions.append("No clock signal was confidently identified for formal properties.")
        clk = "clk"
    if not rst:
        open_questions.append("No reset signal was confidently identified for reset assertions.")
        rst = "rst_n"
        rst_active_low = True

    for port in outputs[:16]:
        name = port["name"]
        assertions.append(
            {
                "id": f"F-A-{len(assertions) + 1:03d}",
                "name": f"reset_{_safe_sv_name(name)}",
                "kind": "assert",
                "description": f"{name} should be known after reset is released.",
                "signals": [name],
                "requirement_ids": _best_requirement_ids(requirements, name),
                "template": "known_after_reset",
                "priority": "high",
            }
        )

    for proto in context["protocols"]:
        proto_name = str(proto.get("protocol") or proto.get("name") or "")
        port_group = [str(p) for p in proto.get("port_group", []) if str(p)]
        valid = _find_signal(port_group, ["valid", "vld"])
        ready = _find_signal(port_group, ["ready", "rdy"])
        if valid and ready:
            assertions.append(
                {
                    "id": f"F-A-{len(assertions) + 1:03d}",
                    "name": f"{_safe_sv_name(valid)}_stable_until_{_safe_sv_name(ready)}",
                    "kind": "assert",
                    "description": f"{proto_name or 'Valid-ready'} valid must remain asserted until ready.",
                    "signals": [valid, ready],
                    "requirement_ids": _best_requirement_ids(requirements, proto_name or valid),
                    "template": "valid_stable_until_ready",
                    "priority": "high",
                }
            )

    for fsm in context["fsms"]:
        states = [str(s) for s in fsm.get("states", []) if str(s)]
        state_signal = str(fsm.get("state_signal") or "")
        fsm_name = _safe_sv_name(str(fsm.get("name") or state_signal or "fsm"))
        if state_signal and states:
            assertions.append(
                {
                    "id": f"F-A-{len(assertions) + 1:03d}",
                    "name": f"{fsm_name}_legal_state",
                    "kind": "assert",
                    "description": f"{fsm_name} never enters an illegal state.",
                    "signals": [state_signal],
                    "requirement_ids": _best_requirement_ids(requirements, fsm_name),
                    "template": "fsm_legal_state",
                    "states": states,
                    "encoding": str(fsm.get("encoding") or "binary"),
                    "priority": "high",
                }
            )
            for state in states[:16]:
                covers.append(
                    {
                        "id": f"F-C-{len(covers) + 1:03d}",
                        "name": f"{fsm_name}_reach_{_safe_sv_name(state)}",
                        "description": f"Reach FSM state {state}.",
                        "signals": [state_signal],
                        "template": "fsm_state_reachable",
                        "state": state,
                        "states": states,
                        "encoding": str(fsm.get("encoding") or "binary"),
                    }
                )

    for behavior in context["expected_behaviors"][:12]:
        signals = _filter_allowed_signals(
            _extract_behavior_signals(behavior),
            allowed_formal_signals,
        )
        if signals:
            assertions.append(
                {
                    "id": f"F-A-{len(assertions) + 1:03d}",
                    "name": _safe_sv_name(
                        str(behavior.get("name") or behavior.get("id") or f"expected_behavior_{len(assertions) + 1}")
                    ),
                    "kind": "assert",
                    "description": str(
                        behavior.get("description")
                        or behavior.get("expected_output")
                        or "Expected behavior from mental model"
                    ),
                    "signals": signals,
                    "requirement_ids": _filter_allowed_requirements(
                        behavior.get("requirement_ids") or behavior.get("requirements") or [],
                        allowed_requirements,
                    ),
                    "template": "known_output",
                    "priority": "medium",
                }
            )

    for flow in context["transaction_flows"][:8]:
        signals = _filter_allowed_signals(
            _extract_flow_signals(flow),
            allowed_formal_signals,
        )
        if signals:
            covers.append(
                {
                    "id": f"F-C-{len(covers) + 1:03d}",
                    "name": _safe_sv_name(str(flow.get("name") or flow.get("id") or f"flow_{len(covers) + 1}")),
                    "description": str(flow.get("description") or "Transaction flow is reachable."),
                    "signals": signals,
                    "template": "signals_seen",
                }
            )

    kb_assertions, kb_covers = _formal_items_from_knowledge_base(
        context,
        allowed_formal_signals,
        allowed_requirements,
    )
    _append_unique_formal_items(assertions, kb_assertions)
    _append_unique_formal_items(covers, kb_covers)

    for item in context["verification_intent"].get("formal_properties", []) or []:
        signals = _filter_allowed_signals(
            item.get("related_signals") or item.get("signals") or [],
            allowed_formal_signals,
        )
        assertions.append(
            {
                "id": str(item.get("id") or f"F-A-{len(assertions) + 1:03d}"),
                "name": _safe_sv_name(str(item.get("name") or f"formal_property_{len(assertions) + 1}")),
                "kind": str(item.get("property_type") or "assert"),
                "description": str(item.get("description") or ""),
                "signals": signals,
                "requirement_ids": _filter_allowed_requirements(
                    item.get("requirement_ids") or [],
                    allowed_requirements,
                ),
                "template": "llm_sketch" if item.get("sva_sketch") else "known_output",
                "sva": str(item.get("sva_sketch") or ""),
                "priority": "medium",
            }
        )

    if inputs:
        assumptions.append(
            {
                "id": "F-AS-001",
                "name": "inputs_known_after_reset",
                "kind": "assume",
                "description": "Environment drives known values on primary inputs after reset.",
                "signals": [p["name"] for p in inputs[:12] if p["name"] not in {clk, rst}],
                "template": "inputs_known_after_reset",
            }
        )

    depth = 30
    if context["estimated_complexity"] in {"large", "very_large"}:
        depth = 80
    elif len(context["fsms"]) or len(context["protocols"]):
        depth = 50

    traceability = _formal_traceability(assertions, covers, allowed_requirements)
    files_preview = [
        f"{top}_sva.sv",
        f"{top}_bind.sv",
        f"{top}_formal.sby",
        f"{top}_jasper.tcl",
    ]

    return {
        "top_module": top,
        "clock": clk,
        "reset": rst,
        "reset_polarity": "active_low" if rst_active_low else "active_high",
        "modes": ["prove", "cover"],
        "engine": "smtbmc z3",
        "depth": {"prove": depth, "cover": max(depth, 60)},
        "bind_strategy": "external_bind",
        "assumptions": assumptions,
        "assertions": assertions,
        "properties": assertions,
        "property_count": len(assertions),
        "assumption_count": len(assumptions),
        "covers": covers,
        "cover_count": len(covers),
        "open_questions": open_questions,
        "files_preview": files_preview,
        "traceability": traceability,
        "llm_used": False,
        "grounding": "mental_model_rules",
    }


async def generate_formal_plan_with_ai(
    model: Any,
    *,
    content: Optional[Dict[str, Any]] = None,
    ai_client: Any = None,
    fallback_plan: Optional[Dict[str, Any]] = None,
    require_llm: bool = False,
) -> Dict[str, Any]:
    """Generate a formal plan with an LLM, then sanitize it against the mental model."""

    fallback = fallback_plan or generate_formal_plan_from_model(model)
    if not ai_client:
        if require_llm:
            raise RuntimeError("AI client is required for formal plan generation")
        return dict(fallback)

    context = _mental_model_formal_context(model, content=content)
    try:
        response = await _call_formal_plan_ai_client(
            ai_client=ai_client,
            context=context,
            fallback_plan=fallback,
        )
        plan_data = _extract_json_object(response)
        plan = _sanitize_formal_plan_from_ai(plan_data, context, fallback)
        plan["llm_used"] = True
        plan["llm_attempted"] = True
        plan["grounding"] = "mental_model_validated"
        return plan
    except Exception as exc:
        logger.warning("AI formal plan generation failed: %s", exc)
        if require_llm:
            raise RuntimeError(f"AI formal plan generation failed: {exc}") from exc
        plan = dict(fallback)
        plan["llm_used"] = False
        plan["llm_attempted"] = True
        plan["llm_error"] = str(exc)
        plan["grounding"] = "mental_model_rules"
        return plan


def generate_formal_from_model(
    model: Any,
    work_dir: str = "",
    *,
    approved_plan: Optional[Dict[str, Any]] = None,
    rtl_files: Optional[Iterable[str | Path]] = None,
) -> FormalOutput:
    """Generate SVA assertions, bind module, and formal tool scripts."""

    plan = _coerce_formal_plan(approved_plan) or generate_formal_plan_from_model(model)
    design = _get(model, "design", {})
    module_name = str(plan.get("top_module") or _get(design, "top_module", "dut") or "dut")
    context = _mental_model_formal_context(model)
    ports = _formal_ports_for_generation(_get(design, "ports", []) or [], context, plan)
    clk_name = str(plan.get("clock") or context.get("clock") or "clk")
    rst_name = str(plan.get("reset") or context.get("reset") or "rst_n")
    rst_active_low = str(plan.get("reset_polarity") or "active_low") == "active_low"
    resolved_rtl = [str(Path(path)) for path in (rtl_files or resolve_rtl_files_from_model(model)) if str(path)]

    output = FormalOutput(
        formal_plan=plan,
        rtl_files=resolved_rtl,
        generation_mode="rules",
    )

    assertion_content, prop_count, assumption_count, cover_count = _gen_assertion_module(
        module_name=module_name,
        ports=ports,
        plan=plan,
        clk_name=clk_name,
        rst_name=rst_name,
        rst_active_low=rst_active_low,
    )
    output.assertion_content = assertion_content
    output.property_count = prop_count
    output.assumption_count = assumption_count
    output.cover_count = cover_count
    output.bind_content = _gen_bind_module(module_name, ports)
    output.sby_content = _gen_sby_content(
        module_name=module_name,
        rtl_files=resolved_rtl,
        assertion_file=f"{module_name}_sva.sv",
        bind_file=f"{module_name}_bind.sv",
        plan=plan,
        multi_clock=len(context.get("clock_domains", [])) > 1,
    )
    output.jasper_tcl_content = _gen_jasper_tcl(
        module_name=module_name,
        rtl_files=resolved_rtl,
        assertion_file=f"{module_name}_sva.sv",
        bind_file=f"{module_name}_bind.sv",
    )

    if work_dir:
        work = Path(work_dir)
        work.mkdir(parents=True, exist_ok=True)
        output.assertion_file = _write(work, f"{module_name}_sva.sv", output.assertion_content)
        output.bind_file = _write(work, f"{module_name}_bind.sv", output.bind_content)
        output.sby_file = _write(work, f"{module_name}_formal.sby", output.sby_content)
        output.jasper_tcl_file = _write(work, f"{module_name}_jasper.tcl", output.jasper_tcl_content)
        plan_path = _write(work, "approved_formal_plan.json", json.dumps(plan, indent=2, default=str))
        output.all_files = [
            output.assertion_file,
            output.bind_file,
            output.sby_file,
            output.jasper_tcl_file,
            plan_path,
        ]

    output.summary = (
        f"Generated formal collateral for {module_name}: "
        f"{output.property_count} assert(s), {output.assumption_count} assume(s), "
        f"{output.cover_count} cover(s), {len(resolved_rtl)} RTL source file(s)."
    )
    validation = _validate_formal_artifacts(
        assertion_content=output.assertion_content,
        bind_content=output.bind_content,
        plan=plan,
        module_name=module_name,
        allowed_signals=_context_formal_signal_names(context),
    )
    output.validation_errors = validation["errors"]
    output.validation_warnings = validation["warnings"]
    logger.info(output.summary)
    return output


async def generate_formal_from_model_async(
    model: Any,
    work_dir: str = "",
    *,
    approved_plan: Optional[Dict[str, Any]] = None,
    rtl_files: Optional[Iterable[str | Path]] = None,
    ai_client: Any = None,
    require_llm: bool = False,
) -> FormalOutput:
    """Generate formal collateral and optionally let the LLM improve the SVA."""

    plan = _coerce_formal_plan(approved_plan)
    llm_plan_used = False
    if plan is None:
        plan = await generate_formal_plan_with_ai(
            model,
            ai_client=ai_client,
            require_llm=require_llm,
        )
        llm_plan_used = bool(plan.get("llm_used"))

    output = generate_formal_from_model(
        model,
        work_dir=work_dir,
        approved_plan=plan,
        rtl_files=rtl_files,
    )
    output.formal_plan = plan
    output.llm_called = bool(ai_client)
    output.llm_used = llm_plan_used
    output.generation_mode = "llm_plan" if llm_plan_used else "rules"

    if ai_client and output.assertion_file:
        try:
            context = _mental_model_formal_context(model)
            original = Path(output.assertion_file).read_text(encoding="utf-8", errors="ignore")
            enhanced = await _enhance_formal_sva_with_llm(
                ai_client=ai_client,
                filename=Path(output.assertion_file).name,
                scaffold=original,
                context=context,
                plan=plan,
            )
            enhanced = _sanitize_sv_text(enhanced)
            accepted, reason = _accept_formal_sva_candidate(
                original=original,
                candidate=enhanced,
                module_name=f"{plan.get('top_module') or context.get('top_module')}_sva",
                allowed_ports={p["name"] for p in context["ports"] if p.get("name")},
            )
            if accepted:
                Path(output.assertion_file).write_text(enhanced, encoding="utf-8")
                output.assertion_content = enhanced
                output.llm_enhanced_files.append(output.assertion_file)
                output.llm_used = True
                output.generation_mode = "llm_hybrid"
            else:
                output.llm_errors.append(f"SVA enhancement rejected: {reason}")
        except Exception as exc:
            logger.warning("AI formal SVA enhancement failed: %s", exc)
            output.llm_errors.append(str(exc))

    if require_llm and not output.llm_used:
        raise RuntimeError("LLM formal generation was requested but no LLM output was accepted")

    return output


def resolve_rtl_files_from_model(model: Any) -> List[str]:
    """Resolve source RTL paths from model.project_scan."""

    scan = _get(model, "project_scan", None)
    root_value = _get(scan, "root_path", "") if scan else ""
    entries = _get(scan, "rtl_files", []) if scan else []
    if not root_value or not entries:
        return []
    root = Path(str(root_value))
    resolved = []
    for entry in entries:
        path = Path(str(entry))
        if not path.is_absolute():
            path = root / path
        if path.exists() and path.is_file():
            resolved.append(str(path))
    return resolved


def _gen_assertion_module(
    *,
    module_name: str,
    ports: Iterable[Any],
    plan: Dict[str, Any],
    clk_name: str,
    rst_name: str,
    rst_active_low: bool,
) -> tuple[str, int, int, int]:
    lines: List[str] = []
    lines.append(f"// Formal assertions for: {module_name}")
    lines.append(f"// Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append("// Tool: ChipVerify AI Formal Agent")
    lines.append("`timescale 1ns/1ps")
    lines.append("")
    lines.append(f"module {module_name}_sva (")
    port_lines = []
    for port in ports:
        name = _port_name(port)
        if not name:
            continue
        bus = _port_range(port)
        br = f" {bus}" if bus else ""
        direction = _port_dir(port)
        sv_dir = "input" if direction in {"input", "output", "inout"} else "input"
        port_lines.append(f"  {sv_dir} logic{br} {name}")
    lines.append(",\n".join(port_lines))
    lines.append(");")
    lines.append("")
    lines.append("  // Default formal clock/reset")
    lines.append("  default clocking cb @(posedge " + clk_name + "); endclocking")
    rst_condition = f"!{rst_name}" if rst_active_low else rst_name
    lines.append("")

    assumption_count = 0
    property_count = 0
    cover_count = 0

    assumptions = plan.get("assumptions", []) if isinstance(plan, dict) else []
    if assumptions:
        lines.append("  // Assumptions")
    for item in assumptions:
        emitted = _emit_formal_item(
            lines,
            item,
            rst_condition,
            clk_name=clk_name,
            item_kind="assume",
        )
        assumption_count += int(emitted)

    assertions = plan.get("assertions", []) if isinstance(plan, dict) else []
    if assertions:
        lines.append("  // Assertions")
    for item in assertions:
        emitted = _emit_formal_item(
            lines,
            item,
            rst_condition,
            clk_name=clk_name,
            item_kind="assert",
        )
        property_count += int(emitted)

    covers = plan.get("covers", []) if isinstance(plan, dict) else []
    if covers:
        lines.append("  // Cover properties")
    for item in covers:
        emitted = _emit_formal_item(
            lines,
            item,
            rst_condition,
            clk_name=clk_name,
            item_kind="cover",
        )
        cover_count += int(emitted)

    lines.append("endmodule")
    return "\n".join(lines) + "\n", property_count, assumption_count, cover_count


def _emit_formal_item(
    lines: List[str],
    item: Dict[str, Any],
    rst_condition: str,
    *,
    clk_name: str,
    item_kind: str,
) -> bool:
    if not isinstance(item, dict):
        return False
    name = _safe_sv_name(str(item.get("name") or item.get("id") or f"{item_kind}_property"))
    signals = [str(sig) for sig in item.get("signals", []) if str(sig)]
    description = str(item.get("description") or name)
    template = str(item.get("template") or "")
    sva = str(item.get("sva") or "").strip()

    lines.append(f"  // {description[:180]}")
    reqs = [str(r) for r in item.get("requirement_ids", []) if str(r)]
    if reqs:
        lines.append(f"  // Requirements: {', '.join(reqs)}")

    if sva and _looks_like_sva_expression(sva):
        body = sva.rstrip(";")
        if body.startswith("property ") or body.startswith("assert property") or body.startswith("assume property"):
            lines.append("  " + body.replace("\n", "\n  "))
            if not body.endswith(";"):
                lines.append("  ;")
            lines.append("")
            return True

    expr = _formal_expression_from_template(template, signals, item)
    if not expr:
        lines.append(f"  // Could not emit executable {item_kind} for {name}: no grounded signal expression.")
        lines.append("")
        return False

    clock_event = f"@(posedge {clk_name})"
    if item_kind == "cover":
        lines.append(f"  c_{name}: cover property ({clock_event} disable iff ({rst_condition}) {expr});")
    elif item_kind == "assume":
        lines.append(f"  a_{name}: assume property ({clock_event} disable iff ({rst_condition}) {expr});")
    else:
        lines.append(f"  a_{name}: assert property ({clock_event} disable iff ({rst_condition}) {expr})")
        lines.append(f'    else $error("FORMAL FAIL: {name}");')
    lines.append("")
    return True


def _formal_expression_from_template(template: str, signals: List[str], item: Dict[str, Any]) -> str:
    if template.startswith("FP-"):
        return _formal_expression_from_kb_rule(template, signals, item)
    if template == "valid_stable_until_ready" and len(signals) >= 2:
        return f"({signals[0]} && !{signals[1]}) |=> {signals[0]}"
    if template == "fsm_legal_state" and signals:
        states = [str(s) for s in item.get("states", []) if str(s)]
        encoding = str(item.get("encoding") or "binary")
        if states:
            checks = " || ".join(
                f"({signals[0]} == {_state_val(state, idx, encoding)})"
                for idx, state in enumerate(states)
            )
            return f"({checks})"
    if template == "fsm_state_reachable" and signals:
        states = [str(s) for s in item.get("states", []) if str(s)]
        state = str(item.get("state") or "")
        encoding = str(item.get("encoding") or "binary")
        if state and state in states:
            return f"{signals[0]} == {_state_val(state, states.index(state), encoding)}"
    if template == "inputs_known_after_reset" and signals:
        return f"!$isunknown({_sv_concat(signals[:12])})"
    if template == "signals_seen" and signals:
        return " && ".join(f"!$isunknown({sig})" for sig in signals[:4])
    if template in {"known_after_reset", "known_output", "llm_sketch"} and signals:
        return f"!$isunknown({_sv_concat(signals[:8])})"
    if signals:
        return f"!$isunknown({_sv_concat(signals[:8])})"
    return ""


def _formal_expression_from_kb_rule(template: str, signals: List[str], item: Dict[str, Any]) -> str:
    signal_map = item.get("signal_map") if isinstance(item.get("signal_map"), dict) else {}
    get = lambda key: str(signal_map.get(key) or "")

    if template == "FP-FIFO-001":
        wr_ptr, rd_ptr, depth = get("wr_ptr"), get("rd_ptr"), item.get("depth")
        if wr_ptr and rd_ptr and depth:
            return f"({wr_ptr} < {depth}) && ({rd_ptr} < {depth})"
    if template == "FP-FIFO-002":
        full, count, depth = get("full"), get("count"), item.get("depth")
        if full and count and depth:
            return f"{full} == ({count} == {depth})"
    if template == "FP-FIFO-003":
        empty, count = get("empty"), get("count")
        if empty and count:
            return f"{empty} == ({count} == 0)"
    if template == "FP-FIFO-004":
        count, push, pop = get("count"), get("push"), get("pop")
        if count and push and pop:
            return f"(({push} && !{pop}) |=> ({count} == $past({count}) + 1))"
    if template == "FP-FIFO-005":
        full, push, pop = get("full"), get("push"), get("pop")
        if full and push and pop:
            return f"({full} && !{pop}) |-> !{push}"
    if template == "FP-FIFO-006":
        empty, push, pop = get("empty"), get("push"), get("pop")
        if empty and push and pop:
            return f"({empty} && !{push}) |-> !{pop}"

    if template == "FP-HS-001":
        req, ack = get("req"), get("ack")
        if req and ack:
            return f"{req} |-> ##[1:{int(item.get('max_latency') or 16)}] {ack}"
    if template == "FP-HS-002":
        req, ack = get("req"), get("ack")
        if req and ack:
            return f"{ack} |-> ({req} || $past({req}))"
    if template == "FP-HS-003":
        valid, ready = get("valid"), get("ready")
        if valid and ready:
            return f"({valid} && !{ready}) |=> {valid}"
    if template == "FP-HS-004":
        valid, ready, data = get("valid"), get("ready"), get("data")
        if valid and ready and data:
            return f"({valid} && !{ready}) |=> $stable({data})"

    if template == "FP-TLUL-001":
        a_valid, a_ready = get("a_valid"), get("a_ready")
        if a_valid and a_ready:
            return f"({a_valid} && !{a_ready}) |=> {a_valid}"
    if template == "FP-TLUL-002":
        a_valid, a_ready = get("a_valid"), get("a_ready")
        payload = _sv_concat([
            value
            for key, value in sorted(signal_map.items())
            if key.startswith("a_payload_")
        ])
        if a_valid and a_ready and payload != "1'b0":
            return f"({a_valid} && !{a_ready}) |=> $stable({payload})"
    if template == "FP-TLUL-003":
        d_valid, d_ready = get("d_valid"), get("d_ready")
        if d_valid and d_ready:
            return f"({d_valid} && !{d_ready}) |=> {d_valid}"
    if template == "FP-TLUL-004":
        d_valid, d_ready = get("d_valid"), get("d_ready")
        payload = _sv_concat([
            value
            for key, value in sorted(signal_map.items())
            if key.startswith("d_payload_")
        ])
        if d_valid and d_ready and payload != "1'b0":
            return f"({d_valid} && !{d_ready}) |=> $stable({payload})"
    if template == "FP-TLUL-005":
        a_valid, a_ready = get("a_valid"), get("a_ready")
        d_valid, d_ready = get("d_valid"), get("d_ready")
        if a_valid and a_ready and d_valid and d_ready:
            return f"({a_valid} && {a_ready}) ##[1:{int(item.get('max_latency') or 32)}] ({d_valid} && {d_ready})"

    if template == "FP-ENC-001":
        signal = get("signal")
        if signal:
            return f"$onehot({signal})"
    if template in {"FP-ENC-002", "FP-ARB-001"}:
        signal = get("signal") or get("grant")
        if signal:
            return f"$onehot0({signal})"
    if template == "FP-ENC-003":
        gray = get("gray_val") or get("signal")
        if gray:
            return f"$onehot({gray} ^ $past({gray}))"
    if template == "FP-ARB-002":
        grant, request = get("grant"), get("request")
        if grant and request:
            return f"(|{grant}) |-> |({grant} & {request})"

    if template in {"FP-RST-001", "FP-RST-003"}:
        output = get("output") or (signals[0] if signals else "")
        if output:
            return f"!$isunknown({output})"
    if template == "FP-FSM-004":
        output = get("output") or (signals[0] if signals else "")
        if output:
            return f"!$isunknown({output})"

    if signals:
        return f"!$isunknown({_sv_concat(signals[:8])})"
    return ""


def _formal_items_from_knowledge_base(
    context: Dict[str, Any],
    allowed_signals: set[str],
    allowed_reqs: set[str],
) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    knowledge_base = context.get("knowledge_base", {})
    if not isinstance(knowledge_base, dict):
        return [], []

    raw_rules = knowledge_base.get("formal_properties", [])
    if not isinstance(raw_rules, list):
        raw_rules = []

    assertions: List[Dict[str, Any]] = []
    covers: List[Dict[str, Any]] = []
    for raw in raw_rules[:24]:
        if not isinstance(raw, dict):
            continue
        rule_id = str(raw.get("id") or "").strip()
        if not rule_id:
            continue
        signal_map = _match_formal_rule_signal_map(raw, allowed_signals)
        signals = _unique_preserve_order(signal_map.values())
        if not signal_map or not signals:
            continue

        item = {
            "id": rule_id,
            "name": _safe_sv_name(f"{rule_id}_{raw.get('title') or 'property'}"),
            "kind": str(raw.get("property_type") or "assert"),
            "description": str(raw.get("description") or raw.get("title") or rule_id),
            "signals": signals,
            "signal_map": signal_map,
            "requirement_ids": _filter_allowed_requirements(
                _best_requirement_ids(
                    context.get("requirements", []),
                    f"{raw.get('title', '')} {raw.get('description', '')}",
                ),
                allowed_reqs,
            ),
            "template": rule_id,
            "priority": str(raw.get("severity") or "medium").lower(),
            "source": "knowledge_base.formal_property",
            "library_source": str(raw.get("library_source") or ""),
        }
        depth = _infer_fifo_depth(context)
        if depth:
            item["depth"] = depth
        if not _formal_expression_from_kb_rule(rule_id, signals, item):
            continue
        if item["kind"] == "cover":
            covers.append(item)
        else:
            assertions.append(item)
    return assertions, covers


def _match_formal_rule_signal_map(rule: Dict[str, Any], allowed_signals: set[str]) -> Dict[str, str]:
    rule_id = str(rule.get("id") or "")
    candidates = sorted(allowed_signals)
    result: Dict[str, str] = {}

    def find(key: str, include: Iterable[str], exclude: Iterable[str] = ()) -> None:
        signal = _find_signal_by_keywords(candidates, include, exclude)
        if signal:
            result[key] = signal

    if rule_id.startswith("FP-FIFO"):
        find("full", ["full"], ["almost"])
        find("empty", ["empty"], ["almost"])
        find("count", ["fifo_cnt", "fifo_count", "count", "cnt", "level", "occup"])
        find("push", ["wr_en", "write_en", "wen", "push", "wr", "write"], ["ptr", "addr", "data"])
        find("pop", ["rd_en", "read_en", "ren", "pop", "rd", "read"], ["ptr", "addr", "data"])
        find("wr_ptr", ["wr_ptr", "wptr", "write_ptr"])
        find("rd_ptr", ["rd_ptr", "rptr", "read_ptr"])
        return {k: v for k, v in result.items() if v}

    if rule_id.startswith("FP-HS"):
        find("valid", ["valid", "vld"])
        find("ready", ["ready", "rdy"])
        find("data", ["data", "payload", "wdata", "rdata"])
        find("req", ["req", "request", "valid", "vld"])
        find("ack", ["ack", "acknowledge", "ready", "rdy"])
        return {k: v for k, v in result.items() if v}

    if rule_id.startswith("FP-TLUL"):
        find("a_valid", ["a_valid", "avalid"])
        find("a_ready", ["a_ready", "aready"])
        find("d_valid", ["d_valid", "dvalid"])
        find("d_ready", ["d_ready", "dready"])
        a_payload_patterns = [
            ("a_opcode", ["a_opcode", "aopcode"]),
            ("a_param", ["a_param", "aparam"]),
            ("a_size", ["a_size", "asize"]),
            ("a_source", ["a_source", "asource"]),
            ("a_address", ["a_address", "aaddress"]),
            ("a_mask", ["a_mask", "amask"]),
            ("a_data", ["a_data", "adata"]),
            ("a_user", ["a_user", "auser"]),
        ]
        d_payload_patterns = [
            ("d_opcode", ["d_opcode", "dopcode"]),
            ("d_param", ["d_param", "dparam"]),
            ("d_size", ["d_size", "dsize"]),
            ("d_source", ["d_source", "dsource"]),
            ("d_sink", ["d_sink", "dsink"]),
            ("d_data", ["d_data", "ddata"]),
            ("d_error", ["d_error", "derror"]),
            ("d_user", ["d_user", "duser"]),
        ]
        for index, (_name, patterns) in enumerate(a_payload_patterns):
            signal = _find_signal_by_keywords(candidates, patterns)
            if signal:
                result[f"a_payload_{index:02d}"] = signal
        for index, (_name, patterns) in enumerate(d_payload_patterns):
            signal = _find_signal_by_keywords(candidates, patterns)
            if signal:
                result[f"d_payload_{index:02d}"] = signal
        return {k: v for k, v in result.items() if v}

    if rule_id.startswith("FP-ENC"):
        find("gray_val", ["gray"])
        find("signal", ["grant", "gnt", "select", "sel", "enable", "en", "gray"])
        return {k: v for k, v in result.items() if v}

    if rule_id.startswith("FP-ARB"):
        find("grant", ["grant", "gnt"])
        find("request", ["request", "req"])
        if "grant" in result:
            result["signal"] = result["grant"]
        return {k: v for k, v in result.items() if v}

    if rule_id.startswith("FP-FSM"):
        find("state", ["state", "fsm"])
        find("output", ["out", "valid", "ready", "done"])
        return {k: v for k, v in result.items() if v}

    if rule_id.startswith("FP-CNT"):
        find("counter", ["counter", "count", "cnt"])
        find("enable", ["enable", "en", "inc"])
        find("clear", ["clear", "clr", "reset", "rst"])
        return {k: v for k, v in result.items() if v}

    patterns = [str(item) for item in rule.get("trigger_patterns", []) if str(item)]
    signal = _find_signal_by_keywords(candidates, patterns)
    return {"signal": signal} if signal else {}


def _find_signal_by_keywords(
    candidates: Iterable[str],
    include: Iterable[str],
    exclude: Iterable[str] = (),
) -> str:
    include_l = [str(token).lower() for token in include if str(token)]
    exclude_l = [str(token).lower() for token in exclude if str(token)]
    if not include_l:
        return ""
    scored: List[tuple[int, str]] = []
    for signal in candidates:
        lower = signal.lower()
        if exclude_l and any(token in lower for token in exclude_l):
            continue
        score = 0
        for idx, token in enumerate(include_l):
            if lower == token:
                score += 100 - idx
            elif lower.endswith(token) or lower.startswith(token):
                score += 60 - idx
            elif token in lower:
                score += 20 - idx
        if score > 0:
            scored.append((score, signal))
    if not scored:
        return ""
    scored.sort(key=lambda item: (-item[0], len(item[1]), item[1]))
    return scored[0][1]


def _context_formal_signal_names(context: Dict[str, Any]) -> set[str]:
    names = {str(p.get("name")) for p in context.get("ports", []) if isinstance(p, dict) and p.get("name")}
    top = str(context.get("top_module") or "")
    symbol_table = context.get("symbol_table", {})
    if isinstance(symbol_table, dict):
        symbols = symbol_table.get(top, [])
        if isinstance(symbols, list):
            names.update(str(symbol) for symbol in symbols if _is_sv_identifier(str(symbol)))
    block_models = context.get("block_models", {})
    block = block_models.get(top) if isinstance(block_models, dict) else None
    if isinstance(block, dict):
        for key in ("internal_symbols", "ports"):
            values = block.get(key, [])
            if isinstance(values, list):
                for value in values:
                    if isinstance(value, dict):
                        value = value.get("name")
                    if _is_sv_identifier(str(value)):
                        names.add(str(value))
    return {name for name in names if _is_sv_identifier(name)}


def _formal_ports_for_generation(
    ports: Iterable[Any],
    context: Dict[str, Any],
    plan: Dict[str, Any],
) -> List[Any]:
    result = list(ports or [])
    existing = {_port_name(port) for port in result if _port_name(port)}
    allowed = _context_formal_signal_names(context)
    for signal in _plan_signal_names(plan):
        if signal not in existing and signal in allowed:
            result.append({"name": signal, "direction": "input", "width": 1, "bus_range": ""})
            existing.add(signal)
    return result


def _plan_signal_names(plan: Dict[str, Any]) -> List[str]:
    names: List[str] = []
    for key in ("assumptions", "assertions", "covers", "properties"):
        values = plan.get(key, []) if isinstance(plan, dict) else []
        if not isinstance(values, list):
            continue
        for item in values:
            if isinstance(item, dict):
                names.extend(str(sig) for sig in item.get("signals", []) if _is_sv_identifier(str(sig)))
                signal_map = item.get("signal_map")
                if isinstance(signal_map, dict):
                    names.extend(str(sig) for sig in signal_map.values() if _is_sv_identifier(str(sig)))
    return _unique_preserve_order(names)


def _append_unique_formal_items(target: List[Dict[str, Any]], additions: List[Dict[str, Any]]) -> None:
    seen = {
        str(item.get("id") or item.get("name") or "").lower()
        for item in target
        if isinstance(item, dict)
    }
    for item in additions:
        key = str(item.get("id") or item.get("name") or "").lower()
        if key and key not in seen:
            target.append(item)
            seen.add(key)


def _unique_preserve_order(values: Iterable[Any]) -> List[str]:
    result: List[str] = []
    for value in values:
        text = str(value or "").strip()
        if text and text not in result:
            result.append(text)
    return result


def _infer_fifo_depth(context: Dict[str, Any]) -> Optional[int]:
    for param in context.get("parameters", []) or []:
        if not isinstance(param, dict):
            continue
        name = str(param.get("name") or "").lower()
        if not any(token in name for token in ("depth", "entries", "size")):
            continue
        raw = param.get("value", param.get("default", ""))
        value = _parse_int_literal(raw)
        if value and value > 1:
            return value
    text = " ".join(str(req.get("text") or "") for req in context.get("requirements", []) if isinstance(req, dict))
    match = re.search(r"\b(\d+)\s*[- ]?(?:deep|depth|entry|entries)\b", text, re.IGNORECASE)
    if match:
        value = _parse_int_literal(match.group(1))
        if value and value > 1:
            return value
    return None


def _validate_formal_artifacts(
    *,
    assertion_content: str,
    bind_content: str,
    plan: Dict[str, Any],
    module_name: str,
    allowed_signals: set[str],
) -> Dict[str, List[str]]:
    errors: List[str] = []
    warnings: List[str] = []
    if "```" in assertion_content or "```" in bind_content:
        errors.append("Generated formal collateral contains markdown fences.")
    if f"module {module_name}_sva" not in assertion_content:
        errors.append(f"Assertion module {module_name}_sva is missing or renamed.")
    if f"bind {module_name} {module_name}_sva" not in bind_content:
        errors.append(f"Bind statement for {module_name}_sva is missing.")
    for signal in _plan_signal_names(plan):
        if signal not in allowed_signals:
            errors.append(f"Formal plan references ungrounded signal: {signal}")
    if "assert property" not in assertion_content and "assume property" not in assertion_content:
        warnings.append("Generated SVA has no executable assert/assume property.")
    if "cover property" not in assertion_content:
        warnings.append("Generated SVA has no cover properties; coverage closure will be weak.")
    return {"errors": errors, "warnings": warnings}


def _parse_int_literal(raw: Any) -> Optional[int]:
    text = str(raw or "").strip().replace("_", "")
    if not text:
        return None
    try:
        if re.match(r"^\d+'[hH][0-9a-fA-F]+$", text):
            return int(text.split("'", 1)[1][1:], 16)
        if re.match(r"^\d+'[dD]\d+$", text):
            return int(text.split("'", 1)[1][1:], 10)
        if re.match(r"^\d+'[bB][01]+$", text):
            return int(text.split("'", 1)[1][1:], 2)
        return int(text, 0)
    except Exception:
        return None


def _gen_bind_module(module_name: str, ports: Iterable[Any]) -> str:
    lines = [
        f"// Bind module: connect {module_name}_sva to {module_name}",
        f"bind {module_name} {module_name}_sva sva_inst (",
    ]
    port_names = [_port_name(p) for p in ports if _port_name(p)]
    for idx, name in enumerate(port_names):
        comma = "," if idx < len(port_names) - 1 else ""
        lines.append(f"  .{name}({name}){comma}")
    lines.append(");")
    return "\n".join(lines) + "\n"


def _gen_sby_content(
    *,
    module_name: str,
    rtl_files: List[str],
    assertion_file: str,
    bind_file: str,
    plan: Dict[str, Any],
    multi_clock: bool = False,
) -> str:
    prove_depth = int(_get(plan.get("depth", {}), "prove", 40) or 40)
    depth_cfg = plan.get("depth", {}) if isinstance(plan.get("depth", {}), dict) else {}
    cover_depth = int(_get(depth_cfg, "cover", max(60, prove_depth)) or max(60, prove_depth))
    read_commands = []
    for path in rtl_files:
        read_commands.append(_read_command_for_source(path))
    read_commands.append(_read_command_for_source(assertion_file, formal=True))
    read_commands.append(_read_command_for_source(bind_file, formal=True))
    options_extra = "\nmulticlock on" if multi_clock else ""
    files = [*_sby_file_entries(rtl_files), assertion_file, bind_file]
    return f"""[tasks]
prove
cover

[options]
prove: mode prove
prove: depth {prove_depth}{options_extra}
cover: mode cover
cover: depth {cover_depth}{options_extra}

[engines]
prove: smtbmc z3
cover: smtbmc z3

[script]
{chr(10).join(read_commands)}
prep -top {module_name}

[files]
{chr(10).join(files)}
"""


def _gen_jasper_tcl(
    *,
    module_name: str,
    rtl_files: List[str],
    assertion_file: str,
    bind_file: str,
) -> str:
    analyze_files = " ".join(_tcl_quote(path) for path in [*rtl_files, assertion_file, bind_file])
    return "\n".join(
        [
            "# Jasper-style formal launch script generated by ChipVerify AI",
            f"analyze -sv {analyze_files}",
            f"elaborate -top {module_name}",
            "clock -infer",
            "reset -infer",
            "prove -all",
            "report -summary",
            "",
        ]
    )


async def _call_formal_plan_ai_client(
    *,
    ai_client: Any,
    context: Dict[str, Any],
    fallback_plan: Dict[str, Any],
) -> str:
    system_prompt = (
        "You are a senior formal verification engineer. Create a reviewable "
        "formal verification plan only from the provided mental model. "
        "Return valid JSON only."
    )
    prompt = f"""Create a mental-model-backed formal verification plan.

MENTAL MODEL CONTEXT:
```json
{json.dumps(context, indent=2, default=str)[:18000]}
```

RULE-BASED BASELINE PLAN:
```json
{json.dumps(fallback_plan, indent=2, default=str)[:8000]}
```

Return JSON in this exact shape:
{{
  "top_module": "{context.get('top_module') or 'dut'}",
  "clock": "{context.get('clock') or 'clk'}",
  "reset": "{context.get('reset') or 'rst_n'}",
  "reset_polarity": "active_low|active_high",
  "modes": ["prove", "cover"],
  "engine": "smtbmc z3",
  "depth": {{"prove": 40, "cover": 80}},
  "bind_strategy": "external_bind",
  "assumptions": [
    {{"id": "F-AS-001", "name": "snake_case_name", "description": "environment constraint", "signals": ["existing_port_name"], "template": "inputs_known_after_reset"}}
  ],
  "assertions": [
    {{"id": "F-A-001", "name": "snake_case_name", "description": "property intent", "signals": ["existing_port_name"], "requirement_ids": ["REQ-001"], "template": "known_output|valid_stable_until_ready|fsm_legal_state", "priority": "high"}}
  ],
  "covers": [
    {{"id": "F-C-001", "name": "snake_case_name", "description": "reachability goal", "signals": ["existing_port_name"], "template": "signals_seen"}}
  ],
  "open_questions": ["ambiguity to ask the user"],
  "files_preview": ["{context.get('top_module') or 'dut'}_sva.sv", "{context.get('top_module') or 'dut'}_formal.sby"]
}}

Rules:
- Use only signal names and requirement IDs from MENTAL MODEL CONTEXT.
- Treat knowledge_base.formal and knowledge_base.plan_hints as candidate property guidance; still validate each property against real model signals.
- Use assumptions only for environment constraints; do not hide DUT bugs by over-constraining outputs.
- Prefer safety assertions, protocol stability, FSM legality, reset behavior, and cover reachability.
- Read evidence and living_agent_memory before planning. Do not repeat properties that previous compile/prove evidence marked invalid unless the new plan fixes the cause.
- If open_questions or evidence show unresolved ambiguity, add an open question instead of guessing a property.
- If behavior is ambiguous, add an open question instead of guessing.
- This is a plan only. Do not generate full SystemVerilog code here.
"""
    result = None
    if hasattr(ai_client, "chat"):
        result = ai_client.chat(system_prompt, prompt)
    elif hasattr(ai_client, "generate"):
        result = ai_client.generate(prompt, system_prompt=system_prompt, temperature=0.1)
    else:
        raise RuntimeError("AI client does not support chat or generate")
    if asyncio.iscoroutine(result):
        result = await result
    return str(result or "")


async def _enhance_formal_sva_with_llm(
    *,
    ai_client: Any,
    filename: str,
    scaffold: str,
    context: Dict[str, Any],
    plan: Dict[str, Any],
) -> str:
    system_prompt = (
        "You are a senior SVA engineer. Improve the provided assertion module "
        "without changing module names, ports, bind compatibility, or inventing signals. "
        "Return only SystemVerilog source."
    )
    prompt = f"""Improve this formal assertion file using the approved plan and mental model.

File: {filename}

MENTAL MODEL CONTEXT:
```json
{json.dumps(context, indent=2, default=str)[:12000]}
```

APPROVED FORMAL PLAN:
```json
{json.dumps(plan, indent=2, default=str)[:10000]}
```

CURRENT SVA:
```systemverilog
{scaffold[:18000]}
```

Rules:
- Preserve the existing module declaration and port list.
- Use only existing port/signal names from the mental model.
- Do not include markdown fences or explanations.
- Keep assertions reviewable and avoid over-constraining DUT outputs.
"""
    if hasattr(ai_client, "chat"):
        result = ai_client.chat(system_prompt, prompt)
    elif hasattr(ai_client, "generate"):
        result = ai_client.generate(prompt, system_prompt=system_prompt, temperature=0.1)
    else:
        raise RuntimeError("AI client does not support chat or generate")
    if asyncio.iscoroutine(result):
        result = await result
    return str(result or "")


def _sanitize_formal_plan_from_ai(
    plan_data: Dict[str, Any],
    context: Dict[str, Any],
    fallback: Dict[str, Any],
) -> Dict[str, Any]:
    allowed_ports = {p["name"] for p in context["ports"] if p.get("name")}
    allowed_reqs = {r["id"] for r in context["requirements"] if r.get("id")}
    plan = {
        "top_module": str(plan_data.get("top_module") or fallback.get("top_module") or context["top_module"]),
        "clock": str(plan_data.get("clock") or fallback.get("clock") or context.get("clock") or "clk"),
        "reset": str(plan_data.get("reset") or fallback.get("reset") or context.get("reset") or "rst_n"),
        "reset_polarity": str(plan_data.get("reset_polarity") or fallback.get("reset_polarity") or "active_low"),
        "modes": _sanitize_text_list(plan_data.get("modes"), fallback.get("modes", ["prove", "cover"])),
        "engine": str(plan_data.get("engine") or fallback.get("engine") or "smtbmc z3"),
        "depth": _sanitize_depth(plan_data.get("depth"), fallback.get("depth", {})),
        "bind_strategy": "external_bind",
        "assumptions": _sanitize_formal_items(
            plan_data.get("assumptions"),
            fallback.get("assumptions", []),
            allowed_ports,
            allowed_reqs,
            default_kind="assume",
        ),
        "assertions": _sanitize_formal_items(
            plan_data.get("assertions"),
            fallback.get("assertions", []),
            allowed_ports,
            allowed_reqs,
            default_kind="assert",
        ),
        "covers": _sanitize_formal_items(
            plan_data.get("covers"),
            fallback.get("covers", []),
            allowed_ports,
            allowed_reqs,
            default_kind="cover",
        ),
        "open_questions": _sanitize_text_list(
            plan_data.get("open_questions"),
            fallback.get("open_questions", []),
        ),
        "files_preview": _sanitize_text_list(
            plan_data.get("files_preview"),
            fallback.get("files_preview", []),
        ),
    }
    if not plan["assertions"]:
        plan["assertions"] = list(fallback.get("assertions", []))
    plan["properties"] = plan["assertions"]
    plan["property_count"] = len(plan["assertions"])
    plan["assumption_count"] = len(plan["assumptions"])
    plan["cover_count"] = len(plan["covers"])
    plan["traceability"] = _formal_traceability(
        plan["assertions"],
        plan["covers"],
        allowed_reqs,
    )
    return plan


def _sanitize_formal_items(
    raw_items: Any,
    fallback_items: List[Dict[str, Any]],
    allowed_ports: set[str],
    allowed_reqs: set[str],
    *,
    default_kind: str,
) -> List[Dict[str, Any]]:
    source = raw_items if isinstance(raw_items, list) else fallback_items
    cleaned: List[Dict[str, Any]] = []
    for index, raw in enumerate(source or []):
        if not isinstance(raw, dict):
            continue
        signals = _filter_allowed_signals(raw.get("signals", []), allowed_ports)
        if not signals and default_kind != "cover":
            signals = _filter_allowed_signals(raw.get("related_signals", []), allowed_ports)
        item = {
            "id": str(raw.get("id") or f"F-{default_kind[:1].upper()}-{index + 1:03d}"),
            "name": _safe_sv_name(str(raw.get("name") or f"{default_kind}_{index + 1}")),
            "kind": str(raw.get("kind") or default_kind),
            "description": str(raw.get("description") or raw.get("text") or ""),
            "signals": signals,
            "requirement_ids": _filter_allowed_requirements(raw.get("requirement_ids", []), allowed_reqs),
            "template": str(raw.get("template") or ("signals_seen" if default_kind == "cover" else "known_output")),
            "priority": str(raw.get("priority") or "medium"),
        }
        states = raw.get("states")
        if isinstance(states, list):
            item["states"] = [str(s) for s in states if str(s)]
        if raw.get("state"):
            item["state"] = str(raw.get("state"))
        if raw.get("encoding"):
            item["encoding"] = str(raw.get("encoding"))
        sva = str(raw.get("sva") or "")
        if sva and _sva_uses_only_allowed_signals(sva, allowed_ports):
            item["sva"] = sva
        cleaned.append(item)
    return cleaned


def _compact_block_models_for_context(block_models: Any, limit: int = 24) -> Dict[str, Any]:
    """Expose sub-module facts to the formal planner without flooding context."""
    if not isinstance(block_models, dict):
        return {}

    def _list_attr(block: Any, key: str) -> List[Any]:
        value = _get(block, key, [])
        return value if isinstance(value, list) else []

    compact: Dict[str, Any] = {}
    for name, block in list(block_models.items())[:limit]:
        compact[str(name)] = {
            "top_module": _get(block, "top_module", str(name)),
            "definition_kind": _get(block, "definition_kind", "Module"),
            "file": _get(block, "file", ""),
            "description": _get(block, "description", ""),
            "ports": _to_jsonable(_list_attr(block, "ports")[:32]),
            "parameters": _to_jsonable(_list_attr(block, "parameters")[:16]),
            "instantiations": _to_jsonable(_list_attr(block, "instantiations")[:16]),
            "sub_instances": _to_jsonable(_list_attr(block, "sub_instances")[:16]),
            "clock_domains": _to_jsonable(_list_attr(block, "clock_domains")[:8]),
            "protocols": _to_jsonable(_list_attr(block, "protocols")[:8]),
            "fsms": _to_jsonable(_list_attr(block, "fsms")[:8]),
            "existing_assertions": _to_jsonable(_list_attr(block, "existing_assertions")[:12]),
            "register_fields": _to_jsonable(_list_attr(block, "register_fields")[:16]),
            "expected_behaviors": _to_jsonable(_list_attr(block, "expected_behaviors")[:8]),
            "transaction_flows": _to_jsonable(_list_attr(block, "transaction_flows")[:8]),
            "internal_symbols": _to_jsonable(_list_attr(block, "internal_symbols")[:64]),
            "modports": _to_jsonable(_list_attr(block, "modports")[:16]),
        }
    return compact


def _compact_evidence_for_context(items: Any, limit: int = 20) -> List[Any]:
    if not isinstance(items, list):
        return []
    return _to_jsonable(items[-limit:])


def _mental_model_formal_context(
    model: Any,
    *,
    content: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    design = _get(model, "design", {})
    if isinstance(content, dict) and not design:
        design = content.get("design", {})
    ports = _get(design, "ports", []) or []
    requirements = _get(model, "requirements", []) or (content or {}).get("requirements", [])
    scan = _get(model, "project_scan", None) or (content or {}).get("project_scan", {})
    block_models = (
        (content or {}).get("block_models", {})
        if isinstance(content, dict)
        else _get(model, "block_models", {})
    )
    symbol_table = (
        (content or {}).get("symbol_table", {})
        if isinstance(content, dict)
        else _get(model, "symbol_table", {})
    )
    knowledge_base = (
        (content or {}).get("knowledge_base", {})
        if isinstance(content, dict)
        else _get(model, "knowledge_base", {})
    )
    evidence = (
        (content or {}).get("evidence", [])
        if isinstance(content, dict)
        else _get(model, "evidence", [])
    )
    living_agent = (
        (content or {}).get("living_agent", {})
        if isinstance(content, dict)
        else _get(model, "living_agent", {})
    )
    living_memory = _get(living_agent, "verification_memory", {}) if living_agent else {}
    ctx = {
        "top_module": str(_get(design, "top_module", "dut") or "dut"),
        "parser_engine": (
            (content or {}).get("parser_engine", "")
            if isinstance(content, dict)
            else _get(model, "parser_engine", "")
        ),
        "parser_version": (
            (content or {}).get("parser_version", "")
            if isinstance(content, dict)
            else _get(model, "parser_version", "")
        ),
        "parser_diagnostics": _to_jsonable(
            (content or {}).get("parser_diagnostics", [])
            if isinstance(content, dict)
            else _get(model, "parser_diagnostics", [])
        ),
        "description": str(_get(design, "description", "") or ""),
        "ports": [
            {
                "name": _port_name(port),
                "direction": _port_dir(port),
                "width": _port_width(port),
                "bus_range": _port_range(port),
                "protocol_role": str(_get(port, "protocol_role", "") or ""),
            }
            for port in ports
            if _port_name(port)
        ],
        "parameters": _to_jsonable(_get(design, "parameters", []) or []),
        "modules": _to_jsonable(_get(design, "modules", []) or []),
        "clock_domains": _to_jsonable(_get(design, "clock_domains", []) or []),
        "protocols": _to_jsonable(_get(design, "protocols", []) or []),
        "fsms": _to_jsonable(_get(design, "fsms", []) or []),
        "expected_behaviors": _to_jsonable(_get(design, "expected_behaviors", []) or []),
        "transaction_flows": _to_jsonable(_get(design, "transaction_flows", []) or []),
        "register_fields": _to_jsonable(_get(design, "register_fields", []) or []),
        "verification_intent": _to_jsonable(_get(model, "verification", {}) or (content or {}).get("verification", {})),
        "block_models": _compact_block_models_for_context(block_models),
        "symbol_table": _to_jsonable(symbol_table if isinstance(symbol_table, dict) else {}),
        "knowledge_base": _to_jsonable(knowledge_base if isinstance(knowledge_base, dict) else {}),
        "evidence": _compact_evidence_for_context(evidence),
        "living_agent_memory": _to_jsonable(living_memory),
        "requirements": [
            {
                "id": str(_get(req, "id", "") or ""),
                "text": str(_get(req, "text", "") or ""),
                "priority": str(_get(req, "priority", "medium") or "medium"),
                "category": str(_get(req, "category", "functional") or "functional"),
            }
            for req in requirements or []
        ],
        "estimated_complexity": str(_get(scan, "estimated_complexity", "") or _get(design, "complexity", "medium") or "medium"),
        "risks": _to_jsonable(_get(model, "risks", []) or (content or {}).get("risks", [])),
        "open_questions": _to_jsonable(_get(model, "open_questions", []) or (content or {}).get("open_questions", [])),
    }
    clk, rst, rst_active_low = _detect_clk_rst_from_context(ctx)
    ctx["clock"] = clk or "clk"
    ctx["reset"] = rst or "rst_n"
    ctx["reset_polarity"] = "active_low" if rst_active_low else "active_high"
    return ctx


def _detect_clk_rst_from_context(context: Dict[str, Any]) -> tuple[str, str, bool]:
    clock_domains = context.get("clock_domains") or []
    if clock_domains and isinstance(clock_domains[0], dict):
        cd = clock_domains[0]
        clk = str(cd.get("name") or "")
        rst = str(cd.get("associated_reset") or cd.get("reset") or "")
        if clk or rst:
            return (
                clk,
                rst,
                str(cd.get("reset_polarity") or "active_low") == "active_low",
            )
    ports = context.get("ports", [])
    clk = next((p["name"] for p in ports if _is_clock_name(p.get("name", ""))), "")
    rst = next((p["name"] for p in ports if _is_reset_name(p.get("name", ""))), "")
    return clk, rst, bool(rst and ("n" in rst.lower() or rst.lower().endswith("_n")))


def _coerce_formal_plan(plan: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not isinstance(plan, dict) or not plan:
        return None
    if "formal" in plan and isinstance(plan["formal"], dict):
        return plan["formal"]
    if "assertions" in plan or "property_count" in plan:
        return plan
    return None


def _formal_traceability(
    assertions: List[Dict[str, Any]],
    covers: List[Dict[str, Any]],
    allowed_reqs: set[str],
) -> Dict[str, Any]:
    covered = set()
    for item in [*(assertions or []), *(covers or [])]:
        for rid in item.get("requirement_ids", []) or []:
            if rid in allowed_reqs:
                covered.add(rid)
    return {
        "requirements_total": len(allowed_reqs),
        "requirements_targeted": len(covered),
        "assertions_total": len(assertions or []),
        "covers_total": len(covers or []),
        "targeted_requirement_ids": sorted(covered),
    }


def _best_requirement_ids(requirements: List[Dict[str, Any]], text: str) -> List[str]:
    text_l = str(text or "").lower()
    matches = []
    for req in requirements:
        rid = str(req.get("id") or "")
        body = str(req.get("text") or "").lower()
        if rid and text_l and any(token for token in re.split(r"\W+", text_l) if len(token) > 3 and token in body):
            matches.append(rid)
        if len(matches) >= 3:
            break
    return matches


def _extract_behavior_signals(behavior: Dict[str, Any]) -> List[str]:
    signals: List[str] = []
    for key in ("signals", "related_signals", "stimulus", "expected_output", "outputs"):
        value = behavior.get(key) if isinstance(behavior, dict) else None
        if isinstance(value, list):
            signals.extend(str(v) for v in value)
        elif isinstance(value, dict):
            signals.extend(str(k) for k in value.keys())
            signals.extend(str(v) for v in value.values())
        elif isinstance(value, str):
            signals.extend(re.findall(r"\b[A-Za-z_][A-Za-z0-9_]*\b", value))
    return signals


def _extract_flow_signals(flow: Dict[str, Any]) -> List[str]:
    signals: List[str] = []
    for key in ("signals", "steps", "source", "destination"):
        value = flow.get(key) if isinstance(flow, dict) else None
        if isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    signals.extend(_extract_behavior_signals(item))
                else:
                    signals.append(str(item))
        elif isinstance(value, str):
            signals.extend(re.findall(r"\b[A-Za-z_][A-Za-z0-9_]*\b", value))
    return signals


def _filter_allowed_signals(raw: Any, allowed: set[str]) -> List[str]:
    if isinstance(raw, str):
        candidates = re.findall(r"\b[A-Za-z_][A-Za-z0-9_]*\b", raw)
    elif isinstance(raw, list):
        candidates = [str(item) for item in raw]
    else:
        candidates = []
    result = []
    for item in candidates:
        if item in allowed and item not in result:
            result.append(item)
    return result


def _filter_allowed_requirements(raw: Any, allowed: set[str]) -> List[str]:
    if isinstance(raw, str):
        candidates = [raw]
    elif isinstance(raw, list):
        candidates = [str(item) for item in raw]
    else:
        candidates = []
    return [item for item in candidates if item in allowed]


def _sanitize_text_list(raw: Any, fallback: Optional[List[str]] = None) -> List[str]:
    source = raw if isinstance(raw, list) else (fallback or [])
    return [str(item).strip()[:240] for item in source if str(item).strip()]


def _sanitize_depth(raw: Any, fallback: Dict[str, Any]) -> Dict[str, int]:
    if not isinstance(raw, dict):
        raw = fallback if isinstance(fallback, dict) else {}
    return {
        "prove": max(1, min(500, int(raw.get("prove") or 40))),
        "cover": max(1, min(500, int(raw.get("cover") or raw.get("prove") or 80))),
    }


def _sva_uses_only_allowed_signals(sva: str, allowed_ports: set[str]) -> bool:
    if not sva:
        return False
    forbidden = {"module", "endmodule", "bind", "assign", "always", "initial"}
    tokens = set(re.findall(r"\b[A-Za-z_][A-Za-z0-9_]*\b", sva))
    if tokens & forbidden:
        return False
    known_keywords = {
        "assert", "assume", "cover", "property", "endproperty", "posedge", "negedge",
        "disable", "iff", "stable", "past", "rose", "fell", "isunknown", "error",
        "and", "or", "not", "strong", "weak", "true", "false",
        "sequence", "endsequence", "throughout", "within", "intersect", "first_match",
        "eventually", "s_eventually", "until", "until_with", "nexttime", "s_nexttime",
    }
    unknown = {
        token for token in tokens
        if token not in allowed_ports and token not in known_keywords and not token.startswith("p_")
    }
    unknown_non_numeric = {
        token for token in unknown
        if not re.match(r"^\d+$", token)
        and not re.match(r"^\d+'[bhdBHD][0-9a-fA-F_xXzZ]+$", token)
    }
    return len(unknown_non_numeric) <= 1


def _looks_like_sva_expression(sva: str) -> bool:
    return bool(re.search(r"\b(assert|assume|cover|property|\|->|\|=>)\b", sva))


def _accept_formal_sva_candidate(
    *,
    original: str,
    candidate: str,
    module_name: str,
    allowed_ports: set[str],
) -> tuple[bool, str]:
    if not candidate.strip():
        return False, "empty output"
    if "```" in candidate:
        return False, "markdown fence present"
    if f"module {module_name}" not in candidate:
        return False, "module name changed"
    if candidate.count("endmodule") != original.count("endmodule"):
        return False, "endmodule count changed"
    for port in allowed_ports:
        if re.search(rf"\b{re.escape(port)}\b", original) and not re.search(rf"\b{re.escape(port)}\b", candidate):
            return False, f"port removed: {port}"
    return True, "accepted"


def _sanitize_sv_text(text: str) -> str:
    cleaned = re.sub(
        r"```(?:systemverilog|verilog|sv)?\s*",
        "",
        str(text or "").strip(),
        flags=re.IGNORECASE,
    )
    cleaned = cleaned.replace("```", "")
    return cleaned.rstrip() + "\n" if cleaned.strip() else ""


def _extract_json_object(text: str) -> Dict[str, Any]:
    match = re.search(r"\{[\s\S]*\}", text or "")
    if not match:
        raise ValueError("AI response did not contain a JSON object")
    data = json.loads(match.group())
    if not isinstance(data, dict):
        raise ValueError("AI response JSON was not an object")
    return data


def _get(obj: Any, key: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _to_jsonable(value: Any) -> Any:
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, dict):
        return {str(k): _to_jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_to_jsonable(v) for v in value]
    if hasattr(value, "__dict__"):
        return {
            str(k): _to_jsonable(v)
            for k, v in vars(value).items()
            if not str(k).startswith("_")
        }
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _port_name(port: Any) -> str:
    return str(_get(port, "name", "") or "")


def _port_dir(port: Any) -> str:
    return str(_get(port, "direction", "") or "").lower()


def _port_width(port: Any) -> int:
    try:
        return int(_get(port, "width", 1) or 1)
    except Exception:
        return 1


def _port_range(port: Any) -> str:
    return str(_get(port, "bus_range", "") or "")


def _safe_sv_name(raw: str) -> str:
    cleaned = re.sub(r"\W+", "_", str(raw or "").strip().lower()).strip("_")
    if not cleaned:
        cleaned = "property"
    if cleaned[0].isdigit():
        cleaned = f"p_{cleaned}"
    return cleaned[:80]


def _is_sv_identifier(raw: str) -> bool:
    return bool(re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", str(raw or "")))


def _find_signal(port_group: List[str], keywords: List[str]) -> str:
    for port in port_group:
        lower = port.lower()
        if any(keyword in lower for keyword in keywords):
            return port
    return ""


def _state_val(state: str, idx: int, encoding: str) -> str:
    if str(encoding).lower() == "one-hot":
        return f"(1 << {idx})"
    return str(idx)


def _sv_concat(signals: List[str]) -> str:
    if not signals:
        return "1'b0"
    if len(signals) == 1:
        return signals[0]
    return "{" + ", ".join(signals) + "}"


def _is_clock_name(name: str) -> bool:
    lower = str(name or "").lower()
    return any(token in lower for token in ("clk", "clock", "aclk"))


def _is_reset_name(name: str) -> bool:
    lower = str(name or "").lower()
    return any(token in lower for token in ("rst", "reset", "aresetn"))


def _read_command_for_source(path: str, *, formal: bool = False) -> str:
    suffix = Path(path).suffix.lower()
    quoted = _sby_quote(path)
    if suffix in {".sv", ".svh"}:
        return f"read -formal -sv {quoted}" if formal else f"read -sv {quoted}"
    if suffix in {".v", ".vh"}:
        return f"read -formal {quoted}" if formal else f"read -vlog2k {quoted}"
    if suffix in {".vhd", ".vhdl"}:
        return f"read -vhdl {quoted}"
    return f"read -sv {quoted}"


def _sby_file_entries(paths: Iterable[str]) -> List[str]:
    return [str(path).replace("\\", "/") for path in paths]


def _sby_quote(path: str) -> str:
    text = str(path).replace("\\", "/")
    if re.search(r"\s", text):
        return '"' + text.replace('"', '\\"') + '"'
    return text


def _tcl_quote(path: str) -> str:
    return "{" + str(path).replace("\\", "/") + "}"


def _write(root: Path, name: str, content: str) -> str:
    path = root / name
    path.write_text(content, encoding="utf-8")
    return str(path)
