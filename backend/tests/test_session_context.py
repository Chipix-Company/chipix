"""Tests for session context injection into verification tool calls."""

from __future__ import annotations

import pytest

from services.session_context import merge_verification_args_from_context, resolve_project_id


def test_resolve_project_id_prefers_session():
    resolved = resolve_project_id(
        {"project_id": "wrong-id"},
        {"project_id": "session-proj-1"},
        tool_name="runCadenceSimulation",
    )
    assert resolved == "session-proj-1"


def test_resolve_project_id_falls_back_to_args():
    resolved = resolve_project_id(
        {"project_id": "arg-proj-1"},
        {},
        tool_name="runCadenceSimulation",
    )
    assert resolved == "arg-proj-1"


def test_resolve_project_id_required_raises():
    with pytest.raises(ValueError, match="No active project"):
        resolve_project_id({}, {}, tool_name="runCadenceSimulation", required=True)


def test_merge_injects_cadence_fields():
    merged = merge_verification_args_from_context(
        "runCadenceSimulation",
        {},
        {
            "project_id": "proj-abc",
            "generated_artifact_ids": ["art-1", "art-2"],
            "uvm_top_module": "top_tb",
            "uvm_testname": "fifo_base_test",
            "current_user": {"id": "user-1"},
        },
    )
    assert merged["project_id"] == "proj-abc"
    assert merged["generated_artifact_ids"] == ["art-1", "art-2"]
    assert merged["top_module"] == "top_tb"
    assert merged["uvm_testname"] == "fifo_base_test"
    assert merged["current_user"]["id"] == "user-1"


def test_merge_overrides_wrong_llm_project_id():
    merged = merge_verification_args_from_context(
        "runCadenceSimulation",
        {"project_id": "default", "generated_artifact_ids": ["x"]},
        {
            "project_id": "real-proj-uuid",
            "generated_artifact_ids": ["art-1"],
        },
    )
    assert merged["project_id"] == "real-proj-uuid"
    assert merged["generated_artifact_ids"] == ["x"]
