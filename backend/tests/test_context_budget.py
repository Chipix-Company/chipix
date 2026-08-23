"""Tests for context budgeting helpers."""

from __future__ import annotations

import json

from services.context_budget import (
    compute_input_token_budget,
    get_model_max_output_tokens,
    prune_messages_for_context,
    truncate_tool_result_content,
    trim_conversation_history,
)


def test_deepseek_v32_output_cap():
    assert get_model_max_output_tokens("deepseek.v3.2", "bedrock", 16384) == 8192
    assert get_model_max_output_tokens("deepseek.v3.2", "bedrock", 4096) == 4096


def test_input_budget_reserves_output():
    budget = compute_input_token_budget(
        model="deepseek.v3.2",
        provider="bedrock",
        requested_output_tokens=8192,
    )
    assert budget == 163_840 - 8192 - 2048


def test_trim_conversation_history_caps_message_size():
    history = [{"role": "assistant", "content": "x" * 20_000}]
    trimmed = trim_conversation_history(history, max_chars_per_message=1000)
    assert len(trimmed[0]["content"]) < 20_000
    assert "truncated" in trimmed[0]["content"]


def test_truncate_cadence_tool_result():
    payload = {
        "status": "failed",
        "run_id": "abc",
        "analysis": {"verdict": "fail", "summary": "compile error", "extra": "z" * 50_000},
        "feedback_memory": {"root_causes": [{"code": "E001", "message": "bad module"}]},
        "repair_history": [{"round": i} for i in range(10)],
        "closure_report": {"overall_status": "open", "summary": "needs fix"},
    }
    raw = json.dumps(payload)
    compact = truncate_tool_result_content("runCadenceSimulation", raw, max_chars=4000)
    assert len(compact) <= 4000
    parsed = json.loads(compact)
    assert parsed.get("run_id") == "abc"
    assert "root_causes" in (parsed.get("feedback_memory") or {})


def test_prune_messages_drops_middle_when_over_budget():
    system = {"role": "system", "content": "You are helpful."}
    huge = {"role": "user", "content": "A" * 500_000}
    messages = [system, huge]
    pruned = prune_messages_for_context(
        messages,
        model="deepseek.v3.2",
        provider="bedrock",
        requested_output_tokens=8192,
        max_chars_per_message=50_000,
    )
    assert len(pruned) >= 1
    assert estimate_tokens(pruned) < estimate_tokens(messages)


def estimate_tokens(messages):
    from services.context_budget import estimate_messages_tokens

    return estimate_messages_tokens(messages)
