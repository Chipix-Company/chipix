"""Cadence verification agent system prompt helpers."""

from __future__ import annotations

from typing import Any, Optional


CADENCE_VERIFICATION_SYSTEM_PROMPT = """\
You are the Cadence Verification Agent for Chipix Studio.

The user has generated UVM collateral and wants a full Cadence Xcelium simulation
(compile → elaborate → simulate) with automatic repair when possible.

Rules:
0. The active project_id is bound server-side from the user's session. Do NOT invent,
   guess, or substitute project IDs (mental_model_revision_id is NOT a project_id).
   Call runCadenceSimulation without project_id — the server uses the open project.
1. Call checkCadenceStatus first. If unavailable, explain how to configure Cadence.
2. Call runCadenceSimulation. Staged generated_artifact_ids are injected server-side
   when omitted. Only pass overrides if the user explicitly requests different artifacts.
3. On failure, use feedback_memory.root_causes. Apply fixes via applyCadenceFixes or
   applyCodeToFile, then re-run (max 3 agent repair rounds).
4. Report phase progress and final pass/fail clearly. Do not regenerate UVM unless files are missing.
5. After pass, summarize coverage and closure report highlights.
"""


def build_cadence_verification_prompt(context: dict) -> str:
    blocks = [CADENCE_VERIFICATION_SYSTEM_PROMPT]
    project_id = context.get("project_id")
    if project_id:
        blocks.append(f"Active project_id (server-bound): {str(project_id)[:140]}")
    revision_id = context.get("mental_model_revision_id")
    if revision_id:
        blocks.append(
            f"mental_model_revision_id (NOT a project_id): {str(revision_id)[:140]}"
        )
    artifact_ids = context.get("generated_artifact_ids")
    if isinstance(artifact_ids, list) and artifact_ids:
        blocks.append(
            "Staged/generated artifact IDs:\n"
            + "\n".join(f"- {str(aid)[:80]}" for aid in artifact_ids[:40])
        )
    top = context.get("uvm_top_module") or context.get("top_module")
    test = context.get("uvm_testname")
    if top:
        blocks.append(f"Top module: {top}")
    if test:
        blocks.append(f"UVM test: {test}")
    sim_status = context.get("cadence_simulation_status")
    if sim_status:
        blocks.append(f"Cadence simulation status: {sim_status}")
    brief = context.get("staged_execution_brief")
    if isinstance(brief, str) and brief.strip():
        blocks.append("Staged execution brief:\n" + brief.strip()[:4000])
    selected = context.get("selected_module")
    if selected:
        blocks.append(f"Graph-selected module: {selected}")
    return "\n\n".join(blocks)
