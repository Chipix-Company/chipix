"""Tests for Cadence verification prompt building."""

from __future__ import annotations

from services.cadence_agent_prompt import build_cadence_verification_prompt


def test_cadence_prompt_includes_project_id():
    prompt = build_cadence_verification_prompt(
        {
            "project_id": "7b2bd89a-7330-40ad-af6d-9d48484148c5",
            "generated_artifact_ids": ["art-1"],
            "uvm_top_module": "top_tb",
            "mental_model_revision_id": "b716838a-a2e5-47ac-9554-cd0db8bb62e4",
        }
    )
    assert "7b2bd89a-7330-40ad-af6d-9d48484148c5" in prompt
    assert "NOT a project_id" in prompt
    assert "server-bound" in prompt
