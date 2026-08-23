"""Living MentalModelAgent observer.

This module keeps the mental model active after its initial build. It observes
verification workflow events, records durable evidence, and safely applies
approved verification intent back into the model.
"""

from __future__ import annotations

import json
import logging
import os
import re
import uuid
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from database.models import MentalModelRevision, User
from services.mental_model.builder import _parse_llm_json_object

logger = logging.getLogger(__name__)

MAX_OBSERVED_EVENTS = 200
MAX_PENDING_UPDATES = 100
MAX_EVIDENCE = 300
REVIEWED_PLAN_EVENT_TYPES = {
    "uvm.plan.generated",
    "uvm.plan.refined",
    "uvm.plan.approved",
    "formal.plan.generated",
    "unitsim.plan.generated",
}


from common.env import env_bool as _env_flag
def _demo_relaxed_readiness_enabled() -> bool:
    """Allow demos to proceed while still surfacing open-question warnings."""
    return _env_flag("CHIPVERIFY_DEMO_RELAX_MENTAL_MODEL_GATES", True)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_content(model_revision: MentalModelRevision) -> Dict[str, Any]:
    try:
        content = json.loads(model_revision.content_json or "{}")
    except Exception:
        content = {}
    return content if isinstance(content, dict) else {}


def _compact_list(items: Any, limit: int = 24) -> list[Any]:
    if not isinstance(items, list):
        return []
    return deepcopy(items[:limit])


def _compact_uvm_plan(plan: Dict[str, Any]) -> Dict[str, Any]:
    uvm = plan.get("uvm") if isinstance(plan, dict) else {}
    if not isinstance(uvm, dict):
        uvm = {}
    return {
        "top_module": uvm.get("top_module") or plan.get("module_name"),
        "agents": _compact_list(uvm.get("agents"), 16),
        "sequences": _compact_list(uvm.get("sequences"), 32),
        "scoreboard_checks": _compact_list(uvm.get("scoreboard_checks"), 32),
        "coverage_points": _compact_list(uvm.get("coverage_points"), 32),
        "assumptions": _compact_list(uvm.get("assumptions"), 16),
        "open_questions": _compact_list(uvm.get("open_questions"), 16),
        "files_preview": _compact_list(uvm.get("files_preview"), 64),
    }


def _compact_plan_for_review(event_type: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    plan = payload.get("plan") or {}
    if event_type.startswith("uvm."):
        return _compact_uvm_plan(plan)
    if not isinstance(plan, dict):
        return {}

    section_name = "formal" if event_type.startswith("formal.") else "unitsim"
    section = plan.get(section_name)
    if not isinstance(section, dict):
        section = {}

    compact_section: Dict[str, Any] = {}
    for key, value in section.items():
        if isinstance(value, list):
            compact_section[key] = _compact_list(value, 32)
        elif isinstance(value, dict):
            compact_section[key] = deepcopy(value)
        elif isinstance(value, (str, int, float, bool)) or value is None:
            compact_section[key] = value

    return {
        "verification_type": plan.get("verification_type") or payload.get("verification_type"),
        "module_name": plan.get("module_name"),
        section_name: compact_section,
    }


def _plan_summary(plan: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(plan, dict):
        return {}
    uvm = plan.get("uvm") if isinstance(plan.get("uvm"), dict) else {}
    unitsim = plan.get("unitsim") if isinstance(plan.get("unitsim"), dict) else {}
    formal = plan.get("formal") if isinstance(plan.get("formal"), dict) else {}
    return {
        "verification_type": plan.get("verification_type"),
        "module_name": plan.get("module_name"),
        "uvm_agents": len(uvm.get("agents") or []),
        "uvm_sequences": len(uvm.get("sequences") or []),
        "uvm_scoreboard_checks": len(uvm.get("scoreboard_checks") or []),
        "uvm_coverage_points": len(uvm.get("coverage_points") or []),
        "unitsim_scenarios": len(unitsim.get("scenarios") or []),
        "formal_properties": formal.get("property_count"),
    }


def _dedupe_strings(values: Any) -> list[str]:
    result: list[str] = []
    raw_values = values if isinstance(values, list) else [values]
    for value in raw_values:
        text = str(value or "").strip()
        if text and text not in result:
            result.append(text)
    return result


def _collect_requirement_ids(value: Any) -> list[str]:
    """Collect requirement links from nested plan/result payloads."""
    found: list[str] = []

    def visit(item: Any) -> None:
        if isinstance(item, dict):
            for key, nested in item.items():
                key_lower = str(key).lower()
                if key_lower in {"requirement_ids", "related_requirements", "requirements"}:
                    if isinstance(nested, list):
                        found.extend(str(req) for req in nested if str(req or "").strip())
                    elif isinstance(nested, str):
                        found.extend(re.findall(r"\bREQ[-_]\d+\b", nested, re.IGNORECASE))
                else:
                    visit(nested)
        elif isinstance(item, list):
            for nested in item:
                visit(nested)
        elif isinstance(item, str):
            found.extend(re.findall(r"\bREQ[-_]\d+\b", item, re.IGNORECASE))

    visit(value)
    return _dedupe_strings(found)


def _collect_verification_item_ids(plan: Dict[str, Any]) -> list[str]:
    """Collect planned test/assertion/sequence IDs used for requirement closure."""
    if not isinstance(plan, dict):
        return []
    ids: list[str] = []
    unitsim = plan.get("unitsim") if isinstance(plan.get("unitsim"), dict) else {}
    for scenario in unitsim.get("scenarios") or []:
        if isinstance(scenario, dict):
            ids.append(str(scenario.get("id") or scenario.get("name") or ""))
    uvm = plan.get("uvm") if isinstance(plan.get("uvm"), dict) else {}
    for seq in uvm.get("sequences") or []:
        if isinstance(seq, dict):
            ids.append(str(seq.get("id") or seq.get("name") or ""))
    formal = plan.get("formal") if isinstance(plan.get("formal"), dict) else {}
    for prop in formal.get("properties") or formal.get("assertions") or []:
        if isinstance(prop, dict):
            ids.append(str(prop.get("id") or prop.get("name") or ""))
    return _dedupe_strings(ids)


def _closure_ids_for_event(
    event_type: str,
    payload: Dict[str, Any],
    evidence: Dict[str, Any],
) -> list[str]:
    if not _is_trusted_verification_event(event_type, payload):
        return []
    ids: list[str] = []
    ids.append(str(payload.get("run_id") or evidence.get("run_id") or ""))
    ids.append(str(evidence.get("id") or ""))
    return _dedupe_strings(ids)


def _is_trusted_verification_event(event_type: str, payload: Dict[str, Any]) -> bool:
    """Return True only for evidence that can close a requirement."""
    status = str(payload.get("status") or "").strip().lower()
    trusted = payload.get("trusted")
    if "validation" in event_type:
        return bool(trusted)
    if "proof" in event_type:
        return trusted is not False and status in {"passed", "proved", "valid", "success"}
    if "coverage" in event_type and "result" in event_type:
        return trusted is not False and status in {"passed", "met", "covered", "success"}
    if "result" in event_type or event_type.endswith(".completed"):
        return trusted is not False and status in {
            "passed",
            "validated",
            "proved",
            "success",
            "completed",
        }
    return False


def _traceability_stage_for_event(event_type: str, payload: Dict[str, Any]) -> str:
    if _is_trusted_verification_event(event_type, payload):
        return "verified_by"
    if "validation" in event_type or "result" in event_type or "proof" in event_type:
        return "failed_by"
    if "plan" in event_type:
        return "planned_by"
    if "files.generated" in event_type or "artifacts" in event_type:
        return "generated_by"
    return "observed_by"


def _traceability_refs_for_event(
    event_type: str,
    payload: Dict[str, Any],
    evidence: Dict[str, Any],
) -> list[str]:
    refs: list[str] = []
    if "plan" in event_type:
        refs.extend(_collect_verification_item_ids(payload.get("plan") or {}))
    refs.extend(str(item) for item in (payload.get("artifact_ids") or []))
    refs.append(str(payload.get("run_id") or evidence.get("run_id") or ""))
    refs.append(str(evidence.get("id") or ""))
    return _dedupe_strings(refs)


def _update_requirement_traceability(
    content: Dict[str, Any],
    requirement_ids: list[str],
    stage: str,
    refs: list[str],
    evidence: Dict[str, Any],
) -> int:
    if not requirement_ids or not refs:
        return 0
    traceability = content.setdefault("traceability", {})
    matrix = traceability.setdefault("requirements", {})
    if not isinstance(matrix, dict):
        matrix = {}
        traceability["requirements"] = matrix

    updated = 0
    event_type = str((evidence.get("details") or {}).get("event_type") or "")
    for req_id in _dedupe_strings(requirement_ids):
        row = matrix.setdefault(
            req_id,
            {
                "requirement_id": req_id,
                "planned_by": [],
                "generated_by": [],
                "verified_by": [],
                "failed_by": [],
                "observed_by": [],
                "evidence": [],
                "status": "unplanned",
            },
        )
        if not isinstance(row, dict):
            continue
        bucket = row.setdefault(stage, [])
        if not isinstance(bucket, list):
            bucket = []
            row[stage] = bucket
        before = len(bucket)
        for ref in refs:
            if ref and ref not in bucket:
                bucket.append(ref)
        evidence_refs = row.setdefault("evidence", [])
        if isinstance(evidence_refs, list) and evidence.get("id") not in evidence_refs:
            evidence_refs.append(evidence.get("id"))
        events = row.setdefault("events", [])
        if isinstance(events, list):
            events.append(
                {
                    "event_type": event_type,
                    "evidence_id": evidence.get("id"),
                    "timestamp": evidence.get("timestamp"),
                    "stage": stage,
                }
            )
            if len(events) > 40:
                del events[: len(events) - 40]

        latest_closure_stage = ""
        if isinstance(events, list):
            for item in reversed(events):
                if not isinstance(item, dict):
                    continue
                stage_name = str(item.get("stage") or "")
                if stage_name in {"verified_by", "failed_by"}:
                    latest_closure_stage = stage_name
                    break

        if latest_closure_stage == "failed_by":
            row["status"] = "needs_debug"
        elif latest_closure_stage == "verified_by":
            row["status"] = "verified"
        elif row.get("failed_by"):
            row["status"] = "needs_debug"
        elif row.get("verified_by"):
            row["status"] = "verified"
        elif row.get("generated_by"):
            row["status"] = "generated"
        elif row.get("planned_by"):
            row["status"] = "planned"
        else:
            row["status"] = "observed"
        if len(bucket) != before:
            updated += 1

    _refresh_traceability_summary(content)
    return updated


def _refresh_traceability_summary(content: Dict[str, Any]) -> None:
    requirements = content.get("requirements") if isinstance(content.get("requirements"), list) else []
    matrix = (
        (content.get("traceability") or {}).get("requirements")
        if isinstance(content.get("traceability"), dict)
        else {}
    )
    if not isinstance(matrix, dict):
        matrix = {}
    total = len([req for req in requirements if isinstance(req, dict)])
    statuses = {"verified": 0, "needs_debug": 0, "generated": 0, "planned": 0, "unplanned": 0}
    for req in requirements:
        if not isinstance(req, dict):
            continue
        req_id = str(req.get("id") or "")
        row = matrix.get(req_id) if req_id else None
        status = str((row or {}).get("status") or "unplanned")
        statuses[status if status in statuses else "unplanned"] += 1
    content.setdefault("traceability", {})["summary"] = {
        "total_requirements": total,
        **statuses,
        "verified_percent": round((statuses["verified"] / total) * 100, 2) if total else 0.0,
    }


def _mark_requirements_verified(
    content: Dict[str, Any],
    requirement_ids: list[str],
    verified_by: list[str],
) -> int:
    requirements = content.get("requirements")
    if not isinstance(requirements, list) or not requirement_ids or not verified_by:
        return 0
    wanted = set(requirement_ids)
    updated = 0
    for req in requirements:
        if not isinstance(req, dict):
            continue
        req_id = str(req.get("id") or "")
        if req_id not in wanted:
            continue
        current = req.get("verified_by")
        if not isinstance(current, list):
            current = []
        before = len(current)
        for item in verified_by:
            if item and item not in current:
                current.append(item)
        req["verified_by"] = current
        if len(current) != before:
            updated += 1
    return updated


def _ensure_living_state(content: Dict[str, Any]) -> Dict[str, Any]:
    living = content.setdefault("living_agent", {})
    living.setdefault("status", "active")
    living.setdefault("created_at", _now())
    living["updated_at"] = _now()
    living.setdefault("observed_events", [])
    living.setdefault("pending_updates", [])
    living.setdefault("verification_memory", {})
    living.setdefault("agent_reviews", [])
    return living


def _append_limited(items: list[Any], item: Any, max_items: int) -> None:
    items.append(item)
    if len(items) > max_items:
        del items[: len(items) - max_items]


def _event_summary(event_type: str, payload: Dict[str, Any]) -> str:
    if event_type == "mental_model.readiness.checked":
        readiness = payload.get("readiness") or {}
        status = readiness.get("status") or "unknown"
        blockers = len(readiness.get("blockers") or [])
        return f"Mental model readiness checked: {status} ({blockers} blocker(s))."
    if event_type == "verification.strategy_selected":
        return f"User selected {payload.get('verification_type', 'verification')} strategy."
    if event_type == "uvm.plan.generated":
        summary = _plan_summary(payload.get("plan") or {})
        return (
            "UVM plan generated: "
            f"{summary.get('uvm_agents', 0)} agents, "
            f"{summary.get('uvm_sequences', 0)} sequences, "
            f"{summary.get('uvm_coverage_points', 0)} coverage points."
        )
    if event_type == "uvm.plan.refined":
        return f"UVM plan refined from feedback: {payload.get('feedback', '')[:120]}"
    if event_type == "uvm.plan.approved":
        return "User approved the UVM plan as verification intent."
    if event_type == "uvm.files.generated":
        return f"UVM generation completed with {payload.get('file_count', 0)} file(s)."
    if event_type == "uvm.validation.completed":
        validation = payload.get("validation") or {}
        status = validation.get("status") or "unknown"
        errors = validation.get("errors", 0)
        warnings = validation.get("warnings", 0)
        return f"UVM validation {status}: {errors} error(s), {warnings} warning(s)."
    if event_type.endswith(".plan.approved"):
        return "User approved a verification plan."
    if event_type.endswith(".files.generated"):
        return "Verification generated artifacts."
    return event_type.replace(".", " ")


def _evidence_type(event_type: str) -> str:
    if "readiness" in event_type:
        return "mental_model_gate"
    if "strategy_selected" in event_type:
        return "strategy_selection"
    if "validation" in event_type:
        return "validation_result"
    if "plan" in event_type:
        return "verification_plan"
    if "files.generated" in event_type:
        return "generated_artifacts"
    if "result" in event_type:
        return "verification_result"
    return "workflow_observation"


def _make_evidence(event_id: str, event_type: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    requirement_ids = _dedupe_strings(
        list(payload.get("requirement_ids") or [])
        + _collect_requirement_ids(payload.get("plan") or {})
    )
    return {
        "id": f"EVID-{str(uuid.uuid4())[:8]}",
        "run_id": str(payload.get("run_id") or ""),
        "agent": str(payload.get("agent") or payload.get("verification_type") or "mental_model"),
        "evidence_type": _evidence_type(event_type),
        "summary": _event_summary(event_type, payload),
        "details": {
            "event_id": event_id,
            "event_type": event_type,
            "plan_summary": _plan_summary(payload.get("plan") or {}),
            "artifact_ids": payload.get("artifact_ids") or [],
            "file_count": payload.get("file_count"),
            "readiness": payload.get("readiness") or {},
            "validation": payload.get("validation") or {},
            "compile_gate": payload.get("compile_gate") or {},
            "trusted": payload.get("trusted"),
            "status": payload.get("status"),
            "requirement_ids": requirement_ids,
        },
        "requirement_ids": requirement_ids,
        "timestamp": _now(),
    }


def _approved_uvm_intent_from_plan(plan: Dict[str, Any]) -> Dict[str, Any]:
    uvm = plan.get("uvm") if isinstance(plan, dict) else {}
    if not isinstance(uvm, dict):
        return {}

    scenarios = []
    for index, seq in enumerate(uvm.get("sequences") or []):
        if not isinstance(seq, dict):
            continue
        scenarios.append({
            "id": seq.get("id") or f"UVM-SEQ-{index + 1:03d}",
            "name": seq.get("name") or f"uvm_sequence_{index + 1}",
            "description": seq.get("description") or "",
            "requirement_ids": list(seq.get("requirement_ids") or seq.get("related_requirements") or []),
            "sequence_type": seq.get("sequence_type") or "directed",
            "stimulus_pattern": seq.get("stimulus_pattern") or "",
            "checker_description": seq.get("checker_description") or "",
            "status": "approved",
        })

    return {
        "uvm_scenarios": scenarios,
        "uvm_agents": _compact_list(uvm.get("agents"), 64),
        "uvm_scoreboard_checks": _compact_list(uvm.get("scoreboard_checks"), 64),
        "uvm_coverage_points": _compact_list(uvm.get("coverage_points"), 64),
        "uvm_files_preview": _compact_list(uvm.get("files_preview"), 128),
        "uvm_plan_approved_at": _now(),
    }


def _blocking_open_questions_from_schema(
    content: Dict[str, Any],
    raw_open_questions: List[Any],
) -> List[Dict[str, Any]]:
    """Use schema semantics first, then fall back to raw legacy dictionaries."""
    blocking: List[Dict[str, Any]] = []
    seen: set[str] = set()

    def _add(question: str, source: str = "raw") -> None:
        text = str(question or "").strip()
        if not text or text.lower() in seen:
            return
        seen.add(text.lower())
        blocking.append({"question": text, "source": source})

    schema_error = ""
    try:
        from services.mental_model.schema import MentalModelSchema

        model = MentalModelSchema.from_dict(content)
        for item in model.open_questions:
            if item.blocking and not item.answered:
                _add(item.question, "schema")
    except Exception as exc:
        schema_error = str(exc)
        logger.warning("Falling back to raw open-question readiness parsing: %s", exc)

    for item in raw_open_questions:
        if not isinstance(item, dict):
            continue
        if (
            item.get("blocking") is True
            or str(item.get("severity") or "").lower() == "blocking"
        ) and not item.get("answered"):
            _add(str(item.get("question") or ""), "raw")

    if schema_error and raw_open_questions and not blocking:
        _add(
            "Mental model open questions could not be parsed cleanly; refresh TruthCore before relying on this plan.",
            "schema_error",
        )

    return blocking


def _readiness_ports(content: Dict[str, Any], design: Dict[str, Any]) -> list[Any]:
    ports = design.get("ports") if isinstance(design.get("ports"), list) else []
    if ports:
        return ports
    top_level_ports = content.get("ports") if isinstance(content.get("ports"), list) else []
    if top_level_ports:
        return top_level_ports

    block_models = content.get("block_models")
    if not isinstance(block_models, dict):
        return []

    top_module = str(design.get("top_module") or content.get("top_module") or "").strip()
    if top_module:
        top_block = block_models.get(top_module)
        if isinstance(top_block, dict) and isinstance(top_block.get("ports"), list):
            return top_block["ports"]

    aggregated: list[Any] = []
    for block in block_models.values():
        if isinstance(block, dict) and isinstance(block.get("ports"), list):
            aggregated.extend(block["ports"])
    return aggregated


def assess_mental_model_readiness(
    content: Dict[str, Any],
    *,
    stage: str,
    verification_type: str = "",
    approved_plan: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Return a conservative gate result for mental-model-backed workflows."""
    design = content.get("design") if isinstance(content.get("design"), dict) else {}
    verification = (
        content.get("verification")
        if isinstance(content.get("verification"), dict)
        else {}
    )
    scan = (
        content.get("project_scan")
        if isinstance(content.get("project_scan"), dict)
        else {}
    )
    requirements = content.get("requirements") if isinstance(content.get("requirements"), list) else []
    open_questions = content.get("open_questions") if isinstance(content.get("open_questions"), list) else []
    llm_status = str(content.get("llm_status") or "").strip()
    build_mode = str(content.get("build_mode") or "").strip()

    blockers: list[str] = []
    warnings: list[str] = []

    top_module = str(design.get("top_module") or content.get("top_module") or "").strip()
    ports = _readiness_ports(content, design)
    if not top_module:
        blockers.append("Mental model has no top module.")
    if not ports:
        blockers.append("Mental model has no top-level ports.")

    rtl_files = scan.get("rtl_files") if isinstance(scan.get("rtl_files"), list) else []
    if not rtl_files and not design.get("total_files"):
        warnings.append("Mental model does not record source RTL files.")

    llm_good_states = {
        "parsed",
        "multi_block_parsed",
        "multi_block_partial",
        "structure_with_llm_attempt",
    }
    if build_mode not in {"llm_enriched", "llm_attempted_source_grounded"}:
        warnings.append("Mental model was not built in LLM-enriched mode.")
    if llm_status and llm_status not in llm_good_states:
        warnings.append(f"Mental model LLM status is '{llm_status}'.")

    blocking_questions = _blocking_open_questions_from_schema(content, open_questions)
    if blocking_questions:
        question_gate = f"{len(blocking_questions)} blocking open mental-model question(s) need answers."
        if _demo_relaxed_readiness_enabled():
            warnings.append(f"Demo mode: proceeding despite {question_gate}")
        else:
            blockers.append(question_gate)

    vtype = (verification_type or "").lower()
    if stage in {"plan", "execute"} and vtype in {"unitsim", "formal", "uvm", "all"}:
        if not requirements:
            warnings.append("No source-grounded requirements are captured; generated plans may be structural only.")

    if stage == "execute" and vtype in {"uvm", "all"}:
        plan = approved_plan or {}
        uvm = plan.get("uvm") if isinstance(plan, dict) else {}
        if not isinstance(uvm, dict):
            blockers.append("Approved UVM plan is missing.")
        else:
            if not uvm.get("agents"):
                warnings.append("Approved UVM plan has no explicit agents; treating as single-agent UVM.")
            if not uvm.get("sequences"):
                blockers.append("Approved UVM plan has no sequences.")
            if not uvm.get("scoreboard_checks"):
                blockers.append("Approved UVM plan has no scoreboard checks.")
            if not uvm.get("coverage_points"):
                blockers.append("Approved UVM plan has no coverage points.")

    if stage == "execute" and vtype in {"formal", "all"}:
        formal_intent = verification.get("formal_properties") or []
        if not formal_intent and not ((approved_plan or {}).get("formal") or {}).get("property_count"):
            warnings.append("No formal properties are captured; formal output may be scaffold-only.")

    status = "blocked" if blockers else ("warning" if warnings else "ready")
    return {
        "stage": stage,
        "verification_type": verification_type,
        "status": status,
        "safe": not blockers,
        "blockers": blockers,
        "warnings": warnings,
        "model_summary": {
            "top_module": top_module,
            "ports": len(ports),
            "requirements": len(requirements),
            "open_questions": len(open_questions),
            "llm_status": llm_status,
            "build_mode": build_mode,
        },
    }


async def _review_plan_with_llm(
    content: Dict[str, Any],
    event_type: str,
    payload: Dict[str, Any],
    ai_client: Any,
) -> Optional[Dict[str, Any]]:
    if not ai_client or not hasattr(ai_client, "chat"):
        return None
    if event_type not in REVIEWED_PLAN_EVENT_TYPES:
        return None

    design = content.get("design") if isinstance(content.get("design"), dict) else {}
    plan_kind = event_type.split(".", 1)[0].upper()
    system_prompt = (
        "You are a living MentalModelAgent for chip verification. Review a "
        "verification plan against the source-grounded mental model. Return JSON only."
    )
    prompt = f"""Review this {plan_kind} plan as a living mental model observer.

DESIGN SUMMARY:
{json.dumps({
    "top_module": design.get("top_module"),
    "ports": [
        {"name": p.get("name"), "direction": p.get("direction"), "width": p.get("width")}
        for p in (design.get("ports") or [])[:80]
        if isinstance(p, dict)
    ],
    "protocols": design.get("protocols", [])[:20] if isinstance(design.get("protocols"), list) else [],
    "requirements": content.get("requirements", [])[:40] if isinstance(content.get("requirements"), list) else [],
    "open_questions": content.get("open_questions", [])[:20] if isinstance(content.get("open_questions"), list) else [],
}, indent=2)}

VERIFICATION PLAN:
{json.dumps(_compact_plan_for_review(event_type, payload), indent=2)}

Return one JSON object:
{{
  "summary": "short review",
  "plan_quality": "good|needs_refinement|blocked",
  "missing_items": ["missing checker/coverage/sequence"],
  "suggested_refinements": ["specific natural-language refinement"],
  "open_questions": ["question for user if needed"],
  "safe_to_execute": true
}}

Rules:
- Do not invent signals not present in DESIGN SUMMARY ports.
- If an item cannot be verified from the model, put it in open_questions.
- Suggestions are proposals only; do not claim the plan changed.
"""
    try:
        response = await ai_client.chat(system_prompt, prompt)
        parsed = _parse_llm_json_object(response if isinstance(response, str) else str(response))
        if isinstance(parsed, dict):
            parsed["reviewed_at"] = _now()
            parsed["event_type"] = event_type
            return parsed
    except Exception as exc:
        logger.warning("Living MentalModelAgent review failed: %s", exc)
    return None


async def observe_mental_model_event(
    db: Session,
    *,
    model_revision: MentalModelRevision,
    user: User,
    event_type: str,
    payload: Dict[str, Any],
    source: str = "staged_verification",
    ai_client: Any = None,
    apply_approved_intent: bool = True,
    commit: bool = True,
) -> Dict[str, Any]:
    """Observe a workflow event and update mental-model memory/evidence."""
    content = _load_content(model_revision)
    living = _ensure_living_state(content)
    event_id = f"MMEVT-{str(uuid.uuid4())[:8]}"
    event = {
        "id": event_id,
        "type": event_type,
        "source": source,
        "user_id": user.id,
        "timestamp": _now(),
        "summary": _event_summary(event_type, payload),
        "payload_summary": {
            "verification_type": payload.get("verification_type"),
            "plan_summary": _plan_summary(payload.get("plan") or {}),
            "feedback": str(payload.get("feedback") or "")[:300],
            "file_count": payload.get("file_count"),
            "artifact_ids": payload.get("artifact_ids") or [],
        },
    }
    _append_limited(living["observed_events"], event, MAX_OBSERVED_EVENTS)

    evidence = _make_evidence(event_id, event_type, payload)
    _append_limited(content.setdefault("evidence", []), evidence, MAX_EVIDENCE)

    memory = living.setdefault("verification_memory", {})
    if event_type == "verification.strategy_selected":
        memory["selected_strategy"] = {
            "verification_type": payload.get("verification_type"),
            "selected_at": event["timestamp"],
            "event_id": event_id,
        }

    if "uvm" in event_type and isinstance(payload.get("plan"), dict):
        memory["latest_uvm_plan"] = {
            "event_id": event_id,
            "event_type": event_type,
            "updated_at": event["timestamp"],
            "plan": _compact_uvm_plan(payload["plan"]),
        }

    if event_type == "mental_model.readiness.checked":
        memory["latest_readiness_gate"] = {
            "event_id": event_id,
            "updated_at": event["timestamp"],
            "readiness": payload.get("readiness") or {},
        }

    if event_type == "uvm.validation.completed":
        memory["latest_uvm_validation"] = {
            "event_id": event_id,
            "updated_at": event["timestamp"],
            "validation": payload.get("validation") or {},
        }
        memory["latest_uvm_compile_gate"] = {
            "event_id": event_id,
            "updated_at": event["timestamp"],
            "compile_gate": payload.get("compile_gate") or {},
            "trusted": bool(payload.get("trusted")),
        }

    if event_type == "unitsim.result.completed":
        memory["latest_unitsim_result"] = {
            "event_id": event_id,
            "updated_at": event["timestamp"],
            "run_id": payload.get("run_id"),
            "status": payload.get("status"),
            "trusted": bool(payload.get("trusted")),
            "result": payload.get("result") or {},
            "artifact_ids": payload.get("artifact_ids") or [],
        }

    if event_type.startswith("formal."):
        memory["latest_formal_result"] = {
            "event_id": event_id,
            "event_type": event_type,
            "updated_at": event["timestamp"],
            "run_id": payload.get("run_id"),
            "status": payload.get("status"),
            "trusted": bool(payload.get("trusted")),
            "result": payload.get("result") or payload.get("formal_result") or {},
            "artifact_ids": payload.get("artifact_ids") or [],
        }

    review = await _review_plan_with_llm(content, event_type, payload, ai_client)
    if review:
        _append_limited(living["agent_reviews"], review, MAX_OBSERVED_EVENTS)

    applied_updates: Dict[str, Any] = {}
    if apply_approved_intent and event_type == "uvm.plan.approved":
        approved_intent = _approved_uvm_intent_from_plan(payload.get("plan") or {})
        if approved_intent:
            verification = content.setdefault("verification", {})
            if isinstance(verification, dict):
                verification.update(approved_intent)
                applied_updates = approved_intent

    requirement_ids = list(evidence.get("requirement_ids") or [])
    trace_stage = _traceability_stage_for_event(event_type, payload)
    trace_refs = _traceability_refs_for_event(event_type, payload, evidence)
    trace_count = _update_requirement_traceability(
        content,
        requirement_ids,
        trace_stage,
        trace_refs,
        evidence,
    )
    if trace_count:
        applied_updates["requirement_traceability"] = {
            "updated_count": trace_count,
            "stage": trace_stage,
            "requirement_ids": requirement_ids,
        }
    closure_ids = _closure_ids_for_event(event_type, payload, evidence)
    verified_count = _mark_requirements_verified(content, requirement_ids, closure_ids)
    if verified_count:
        applied_updates["requirements_verified_by"] = {
            "updated_count": verified_count,
            "requirement_ids": requirement_ids,
            "verified_by": closure_ids,
        }
        memory["latest_requirement_closure"] = {
            "event_id": event_id,
            "updated_at": event["timestamp"],
            "updated_count": verified_count,
            "requirement_ids": requirement_ids,
            "verified_by": closure_ids,
        }

    pending_update = {
        "id": f"MMPROP-{str(uuid.uuid4())[:8]}",
        "event_id": event_id,
        "status": "observed" if applied_updates else "pending_review",
        "target": "verification",
        "summary": _event_summary(event_type, payload),
        "created_at": _now(),
        "review": review,
        "applied_fields": sorted(applied_updates.keys()),
    }
    _append_limited(living["pending_updates"], pending_update, MAX_PENDING_UPDATES)

    model_revision.content_json = json.dumps(content, indent=2, default=str)
    db.add(model_revision)
    if commit:
        db.commit()
        db.refresh(model_revision)

    return {
        "event_id": event_id,
        "event_type": event_type,
        "summary": event["summary"],
        "evidence_id": evidence["id"],
        "review": review,
        "applied_fields": sorted(applied_updates.keys()),
    }
