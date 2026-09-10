"""
Scripted demo LLM — phase-gated ChatResponses (no remote model).

Phases: mental_model → implement → verify
A new user message that matches a phase trigger switches phase and resets the step cursor.
Within a phase, tool-loop continuations advance the turn list.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

DEMO_ROOT = Path(__file__).resolve().parent
DEFAULT_SCRIPT = DEMO_ROOT / "scripts" / "sha256_verify.json"

_lock = threading.Lock()
# session_key -> {phase, step, last_user_hash}
_sessions: dict[str, dict[str, Any]] = {}


def _load_script() -> dict[str, Any]:
    path = Path(os.getenv("CHIPVERIFY_DEMO_LLM_SCRIPT") or str(DEFAULT_SCRIPT))
    if not path.is_file():
        raise FileNotFoundError(f"Demo LLM script not found: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def _extract_project_id(messages: list[dict]) -> str:
    blob = json.dumps(messages, default=str)
    patterns = [
        r"The project_id is [`'\"]([0-9a-fA-F-]{36})[`'\"]",
        r"project_id[`'\"=\s:]+([0-9a-fA-F-]{36})",
        r"Always pass this exact value as `project_id`[^\n]*\n([0-9a-fA-F-]{36})",
        r"([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})",
    ]
    for pattern in patterns:
        m = re.search(pattern, blob)
        if m:
            return m.group(1)
    return "00000000-0000-0000-0000-000000000000"


def _message_text(msg: dict) -> str:
    content = msg.get("content") or ""
    if isinstance(content, list):
        return " ".join(str(part.get("text") or part) for part in content)
    return str(content)


def _last_user_text(messages: list[dict]) -> str:
    for msg in reversed(messages):
        if msg.get("role") == "user":
            return _message_text(msg)
    return ""


def _session_key(messages: list[dict]) -> str:
    project_id = _extract_project_id(messages)
    # Stable per project+thread-ish: hash of first user message only for keying,
    # but phase switches on each new user hash inside the session state.
    first_user = ""
    for msg in messages:
        if msg.get("role") == "user":
            first_user = _message_text(msg)[:160]
            break
    return f"{project_id}::{hash(first_user)}"


def _match_phase(script: dict[str, Any], user_text: str) -> Optional[str]:
    lowered = (user_text or "").lower()
    phases = script.get("phases") or {}
    # Prefer more specific phases when multiple triggers match.
    order = [
        "verify_rerun_pass",
        "analyze_fix",
        "verify_fail",
        "verify_plan",
        "verify",
        "implement",
        "mental_model",
    ]
    for name in order:
        phase = phases.get(name) or {}
        triggers = phase.get("triggers") or []
        if any(str(t).lower() in lowered for t in triggers):
            return name
    # Legacy flat turns fallback
    if script.get("turns"):
        return "_legacy"
    return None


def _resolve_includes(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: _resolve_includes(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_resolve_includes(v) for v in obj]
    if isinstance(obj, str) and obj.startswith("@include "):
        rel = obj[len("@include ") :].strip()
        path = DEMO_ROOT / rel
        return path.read_text(encoding="utf-8")
    return obj


def _substitute(obj: Any, project_id: str) -> Any:
    if isinstance(obj, dict):
        return {k: _substitute(v, project_id) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_substitute(v, project_id) for v in obj]
    if isinstance(obj, str):
        return obj.replace("{{project_id}}", project_id)
    return obj


def _think_delay() -> None:
    try:
        ms = float(os.getenv("CHIPVERIFY_DEMO_THINK_MS") or "180")
    except ValueError:
        ms = 180.0
    if ms > 0:
        time.sleep(ms / 1000.0)


def _to_chat_response(step: dict[str, Any]):
    from llm_provider import ChatResponse, ToolCall

    content = str(step.get("content") or "")
    reasoning = str(step.get("reasoning") or "")
    raw_calls = step.get("tool_calls") or []
    tool_calls = []
    for call in raw_calls:
        cid = str(call.get("id") or f"demo_{uuid.uuid4().hex[:10]}")
        name = str(call.get("name") or "")
        args = call.get("arguments") or {}
        if not isinstance(args, dict):
            args = {}
        tool_calls.append(ToolCall(id=cid, name=name, arguments=args))

    finish = str(step.get("finish_reason") or ("tool_calls" if tool_calls else "stop"))
    return ChatResponse(
        content=content,
        tool_calls=tool_calls,
        finish_reason=finish,
        reasoning=reasoning,
    )


def _match_generate_prompt(prompt: str, system_prompt: str, script: dict) -> Optional[str]:
    combined = f"{system_prompt}\n{prompt}".lower()
    for rule in script.get("generate_rules") or []:
        needles = rule.get("when_any") or []
        if any(str(n).lower() in combined for n in needles):
            body = rule.get("content")
            if isinstance(body, str) and body.startswith("@include "):
                path = DEMO_ROOT / body[len("@include ") :].strip()
                return path.read_text(encoding="utf-8")
            return str(body or "")
    fallback = script.get("generate_fallback") or {
        "content": '{"status":"ok","demo":true}'
    }
    body = fallback.get("content") if isinstance(fallback, dict) else fallback
    if isinstance(body, str) and body.startswith("@include "):
        path = DEMO_ROOT / body[len("@include ") :].strip()
        return path.read_text(encoding="utf-8")
    return str(body or '{"status":"ok","demo":true}')


def _turns_for_phase(script: dict[str, Any], phase: str) -> list[dict]:
    if phase == "_legacy":
        return list(script.get("turns") or [])
    phases = script.get("phases") or {}
    return list((phases.get(phase) or {}).get("turns") or [])


def chat_with_tools_demo(
    messages: list[dict],
    tools: list[dict] | None = None,
    **_kwargs: Any,
):
    """Return the next scripted ChatResponse for the agentic loop."""
    _ = tools
    script = _load_script()
    project_id = _extract_project_id(messages)
    key = _session_key(messages)
    user_text = _last_user_text(messages)
    user_hash = hashlib.sha1(user_text.encode("utf-8", errors="ignore")).hexdigest()

    with _lock:
        state = _sessions.get(key) or {
            "phase": None,
            "step": 0,
            "last_user_hash": None,
        }
        matched = _match_phase(script, user_text)
        if matched and user_hash != state.get("last_user_hash"):
            # New user prompt that maps to a phase → start that phase.
            state = {
                "phase": matched,
                "step": 0,
                "last_user_hash": user_hash,
            }
        elif state.get("phase") is None:
            state = {
                "phase": matched or "mental_model",
                "step": 0,
                "last_user_hash": user_hash,
            }

        phase = state["phase"]
        turns = _turns_for_phase(script, phase)
        step_idx = int(state.get("step") or 0)

        if step_idx >= len(turns):
            logger.warning("Demo phase %s exhausted for session %s", phase, key)
            step = script.get("fallback") or {
                "content": "Demo phase complete. Continue with the next on-camera step.",
                "tool_calls": [],
                "finish_reason": "stop",
            }
        else:
            step = turns[step_idx]
            state["step"] = step_idx + 1

        _sessions[key] = state

    step = _substitute(_resolve_includes(step), project_id)
    logger.info(
        "Demo LLM phase=%s step=%s tools=%s",
        phase,
        step_idx,
        [c.get("name") for c in (step.get("tool_calls") or [])],
    )
    _think_delay()
    return _to_chat_response(step)


def generate_demo(system_prompt: str, user_message: str, **_kwargs: Any) -> tuple[str, str]:
    script = _load_script()
    content = _match_generate_prompt(user_message, system_prompt, script) or ""
    _think_delay()
    return content, str((script.get("generate_reasoning") or "demo"))


def reset_demo_sessions() -> None:
    with _lock:
        _sessions.clear()
