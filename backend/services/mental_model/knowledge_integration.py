"""Knowledge-base matching for TruthCore mental models.

This module is intentionally additive. It derives compact rule matches and
planning hints from the already-built mental model, without changing parser,
LLM, or generator contracts.
"""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from typing import Any, Dict, Iterable, List, Sequence, Tuple


SEVERITY_ORDER = {
    "p0": 0,
    "critical": 0,
    "high": 1,
    "p1": 1,
    "medium": 2,
    "p2": 2,
    "low": 3,
}


def build_knowledge_base_context(
    model_or_content: Any,
    *,
    max_signal_rules: int = 18,
    max_corner_cases: int = 18,
    max_formal_properties: int = 18,
    max_vplan_rules: int = 18,
) -> Dict[str, Any]:
    """Build a compact, JSON-safe knowledge-base context for a mental model."""

    from services.mental_model.knowledge_base import (
        FORMAL_PROPERTY_RULES,
        POWER_RULES,
        PROTOCOL_RULES,
        SAFETY_RULES,
        SIGNAL_RULES,
        VPLAN_RULES,
        ALL_TEMPLATES,
        get_templates_for_category,
        get_templates_for_trait,
        get_rules_for_protocol,
        get_vplan_rules_for_category,
        match_formal_properties,
        match_signal_rules,
    )

    content = _plain(model_or_content)
    design = _dict(_get(content, "design", content))
    signal_names = sorted(_collect_signal_names(content, design), key=str.lower)
    traits = _infer_traits(content, design, signal_names)
    categories = _infer_categories(content, design, signal_names, traits)

    matched_signal_rules = [
        _signal_rule_dict(rule, matched)
        for rule, matched in match_signal_rules(signal_names)
    ][:max_signal_rules]

    corner_cases = _dedupe_by_id(
        _corner_case_dict(template, signal_names)
        for category in categories
        for template in get_templates_for_category(category)
    )
    for trait in traits:
        corner_cases.extend(
            _corner_case_dict(template, signal_names)
            for template in get_templates_for_trait(trait)
        )
    corner_cases = _sort_rules(_dedupe_by_id(corner_cases))[:max_corner_cases]

    formal_properties = [
        _formal_property_dict(rule)
        for rule in match_formal_properties(signal_names)
    ][:max_formal_properties]

    protocol_rules = _sort_rules(_dedupe_by_id(
        _protocol_rule_dict(rule, signal_names)
        for protocol_name in _collect_protocol_names(design, signal_names)
        for rule in get_rules_for_protocol(protocol_name)
    ))[:18]

    vplan_rules = _dedupe_by_id(
        _vplan_rule_dict(rule, signal_names)
        for category in categories
        for rule in get_vplan_rules_for_category(category)
    )
    vplan_rules = _sort_rules(vplan_rules)[:max_vplan_rules]

    hints = _build_plan_hints(
        matched_signal_rules,
        corner_cases,
        formal_properties,
        vplan_rules,
        protocol_rules,
    )

    return {
        "engine": "truthcore_knowledge_base",
        "version": "1",
        "rule_counts": {
            "protocol": len(PROTOCOL_RULES),
            "category": len(ALL_TEMPLATES),
            "signal": len(SIGNAL_RULES),
            "safety": len(SAFETY_RULES),
            "power": len(POWER_RULES),
            "formal": len(FORMAL_PROPERTY_RULES),
            "vplan": len(VPLAN_RULES),
        },
        "signals_considered": len(signal_names),
        "categories": categories,
        "traits": traits,
        "signal_rules": matched_signal_rules,
        "protocol_rules": protocol_rules,
        "corner_cases": corner_cases,
        "formal_properties": formal_properties,
        "vplan_testpoints": vplan_rules,
        "plan_hints": hints,
    }


def empty_knowledge_base_context() -> Dict[str, Any]:
    """Return the stable empty shape used if matching is unavailable."""

    return {
        "engine": "truthcore_knowledge_base",
        "version": "1",
        "rule_counts": {},
        "signals_considered": 0,
        "categories": [],
        "traits": [],
        "signal_rules": [],
        "protocol_rules": [],
        "corner_cases": [],
        "formal_properties": [],
        "vplan_testpoints": [],
        "plan_hints": {"unitsim": [], "formal": [], "uvm": [], "coverage": []},
    }


def _plain(value: Any) -> Any:
    if is_dataclass(value):
        return asdict(value)
    if hasattr(value, "to_dict"):
        try:
            return value.to_dict()
        except Exception:
            pass
    if isinstance(value, dict):
        return value
    if hasattr(value, "__dict__"):
        return {
            key: _plain(val)
            for key, val in vars(value).items()
            if not key.startswith("_")
        }
    return value


def _dict(value: Any) -> Dict[str, Any]:
    value = _plain(value)
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> List[Any]:
    value = _plain(value)
    return value if isinstance(value, list) else []


def _get(obj: Any, key: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _collect_signal_names(content: Dict[str, Any], design: Dict[str, Any]) -> set[str]:
    names: set[str] = set()

    def add(value: Any) -> None:
        text = str(value or "").strip()
        if text:
            names.add(text)

    def collect_from_block(block: Dict[str, Any]) -> None:
        for port in _list(block.get("ports")):
            if isinstance(port, dict):
                add(port.get("name"))
        for param in _list(block.get("parameters")):
            if isinstance(param, dict):
                add(param.get("name"))
        for reg in _list(block.get("register_fields")) + _list(block.get("register_map")):
            if isinstance(reg, dict):
                for key in ("name", "write_enable", "addr_signal", "data_signal"):
                    add(reg.get(key))
        for proto in _list(block.get("protocols")):
            if isinstance(proto, dict):
                add(proto.get("protocol") or proto.get("name"))
                for port_name in _list(proto.get("port_group") or proto.get("ports")):
                    add(port_name)
        for clk in _list(block.get("clock_domains")):
            if isinstance(clk, dict):
                add(clk.get("name"))
                add(clk.get("associated_reset") or clk.get("reset"))
        for fsm in _list(block.get("fsms")):
            if isinstance(fsm, dict):
                add(fsm.get("state_signal"))
                for state in _list(fsm.get("states")):
                    add(state)
        for symbol in _list(block.get("internal_symbols")):
            add(symbol)

    collect_from_block(design)

    for block in _dict(content.get("block_models")).values():
        if isinstance(block, dict):
            collect_from_block(block)

    symbol_table = _dict(content.get("symbol_table"))
    for key, value in symbol_table.items():
        add(key)
        if isinstance(value, dict):
            for item in value.values():
                for symbol in _list(item):
                    add(symbol)
        else:
            for symbol in _list(value):
                add(symbol)

    return names


def _infer_traits(content: Dict[str, Any], design: Dict[str, Any], signal_names: Sequence[str]) -> List[str]:
    traits: List[str] = []
    clock_domains = _list(design.get("clock_domains"))
    text = " ".join(signal_names).lower()
    if len(clock_domains) > 1 or design.get("has_cdc_crossings") or any(s in text for s in ("cdc", "sync", "async", "gray")):
        traits.append("multi_clock")
    if _list(design.get("parameters")):
        traits.append("parameterized")
    if _list(design.get("register_fields")) or _list(design.get("register_map")):
        traits.append("register_mapped")
    if len(_dict(content.get("block_models"))) > 1 or len(_list(design.get("modules"))) > 1:
        traits.append("hierarchical")
    return traits


def _infer_categories(
    content: Dict[str, Any],
    design: Dict[str, Any],
    signal_names: Sequence[str],
    traits: Sequence[str],
) -> List[str]:
    text_parts = list(signal_names)
    text_parts.extend(str(item or "") for item in _list(design.get("modules")))
    text_parts.append(str(design.get("top_module") or ""))
    for proto in _list(design.get("protocols")):
        if isinstance(proto, dict):
            text_parts.append(str(proto.get("protocol") or proto.get("name") or ""))
    text = " ".join(text_parts).lower()

    scores: Dict[str, int] = {"universal": 1}

    def bump(category: str, amount: int = 1) -> None:
        scores[category] = scores.get(category, 0) + amount

    if any(token in text for token in ("fifo", "queue", "buffer", "full", "empty", "almost_full", "almost_empty")):
        bump("storage", 3)
    if any(token in text for token in ("ram", "sram", "dram", "cache", "mem", "byte_en", "wen", "ren")):
        bump("memory_controller", 2)
    if any(token in text for token in ("apb", "axi", "ahb", "wishbone", "avalon", "tilelink", "valid", "ready", "bridge")):
        bump("protocol_bridge", 3)
        bump("bus_interconnect", 1)
    if len(_list(design.get("protocols"))) > 1:
        bump("protocol_bridge", 3)
    if any(token in text for token in ("arb", "grant", "gnt", "request", "req", "priority", "round_robin")):
        bump("arbiter_scheduler", 2)
    if any(token in text for token in ("csr", "reg", "status", "ctrl", "irq", "interrupt", "enable", "w1c")):
        bump("peripheral", 2)
    if any(token in text for token in ("dma", "descriptor", "scatter", "gather", "burst")):
        bump("dma_data_mover", 2)
    if any(token in text for token in ("pc", "instr", "opcode", "exception", "priv", "pipeline", "hart")):
        bump("processor_core", 2)
    if any(token in text for token in ("aes", "sha", "hmac", "key", "crypto", "secure", "lock", "otp")):
        bump("crypto_security", 2)
    if any(token in text for token in ("clk_en", "clock_gate", "sleep", "wakeup", "power", "reset")):
        bump("clock_reset_pmu", 2)
    if "multi_clock" in traits:
        bump("cdc", 3)
    if any(token in text for token in ("scan", "jtag", "debug", "test_mode", "mbist")):
        bump("test_debug", 2)
    if any(token in text for token in ("packet", "frame", "crc", "length", "payload")):
        bump("network_packet", 2)
    if any(token in text for token in ("fir", "fft", "mac", "coef", "sample", "accum")):
        bump("dsp_datapath", 2)

    ranked = sorted(scores.items(), key=lambda item: (-item[1], item[0]))
    return [category for category, _score in ranked[:8]]


def _collect_protocol_names(design: Dict[str, Any], signal_names: Sequence[str]) -> List[str]:
    names: List[str] = []
    for proto in _list(design.get("protocols")):
        if isinstance(proto, dict):
            name = str(proto.get("protocol") or proto.get("name") or "").strip()
            if name:
                names.append(name)

    compact_signals = {str(signal).lower().replace("_", "") for signal in signal_names}
    if {"avalid", "aready", "dvalid", "dready"} <= compact_signals:
        names.append("TileLink-UL")

    unique: List[str] = []
    for name in names:
        key = name.lower().replace("_", "").replace("-", "").replace(" ", "")
        if key and key not in {item.lower().replace("_", "").replace("-", "").replace(" ", "") for item in unique}:
            unique.append(name)
    return unique


def _signal_rule_dict(rule: Any, matched_signals: Sequence[str]) -> Dict[str, Any]:
    return {
        "id": rule.id,
        "title": rule.title,
        "description": rule.description,
        "severity": rule.severity,
        "corner_type": rule.corner_type,
        "verification_approach": rule.verification_approach,
        "matched_signals": list(matched_signals),
        "rationale": rule.rationale,
        "stimulus_hint": rule.stimulus_hint,
        "check_hint": rule.check_hint,
        "source": "signal_rule",
    }


def _protocol_rule_dict(rule: Any, signal_names: Sequence[str]) -> Dict[str, Any]:
    protocol = str(getattr(rule, "protocol", "") or "")
    patterns = _protocol_signal_patterns(protocol)
    return {
        "id": rule.id,
        "title": f"{protocol}: {rule.text}",
        "description": rule.text,
        "protocol": protocol,
        "category": rule.category,
        "severity": rule.severity,
        "check_type": rule.check_type,
        "spec_ref": rule.spec_ref,
        "matched_signals": _match_patterns(signal_names, patterns),
        "stimulus_hint": rule.negative_test or f"Exercise {protocol} {rule.category} behavior.",
        "check_hint": rule.text,
        "source": "protocol_rule",
    }


def _protocol_signal_patterns(protocol: str) -> List[str]:
    compact = str(protocol or "").lower().replace("_", "").replace("-", "").replace(" ", "")
    if "tilelink" in compact or "tlul" in compact:
        return [
            "a_valid", "a_ready", "a_opcode", "a_param", "a_size", "a_source",
            "a_address", "a_mask", "a_data", "d_valid", "d_ready", "d_opcode",
            "d_param", "d_size", "d_source", "d_sink", "d_data", "d_error",
        ]
    if "axi" in compact:
        return ["awvalid", "awready", "wvalid", "wready", "bvalid", "bready", "arvalid", "rvalid"]
    if "apb" in compact:
        return ["psel", "penable", "pready", "paddr", "pwdata", "prdata"]
    if "ahb" in compact:
        return ["haddr", "htrans", "hready", "hwdata", "hrdata"]
    return [protocol]


def _corner_case_dict(template: Any, signal_names: Sequence[str]) -> Dict[str, Any]:
    matched = _match_patterns(signal_names, template.trigger_signals)
    return {
        "id": template.id,
        "title": template.title,
        "description": template.description,
        "category": template.category,
        "severity": template.severity,
        "corner_type": template.corner_type,
        "verification_approach": template.verification_approach,
        "trigger_signals": list(template.trigger_signals),
        "matched_signals": matched,
        "rationale": template.rationale,
        "stimulus_hint": template.stimulus_hint,
        "check_hint": template.check_hint,
        "source": "corner_case",
    }


def _formal_property_dict(rule: Any) -> Dict[str, Any]:
    return {
        "id": rule.id,
        "title": rule.title,
        "description": rule.description,
        "library_source": rule.library_source,
        "property_class": rule.property_class,
        "severity": rule.severity,
        "property_type": rule.property_type,
        "trigger_patterns": list(rule.trigger_patterns),
        "rationale": rule.rationale,
        "sva_template": rule.sva_template,
        "source": "formal_property",
    }


def _vplan_rule_dict(rule: Any, signal_names: Sequence[str]) -> Dict[str, Any]:
    return {
        "id": rule.id,
        "title": rule.title,
        "description": rule.description,
        "source_project": rule.source_project,
        "source_ip": rule.source_ip,
        "testpoint_ref": rule.testpoint_ref,
        "applicable_to": list(rule.applicable_to),
        "severity": rule.severity,
        "verification_approach": rule.verification_approach,
        "trigger_signals": list(rule.trigger_signals),
        "matched_signals": _match_patterns(signal_names, rule.trigger_signals),
        "stimulus_hint": rule.stimulus_hint,
        "check_hint": rule.check_hint,
        "source": "vplan_rule",
    }


def _build_plan_hints(
    signal_rules: Sequence[Dict[str, Any]],
    corner_cases: Sequence[Dict[str, Any]],
    formal_properties: Sequence[Dict[str, Any]],
    vplan_rules: Sequence[Dict[str, Any]],
    protocol_rules: Sequence[Dict[str, Any]] = (),
) -> Dict[str, List[Dict[str, Any]]]:
    hints = {"unitsim": [], "formal": [], "uvm": [], "coverage": []}
    for item in list(signal_rules) + list(corner_cases) + list(vplan_rules):
        hint = _compact_hint(item)
        approach = str(item.get("verification_approach") or "").lower()
        if approach in hints:
            hints[approach].append(hint)
        if item.get("source") == "vplan_rule" or item.get("matched_signals"):
            hints["coverage"].append(hint)
    for item in formal_properties:
        hints["formal"].append(_compact_hint(item))
    for item in protocol_rules:
        hint = _compact_hint(item)
        check_type = str(item.get("check_type") or "").lower()
        if check_type in {"assertion", "cover"}:
            hints["formal"].append(hint)
        hints["uvm"].append(hint)
        hints["coverage"].append(hint)
    for key, values in hints.items():
        hints[key] = _sort_rules(_dedupe_by_id(values))[:10]
    return hints


def _compact_hint(item: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": item.get("id", ""),
        "title": item.get("title", ""),
        "severity": item.get("severity", ""),
        "source": item.get("source", ""),
        "description": item.get("description", ""),
        "stimulus_hint": item.get("stimulus_hint", ""),
        "check_hint": item.get("check_hint", ""),
        "signals": list(item.get("matched_signals") or item.get("trigger_signals") or [])[:8],
    }


def _match_patterns(signal_names: Sequence[str], patterns: Iterable[str]) -> List[str]:
    result: List[str] = []
    lower_signals = [(signal, signal.lower()) for signal in signal_names]
    for pattern in patterns or []:
        lower_pattern = str(pattern or "").lower()
        if not lower_pattern:
            continue
        for original, lower in lower_signals:
            if lower_pattern in lower and original not in result:
                result.append(original)
    return result[:12]


def _dedupe_by_id(items: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen: set[str] = set()
    result: List[Dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        key = str(item.get("id") or item.get("title") or "").strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def _sort_rules(items: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return sorted(
        items,
        key=lambda item: (
            SEVERITY_ORDER.get(str(item.get("severity") or "").lower(), 9),
            str(item.get("id") or ""),
        ),
    )
