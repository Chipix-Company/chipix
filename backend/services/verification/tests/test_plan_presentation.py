"""Tests for verification plan presentation layer."""

from services.verification.plan_presentation import (
    build_verification_plan_presentation,
    merge_presentation_into_plan,
    refine_presentation_summary,
)


def test_build_verification_plan_presentation_includes_summary_and_warnings():
    plan = {
        "verification_type": "unitsim",
        "module_name": "fifo",
        "unitsim": {"total_scenarios": 2, "scenarios": [{"name": "reset_test"}]},
    }
    model_content = {
        "design": {"top_module": "fifo", "description": "Sync FIFO with gray pointers."},
        "requirements": [{"text": "Must handle full/empty flags"}],
    }
    readiness = {"warnings": ["No formal properties in RTL yet"], "blockers": []}

    presentation = build_verification_plan_presentation(
        plan,
        verification_type="unitsim",
        model_content=model_content,
        spec_filename="spec.txt",
        rtl_filename="fifo.sv",
        readiness=readiness,
    )

    assert "fifo" in presentation["summary"]
    assert any("spec.txt" in a for a in presentation["assumptions"])
    assert any("formal" in r.lower() for r in presentation["risks"])

    merged = merge_presentation_into_plan(plan, presentation)
    assert merged["presentation"]["summary"] == presentation["summary"]


def test_refine_presentation_summary_appends_feedback():
    plan = {
        "presentation": {"summary": "Original summary."},
        "unitsim": {"total_scenarios": 1},
    }
    out = refine_presentation_summary(plan, "add overflow stress test")
    assert "overflow" in out["presentation"]["summary"].lower()
