"""
Test Plan Generator — Creates structured test plans from the mental model.

The test plan is the bridge between the mental model (what to verify)
and the testbench generator (how to verify it).

Each test scenario maps to a requirement and defines:
  - Stimulus pattern (what to drive)
  - Expected behavior (what to check)
  - Coverage points (what to measure)
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from dataclasses import asdict, dataclass, field, is_dataclass
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class TestCheck:
    """A single assertion/check within a test scenario."""
    signal: str
    condition: str     # "==", "!=", ">", "<"
    expected: str      # Expected value
    description: str = ""


@dataclass
class StimulusStep:
    """A single step in a stimulus sequence."""
    action: str        # "set", "toggle", "wait", "assert"
    signal: str = ""
    value: str = ""
    cycles: int = 1
    description: str = ""


@dataclass
class TestScenario:
    """A complete test scenario to be converted into a testbench task."""
    id: str
    name: str
    description: str = ""
    requirement_ids: List[str] = field(default_factory=list)
    priority: str = "medium"  # "critical", "high", "medium", "low"
    category: str = "functional"  # "reset", "functional", "boundary", "stress", "protocol"
    precondition: str = ""     # e.g., "after_reset", "fifo_full"
    stimulus: List[StimulusStep] = field(default_factory=list)
    checks: List[TestCheck] = field(default_factory=list)
    cleanup: str = ""          # e.g., "apply_reset()"


@dataclass
class TestPlan:
    """Complete test plan for a design block."""
    module_name: str
    scenarios: List[TestScenario] = field(default_factory=list)
    coverage_goals: Dict[str, float] = field(default_factory=dict)
    total_scenarios: int = 0
    estimated_sim_time: str = ""

    @property
    def summary(self) -> str:
        by_priority = {}
        for s in self.scenarios:
            by_priority[s.priority] = by_priority.get(s.priority, 0) + 1
        return (
            f"TestPlan for {self.module_name}: {len(self.scenarios)} scenarios "
            f"({by_priority})"
        )


def generate_test_plan(model: Any, limits: Any = None) -> TestPlan:
    """
    Generate a test plan from a MentalModelSchema.

    Strategy:
      1. Always generate reset test (critical)
      2. Generate functional tests from requirements
      3. Generate boundary tests for wide ports
      4. Generate protocol-specific tests
      5. Generate FSM state coverage tests
      6. Generate stress/corner-case tests
    """
    design = model.design
    module_name = design.top_module or "dut"
    requirements = list(getattr(model, "requirements", []) or [])
    max_sequences = _limit_value(limits, "max_sequence_classes", 12)
    max_tests = _limit_value(limits, "max_test_classes", 8)
    max_requirements = _limit_value(limits, "max_requirements", len(requirements))

    scenarios: List[TestScenario] = []

    # ── 1. Reset Test (always present) ─────────────────────────
    reset_scenario = _gen_reset_scenario(design, requirements)
    scenarios.append(reset_scenario)

    scenarios.extend(_gen_expected_behavior_scenarios(design)[:max_sequences])
    scenarios.extend(_gen_transaction_flow_scenarios(design)[:max_sequences])

    # ── 2. Functional Tests from Requirements ──────────────────
    for i, req in enumerate(requirements[:max_requirements]):
        scenario = _gen_requirement_scenario(i, req, design)
        scenarios.append(scenario)

    # ── 3. Boundary Value Tests ────────────────────────────────
    wide_ports = [p for p in design.ports if p.width > 1 and p.direction == "input"]
    for port in wide_ports[:3]:
        scenario = _gen_boundary_scenario(port, design, requirements)
        scenarios.append(scenario)

    # ── 4. Protocol Tests ──────────────────────────────────────
    for protocol in list(getattr(design, "protocols", []) or [])[:3]:
        scenario = _gen_protocol_scenario(protocol, design, requirements)
        scenarios.append(scenario)

    # ── 5. FSM Coverage Tests ──────────────────────────────────
    for fsm in list(getattr(design, "fsms", []) or [])[:2]:
        scenario = _gen_fsm_scenario(fsm, design, requirements)
        scenarios.append(scenario)

    # ── 6. Stress Tests ────────────────────────────────────────
    if len(design.ports) > 2:
        scenarios.append(_gen_stress_scenario(design, requirements))

    if _limit_value(limits, "deduplicate_scenarios", True):
        scenarios = _deduplicate_scenarios(scenarios)
    # Keep reset plus a compact representative set. UVM can still carry richer
    # intent in the approved plan; this keeps generated directed TBs manageable.
    max_total = max(1, max_tests) + 1
    scenarios = scenarios[:max_total]

    plan = TestPlan(
        module_name=module_name,
        scenarios=scenarios,
        total_scenarios=len(scenarios),
        coverage_goals={
            "line_coverage": 90.0,
            "branch_coverage": 80.0,
            "toggle_coverage": 70.0,
        },
    )

    logger.info(f"Generated test plan: {plan.summary}")
    return plan


# ═══════════════════════════════════════════════════════════════════════
# Scenario Generators
# ═══════════════════════════════════════════════════════════════════════


async def generate_test_plan_with_ai(
    model: Any,
    ai_client: Any = None,
    require_llm: bool = False,
    limits: Any = None,
) -> TestPlan:
    """
    Generate a UnitSim test plan with an LLM, constrained by the mental model.

    The LLM proposes scenarios, but every signal and requirement reference is
    validated against the mental model before the plan is returned.
    """
    fallback = generate_test_plan(model, limits=limits)
    _mark_plan(
        fallback,
        generation_engine="rules",
        llm_used=False,
        grounding="mental_model_rules",
    )

    if not ai_client:
        if require_llm:
            raise RuntimeError("AI client is required for UnitSim test plan generation")
        return fallback

    try:
        context = _mental_model_test_plan_context(model)
        response = await _call_test_plan_ai_client(ai_client, context, fallback)
        plan_data = _extract_json_object(response)
        ai_plan = _plan_from_ai_json(plan_data, model, fallback)
        ai_plan = _apply_plan_limits(ai_plan, limits)
        _mark_plan(
            ai_plan,
            generation_engine="llm_grounded",
            llm_used=True,
            grounding="mental_model_validated",
        )
        logger.info("Generated LLM-grounded test plan: %s", ai_plan.summary)
        return ai_plan
    except Exception as exc:
        logger.warning("AI test plan generation failed: %s", exc)
        if require_llm:
            raise RuntimeError(f"AI test plan generation failed: {exc}") from exc
        _mark_plan(
            fallback,
            generation_engine="rules_fallback",
            llm_used=False,
            grounding="mental_model_rules",
            llm_error=str(exc),
        )
        return fallback


def _mark_plan(plan: TestPlan, **metadata: Any) -> TestPlan:
    for key, value in metadata.items():
        setattr(plan, key, value)
    return plan


def _limit_value(limits: Any, key: str, default: Any) -> Any:
    if limits is None:
        return default
    if isinstance(limits, dict):
        return limits.get(key, default)
    return getattr(limits, key, default)


def _apply_plan_limits(plan: TestPlan, limits: Any) -> TestPlan:
    if limits is None:
        return plan
    scenarios = list(plan.scenarios or [])
    if _limit_value(limits, "deduplicate_scenarios", True):
        scenarios = _deduplicate_scenarios(scenarios)
    max_tests = _limit_value(limits, "max_test_classes", len(scenarios))
    try:
        max_tests = int(max_tests)
    except (TypeError, ValueError):
        max_tests = len(scenarios)
    plan.scenarios = scenarios[: max(1, max_tests) + 1]
    plan.total_scenarios = len(plan.scenarios)
    return plan


def _deduplicate_scenarios(scenarios: List[TestScenario]) -> List[TestScenario]:
    seen: set[tuple] = set()
    unique: List[TestScenario] = []
    for scenario in scenarios:
        stimulus_key = tuple(
            (step.signal, step.value, step.action)
            for step in scenario.stimulus
            if step.signal
        )
        check_key = tuple(
            (check.signal, check.condition, check.expected)
            for check in scenario.checks
            if check.signal
        )
        key = (
            scenario.category,
            tuple(scenario.requirement_ids or []),
            stimulus_key,
            check_key,
        )
        if key in seen:
            continue
        seen.add(key)
        unique.append(scenario)
    return unique


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


def _compact_block_models(block_models: Any, limit: int = 24) -> Dict[str, Any]:
    if not isinstance(block_models, dict):
        return {}
    def _list_attr(block: Any, key: str) -> List[Any]:
        value = _get(block, key, [])
        return value if isinstance(value, list) else []

    compact: Dict[str, Any] = {}
    for index, (name, block) in enumerate(block_models.items()):
        if index >= limit:
            break
        compact[str(name)] = {
            "top_module": _get(block, "top_module", str(name)),
            "definition_kind": _get(block, "definition_kind", "Module"),
            "file": _get(block, "file", ""),
            "description": _get(block, "description", ""),
            "ports": _to_jsonable(_list_attr(block, "ports")[:32]),
            "parameters": _to_jsonable(_list_attr(block, "parameters")[:16]),
            "instantiations": _to_jsonable(_list_attr(block, "instantiations")[:16]),
            "sub_instances": _to_jsonable(_list_attr(block, "sub_instances")[:16]),
            "fsms": _to_jsonable(_list_attr(block, "fsms")[:8]),
            "protocols": _to_jsonable(_list_attr(block, "protocols")[:8]),
            "register_fields": _to_jsonable(_list_attr(block, "register_fields")[:16]),
            "expected_behaviors": _to_jsonable(_list_attr(block, "expected_behaviors")[:8]),
            "transaction_flows": _to_jsonable(_list_attr(block, "transaction_flows")[:8]),
            "internal_symbols": _to_jsonable(_list_attr(block, "internal_symbols")[:64]),
            "modports": _to_jsonable(_list_attr(block, "modports")[:16]),
        }
    return compact


def _compact_evidence(items: Any, limit: int = 20) -> List[Any]:
    if not isinstance(items, list):
        return []
    return _to_jsonable(items[-limit:])


def _mental_model_test_plan_context(model: Any) -> Dict[str, Any]:
    design = _get(model, "design", {})
    ports = _get(design, "ports", []) or []
    requirements = _get(model, "requirements", []) or []
    block_models = _get(model, "block_models", {}) or {}
    return {
        "top_module": _get(design, "top_module", "dut"),
        "parser_engine": _get(model, "parser_engine", ""),
        "parser_version": _get(model, "parser_version", ""),
        "parser_diagnostics": _to_jsonable(_get(model, "parser_diagnostics", []) or []),
        "ports": [
            {
                "name": _get(port, "name", ""),
                "direction": _get(port, "direction", ""),
                "width": _get(port, "width", 1),
                "bus_range": _get(port, "bus_range", ""),
                "protocol_role": _get(port, "protocol_role", ""),
                "description": _get(port, "description", ""),
            }
            for port in ports
        ],
        "clock_domains": _to_jsonable(_get(design, "clock_domains", []) or []),
        "protocols": _to_jsonable(_get(design, "protocols", []) or []),
        "fsms": _to_jsonable(_get(design, "fsms", []) or []),
        "register_fields": _to_jsonable(_get(design, "register_fields", []) or []),
        "register_map": _to_jsonable(_get(design, "register_map", []) or []),
        "expected_behaviors": _to_jsonable(_get(design, "expected_behaviors", []) or []),
        "transaction_flows": _to_jsonable(_get(design, "transaction_flows", []) or []),
        "requirements": [
            {
                "id": _get(req, "id", ""),
                "text": _get(req, "text", ""),
                "priority": _get(req, "priority", "medium"),
                "category": _get(req, "category", "functional"),
            }
            for req in requirements
        ],
        "verification_intent": _to_jsonable(_get(model, "verification", {})),
        "block_models": _compact_block_models(block_models),
        "symbol_table": _to_jsonable(_get(model, "symbol_table", {}) or {}),
        "knowledge_base": _to_jsonable(_get(model, "knowledge_base", {}) or {}),
        "evidence": _compact_evidence(_get(model, "evidence", []) or []),
        "living_agent_memory": _to_jsonable(_get(_get(model, "living_agent", {}) or {}, "verification_memory", {}) or {}),
        "risks": _to_jsonable(_get(model, "risks", []) or []),
        "open_questions": _to_jsonable(_get(model, "open_questions", []) or []),
    }


async def _call_test_plan_ai_client(
    ai_client: Any,
    context: Dict[str, Any],
    fallback_plan: TestPlan,
) -> str:
    import asyncio

    system_prompt = (
        "You are a senior ASIC verification planning agent. "
        "Create UnitSim test plans only from the provided mental model. "
        "Return valid JSON only."
    )
    prompt = f"""Create a mental-model-backed UnitSim test plan.

MENTAL MODEL CONTEXT:
```json
{json.dumps(context, indent=2)[:16000]}
```

RULE-BASED BASELINE PLAN SUMMARY:
{fallback_plan.summary}

Return JSON in this exact shape:
{{
  "module_name": "{context.get('top_module') or fallback_plan.module_name}",
  "scenarios": [
    {{
      "id": "TP-001",
      "name": "snake_case_task_name",
      "description": "what this verifies",
      "requirement_ids": ["REQ-001"],
      "priority": "critical|high|medium|low",
      "category": "reset|functional|boundary|stress|protocol",
      "precondition": "after_reset",
      "stimulus": [
        {{"action": "set|toggle|wait|assert", "signal": "existing_input_or_empty", "value": "0", "cycles": 1, "description": "step purpose"}}
      ],
      "checks": [
        {{"signal": "existing_output_or_empty", "condition": "==|!=|===|!==|>|<|>=|<=", "expected": "0", "description": "check purpose"}}
      ],
      "cleanup": ""
    }}
  ],
  "coverage_goals": {{"line_coverage": 90, "branch_coverage": 80, "toggle_coverage": 70}},
  "estimated_sim_time": "short"
}}

Rules:
- Use only signal names from MENTAL MODEL CONTEXT. Do not invent signals.
- Use only requirement IDs from MENTAL MODEL CONTEXT.
- Treat knowledge_base.plan_hints as additional generic DV guidance, but still ground every scenario in real model signals.
- Read evidence and living_agent_memory before planning. Avoid repeating scenarios/checks that previous simulation or compile evidence marked as invalid unless the new scenario fixes the cause.
- If open_questions or evidence show unresolved ambiguity, prefer a blocking/open-question style scenario note over guessing expected behavior.
- Include reset, requirement, boundary, protocol/FSM, and stress scenarios when supported by the mental model.
- Convert expected_behaviors into executable scenarios with concrete output checks.
- Every functional or protocol scenario should include at least one real output check when expected behavior is available.
- Use transaction_flows and register_fields/register_map to create legal stimulus; do not make up register names or addresses.
- Leave signal empty only for generic wait/assert comment steps.
- Keep scenarios executable by a simple directed SystemVerilog testbench generator.
"""

    if hasattr(ai_client, "chat"):
        result = ai_client.chat(system_prompt, prompt)
    elif hasattr(ai_client, "generate"):
        result = ai_client.generate(
            prompt,
            system_prompt=system_prompt,
            temperature=0.2,
        )
    else:
        raise RuntimeError("AI client does not support chat or generate")

    if asyncio.iscoroutine(result):
        result = await result
    return str(result or "")


def _extract_json_object(text: str) -> Dict[str, Any]:
    match = re.search(r"\{[\s\S]*\}", text or "")
    if not match:
        raise ValueError("AI response did not contain a JSON object")
    data = json.loads(match.group())
    if not isinstance(data, dict):
        raise ValueError("AI response JSON was not an object")
    return data


def _plan_from_ai_json(
    plan_data: Dict[str, Any],
    model: Any,
    fallback_plan: TestPlan,
) -> TestPlan:
    design = _get(model, "design", {})
    ports = _get(design, "ports", []) or []
    allowed_signals = {
        str(_get(port, "name", "") or "")
        for port in ports
        if _get(port, "name", "")
    }
    allowed_requirements = {
        str(_get(req, "id", "") or "")
        for req in (_get(model, "requirements", []) or [])
        if _get(req, "id", "")
    }
    raw_scenarios = plan_data.get("scenarios", [])
    if not isinstance(raw_scenarios, list):
        raise ValueError("AI plan scenarios must be a list")

    scenarios: List[TestScenario] = []
    for index, raw in enumerate(raw_scenarios):
        if not isinstance(raw, dict):
            continue
        scenario = _sanitize_ai_scenario(
            raw,
            index=index,
            allowed_signals=allowed_signals,
            allowed_requirements=allowed_requirements,
        )
        if scenario:
            scenarios.append(scenario)
    if not scenarios:
        raise ValueError("AI plan did not contain any valid grounded scenarios")

    module_name = str(
        plan_data.get("module_name")
        or _get(design, "top_module", "")
        or fallback_plan.module_name
        or "dut"
    )
    coverage_goals = plan_data.get("coverage_goals")
    if not isinstance(coverage_goals, dict):
        coverage_goals = dict(fallback_plan.coverage_goals)
    return TestPlan(
        module_name=module_name,
        scenarios=scenarios,
        coverage_goals=coverage_goals,
        total_scenarios=len(scenarios),
        estimated_sim_time=str(plan_data.get("estimated_sim_time") or ""),
    )


def _sanitize_ai_scenario(
    raw: Dict[str, Any],
    index: int,
    allowed_signals: set[str],
    allowed_requirements: set[str],
) -> Optional[TestScenario]:
    allowed_priorities = {"critical", "high", "medium", "low"}
    allowed_categories = {"reset", "functional", "boundary", "stress", "protocol"}
    name = _safe_task_name(str(raw.get("name") or f"test_{index + 1:03d}"))
    req_ids = [
        str(req_id)
        for req_id in (raw.get("requirement_ids") or [])
        if str(req_id) in allowed_requirements
    ]
    stimulus = [
        step
        for step in (
            _sanitize_ai_step(step, allowed_signals)
            for step in (raw.get("stimulus") or [])
            if isinstance(step, dict)
        )
        if step is not None
    ]
    checks = [
        check
        for check in (
            _sanitize_ai_check(check, allowed_signals)
            for check in (raw.get("checks") or [])
            if isinstance(check, dict)
        )
        if check is not None
    ]
    if not stimulus and not checks:
        stimulus = [StimulusStep(action="wait", cycles=2, description="Observe stable behavior")]

    priority = str(raw.get("priority") or "medium").lower()
    if priority not in allowed_priorities:
        priority = "medium"
    category = str(raw.get("category") or "functional").lower()
    if category not in allowed_categories:
        category = "functional"
    return TestScenario(
        id=str(raw.get("id") or f"TP-{index + 1:03d}"),
        name=name,
        description=str(raw.get("description") or name),
        requirement_ids=req_ids,
        priority=priority,
        category=category,
        precondition=str(raw.get("precondition") or ""),
        stimulus=stimulus,
        checks=checks,
        cleanup=str(raw.get("cleanup") or ""),
    )


def _sanitize_ai_step(
    raw: Dict[str, Any],
    allowed_signals: set[str],
) -> Optional[StimulusStep]:
    action = str(raw.get("action") or "wait").lower()
    if action not in {"set", "toggle", "wait", "assert"}:
        action = "wait"
    signal = str(raw.get("signal") or "")
    if signal and signal not in allowed_signals:
        return None
    try:
        cycles = max(1, min(int(raw.get("cycles") or 1), 10000))
    except (TypeError, ValueError):
        cycles = 1
    return StimulusStep(
        action=action,
        signal=signal,
        value=str(raw.get("value") or ""),
        cycles=cycles,
        description=str(raw.get("description") or ""),
    )


def _sanitize_ai_check(
    raw: Dict[str, Any],
    allowed_signals: set[str],
) -> Optional[TestCheck]:
    signal = str(raw.get("signal") or "")
    if signal not in allowed_signals:
        return None
    condition = str(raw.get("condition") or "==")
    if condition not in {"==", "!=", "===", "!==", ">", "<", ">=", "<="}:
        condition = "=="
    return TestCheck(
        signal=signal,
        condition=condition,
        expected=str(raw.get("expected") or "0"),
        description=str(raw.get("description") or f"Check {signal}"),
    )


def _safe_task_name(name: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9_]", "_", name).strip("_").lower()
    if not cleaned:
        cleaned = f"test_{_short_id()}"
    if cleaned[0].isdigit():
        cleaned = f"test_{cleaned}"
    return cleaned[:80]


def _safe_cycles(value: Any, default: int = 1) -> int:
    try:
        return max(1, min(int(value), 10000))
    except (TypeError, ValueError):
        return default


def _is_concrete_sv_value(value: Any) -> bool:
    text = str(value if value is not None else "").strip()
    if not text:
        return False
    lower = text.lower()
    if lower in {"0", "1", "true", "false", "high", "low", "'0", "'1"}:
        return True
    return bool(
        re.fullmatch(r"\d+", text)
        or re.fullmatch(r"\d+'[sS]?[bBoOdDhH][0-9a-fA-F_xzXZ]+", text)
        or re.fullmatch(r"'[sS]?[bBoOdDhH][0-9a-fA-F_xzXZ]+", text)
        or re.fullmatch(r"0x[0-9a-fA-F_]+", text)
        or re.fullmatch(r"0b[01_xzXZ]+", text)
    )


def _safe_stimulus_value(signal: str, value: Any) -> str:
    if _is_concrete_sv_value(value):
        return str(value)
    name = str(signal or "").lower()
    text = str(value if value is not None else "").lower()
    if any(token in name for token in ("valid", "sel", "enable", "ready")):
        return "1"
    if any(token in name for token in ("write", "read")):
        return "0"
    if "error" in name or "err" in name:
        return "0"
    if any(token in text for token in ("one", "true", "assert")):
        return "1"
    return "0"


def _gen_expected_behavior_scenarios(design: Any) -> List[TestScenario]:
    """Convert Phase 4 stimulus-to-output mappings into directed checks."""
    behaviors = _get(design, "expected_behaviors", []) or []
    if not isinstance(behaviors, list):
        return []

    ports = _get(design, "ports", []) or []
    port_by_name = {
        str(_get(port, "name", "") or ""): port
        for port in ports
        if _get(port, "name", "")
    }
    input_names = {
        name
        for name, port in port_by_name.items()
        if str(_get(port, "direction", "") or "").lower() in {"input", "inout"}
        and not _is_clock_or_reset(name)
    }
    output_names = {
        name
        for name, port in port_by_name.items()
        if str(_get(port, "direction", "") or "").lower() in {"output", "inout"}
    }

    scenarios: List[TestScenario] = []
    for index, raw in enumerate(behaviors[:16]):
        stimulus_map = _get(raw, "stimulus", {}) or {}
        expected_map = _get(raw, "expected_output", {}) or {}
        if not isinstance(stimulus_map, dict) or not isinstance(expected_map, dict):
            continue

        stimulus = [
            StimulusStep(
                action="set",
                signal=str(signal),
                value=_safe_stimulus_value(str(signal), value),
                description=f"Drive {signal} for expected behavior",
            )
            for signal, value in stimulus_map.items()
            if str(signal) in input_names
        ]
        latency = _safe_cycles(_get(raw, "latency_cycles", 1), 1)
        stimulus.append(
            StimulusStep(
                action="wait",
                cycles=latency,
                description=f"Wait {latency} cycle(s) for expected response",
            )
        )

        checks = [
            TestCheck(
                signal=str(signal),
                condition="===",
                expected=str(value),
                description=f"{signal} matches expected behavior",
            )
            for signal, value in expected_map.items()
            if str(signal) in output_names
            and _is_concrete_sv_value(value)
        ]
        if not checks:
            continue

        behavior_id = str(_get(raw, "id", "") or f"EB-{index + 1:03d}")
        req_ids = [
            str(req_id)
            for req_id in (_get(raw, "requirement_ids", []) or [])
            if str(req_id)
        ]
        scenarios.append(
            TestScenario(
                id=f"TP-{_short_id()}",
                name=_safe_task_name(f"test_expected_{behavior_id}"),
                description=str(
                    _get(raw, "description", "")
                    or f"Verify expected behavior {behavior_id}"
                ),
                requirement_ids=req_ids,
                priority="high" if req_ids else "medium",
                category="functional",
                precondition=str(_get(raw, "precondition", "") or "after_reset"),
                stimulus=stimulus,
                checks=checks,
            )
        )

    return scenarios


def _gen_transaction_flow_scenarios(design: Any) -> List[TestScenario]:
    """Convert grounded transaction flows into directed protocol scenarios."""
    flows = _get(design, "transaction_flows", []) or []
    if not isinstance(flows, list):
        return []

    ports = _get(design, "ports", []) or []
    port_by_name = {
        str(_get(port, "name", "") or ""): port
        for port in ports
        if _get(port, "name", "")
    }

    def _is_driven(signal: str) -> bool:
        direction = str(_get(port_by_name.get(signal), "direction", "") or "").lower()
        return direction in {"input", "inout"} and not _is_clock_or_reset(signal)

    def _is_observed(signal: str) -> bool:
        direction = str(_get(port_by_name.get(signal), "direction", "") or "").lower()
        return direction in {"output", "inout"}

    scenarios: List[TestScenario] = []
    for index, raw in enumerate(flows[:12]):
        name = str(_get(raw, "name", "") or f"transaction_flow_{index + 1}")
        steps = _get(raw, "steps", []) or []
        if not isinstance(steps, list):
            continue

        stimulus: List[StimulusStep] = []
        checks: List[TestCheck] = []
        for step in steps:
            if not isinstance(step, dict):
                continue
            signals = step.get("signals", {})
            if not isinstance(signals, dict):
                signals = {}
            for signal, value in signals.items():
                signal_name = str(signal)
                if signal_name not in port_by_name:
                    continue
                if _is_driven(signal_name):
                    stimulus.append(
                        StimulusStep(
                            action="set",
                            signal=signal_name,
                            value=_safe_stimulus_value(signal_name, value),
                            description=str(step.get("phase") or f"Drive {signal_name}"),
                        )
                    )
                elif _is_observed(signal_name) and _is_concrete_sv_value(value):
                    checks.append(
                        TestCheck(
                            signal=signal_name,
                            condition="===",
                            expected=str(value),
                            description=str(step.get("phase") or f"Observe {signal_name}"),
                        )
                    )
            cycles = _safe_cycles(step.get("cycles", 1), 1)
            stimulus.append(
                StimulusStep(
                    action="wait",
                    cycles=cycles,
                    description=str(step.get("description") or step.get("phase") or "Advance transaction"),
                )
            )

        if not stimulus and not checks:
            continue

        scenarios.append(
            TestScenario(
                id=f"TP-{_short_id()}",
                name=_safe_task_name(f"test_flow_{name}"),
                description=str(
                    _get(raw, "description", "")
                    or f"Exercise transaction flow {name}"
                ),
                priority="high",
                category="protocol",
                precondition="after_reset",
                stimulus=stimulus or [StimulusStep(action="wait", cycles=1)],
                checks=checks,
            )
        )

    return scenarios


def _gen_reset_scenario(
    design: Any,
    requirements: Optional[List[Any]] = None,
) -> TestScenario:
    """Generate reset behavior verification scenario."""
    checks = []
    for port in design.ports:
        if port.direction == "output":
            checks.append(TestCheck(
                signal=port.name,
                condition="==",
                expected="0",
                description=f"{port.name} should be 0 after reset",
            ))

    # Detect reset signal
    reset_name = "rst_n"
    reset_active_low = True
    if design.clock_domains:
        cd = design.clock_domains[0]
        reset_name = cd.associated_reset or "rst_n"
        reset_active_low = cd.reset_polarity == "active_low"

    rst_val = "0" if reset_active_low else "1"
    rst_release = "1" if reset_active_low else "0"

    stimulus = [
        StimulusStep(action="set", signal=reset_name, value=rst_val, description="Assert reset"),
        StimulusStep(action="wait", cycles=5, description="Hold reset for 5 cycles"),
        StimulusStep(action="assert", description="Check all outputs are at reset values"),
        StimulusStep(action="set", signal=reset_name, value=rst_release, description="Release reset"),
        StimulusStep(action="wait", cycles=2, description="Wait for reset release propagation"),
    ]

    return TestScenario(
        id=f"TP-{_short_id()}",
        name="test_reset_behavior",
        description="Verify all outputs assume correct reset values",
        requirement_ids=_match_requirement_ids(requirements, ["reset", "rst", "initial"]),
        priority="critical",
        category="reset",
        stimulus=stimulus,
        checks=checks,
    )


def _gen_requirement_scenario(idx: int, req: Any, design: Any) -> TestScenario:
    """Generate a test scenario for a specific requirement."""
    # Create stimulus based on requirement text analysis
    stimulus = [
        StimulusStep(
            action="set",
            description=f"Drive inputs for: {req.text[:80]}",
        ),
        StimulusStep(action="wait", cycles=10, description="Wait for response"),
    ]

    return TestScenario(
        id=f"TP-{_short_id()}",
        name=f"test_{req.id.lower().replace('-', '_')}",
        description=f"Verify: {req.text[:120]}",
        requirement_ids=[req.id],
        priority=req.priority,
        category="functional",
        precondition="after_reset",
        stimulus=stimulus,
        checks=[],
    )


def _gen_boundary_scenario(
    port: Any,
    design: Any,
    requirements: Optional[List[Any]] = None,
) -> TestScenario:
    """Generate boundary value tests for a wide port."""
    w = port.width
    max_val = (1 << w) - 1 if w <= 32 else 0xFFFFFFFF

    stimulus = [
        StimulusStep(action="set", signal=port.name, value="0", description="Min value"),
        StimulusStep(action="wait", cycles=2),
        StimulusStep(action="set", signal=port.name, value=str(max_val), description="Max value"),
        StimulusStep(action="wait", cycles=2),
        StimulusStep(action="set", signal=port.name, value="1", description="Min+1"),
        StimulusStep(action="wait", cycles=2),
        StimulusStep(action="set", signal=port.name, value=str(max_val - 1), description="Max-1"),
        StimulusStep(action="wait", cycles=2),
    ]

    return TestScenario(
        id=f"TP-{_short_id()}",
        name=f"test_boundary_{port.name}",
        description=f"Boundary value test for {port.name}[{w-1}:0]",
        requirement_ids=_match_requirement_ids(
            requirements,
            [port.name, "boundary", "min", "max", "width", "range"],
        ),
        priority="medium",
        category="boundary",
        precondition="after_reset",
        stimulus=stimulus,
    )


def _gen_protocol_scenario(
    protocol: Any,
    design: Any,
    requirements: Optional[List[Any]] = None,
) -> TestScenario:
    """Generate protocol-specific test scenario."""
    proto_name = str(_get(protocol, "protocol", "") or "interface")
    port_group = [
        str(name)
        for name in (_get(protocol, "port_group", []) or [])
        if str(name)
    ]
    port_by_name = {
        str(_get(port, "name", "") or ""): port
        for port in (_get(design, "ports", []) or [])
        if _get(port, "name", "")
    }

    def _is_driven(name: str) -> bool:
        direction = str(_get(port_by_name.get(name), "direction", "") or "").lower()
        return direction in {"input", "inout"} and not _is_clock_or_reset(name)

    def _is_observed(name: str) -> bool:
        direction = str(_get(port_by_name.get(name), "direction", "") or "").lower()
        return direction in {"output", "inout"}

    stimulus: List[StimulusStep] = []
    for name in port_group:
        lower = name.lower()
        if not _is_driven(name):
            continue
        if "valid" in lower or "sel" in lower or lower.endswith("_vld"):
            stimulus.append(StimulusStep(action="set", signal=name, value="1", description=f"Assert {name}"))
        elif "ready" in lower or "enable" in lower or lower.endswith("_en"):
            stimulus.append(StimulusStep(action="set", signal=name, value="1", description=f"Enable {name}"))
        elif any(token in lower for token in ("data", "addr", "payload", "write", "wdata", "awaddr")):
            stimulus.append(StimulusStep(action="set", signal=name, value="1", description=f"Drive representative {name}"))

    if not stimulus:
        stimulus.append(
            StimulusStep(
                action="wait",
                cycles=1,
                description=f"Observe {proto_name} interface activity",
            )
        )
    stimulus.append(StimulusStep(action="wait", cycles=2, description="Sample protocol response"))

    checks = [
        TestCheck(
            signal=name,
            condition="!=",
            expected="'x",
            description=f"{name} should be known during {proto_name} transaction",
        )
        for name in port_group
        if _is_observed(name)
    ][:6]

    return TestScenario(
        id=f"TP-{_short_id()}",
        name=f"test_protocol_{proto_name.lower().replace('-', '_')}",
        description=f"Verify {proto_name} protocol compliance ({_get(protocol, 'role', '')})",
        requirement_ids=_match_requirement_ids(
            requirements,
            [proto_name, "protocol", "valid", "ready", *port_group],
        ),
        priority="high",
        category="protocol",
        precondition="after_reset",
        stimulus=stimulus,
        checks=checks,
    )


def _gen_fsm_scenario(
    fsm: Any,
    design: Any,
    requirements: Optional[List[Any]] = None,
) -> TestScenario:
    """Generate FSM state coverage test."""
    stimulus = []
    for state in fsm.states:
        stimulus.append(StimulusStep(
            action="set",
            description=f"Drive inputs to reach state: {state}",
        ))
        stimulus.append(StimulusStep(action="wait", cycles=3))

    return TestScenario(
        id=f"TP-{_short_id()}",
        name=f"test_fsm_{fsm.name}",
        description=f"Visit all FSM states: {', '.join(fsm.states[:8])}",
        requirement_ids=_match_requirement_ids(
            requirements,
            [fsm.name, fsm.state_signal, "state", "fsm", *list(fsm.states or [])],
        ),
        priority="high",
        category="functional",
        precondition="after_reset",
        stimulus=stimulus,
    )


def _gen_stress_scenario(
    design: Any,
    requirements: Optional[List[Any]] = None,
) -> TestScenario:
    """Generate stress/rapid-toggle test."""
    input_ports = [p for p in design.ports if p.direction == "input"
                   and not _is_clock_or_reset(p.name)]

    stimulus = [
        StimulusStep(
            action="set",
            description="Rapidly toggle all inputs for 100 cycles",
        ),
        StimulusStep(action="wait", cycles=100),
    ]

    return TestScenario(
        id=f"TP-{_short_id()}",
        name="test_stress_rapid_toggle",
        description="Stress test: rapidly toggle all inputs, check no hangs or assertions",
        requirement_ids=_match_requirement_ids(
            requirements,
            ["stress", "burst", "backpressure", "throughput", "rapid", "toggle"],
        ),
        priority="low",
        category="stress",
        precondition="after_reset",
        stimulus=stimulus,
    )


def _is_clock_or_reset(name: str) -> bool:
    n = name.lower()
    return any(k in n for k in ("clk", "clock", "rst", "reset"))


def _match_requirement_ids(
    requirements: Optional[List[Any]],
    keywords: List[Any],
    *,
    limit: int = 8,
) -> List[str]:
    """Best-effort deterministic requirement linking for generated scenarios."""
    if not requirements:
        return []
    tokens = []
    for keyword in keywords:
        text = str(keyword or "").strip().lower()
        if len(text) < 3:
            continue
        tokens.append(text)
        tokens.append(text.replace("_", " "))
        tokens.append(text.replace("-", " "))
    tokens = list(dict.fromkeys(tokens))

    result: List[str] = []
    for req in requirements:
        req_id = str(_get(req, "id", "") or "")
        if not req_id:
            continue
        haystack = " ".join(
            str(_get(req, key, "") or "")
            for key in ("id", "text", "category", "priority")
        ).lower()
        if any(token and token in haystack for token in tokens):
            result.append(req_id)
        if len(result) >= limit:
            break
    return result


def _short_id() -> str:
    return str(uuid.uuid4())[:8]
