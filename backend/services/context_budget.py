"""Token-aware context budgeting for agentic LLM calls.

DeepSeek V3.2 on Bedrock exposes a 163,840-token combined context window
(input + output). This module estimates message size and prunes or truncates
before requests are sent so long Cadence verification runs stay within limits.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Optional

logger = logging.getLogger(__name__)

# chars-per-token heuristic (conservative for code / JSON-heavy prompts)
CHARS_PER_TOKEN = 4

# Model limits: (context_window_tokens, max_output_tokens)
_MODEL_LIMITS: dict[str, tuple[int, int]] = {
    "deepseek.v3.2": (163_840, 8_192),
    "deepseek-v3.2": (163_840, 8_192),
    "deepseek.v3.1": (163_840, 8_192),
    "deepseek-r1": (128_000, 8_192),
}

_DEFAULT_CONTEXT_WINDOW = 128_000
_DEFAULT_MAX_OUTPUT = 8_192
_SAFETY_MARGIN_TOKENS = 2_048

# Per-message content caps applied before token budgeting
DEFAULT_MAX_CHARS_PER_MESSAGE = 12_000
CADENCE_MAX_CHARS_PER_MESSAGE = 4_000
DEFAULT_MAX_TOOL_RESULT_CHARS = 8_000

CADENCE_TOOL_NAMES = frozenset(
    {
        "checkCadenceStatus",
        "runCadenceSimulation",
        "getCadenceRunStatus",
        "applyCadenceFixes",
        "applyCodeToFile",
        "readFile",
        "listFiles",
        "attemptCompletion",
    }
)

_CONTEXT_OVERFLOW_RE = re.compile(
    r"maximum context length|context length is|input_tokens|too many tokens",
    re.IGNORECASE,
)


def estimate_text_tokens(text: str) -> int:
    value = str(text or "")
    if not value:
        return 0
    return max(1, (len(value) + CHARS_PER_TOKEN - 1) // CHARS_PER_TOKEN)


def estimate_messages_tokens(messages: list[dict[str, Any]]) -> int:
    total = 0
    for msg in messages or []:
        content = msg.get("content", "")
        if isinstance(content, list):
            for part in content:
                if isinstance(part, dict):
                    total += estimate_text_tokens(str(part.get("text") or part))
                else:
                    total += estimate_text_tokens(str(part))
        else:
            total += estimate_text_tokens(str(content or ""))
        tool_calls = msg.get("tool_calls")
        if tool_calls:
            total += estimate_text_tokens(json.dumps(tool_calls, ensure_ascii=False))
    # Rough overhead for roles / tool schemas in the wire format
    total += max(0, len(messages or [])) * 8
    return total


def get_model_limits(
    model: Optional[str] = None,
    provider: Optional[str] = None,
) -> tuple[int, int]:
    _ = provider
    name = str(model or "").strip().lower()
    for key, limits in _MODEL_LIMITS.items():
        if key in name or name.endswith(key.split(".")[-1]):
            return limits
    if "deepseek" in name:
        return (163_840, 8_192)
    return (_DEFAULT_CONTEXT_WINDOW, _DEFAULT_MAX_OUTPUT)


def get_model_max_output_tokens(
    model: Optional[str] = None,
    provider: Optional[str] = None,
    requested: Optional[int] = None,
) -> int:
    _, model_max = get_model_limits(model, provider)
    req = max(1, int(requested or model_max))
    return min(req, model_max)


def compute_input_token_budget(
    *,
    model: Optional[str] = None,
    provider: Optional[str] = None,
    requested_output_tokens: Optional[int] = None,
) -> int:
    context_window, _ = get_model_limits(model, provider)
    reserved_output = get_model_max_output_tokens(
        model, provider, requested_output_tokens
    )
    return max(8_192, context_window - reserved_output - _SAFETY_MARGIN_TOKENS)


def truncate_text(text: str, max_chars: int, *, label: str = "content") -> str:
    value = str(text or "")
    if len(value) <= max_chars:
        return value
    head = max(512, max_chars // 2)
    tail = max(256, max_chars - head - 80)
    return (
        value[:head]
        + f"\n\n… [{label} truncated: {len(value):,} → {max_chars:,} chars] …\n\n"
        + value[-tail:]
    )


def truncate_message_content(content: Any, max_chars: int) -> Any:
    if isinstance(content, str):
        return truncate_text(content, max_chars)
    if content is None:
        return content
    serialized = json.dumps(content, ensure_ascii=False) if not isinstance(content, str) else content
    if len(serialized) <= max_chars:
        return content
    return truncate_text(serialized, max_chars, label="message")


def trim_conversation_history(
    history: list[dict[str, Any]],
    *,
    max_messages: int = 40,
    max_chars_per_message: int = DEFAULT_MAX_CHARS_PER_MESSAGE,
) -> list[dict[str, Any]]:
    items = list(history or [])
    if not items:
        return []
    trimmed = items[-max_messages:] if len(items) > max_messages else items
    result: list[dict[str, Any]] = []
    for msg in trimmed:
        if not isinstance(msg, dict):
            continue
        role = msg.get("role", "user")
        content = msg.get("content", "")
        if isinstance(content, str) and len(content) > max_chars_per_message:
            content = truncate_text(content, max_chars_per_message, label="history")
        result.append({"role": role, "content": content})
    return result


def _compact_cadence_tool_payload(data: dict[str, Any]) -> dict[str, Any]:
    feedback = data.get("feedback_memory") or {}
    root_causes = feedback.get("root_causes") if isinstance(feedback, dict) else None
    compact: dict[str, Any] = {
        "status": data.get("status"),
        "run_id": data.get("run_id"),
        "cadence_connected": data.get("cadence_connected"),
        "phases": (data.get("phases") or [])[-6:],
        "next_step": data.get("next_step") or "",
    }
    if isinstance(root_causes, list) and root_causes:
        compact["feedback_memory"] = {"root_causes": root_causes[:8]}
    analysis = data.get("analysis")
    if isinstance(analysis, dict) and analysis:
        compact["analysis"] = {
            k: analysis[k]
            for k in ("verdict", "summary", "failure_phase", "error_count")
            if k in analysis
        }
    closure = data.get("closure_report")
    if isinstance(closure, dict) and closure:
        compact["closure_report"] = {
            k: closure[k]
            for k in ("overall_status", "summary", "passed", "failed")
            if k in closure
        }
    repair_history = data.get("repair_history")
    if isinstance(repair_history, list) and repair_history:
        compact["repair_history"] = repair_history[-3:]
    compact["_note"] = (
        "Compact Cadence result. Call getCadenceRunStatus(run_id) for full logs."
    )
    return compact


def truncate_tool_result_content(
    tool_name: str,
    content: str,
    *,
    max_chars: int = DEFAULT_MAX_TOOL_RESULT_CHARS,
) -> str:
    text = str(content or "")
    if len(text) <= max_chars:
        return text

    if tool_name in {"runCadenceSimulation", "getCadenceRunStatus"}:
        try:
            parsed = json.loads(text)
            if isinstance(parsed, dict):
                compact = _compact_cadence_tool_payload(parsed)
                compact_text = json.dumps(compact, indent=2, ensure_ascii=False)
                if len(compact_text) <= max_chars:
                    return compact_text
                return truncate_text(compact_text, max_chars, label=tool_name)
        except json.JSONDecodeError:
            pass

    if tool_name == "readFile":
        try:
            parsed = json.loads(text)
            if isinstance(parsed, dict) and "content" in parsed:
                body = str(parsed.get("content") or "")
                if len(body) > max_chars // 2:
                    parsed["content"] = truncate_text(
                        body, max_chars // 2, label="file"
                    )
                    parsed["_truncated"] = True
                    return json.dumps(parsed, indent=2, ensure_ascii=False)
        except json.JSONDecodeError:
            pass

    return truncate_text(text, max_chars, label=tool_name)


def _summarize_tool_message(msg: dict[str, Any]) -> dict[str, Any]:
    name = str(msg.get("name") or "tool")
    content = str(msg.get("content") or "")
    preview = content[:240].replace("\n", " ")
    return {
        "role": "tool",
        "name": name,
        "tool_call_id": msg.get("tool_call_id"),
        "content": json.dumps(
            {
                "_summarized": True,
                "tool": name,
                "preview": preview,
                "original_chars": len(content),
            },
            ensure_ascii=False,
        ),
    }


def prune_messages_for_context(
    messages: list[dict[str, Any]],
    *,
    model: Optional[str] = None,
    provider: Optional[str] = None,
    requested_output_tokens: Optional[int] = None,
    max_chars_per_message: int = DEFAULT_MAX_CHARS_PER_MESSAGE,
) -> list[dict[str, Any]]:
    """Return a copy of messages that fits within the model input token budget."""
    if not messages:
        return []

    budget = compute_input_token_budget(
        model=model,
        provider=provider,
        requested_output_tokens=requested_output_tokens,
    )

    working = [dict(m) for m in messages]

    # Pass 1 — cap individual message bodies
    for msg in working:
        content = msg.get("content")
        if content is not None:
            msg["content"] = truncate_message_content(content, max_chars_per_message)

    if estimate_messages_tokens(working) <= budget:
        return working

    if len(working) <= 2:
        # system + user only — shrink user message further
        if len(working) >= 2:
            user = working[-1]
            user["content"] = truncate_message_content(
                user.get("content", ""),
                max(4_000, max_chars_per_message // 2),
            )
        return working

    system_msgs = [working[0]] if working[0].get("role") == "system" else []
    start_idx = len(system_msgs)
    tail = working[start_idx:]

    # Pass 2 — summarize older tool results in the tail (keep last 6 messages intact)
    if len(tail) > 8:
        preserve_from = max(0, len(tail) - 6)
        for i in range(0, preserve_from):
            if tail[i].get("role") == "tool":
                tail[i] = _summarize_tool_message(tail[i])

    working = system_msgs + tail
    if estimate_messages_tokens(working) <= budget:
        logger.info(
            "Context prune: summarized older tool results (est=%d budget=%d)",
            estimate_messages_tokens(working),
            budget,
        )
        return working

    # Pass 3 — drop middle history, keep system + last N messages
    for keep_tail in range(min(12, len(tail)), 1, -1):
        candidate = system_msgs + tail[-keep_tail:]
        if estimate_messages_tokens(candidate) <= budget:
            dropped = len(tail) - keep_tail
            logger.warning(
                "Context prune: dropped %d middle messages (kept tail=%d est=%d budget=%d)",
                dropped,
                keep_tail,
                estimate_messages_tokens(candidate),
                budget,
            )
            return candidate

    # Pass 4 — aggressive truncation on surviving messages
    aggressive = system_msgs + tail[-4:]
    for msg in aggressive:
        msg["content"] = truncate_message_content(
            msg.get("content", ""),
            max(2_000, max_chars_per_message // 4),
        )
    logger.warning(
        "Context prune: aggressive truncation (est=%d budget=%d)",
        estimate_messages_tokens(aggressive),
        budget,
    )
    return aggressive


def is_context_length_error(exc: BaseException) -> bool:
    text = str(exc)
    return bool(_CONTEXT_OVERFLOW_RE.search(text))


def select_tool_definitions(
    all_tools: list[dict[str, Any]],
    *,
    cadence_mode: bool = False,
) -> list[dict[str, Any]]:
    if not cadence_mode:
        return all_tools
    selected = []
    for tool in all_tools:
        fn = (tool.get("function") or {}) if isinstance(tool, dict) else {}
        name = fn.get("name")
        if name in CADENCE_TOOL_NAMES:
            selected.append(tool)
    return selected or all_tools
