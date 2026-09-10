#!/usr/bin/env python3
"""Exercise the scripted demo agent over WebSocket without a browser."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import requests
from websockets.sync.client import connect

ROOT = Path(__file__).resolve().parents[2]
STATE = ROOT / "scripts" / "demo" / ".demo_state.json"
BASE = "http://127.0.0.1:7348/api/v1"


def main() -> int:
    state = json.loads(STATE.read_text(encoding="utf-8"))
    project_id = state["project_id"]
    prompt = state["prompt"]

    login = requests.post(f"{BASE}/auth/auto-login", timeout=30)
    login.raise_for_status()
    token = login.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    created = requests.post(
        f"{BASE}/projects/{project_id}/chat/threads",
        headers=headers,
        json={"title": f"SHA-256 demo {int(__import__('time').time())}"},
        timeout=30,
    )
    created.raise_for_status()
    thread_id = created.json()["id"]

    ws_url = f"ws://127.0.0.1:7348/api/v1/ws/agent/{thread_id}/chat?token={token}"
    request_id = "demo-smoke-1"
    seen = []
    with connect(ws_url, open_timeout=30, close_timeout=30) as ws:
        ws.send(
            json.dumps(
                {
                    "id": request_id,
                    "type": "agentic_chat",
                    "prompt": prompt,
                    "context": {"project_id": project_id},
                }
            )
        )
        while True:
            raw = ws.recv(timeout=120)
            evt = json.loads(raw)
            et = evt.get("type")
            seen.append(et)
            print(et, json.dumps({k: evt.get(k) for k in ("tool", "content", "summary", "status", "message") if k in evt})[:200])
            if et in {"done", "error", "conversation_ended"}:
                break
            if len(seen) > 400:
                break

    print("EVENT_TYPES", seen)
    ok = "tool_call_started" in seen and (
        "done" in seen or "conversation_ended" in seen
    )
    # Prefer hard evidence of verification tools for the demo ship gate.
    tools = [e for e in seen if e == "tool_call_started"]
    print("tool_call_started_count", len(tools))
    return 0 if ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
