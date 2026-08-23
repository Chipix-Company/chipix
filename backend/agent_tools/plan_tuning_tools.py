"""
Plan Tuning Tools — Natural language test plan refinement.

ChipStack feature: After displaying the test plan, the user can give
natural language feedback to tune it before testbench generation.

Tools:
  - tuneTestPlan: Add/remove/modify scenarios based on natural language
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List

from agent_tools import register_tool

logger = logging.getLogger(__name__)


async def handle_tune_test_plan(
    project_id: str,
    plan_json: str = "",
    feedback: str = "",
    action: str = "tune",
    scenario_id: str = "",
    ai_client: Any = None,
    **kwargs: Any,
) -> str:
    """
    Tune a test plan with natural language feedback.

    Actions:
      - "tune": Use natural language to modify the plan (add/remove/modify scenarios)
      - "add": Add a new scenario described in feedback text
      - "remove": Remove scenario by ID
      - "prioritize": Change priority of a scenario
      - "show": Display current plan summary

    Examples of natural language feedback:
      - "Add an overflow stress test"
      - "Make the reset test more aggressive — hold reset for 20 cycles"
      - "Remove the boundary test, it's not needed"
      - "Add a test for simultaneous read and write"
      - "Increase priority of the FSM test to critical"
    """
    from services.verification.test_plan import TestPlan, TestScenario, StimulusStep, TestCheck
    import uuid

    if not plan_json:
        return json.dumps({
            "error": "plan_json is required — provide the current test plan JSON",
        })

    if not feedback and action == "tune":
        return json.dumps({
            "error": "feedback is required — tell me how to change the plan",
            "hint": "Example: 'Add an overflow test' or 'Make reset test hold for 20 cycles'",
        })

    # Parse the current plan
    try:
        plan_data = json.loads(plan_json) if isinstance(plan_json, str) else plan_json
    except json.JSONDecodeError:
        return json.dumps({"error": "Invalid plan JSON"})

    scenarios = plan_data.get("scenarios", [])
    module_name = plan_data.get("module_name", "dut")

    if action == "tune" and ai_client:
        llm_result = await _try_llm_tune_plan(
            ai_client=ai_client,
            plan_data=plan_data,
            feedback=feedback,
            module_name=module_name,
        )
        if llm_result:
            return json.dumps(llm_result, indent=2)

    # ── Action: Show ─────────────────────────────────────────────
    if action == "show":
        return json.dumps({
            "module": module_name,
            "total_scenarios": len(scenarios),
            "scenarios": [
                {
                    "id": s.get("id", f"TP-{i}"),
                    "name": s.get("name", ""),
                    "category": s.get("category", ""),
                    "priority": s.get("priority", "medium"),
                    "description": s.get("description", ""),
                }
                for i, s in enumerate(scenarios)
            ],
            "_instruction_for_llm": (
                "Display this plan to the user in a nice table. "
                "Ask: 'Would you like to tune this plan? You can say things like:\n"
                "- \"Add an overflow test\"\n"
                "- \"Remove the stress test\"\n"
                "- \"Make reset hold for 20 cycles\"\n"
                "Or say \"Looks good, generate testbench\" to proceed.'"
            ),
        }, indent=2)

    # ── Action: Remove ───────────────────────────────────────────
    if action == "remove":
        if not scenario_id:
            # Try to find by name in feedback
            feedback_lower = feedback.lower()
            matching = [
                s for s in scenarios
                if feedback_lower in s.get("name", "").lower()
                or feedback_lower in s.get("description", "").lower()
            ]
            if matching:
                scenarios = [s for s in scenarios if s not in matching]
                removed_names = [s.get("name", "") for s in matching]
            else:
                return json.dumps({
                    "error": f"No scenario matching '{feedback}' found",
                    "available": [s.get("name", "") for s in scenarios],
                })
        else:
            before = len(scenarios)
            scenarios = [s for s in scenarios if s.get("id") != scenario_id]
            removed_names = [scenario_id] if len(scenarios) < before else []

        plan_data["scenarios"] = scenarios
        plan_data["total_scenarios"] = len(scenarios)

        return json.dumps({
            "status": "removed",
            "removed": removed_names,
            "total_scenarios": len(scenarios),
            "plan": plan_data,
            "_instruction_for_llm": (
                f"Removed {removed_names}. Show updated plan. "
                "Ask: 'Any other changes, or generate testbench?'"
            ),
        }, indent=2)

    # ── Action: Prioritize ───────────────────────────────────────
    if action == "prioritize":
        feedback_lower = feedback.lower()
        new_priority = "medium"
        for p in ["critical", "high", "medium", "low"]:
            if p in feedback_lower:
                new_priority = p
                break

        updated = []
        for s in scenarios:
            if (scenario_id and s.get("id") == scenario_id) or \
               any(word in s.get("name", "").lower() for word in feedback_lower.split()):
                s["priority"] = new_priority
                updated.append(s.get("name", ""))

        plan_data["scenarios"] = scenarios

        return json.dumps({
            "status": "updated_priority",
            "updated": updated,
            "new_priority": new_priority,
            "plan": plan_data,
        }, indent=2)

    # ── Action: Add ──────────────────────────────────────────────
    if action == "add":
        new_scenario = _parse_feedback_to_scenario(feedback, module_name)
        scenarios.append(new_scenario)
        plan_data["scenarios"] = scenarios
        plan_data["total_scenarios"] = len(scenarios)

        return json.dumps({
            "status": "added",
            "new_scenario": new_scenario,
            "total_scenarios": len(scenarios),
            "plan": plan_data,
            "_instruction_for_llm": (
                f"Added '{new_scenario['name']}'. Show updated plan. "
                "Ask: 'Any other changes, or generate testbench?'"
            ),
        }, indent=2)

    # ── Action: Tune (natural language) ──────────────────────────
    # Parse the feedback to determine what changes to make
    changes = _parse_natural_language_feedback(feedback, scenarios, module_name)

    for change in changes:
        if change["action"] == "add":
            scenarios.append(change["scenario"])
        elif change["action"] == "remove":
            scenarios = [s for s in scenarios if s.get("name") != change["target"]]
        elif change["action"] == "modify":
            for s in scenarios:
                if s.get("name") == change["target"] or s.get("id") == change.get("target_id"):
                    s.update(change.get("updates", {}))

    plan_data["scenarios"] = scenarios
    plan_data["total_scenarios"] = len(scenarios)

    return json.dumps({
        "status": "tuned",
        "changes_applied": [
            {"action": c["action"], "description": c.get("description", "")}
            for c in changes
        ],
        "total_scenarios": len(scenarios),
        "plan": plan_data,
        "_instruction_for_llm": (
            f"Applied {len(changes)} changes to the plan. "
            "Show the updated plan with changes highlighted. "
            "Ask: 'Any more changes, or shall I generate the testbench?'"
        ),
    }, indent=2)


async def _try_llm_tune_plan(
    ai_client: Any,
    plan_data: Dict[str, Any],
    feedback: str,
    module_name: str,
) -> Dict[str, Any] | None:
    """Use the configured LLM to refine a UnitSim plan, with validation."""
    import asyncio

    system_prompt = (
        "You are a chip verification planning agent. "
        "Refine UnitSim test plans using only the provided plan and user feedback. "
        "Return valid JSON only."
    )
    prompt = f"""Current UnitSim plan JSON:
```json
{json.dumps(plan_data, indent=2)[:12000]}
```

User feedback:
{feedback}

Return JSON in this exact shape:
{{
  "changes_applied": [
    {{"action": "add|remove|modify", "description": "what changed"}}
  ],
  "plan": {{
    "module_name": "{module_name}",
    "scenarios": [
      {{
        "id": "stable id",
        "name": "snake_case_name",
        "description": "what this scenario verifies",
        "requirement_ids": [],
        "priority": "critical|high|medium|low",
        "category": "reset|functional|boundary|stress|protocol",
        "precondition": "",
        "stimulus": [
          {{"action": "set|toggle|wait|assert", "signal": "", "value": "", "cycles": 1, "description": ""}}
        ],
        "checks": [
          {{"signal": "", "condition": "==|!=|>|<", "expected": "", "description": ""}}
        ],
        "cleanup": ""
      }}
    ],
    "coverage_goals": {{}},
    "total_scenarios": 0,
    "estimated_sim_time": ""
  }}
}}

Rules:
- Preserve existing scenarios unless the feedback asks to remove or change them.
- Do not invent RTL signal names if the current plan does not already contain them.
- Keep the output executable by the existing UnitSim testbench generator.
"""
    try:
        if hasattr(ai_client, "chat"):
            response = ai_client.chat(system_prompt, prompt)
        elif hasattr(ai_client, "generate"):
            response = ai_client.generate(
                prompt,
                system_prompt=system_prompt,
                temperature=0.2,
            )
        else:
            return None
        if asyncio.iscoroutine(response):
            response = await response

        text = str(response or "")
        match = re.search(r"\{[\s\S]*\}", text)
        if not match:
            return None
        data = json.loads(match.group())
        tuned_plan = data.get("plan")
        if not isinstance(tuned_plan, dict):
            return None
        scenarios = tuned_plan.get("scenarios")
        if not isinstance(scenarios, list) or not scenarios:
            return None

        allowed_scenario_keys = {
            "id",
            "name",
            "description",
            "requirement_ids",
            "priority",
            "category",
            "precondition",
            "stimulus",
            "checks",
            "cleanup",
        }
        allowed_stimulus_keys = {"action", "signal", "value", "cycles", "description"}
        allowed_check_keys = {"signal", "condition", "expected", "description"}
        cleaned_scenarios = []
        for index, raw in enumerate(scenarios):
            if not isinstance(raw, dict):
                continue
            scenario = {k: raw.get(k) for k in allowed_scenario_keys if k in raw}
            scenario["id"] = str(scenario.get("id") or f"TP-{index + 1:03d}")
            scenario["name"] = str(scenario.get("name") or f"test_{index + 1:03d}")
            scenario["description"] = str(scenario.get("description") or "")
            scenario["requirement_ids"] = list(scenario.get("requirement_ids") or [])
            scenario["priority"] = str(scenario.get("priority") or "medium")
            scenario["category"] = str(scenario.get("category") or "functional")
            scenario["precondition"] = str(scenario.get("precondition") or "")
            scenario["cleanup"] = str(scenario.get("cleanup") or "")
            scenario["stimulus"] = [
                {k: step.get(k) for k in allowed_stimulus_keys if k in step}
                for step in (scenario.get("stimulus") or [])
                if isinstance(step, dict)
            ]
            scenario["checks"] = [
                {k: check.get(k) for k in allowed_check_keys if k in check}
                for check in (scenario.get("checks") or [])
                if isinstance(check, dict)
            ]
            cleaned_scenarios.append(scenario)

        if not cleaned_scenarios:
            return None

        tuned_plan["module_name"] = str(tuned_plan.get("module_name") or module_name or "dut")
        tuned_plan["scenarios"] = cleaned_scenarios
        tuned_plan["total_scenarios"] = len(cleaned_scenarios)
        tuned_plan["coverage_goals"] = dict(tuned_plan.get("coverage_goals") or {})
        tuned_plan["estimated_sim_time"] = str(tuned_plan.get("estimated_sim_time") or "")

        changes = data.get("changes_applied")
        if not isinstance(changes, list):
            changes = [{"action": "modify", "description": "LLM refined the UnitSim plan"}]

        return {
            "status": "llm_tuned",
            "engine": "llm",
            "changes_applied": changes,
            "total_scenarios": len(cleaned_scenarios),
            "plan": tuned_plan,
            "_instruction_for_llm": (
                "Show the LLM-refined plan to the user and ask for approval "
                "before generating or executing verification."
            ),
        }
    except Exception as exc:
        logger.warning("LLM plan tuning failed, falling back to rules: %s", exc)
        return None


def _parse_feedback_to_scenario(feedback: str, module_name: str) -> dict:
    """Create a new scenario dict from natural language description."""
    import uuid

    # Extract a name from the feedback
    name_clean = re.sub(r"[^a-z0-9_\s]", "", feedback.lower())
    name_words = name_clean.split()[:5]
    name = "test_" + "_".join(name_words)

    # Detect category
    category = "functional"
    feedback_lower = feedback.lower()
    if any(w in feedback_lower for w in ["overflow", "stress", "rapid", "flood"]):
        category = "stress"
    elif any(w in feedback_lower for w in ["reset", "power"]):
        category = "reset"
    elif any(w in feedback_lower for w in ["boundary", "edge", "max", "min"]):
        category = "boundary"
    elif any(w in feedback_lower for w in ["protocol", "handshake", "axi", "apb"]):
        category = "protocol"

    # Detect priority
    priority = "medium"
    if any(w in feedback_lower for w in ["critical", "must", "essential"]):
        priority = "critical"
    elif any(w in feedback_lower for w in ["important", "high"]):
        priority = "high"

    return {
        "id": f"TP-{str(uuid.uuid4())[:8]}",
        "name": name,
        "description": feedback,
        "category": category,
        "priority": priority,
        "precondition": "after_reset",
        "requirement_ids": [],
        "stimulus": [
            {"action": "set", "description": feedback},
            {"action": "wait", "cycles": 10, "description": "Wait for response"},
        ],
        "checks": [],
    }


def _parse_natural_language_feedback(
    feedback: str, scenarios: list, module_name: str
) -> List[Dict]:
    """Parse natural language feedback into concrete plan changes."""
    changes = []
    feedback_lower = feedback.lower()

    # ── Detect "add" intent ──────────────────────────────────────
    add_patterns = [
        r"add\s+(?:a\s+)?(?:new\s+)?(?:test\s+)?(?:for\s+)?(.+)",
        r"include\s+(?:a\s+)?(.+?)(?:\s+test)?$",
        r"also\s+test\s+(.+)",
        r"create\s+(?:a\s+)?(.+?)(?:\s+test)?$",
    ]
    for pattern in add_patterns:
        match = re.search(pattern, feedback_lower)
        if match:
            desc = match.group(1).strip()
            new_scenario = _parse_feedback_to_scenario(desc, module_name)
            changes.append({
                "action": "add",
                "scenario": new_scenario,
                "description": f"Added test: {desc}",
            })
            return changes

    # ── Detect "remove" intent ───────────────────────────────────
    remove_patterns = [
        r"remove\s+(?:the\s+)?(.+?)(?:\s+test)?$",
        r"delete\s+(?:the\s+)?(.+?)(?:\s+test)?$",
        r"drop\s+(?:the\s+)?(.+?)(?:\s+test)?$",
        r"skip\s+(?:the\s+)?(.+?)(?:\s+test)?$",
        r"don'?t\s+(?:need|want)\s+(?:the\s+)?(.+?)(?:\s+test)?$",
    ]
    for pattern in remove_patterns:
        match = re.search(pattern, feedback_lower)
        if match:
            target = match.group(1).strip()
            # Find matching scenario
            for s in scenarios:
                if target in s.get("name", "").lower() or target in s.get("description", "").lower():
                    changes.append({
                        "action": "remove",
                        "target": s.get("name"),
                        "description": f"Removed: {s.get('name')}",
                    })
                    return changes

    # ── Detect "modify" intent ───────────────────────────────────
    modify_patterns = [
        r"make\s+(?:the\s+)?(.+?)\s+(?:test\s+)?(?:more\s+)?(.+)",
        r"change\s+(?:the\s+)?(.+?)\s+(?:test\s+)?to\s+(.+)",
        r"increase\s+(.+?)\s+(?:to\s+)?(\d+)",
        r"(?:set|use)\s+(\d+)\s+cycles?\s+(?:for\s+)?(.+)",
    ]
    for pattern in modify_patterns:
        match = re.search(pattern, feedback_lower)
        if match:
            target_hint = match.group(1).strip()
            modification = match.group(2).strip() if match.lastindex >= 2 else ""
            for s in scenarios:
                if target_hint in s.get("name", "").lower():
                    updates = {}
                    if "aggressive" in modification or "longer" in modification:
                        # Increase cycles in stimulus
                        for step in s.get("stimulus", []):
                            if "cycles" in step:
                                step["cycles"] = step.get("cycles", 1) * 4
                        updates["stimulus"] = s.get("stimulus", [])
                    if "critical" in modification:
                        updates["priority"] = "critical"
                    elif "high" in modification:
                        updates["priority"] = "high"
                    updates["description"] = s.get("description", "") + f" [Tuned: {feedback}]"
                    changes.append({
                        "action": "modify",
                        "target": s.get("name"),
                        "updates": updates,
                        "description": f"Modified: {s.get('name')} — {modification}",
                    })
                    return changes

    # ── Fallback: treat as "add" ─────────────────────────────────
    new_scenario = _parse_feedback_to_scenario(feedback, module_name)
    changes.append({
        "action": "add",
        "scenario": new_scenario,
        "description": f"Added test from feedback: {feedback}",
    })

    return changes


# Register tool
register_tool("tuneTestPlan", handle_tune_test_plan)
