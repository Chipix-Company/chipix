"""
UVM Generation Service — Generate UVM environments from mental model.

Produces a complete UVM testbench environment using the mental model's
design structure (ports, protocols, FSMs) and verification intent
(UVM scenarios, requirements, coverage points).

Template-based generation with optional LLM refinement.
"""

from __future__ import annotations

import copy
import contextvars
import inspect
import json
import logging
import os
import re
from dataclasses import asdict, dataclass, field, is_dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)
_UVM_PROGRESS_CALLBACK = contextvars.ContextVar("uvm_progress_callback", default=None)


def set_uvm_progress_callback(callback: Any):
    """Attach a per-request progress callback used by streaming routes."""
    return _UVM_PROGRESS_CALLBACK.set(callback)


def publish_uvm_progress(event: Dict[str, Any]) -> None:
    """Emit a progress event if a streaming callback is attached."""
    callback = _UVM_PROGRESS_CALLBACK.get()
    if callback is None:
        return
    try:
        callback(event)
    except Exception:
        logger.debug("uvm progress callback failed", exc_info=True)


def reset_uvm_progress_callback(token: Any) -> None:
    _UVM_PROGRESS_CALLBACK.reset(token)


def _emit_uvm_progress(event: Dict[str, Any]) -> None:
    callback = _UVM_PROGRESS_CALLBACK.get()
    if not callback:
        return
    try:
        callback({"phase": "uvm", **event})
    except Exception:
        logger.warning("UVM progress callback failed", exc_info=True)


# ─── Output Types ────────────────────────────────────────────────────

@dataclass
class UVMOutput:
    """Complete UVM environment output."""
    pkg_file: str = ""
    interface_file: str = ""
    interface_files: List[str] = field(default_factory=list)
    seq_item_file: str = ""
    sequencer_file: str = ""
    driver_file: str = ""
    monitor_file: str = ""
    scoreboard_file: str = ""
    agent_file: str = ""
    agent_files: List[str] = field(default_factory=list)
    env_file: str = ""
    seq_lib_file: str = ""
    sequence_files: List[str] = field(default_factory=list)
    coverage_file: str = ""
    test_file: str = ""
    test_files: List[str] = field(default_factory=list)
    top_file: str = ""
    filelist: str = ""
    build_file: str = ""
    regression_manifest: str = ""
    support_files: List[str] = field(default_factory=list)
    summary: str = ""
    all_files: List[str] = field(default_factory=list)
    generation_mode: str = "scaffold"
    llm_called: bool = False
    llm_status: str = "not_requested"
    llm_candidate_files: List[str] = field(default_factory=list)
    llm_enhanced_files: List[str] = field(default_factory=list)
    llm_errors: List[str] = field(default_factory=list)
    generation_policy: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "pkg_file": self.pkg_file,
            "pkg": self.pkg_file,
            "interface_file": self.interface_file,
            "interface": self.interface_file,
            "interface_files": self.interface_files,
            "interfaces": self.interface_files,
            "seq_item_file": self.seq_item_file,
            "seq_item": self.seq_item_file,
            "sequencer_file": self.sequencer_file,
            "sequencer": self.sequencer_file,
            "driver_file": self.driver_file,
            "driver": self.driver_file,
            "monitor_file": self.monitor_file,
            "monitor": self.monitor_file,
            "scoreboard_file": self.scoreboard_file,
            "scoreboard": self.scoreboard_file,
            "agent_file": self.agent_file,
            "agent": self.agent_file,
            "agent_files": self.agent_files,
            "agents": self.agent_files,
            "env_file": self.env_file,
            "env": self.env_file,
            "seq_lib_file": self.seq_lib_file,
            "seq_lib": self.seq_lib_file,
            "sequence_files": self.sequence_files,
            "sequences": self.sequence_files,
            "coverage_file": self.coverage_file,
            "coverage": self.coverage_file,
            "test_file": self.test_file,
            "test": self.test_file,
            "test_files": self.test_files,
            "tests": self.test_files,
            "top_file": self.top_file,
            "top": self.top_file,
            "filelist": self.filelist,
            "build_file": self.build_file,
            "regression_manifest": self.regression_manifest,
            "support_files": self.support_files,
            "total_files": len(self.all_files),
            "generation_mode": self.generation_mode,
            "llm_called": self.llm_called,
            "llm_status": self.llm_status,
            "llm_candidate_files": self.llm_candidate_files,
            "llm_enhanced_files": self.llm_enhanced_files,
            "llm_errors": self.llm_errors,
            "generation_policy": self.generation_policy,
        }


@dataclass
class _UVMGeneratedSymbolRegistry:
    """Generated package/type names that LLM-enhanced files may reference."""

    allowed_packages: set[str] = field(default_factory=set)
    generated_types: set[str] = field(default_factory=set)

    def prompt_summary(self) -> str:
        packages = ", ".join(sorted(self.allowed_packages)) or "(none)"
        types = ", ".join(sorted(self.generated_types)) or "(none)"
        return (
            "Generated UVM package/type contract:\n"
            f"- Allowed package imports: {packages}\n"
            f"- Allowed generated classes/interfaces/modules: {types}\n"
            "- Do not introduce per-agent packages, config classes, or role-qualified "
            "sequencer aliases unless they are listed above."
        )


# ─── Main Generator ─────────────────────────────────────────────────

@dataclass
class UVMAgentSpec:
    """Grounded UVM agent bundle derived from the mental model or approved plan."""
    name: str
    base: str
    agent_type: str
    ports: List[Any]
    description: str = ""
    protocols: List[Any] = field(default_factory=list)
    constraints: List[str] = field(default_factory=list)

    @property
    def inputs(self) -> List[Any]:
        return [p for p in self.ports if _port_dir(p) in {"input", "inout"}]

    @property
    def outputs(self) -> List[Any]:
        return [p for p in self.ports if _port_dir(p) in {"output", "inout"}]

    @property
    def is_active(self) -> bool:
        return self.agent_type == "active"


async def generate_uvm_plan_with_ai(
    model: Any,
    *,
    content: Optional[Dict[str, Any]] = None,
    ai_client: Any = None,
    fallback_plan: Optional[Dict[str, Any]] = None,
    require_llm: bool = False,
) -> Dict[str, Any]:
    """
    Generate a reviewable UVM plan with an LLM, grounded by the mental model.

    The LLM only proposes the plan. Returned port and requirement references are
    validated against the persisted mental model before the staged flow sees it.
    """
    fallback = copy.deepcopy(fallback_plan or {})
    if fallback:
        fallback.setdefault("generation_engine", "rules")
        fallback.setdefault("llm_used", False)
        fallback.setdefault("grounding", "mental_model_rules")

    if ai_client is None:
        if require_llm:
            raise RuntimeError("AI client is required for UVM plan generation")
        return fallback

    try:
        context = _mental_model_uvm_plan_context(model, content=content)
        response = await _call_uvm_plan_ai_client(
            ai_client=ai_client,
            context=context,
            fallback_plan=fallback,
        )
        try:
            plan_data = _extract_json_object(response)
        except Exception as parse_exc:
            logger.warning("AI UVM plan JSON parse failed; requesting repair: %s", parse_exc)
            repaired = await _repair_uvm_plan_json(
                ai_client=ai_client,
                previous_response=response,
                context=context,
                fallback_plan=fallback,
            )
            if repaired is None:
                raise parse_exc
            plan_data = repaired
        plan = _sanitize_uvm_plan_from_ai(plan_data, context, fallback)
        plan["generation_engine"] = "llm_grounded"
        plan["llm_used"] = True
        plan["grounding"] = "mental_model_validated"
        return plan
    except Exception as exc:
        logger.warning("AI UVM plan generation failed: %s", exc)
        if fallback:
            fallback["generation_engine"] = "rules_fallback"
            fallback["llm_used"] = False
            fallback["llm_attempted"] = True
            fallback["grounding"] = "mental_model_rules"
            fallback["llm_error"] = str(exc)
            fallback["llm_warning"] = (
                "The AI UVM plan response was not valid after repair; "
                "showing the grounded mental-model fallback plan."
            )
            return fallback
        if require_llm:
            raise RuntimeError(f"AI UVM plan generation failed: {exc}") from exc
        return fallback


def generate_uvm_from_model(
    model: Any,
    work_dir: str,
    target_module: str = "",
    ai_client: Any = None,
) -> UVMOutput:
    """
    Generate a complete UVM environment from the mental model.

    Uses model.design (ports, parameters, protocols, FSMs) to generate:
    - Package with type definitions
    - Interface with clocking blocks
    - Agent: driver + monitor
    - Scoreboard with reference model skeleton
    - Sequences for each requirement
    - Coverage collectors
    - Test top that connects everything

    Args:
        model: MentalModelSchema or dict with design info
        work_dir: Directory to write generated files
        target_module: Module to generate UVM for (default: top_module)
        ai_client: Optional LLM client for enhanced generation
    """
    work = Path(work_dir)
    work.mkdir(parents=True, exist_ok=True)

    # Extract design info from model
    design = _get_design(model)
    top = target_module or design.get("top_module", "dut")
    ports = design.get("ports", [])
    parameters = design.get("parameters", [])
    clock_domains = design.get("clock_domains", [])
    protocols = design.get("protocols", [])
    fsms = design.get("fsms", [])
    requirements = _get_requirements(model)
    uvm_scenarios = _get_uvm_scenarios(model)
    uvm_agents = _get_uvm_agents(model)
    uvm_scoreboard_checks = _get_verification_list(model, "uvm_scoreboard_checks")
    uvm_coverage_points = _get_verification_list(model, "uvm_coverage_points")
    uvm_assertions = _get_verification_list(model, "formal_properties")
    reference_data = {
        "expected_behaviors": design.get("expected_behaviors", []),
        "register_fields": design.get("register_fields", []),
        "register_map": design.get("register_map", []),
        "transaction_flows": design.get("transaction_flows", []),
        "constraints": design.get("constraints", []),
    }
    dut_rtl_files = _get_dut_rtl_files(model)
    uvm_parameters = _collect_uvm_parameters(parameters, ports)

    # Detect clock/reset
    clk_name, rst_name, rst_active_low = _detect_clk_rst(ports, clock_domains)

    # Classify ports
    input_ports = [p for p in ports if _port_dir(p) == "input" and _port_name(p) not in (clk_name, rst_name)]
    output_ports = [p for p in ports if _port_dir(p) == "output"]

    from services.verification.uvm_planner import (
        compute_design_profile,
        plan_uvm_generation,
    )

    profile = compute_design_profile(model)
    generation_plan = plan_uvm_generation(profile, model)
    limits = generation_plan.content_limits

    output = UVMOutput()
    output.generation_policy = generation_plan.to_dict()
    if not generation_plan.multi_agent and uvm_agents:
        output.generation_policy["single_agent_plan"] = _to_jsonable(uvm_agents[0])
    _emit_uvm_progress({
        "type": "policy_selected",
        "message": (
            f"UVM policy: {generation_plan.tier} tier, "
            f"{'multi-agent' if generation_plan.multi_agent else 'single-agent'}"
        ),
        "tier": generation_plan.tier,
        "complexity_score": generation_plan.complexity_score,
        "multi_agent": generation_plan.multi_agent,
    })

    agent_specs = (
        _resolve_uvm_agent_specs(
            model=model,
            ports=ports,
            protocols=protocols,
            clk=clk_name,
            rst=rst_name,
        )
        if generation_plan.multi_agent
        else []
    )
    if generation_plan.multi_agent and len(agent_specs) > 1:
        return _generate_multi_agent_uvm(
            top=top,
            work=work,
            output=output,
            ports=ports,
            parameters=uvm_parameters,
            fsms=fsms,
            clk_name=clk_name,
            rst_name=rst_name,
            rst_active_low=rst_active_low,
            agent_specs=agent_specs,
            uvm_scenarios=uvm_scenarios,
            uvm_scoreboard_checks=uvm_scoreboard_checks,
            uvm_coverage_points=uvm_coverage_points,
            uvm_assertions=uvm_assertions,
            reference_data=reference_data,
            content_limits=limits,
            dut_rtl_files=dut_rtl_files,
        )

    sequence_specs = _build_sequence_specs(top, uvm_scenarios, limits)
    output.seq_lib_file = _write(
        work,
        f"{top}_seq_lib.sv",
        _gen_consolidated_seq_lib(top, sequence_specs, input_ports),
    )
    output.sequence_files.append(output.seq_lib_file)

    # Generate each file
    output.pkg_file = _write(
        work,
        f"{top}_pkg.sv",
        _gen_package(top, ports, uvm_parameters, [Path(output.seq_lib_file).name], f"{top}_tests.sv"),
    )
    output.interface_file = _write(work, f"{top}_if.sv", _gen_interface(top, ports, clk_name, rst_name))
    output.seq_item_file = _write(work, f"{top}_seq_item.sv", _gen_seq_item(top, input_ports, output_ports, reference_data))
    output.sequencer_file = _write(work, f"{top}_sequencer.sv", _gen_sequencer(top))
    output.driver_file = _write(work, f"{top}_driver.sv", _gen_driver(top, input_ports, output_ports, clk_name, rst_name, rst_active_low, protocols, reference_data))
    output.monitor_file = _write(work, f"{top}_monitor.sv", _gen_monitor(top, input_ports, output_ports, clk_name, protocols, reference_data))
    output.scoreboard_file = _write(
        work,
        f"{top}_scoreboard.sv",
        _gen_scoreboard(top, input_ports, output_ports, uvm_scoreboard_checks, reference_data, protocols),
    )
    output.agent_file = _write(
        work,
        f"{top}_agent.sv",
        _gen_agent(top, uvm_agents[0] if uvm_agents else None),
    )
    output.env_file = _write(work, f"{top}_env.sv", _gen_env(top))
    output.coverage_file = _write(
        work,
        f"{top}_coverage.sv",
        _gen_coverage(
            top,
            input_ports,
            output_ports,
            fsms,
            _limit_items(uvm_coverage_points, limits.max_coverage_bins),
        ),
    )
    output.test_file = _write(
        work,
        f"{top}_tests.sv",
        _gen_test(
            top,
            _limit_items(requirements, limits.max_requirements),
            sequence_specs,
        ),
    )
    output.test_files.append(output.test_file)
    optional_roles = set(generation_plan.optional_files or [])
    if "assertions" in optional_roles:
        output.support_files.append(
            _write(work, f"{top}_assertions.sv", _gen_assertions(top, ports, clk_name, rst_name))
        )
    if "ral" in optional_roles:
        output.support_files.append(
            _write(work, f"{top}_ral.sv", _gen_ral(top, reference_data.get("register_fields", [])))
        )
    if "test_plan_doc" in optional_roles:
        output.support_files.append(
            _write(work, "test_plan.md", _gen_uvm_test_plan_doc(top, uvm_scenarios, uvm_scoreboard_checks, uvm_coverage_points))
        )
    output.top_file = _write(
        work,
        f"top_tb.sv",
        _gen_top(
            top,
            ports,
            clk_name,
            rst_name,
            rst_active_low,
            uvm_parameters,
            include_assertions="assertions" in optional_roles,
        ),
    )
    package_includes = [
        output.seq_item_file,
        output.seq_lib_file,
        output.sequencer_file,
        output.driver_file,
        output.monitor_file,
        output.scoreboard_file,
        output.agent_file,
        output.coverage_file,
        output.env_file,
        output.test_file,
    ]
    output.filelist = _write(
        work,
        f"{top}_uvm.f",
        _gen_filelist(
            top,
            work,
            dut_rtl_files,
            output.support_files,
            package_include_files=package_includes,
        ),
    )
    output.regression_manifest = _write(
        work,
        "uvm_regression_manifest.json",
        json.dumps(
            _build_regression_manifest(
                top=top,
                sequence_specs=sequence_specs,
                scoreboard_checks=uvm_scoreboard_checks,
                coverage_points=uvm_coverage_points,
                assertions=uvm_assertions,
            ),
            indent=2,
        ),
    )
    if "makefile" in optional_roles:
        output.build_file = _write(work, "Makefile", _gen_makefile(top))

    # Sequence files — one per scenario
    output.all_files = [
        f for f in [
            output.pkg_file, output.interface_file, output.seq_item_file,
            output.sequencer_file, output.driver_file, output.monitor_file, output.scoreboard_file,
            output.agent_file, output.env_file, output.coverage_file,
            output.seq_lib_file, output.test_file, output.top_file,
            output.filelist, output.build_file,
            output.regression_manifest,
        ] + output.sequence_files + output.support_files if f
    ]
    output.all_files = list(dict.fromkeys(output.all_files))

    output.summary = (
        f"Generated {len(output.all_files)} UVM files for {top}: "
        f"{len(input_ports)} inputs, {len(output_ports)} outputs, "
        f"{len(sequence_specs)} sequence classes, {generation_plan.tier} tier"
    )

    logger.info(output.summary)
    return output


async def generate_uvm_from_model_async(
    model: Any,
    work_dir: str,
    target_module: str = "",
    ai_client: Any = None,
    generation_mode: str = "hybrid",
    require_llm: bool = False,
) -> UVMOutput:
    """
    Generate UVM from the mental model, optionally enhancing scaffold files with an LLM.

    Modes:
    - scaffold: deterministic template generation only
    - hybrid: template scaffold first, then LLM improves each SystemVerilog file

    The hybrid path keeps the scaffold as the safety net and only accepts LLM
    output that preserves the expected package/module/interface/class names.
    """
    mode = (generation_mode or "hybrid").strip().lower()
    if mode not in {"scaffold", "hybrid"}:
        raise ValueError("generation_mode must be 'scaffold' or 'hybrid'")

    output = generate_uvm_from_model(
        model=model,
        work_dir=work_dir,
        target_module=target_module,
        ai_client=None,
    )
    output.generation_mode = mode
    _emit_uvm_progress({
        "type": "scaffold_complete",
        "message": output.summary,
        "total_files": len(output.all_files),
    })

    if mode == "scaffold":
        output.llm_status = "not_requested"
        return output

    if ai_client is None:
        if require_llm:
            raise RuntimeError("LLM client is required for hybrid UVM generation")
        output.llm_status = "skipped_no_client"
        output.llm_errors.append("No LLM client provided; scaffold files were kept")
        return output

    design = _get_design(model)
    top = target_module or design.get("top_module", "dut")
    ports = design.get("ports", [])
    context = _build_uvm_llm_context(model, top, ports)
    allowed_ports = {_port_name(p) for p in ports if _port_name(p)}
    candidate_paths = [
        Path(path)
        for path in output.all_files
        if str(path).lower().endswith((".sv", ".svh"))
    ]
    generated_symbols = _build_generated_symbol_registry(output, top)

    output.llm_called = True
    output.llm_status = "attempted"
    output.llm_candidate_files = [str(path) for path in candidate_paths]
    for path in candidate_paths:
        try:
            _emit_uvm_progress({
                "type": "llm_enhance_start",
                "file": str(path),
                "message": f"Enhancing {path.name} with the LLM",
            })
            scaffold = path.read_text(encoding="utf-8", errors="ignore")
            enhanced = await _enhance_uvm_file_with_llm(
                ai_client=ai_client,
                filename=path.name,
                scaffold=scaffold,
                context=context,
                allowed_ports=allowed_ports,
                generated_symbols=generated_symbols,
            )
            enhanced = _sanitize_systemverilog_text(enhanced)
            accepted, rejection_reason = _validate_llm_uvm_content(
                path.name,
                scaffold,
                enhanced,
                allowed_ports,
                generated_symbols,
            )
            if accepted:
                path.write_text(enhanced, encoding="utf-8")
                output.llm_enhanced_files.append(str(path))
                _emit_uvm_progress({
                    "type": "llm_enhance_done",
                    "file": str(path),
                    "message": f"Accepted LLM enhancement for {path.name}",
                })
            else:
                output.llm_errors.append(
                    f"{path.name}: {rejection_reason}; scaffold kept"
                )
                _emit_uvm_progress({
                    "type": "llm_enhance_rejected",
                    "file": str(path),
                    "message": f"Kept scaffold for {path.name}; {rejection_reason}",
                })
        except Exception as exc:
            logger.warning("LLM UVM enhancement failed for %s: %s", path.name, exc)
            output.llm_errors.append(f"{path.name}: {exc}")
            _emit_uvm_progress({
                "type": "llm_enhance_error",
                "file": str(path),
                "message": f"LLM enhancement failed for {path.name}: {exc}",
            })

    if require_llm and not output.llm_enhanced_files:
        raise RuntimeError(
            "LLM UVM generation was requested but no generated file passed validation"
        )

    if not candidate_paths:
        output.llm_status = "not_attempted_no_sv_files"
    elif len(output.llm_enhanced_files) == len(candidate_paths):
        output.llm_status = "accepted_all"
    elif output.llm_enhanced_files:
        output.llm_status = "partially_accepted"
    else:
        output.llm_status = "all_rejected_scaffold_kept"

    output.summary = (
        f"{output.summary}; LLM enhanced "
        f"{len(output.llm_enhanced_files)}/{len(candidate_paths)} SystemVerilog files"
    )
    _normalize_top_run_test(output.top_file)
    return output


def _obj_get(obj: Any, key: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _obj_list(obj: Any, key: str) -> List[Any]:
    value = _obj_get(obj, key, [])
    return value if isinstance(value, list) else []


def _compact_block_models_for_context(block_models: Any, limit: int = 24) -> Dict[str, Any]:
    """Expose sub-module facts to the planning LLM without flooding context."""
    if not isinstance(block_models, dict):
        return {}

    compact: Dict[str, Any] = {}
    for name, block in list(block_models.items())[:limit]:
        compact[str(name)] = {
            "top_module": _obj_get(block, "top_module", str(name)),
            "definition_kind": _obj_get(block, "definition_kind", "Module"),
            "file": _obj_get(block, "file", ""),
            "description": _obj_get(block, "description", ""),
            "ports": _to_jsonable(_obj_list(block, "ports")[:32]),
            "parameters": _to_jsonable(_obj_list(block, "parameters")[:16]),
            "instantiations": _to_jsonable(_obj_list(block, "instantiations")[:16]),
            "sub_instances": _to_jsonable(_obj_list(block, "sub_instances")[:16]),
            "protocols": _to_jsonable(_obj_list(block, "protocols")[:8]),
            "fsms": _to_jsonable(_obj_list(block, "fsms")[:8]),
            "register_fields": _to_jsonable(_obj_list(block, "register_fields")[:16]),
            "expected_behaviors": _to_jsonable(_obj_list(block, "expected_behaviors")[:8]),
            "transaction_flows": _to_jsonable(_obj_list(block, "transaction_flows")[:8]),
            "internal_symbols": _to_jsonable(_obj_list(block, "internal_symbols")[:64]),
            "modports": _to_jsonable(_obj_list(block, "modports")[:16]),
        }
    return compact


def _compact_evidence_for_context(items: Any, limit: int = 20) -> List[Any]:
    if not isinstance(items, list):
        return []
    return _to_jsonable(items[-limit:])


def _mental_model_uvm_plan_context(
    model: Any,
    *,
    content: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    design = _get_design(model)
    if not design and isinstance(content, dict):
        raw_design = content.get("design", {})
        design = raw_design if isinstance(raw_design, dict) else {}
    requirements = _get_requirements(model)
    if not requirements and isinstance(content, dict):
        raw_requirements = content.get("requirements", [])
        requirements = raw_requirements if isinstance(raw_requirements, list) else []

    ports = design.get("ports", []) if isinstance(design, dict) else []
    verification = _to_jsonable(
        getattr(model, "verification", {})
        if not isinstance(model, dict)
        else model.get("verification", {})
    )
    if not verification and isinstance(content, dict):
        verification = _to_jsonable(content.get("verification", {}))

    raw_block_models = (
        content.get("block_models", {})
        if isinstance(content, dict)
        else _obj_get(model, "block_models", {})
    )
    raw_symbol_table = (
        content.get("symbol_table", {})
        if isinstance(content, dict)
        else _obj_get(model, "symbol_table", {})
    )
    raw_evidence = (
        content.get("evidence", [])
        if isinstance(content, dict)
        else _obj_get(model, "evidence", [])
    )
    raw_knowledge_base = (
        content.get("knowledge_base", {})
        if isinstance(content, dict)
        else _obj_get(model, "knowledge_base", {})
    )
    living_agent = (
        content.get("living_agent", {})
        if isinstance(content, dict)
        else _obj_get(model, "living_agent", {})
    )
    living_memory = _obj_get(living_agent, "verification_memory", {}) if living_agent else {}

    return {
        "top_module": str(design.get("top_module") or "dut"),
        "parser_engine": (
            content.get("parser_engine", "")
            if isinstance(content, dict)
            else _obj_get(model, "parser_engine", "")
        ),
        "parser_version": (
            content.get("parser_version", "")
            if isinstance(content, dict)
            else _obj_get(model, "parser_version", "")
        ),
        "parser_diagnostics": _to_jsonable(
            content.get("parser_diagnostics", [])
            if isinstance(content, dict)
            else _obj_get(model, "parser_diagnostics", [])
        ),
        "description": str(design.get("description") or ""),
        "ports": [
            {
                "name": _port_name(port),
                "direction": _port_dir(port),
                "width": _port_width(port),
                "bus_range": _dict_get(port, "bus_range", ""),
                "description": _dict_get(port, "description", ""),
                "protocol_role": _dict_get(port, "protocol_role", ""),
            }
            for port in ports
            if _port_name(port)
        ],
        "parameters": _to_jsonable(design.get("parameters", [])),
        "modules": _to_jsonable(design.get("modules", [])),
        "clock_domains": _to_jsonable(design.get("clock_domains", [])),
        "protocols": _to_jsonable(design.get("protocols", [])),
        "fsms": _to_jsonable(design.get("fsms", [])),
        "has_cdc_crossings": bool(design.get("has_cdc_crossings")),
        "cdc_crossings": _to_jsonable(design.get("cdc_crossings", [])),
        "register_fields": _to_jsonable(design.get("register_fields", [])),
        "register_map": _to_jsonable(design.get("register_map", [])),
        "expected_behaviors": _to_jsonable(design.get("expected_behaviors", [])),
        "transaction_flows": _to_jsonable(design.get("transaction_flows", [])),
        "requirements": [
            {
                "id": _dict_get(req, "id", ""),
                "text": _dict_get(req, "text", ""),
                "priority": _dict_get(req, "priority", "medium"),
                "category": _dict_get(req, "category", "functional"),
            }
            for req in requirements
            if _dict_get(req, "id", "") or _dict_get(req, "text", "")
        ],
        "verification_intent": verification,
        "block_models": _compact_block_models_for_context(raw_block_models),
        "symbol_table": _to_jsonable(raw_symbol_table if isinstance(raw_symbol_table, dict) else {}),
        "knowledge_base": _to_jsonable(raw_knowledge_base if isinstance(raw_knowledge_base, dict) else {}),
        "evidence": _compact_evidence_for_context(raw_evidence),
        "living_agent_memory": _to_jsonable(living_memory),
        "risks": _to_jsonable(content.get("risks", []) if isinstance(content, dict) else getattr(model, "risks", [])),
        "open_questions": _to_jsonable(
            content.get("open_questions", [])
            if isinstance(content, dict)
            else getattr(model, "open_questions", [])
        ),
    }


async def _call_uvm_plan_ai_client(
    *,
    ai_client: Any,
    context: Dict[str, Any],
    fallback_plan: Dict[str, Any],
) -> str:
    fallback_plan_for_prompt = _compact_uvm_plan_for_llm(fallback_plan)
    system_prompt = (
        "You are a senior design verification architect writing a reviewable "
        "coverage-driven UVM verification plan. Create the plan only from the "
        "provided mental model, requirements, evidence, and rule-based baseline. "
        "Return valid JSON only."
    )
    user_prompt = f"""Create a mental-model-backed UVM verification plan.

MENTAL MODEL CONTEXT:
```json
{json.dumps(context, indent=2, default=str)[:18000]}
```

RULE-BASED BASELINE PLAN:
```json
{json.dumps(fallback_plan_for_prompt, indent=2, default=str)[:8000]}
```

Return JSON in this exact shape:
{{
  "top_module": "{context.get('top_module') or 'dut'}",
  "interfaces": [
    {{"name": "existing_port_name", "direction": "input|output|inout", "width": 1, "role": "clock|reset|driver|monitor|data|status"}}
  ],
  "agents": [
    {{"name": "snake_case_agent_name", "type": "active|passive", "ports": ["existing_port_name"], "description": "agent responsibility"}}
  ],
  "sequences": [
    {{"id": "UVM-SEQ-001", "name": "snake_case_sequence_name", "description": "what this sequence verifies", "requirement_ids": ["REQ-001"], "priority": "critical|high|medium|low"}}
  ],
  "scoreboard_checks": [
    {{"check": "snake_case_check_name", "description": "expected/reference behavior", "signals": ["existing_port_name"], "requirement_ids": ["REQ-001"]}}
  ],
  "coverage_points": [
    {{"point": "snake_case_coverage_point", "type": "functional|toggle|cross", "description": "coverage goal", "signal": "existing_port_name", "bins": ["meaningful bin"]}}
  ],
  "verification_objectives": [
    "reset/initialization objective",
    "nominal functional objective",
    "boundary/corner/stress objective"
  ],
  "test_case_matrix": [
    {{"category": "reset|nominal|boundary|protocol|error|stress", "intent": "what is proven", "planned_stimulus": "how stimulus is driven", "checker": "scoreboard/assertion expectation", "coverage": "coverage bin/cross goal", "requirement_ids": ["REQ-001"]}}
  ],
  "checking_strategy": ["how monitors, scoreboard, predictor, and assertions will prove correctness"],
  "coverage_strategy": ["functional coverage groups/crosses tied to requirements and protocol behavior"],
  "closure_criteria": ["compile clean", "planned checks pass", "coverage goals met or waived", "logs feed back into TruthCore"],
  "assumptions": ["grounded assumption"],
  "open_questions": ["question to ask user if behavior is ambiguous"],
  "files_preview": ["{context.get('top_module') or 'dut'}_pkg.sv", "{context.get('top_module') or 'dut'}_if.sv", "top_tb.sv"]
}}

Rules:
- Use only port names and requirement IDs from MENTAL MODEL CONTEXT.
- Do not invent protocol behavior that is absent from the mental model.
- Think like a senior DV engineer before emitting JSON: identify scope,
  requirements, test categories, expected checkers, coverage closure goals,
  risks/open questions, and sign-off criteria.
- Planned test cases must cover reset, nominal functionality, boundary/range,
  protocol ordering/backpressure when present, illegal/error behavior when
  specified, and constrained-random/stress regression.
- The plan is the baseline contract for downstream generators. Sequences,
  scoreboard checks, coverage points, assertions, generated files, and later
  log-repair patches must follow this plan.
- If the protocol is TileLink-UL/TL-UL, keep A-channel request signals together
  (a_valid, a_ready, a_opcode, a_param, a_size, a_source, a_address, a_mask,
  a_data) and D-channel response signals together (d_valid, d_ready, d_opcode,
  d_param, d_size, d_source, d_sink, d_data, d_error). Plan explicit stalled
  valid/ready payload-stability checks instead of treating it as generic APB,
  AXI, SPI, or arbitrary prefix grouping.
- Treat knowledge_base.plan_hints as generic DV guidance for scenarios, checks, and coverage, but keep every item grounded to real ports or explicit open questions.
- Convert expected_behaviors into scoreboard_checks with concrete signals and expected/reference intent.
- Use transaction_flows to choose legal sequence scenarios; use register_fields/register_map for CSR/register checks.
- Read evidence and living_agent_memory before planning. Do not repeat checks or file structures that previous compile/validation evidence marked as failed unless you explicitly fix the cause.
- If open_questions or evidence show unresolved ambiguity, move the item into open_questions instead of guessing.
- If behavior is ambiguous, add an open question instead of guessing.
- This is a plan only. Do not generate SystemVerilog/UVM code here.
- Keep files_preview as a list of file-name strings.
"""
    return await _call_uvm_ai_client(ai_client, system_prompt, user_prompt)


def _compact_uvm_plan_for_llm(plan: Dict[str, Any]) -> Dict[str, Any]:
    """Keep the baseline plan prompt focused on user-reviewable intent."""
    if not isinstance(plan, dict):
        return {}
    internal_keys = {
        "generation_policy",
        "traceability",
        "estimated_file_count",
    }
    return {key: value for key, value in plan.items() if key not in internal_keys}


def _extract_json_object(text: str) -> Dict[str, Any]:
    match = re.search(r"\{[\s\S]*\}", text or "")
    if not match:
        raise ValueError("AI response did not contain a JSON object")
    data = json.loads(match.group())
    if not isinstance(data, dict):
        raise ValueError("AI response JSON was not an object")
    return data


async def _repair_uvm_plan_json(
    *,
    ai_client: Any,
    previous_response: str,
    context: Dict[str, Any],
    fallback_plan: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    """Ask the LLM once to convert malformed UVM plan output into strict JSON."""
    system_prompt = (
        "You are a JSON repair assistant for a UVM verification planning agent. "
        "Return only one valid JSON object. No markdown. No prose."
    )
    top = context.get("top_module") or fallback_plan.get("top_module") or "dut"
    allowed_ports = [
        str(port.get("name") or "")
        for port in context.get("ports", [])
        if isinstance(port, dict) and port.get("name")
    ]
    allowed_requirements = [
        str(req.get("id") or "")
        for req in context.get("requirements", [])
        if isinstance(req, dict) and req.get("id")
    ]
    user_prompt = f"""The previous UVM plan response was not valid JSON.

PREVIOUS RESPONSE:
{str(previous_response or "")[:10000]}

Allowed top module: {top}
Allowed port names: {json.dumps(allowed_ports)}
Allowed requirement IDs: {json.dumps(allowed_requirements)}

If any item is ambiguous or invalid, omit it or move it into open_questions.
Return exactly one JSON object with this shape:
{{
  "top_module": "{top}",
  "interfaces": [],
  "agents": [],
  "sequences": [],
  "scoreboard_checks": [],
  "coverage_points": [],
  "verification_objectives": [],
  "test_case_matrix": [],
  "checking_strategy": [],
  "coverage_strategy": [],
  "closure_criteria": [],
  "assumptions": [],
  "open_questions": [],
  "files_preview": []
}}
"""
    try:
        response = await _call_uvm_ai_client(ai_client, system_prompt, user_prompt)
        return _extract_json_object(response)
    except Exception as exc:
        logger.warning("AI UVM plan JSON repair failed: %s", exc)
        return None


def _sanitize_uvm_plan_from_ai(
    plan_data: Dict[str, Any],
    context: Dict[str, Any],
    fallback_plan: Dict[str, Any],
) -> Dict[str, Any]:
    allowed_ports = {
        str(port.get("name") or "")
        for port in context.get("ports", [])
        if isinstance(port, dict) and port.get("name")
    }
    allowed_requirements = {
        str(req.get("id") or "")
        for req in context.get("requirements", [])
        if isinstance(req, dict) and req.get("id")
    }
    top_module = str(
        context.get("top_module")
        or plan_data.get("top_module")
        or fallback_plan.get("top_module")
        or "dut"
    )

    interfaces = _sanitize_uvm_interfaces(
        plan_data.get("interfaces"),
        context.get("ports", []),
    )
    agents = _sanitize_uvm_agents(
        plan_data.get("agents"),
        allowed_ports,
        fallback_plan.get("agents", []),
    )
    generation_policy = copy.deepcopy(fallback_plan.get("generation_policy") or {})
    if isinstance(generation_policy, dict) and not generation_policy.get("multi_agent"):
        agents = _single_agent_plan_agents(agents, fallback_plan.get("agents", []))
    if isinstance(generation_policy, dict) and agents:
        generation_policy["agent_names"] = [
            str(agent.get("name") or "")
            for agent in agents
            if isinstance(agent, dict) and agent.get("name")
        ]
    sequences = _sanitize_uvm_sequences(
        plan_data.get("sequences"),
        allowed_requirements,
        fallback_plan.get("sequences", []),
    )
    scoreboard_checks = _sanitize_uvm_named_items(
        plan_data.get("scoreboard_checks"),
        name_key="check",
        allowed_ports=allowed_ports,
        allowed_requirements=allowed_requirements,
        fallback_items=fallback_plan.get("scoreboard_checks", []),
    )
    coverage_points = _sanitize_uvm_named_items(
        plan_data.get("coverage_points"),
        name_key="point",
        allowed_ports=allowed_ports,
        allowed_requirements=allowed_requirements,
        fallback_items=fallback_plan.get("coverage_points", []),
    )
    verification_objectives = _sanitize_text_list(
        plan_data.get("verification_objectives"),
        fallback_plan.get("verification_objectives", []),
    )
    test_case_matrix = _sanitize_uvm_test_case_matrix(
        plan_data.get("test_case_matrix"),
        allowed_requirements,
        fallback_plan.get("test_case_matrix", []),
    )
    checking_strategy = _sanitize_text_list(
        plan_data.get("checking_strategy"),
        fallback_plan.get("checking_strategy", []),
    )
    coverage_strategy = _sanitize_text_list(
        plan_data.get("coverage_strategy"),
        fallback_plan.get("coverage_strategy", []),
    )
    closure_criteria = _sanitize_text_list(
        plan_data.get("closure_criteria"),
        fallback_plan.get("closure_criteria", []),
    )
    files_preview = _sanitize_file_preview(
        plan_data.get("files_preview"),
        fallback_plan.get("files_preview", []),
    )
    if isinstance(generation_policy, dict):
        policy_files = _policy_file_preview_from_agents(
            top_module=top_module,
            generation_policy=generation_policy,
            agents=agents,
            fallback_files=fallback_plan.get("files_preview", []),
        )
        if policy_files:
            files_preview = _merge_policy_and_ai_file_preview(
                top_module=top_module,
                policy_files=policy_files,
                ai_files=files_preview,
                generation_policy=generation_policy,
                agent_count=len(agents),
            )
    traceability = _uvm_plan_traceability(
        sequences,
        scoreboard_checks,
        coverage_points,
        allowed_requirements,
    )

    if not sequences:
        raise ValueError("AI UVM plan did not include any valid grounded sequences")

    return {
        "top_module": top_module,
        "interfaces": interfaces,
        "agents": agents,
        "sequences": sequences,
        "scoreboard_checks": scoreboard_checks,
        "coverage_points": coverage_points,
        "verification_objectives": verification_objectives,
        "test_case_matrix": test_case_matrix,
        "checking_strategy": checking_strategy,
        "coverage_strategy": coverage_strategy,
        "closure_criteria": closure_criteria,
        "assumptions": _sanitize_text_list(
            plan_data.get("assumptions"),
            fallback_plan.get("assumptions", []),
        ),
        "open_questions": _sanitize_text_list(
            plan_data.get("open_questions"),
            fallback_plan.get("open_questions", []),
        ),
        "files_preview": files_preview,
        "estimated_file_count": len(files_preview),
        "generation_policy": generation_policy if isinstance(generation_policy, dict) else {},
        "traceability": traceability,
    }


def _sanitize_uvm_test_case_matrix(
    raw_items: Any,
    allowed_requirements: set[str],
    fallback_items: Any,
) -> List[Dict[str, Any]]:
    if not isinstance(raw_items, list):
        raw_items = []
    result: List[Dict[str, Any]] = []
    for index, item in enumerate(raw_items[:16]):
        if not isinstance(item, dict):
            continue
        category = _safe_plan_name(str(item.get("category") or f"case_{index + 1}"))
        sanitized = {
            "category": category,
            "intent": str(item.get("intent") or item.get("description") or "").strip(),
            "planned_stimulus": str(item.get("planned_stimulus") or item.get("stimulus") or "").strip(),
            "checker": str(item.get("checker") or item.get("check") or "").strip(),
            "coverage": str(item.get("coverage") or "").strip(),
        }
        req_ids = _filter_allowed_strings(item.get("requirement_ids", []), allowed_requirements)
        if req_ids:
            sanitized["requirement_ids"] = req_ids
        result.append({k: v for k, v in sanitized.items() if v})
    if result:
        return result
    return copy.deepcopy(fallback_items or [])


def _single_agent_plan_agents(
    ai_agents: List[Dict[str, Any]],
    fallback_agents: Any,
) -> List[Dict[str, Any]]:
    """Keep one grounded AI agent for single-agent plans instead of discarding it."""
    fallback = copy.deepcopy(fallback_agents or [])
    if not ai_agents:
        return fallback

    chosen = copy.deepcopy(ai_agents[0])
    if fallback and isinstance(fallback[0], dict):
        chosen.setdefault("name", fallback[0].get("name", "dut_agent"))
        chosen.setdefault("type", fallback[0].get("type", "active"))
        if not chosen.get("ports"):
            chosen["ports"] = list(fallback[0].get("ports") or [])
    chosen["single_agent_policy"] = True
    return [chosen]


def _merge_policy_and_ai_file_preview(
    *,
    top_module: str,
    policy_files: List[str],
    ai_files: List[str],
    generation_policy: Dict[str, Any],
    agent_count: int,
) -> List[str]:
    """Allow AI file-preview additions only when the generator can emit that role."""
    merged = _sanitize_file_preview(policy_files, policy_files)
    optional_roles = set(generation_policy.get("optional_files") or [])
    multi_agent = bool(generation_policy.get("multi_agent")) or agent_count > 1

    for raw in ai_files or []:
        name = Path(str(raw)).name
        if not name or name in merged:
            continue
        role = _uvm_preview_file_role(top_module, name)
        if role in {"mandatory", "makefile"}:
            merged.append(name)
        elif role in optional_roles:
            merged.append(name)
        elif multi_agent and role in {"agent_bundle", "env_config", "virtual_sequencer", "virtual_sequence"}:
            merged.append(name)
    return merged


def _policy_file_preview_from_agents(
    *,
    top_module: str,
    generation_policy: Dict[str, Any],
    agents: List[Dict[str, Any]],
    fallback_files: List[str],
) -> List[str]:
    """Build the preview from the same agent names the generator will use."""
    top = _safe_sv_name(top_module or "dut")
    optional_roles = set(generation_policy.get("optional_files") or [])
    multi_agent = bool(generation_policy.get("multi_agent")) or len(agents) > 1
    if not multi_agent:
        return _sanitize_file_preview(fallback_files, fallback_files)

    used: set[str] = set()
    bases: List[str] = []
    for index, agent in enumerate(agents or []):
        raw_name = ""
        if isinstance(agent, dict):
            raw_name = str(agent.get("name") or f"uvm_agent_{index + 1}")
        base = _safe_plan_name(raw_name)
        if base.endswith("_agent"):
            base = base[:-6]
        if not base:
            base = f"uvm_{index + 1}"
        original = base
        suffix = 2
        while base in used:
            base = f"{original}_{suffix}"
            suffix += 1
        used.add(base)
        bases.append(base)

    files: List[str] = []
    for base in bases:
        files.extend([
            f"{base}_intf.sv",
            f"{base}_sequence_item.sv",
            f"{base}_sequencer.sv",
            f"{base}_sequence.sv",
            f"{base}_driver.sv",
            f"{base}_monitor.sv",
            f"{base}_agent.sv",
        ])
    files.extend([
        f"{top}_pkg.sv",
        f"{top}_scoreboard.sv",
        f"{top}_coverage.sv",
        f"{top}_env_config.sv",
        f"{top}_virtual_sequencer.sv",
        f"{top}_virtual_sequence.sv",
        f"{top}_env.sv",
        f"{top}_tests.sv",
        "top_tb.sv",
        f"{top}_uvm.f",
    ])
    if "makefile" in optional_roles:
        files.append("Makefile")
    if "assertions" in optional_roles:
        files.append(f"{top}_assertions.sv")
    if "ral" in optional_roles:
        files.append(f"{top}_ral.sv")
    if "test_plan_doc" in optional_roles:
        files.append("test_plan.md")
    return list(dict.fromkeys(_sanitize_file_preview(files, fallback_files)))


def _uvm_preview_file_role(top_module: str, filename: str) -> str:
    top = _safe_sv_name(top_module or "dut")
    name = Path(filename).name
    lower = name.lower()
    if lower == "makefile":
        return "makefile"
    if lower == f"{top.lower()}_assertions.sv":
        return "assertions"
    if lower == f"{top.lower()}_ral.sv":
        return "ral"
    if lower == "test_plan.md":
        return "test_plan_doc"
    if lower == f"{top.lower()}_env_config.sv":
        return "env_config"
    if lower == f"{top.lower()}_virtual_sequencer.sv":
        return "virtual_sequencer"
    if lower == f"{top.lower()}_virtual_sequence.sv":
        return "virtual_sequence"

    mandatory = {
        f"{top.lower()}_pkg.sv",
        f"{top.lower()}_if.sv",
        f"{top.lower()}_seq_item.sv",
        f"{top.lower()}_driver.sv",
        f"{top.lower()}_monitor.sv",
        f"{top.lower()}_sequencer.sv",
        f"{top.lower()}_agent.sv",
        f"{top.lower()}_scoreboard.sv",
        f"{top.lower()}_env.sv",
        f"{top.lower()}_coverage.sv",
        f"{top.lower()}_seq_lib.sv",
        f"{top.lower()}_tests.sv",
        "top_tb.sv",
        f"{top.lower()}_uvm.f",
    }
    if lower in mandatory:
        return "mandatory"
    if re.search(r"_(intf|sequence_item|sequencer|sequence|driver|monitor|agent)\.sv$", lower):
        return "agent_bundle"
    return "unsupported"


def _uvm_plan_traceability(
    sequences: List[Dict[str, Any]],
    scoreboard_checks: List[Dict[str, Any]],
    coverage_points: List[Dict[str, Any]],
    allowed_requirements: set[str],
) -> Dict[str, Any]:
    linked: set[str] = set()
    linked_sequences = 0
    linked_scoreboard = 0
    linked_coverage = 0
    for bucket_name, items in (
        ("sequences", sequences),
        ("scoreboard", scoreboard_checks),
        ("coverage", coverage_points),
    ):
        for item in items:
            if not isinstance(item, dict):
                continue
            req_ids = [
                str(req_id)
                for req_id in (item.get("requirement_ids") or [])
                if str(req_id) in allowed_requirements
            ]
            if not req_ids:
                continue
            linked.update(req_ids)
            if bucket_name == "sequences":
                linked_sequences += 1
            elif bucket_name == "scoreboard":
                linked_scoreboard += 1
            else:
                linked_coverage += 1
    total = len(allowed_requirements)
    return {
        "requirements_total": total,
        "requirements_linked": len(linked),
        "requirements_linked_percent": round((len(linked) / total) * 100, 2) if total else 0.0,
        "linked_sequences": linked_sequences,
        "linked_scoreboard_checks": linked_scoreboard,
        "linked_coverage_points": linked_coverage,
    }


def _sanitize_uvm_interfaces(raw_items: Any, fallback_ports: Any) -> List[Dict[str, Any]]:
    if not isinstance(raw_items, list):
        raw_items = []
    allowed_names = {_port_name(port) for port in fallback_ports if _port_name(port)}
    result: List[Dict[str, Any]] = []
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        name = _dict_get(item, "name", "").strip()
        if not name or (allowed_names and name not in allowed_names):
            continue
        result.append(
            {
                "name": name,
                "direction": _dict_get(item, "direction", "unknown"),
                "width": _safe_int(_dict_get(item, "width", "1"), default=1),
                "role": _dict_get(item, "role", ""),
            }
        )
    if result:
        return result
    return [
        {
            "name": _port_name(port),
            "direction": _port_dir(port),
            "width": _port_width(port),
            "role": _port_role(port),
        }
        for port in fallback_ports
        if _port_name(port)
    ]


def _sanitize_uvm_agents(
    raw_items: Any,
    allowed_ports: set[str],
    fallback_items: Any,
) -> List[Dict[str, Any]]:
    if not isinstance(raw_items, list):
        raw_items = []
    result: List[Dict[str, Any]] = []
    for index, item in enumerate(raw_items):
        if not isinstance(item, dict):
            continue
        ports = _filter_allowed_strings(item.get("ports", []), allowed_ports)
        agent_type = str(item.get("type") or "active").lower()
        if agent_type not in {"active", "passive"}:
            agent_type = "active"
        result.append(
            {
                "name": _safe_plan_name(str(item.get("name") or f"uvm_agent_{index + 1}")),
                "type": agent_type,
                "ports": ports,
                "description": str(item.get("description") or ""),
            }
        )
    return result or copy.deepcopy(fallback_items or [])


def _sanitize_uvm_sequences(
    raw_items: Any,
    allowed_requirements: set[str],
    fallback_items: Any,
) -> List[Dict[str, Any]]:
    if not isinstance(raw_items, list):
        raw_items = []
    result: List[Dict[str, Any]] = []
    for index, item in enumerate(raw_items):
        if not isinstance(item, dict):
            continue
        req_ids = _filter_allowed_strings(item.get("requirement_ids", []), allowed_requirements)
        result.append(
            {
                "id": str(item.get("id") or f"UVM-SEQ-{index + 1:03d}"),
                "name": _safe_plan_name(str(item.get("name") or f"uvm_sequence_{index + 1}")),
                "description": str(item.get("description") or ""),
                "requirement_ids": req_ids,
                "priority": _safe_priority(item.get("priority")),
            }
        )
    return result or copy.deepcopy(fallback_items or [])


def _sanitize_uvm_named_items(
    raw_items: Any,
    *,
    name_key: str,
    allowed_ports: set[str],
    allowed_requirements: set[str],
    fallback_items: Any,
) -> List[Dict[str, Any]]:
    if not isinstance(raw_items, list):
        raw_items = []
    result: List[Dict[str, Any]] = []
    for index, item in enumerate(raw_items):
        if not isinstance(item, dict):
            continue
        name = _safe_plan_name(str(item.get(name_key) or f"{name_key}_{index + 1}"))
        sanitized = {
            name_key: name,
            "description": str(item.get("description") or ""),
        }
        signals = _filter_allowed_strings(item.get("signals", []), allowed_ports)
        signal = str(item.get("signal") or "")
        if signal and signal in allowed_ports:
            sanitized["signal"] = signal
            if signal not in signals:
                signals.append(signal)
        if signals:
            sanitized["signals"] = signals
        req_ids = _filter_allowed_strings(item.get("requirement_ids", []), allowed_requirements)
        if req_ids:
            sanitized["requirement_ids"] = req_ids
        if name_key == "point":
            point_type = str(item.get("type") or "functional").lower()
            if point_type not in {"functional", "toggle", "cross"}:
                point_type = "functional"
            sanitized["type"] = point_type
            bins = _sanitize_text_list(item.get("bins"), [])
            if bins:
                sanitized["bins"] = bins
        result.append(sanitized)
    return result or copy.deepcopy(fallback_items or [])


def _sanitize_file_preview(raw_items: Any, fallback_items: Any) -> List[str]:
    values = _sanitize_text_list(raw_items, [])
    cleaned = []
    for value in values:
        name = Path(value).name
        if name and name not in cleaned:
            cleaned.append(name)
    if cleaned:
        return cleaned
    return [str(item) for item in (fallback_items or []) if str(item)]


def _sanitize_text_list(raw_items: Any, fallback_items: Any) -> List[str]:
    if not isinstance(raw_items, list):
        return [str(item) for item in (fallback_items or []) if str(item)]
    result = []
    for item in raw_items:
        if isinstance(item, dict):
            text = str(item.get("text") or item.get("question") or item.get("description") or item.get("path") or "")
        else:
            text = str(item or "")
        text = text.strip()
        if text:
            result.append(text[:500])
    return result


def _filter_allowed_strings(raw_items: Any, allowed: set[str]) -> List[str]:
    if not isinstance(raw_items, list):
        raw_items = [raw_items] if raw_items else []
    result = []
    for item in raw_items:
        value = str(item or "")
        if value and value in allowed and value not in result:
            result.append(value)
    return result


def _safe_plan_name(value: str) -> str:
    name = re.sub(r"[^a-zA-Z0-9_]", "_", value or "").strip("_").lower()
    if not name:
        return ""
    if name[0].isdigit():
        name = f"uvm_{name}"
    return name[:96]


def _safe_priority(value: Any) -> str:
    priority = str(value or "medium").lower()
    return priority if priority in {"critical", "high", "medium", "low"} else "medium"


def _safe_int(value: Any, *, default: int = 1) -> int:
    try:
        return max(1, int(value))
    except (TypeError, ValueError):
        return default


def _port_role(port: Any) -> str:
    name = _port_name(port).lower()
    if "clk" in name or "clock" in name:
        return "clock"
    if "rst" in name or "reset" in name:
        return "reset"
    if _port_dir(port) == "input":
        return "driver"
    if _port_dir(port) == "output":
        return "monitor"
    return ""


def _to_jsonable(value: Any) -> Any:
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, dict):
        return {str(key): _to_jsonable(val) for key, val in value.items()}
    if isinstance(value, (list, tuple)):
        return [_to_jsonable(item) for item in value]
    if hasattr(value, "__dict__"):
        return {
            str(key): _to_jsonable(val)
            for key, val in vars(value).items()
            if not str(key).startswith("_")
        }
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _build_uvm_llm_context(model: Any, top: str, ports: list) -> str:
    design = _get_design(model)
    requirements = _get_requirements(model)
    scenarios = _get_uvm_scenarios(model)
    scoreboard_checks = _get_verification_list(model, "uvm_scoreboard_checks")
    coverage_points = _get_verification_list(model, "uvm_coverage_points")
    verification = _obj_get(model, "verification", {}) or {}
    approved_plan_markdown = (
        _obj_get(verification, "approved_uvm_plan_markdown", "")
        or _obj_get(verification, "uvm_plan_markdown", "")
    )
    llm_item_limit = 20
    truncations: list[str] = []

    def _limit_for_llm(label: str, items: list) -> list:
        values = list(items or [])
        if len(values) > llm_item_limit:
            truncations.append(f"{label} {len(values)}->{llm_item_limit}")
        return values[:llm_item_limit]

    port_lines = [
        f"- {_port_dir(p) or 'unknown'} {_port_bus_str(p).strip()} {_port_name(p)}".strip()
        for p in ports
    ]
    summary = {
        "top_module": top,
        "parser_engine": _obj_get(model, "parser_engine", ""),
        "parser_version": _obj_get(model, "parser_version", ""),
        "parser_diagnostics": _to_jsonable(_obj_get(model, "parser_diagnostics", []) or []),
        "description": design.get("description", ""),
        "parameters": design.get("parameters", []),
        "clock_domains": design.get("clock_domains", []),
        "protocols": design.get("protocols", []),
        "fsms": design.get("fsms", []),
        "register_fields": design.get("register_fields", []),
        "register_map": design.get("register_map", []),
        "expected_behaviors": design.get("expected_behaviors", []),
        "transaction_flows": design.get("transaction_flows", []),
        "requirements": _limit_for_llm("requirements", requirements),
        "approved_uvm_scenarios": _limit_for_llm("approved_uvm_scenarios", scenarios),
        "approved_scoreboard_checks": _limit_for_llm("approved_scoreboard_checks", scoreboard_checks),
        "approved_coverage_points": _limit_for_llm("approved_coverage_points", coverage_points),
        "approved_uvm_plan_markdown": str(approved_plan_markdown or "")[:6000],
        "block_models": _compact_block_models_for_context(_obj_get(model, "block_models", {})),
        "symbol_table": _to_jsonable(_obj_get(model, "symbol_table", {}) or {}),
        "knowledge_base": _to_jsonable(_obj_get(model, "knowledge_base", {}) or {}),
        "evidence": _compact_evidence_for_context(_obj_get(model, "evidence", [])),
        "open_questions": _to_jsonable(_obj_get(model, "open_questions", []) or []),
        "living_agent_memory": _to_jsonable(
            _obj_get(_obj_get(model, "living_agent", {}) or {}, "verification_memory", {})
        ),
    }
    if truncations:
        logger.warning("UVM LLM context truncated: %s", ", ".join(truncations))
    return (
        "MENTAL MODEL SUMMARY:\n"
        + json.dumps(summary, indent=2, default=str)[:12000]
        + "\n\nGROUND-TRUTH DUT PORTS:\n"
        + "\n".join(port_lines)
    )


async def _enhance_uvm_file_with_llm(
    *,
    ai_client: Any,
    filename: str,
    scaffold: str,
    context: str,
    allowed_ports: set[str],
    generated_symbols: _UVMGeneratedSymbolRegistry | None = None,
) -> str:
    symbol_contract = (
        generated_symbols.prompt_summary()
        if generated_symbols
        else "Generated UVM package/type contract: preserve scaffold names exactly."
    )
    system_prompt = (
        "You are a senior UVM verification engineer. Improve the provided "
        "SystemVerilog UVM file using the approved mental model and UVM plan. "
        "Preserve the exact package/module/interface/class names already present. "
        "Use only generated package and class names from the supplied contract. "
        "Only reference DUT signals from the ground-truth port list. If a behavior "
        "is unclear, use conservative known-value checks instead of inventing signals. "
        "Return only the complete SystemVerilog code for this file."
    )
    user_prompt = f"""FILE TO IMPROVE: {filename}

{context}

ALLOWED DUT PORT NAMES:
{', '.join(sorted(allowed_ports)) or '(none)'}

{symbol_contract}

CURRENT SCAFFOLD:
```systemverilog
{scaffold[:14000]}
```

Improve this file so it better reflects the mental model and approved UVM plan.
If MENTAL MODEL SUMMARY contains approved_uvm_plan_markdown, treat that Markdown
as the latest user-approved/editable vPlan baseline.
Do not import packages that are not listed in the generated contract.
Do not declare handles of generated-looking classes unless they are listed in the contract.
Keep it compilable UVM/SystemVerilog and return only this file's complete code.
"""
    response = await _call_uvm_ai_client(ai_client, system_prompt, user_prompt)
    return _extract_generated_code(response)


async def _call_uvm_ai_client(
    ai_client: Any,
    system_prompt: str,
    user_prompt: str,
) -> str:
    if hasattr(ai_client, "chat"):
        result = ai_client.chat(system_prompt, user_prompt)
    elif hasattr(ai_client, "generate_with_context"):
        result = ai_client.generate_with_context(
            system_prompt=system_prompt,
            context_blocks={"UVM GENERATION REQUEST": user_prompt},
            focus_instruction="Generate grounded UVM SystemVerilog",
            task_instruction="Improve the provided UVM scaffold",
        )
    elif hasattr(ai_client, "generate"):
        result = ai_client.generate(
            user_prompt,
            system_prompt=system_prompt,
            temperature=0.2,
        )
    else:
        raise RuntimeError("AI client does not provide chat/generate methods")

    if inspect.isawaitable(result):
        result = await result
    return str(result)


def _extract_generated_code(response: str) -> str:
    text = response or ""
    match = re.search(
        r"```(?:systemverilog|verilog|sv)?\s*\n([\s\S]*?)```",
        text,
        re.IGNORECASE,
    )
    if match:
        return _sanitize_systemverilog_text(match.group(1))
    generic = re.search(r"```\w*\s*\n([\s\S]*?)```", text)
    if generic:
        return _sanitize_systemverilog_text(generic.group(1))
    return _sanitize_systemverilog_text(text)


def _sanitize_systemverilog_text(content: str) -> str:
    """Strip markdown fences and normalize generated SV before validation/write."""
    lines = []
    for line in (content or "").strip().splitlines():
        if line.strip().startswith("```"):
            continue
        lines.append(line.rstrip())
    text = "\n".join(lines).strip()
    return f"{text}\n" if text else ""


def _accept_llm_uvm_content(
    filename: str,
    scaffold: str,
    content: str,
    allowed_ports: set[str],
    generated_symbols: _UVMGeneratedSymbolRegistry | None = None,
) -> bool:
    accepted, _reason = _validate_llm_uvm_content(
        filename,
        scaffold,
        content,
        allowed_ports,
        generated_symbols,
    )
    return accepted


def _validate_llm_uvm_content(
    filename: str,
    scaffold: str,
    content: str,
    allowed_ports: set[str],
    generated_symbols: _UVMGeneratedSymbolRegistry | None = None,
) -> tuple[bool, str]:
    content = _sanitize_systemverilog_text(content)
    if not content or len(content.splitlines()) < 5:
        return False, "LLM output is empty or too short"
    if "```" in content:
        return False, "LLM output still contains markdown fences"
    if not _double_quotes_are_balanced(content):
        return False, "LLM output has unbalanced double quotes"

    expected_names = _sv_declaration_names(scaffold)
    for kind, name in expected_names:
        if not re.search(rf"\b{kind}\s+{re.escape(name)}\b", content):
            return False, f"missing expected {kind} declaration {name}"
        if not _has_matching_sv_terminator(content, kind, name):
            return False, f"missing terminator for {kind} {name}"

    if not _local_includes_match_scaffold(scaffold, content):
        return False, "required scaffold includes were removed"

    package_errors = _unknown_package_imports(content, generated_symbols)
    if package_errors:
        return False, f"unknown package import {package_errors[0]}"

    type_errors = _unknown_generated_type_references(content, generated_symbols)
    if type_errors:
        return False, f"unknown generated type {type_errors[0]}"

    if re.search(r"\bmon\.ap\b", content):
        return False, "monitor analysis port is referenced through mon.ap"

    if filename.endswith((".sv", ".svh")) and not _vif_references_are_grounded(
        content,
        allowed_ports,
    ):
        return False, "vif references are not grounded to DUT ports"

    return True, "accepted"


def _build_generated_symbol_registry(output: UVMOutput, top: str) -> _UVMGeneratedSymbolRegistry:
    registry = _UVMGeneratedSymbolRegistry(
        allowed_packages={"uvm_pkg", f"{top}_pkg"},
        generated_types=set(),
    )
    for raw_path in output.all_files:
        path = Path(raw_path)
        if path.suffix.lower() not in {".sv", ".svh"} or not path.exists():
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for kind, name in _sv_declaration_names(text):
            if kind == "package":
                registry.allowed_packages.add(name)
            else:
                registry.generated_types.add(name)
    return registry


def _strip_sv_comments_and_strings(content: str) -> str:
    text = re.sub(r"/\*[\s\S]*?\*/", " ", content or "")
    text = re.sub(r"//.*", " ", text)
    text = re.sub(r'"(?:\\.|[^"\\])*"', '""', text)
    return text


def _unknown_package_imports(
    content: str,
    generated_symbols: _UVMGeneratedSymbolRegistry | None,
) -> list[str]:
    if not generated_symbols:
        return []
    allowed = set(generated_symbols.allowed_packages or set())
    stripped = _strip_sv_comments_and_strings(content)
    unknown: list[str] = []
    for package in re.findall(r"\bimport\s+([a-zA-Z_]\w*)::(?:\*|[a-zA-Z_]\w*)\s*;", stripped):
        if package not in allowed and package not in unknown:
            unknown.append(package)
    return unknown


_GENERATED_TYPE_SUFFIXES = (
    "_agent_config",
    "_env_config",
    "_virtual_sequencer",
    "_sequence_item",
    "_sequencer",
    "_scoreboard",
    "_coverage",
    "_base_vseq",
    "_base_test",
    "_sequence",
    "_driver",
    "_monitor",
    "_agent",
    "_env",
)


def _unknown_generated_type_references(
    content: str,
    generated_symbols: _UVMGeneratedSymbolRegistry | None,
) -> list[str]:
    if not generated_symbols:
        return []
    allowed = set(generated_symbols.generated_types or set())
    stripped = _strip_sv_comments_and_strings(content)
    candidates: list[str] = []
    type_context_patterns = [
        r"\bclass\s+([a-zA-Z_]\w*)\b",
        r"\bextends\s+([a-zA-Z_]\w*)\b",
        r"#\s*\(\s*([a-zA-Z_]\w*)\s*\)",
        r"\b([a-zA-Z_]\w*)::(?:type_id|create|get_type)\b",
        r"(?m)^\s*(?:(?:automatic|static|rand|local|protected|virtual)\s+)*([a-zA-Z_]\w*)\s+[a-zA-Z_]\w*\s*(?:\[[^\]]*\]\s*)?(?:;|=|,)",
    ]
    for pattern_text in type_context_patterns:
        for token in re.findall(pattern_text, stripped):
            if token not in candidates:
                candidates.append(token)
    unknown: list[str] = []
    for token in candidates:
        if token.startswith("uvm_"):
            continue
        if not token.endswith(_GENERATED_TYPE_SUFFIXES):
            continue
        if token not in allowed and token not in unknown:
            unknown.append(token)
    return unknown


def _double_quotes_are_balanced(content: str) -> bool:
    return len(re.findall(r'(?<!\\)"', content or "")) % 2 == 0


def _has_matching_sv_terminator(content: str, kind: str, name: str) -> bool:
    end_by_kind = {
        "class": "endclass",
        "module": "endmodule",
        "interface": "endinterface",
        "package": "endpackage",
    }
    terminator = end_by_kind.get(kind)
    if not terminator:
        return True
    return bool(re.search(rf"\b{terminator}\b(?:\s*:\s*{re.escape(name)})?", content))


def _local_includes_match_scaffold(scaffold: str, content: str) -> bool:
    def _includes(text: str) -> set[str]:
        return {
            inc
            for inc in re.findall(r'`include\s+"([^"]+)"', text or "")
            if Path(inc).name != "uvm_macros.svh"
        }

    expected = _includes(scaffold)
    actual = _includes(content)
    return expected.issubset(actual)


def _sv_declaration_names(content: str) -> list[tuple[str, str]]:
    pattern = re.compile(r"\b(class|module|interface|package)\s+([a-zA-Z_]\w*)")
    declarations: list[tuple[str, str]] = []
    for match in pattern.finditer(content):
        prefix = content[max(0, match.start() - 24):match.start()].lower()
        if re.search(r"\btypedef\s+$", prefix):
            continue
        declarations.append((match.group(1), match.group(2)))
    return declarations


def _vif_references_are_grounded(content: str, allowed_ports: set[str]) -> bool:
    if not allowed_ports:
        return True
    direct_refs = re.findall(r"\bvif\.([a-zA-Z_]\w*)\b", content)
    clocking_refs = re.findall(
        r"\bvif\.(?:driver_cb|monitor_cb)\.([a-zA-Z_]\w*)\b",
        content,
    )
    allowed_vif_fields = allowed_ports | {"driver_cb", "monitor_cb"}
    return all(ref in allowed_vif_fields for ref in direct_refs) and all(
        ref in allowed_ports for ref in clocking_refs
    )


# ─── Template Generators ─────────────────────────────────────────────

def _resolve_uvm_agent_specs(
    *,
    model: Any,
    ports: List[Any],
    protocols: List[Any],
    clk: str,
    rst: str,
) -> List[UVMAgentSpec]:
    port_by_name = {_port_name(port): port for port in ports if _port_name(port)}
    raw_candidates: List[Dict[str, Any]] = []

    for item in _get_uvm_agents(model):
        item_dict = _to_jsonable(item)
        if isinstance(item_dict, dict):
            raw_candidates.append({
                "name": item_dict.get("name") or f"uvm_agent_{len(raw_candidates) + 1}",
                "type": item_dict.get("type") or "active",
                "ports": item_dict.get("ports") or item_dict.get("port_group") or [],
                "description": item_dict.get("description") or "",
                "protocols": [],
            })

    if not raw_candidates:
        for protocol in protocols or []:
            proto = _to_jsonable(protocol)
            if not isinstance(proto, dict):
                continue
            proto_name = proto.get("protocol") or proto.get("name") or proto.get("interface")
            if not proto_name:
                proto_name = f"protocol_{len(raw_candidates) + 1}"
            role = str(proto.get("role") or "").lower()
            raw_candidates.append({
                "name": f"{proto_name}_agent",
                "type": "passive" if role in {"monitor", "passive"} else "active",
                "ports": proto.get("port_group") or proto.get("ports") or [],
                "description": proto.get("description") or "",
                "protocols": [proto],
            })

    if not raw_candidates:
        raw_candidates = _infer_prefix_agent_candidates(ports, clk, rst)

    specs: List[UVMAgentSpec] = []
    used_names: set[str] = set()
    used_ports: set[str] = set()
    for index, raw in enumerate(raw_candidates):
        port_names = [
            str(name)
            for name in (raw.get("ports") or [])
            if str(name) in port_by_name and str(name) not in {clk, rst}
        ]
        owned_ports = []
        for name in port_names:
            if name in used_ports:
                continue
            owned_ports.append(port_by_name[name])
            used_ports.add(name)
        if not owned_ports:
            continue

        base, agent_name = _agent_base_and_class_name(
            str(raw.get("name") or f"uvm_agent_{index + 1}"),
            used_names,
        )
        agent_type = str(raw.get("type") or "active").lower()
        if agent_type not in {"active", "passive"}:
            agent_type = "active"
        if any(_port_dir(port) in {"input", "inout"} for port in owned_ports):
            agent_type = "active"
        owned_names = {_port_name(port) for port in owned_ports if _port_name(port)}
        raw_protocols = list(raw.get("protocols") or [])
        if not raw_protocols:
            for protocol in protocols or []:
                proto = _to_jsonable(protocol)
                if not isinstance(proto, dict):
                    continue
                proto_ports = {
                    str(name)
                    for name in (proto.get("port_group") or proto.get("ports") or [])
                }
                if proto_ports and proto_ports & owned_names:
                    raw_protocols.append(proto)
        specs.append(UVMAgentSpec(
            name=agent_name,
            base=base,
            agent_type=agent_type,
            ports=owned_ports,
            description=str(raw.get("description") or ""),
            protocols=raw_protocols,
        ))

    unassigned = [
        port for port in ports
        if _port_name(port)
        and _port_name(port) not in {clk, rst}
        and _port_name(port) not in used_ports
    ]
    if specs and unassigned:
        if len(unassigned) >= 8:
            base, agent_name = _agent_base_and_class_name("misc_agent", used_names)
            specs.append(UVMAgentSpec(
                name=agent_name,
                base=base,
                agent_type="active" if any(_port_dir(p) == "input" for p in unassigned) else "passive",
                ports=unassigned,
                description="Covers DUT ports not assigned to a named interface group",
            ))
        else:
            target = next((spec for spec in specs if spec.is_active), specs[0])
            target.ports.extend(unassigned)
            suffix = ", plus low-count DUT sideband signals"
            if suffix not in target.description:
                target.description = f"{target.description}{suffix}" if target.description else suffix.strip(", ")

    if not specs:
        base, agent_name = _agent_base_and_class_name("dut_agent", set())
        specs.append(UVMAgentSpec(
            name=agent_name,
            base=base,
            agent_type="active",
            ports=[
                port for port in ports
                if _port_name(port) and _port_name(port) not in {clk, rst}
            ],
            description="Single generic DUT interface agent",
        ))

    return specs


def _infer_prefix_agent_candidates(ports: List[Any], clk: str, rst: str) -> List[Dict[str, Any]]:
    groups: Dict[str, List[str]] = {}
    for port in ports:
        name = _port_name(port)
        if not name or name in {clk, rst}:
            continue
        prefix = _infer_interface_prefix(name)
        if prefix:
            groups.setdefault(prefix, []).append(name)

    eligible = {prefix: names for prefix, names in groups.items() if len(names) >= 3}
    if len(eligible) < 2:
        return []

    return [
        {
            "name": f"{prefix}_agent",
            "type": "active",
            "ports": names,
            "description": f"Auto-inferred interface group for '{prefix}' signals",
        }
        for prefix, names in sorted(eligible.items())
    ]


def _infer_interface_prefix(name: str) -> str:
    snake = re.match(r"^([a-zA-Z][a-zA-Z0-9]*)_", name)
    if snake and len(snake.group(1)) >= 2:
        return _safe_plan_name(snake.group(1))
    camel = re.match(r"^([A-Z]+)(?=[A-Z][a-z]|[a-z])", name)
    if camel:
        return _safe_plan_name(camel.group(1).lower())
    first = re.match(r"^([A-Z])", name)
    if first:
        return _safe_plan_name(first.group(1).lower())
    return ""


def _agent_base_and_class_name(raw_name: str, used_names: set[str]) -> tuple[str, str]:
    base = _safe_plan_name(raw_name)
    if base.endswith("_agent"):
        base = base[:-6]
    if not base:
        base = "uvm"
    original = base
    suffix = 2
    while base in used_names:
        base = f"{original}_{suffix}"
        suffix += 1
    used_names.add(base)
    return base, f"{base}_agent"


def _generate_multi_agent_uvm(
    *,
    top: str,
    work: Path,
    output: UVMOutput,
    ports: List[Any],
    parameters: List[Any],
    fsms: List[Any],
    clk_name: str,
    rst_name: str,
    rst_active_low: bool,
    agent_specs: List[UVMAgentSpec],
    uvm_scenarios: List[Any],
    uvm_scoreboard_checks: List[Any],
    uvm_coverage_points: List[Any],
    uvm_assertions: List[Any],
    reference_data: Dict[str, Any],
    content_limits: Any = None,
    dut_rtl_files: List[str] | None = None,
) -> UVMOutput:
    include_files: List[str] = []

    for spec in agent_specs:
        intf_file = _write(work, f"{spec.base}_intf.sv", _gen_agent_interface(spec, clk_name, rst_name))
        output.interface_files.append(intf_file)
        bundle_files = [
            _write(work, f"{spec.base}_sequence_item.sv", _gen_agent_seq_item(spec)),
            _write(work, f"{spec.base}_sequencer.sv", _gen_agent_sequencer(spec)),
            _write(work, f"{spec.base}_sequence.sv", _gen_agent_sequence(spec)),
            _write(work, f"{spec.base}_driver.sv", _gen_agent_driver(spec)),
            _write(work, f"{spec.base}_monitor.sv", _gen_agent_monitor(spec)),
            _write(work, f"{spec.base}_agent.sv", _gen_protocol_agent(spec)),
        ]
        output.agent_files.extend(bundle_files)
        output.sequence_files.append(bundle_files[2])
        include_files.extend(Path(path).name for path in bundle_files)

    output.interface_file = output.interface_files[0] if output.interface_files else ""
    output.seq_item_file = output.agent_files[0] if output.agent_files else ""
    output.driver_file = next((path for path in output.agent_files if path.endswith("_driver.sv")), "")
    output.monitor_file = next((path for path in output.agent_files if path.endswith("_monitor.sv")), "")
    output.agent_file = next((path for path in output.agent_files if path.endswith("_agent.sv")), "")

    output.scoreboard_file = _write(
        work,
        f"{top}_scoreboard.sv",
        _gen_multi_scoreboard(top, agent_specs, uvm_scoreboard_checks, reference_data),
    )
    output.coverage_file = _write(work, f"{top}_coverage.sv", _gen_multi_coverage(top, agent_specs, fsms, uvm_coverage_points))
    env_config_file = _write(work, f"{top}_env_config.sv", _gen_env_config(top, agent_specs))
    virtual_sequencer_file = _write(work, f"{top}_virtual_sequencer.sv", _gen_virtual_sequencer(top, agent_specs))
    virtual_sequence_file = _write(work, f"{top}_virtual_sequence.sv", _gen_virtual_sequence(top, agent_specs))
    output.sequence_files.append(virtual_sequence_file)
    output.env_file = _write(work, f"{top}_env.sv", _gen_multi_env(top, agent_specs))

    max_tests = getattr(content_limits, "max_test_classes", 6)
    try:
        max_tests = int(max_tests)
    except (TypeError, ValueError):
        max_tests = 6
    selected_scenarios = list(uvm_scenarios or [])[:max_tests]
    output.test_file = _write(
        work,
        f"{top}_tests.sv",
        _gen_multi_tests(top, selected_scenarios),
    )
    output.test_files.append(output.test_file)

    include_files.extend([
        Path(output.scoreboard_file).name,
        Path(output.coverage_file).name,
        Path(env_config_file).name,
        Path(virtual_sequencer_file).name,
        Path(virtual_sequence_file).name,
        Path(output.env_file).name,
    ])
    include_files.extend(Path(path).name for path in output.test_files)

    optional_roles = set((output.generation_policy or {}).get("optional_files") or [])
    if "assertions" in optional_roles:
        output.support_files.append(
            _write(work, f"{top}_assertions.sv", _gen_assertions(top, ports, clk_name, rst_name))
        )
    if "ral" in optional_roles:
        output.support_files.append(
            _write(work, f"{top}_ral.sv", _gen_ral(top, reference_data.get("register_fields", [])))
        )
    if "test_plan_doc" in optional_roles:
        output.support_files.append(
            _write(work, "test_plan.md", _gen_uvm_test_plan_doc(top, uvm_scenarios, uvm_scoreboard_checks, uvm_coverage_points))
        )
    output.pkg_file = _write(work, f"{top}_pkg.sv", _gen_multi_package(top, parameters, agent_specs, include_files))
    output.top_file = _write(
        work,
        "top_tb.sv",
        _gen_multi_top(
            top,
            ports,
            clk_name,
            rst_name,
            rst_active_low,
            parameters,
            agent_specs,
            include_assertions="assertions" in optional_roles,
        ),
    )
    output.filelist = _write(
        work,
        f"{top}_uvm.f",
        _gen_multi_filelist(
            top,
            work,
            output.interface_files,
            dut_rtl_files or [],
            output.support_files,
            package_include_files=[str(work / name) for name in include_files],
        ),
    )
    output.regression_manifest = _write(
        work,
        "uvm_regression_manifest.json",
        json.dumps(
            _build_regression_manifest(
                top=top,
                sequence_specs=[
                    {"name": "base", "scenario": {"id": "base", "description": "Base smoke test"}},
                    *[
                        {"name": _safe_sv_name(str(_dict_get(item, "name", f"scenario_{index + 1}"))), "scenario": _to_jsonable(item)}
                        for index, item in enumerate(selected_scenarios)
                    ],
                ],
                scoreboard_checks=uvm_scoreboard_checks,
                coverage_points=uvm_coverage_points,
                assertions=uvm_assertions,
                multi_agent=True,
            ),
            indent=2,
        ),
    )
    output.build_file = _write(work, "Makefile", _gen_makefile(top))

    output.all_files = [
        path
        for path in (
            output.interface_files
            + [output.pkg_file]
            + output.agent_files
            + [output.scoreboard_file, output.coverage_file, env_config_file, virtual_sequencer_file, virtual_sequence_file, output.env_file]
            + output.test_files
            + [output.top_file, output.filelist, output.build_file]
            + [output.regression_manifest]
            + output.support_files
        )
        if path
    ]
    output.all_files = list(dict.fromkeys(output.all_files))
    output.summary = (
        f"Generated {len(output.all_files)} UVM files for {top}: "
        f"{len(agent_specs)} interface agents, {len(ports)} DUT ports, "
        f"{len(output.test_files)} consolidated test file(s)"
    )
    logger.info(output.summary)
    return output


def _gen_multi_package(top: str, params: list, agent_specs: List[UVMAgentSpec], include_files: List[str]) -> str:
    TOP = top.upper()
    param_lines = "\n".join(
        f"    parameter int {_param_name(p)} = {_param_default(p)};"
        for p in params
        if _param_name(p)
    )
    analysis_decl_lines = "\n".join(
        f"    `uvm_analysis_imp_decl(_{spec.base})"
        for spec in agent_specs
    )
    typedef_lines = []
    for spec in agent_specs:
        typedef_lines.extend([
            f"    typedef class {spec.base}_sequence_item;",
            f"    typedef class {spec.base}_sequencer;",
            f"    typedef class {spec.base}_sequence;",
            f"    typedef class {spec.base}_driver;",
            f"    typedef class {spec.base}_monitor;",
            f"    typedef class {spec.name};",
        ])
    typedef_lines.extend([
        f"    typedef class {top}_scoreboard;",
        f"    typedef class {top}_coverage;",
        f"    typedef class {top}_env_config;",
        f"    typedef class {top}_virtual_sequencer;",
        f"    typedef class {top}_base_vseq;",
        f"    typedef class {top}_env;",
        f"    typedef class {top}_base_test;",
    ])
    include_lines = "\n".join(f'    `include "{Path(name).name}"' for name in include_files)
    return f"""`ifndef {TOP}_PKG_SV
`define {TOP}_PKG_SV

package {top}_pkg;
    import uvm_pkg::*;
    `include "uvm_macros.svh"

    // Parameters
{param_lines if param_lines else "    // (no parameters)"}

    // Analysis implementation suffixes for multi-agent scoreboarding/coverage
{analysis_decl_lines}

    // Forward declarations
{chr(10).join(typedef_lines)}

{include_lines}

endpackage : {top}_pkg

`endif
"""


def _gen_agent_interface(spec: UVMAgentSpec, clk: str, rst: str) -> str:
    param_header = _sv_parameter_header(_collect_uvm_parameters([], spec.ports))
    signal_lines = []
    for port in spec.ports:
        name = _port_name(port)
        bus = _port_bus_str(port)
        signal_lines.append(f"    logic {bus}{name};")
    driver_outputs = "\n".join(
        f"        output {_port_name(port)};"
        for port in spec.inputs
    )
    monitor_inputs = "\n".join(
        f"        input {_port_name(port)};"
        for port in spec.ports
    )
    return f"""interface {spec.base}_intf{param_header}(input logic {clk}, input logic {rst});

{chr(10).join(signal_lines) if signal_lines else "    // No grouped DUT signals"}

    clocking driver_cb @(posedge {clk});
        default input #1 output #1;
{driver_outputs if driver_outputs else "        // Passive interface has no driver outputs"}
    endclocking

    clocking monitor_cb @(posedge {clk});
        default input #1;
{monitor_inputs if monitor_inputs else "        // No monitor inputs"}
    endclocking

    modport driver_mp(clocking driver_cb, input {clk}, input {rst});
    modport monitor_mp(clocking monitor_cb, input {clk}, input {rst});

endinterface : {spec.base}_intf
"""


def _gen_agent_seq_item(spec: UVMAgentSpec) -> str:
    in_fields = "\n".join(
        f"    rand logic {_port_bus_str(port)}{_port_name(port)};"
        for port in spec.inputs
    )
    out_fields = "\n".join(
        f"    logic {_port_bus_str(port)}{_port_name(port)};"
        for port in spec.outputs
    )
    field_lines = "\n".join(
        f"        `uvm_field_int({_port_name(port)}, UVM_ALL_ON)"
        for port in spec.inputs + spec.outputs
    )
    constraint_lines = _seq_item_constraint_lines(
        spec.inputs,
        {"constraints": list(spec.constraints or [])},
    )
    constraints = "\n".join(f"        {line}" for line in constraint_lines)
    return f"""class {spec.base}_sequence_item extends uvm_sequence_item;

    // Driven fields
{in_fields if in_fields else "    // (no driven fields)"}

    // Observed fields
{out_fields if out_fields else "    // (no observed fields)"}

    `uvm_object_utils_begin({spec.base}_sequence_item)
{field_lines}
    `uvm_object_utils_end

    function new(string name = "{spec.base}_sequence_item");
        super.new(name);
    endfunction

    constraint legal_values {{
{constraints if constraints else "        // Add mental-model constraints when available"}
    }}

endclass : {spec.base}_sequence_item
"""


def _gen_agent_sequencer(spec: UVMAgentSpec) -> str:
    return f"""class {spec.base}_sequencer extends uvm_sequencer #({spec.base}_sequence_item);

    `uvm_component_utils({spec.base}_sequencer)

    function new(string name = "{spec.base}_sequencer", uvm_component parent = null);
        super.new(name, parent);
    endfunction

endclass : {spec.base}_sequencer
"""


def _scenario_kind_id(scenario: Dict[str, Any] | None) -> int:
    data = scenario if isinstance(scenario, dict) else {}
    text = " ".join(
        str(data.get(key) or "")
        for key in ("name", "description", "id", "type", "category")
    ).lower()
    if any(word in text for word in ("reset", "initial", "init")):
        return 1
    if any(word in text for word in ("write", "wr", "store", "push")):
        return 2
    if any(word in text for word in ("read", "rd", "load", "pop")):
        return 3
    if any(word in text for word in ("burst", "stress", "random", "back to back", "back-to-back", "overflow", "underflow")):
        return 4
    if any(word in text for word in ("error", "illegal", "invalid", "violation")):
        return 5
    return 0


def _scenario_transaction_count(scenario: Dict[str, Any] | None, scenario_kind: int) -> int:
    data = scenario if isinstance(scenario, dict) else {}
    try:
        explicit = int(data.get("num_transactions") or data.get("transactions") or 0)
    except (TypeError, ValueError):
        explicit = 0
    if explicit > 0:
        return max(1, min(explicit, 500))
    if scenario_kind == 1:
        return 4
    if scenario_kind in {2, 3}:
        return 16
    if scenario_kind == 4:
        return 96
    if scenario_kind == 5:
        return 12
    return 32


def _directed_constraints(inputs: List[Any], scenario_kind: int) -> List[str]:
    constraints: List[str] = []
    for port in inputs or []:
        name = _port_name(port)
        if not name or _port_width(port) != 1:
            continue
        lower = name.lower()
        is_write = lower in {"wr", "we", "wen", "write"} or "write" in lower or lower.startswith("wr_")
        is_read = lower in {"rd", "re", "ren", "read"} or "read" in lower or lower.startswith("rd_")
        compact = _compact_sv_name(lower)
        is_apb_select = compact in {"psel", "pselx", "penable"}
        is_enable = (
            lower in {"en", "enable", "valid", "sel", "select"}
            or lower.endswith("_en")
            or lower.endswith("valid")
            or is_apb_select
        )
        is_tlul_request_valid = lower.replace("_", "") == "avalid"
        is_tlul_response_ready = lower.replace("_", "") == "dready"
        if scenario_kind == 2:
            if is_write or is_enable or is_tlul_request_valid or is_tlul_response_ready:
                constraints.append(f"{name} == 1'b1;")
            elif is_read:
                constraints.append(f"{name} == 1'b0;")
        elif scenario_kind == 3:
            if is_write:
                constraints.append(f"{name} == 1'b0;")
            elif is_read or is_enable or is_tlul_request_valid or is_tlul_response_ready:
                constraints.append(f"{name} == 1'b1;")
        elif scenario_kind == 4:
            if is_enable or is_write or is_read or is_tlul_request_valid or is_tlul_response_ready:
                constraints.append(f"{name} == 1'b1;")
        elif scenario_kind == 5 and (is_enable or is_write or is_read or is_tlul_request_valid or is_tlul_response_ready):
            constraints.append(f"{name} inside {{1'b0, 1'b1}};")
    return list(dict.fromkeys(constraints))


def _directed_randomize_block(item_var: str, inputs: List[Any], scenario_kind: int, indent: str) -> str:
    constraints = _directed_constraints(inputs, scenario_kind)
    if not constraints:
        return (
            f"{indent}if (!{item_var}.randomize()) begin\n"
            f'{indent}    `uvm_error(get_name(), "Randomization failed")\n'
            f"{indent}end"
        )
    constraint_body = "\n".join(f"{indent}    {line}" for line in constraints)
    return (
        f"{indent}if (!{item_var}.randomize() with {{\n"
        f"{constraint_body}\n"
        f"{indent}}}) begin\n"
        f'{indent}    `uvm_error(get_name(), "Directed randomization failed")\n'
        f"{indent}end"
    )


def _scenario_randomize_case_block(item_var: str, inputs: List[Any], indent: str) -> str:
    cases = []
    for kind in range(1, 6):
        cases.append(
            f"{indent}{kind}: begin\n"
            f"{_directed_randomize_block(item_var, inputs, kind, indent + '    ')}\n"
            f"{indent}end"
        )
    default_block = _directed_randomize_block(item_var, inputs, 0, indent + "    ")
    return (
        f"{indent}case (scenario_kind)\n"
        + "\n".join(cases)
        + f"\n{indent}default: begin\n"
        + default_block
        + f"\n{indent}end\n"
        + f"{indent}endcase"
    )


def _compact_sv_name(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(name or "").lower())


def _port_names(ports: List[Any]) -> List[str]:
    return [_port_name(port) for port in ports or [] if _port_name(port)]


def _find_port(
    ports: List[Any],
    *,
    exact: List[str] | tuple[str, ...] = (),
    suffixes: List[str] | tuple[str, ...] = (),
    contains: List[str] | tuple[str, ...] = (),
    direction: str = "",
) -> Any:
    candidates = [
        port for port in ports or []
        if _port_name(port) and (not direction or _port_dir(port) == direction)
    ]
    by_compact = {_compact_sv_name(_port_name(port)): port for port in candidates}
    for item in exact:
        hit = by_compact.get(_compact_sv_name(item))
        if hit is not None:
            return hit
    for suffix in suffixes:
        suffix_compact = _compact_sv_name(suffix)
        for port in candidates:
            if _compact_sv_name(_port_name(port)).endswith(suffix_compact):
                return port
    for token in contains:
        token_compact = _compact_sv_name(token)
        for port in candidates:
            if token_compact in _compact_sv_name(_port_name(port)):
                return port
    return None


def _find_port_name(ports: List[Any], **kwargs) -> str:
    port = _find_port(ports, **kwargs)
    return _port_name(port) if port is not None else ""


def _protocol_kind_for_ports(ports: List[Any], hint: str = "") -> str:
    names = {_compact_sv_name(name) for name in _port_names(ports)}
    hint_compact = _compact_sv_name(hint)
    if (
        "apb" in hint_compact
        or {"psel", "penable", "paddr"} <= names
        or {"psel", "pready", "pwrite"} <= names
    ):
        return "apb"
    if (
        "tilelink" in hint_compact
        or "tlul" in hint_compact
        or {"avalid", "aready", "dvalid", "dready"} <= names
    ):
        return "tilelink_ul"
    if any(name.endswith("valid") for name in names) and any(name.endswith("ready") for name in names):
        return "valid_ready"
    return "generic"


def _agent_protocol_kind(spec: UVMAgentSpec) -> str:
    protocol_hint = " ".join(
        str(
            _dict_get(protocol, "protocol")
            or _dict_get(protocol, "name")
            or _dict_get(protocol, "interface")
            or ""
        )
        for protocol in spec.protocols
    )
    return _protocol_kind_for_ports(
        spec.ports,
        f"{spec.name} {spec.base} {spec.description} {protocol_hint}",
    )


def _payload_ports_for_valid_ready(spec: UVMAgentSpec, valid_name: str, ready_name: str) -> List[Any]:
    valid_compact = _compact_sv_name(valid_name)
    ready_compact = _compact_sv_name(ready_name)
    stem = valid_name[:-5] if valid_name.lower().endswith("valid") else ""
    payloads: List[Any] = []
    for port in spec.ports:
        name = _port_name(port)
        compact = _compact_sv_name(name)
        if not name or compact in {valid_compact, ready_compact}:
            continue
        lower = name.lower()
        if stem and lower.startswith(stem.lower()):
            payloads.append(port)
        elif any(token in compact for token in ("data", "addr", "size", "mask", "strb", "user", "offset")):
            payloads.append(port)
    return payloads[:8]


def _generic_driver_body(spec: UVMAgentSpec) -> str:
    assignments = [
        f"        vif.driver_cb.{_port_name(port)} <= item.{_port_name(port)};"
        for port in spec.inputs
        if _port_name(port)
    ]
    return "\n".join(["        @(vif.driver_cb);", *assignments])


def _apb_driver_body(spec: UVMAgentSpec) -> str:
    psel = _find_port_name(spec.ports, exact=("psel",), suffixes=("psel",), direction="input")
    penable = _find_port_name(spec.ports, exact=("penable",), suffixes=("penable",), direction="input")
    pready = _find_port_name(spec.ports, exact=("pready",), suffixes=("pready",), direction="output")
    excluded = {psel, penable}
    setup_assignments = [
        f"        vif.driver_cb.{_port_name(port)} <= item.{_port_name(port)};"
        for port in spec.inputs
        if _port_name(port) and _port_name(port) not in excluded
    ]
    lines = [
        "        // APB transfer: setup phase followed by access phase.",
        "        @(vif.driver_cb);",
    ]
    if psel:
        lines.append(f"        vif.driver_cb.{psel} <= 1'b1;")
    if penable:
        lines.append(f"        vif.driver_cb.{penable} <= 1'b0;")
    lines.extend(setup_assignments)
    lines.append("        @(vif.driver_cb);")
    if penable:
        lines.append(f"        vif.driver_cb.{penable} <= 1'b1;")
    if pready:
        lines.extend([
            f"        while (vif.{pready} !== 1'b1) begin",
            "            @(vif.driver_cb);",
            "        end",
        ])
    lines.append("        @(vif.driver_cb);")
    if penable:
        lines.append(f"        vif.driver_cb.{penable} <= 1'b0;")
    if psel:
        lines.append(f"        vif.driver_cb.{psel} <= 1'b0;")
    return "\n".join(lines)


def _valid_ready_driver_body(spec: UVMAgentSpec) -> str:
    valid = _find_port_name(spec.ports, suffixes=("valid",), direction="input")
    ready_output = _find_port_name(spec.ports, suffixes=("ready",), direction="output")
    ready_input = _find_port_name(spec.ports, suffixes=("ready",), direction="input")
    if not valid and not ready_input:
        return _generic_driver_body(spec)
    lines = [
        "        // Valid/ready transfer with payload held stable while stalled.",
        "        @(vif.driver_cb);",
    ]
    excluded = {valid, ready_input}
    payload_assignments = [
        f"        vif.driver_cb.{_port_name(port)} <= item.{_port_name(port)};"
        for port in spec.inputs
        if _port_name(port) and _port_name(port) not in excluded
    ]
    lines.extend(payload_assignments)
    if ready_input:
        lines.append(f"        vif.driver_cb.{ready_input} <= item.{ready_input};")
    if valid:
        lines.append(f"        vif.driver_cb.{valid} <= 1'b1;")
        if ready_output:
            lines.extend([
                f"        while (vif.{ready_output} !== 1'b1) begin",
                "            @(vif.driver_cb);",
                "        end",
            ])
        lines.append("        @(vif.driver_cb);")
        lines.append(f"        vif.driver_cb.{valid} <= 1'b0;")
    return "\n".join(lines)


def _protocol_driver_body(spec: UVMAgentSpec) -> str:
    kind = _agent_protocol_kind(spec)
    if kind == "apb":
        return _apb_driver_body(spec)
    if kind in {"valid_ready", "tilelink_ul"}:
        return _valid_ready_driver_body(spec)
    return _generic_driver_body(spec)


def _protocol_monitor_guard_expr(spec: UVMAgentSpec) -> str:
    kind = _agent_protocol_kind(spec)
    if kind == "apb":
        psel = _find_port_name(spec.ports, exact=("psel",), suffixes=("psel",))
        penable = _find_port_name(spec.ports, exact=("penable",), suffixes=("penable",))
        pready = _find_port_name(spec.ports, exact=("pready",), suffixes=("pready",))
        required = [name for name in (psel, penable, pready) if name]
        if len(required) >= 2:
            return " && ".join(f"vif.monitor_cb.{name} === 1'b1" for name in required)
    if kind in {"valid_ready", "tilelink_ul"}:
        valid = _find_port_name(spec.ports, suffixes=("valid",))
        ready = _find_port_name(spec.ports, suffixes=("ready",))
        if valid and ready:
            return f"vif.monitor_cb.{valid} === 1'b1 && vif.monitor_cb.{ready} === 1'b1"
    return ""


def _gen_agent_sequence(spec: UVMAgentSpec) -> str:
    randomize_cases = _scenario_randomize_case_block(
        item_var="item",
        inputs=spec.inputs,
        indent="            ",
    )
    return f"""class {spec.base}_sequence extends uvm_sequence #({spec.base}_sequence_item);

    `uvm_object_utils({spec.base}_sequence)

    int unsigned num_transactions = 100;
    int unsigned scenario_kind = 0; // 0=random, 1=reset, 2=write, 3=read, 4=stress, 5=error

    function new(string name = "{spec.base}_sequence");
        super.new(name);
    endfunction

    task body();
        {spec.base}_sequence_item item;
        repeat (num_transactions) begin
            item = {spec.base}_sequence_item::type_id::create("item");
            start_item(item);
{randomize_cases}
            finish_item(item);
        end
    endtask

endclass : {spec.base}_sequence
"""


def _gen_agent_driver(spec: UVMAgentSpec) -> str:
    drive_lines = _protocol_driver_body(spec)
    default_drive_lines = (
        "        @(vif.driver_cb);\n"
        "            // Passive/monitor-only interface: no driven fields"
    )
    drive_body = drive_lines if drive_lines else default_drive_lines
    return f"""class {spec.base}_driver extends uvm_driver #({spec.base}_sequence_item);

    `uvm_component_utils({spec.base}_driver)

    virtual {spec.base}_intf vif;

    function new(string name = "{spec.base}_driver", uvm_component parent = null);
        super.new(name, parent);
    endfunction

    function void build_phase(uvm_phase phase);
        super.build_phase(phase);
        if (!uvm_config_db #(virtual {spec.base}_intf)::get(this, "", "vif", vif))
            `uvm_fatal(get_name(), "Could not get virtual interface")
    endfunction

    task run_phase(uvm_phase phase);
        {spec.base}_sequence_item req;
        forever begin
            seq_item_port.get_next_item(req);
            drive_item(req);
            seq_item_port.item_done();
        end
    endtask

    task drive_item({spec.base}_sequence_item item);
{drive_body}
    endtask

endclass : {spec.base}_driver
"""


def _gen_agent_monitor(spec: UVMAgentSpec) -> str:
    sample_lines = "\n".join(
        f"            item.{_port_name(port)} = vif.monitor_cb.{_port_name(port)};"
        for port in spec.ports
    )
    guard_expr = _protocol_monitor_guard_expr(spec)
    if guard_expr:
        body = f"""            if ({guard_expr}) begin
                {spec.base}_sequence_item item = {spec.base}_sequence_item::type_id::create("item");
{sample_lines if sample_lines else "                // No fields to sample"}
                analysis_port.write(item);
            end"""
    else:
        body = f"""            {spec.base}_sequence_item item = {spec.base}_sequence_item::type_id::create("item");
{sample_lines if sample_lines else "            // No fields to sample"}
            analysis_port.write(item);"""
    return f"""class {spec.base}_monitor extends uvm_monitor;

    `uvm_component_utils({spec.base}_monitor)

    virtual {spec.base}_intf vif;
    uvm_analysis_port #({spec.base}_sequence_item) analysis_port;

    function new(string name = "{spec.base}_monitor", uvm_component parent = null);
        super.new(name, parent);
    endfunction

    function void build_phase(uvm_phase phase);
        super.build_phase(phase);
        analysis_port = new("analysis_port", this);
        if (!uvm_config_db #(virtual {spec.base}_intf)::get(this, "", "vif", vif))
            `uvm_fatal(get_name(), "Could not get virtual interface")
    endfunction

    task run_phase(uvm_phase phase);
        forever begin
            @(vif.monitor_cb);
{body}
        end
    endtask

endclass : {spec.base}_monitor
"""


def _gen_protocol_agent(spec: UVMAgentSpec) -> str:
    active_build = f"""        if (get_is_active() == UVM_ACTIVE) begin
            drv = {spec.base}_driver::type_id::create("drv", this);
            sqr = {spec.base}_sequencer::type_id::create("sqr", this);
        end"""
    return f"""class {spec.name} extends uvm_agent;

    `uvm_component_utils({spec.name})

    {spec.base}_driver    drv;
    {spec.base}_monitor   mon;
    {spec.base}_sequencer sqr;

    function new(string name = "{spec.name}", uvm_component parent = null);
        super.new(name, parent);
    endfunction

    function void build_phase(uvm_phase phase);
        super.build_phase(phase);
        mon = {spec.base}_monitor::type_id::create("mon", this);
{active_build}
    endfunction

    function void connect_phase(uvm_phase phase);
        super.connect_phase(phase);
        if (get_is_active() == UVM_ACTIVE) begin
            drv.seq_item_port.connect(sqr.seq_item_export);
        end
    endfunction

endclass : {spec.name}
"""


def _reference_items(reference_data: Dict[str, Any] | None, key: str) -> List[Dict[str, Any]]:
    raw_items = (reference_data or {}).get(key, [])
    if not isinstance(raw_items, list):
        return []
    items: List[Dict[str, Any]] = []
    for raw in raw_items:
        item = _to_jsonable(raw)
        if isinstance(item, dict):
            items.append(item)
    return items


def _reference_strings(reference_data: Dict[str, Any] | None, key: str) -> List[str]:
    raw_items = (reference_data or {}).get(key, []) if reference_data else []
    if not isinstance(raw_items, list):
        raw_items = [raw_items] if raw_items else []
    values: List[str] = []
    seen: set[str] = set()
    for raw in raw_items:
        if isinstance(raw, dict):
            text = str(
                raw.get("constraint")
                or raw.get("text")
                or raw.get("description")
                or raw.get("rule")
                or ""
            ).strip()
        else:
            text = str(raw or "").strip()
        if not text:
            continue
        key_text = re.sub(r"\s+", " ", text.lower())
        if key_text in seen:
            continue
        seen.add(key_text)
        values.append(text)
    return values


def _reference_constraint_texts(reference_data: Dict[str, Any] | None) -> List[str]:
    constraints = _reference_strings(reference_data, "constraints")
    for flow in _reference_items(reference_data, "transaction_flows"):
        constraints.extend(_reference_strings({"constraints": flow.get("constraints", [])}, "constraints"))
    deduped: List[str] = []
    seen: set[str] = set()
    for text in constraints:
        key = re.sub(r"\s+", " ", text.lower())
        if key in seen:
            continue
        seen.add(key)
        deduped.append(text)
    return deduped


def _sv_comment(value: Any) -> str:
    return str(value or "").replace("\r", " ").replace("\n", " ").replace("*/", "* /")[:220]


def _full_width_range_constraint(port: Any) -> str:
    name = _port_name(port)
    width = _port_width(port)
    if width <= 1:
        return f"{name} inside {{1'b0, 1'b1}};"
    if width <= 32:
        return f"{name} inside {{[0:{(1 << width) - 1}]}};"
    return f"{name} inside {{['0:'1]}};"


def _find_address_input(inputs: List[Any], register_items: List[Dict[str, Any]]) -> str:
    input_names = {_port_name(port) for port in inputs if _port_name(port)}
    for reg in register_items:
        addr_signal = str(reg.get("addr_signal") or "").strip()
        if addr_signal in input_names:
            return addr_signal
    for port in inputs:
        name = _port_name(port)
        compact = _compact_sv_name(name)
        if compact in {"paddr", "addr", "address"} or compact.endswith("addr") or compact.endswith("address"):
            return name
    return ""


def _seq_item_constraint_lines(inputs: List[Any], reference_data: Dict[str, Any] | None) -> List[str]:
    lines: List[str] = []
    register_items = _reference_items(reference_data, "register_map")
    address_name = _find_address_input(inputs, register_items)
    offsets: List[str] = []
    seen_offsets: set[str] = set()
    if address_name:
        for reg in register_items[:64]:
            literal = _sv_literal(reg.get("offset"))
            if literal and literal not in seen_offsets:
                seen_offsets.add(literal)
                offsets.append(literal)
        if offsets:
            lines.append(f"{address_name} inside {{{', '.join(offsets[:32])}}};")

    constrained = {address_name} if address_name else set()
    for port in inputs[:8]:
        name = _port_name(port)
        if not name or name in constrained:
            continue
        lines.append(_full_width_range_constraint(port))

    for text in _reference_constraint_texts(reference_data)[:12]:
        lines.append(f"// Mental-model constraint: {_sv_comment(text)}")
    return lines


def _sv_literal(value: Any) -> Optional[str]:
    text = str(value if value is not None else "").strip()
    if not text:
        return None
    lower = text.lower()
    if lower in {"true", "high", "asserted"}:
        return "1'b1"
    if lower in {"false", "low", "deasserted"}:
        return "1'b0"
    if lower in {"'0", "'1", "'x", "'z"}:
        return text
    if re.fullmatch(r"\d+", text):
        return text
    if re.fullmatch(r"\d+'\s*[sS]?[bBdDhHoOxXzZ][0-9a-fA-F_xXzZ]+", text):
        return text
    if re.fullmatch(r"0x[0-9a-fA-F_]+", text):
        return "'h" + text[2:]
    if re.fullmatch(r"0b[01_xXzZ_]+", text):
        return "'b" + text[2:]
    return None


def _sv_string(value: Any) -> str:
    return str(value or "").replace("\\", "\\\\").replace('"', '\\"')[:180]


def _behavior_match_expr(behavior: Dict[str, Any], item_ports: set[str]) -> str:
    stimulus = behavior.get("stimulus", {})
    if not isinstance(stimulus, dict):
        return ""
    clauses = []
    for signal, value in stimulus.items():
        signal_name = str(signal)
        literal = _sv_literal(value)
        if signal_name in item_ports and literal is not None:
            clauses.append(f"item.{signal_name} === {literal}")
    return " && ".join(clauses)


def _behavior_expected_pairs(
    behavior: Dict[str, Any],
    output_names: set[str],
) -> List[tuple[str, str]]:
    expected = behavior.get("expected_output", {})
    if not isinstance(expected, dict):
        return []
    pairs: List[tuple[str, str]] = []
    for signal, value in expected.items():
        signal_name = str(signal)
        literal = _sv_literal(value)
        if signal_name in output_names and literal is not None:
            pairs.append((signal_name, literal))
    return pairs


def _scoreboard_known_output_lines(output_names: List[str], indent: str = "        ") -> str:
    lines: List[str] = []
    for name in output_names[:8]:
        lines.extend([
            f"{indent}if ($isunknown(item.{name})) begin",
            f'{indent}    `uvm_error(get_name(), "{_sv_string(name)} contains X/Z")',
            f"{indent}    fail_count++;",
            f"{indent}end else begin",
            f"{indent}    pass_count++;",
            f"{indent}end",
        ])
    if not lines:
        lines.extend([
            f"{indent}if (item == null) begin",
            f'{indent}    `uvm_error(get_name(), "Null transaction received")',
            f"{indent}    fail_count++;",
            f"{indent}end else begin",
            f"{indent}    pass_count++;",
            f"{indent}end",
        ])
    if lines:
        lines.insert(0, f"{indent}matched_behavior = 1'b1;")
    return "\n".join(lines)


def _approved_scoreboard_check_lines(
    approved_checks: list,
    output_names: List[str],
    *,
    indent: str = "        ",
) -> str:
    output_set = set(output_names)
    lines: List[str] = []
    for index, check in enumerate(approved_checks or []):
        check_dict = _to_jsonable(check)
        if not isinstance(check_dict, dict):
            continue
        expected_output = check_dict.get("expected_output", {})
        if isinstance(expected_output, dict):
            for signal, value in expected_output.items():
                signal_name = str(signal)
                literal = _sv_literal(value)
                if signal_name not in output_set or literal is None:
                    continue
                label = _sv_string(check_dict.get("check") or f"approved_check_{index + 1}")
                lines.extend([
                    f"{indent}if (item.{signal_name} !== {literal}) begin",
                    (
                        f'{indent}    `uvm_error(get_name(), $sformatf("{label}: '
                        f'{_sv_string(signal_name)} expected %0h got %0h", {literal}, item.{signal_name}))'
                    ),
                    f"{indent}    fail_count++;",
                    f"{indent}end else begin",
                    f"{indent}    pass_count++;",
                    f"{indent}end",
                ])
        else:
            signals = check_dict.get("signals", [])
            if not isinstance(signals, list):
                signals = [signals]
            for signal in signals:
                signal_name = str(signal)
                if signal_name in output_set:
                    lines.extend(_scoreboard_known_output_lines([signal_name], indent).splitlines())
    if lines and not any("matched_behavior" in line for line in lines):
        lines.insert(0, f"{indent}matched_behavior = 1'b1;")
    return "\n".join(lines)


def _scoreboard_reference_sections(
    *,
    item_type: str,
    item_ports: set[str],
    output_names: List[str],
    reference_data: Dict[str, Any] | None,
    include_register_model: bool = True,
) -> Dict[str, str]:
    class_decls: List[str] = []
    build_lines: List[str] = []
    helper_functions: List[str] = []
    write_lines: List[str] = []

    register_items = _reference_items(reference_data, "register_fields") if include_register_model else []
    if include_register_model and not register_items:
        register_items = _reference_items(reference_data, "register_map")
    register_inits: List[str] = []
    register_updates: List[str] = []
    for reg in register_items[:32]:
        reg_name = str(reg.get("name") or "").strip()
        if not reg_name:
            continue
        reset_literal = _sv_literal(reg.get("reset_value", "0")) or "0"
        register_inits.append(f'        reg_model["{_sv_string(reg_name)}"] = {reset_literal};')
        wr_en = str(reg.get("write_enable") or "").strip()
        data_signal = str(reg.get("data_signal") or "").strip()
        if wr_en in item_ports and data_signal in item_ports:
            register_updates.extend([
                f"        if (item.{wr_en} === 1'b1) begin",
                f'            reg_model["{_sv_string(reg_name)}"] = item.{data_signal};',
                "        end",
            ])
    if register_inits:
        class_decls.append("    bit [1023:0] reg_model[string];")
        helper_functions.append(
            "    function void reset_reference_model();\n"
            + "\n".join(register_inits)
            + "\n    endfunction"
        )
        build_lines.append("        reset_reference_model();")
    if register_updates:
        write_lines.extend(register_updates)

    output_set = set(output_names)
    for index, behavior in enumerate(_reference_items(reference_data, "expected_behaviors")[:24]):
        match_expr = _behavior_match_expr(behavior, item_ports)
        expected_pairs = _behavior_expected_pairs(behavior, output_set)
        if not match_expr or not expected_pairs:
            continue
        behavior_id = str(behavior.get("id") or f"EB-{index + 1:03d}")
        compare_name = _safe_sv_name(f"compare_{index}_{behavior_id}")
        pending_name = _safe_sv_name(f"check_pending_{index}_{behavior_id}")
        queue_name = _safe_sv_name(f"{behavior_id}_{index}_latency_q")
        latency = 1
        try:
            latency = max(0, int(behavior.get("latency_cycles", 1)))
        except (TypeError, ValueError):
            latency = 1
        class_decls.append(f"    int unsigned {queue_name}[$];")
        compare_lines = [
            f"    function bit {compare_name}({item_type} item);",
            "        bit ok;",
            "        ok = 1'b1;",
        ]
        for signal, literal in expected_pairs:
            compare_lines.extend([
                f"        if (item.{signal} !== {literal}) begin",
                (
                    f'            `uvm_error(get_name(), $sformatf("{_sv_string(behavior_id)}: '
                    f'{_sv_string(signal)} expected %0h got %0h", {literal}, item.{signal}))'
                ),
                "            fail_count++;",
                "            ok = 1'b0;",
                "        end",
            ])
        compare_lines.extend([
            "        if (ok) begin",
            "            pass_count++;",
            "        end",
            "        return ok;",
            "    endfunction",
        ])
        helper_functions.append("\n".join(compare_lines))
        helper_functions.append(
            f"""    function void {pending_name}({item_type} item);
        for (int i = 0; i < {queue_name}.size(); i++) begin
            if ({queue_name}[i] == 0) begin
                void'({compare_name}(item));
                {queue_name}.delete(i);
                i--;
            end else begin
                {queue_name}[i]--;
            end
        end
    endfunction"""
        )
        write_lines.append(f"        {pending_name}(item);")
        write_lines.append(f"        if ({match_expr}) begin")
        write_lines.append("            matched_behavior = 1'b1;")
        if latency == 0:
            write_lines.append(f"            void'({compare_name}(item));")
        else:
            write_lines.append(f"            {queue_name}.push_back({latency - 1});")
        write_lines.append("        end")

    return {
        "class_decls": "\n".join(class_decls),
        "build_lines": "\n".join(build_lines),
        "helper_functions": "\n\n".join(helper_functions),
        "write_lines": "\n".join(write_lines),
    }


def _protocol_scoreboard_decls(spec: UVMAgentSpec) -> str:
    kind = _agent_protocol_kind(spec)
    base = _safe_sv_name(spec.base)
    lines: List[str] = []
    if kind == "apb":
        paddr = _find_port(spec.ports, exact=("paddr",), suffixes=("paddr",))
        pwrite = _find_port(spec.ports, exact=("pwrite",), suffixes=("pwrite",))
        pwdata = _find_port(spec.ports, exact=("pwdata",), suffixes=("pwdata", "wdata"))
        lines.append(f"    bit {base}_apb_setup_seen;")
        if paddr is not None:
            lines.append(f"    logic {_port_bus_str(paddr)}{base}_apb_addr_q;")
        if pwrite is not None:
            lines.append(f"    bit {base}_apb_write_q;")
        if pwdata is not None:
            lines.append(f"    logic {_port_bus_str(pwdata)}{base}_apb_wdata_q;")
    elif kind in {"valid_ready", "tilelink_ul"}:
        valid = _find_port_name(spec.ports, suffixes=("valid",))
        ready = _find_port_name(spec.ports, suffixes=("ready",))
        payloads = _payload_ports_for_valid_ready(spec, valid, ready)
        if valid and ready:
            lines.append(f"    bit {base}_vr_stalled;")
            for payload in payloads:
                pname = _port_name(payload)
                lines.append(f"    logic {_port_bus_str(payload)}{base}_{_safe_sv_name(pname)}_q;")
    return "\n".join(lines)


def _protocol_scoreboard_lines(spec: UVMAgentSpec, indent: str = "        ") -> str:
    kind = _agent_protocol_kind(spec)
    base = _safe_sv_name(spec.base)
    if kind == "apb":
        psel = _find_port_name(spec.ports, exact=("psel",), suffixes=("psel",))
        penable = _find_port_name(spec.ports, exact=("penable",), suffixes=("penable",))
        pready = _find_port_name(spec.ports, exact=("pready",), suffixes=("pready",))
        pslverr = _find_port_name(spec.ports, exact=("pslverr",), suffixes=("pslverr",))
        paddr = _find_port_name(spec.ports, exact=("paddr",), suffixes=("paddr",))
        pwrite = _find_port_name(spec.ports, exact=("pwrite",), suffixes=("pwrite",))
        pwdata = _find_port_name(spec.ports, exact=("pwdata",), suffixes=("pwdata", "wdata"))
        if not (psel and penable):
            return ""
        lines = [
            f"{indent}// APB protocol checker: setup/access sequencing and stable control payload.",
            f"{indent}if (item.{penable} === 1'b1 && item.{psel} !== 1'b1) begin",
            f'{indent}    `uvm_error(get_name(), "APB PENABLE asserted without PSEL")',
            f"{indent}    fail_count++;",
            f"{indent}    matched_behavior = 1'b1;",
            f"{indent}end",
        ]
        if pslverr and pready:
            lines.extend([
                f"{indent}if (item.{pslverr} === 1'b1 && !(item.{psel} === 1'b1 && item.{penable} === 1'b1 && item.{pready} === 1'b1)) begin",
                f'{indent}    `uvm_error(get_name(), "APB PSLVERR observed outside completed access phase")',
                f"{indent}    fail_count++;",
                f"{indent}    matched_behavior = 1'b1;",
                f"{indent}end",
            ])
        lines.extend([
            f"{indent}if (item.{psel} === 1'b1 && item.{penable} !== 1'b1) begin",
            f"{indent}    {base}_apb_setup_seen = 1'b1;",
        ])
        if paddr:
            lines.append(f"{indent}    {base}_apb_addr_q = item.{paddr};")
        if pwrite:
            lines.append(f"{indent}    {base}_apb_write_q = item.{pwrite};")
        if pwdata:
            lines.append(f"{indent}    {base}_apb_wdata_q = item.{pwdata};")
        lines.extend([
            f"{indent}    pass_count++;",
            f"{indent}    matched_behavior = 1'b1;",
            f"{indent}end",
            f"{indent}if (item.{psel} === 1'b1 && item.{penable} === 1'b1) begin",
            f"{indent}    matched_behavior = 1'b1;",
            f"{indent}    if (!{base}_apb_setup_seen) begin",
            f'{indent}        `uvm_error(get_name(), "APB access phase observed without prior setup phase")',
            f"{indent}        fail_count++;",
            f"{indent}    end",
        ])
        if paddr:
            lines.extend([
                f"{indent}    if ({base}_apb_setup_seen && item.{paddr} !== {base}_apb_addr_q) begin",
                f'{indent}        `uvm_error(get_name(), "APB PADDR changed between setup and access")',
                f"{indent}        fail_count++;",
                f"{indent}    end",
            ])
        if pwrite:
            lines.extend([
                f"{indent}    if ({base}_apb_setup_seen && item.{pwrite} !== {base}_apb_write_q) begin",
                f'{indent}        `uvm_error(get_name(), "APB PWRITE changed between setup and access")',
                f"{indent}        fail_count++;",
                f"{indent}    end",
            ])
        if pwdata and pwrite:
            lines.extend([
                f"{indent}    if ({base}_apb_setup_seen && {base}_apb_write_q === 1'b1 && item.{pwdata} !== {base}_apb_wdata_q) begin",
                f'{indent}        `uvm_error(get_name(), "APB PWDATA changed between setup and write access")',
                f"{indent}        fail_count++;",
                f"{indent}    end",
            ])
        if pready:
            lines.extend([
                f"{indent}    if (item.{pready} === 1'b1) begin",
                f"{indent}        {base}_apb_setup_seen = 1'b0;",
                f"{indent}        pass_count++;",
                f"{indent}    end",
            ])
        else:
            lines.append(f"{indent}    pass_count++;")
        lines.append(f"{indent}end")
        return "\n".join(lines)

    if kind in {"valid_ready", "tilelink_ul"}:
        valid = _find_port_name(spec.ports, suffixes=("valid",))
        ready = _find_port_name(spec.ports, suffixes=("ready",))
        if not (valid and ready):
            return ""
        payloads = _payload_ports_for_valid_ready(spec, valid, ready)
        lines = [
            f"{indent}// Valid/ready checker: accepted transfers and stalled payload stability.",
            f"{indent}if (item.{valid} === 1'b1 && item.{ready} !== 1'b1) begin",
            f"{indent}    matched_behavior = 1'b1;",
            f"{indent}    if (!{base}_vr_stalled) begin",
            f"{indent}        {base}_vr_stalled = 1'b1;",
        ]
        for payload in payloads:
            pname = _port_name(payload)
            lines.append(f"{indent}        {base}_{_safe_sv_name(pname)}_q = item.{pname};")
        lines.append(f"{indent}    end else begin")
        if payloads:
            for payload in payloads:
                pname = _port_name(payload)
                lines.extend([
                    f"{indent}        if (item.{pname} !== {base}_{_safe_sv_name(pname)}_q) begin",
                    f'{indent}            `uvm_error(get_name(), "{_sv_string(pname)} changed while valid/ready was stalled")',
                    f"{indent}            fail_count++;",
                    f"{indent}        end",
                ])
        else:
            lines.append(f"{indent}        pass_count++;")
        lines.extend([
            f"{indent}    end",
            f"{indent}end",
            f"{indent}if (item.{valid} === 1'b1 && item.{ready} === 1'b1) begin",
            f"{indent}    {base}_vr_stalled = 1'b0;",
            f"{indent}    matched_behavior = 1'b1;",
            f"{indent}    pass_count++;",
            f"{indent}end",
        ])
        return "\n".join(lines)
    return ""


def _gen_multi_scoreboard(
    top: str,
    agent_specs: List[UVMAgentSpec],
    approved_checks: list | None = None,
    reference_data: Dict[str, Any] | None = None,
) -> str:
    cross_agent = _cross_agent_scoreboard_sections(agent_specs)
    imp_fields = "\n".join(
        f"    uvm_analysis_imp_{spec.base} #({spec.base}_sequence_item, {top}_scoreboard) {spec.base}_analysis_imp;"
        for spec in agent_specs
    )
    build_lines = "\n".join(
        f'        {spec.base}_analysis_imp = new("{spec.base}_analysis_imp", this);'
        for spec in agent_specs
    )
    sections_by_spec = []
    for index, spec in enumerate(agent_specs):
        item_ports = {_port_name(port) for port in spec.ports if _port_name(port)}
        output_names = [_port_name(port) for port in spec.outputs if _port_name(port)]
        sections_by_spec.append(
            (
                spec,
                _scoreboard_reference_sections(
                    item_type=f"{spec.base}_sequence_item",
                    item_ports=item_ports,
                    output_names=output_names,
                    reference_data=reference_data,
                    include_register_model=(index == 0),
                ),
            )
        )
    reference_decls = "\n".join(
        section["class_decls"]
        for _spec, section in sections_by_spec
        if section["class_decls"]
    )
    cross_decls = cross_agent.get("class_decls", "")
    protocol_decls = "\n".join(
        decl
        for decl in (_protocol_scoreboard_decls(spec) for spec, _section in sections_by_spec)
        if decl
    )
    reference_build_lines = "\n".join(
        section["build_lines"]
        for _spec, section in sections_by_spec
        if section["build_lines"]
    )
    helper_functions = "\n\n".join(
        section["helper_functions"]
        for _spec, section in sections_by_spec
        if section["helper_functions"]
    )
    write_methods = "\n\n".join(
        _gen_scoreboard_write_method(
            spec,
            approved_checks or [],
            section,
            extra_write_lines="\n".join(
                line
                for line in [
                    _protocol_scoreboard_lines(spec),
                    cross_agent.get("write_lines", {}).get(spec.base, ""),
                ]
                if line
            ),
        )
        for spec, section in sections_by_spec
    )
    return f"""class {top}_scoreboard extends uvm_scoreboard;

    `uvm_component_utils({top}_scoreboard)

{imp_fields}
    int pass_count = 0;
    int fail_count = 0;
{reference_decls}
{cross_decls}
{protocol_decls}

    function new(string name = "{top}_scoreboard", uvm_component parent = null);
        super.new(name, parent);
    endfunction

    function void build_phase(uvm_phase phase);
        super.build_phase(phase);
{build_lines}
{reference_build_lines}
    endfunction

{helper_functions}

{write_methods}

    function void report_phase(uvm_phase phase);
        `uvm_info(get_name(), $sformatf("Scoreboard: %0d PASS, %0d FAIL", pass_count, fail_count), UVM_LOW)
    endfunction

endclass : {top}_scoreboard
"""


def _gen_scoreboard_write_method(
    spec: UVMAgentSpec,
    approved_checks: list,
    reference_sections: Dict[str, str] | None = None,
    extra_write_lines: str = "",
) -> str:
    checks = "\n".join(
        f'        // Plan check: {_dict_get(check, "check", "check")} - {_dict_get(check, "description", "")}'
        for check in approved_checks
        if _check_mentions_agent_ports(check, spec)
    )
    reference_lines = (reference_sections or {}).get("write_lines", "")
    output_names = [_port_name(port) for port in spec.outputs if _port_name(port)]
    approved_check_lines = _approved_scoreboard_check_lines(
        [
            check
            for check in approved_checks
            if _check_mentions_agent_ports(check, spec)
        ],
        output_names,
    )
    fallback_checks = _scoreboard_known_output_lines(output_names)
    return f"""    function void write_{spec.base}({spec.base}_sequence_item item);
        bit matched_behavior;
        matched_behavior = 1'b0;
{reference_lines}
{extra_write_lines}
{approved_check_lines}
        if (!matched_behavior) begin
{fallback_checks}
        end
{checks}
    endfunction"""


def _cross_agent_scoreboard_sections(agent_specs: List[UVMAgentSpec]) -> Dict[str, Any]:
    if len(agent_specs or []) < 2:
        return {"class_decls": "", "write_lines": {}}
    stimulus = next((spec for spec in agent_specs if spec.is_active), agent_specs[0])
    response = next((spec for spec in agent_specs if spec is not stimulus), None)
    if response is None:
        return {"class_decls": "", "write_lines": {}}

    pairs = _cross_agent_compare_pairs(stimulus, response)
    if not pairs:
        return {"class_decls": "", "write_lines": {}}

    queue_name = f"{stimulus.base}_expected_q"
    stimulus_lines = f"""        {queue_name}.push_back(item);
        matched_behavior = 1'b1;"""

    compare_lines = [
        f"""        if ({queue_name}.size() == 0) begin
            `uvm_error(get_name(), "{response.base} transaction observed with no pending {stimulus.base} transaction")
            fail_count++;
            return;
        end
        begin
            {stimulus.base}_sequence_item ref_item;
            ref_item = {queue_name}.pop_front();"""
    ]
    for pair in pairs:
        stim_field = pair["stimulus"]
        resp_field = pair["response"]
        label = pair["label"]
        condition = pair.get("condition", "")
        comparison = f"""            if (item.{resp_field} !== ref_item.{stim_field}) begin
                `uvm_error(get_name(), $sformatf("{label} MISMATCH exp=0x%0h got=0x%0h", ref_item.{stim_field}, item.{resp_field}))
                fail_count++;
            end else begin
                pass_count++;
            end"""
        if condition:
            compare_lines.append(f"""            if ({condition}) begin
{comparison}
            end""")
        else:
            compare_lines.append(comparison)
    compare_lines.append("""        end
        matched_behavior = 1'b1;""")

    return {
        "class_decls": f"    {stimulus.base}_sequence_item {queue_name}[$];",
        "write_lines": {
            stimulus.base: stimulus_lines,
            response.base: "\n".join(compare_lines),
        },
    }


def _cross_agent_compare_pairs(
    stimulus: UVMAgentSpec,
    response: UVMAgentSpec,
) -> List[Dict[str, str]]:
    def _field(spec: UVMAgentSpec, exact: List[str], suffixes: List[str]) -> str:
        names = [_port_name(port) for port in spec.ports if _port_name(port)]
        by_lower = {name.lower(): name for name in names}
        for candidate in exact:
            if candidate.lower() in by_lower:
                return by_lower[candidate.lower()]
        for suffix in suffixes:
            for name in names:
                if name.lower().endswith(suffix.lower()):
                    return name
        return ""

    stimulus_write = _field(stimulus, ["hwrite", "write", "wr"], ["write", "wr"])
    response_write = _field(response, ["pwrite", "write", "wr"], ["write", "wr"])
    candidates = [
        ("ADDR", _field(stimulus, ["haddr", "addr"], ["addr"]), _field(response, ["paddr", "addr"], ["addr"]), ""),
        ("RW", stimulus_write, response_write, ""),
        (
            "WDATA",
            _field(stimulus, ["hwdata", "wdata", "write_data", "data_in"], ["wdata", "write_data"]),
            _field(response, ["pwdata", "wdata", "write_data", "data_in"], ["wdata", "write_data"]),
            f"ref_item.{stimulus_write}" if stimulus_write else "",
        ),
    ]
    pairs: List[Dict[str, str]] = []
    for label, stim_field, resp_field, condition in candidates:
        if stim_field and resp_field:
            pair = {"label": label, "stimulus": stim_field, "response": resp_field}
            if condition:
                pair["condition"] = condition
            pairs.append(pair)
    return pairs


def _check_mentions_agent_ports(check: Any, spec: UVMAgentSpec) -> bool:
    signals = _to_jsonable(_dict_get(check, "signals", "")) if not isinstance(check, dict) else check.get("signals", [])
    if not signals:
        return True
    if not isinstance(signals, list):
        signals = [signals]
    owned = {_port_name(port) for port in spec.ports}
    return any(str(signal) in owned for signal in signals)


def _gen_multi_coverage(top: str, agent_specs: List[UVMAgentSpec], fsms: list, approved_points: list | None = None) -> str:
    imp_fields = "\n".join(
        f"    uvm_analysis_imp_{spec.base} #({spec.base}_sequence_item, {top}_coverage) {spec.base}_analysis_imp;"
        for spec in agent_specs
    )
    covergroups = "\n\n".join(_gen_agent_covergroup(spec, approved_points or []) for spec in agent_specs)
    build_lines = "\n".join(
        f'        {spec.base}_analysis_imp = new("{spec.base}_analysis_imp", this);\n        {spec.base}_cg = new();'
        for spec in agent_specs
    )
    write_methods = "\n\n".join(
        f"""    function void write_{spec.base}({spec.base}_sequence_item item);
        {spec.base}_cg.sample(item);
    endfunction"""
        for spec in agent_specs
    )
    return f"""class {top}_coverage extends uvm_component;

    `uvm_component_utils({top}_coverage)

{imp_fields}

{covergroups}

    function new(string name = "{top}_coverage", uvm_component parent = null);
        super.new(name, parent);
    endfunction

    function void build_phase(uvm_phase phase);
        super.build_phase(phase);
{build_lines}
    endfunction

{write_methods}

endclass : {top}_coverage
"""


def _protocol_coverage_lines(spec: UVMAgentSpec, indent: str = "        ") -> List[str]:
    kind = _agent_protocol_kind(spec)
    lines: List[str] = []

    if kind == "apb":
        psel = _find_port_name(spec.ports, exact=("psel",), suffixes=("psel",))
        penable = _find_port_name(spec.ports, exact=("penable",), suffixes=("penable",))
        pready = _find_port_name(spec.ports, exact=("pready",), suffixes=("pready",))
        pwrite = _find_port_name(spec.ports, exact=("pwrite",), suffixes=("pwrite",))
        pslverr = _find_port_name(spec.ports, exact=("pslverr",), suffixes=("pslverr", "slverr"))

        if psel and penable and pready:
            lines.extend([
                f"{indent}apb_phase_cp: coverpoint {{item.{psel}, item.{penable}, item.{pready}}} {{",
                f"{indent}    bins idle = {{3'b000}};",
                f"{indent}    bins setup = {{3'b100}};",
                f"{indent}    bins wait_state = {{3'b110}};",
                f"{indent}    bins complete = {{3'b111}};",
                f"{indent}}}",
            ])
        if pwrite:
            lines.extend([
                f"{indent}apb_direction_cp: coverpoint item.{pwrite} {{",
                f"{indent}    bins read = {{1'b0}};",
                f"{indent}    bins write = {{1'b1}};",
                f"{indent}}}",
            ])
        if pslverr:
            lines.extend([
                f"{indent}apb_error_cp: coverpoint item.{pslverr} {{",
                f"{indent}    bins ok = {{1'b0}};",
                f"{indent}    bins error = {{1'b1}};",
                f"{indent}}}",
            ])
        if psel and penable and pready and pwrite:
            lines.append(f"{indent}apb_phase_x_direction: cross apb_phase_cp, apb_direction_cp;")
        if psel and penable and pready and pslverr:
            lines.append(f"{indent}apb_phase_x_error: cross apb_phase_cp, apb_error_cp;")
        return lines

    pairs: List[tuple[str, str, str]] = []
    if kind == "tilelink_ul":
        for label, valid_names, ready_names in [
            ("tlul_a", ("a_valid", "avalid"), ("a_ready", "aready")),
            ("tlul_d", ("d_valid", "dvalid"), ("d_ready", "dready")),
        ]:
            valid = _find_port_name(spec.ports, exact=valid_names, suffixes=valid_names)
            ready = _find_port_name(spec.ports, exact=ready_names, suffixes=ready_names)
            if valid and ready:
                pairs.append((label, valid, ready))
    else:
        valid = _find_port_name(spec.ports, suffixes=("valid",))
        ready = _find_port_name(spec.ports, suffixes=("ready",))
        if valid and ready:
            pairs.append(("vr", valid, ready))

    for label, valid, ready in pairs:
        cp_name = _safe_sv_name(f"{label}_handshake_cp")
        lines.extend([
            f"{indent}{cp_name}: coverpoint {{item.{valid}, item.{ready}}} {{",
            f"{indent}    bins idle = {{2'b00}};",
            f"{indent}    bins backpressure = {{2'b10}};",
            f"{indent}    bins ready_only = {{2'b01}};",
            f"{indent}    bins transfer = {{2'b11}};",
            f"{indent}}}",
        ])
    return lines


def _gen_agent_covergroup(spec: UVMAgentSpec, approved_points: list) -> str:
    cp_lines = []
    for port in spec.ports[:12]:
        name = _port_name(port)
        if _port_width(port) <= 8:
            cp_lines.append(f"        {name}_cp: coverpoint item.{name};")
        else:
            cp_lines.append(f"        {name}_cp: coverpoint item.{name} {{ option.auto_bin_max = 16; }}")
    for point in approved_points:
        signal = _dict_get(point, "signal", "")
        signals = point.get("signals", []) if isinstance(point, dict) else []
        owned = {_port_name(port) for port in spec.ports}
        point_name = _safe_sv_name(_dict_get(point, "point", "plan_point"))
        if signal in owned or any(str(item) in owned for item in signals):
            cover_signal = signal if signal in owned else next(
                (str(item) for item in signals if str(item) in owned),
                "",
            )
            if cover_signal:
                cp_lines.append(f"        {point_name}_cp: coverpoint item.{cover_signal};")
            cp_lines.append(
                f'        // Plan coverage: {_dict_get(point, "point", "coverage_point")} - {_dict_get(point, "description", "")}'
            )
    cp_lines.extend(_protocol_coverage_lines(spec))
    return f"""    covergroup {spec.base}_cg with function sample({spec.base}_sequence_item item);
        option.per_instance = 1;
{chr(10).join(cp_lines) if cp_lines else "        // Add coverage points when behavior is clarified"}
    endgroup"""


def _gen_env_config(top: str, agent_specs: List[UVMAgentSpec]) -> str:
    vif_fields = "\n".join(
        f"    virtual {spec.base}_intf {spec.base}_vif;\n    uvm_active_passive_enum {spec.base}_is_active = {'UVM_ACTIVE' if spec.is_active else 'UVM_PASSIVE'};"
        for spec in agent_specs
    )
    field_macros = "\n".join(
        f"        `uvm_field_enum(uvm_active_passive_enum, {spec.base}_is_active, UVM_DEFAULT)"
        for spec in agent_specs
    )
    return f"""class {top}_env_config extends uvm_object;

    `uvm_object_utils_begin({top}_env_config)
{field_macros}
    `uvm_object_utils_end

{vif_fields}

    function new(string name = "{top}_env_config");
        super.new(name);
    endfunction

endclass : {top}_env_config
"""


def _gen_virtual_sequencer(top: str, agent_specs: List[UVMAgentSpec]) -> str:
    fields = "\n".join(
        f"    {spec.base}_sequencer {spec.base}_sqr;"
        for spec in agent_specs
        if spec.is_active
    )
    return f"""class {top}_virtual_sequencer extends uvm_sequencer;

    `uvm_component_utils({top}_virtual_sequencer)

{fields if fields else "    // No active sequencers"}

    function new(string name = "{top}_virtual_sequencer", uvm_component parent = null);
        super.new(name, parent);
    endfunction

endclass : {top}_virtual_sequencer
"""


def _gen_virtual_sequence(top: str, agent_specs: List[UVMAgentSpec]) -> str:
    active_specs = [spec for spec in agent_specs if spec.is_active]
    declarations = "\n".join(
        f"        {spec.base}_sequence {spec.base}_seq;"
        for spec in active_specs
    )
    creates = "\n".join(
        "\n".join([
            f'        {spec.base}_seq = {spec.base}_sequence::type_id::create("{spec.base}_seq");',
            f"        {spec.base}_seq.num_transactions = num_transactions;",
            f"        {spec.base}_seq.scenario_kind = scenario_kind;",
        ])
        for spec in active_specs
    )
    fork_blocks = "\n".join(
        f"""            begin
                if (p_sequencer.{spec.base}_sqr != null)
                    {spec.base}_seq.start(p_sequencer.{spec.base}_sqr);
                else
                    `uvm_warning("VSEQ", "{spec.base} sequencer is null")
            end"""
        for spec in active_specs
    )
    start_block = (
        f"        fork\n{fork_blocks}\n        join_any\n        #50;\n        disable fork;"
        if fork_blocks else
        "        // Nothing to start"
    )
    return f"""class {top}_base_vseq extends uvm_sequence;

    `uvm_object_utils({top}_base_vseq)
    `uvm_declare_p_sequencer({top}_virtual_sequencer)

    int unsigned scenario_kind = 0;
    int unsigned num_transactions = 100;

    function new(string name = "{top}_base_vseq");
        super.new(name);
    endfunction

    task body();
{declarations if declarations else "        // No active agent sequences"}
{creates if creates else "        // Nothing to create"}
{start_block}
    endtask

endclass : {top}_base_vseq
"""


def _gen_multi_env(top: str, agent_specs: List[UVMAgentSpec]) -> str:
    fields = "\n".join(
        f"    {spec.name} {spec.base}_agt;"
        for spec in agent_specs
    )
    active_sets = "\n".join(
        f'        uvm_config_db #(uvm_active_passive_enum)::set(this, "{spec.base}_agt", "is_active", {"UVM_ACTIVE" if spec.is_active else "UVM_PASSIVE"});'
        for spec in agent_specs
    )
    creates = "\n".join(
        f'        {spec.base}_agt = {spec.name}::type_id::create("{spec.base}_agt", this);'
        for spec in agent_specs
    )
    vseq_assigns = "\n".join(
        f"        vseqr.{spec.base}_sqr = {spec.base}_agt.sqr;"
        for spec in agent_specs
        if spec.is_active
    )
    connects = "\n".join(
        f"        {spec.base}_agt.mon.analysis_port.connect(sb.{spec.base}_analysis_imp);\n        {spec.base}_agt.mon.analysis_port.connect(cov.{spec.base}_analysis_imp);"
        for spec in agent_specs
    )
    return f"""class {top}_env extends uvm_env;

    `uvm_component_utils({top}_env)

{fields}
    {top}_scoreboard sb;
    {top}_coverage cov;
    {top}_virtual_sequencer vseqr;

    function new(string name = "{top}_env", uvm_component parent = null);
        super.new(name, parent);
    endfunction

    function void build_phase(uvm_phase phase);
        super.build_phase(phase);
{active_sets}
{creates}
        sb = {top}_scoreboard::type_id::create("sb", this);
        cov = {top}_coverage::type_id::create("cov", this);
        vseqr = {top}_virtual_sequencer::type_id::create("vseqr", this);
    endfunction

    function void connect_phase(uvm_phase phase);
        super.connect_phase(phase);
{vseq_assigns}
{connects}
    endfunction

endclass : {top}_env
"""


def _gen_multi_base_test(top: str) -> str:
    return f"""class {top}_base_test extends uvm_test;

    `uvm_component_utils({top}_base_test)

    {top}_env env;

    function new(string name = "{top}_base_test", uvm_component parent = null);
        super.new(name, parent);
    endfunction

    function void build_phase(uvm_phase phase);
        super.build_phase(phase);
        env = {top}_env::type_id::create("env", this);
    endfunction

    task run_phase(uvm_phase phase);
        {top}_base_vseq seq;
        phase.raise_objection(this);
        seq = {top}_base_vseq::type_id::create("seq");
        seq.start(env.vseqr);
        #100;
        phase.drop_objection(this);
    endtask

endclass : {top}_base_test
"""


def _gen_scenario_test(top: str, test_name: str, scenario: Dict[str, Any]) -> str:
    description = _sv_comment(str(scenario.get("description") or f"Scenario test {test_name}"))
    class_name = f"{top}_{test_name}_test"
    scenario_kind = _scenario_kind_id(scenario)
    num_transactions = _scenario_transaction_count(scenario, scenario_kind)
    return f"""class {class_name} extends {top}_base_test;

    `uvm_component_utils({class_name})

    // {description}

    function new(string name = "{class_name}", uvm_component parent = null);
        super.new(name, parent);
    endfunction

    task run_phase(uvm_phase phase);
        {top}_base_vseq seq;
        phase.raise_objection(this);
        seq = {top}_base_vseq::type_id::create("seq");
        // Scenario intent: {description}
        seq.scenario_kind = {scenario_kind};
        seq.num_transactions = {num_transactions};
        seq.start(env.vseqr);
        #100;
        phase.drop_objection(this);
    endtask

endclass : {class_name}
"""


def _gen_multi_tests(top: str, scenarios: List[Any]) -> str:
    classes = [_gen_multi_base_test(top)]
    seen_names = {"base"}
    for index, scenario in enumerate(scenarios or []):
        scenario_dict = scenario if isinstance(scenario, dict) else _to_jsonable(scenario)
        if not isinstance(scenario_dict, dict):
            scenario_dict = {}
        test_name = _safe_sv_name(str(scenario_dict.get("name") or f"scenario_{index + 1}"))
        if not test_name or test_name in seen_names:
            test_name = f"scenario_{index + 1}"
        seen_names.add(test_name)
        classes.append(_gen_scenario_test(top, test_name, scenario_dict))
    return "\n\n".join(classes)


def _gen_multi_top(
    top: str,
    ports: list,
    clk: str,
    rst: str,
    rst_active_low: bool,
    params: list,
    agent_specs: List[UVMAgentSpec],
    include_assertions: bool = False,
) -> str:
    port_to_vif = {}
    for spec in agent_specs:
        for port in spec.ports:
            port_to_vif[_port_name(port)] = f"{spec.base}_vif"

    intf_instances = "\n".join(
        f"    {spec.base}_intf {spec.base}_vif(.{clk}({clk}), .{rst}({rst}));"
        for spec in agent_specs
    )
    port_connections = []
    for port in ports:
        name = _port_name(port)
        if name == clk or name == rst:
            port_connections.append(f"        .{name}({name})")
        else:
            vif_name = port_to_vif.get(name)
            port_connections.append(f"        .{name}({vif_name}.{name})" if vif_name else f"        .{name}()")
    assertion_signal_exprs = {clk: clk, rst: rst}
    for port in ports:
        name = _port_name(port)
        if name and name not in {clk, rst}:
            vif_name = port_to_vif.get(name)
            assertion_signal_exprs[name] = f"{vif_name}.{name}" if vif_name else name
    assertion_instance = (
        _gen_assertion_instance(top, ports, clk, rst, assertion_signal_exprs)
        if include_assertions else ""
    )
    config_sets = "\n".join(
        f'        uvm_config_db #(virtual {spec.base}_intf)::set(null, "uvm_test_top.env.{spec.base}_agt*", "vif", {spec.base}_vif);'
        for spec in agent_specs
    )
    rst_init = "1'b0" if rst_active_low else "1'b1"
    rst_deassert = "1'b1" if rst_active_low else "1'b0"
    port_connection_block = ",\n".join(port_connections)
    return f"""`timescale 1ns/1ps

module top_tb;
    import uvm_pkg::*;
    `include "uvm_macros.svh"
    import {top}_pkg::*;

    logic {clk};
    logic {rst};

{intf_instances}

    {top} dut (
{port_connection_block}
    );

{assertion_instance}

    initial begin
        {clk} = 0;
        forever #5 {clk} = ~{clk};
    end

    initial begin
        {rst} = {rst_init};
        #20;
        {rst} = {rst_deassert};
    end

    initial begin
{config_sets}
        run_test();
    end

    initial begin
        $dumpfile("{top}_uvm.vcd");
        $dumpvars(0, top_tb);
    end

endmodule : top_tb
"""


def _gen_multi_filelist(
    top: str,
    work: Path,
    interface_files: List[str],
    dut_rtl_files: List[str] | None = None,
    support_files: List[str] | None = None,
    package_include_files: List[str] | None = None,
) -> str:
    entries: List[str] = []
    entries.extend(_sv_filelist_entries(dut_rtl_files or []))
    entries.extend(_sv_filelist_entries(interface_files or []))
    entries.extend(_sv_filelist_entries(support_files or []))
    entries.extend([str(work / f"{top}_pkg.sv"), str(work / "top_tb.sv")])
    return _format_filelist(work, entries, package_include_files)


def _gen_makefile(top: str) -> str:
    return f"""FILELIST ?= {top}_uvm.f
TOP ?= top_tb
TEST ?= {top}_base_test

.PHONY: compile run clean

compile:
\tvlog -sv -f $(FILELIST)

run: compile
\tvsim -c $(TOP) +UVM_TESTNAME=$(TEST) -do "run -all; quit"

clean:
\trm -rf work transcript vsim.wlf *.vcd
"""


def _gen_assertions(top: str, ports: list, clk: str, rst: str) -> str:
    param_header = _sv_parameter_header(_collect_uvm_parameters([], ports))
    port_decls = _assertion_port_declarations(ports, clk, rst)
    rst_lower = str(rst or "").lower()
    rst_active_low = rst_lower.endswith("_n") or rst_lower.endswith("n") or "resetn" in rst_lower
    rst_condition = f"!{rst}" if rst_active_low else rst
    output_ports = [
        port
        for port in ports
        if _port_dir(port) == "output" and _port_name(port) not in {clk, rst}
    ]
    xz_assertions = "\n\n".join(
        f"""    property p_no_xz_{_port_name(port)};
        @(posedge {clk}) disable iff ({rst_condition})
        !$isunknown({_port_name(port)});
    endproperty
    a_no_xz_{_port_name(port)}: assert property(p_no_xz_{_port_name(port)});"""
        for port in output_ports
        if _port_name(port)
    )
    protocol_assertions = _protocol_assertion_lines(ports, clk, rst_condition)
    assertion_blocks = "\n\n".join(
        block
        for block in [xz_assertions, protocol_assertions]
        if block.strip()
    )
    return f"""module {top}_assertions{param_header}(
{port_decls}
);

{assertion_blocks if assertion_blocks else "    // No DUT output ports were available for generated assertions."}

endmodule : {top}_assertions
"""


def _assertion_port_entries(ports: list, clk: str, rst: str) -> List[tuple[str, str]]:
    by_name = {_port_name(port): port for port in ports if _port_name(port)}
    ordered_names: List[str] = []
    for name in [clk, rst] + [_port_name(port) for port in ports]:
        if name and name not in ordered_names:
            ordered_names.append(name)
    entries: List[tuple[str, str]] = []
    for name in ordered_names:
        port = by_name.get(name)
        bus = _port_bus_str(port) if port is not None else ""
        entries.append((name, bus))
    return entries


def _assertion_port_declarations(ports: list, clk: str, rst: str) -> str:
    entries = _assertion_port_entries(ports, clk, rst)
    if not entries:
        return "    input logic clk"
    return ",\n".join(
        f"    input logic {bus}{name}"
        for name, bus in entries
    )


def _protocol_assertion_lines(ports: list, clk: str, rst_condition: str) -> str:
    spec = UVMAgentSpec(
        name="assertion_agent",
        base="dut",
        agent_type="passive",
        ports=list(ports or []),
        description="Assertion view of DUT interface",
    )
    kind = _agent_protocol_kind(spec)
    blocks: List[str] = []

    def prop(name: str, expr: str) -> None:
        safe = _safe_sv_name(name)
        blocks.append(
            f"""    property p_{safe};
        @(posedge {clk}) disable iff ({rst_condition})
        {expr};
    endproperty
    a_{safe}: assert property(p_{safe});"""
        )

    if kind == "apb":
        psel = _find_port_name(spec.ports, exact=("psel",), suffixes=("psel",))
        penable = _find_port_name(spec.ports, exact=("penable",), suffixes=("penable",))
        pready = _find_port_name(spec.ports, exact=("pready",), suffixes=("pready",))
        pwrite = _find_port_name(spec.ports, exact=("pwrite",), suffixes=("pwrite",))
        paddr = _find_port_name(spec.ports, exact=("paddr",), suffixes=("paddr",))
        pwdata = _find_port_name(spec.ports, exact=("pwdata",), suffixes=("pwdata", "wdata"))
        pslverr = _find_port_name(spec.ports, exact=("pslverr",), suffixes=("pslverr", "slverr"))

        if psel and penable:
            prop("apb_enable_requires_select", f"{penable} |-> {psel}")
        if psel and penable and pready and pslverr:
            prop("apb_error_only_on_complete", f"{pslverr} |-> ({psel} && {penable} && {pready})")
        stable_signals = [name for name in [paddr, pwrite, pwdata] if name]
        if psel and penable and pready and stable_signals:
            stable_expr = " && ".join(f"$stable({name})" for name in stable_signals)
            prop(
                "apb_control_stable_while_waiting",
                f"({psel} && {penable} && !{pready}) |=> ({stable_expr})",
            )

    pairs: List[tuple[str, str, str]] = []
    if kind == "tilelink_ul":
        for label, valid_names, ready_names in [
            ("tlul_a", ("a_valid", "avalid"), ("a_ready", "aready")),
            ("tlul_d", ("d_valid", "dvalid"), ("d_ready", "dready")),
        ]:
            valid = _find_port_name(spec.ports, exact=valid_names, suffixes=valid_names)
            ready = _find_port_name(spec.ports, exact=ready_names, suffixes=ready_names)
            if valid and ready:
                pairs.append((label, valid, ready))
    elif kind == "valid_ready":
        valid = _find_port_name(spec.ports, suffixes=("valid",))
        ready = _find_port_name(spec.ports, suffixes=("ready",))
        if valid and ready:
            pairs.append(("valid_ready", valid, ready))

    for label, valid, ready in pairs:
        payloads = _payload_ports_for_valid_ready(spec, valid, ready)[:5]
        if payloads:
            stable_expr = " && ".join(f"$stable({_port_name(port)})" for port in payloads if _port_name(port))
            prop(f"{label}_payload_stable_while_stalled", f"({valid} && !{ready}) |=> ({valid} && {stable_expr})")
        else:
            prop(f"{label}_valid_held_while_stalled", f"({valid} && !{ready}) |=> {valid}")

    return "\n\n".join(blocks)


def _gen_assertion_instance(
    top: str,
    ports: list,
    clk: str,
    rst: str,
    signal_exprs: Dict[str, str],
) -> str:
    connections = []
    for name, _width in _assertion_port_entries(ports, clk, rst):
        expr = signal_exprs.get(name, name)
        connections.append(f"        .{name}({expr})")
    if not connections:
        return ""
    connection_block = ",\n".join(connections)
    return f"""    {top}_assertions u_{top}_assertions (
{connection_block}
    );
"""


def _sv_filelist_entries(paths: List[str]) -> List[str]:
    entries: List[str] = []
    for raw in paths or []:
        path = Path(str(raw))
        if path.suffix.lower() not in {".v", ".sv", ".svh"}:
            continue
        entries.append(str(path))
    return entries


def _format_filelist(
    work: Path,
    entries: List[str],
    package_include_files: List[str] | None = None,
) -> str:
    unique_entries = list(dict.fromkeys(str(Path(entry)) for entry in entries if str(entry).strip()))
    file_lines = "\n".join(unique_entries)
    include_dirs = [str(work)]
    for entry in unique_entries:
        path = Path(entry)
        if path.suffix.lower() in {".v", ".sv", ".svh", ".vh"}:
            parent = str(path.parent)
            if parent and parent not in include_dirs:
                include_dirs.append(parent)
    package_entries = list(dict.fromkeys(
        str(Path(entry))
        for entry in (package_include_files or [])
        if str(entry).strip()
    ))
    package_comment = ""
    if package_entries:
        package_comment = (
            "\n// Package-included UVM sources. They are listed here for review,\n"
            "// but are compiled through the generated package to keep classes scoped once.\n"
            + "\n".join(f"// include: {entry}" for entry in package_entries)
            + "\n"
        )
    incdir_lines = "\n".join(f"+incdir+{directory}" for directory in include_dirs)
    return f"{incdir_lines}\n{file_lines}\n{package_comment}"


def _gen_ral(top: str, register_fields: list) -> str:
    field_comments = "\n".join(
        f"    // Register field: {_sv_comment(str(_dict_get(field, 'name', f'field_{index + 1}')))}"
        for index, field in enumerate(register_fields or [])
    )
    return f"""import uvm_pkg::*;
`include "uvm_macros.svh"

class {top}_reg_block extends uvm_object;
    `uvm_object_utils({top}_reg_block)

{field_comments if field_comments else "    // No register fields were discovered in TruthCore."}

    function new(string name = "{top}_reg_block");
        super.new(name);
    endfunction

endclass : {top}_reg_block
"""


def _gen_uvm_test_plan_doc(
    top: str,
    scenarios: List[Any],
    scoreboard_checks: List[Any],
    coverage_points: List[Any],
) -> str:
    def _rows(items: List[Any], name_key: str) -> str:
        rows = []
        for index, item in enumerate(items or [], start=1):
            data = item if isinstance(item, dict) else _to_jsonable(item)
            if not isinstance(data, dict):
                data = {}
            name = str(data.get(name_key) or data.get("name") or f"item_{index}")
            desc = str(data.get("description") or "")
            reqs = ", ".join(str(req) for req in data.get("requirement_ids", []) or [])
            rows.append(f"| {index} | {_md_cell(name)} | {_md_cell(desc)} | {_md_cell(reqs)} |")
        return "\n".join(rows) if rows else "| - | None captured | - | - |"

    return f"""# UVM Test Plan: {top}

Generated from the approved mental-model-backed UVM plan.

## Sequences

| # | Name | Description | Requirements |
|---|---|---|---|
{_rows(list(scenarios or []), "name")}

## Scoreboard Checks

| # | Name | Description | Requirements |
|---|---|---|---|
{_rows(list(scoreboard_checks or []), "check")}

## Coverage Points

| # | Name | Description | Requirements |
|---|---|---|---|
{_rows(list(coverage_points or []), "point")}
"""


def _md_cell(text: str) -> str:
    return str(text or "").replace("|", "\\|").replace("\n", " ")[:300]


def _sv_comment(text: str) -> str:
    return re.sub(r"[\r\n]+", " ", text or "").replace("*/", "* /")[:240]


def _gen_package(
    top: str,
    ports: list,
    params: list,
    sequence_files: Optional[List[str]] = None,
    test_file: Optional[str] = None,
) -> str:
    TOP = top.upper()
    param_lines = "\n".join(
        f"    parameter int {_param_name(p)} = {_param_default(p)};"
        for p in params
        if _param_name(p)
    )
    sequence_includes = "\n".join(
        f'    `include "{Path(seq_file).name}"'
        for seq_file in (sequence_files or [f"{top}_seq_lib.sv"])
    )
    test_include = test_file or f"{top}_tests.sv"
    return f"""`ifndef {TOP}_PKG_SV
`define {TOP}_PKG_SV

package {top}_pkg;
    import uvm_pkg::*;
    `include "uvm_macros.svh"

    // Parameters
{param_lines if param_lines else "    // (no parameters)"}

    // Forward declarations
    typedef class {top}_seq_item;
    typedef class {top}_sequencer;
    typedef class {top}_driver;
    typedef class {top}_monitor;
    typedef class {top}_scoreboard;
    typedef class {top}_agent;
    typedef class {top}_env;

    `include "{top}_seq_item.sv"
{sequence_includes}
    `include "{top}_sequencer.sv"
    `include "{top}_driver.sv"
    `include "{top}_monitor.sv"
    `include "{top}_scoreboard.sv"
    `include "{top}_agent.sv"
    `include "{top}_coverage.sv"
    `include "{top}_env.sv"
    `include "{test_include}"

endpackage : {top}_pkg

`endif
"""


def _gen_interface(top: str, ports: list, clk: str, rst: str) -> str:
    param_header = _sv_parameter_header(_collect_uvm_parameters([], ports))
    signal_lines = []
    for p in ports:
        name = _port_name(p)
        if name in (clk, rst):
            continue
        bus = _port_bus_str(p)
        signal_lines.append(f"    logic {bus}{name};")

    signals = "\n".join(signal_lines)
    return f"""interface {top}_if{param_header}(input logic {clk}, input logic {rst});

{signals}

    // Clocking blocks
    clocking driver_cb @(posedge {clk});
        default input #1 output #1;
{chr(10).join(f"        output {_port_name(p)};" for p in ports if _port_dir(p) == "input" and _port_name(p) not in (clk, rst))}
{chr(10).join(f"        input  {_port_name(p)};" for p in ports if _port_dir(p) == "output")}
    endclocking

    clocking monitor_cb @(posedge {clk});
        default input #1;
{chr(10).join(f"        input {_port_name(p)};" for p in ports if _port_name(p) not in (clk, rst))}
    endclocking

    modport driver_mp(clocking driver_cb, input {clk}, input {rst});
    modport monitor_mp(clocking monitor_cb, input {clk}, input {rst});

endinterface : {top}_if
"""


def _gen_seq_item(
    top: str,
    inputs: list,
    outputs: list,
    reference_data: Dict[str, Any] | None = None,
) -> str:
    in_fields = "\n".join(
        f"    rand logic {_port_bus_str(p)}{_port_name(p)};"
        for p in inputs
    )
    out_fields = "\n".join(
        f"    logic {_port_bus_str(p)}{_port_name(p)};"
        for p in outputs
    )
    constraint_lines = _seq_item_constraint_lines(inputs, reference_data)
    return f"""class {top}_seq_item extends uvm_sequence_item;

    // Input fields (randomizable)
{in_fields if in_fields else "    // (no input ports)"}

    // Output fields (observed)
{out_fields if out_fields else "    // (no output ports)"}

    `uvm_object_utils_begin({top}_seq_item)
{chr(10).join(f"        `uvm_field_int({_port_name(p)}, UVM_ALL_ON)" for p in inputs + outputs)}
    `uvm_object_utils_end

    function new(string name = "{top}_seq_item");
        super.new(name);
    endfunction

    constraint reasonable_values {{
{chr(10).join(f"        {line}" for line in constraint_lines) if constraint_lines else "        // Add design-specific constraints here"}
    }}

endclass : {top}_seq_item
"""


def _gen_sequencer(top: str) -> str:
    return f"""class {top}_sequencer extends uvm_sequencer #({top}_seq_item);

    `uvm_component_utils({top}_sequencer)

    function new(string name = "{top}_sequencer", uvm_component parent = null);
        super.new(name, parent);
    endfunction

endclass : {top}_sequencer
"""


def _gen_driver(
    top: str,
    inputs: list,
    outputs: list,
    clk: str,
    rst: str,
    rst_active_low: bool,
    protocols: List[Any] | None = None,
    reference_data: Dict[str, Any] | None = None,
) -> str:
    driver_spec = UVMAgentSpec(
        name=f"{top}_agent",
        base=top,
        agent_type="active",
        ports=list(inputs or []) + list(outputs or []),
        description="Single DUT interface agent",
        protocols=list(protocols or []),
        constraints=_reference_constraint_texts(reference_data),
    )
    drive_lines = _protocol_driver_body(driver_spec)
    rst_val = "1'b0" if rst_active_low else "1'b1"
    rst_deassert = "1'b1" if rst_active_low else "1'b0"
    return f"""class {top}_driver extends uvm_driver #({top}_seq_item);

    `uvm_component_utils({top}_driver)

    virtual {top}_if vif;

    function new(string name = "{top}_driver", uvm_component parent = null);
        super.new(name, parent);
    endfunction

    function void build_phase(uvm_phase phase);
        super.build_phase(phase);
        if (!uvm_config_db #(virtual {top}_if)::get(this, "", "vif", vif))
            `uvm_fatal(get_name(), "Could not get virtual interface")
    endfunction

    task run_phase(uvm_phase phase);
        {top}_seq_item req;
        forever begin
            seq_item_port.get_next_item(req);
            drive_item(req);
            seq_item_port.item_done();
        end
    endtask

    task drive_item({top}_seq_item item);
{drive_lines if drive_lines else "        @(vif.driver_cb);"}
    endtask

endclass : {top}_driver
"""


def _gen_monitor(
    top: str,
    inputs: list,
    outputs: list,
    clk: str,
    protocols: List[Any] | None = None,
    reference_data: Dict[str, Any] | None = None,
) -> str:
    sample_lines = "\n".join(
        f"            item.{_port_name(p)} = vif.monitor_cb.{_port_name(p)};"
        for p in inputs + outputs
    )
    monitor_spec = UVMAgentSpec(
        name=f"{top}_agent",
        base=top,
        agent_type="active",
        ports=list(inputs or []) + list(outputs or []),
        description="Single DUT interface monitor",
        protocols=list(protocols or []),
        constraints=_reference_constraint_texts(reference_data),
    )
    guard_expr = _protocol_monitor_guard_expr(monitor_spec)
    if guard_expr:
        body = f"""            if ({guard_expr}) begin
                {top}_seq_item item = {top}_seq_item::type_id::create("item");
{sample_lines if sample_lines else "                // No fields to sample"}
                analysis_port.write(item);
            end"""
    else:
        body = f"""            {top}_seq_item item = {top}_seq_item::type_id::create("item");
{sample_lines if sample_lines else "            // No fields to sample"}
            analysis_port.write(item);"""
    return f"""class {top}_monitor extends uvm_monitor;

    `uvm_component_utils({top}_monitor)

    virtual {top}_if vif;
    uvm_analysis_port #({top}_seq_item) analysis_port;

    function new(string name = "{top}_monitor", uvm_component parent = null);
        super.new(name, parent);
    endfunction

    function void build_phase(uvm_phase phase);
        super.build_phase(phase);
        analysis_port = new("analysis_port", this);
        if (!uvm_config_db #(virtual {top}_if)::get(this, "", "vif", vif))
            `uvm_fatal(get_name(), "Could not get virtual interface")
    endfunction

    task run_phase(uvm_phase phase);
        forever begin
            @(vif.monitor_cb);
{body}
        end
    endtask

endclass : {top}_monitor
"""


def _gen_scoreboard(
    top: str,
    inputs: list,
    outputs: list,
    approved_checks: list | None = None,
    reference_data: Dict[str, Any] | None = None,
    protocols: List[Any] | None = None,
) -> str:
    scoreboard_spec = UVMAgentSpec(
        name=f"{top}_agent",
        base=top,
        agent_type="active",
        ports=list(inputs or []) + list(outputs or []),
        description="Single DUT interface agent",
        protocols=list(protocols or []),
        constraints=_reference_constraint_texts(reference_data),
    )
    item_ports = {
        _port_name(port)
        for port in (inputs or []) + (outputs or [])
        if _port_name(port)
    }
    output_names = [_port_name(port) for port in outputs if _port_name(port)]
    reference_sections = _scoreboard_reference_sections(
        item_type=f"{top}_seq_item",
        item_ports=item_ports,
        output_names=output_names,
        reference_data=reference_data,
    )
    fallback_checks = _scoreboard_known_output_lines(output_names)
    approved_lines = "\n".join(
        f'        // Approved plan check: {_dict_get(check, "check", "check")} - {_dict_get(check, "description", "")}'
        for check in (approved_checks or [])
    )
    approved_check_lines = _approved_scoreboard_check_lines(
        approved_checks or [],
        output_names,
    )
    protocol_decls = _protocol_scoreboard_decls(scoreboard_spec)
    protocol_lines = _protocol_scoreboard_lines(scoreboard_spec)
    return f"""class {top}_scoreboard extends uvm_scoreboard;

    `uvm_component_utils({top}_scoreboard)

    uvm_analysis_imp #({top}_seq_item, {top}_scoreboard) analysis_imp;
    int pass_count = 0;
    int fail_count = 0;
{reference_sections["class_decls"]}
{protocol_decls}

    function new(string name = "{top}_scoreboard", uvm_component parent = null);
        super.new(name, parent);
    endfunction

    function void build_phase(uvm_phase phase);
        super.build_phase(phase);
        analysis_imp = new("analysis_imp", this);
{reference_sections["build_lines"]}
    endfunction

{reference_sections["helper_functions"]}

    function void write({top}_seq_item item);
        bit matched_behavior;
        matched_behavior = 1'b0;
{reference_sections["write_lines"]}
{protocol_lines}
{approved_check_lines}
        if (!matched_behavior) begin
{fallback_checks}
        end
{approved_lines}
    endfunction

    function void report_phase(uvm_phase phase);
        `uvm_info(get_name(), $sformatf("Scoreboard: %0d PASS, %0d FAIL", pass_count, fail_count), UVM_LOW)
    endfunction

endclass : {top}_scoreboard
"""


def _gen_agent(top: str, agent_plan: Optional[Any] = None) -> str:
    agent_note = ""
    if agent_plan:
        agent_name = _dict_get(agent_plan, "name", f"{top}_agent")
        agent_type = _dict_get(agent_plan, "type", _dict_get(agent_plan, "agent_type", "active"))
        description = _sv_comment(_dict_get(agent_plan, "description", ""))
        ports = _obj_get(agent_plan, "ports", [])
        port_note = ", ".join(str(port) for port in (ports or [])[:12])
        agent_note = "\n".join([
            f"    // Approved plan agent: {_sv_comment(agent_name)} ({_sv_comment(agent_type)})",
            f"    // Responsibility: {description or 'single DUT interface agent'}",
            f"    // Planned ports: {_sv_comment(port_note) if port_note else 'all DUT interface ports'}",
            "",
        ])
    return f"""class {top}_agent extends uvm_agent;

    `uvm_component_utils({top}_agent)

{agent_note}
    {top}_driver   drv;
    {top}_monitor  mon;
    {top}_sequencer sqr;

    function new(string name = "{top}_agent", uvm_component parent = null);
        super.new(name, parent);
    endfunction

    function void build_phase(uvm_phase phase);
        super.build_phase(phase);
        mon = {top}_monitor::type_id::create("mon", this);
        if (get_is_active() == UVM_ACTIVE) begin
            drv = {top}_driver::type_id::create("drv", this);
            sqr = {top}_sequencer::type_id::create("sqr", this);
        end
    endfunction

    function void connect_phase(uvm_phase phase);
        super.connect_phase(phase);
        if (get_is_active() == UVM_ACTIVE) begin
            drv.seq_item_port.connect(sqr.seq_item_export);
        end
    endfunction

endclass : {top}_agent
"""


def _gen_env(top: str) -> str:
    return f"""class {top}_env extends uvm_env;

    `uvm_component_utils({top}_env)

    {top}_agent      agt;
    {top}_scoreboard sb;
    {top}_coverage   cov;

    function new(string name = "{top}_env", uvm_component parent = null);
        super.new(name, parent);
    endfunction

    function void build_phase(uvm_phase phase);
        super.build_phase(phase);
        agt = {top}_agent::type_id::create("agt", this);
        sb  = {top}_scoreboard::type_id::create("sb", this);
        cov = {top}_coverage::type_id::create("cov", this);
    endfunction

    function void connect_phase(uvm_phase phase);
        super.connect_phase(phase);
        agt.mon.analysis_port.connect(sb.analysis_imp);
        agt.mon.analysis_port.connect(cov.analysis_export);
    endfunction

endclass : {top}_env
"""


def _gen_coverage(top: str, inputs: list, outputs: list, fsms: list, approved_points: list | None = None) -> str:
    coverage_spec = UVMAgentSpec(
        name=f"{top}_agent",
        base=top,
        agent_type="active",
        ports=list(inputs or []) + list(outputs or []),
        description="Single DUT interface agent",
    )
    cp_lines = []
    for p in inputs[:10]:
        name = _port_name(p)
        width = _port_width(p)
        if width <= 8:
            cp_lines.append(f"            {name}_cp: coverpoint item.{name};")
        else:
            cp_lines.append(f"            {name}_cp: coverpoint item.{name} {{ option.auto_bin_max = 16; }}")

    for p in outputs[:10]:
        name = _port_name(p)
        cp_lines.append(f"            {name}_cp: coverpoint item.{name} {{ option.auto_bin_max = 16; }}")

    for point in approved_points or []:
        point_name = _safe_sv_name(_dict_get(point, "point", "approved_point"))
        description = _dict_get(point, "description", "")
        signal = _dict_get(point, "signal", "")
        available = {_port_name(port) for port in (inputs or []) + (outputs or [])}
        if signal in available:
            cp_lines.append(f"            {point_name}_cp: coverpoint item.{signal};")
        cp_lines.append(f"            // Approved plan coverage: {point_name} - {description}")

    cp_lines.extend(_protocol_coverage_lines(coverage_spec, indent="            "))
    coverpoints = "\n".join(cp_lines)
    return f"""class {top}_coverage extends uvm_subscriber #({top}_seq_item);

    `uvm_component_utils({top}_coverage)

    covergroup {top}_cg with function sample({top}_seq_item item);
        option.per_instance = 1;
{coverpoints}
    endgroup

    function new(string name = "{top}_coverage", uvm_component parent = null);
        super.new(name, parent);
        {top}_cg = new();
    endfunction

    function void write({top}_seq_item t);
        {top}_cg.sample(t);
    endfunction

endclass : {top}_coverage
"""


def _limit_items(items: list, limit: int) -> list:
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        limit = len(items or [])
    return list(items or [])[: max(0, limit)]


def _build_sequence_specs(top: str, scenarios: list, limits: Any) -> list:
    max_sequences = getattr(limits, "max_sequence_classes", 6)
    try:
        max_sequences = int(max_sequences)
    except (TypeError, ValueError):
        max_sequences = 6

    specs = [{
        "name": "base_seq",
        "scenario": {
            "description": f"Base smoke sequence for {top}",
            "num_transactions": 25,
        },
    }]
    seen_names = {"base_seq"}
    seen_descriptions = {f"Base smoke sequence for {top}".lower()}

    for i, scenario in enumerate(list(scenarios or [])):
        if len(specs) >= max(1, max_sequences):
            break
        scenario_dict = scenario if isinstance(scenario, dict) else _to_jsonable(scenario)
        if not isinstance(scenario_dict, dict):
            scenario_dict = {}
        description = str(scenario_dict.get("description") or "").strip().lower()
        if getattr(limits, "deduplicate_scenarios", True) and description and description in seen_descriptions:
            continue
        raw_name = scenario_dict.get("name") or f"seq_{i}"
        name = _safe_sv_name(raw_name)
        if not name or name in seen_names:
            name = f"seq_{i}"
        seen_names.add(name)
        if description:
            seen_descriptions.add(description)
        specs.append({"name": name, "scenario": scenario_dict})
    return specs


def _build_regression_manifest(
    *,
    top: str,
    sequence_specs: list,
    scoreboard_checks: list,
    coverage_points: list,
    assertions: list,
    multi_agent: bool = False,
) -> Dict[str, Any]:
    """Describe the exact generated tests and their requirement evidence contract."""
    tests: List[Dict[str, Any]] = []
    for index, spec in enumerate(sequence_specs or []):
        scenario = spec.get("scenario") if isinstance(spec, dict) else {}
        scenario = scenario if isinstance(scenario, dict) else _to_jsonable(scenario)
        scenario = scenario if isinstance(scenario, dict) else {}
        name = _safe_sv_name(str(spec.get("name") or f"scenario_{index}"))
        is_base = index == 0 or name in {"base", "base_seq"}
        test_id = str(scenario.get("id") or ("base" if is_base else name))
        requirement_ids = [str(value) for value in scenario.get("requirement_ids", []) if str(value).strip()]
        test_class = f"{top}_base_test" if is_base else f"{top}_{name}_test"
        tests.append({
            "id": test_id,
            "name": "base_smoke" if is_base else name,
            "test_class": test_class,
            "description": str(scenario.get("description") or ("Base smoke test" if is_base else name)),
            "priority": str(scenario.get("priority") or ("high" if is_base else "medium")),
            "sequence_type": str(scenario.get("sequence_type") or scenario.get("category") or "directed"),
            "requirement_ids": requirement_ids,
            "checkers": _manifest_evidence_ids(scoreboard_checks, requirement_ids, ("id", "check", "name"), "CHK"),
            "assertions": _manifest_evidence_ids(assertions, requirement_ids, ("id", "name", "property"), "SVA"),
            "coverpoints": _manifest_evidence_ids(coverage_points, requirement_ids, ("id", "point", "name"), "COV"),
        })
    functional_targets = []
    for point in coverage_points or []:
        try:
            functional_targets.append(float(_dict_get(point, "target_percentage", 100.0)))
        except (TypeError, ValueError):
            continue
    return {
        "schema_version": 1,
        "top_module": "top_tb",
        "dut_module": top,
        "execution": "sequential",
        "replay_failures": True,
        "seed_policy": "sha256(run_id:test_id) positive 32-bit",
        "multi_agent": multi_agent,
        "coverage_targets": {
            "functional": max(functional_targets) if functional_targets else 100.0,
            "code": 90.0,
        },
        "tests": tests,
    }


def _manifest_evidence_ids(
    items: list,
    requirement_ids: List[str],
    name_fields: tuple[str, ...],
    prefix: str,
) -> List[str]:
    values: List[str] = []
    wanted = set(requirement_ids)
    for index, item in enumerate(items or [], start=1):
        raw = item if isinstance(item, dict) else _to_jsonable(item)
        raw = raw if isinstance(raw, dict) else {}
        linked = {str(value) for value in raw.get("requirement_ids", [])}
        if wanted and linked and not (wanted & linked):
            continue
        name = next((str(raw.get(field) or "").strip() for field in name_fields if raw.get(field)), "")
        values.append(name or f"{prefix}-{index:03d}")
    return list(dict.fromkeys(values))


def _normalize_top_run_test(path_value: str) -> None:
    """Keep runtime test selection under +UVM_TESTNAME after LLM enhancement."""
    path = Path(path_value or "")
    if not path.exists():
        return
    text = path.read_text(encoding="utf-8", errors="ignore")
    normalized = re.sub(r"\brun_test\s*\(\s*\"[^\"]*\"\s*\)\s*;", "run_test();", text)
    if normalized != text:
        path.write_text(normalized, encoding="utf-8")


def _gen_consolidated_seq_lib(top: str, sequence_specs: list, inputs: list) -> str:
    classes = [
        _gen_sequence(top, spec["name"], spec.get("scenario") or {}, inputs)
        for spec in sequence_specs
    ]
    return "\n\n".join(classes)


def _gen_test(top: str, reqs: list, sequence_specs: list) -> str:
    scenario_tests = "\n\n".join(
        _gen_single_scenario_test(top, spec.get("name", ""), spec.get("scenario") or {})
        for spec in (sequence_specs or [])[1:]
        if spec.get("name")
    )
    return f"""class {top}_base_test extends uvm_test;

    `uvm_component_utils({top}_base_test)

    {top}_env env;

    function new(string name = "{top}_base_test", uvm_component parent = null);
        super.new(name, parent);
    endfunction

    function void build_phase(uvm_phase phase);
        super.build_phase(phase);
        env = {top}_env::type_id::create("env", this);
    endfunction

    task run_phase(uvm_phase phase);
        {top}_base_seq seq;
        phase.raise_objection(this);
        seq = {top}_base_seq::type_id::create("seq");
        seq.start(env.agt.sqr);
        #100;
        phase.drop_objection(this);
    endtask

endclass : {top}_base_test

{scenario_tests}
"""


def _gen_sequence(top: str, name: str, scenario: dict, inputs: list) -> str:
    desc = scenario.get("description", f"Base sequence for {top}")
    scenario_kind = _scenario_kind_id(scenario)
    count = scenario.get("num_transactions", _scenario_transaction_count(scenario, scenario_kind))
    randomize_block = _directed_randomize_block("item", inputs, scenario_kind, "            ")
    return f"""class {top}_{name} extends uvm_sequence #({top}_seq_item);

    `uvm_object_utils({top}_{name})

    // {desc}
    int num_transactions = {count};

    function new(string name = "{top}_{name}");
        super.new(name);
    endfunction

    task body();
        {top}_seq_item item;
        repeat (num_transactions) begin
            item = {top}_seq_item::type_id::create("item");
            start_item(item);
{randomize_block}
            finish_item(item);
        end
    endtask

endclass : {top}_{name}
"""


def _gen_single_scenario_test(top: str, seq_name: str, scenario: dict) -> str:
    safe_seq = _safe_sv_name(seq_name)
    if not safe_seq or safe_seq == "base_seq":
        return ""
    description = _sv_comment(str(scenario.get("description") or f"Scenario {safe_seq}"))
    class_name = f"{top}_{safe_seq}_test"
    seq_class = f"{top}_{safe_seq}"
    return f"""class {class_name} extends {top}_base_test;

    `uvm_component_utils({class_name})

    // {description}

    function new(string name = "{class_name}", uvm_component parent = null);
        super.new(name, parent);
    endfunction

    task run_phase(uvm_phase phase);
        {seq_class} seq;
        phase.raise_objection(this);
        seq = {seq_class}::type_id::create("seq");
        seq.start(env.agt.sqr);
        #100;
        phase.drop_objection(this);
    endtask

endclass : {class_name}
"""


def _gen_top(
    top: str,
    ports: list,
    clk: str,
    rst: str,
    rst_active_low: bool,
    params: list,
    include_assertions: bool = False,
) -> str:
    port_connections = []
    for p in ports:
        name = _port_name(p)
        if name == clk:
            port_connections.append(f"        .{name}({name})")
        elif name == rst:
            port_connections.append(f"        .{name}({name})")
        else:
            port_connections.append(f"        .{name}(intf.{name})")

    connections = ",\n".join(port_connections)
    rst_init = "1'b0" if rst_active_low else "1'b1"
    rst_deassert = "1'b1" if rst_active_low else "1'b0"
    assertion_signal_exprs = {clk: clk, rst: rst}
    for port in ports:
        name = _port_name(port)
        if name and name not in {clk, rst}:
            assertion_signal_exprs[name] = f"intf.{name}"
    assertion_instance = (
        _gen_assertion_instance(top, ports, clk, rst, assertion_signal_exprs)
        if include_assertions else ""
    )

    return f"""`timescale 1ns/1ps

module top_tb;
    import uvm_pkg::*;
    `include "uvm_macros.svh"
    import {top}_pkg::*;

    // Clock and reset
    logic {clk};
    logic {rst};

    // Interface
    {top}_if intf(.{clk}({clk}), .{rst}({rst}));

    // DUT instantiation
    {top} dut (
{connections}
    );

{assertion_instance}

    // Clock generation
    initial begin
        {clk} = 0;
        forever #5 {clk} = ~{clk};
    end

    // Reset sequence
    initial begin
        {rst} = {rst_init};
        #20;
        {rst} = {rst_deassert};
    end

    // UVM setup
    initial begin
        uvm_config_db #(virtual {top}_if)::set(null, "*", "vif", intf);
        run_test();
    end

    // Waveform dump
    initial begin
        $dumpfile("{top}_uvm.vcd");
        $dumpvars(0, top_tb);
    end

endmodule : top_tb
"""


def _gen_filelist(
    top: str,
    work: Path,
    dut_rtl_files: List[str] | None = None,
    support_files: List[str] | None = None,
    package_include_files: List[str] | None = None,
) -> str:
    entries: List[str] = []
    entries.extend(_sv_filelist_entries(dut_rtl_files or []))
    entries.append(str(work / f"{top}_if.sv"))
    entries.extend(_sv_filelist_entries(support_files or []))
    entries.extend([str(work / f"{top}_pkg.sv"), str(work / "top_tb.sv")])
    return _format_filelist(work, entries, package_include_files)


# ─── Helpers ─────────────────────────────────────────────────────────

def _write(work: Path, filename: str, content: str) -> str:
    path = work / filename
    if filename.lower().endswith((".sv", ".svh")):
        content = _sanitize_systemverilog_text(content)
    path.write_text(content, encoding="utf-8")
    _emit_uvm_progress({
        "type": "file_generated",
        "file": str(path),
        "message": f"Generated {filename}",
    })
    return str(path)


def _get_dut_rtl_files(model: Any) -> List[str]:
    """Recover source RTL paths from the mental-model scan for filelist generation."""
    try:
        data = model if isinstance(model, dict) else _to_jsonable(model)
    except Exception:
        data = {}
    if not isinstance(data, dict):
        return []

    candidates: List[Path] = []
    scan = data.get("project_scan", {})
    if isinstance(scan, dict):
        root = Path(str(scan.get("root_path") or "")) if scan.get("root_path") else None
        for raw in scan.get("rtl_files", []) or []:
            path = Path(str(raw))
            if not path.is_absolute() and root is not None:
                path = root / path
            candidates.append(path)

    sources = data.get("sources", {})
    rtl_source = sources.get("rtl", {}) if isinstance(sources, dict) else {}
    if isinstance(rtl_source, dict):
        raw_path = rtl_source.get("file_path") or rtl_source.get("path")
        if raw_path:
            source_path = Path(str(raw_path))
            if source_path.is_dir():
                for suffix in ("*.sv", "*.v", "*.svh"):
                    candidates.extend(source_path.rglob(suffix))
            else:
                candidates.append(source_path)

    result: List[str] = []
    for path in candidates:
        if path.suffix.lower() not in {".v", ".sv", ".svh"}:
            continue
        try:
            resolved = str(path.resolve()) if path.exists() else str(path)
        except OSError:
            resolved = str(path)
        if resolved not in result:
            result.append(resolved)
    return _drop_redundant_include_aggregates(result)


def _drop_redundant_include_aggregates(paths: List[str]) -> List[str]:
    """Drop include-only wrapper files when their included RTL files are listed.

    Some uploaded projects contain a ``design.sv`` file that only includes every
    RTL module. Listing that wrapper plus the individual module files can create
    duplicate module definitions in stricter simulators. Keep the wrapper only
    when it contributes non-include source text or references files that are not
    otherwise present.
    """
    names = {Path(path).name for path in paths}
    filtered: List[str] = []
    for raw in paths:
        path = Path(raw)
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            filtered.append(raw)
            continue
        include_names = re.findall(r"`include\s+\"([^\"]+)\"", text)
        if not include_names:
            filtered.append(raw)
            continue
        stripped = re.sub(r"`include\s+\"[^\"]+\"", "", text)
        stripped = re.sub(r"//.*", "", stripped)
        stripped = re.sub(r"/\*.*?\*/", "", stripped, flags=re.S).strip()
        included_present = all(Path(name).name in names for name in include_names)
        if stripped or not included_present:
            filtered.append(raw)
    return filtered


def _get_design(model: Any) -> dict:
    if isinstance(model, dict):
        return model.get("design", {})
    if hasattr(model, "design"):
        d = model.design
        if isinstance(d, dict):
            return d
        converted = _to_jsonable(d)
        return converted if isinstance(converted, dict) else {}
    return {}


def _get_requirements(model: Any) -> list:
    if isinstance(model, dict):
        return model.get("requirements", [])
    return getattr(model, "requirements", []) or []


def _get_uvm_scenarios(model: Any) -> list:
    if isinstance(model, dict):
        v = model.get("verification", {})
        return v.get("uvm_scenarios", []) if isinstance(v, dict) else []
    v = getattr(model, "verification", None)
    if v:
        scenarios = getattr(v, "uvm_scenarios", [])
        result = []
        for s in scenarios:
            if hasattr(s, "__dict__"):
                result.append(s.__dict__)
            elif isinstance(s, dict):
                result.append(s)
        return result
    return []


def _get_uvm_agents(model: Any) -> list:
    if isinstance(model, dict):
        v = model.get("verification", {})
        agents = v.get("uvm_agents", []) if isinstance(v, dict) else []
        return agents if isinstance(agents, list) else []
    v = getattr(model, "verification", None)
    if not v:
        return []
    agents = getattr(v, "uvm_agents", []) or []
    result = []
    for agent in agents:
        if hasattr(agent, "__dict__"):
            result.append(agent.__dict__)
        elif isinstance(agent, dict):
            result.append(agent)
    return result


def _get_verification_list(model: Any, key: str) -> list:
    if isinstance(model, dict):
        v = model.get("verification", {})
        value = v.get(key, []) if isinstance(v, dict) else []
    else:
        v = getattr(model, "verification", None)
        value = getattr(v, key, []) if v else []
    return value if isinstance(value, list) else []


def _dict_get(obj: Any, key: str, default: str = "") -> str:
    if isinstance(obj, dict):
        return str(obj.get(key, default) or default)
    return str(getattr(obj, key, default) or default)


def _safe_sv_name(value: str) -> str:
    import re

    name = re.sub(r"[^a-zA-Z0-9_]", "_", value or "approved_point")
    if name and name[0].isdigit():
        name = f"cp_{name}"
    return name or "approved_point"


def _port_name(p) -> str:
    if isinstance(p, dict):
        return p.get("name", "")
    return getattr(p, "name", "")


def _port_dir(p) -> str:
    if isinstance(p, dict):
        return p.get("direction", "")
    return getattr(p, "direction", "")


def _port_width(p) -> int:
    if isinstance(p, dict):
        return p.get("width", 1) or 1
    return getattr(p, "width", 1) or 1


def _port_bus_range(p) -> str:
    if isinstance(p, dict):
        return str(p.get("bus_range", "") or "")
    return str(getattr(p, "bus_range", "") or "")


def _port_bus_str(p) -> str:
    bus_range = _port_bus_range(p).strip()
    if bus_range:
        if not bus_range.startswith("["):
            bus_range = f"[{bus_range}]"
        return f"{bus_range} "
    return _bus_str(_port_width(p))


def _bus_param_identifiers(bus_range: str) -> List[str]:
    text = str(bus_range or "")
    if not text:
        return []
    ignored = {
        "clog2", "signed", "unsigned", "logic", "wire", "reg", "bit",
        "input", "output", "inout",
    }
    identifiers: List[str] = []
    for ident in re.findall(r"\b[a-zA-Z_]\w*\b", text):
        if ident.lower() in ignored:
            continue
        if ident not in identifiers:
            identifiers.append(ident)
    return identifiers


def _sv_param_default(value: Any) -> str:
    text = str(value if value is not None else "").strip()
    text = text.rstrip(";")
    if not text:
        return "0"
    if re.search(r"[^a-zA-Z0-9_$'()+\-*/%:._\s]", text):
        return "0"
    return text


def _infer_uvm_param_default(name: str, existing: Dict[str, str]) -> str:
    upper = str(name or "").upper()
    if name in existing:
        return existing[name]
    if "DATA_WIDTH" in upper:
        return existing.get("DATA_WIDTH") or existing.get("ALGN_DATA_WIDTH") or "32"
    if "ADDR_WIDTH" in upper or "ADDRESS_WIDTH" in upper:
        return existing.get("ADDR_WIDTH") or "32"
    if any(token in upper for token in ("OFFSET_WIDTH", "SIZE_WIDTH", "LEN_WIDTH", "LENGTH_WIDTH")):
        return "8"
    if "DEPTH" in upper:
        return "16"
    if any(token in upper for token in ("ID_WIDTH", "SOURCE_WIDTH", "SINK_WIDTH")):
        return "4"
    if "MASK_WIDTH" in upper or "STRB_WIDTH" in upper:
        return "4"
    return "32"


def _collect_uvm_parameters(params: list, ports: List[Any]) -> List[Dict[str, str]]:
    """Merge parsed parameters with parameters referenced by symbolic port widths."""
    merged: Dict[str, str] = {}
    ordered: List[str] = []
    for param in params or []:
        name = _param_name(param)
        if not name:
            continue
        if name not in merged:
            ordered.append(name)
        merged[name] = _sv_param_default(_param_default(param))
    for port in ports or []:
        for ident in _bus_param_identifiers(_port_bus_range(port)):
            if ident not in merged:
                ordered.append(ident)
                merged[ident] = _infer_uvm_param_default(ident, merged)
    return [{"name": name, "default_value": merged[name]} for name in ordered]


def _sv_parameter_header(params: list) -> str:
    entries = [
        f"    parameter int {_param_name(param)} = {_param_default(param)}"
        for param in params or []
        if _param_name(param)
    ]
    if not entries:
        return ""
    return " #(\n" + ",\n".join(entries) + "\n)"


def _param_name(p) -> str:
    if isinstance(p, dict):
        return p.get("name", "")
    return getattr(p, "name", "")


def _param_default(p) -> str:
    if isinstance(p, dict):
        return p.get("default_value", "0")
    return getattr(p, "default_value", "0")


def _bus_str(width: int) -> str:
    return f"[{width-1}:0] " if width > 1 else ""


def _detect_clk_rst(ports: list, clock_domains: list) -> tuple:
    """Detect clock and reset from ports/domains. Returns (clk, rst, active_low)."""
    # Try clock domains first
    if clock_domains:
        if len(clock_domains) > 1:
            logger.warning(
                "UVM generator selected the first of %d clock domains; multi-clock UVM is limited",
                len(clock_domains),
            )
        cd = clock_domains[0] if isinstance(clock_domains[0], dict) else (
            clock_domains[0].__dict__ if hasattr(clock_domains[0], "__dict__") else {}
        )
        clk = cd.get("name", "clk")
        rst = cd.get("associated_reset", "rst_n")
        polarity = cd.get("reset_polarity", "active_low")
        return clk, rst, polarity == "active_low"

    # Scan ports
    clk_name = "clk"
    rst_name = "rst_n"
    active_low = True

    for p in ports:
        name = _port_name(p).lower()
        if any(c in name for c in ("clk", "clock")):
            clk_name = _port_name(p)
        if any(r in name for r in ("rst", "reset")):
            rst_name = _port_name(p)
            active_low = "n" in name or name.endswith("_n")

    if clk_name == "clk" and not any("clk" in _port_name(p).lower() or "clock" in _port_name(p).lower() for p in ports):
        logger.warning("UVM generator did not find a clock port; using fallback 'clk'")
    return clk_name, rst_name, active_low
