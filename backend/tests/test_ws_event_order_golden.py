"""Golden contract test for agent WebSocket event ordering.

The live Agent WS path (AgenticLoopEngine) emits transitional event names such as
``turn_start``, ``assistant_delta``, ``tool_call_started``, and ``done``. The
target agent-core schema uses ``agent_start``, ``message_*``, ``tool_execution_*``,
and ``agent_end``. This module normalizes live events to the canonical sequence
the frontend will depend on after Phases 3/4, and asserts ordering invariants.
"""

import importlib.util
import os
import sys
import uuid
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

os.environ["CHIPVERIFY_SECRET_KEY"] = "test-ws-golden-secret"
TEST_DB_PATH = Path(__file__).resolve().parent / "golden_ws_events.db"
os.environ["DATABASE_URL"] = f"sqlite:///{TEST_DB_PATH.as_posix()}"

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

_main_spec = importlib.util.spec_from_file_location(
    "chipverify_backend_main_golden_ws", BACKEND_ROOT / "main.py"
)
_main_module = importlib.util.module_from_spec(_main_spec)
assert _main_spec and _main_spec.loader
_main_spec.loader.exec_module(_main_module)
app = _main_module.app  # noqa: E402
from routes import api as api_routes  # noqa: E402
from database.database import Base, engine  # noqa: E402
from llm_provider import ChatResponse, ToolCall  # noqa: E402


def _reset_database():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)


@pytest.fixture(autouse=True)
def reset_database_between_tests():
    _reset_database()
    yield
    _reset_database()


@pytest.fixture(scope="session", autouse=True)
def cleanup_golden_ws_db():
    yield
    engine.dispose()
    if TEST_DB_PATH.exists():
        TEST_DB_PATH.unlink(missing_ok=True)


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client


def _auth_headers(client: TestClient):
    response = client.post("/api/v1/auth/auto-login")
    assert response.status_code == 200
    access_token = response.json()["access_token"]
    return {"Authorization": f"Bearer {access_token}"}


def _create_project(client: TestClient, headers: dict) -> str:
    response = client.post(
        "/api/v1/projects",
        headers=headers,
        json={"name": "WS Golden Contract", "description": "event order golden tests"},
    )
    assert response.status_code == 200
    return response.json()["id"]


def _base_context(project_id: str = "test-project"):
    return {"project_id": project_id}


def _collect_ws_events(ws, max_messages: int = 120) -> list[dict]:
    events: list[dict] = []
    for _ in range(max_messages):
        data = ws.receive_json()
        events.append(data)
        if data.get("type") == "done":
            break
    else:
        raise AssertionError("Did not receive done event")
    return events


def _normalize_to_canonical(events: list[dict]) -> list[str]:
    """Map live WS events to the target agent lifecycle schema."""
    canonical: list[str] = []
    saw_turn_start = False

    for event in events:
        event_type = str(event.get("type") or "")

        if event_type == "turn_start" and not saw_turn_start:
            canonical.append("agent_start")
            saw_turn_start = True

        if event_type in {
            "assistant_delta",
            "text_delta",
            "message_start",
            "message_update",
            "message_end",
        }:
            if event_type.startswith("message_"):
                canonical.append(event_type)
            else:
                canonical.append("message_update")
            continue

        if event_type == "tool_call_started":
            canonical.append("tool_execution_start")
            continue

        if event_type == "tool_call_completed":
            canonical.append("tool_execution_end")
            continue

        if event_type in {"done", "conversation_ended"}:
            canonical.append("agent_end")
            continue

        if event_type in {
            "agent_start",
            "turn_start",
            "turn_end",
            "agent_end",
            "tool_execution_start",
            "tool_execution_end",
        }:
            canonical.append(event_type)

    return canonical


def _assert_ordered_subsequence(event_types: list[str], required: list[str]) -> None:
    idx = 0
    for expected in required:
        while idx < len(event_types) and event_types[idx] != expected:
            idx += 1
        assert idx < len(event_types), (
            f"Missing ordered event {expected!r} in sequence {event_types!r}"
        )
        idx += 1


def _assert_tool_execution_pairing(event_types: list[str]) -> None:
    pending = 0
    for event_type in event_types:
        if event_type == "tool_execution_start":
            pending += 1
        elif event_type == "tool_execution_end":
            assert pending > 0, "tool_execution_end without matching start"
            pending -= 1
    assert pending == 0, "Unclosed tool_execution_start events remain"


@patch("services.agentic_loop.chat_with_tools")
def test_ws_event_order_golden_text_turn(mock_chat_with_tools, client: TestClient):
    mock_chat_with_tools.return_value = ChatResponse(
        content="Golden contract reply.",
        tool_calls=[],
    )

    thread_id = str(uuid.uuid4())
    with client.websocket_connect(f"/api/v1/ws/agent/{thread_id}/chat") as ws:
        ws.send_json(
            {
                "id": "golden_text_turn",
                "type": "agentic_chat",
                "prompt": "Say hello for the golden contract test.",
                "context": _base_context(),
            }
        )
        events = _collect_ws_events(ws)

    event_types = [evt.get("type") for evt in events]
    assert "turn_start" in event_types
    assert "turn_end" in event_types
    assert "done" in event_types

    canonical = _normalize_to_canonical(events)
    _assert_ordered_subsequence(
        canonical,
        ["agent_start", "turn_start", "turn_end", "agent_end"],
    )
    assert any(t.startswith("message_") for t in canonical)
    _assert_tool_execution_pairing(canonical)


@patch("services.agentic_loop.chat_with_tools")
def test_ws_event_order_golden_tool_turn(mock_chat_with_tools, client: TestClient):
    headers = _auth_headers(client)
    project_id = _create_project(client, headers)

    mock_chat_with_tools.side_effect = [
        ChatResponse(
            content="",
            tool_calls=[
                ToolCall(
                    id="golden_tool_call_1",
                    name="listFiles",
                    arguments={"project_id": project_id},
                )
            ],
        ),
        ChatResponse(content="Tool turn complete.", tool_calls=[]),
    ]

    thread_id = str(uuid.uuid4())
    with client.websocket_connect(f"/api/v1/ws/agent/{thread_id}/chat") as ws:
        ws.send_json(
            {
                "id": "golden_tool_turn",
                "type": "agentic_chat",
                "prompt": "Plan a quick verification pass.",
                "context": _base_context(project_id),
            }
        )
        events = _collect_ws_events(ws)

    raw_types = [evt.get("type") for evt in events]
    assert "tool_call_started" in raw_types
    assert "tool_call_completed" in raw_types

    canonical = _normalize_to_canonical(events)
    _assert_ordered_subsequence(
        canonical,
        [
            "agent_start",
            "turn_start",
            "tool_execution_start",
            "tool_execution_end",
            "turn_end",
            "agent_end",
        ],
    )
    _assert_tool_execution_pairing(canonical)
