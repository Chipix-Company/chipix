import os
import sys
import time
import uuid
import importlib.util
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

os.environ["CHIPVERIFY_SECRET_KEY"] = "test-agent-ws-secret-key"
os.environ["CHIPVERIFY_ALLOW_DEV_AUTH"] = "true"
TEST_DB_PATH = Path(__file__).resolve().parent / "test_agent_ws_protocol.db"
os.environ["DATABASE_URL"] = f"sqlite:///{TEST_DB_PATH.as_posix()}"

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

_main_spec = importlib.util.spec_from_file_location(
    "chipverify_backend_main", BACKEND_ROOT / "main.py"
)
_main_module = importlib.util.module_from_spec(_main_spec)
assert _main_spec and _main_spec.loader
_main_spec.loader.exec_module(_main_module)
app = _main_module.app  # noqa: E402
from routes import api as api_routes  # noqa: E402
from database.database import Base, engine  # noqa: E402


def _reset_database():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)


@pytest.fixture(autouse=True)
def reset_database_between_tests():
    _reset_database()
    yield
    _reset_database()


@pytest.fixture(autouse=True)
def override_license_dependency():
    app.dependency_overrides[api_routes._check_license_validity] = lambda: {
        "org_id": "ORG-TEST",
        "max_seats": 100,
    }
    yield
    app.dependency_overrides.pop(api_routes._check_license_validity, None)


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client


def _recv_until_type(ws, target_type: str, max_messages: int = 40):
    for _ in range(max_messages):
        data = ws.receive_json()
        if data.get("type") == target_type:
            return data
    raise AssertionError(f"Did not receive event type={target_type}")


def _drain_until_done(ws, max_messages: int = 120):
    received = []
    for _ in range(max_messages):
        data = ws.receive_json()
        received.append(data)
        if data.get("type") in {"done", "complete"}:
            return received
    raise AssertionError("Did not receive done/complete event")


def _base_context():
    return {"project_id": "test-project"}


@patch(
    "routes.agent_ws._generate_agent_response_with_history",
    new_callable=AsyncMock,
    return_value="Hello from test",
)
def test_ws_accepts_legacy_message_type_and_streams(mock_llm, client: TestClient):
    thread_id = str(uuid.uuid4())
    request_id = "req_legacy_001"

    with client.websocket_connect(f"/api/v1/ws/agent/{thread_id}/chat") as ws:
        ws.send_json(
            {
                "id": request_id,
                "type": "message",
                "message": "hi",
                "clientVersion": "1.0.0",
                "context": _base_context(),
            }
        )

        connected = _recv_until_type(ws, "connected")
        assert connected["request_id"] == request_id

        stream = _drain_until_done(ws)
        assert any(
            evt.get("type") in {"assistant_delta", "text_delta"} for evt in stream
        )
        done_event = stream[-1]
        assert done_event.get("type") in {"done", "complete"}
        assert done_event.get("request_id") == request_id
        assert mock_llm.await_count >= 1


@patch(
    "routes.agent_ws._generate_agent_response_with_history",
    new_callable=AsyncMock,
    return_value="Reply for duplicate test",
)
def test_ws_duplicate_followup_request_returns_duplicate_ack(
    mock_llm, client: TestClient
):
    thread_id = str(uuid.uuid4())

    with client.websocket_connect(f"/api/v1/ws/agent/{thread_id}/chat") as ws:
        ws.send_json(
            {
                "id": "init_1",
                "type": "chat_message",
                "prompt": "start",
                "context": _base_context(),
            }
        )
        _recv_until_type(ws, "connected")
        _drain_until_done(ws)

        ws.send_json(
            {
                "id": "dup_1",
                "type": "chat_message",
                "prompt": "first",
                "context": _base_context(),
            }
        )
        _drain_until_done(ws)

        ws.send_json(
            {
                "id": "dup_1",
                "type": "chat_message",
                "prompt": "first",
                "context": _base_context(),
            }
        )
        ack = _recv_until_type(ws, "ack")
        assert ack.get("request_id") == "dup_1"
        assert ack.get("status") == "duplicate_request_ignored"


@patch(
    "routes.agent_ws._generate_agent_response_with_history",
    new_callable=AsyncMock,
    return_value="Continuation after tool result",
)
def test_ws_duplicate_tool_result_returns_duplicate_status(
    mock_llm, client: TestClient
):
    thread_id = str(uuid.uuid4())

    with client.websocket_connect(f"/api/v1/ws/agent/{thread_id}/chat") as ws:
        ws.send_json(
            {
                "id": "init_tool",
                "type": "chat_message",
                "prompt": "init",
                "context": _base_context(),
            }
        )
        _recv_until_type(ws, "connected")
        _drain_until_done(ws)

        ws.send_json(
            {
                "id": "tool_req_1",
                "type": "tool_result",
                "call_id": "tc_repeat_1",
                "result": {"ok": True},
            }
        )
        first_ack = _recv_until_type(ws, "tool_result_ack")
        assert first_ack.get("status") == "received"
        _drain_until_done(ws)

        ws.send_json(
            {
                "id": "tool_req_2",
                "type": "tool_result",
                "call_id": "tc_repeat_1",
                "result": {"ok": True},
            }
        )
        second_ack = _recv_until_type(ws, "tool_result_ack")
        assert second_ack.get("status") == "duplicate"
        assert second_ack.get("call_id") == "tc_repeat_1"


@patch(
    "routes.agent_ws._generate_agent_response_with_history",
    new_callable=AsyncMock,
    return_value="Response before cancel",
)
def test_ws_cancel_returns_complete_cancelled(mock_llm, client: TestClient):
    thread_id = str(uuid.uuid4())

    with client.websocket_connect(f"/api/v1/ws/agent/{thread_id}/chat") as ws:
        ws.send_json(
            {
                "id": "init_cancel",
                "type": "chat_message",
                "prompt": "hello",
                "context": _base_context(),
            }
        )
        _recv_until_type(ws, "connected")
        _drain_until_done(ws)

        ws.send_json({"id": "cancel_1", "type": "cancel"})
        cancelled = _recv_until_type(ws, "complete")
        assert cancelled.get("request_id") == "cancel_1"
        assert cancelled.get("status") == "cancelled"


@patch(
    "routes.agent_ws._generate_agent_response_with_history",
    new_callable=AsyncMock,
    return_value="Response for validation test",
)
def test_ws_rejects_invalid_initial_message_type(mock_llm, client: TestClient):
    thread_id = str(uuid.uuid4())

    with client.websocket_connect(f"/api/v1/ws/agent/{thread_id}/chat") as ws:
        ws.send_json(
            {
                "id": "bad_1",
                "type": "foobar",
                "message": "hello",
                "context": _base_context(),
            }
        )
        received_error = False
        for _ in range(4):
            try:
                evt = ws.receive_json()
                if evt.get("type") == "error":
                    received_error = True
                    assert "Initial message type" in evt.get("message", "")
                    break
            except Exception:
                break
        assert received_error or mock_llm.await_count == 0
        assert mock_llm.await_count == 0


@patch(
    "routes.agent_ws._generate_agent_response_with_history",
    new_callable=AsyncMock,
    return_value="Response without explicit type",
)
def test_ws_accepts_initial_message_without_type(mock_llm, client: TestClient):
    thread_id = str(uuid.uuid4())

    with client.websocket_connect(f"/api/v1/ws/agent/{thread_id}/chat") as ws:
        ws.send_json(
            {
                "id": "no_type_1",
                "message": "hello without type",
                "context": _base_context(),
            }
        )

        connected = _recv_until_type(ws, "connected")
        assert connected.get("request_id") == "no_type_1"
        _drain_until_done(ws)
        assert mock_llm.await_count >= 1


@patch(
    "routes.agent_ws._execute_tool_call",
    new_callable=AsyncMock,
    return_value={"success": True, "content": "ok"},
)
@patch(
    "routes.agent_ws._generate_agent_response_with_history",
    new_callable=AsyncMock,
    return_value="Continuation after execute_tool",
)
def test_ws_duplicate_execute_tool_returns_duplicate_result(
    mock_llm,
    mock_execute_tool,
    client: TestClient,
):
    thread_id = str(uuid.uuid4())

    with client.websocket_connect(f"/api/v1/ws/agent/{thread_id}/chat") as ws:
        ws.send_json(
            {
                "id": "init_exec",
                "type": "chat_message",
                "prompt": "init",
                "context": _base_context(),
            }
        )
        _recv_until_type(ws, "connected")
        _drain_until_done(ws)

        ws.send_json(
            {
                "id": "exec_req_1",
                "type": "execute_tool",
                "call_id": "exec_call_1",
                "tool": "readFile",
                "args": {"artifact_id": "any"},
            }
        )
        first_result = _recv_until_type(ws, "tool_result")
        assert first_result.get("call_id") == "exec_call_1"
        _drain_until_done(ws)

        ws.send_json(
            {
                "id": "exec_req_2",
                "type": "execute_tool",
                "call_id": "exec_call_1",
                "tool": "readFile",
                "args": {"artifact_id": "any"},
            }
        )
        second_result = _recv_until_type(ws, "tool_result")
        assert second_result.get("call_id") == "exec_call_1"
        payload = second_result.get("result") or {}
        assert payload.get("duplicate") is True
        assert mock_execute_tool.await_count == 1


@patch(
    "routes.agent_ws._generate_agent_response_with_history",
    new_callable=AsyncMock,
)
def test_ws_tool_result_continuation_error_has_code(mock_llm, client: TestClient):
    thread_id = str(uuid.uuid4())
    mock_llm.side_effect = [
        "Initial response before tool",
        RuntimeError("forced continuation failure"),
    ]

    with client.websocket_connect(f"/api/v1/ws/agent/{thread_id}/chat") as ws:
        ws.send_json(
            {
                "id": "init_fail_case",
                "type": "chat_message",
                "prompt": "init",
                "context": _base_context(),
            }
        )
        _recv_until_type(ws, "connected")
        _drain_until_done(ws)

        ws.send_json(
            {
                "id": "tool_fail_1",
                "type": "tool_result",
                "call_id": "tc_fail_1",
                "result": {"ok": True},
            }
        )
        ack = _recv_until_type(ws, "tool_result_ack")
        assert ack.get("status") == "received"

        error_event = _recv_until_type(ws, "error")
        assert error_event.get("request_id") == "tool_fail_1"
        assert error_event.get("code") == "LLM_CONTINUATION_FAILED"


@patch("routes.agent_ws.WS_MESSAGE_TIMEOUT_SECONDS", 1)
@patch(
    "routes.agent_ws._generate_agent_response_with_history",
    new_callable=AsyncMock,
    return_value="timeout baseline response",
)
def test_ws_idle_timeout_emits_message_timeout(mock_llm, client: TestClient):
    thread_id = str(uuid.uuid4())

    with client.websocket_connect(f"/api/v1/ws/agent/{thread_id}/chat") as ws:
        ws.send_json(
            {
                "id": "timeout_init",
                "type": "chat_message",
                "prompt": "start",
                "context": _base_context(),
            }
        )
        _recv_until_type(ws, "connected")
        _drain_until_done(ws)

        timeout_error = _recv_until_type(ws, "error", max_messages=8)
        assert timeout_error.get("code") == "MESSAGE_TIMEOUT"


@patch(
    "routes.agent_ws._generate_agent_response_with_history",
    new_callable=AsyncMock,
)
def test_ws_cancel_cancels_in_flight_generation(mock_llm, client: TestClient):
    thread_id = str(uuid.uuid4())

    async def _slow_generation(*args, **kwargs):
        await __import__("asyncio").sleep(5)
        return "should not be delivered"

    mock_llm.side_effect = _slow_generation

    with client.websocket_connect(f"/api/v1/ws/agent/{thread_id}/chat") as ws:
        ws.send_json(
            {
                "id": "cancel_init_req",
                "type": "chat_message",
                "prompt": "start long op",
                "context": _base_context(),
            }
        )
        _recv_until_type(ws, "connected")

        ws.send_json({"id": "cancel_now", "type": "cancel"})
        cancelled = _recv_until_type(ws, "complete")
        assert cancelled.get("request_id") == "cancel_init_req"
        assert cancelled.get("status") == "cancelled"


@patch("routes.agent_ws.RATE_LIMIT_MESSAGES_PER_WINDOW", 1)
@patch("routes.agent_ws.RATE_LIMIT_WINDOW_SECONDS", 60)
@patch(
    "routes.agent_ws._generate_agent_response_with_history",
    new_callable=AsyncMock,
    return_value="rate-limit baseline response",
)
def test_ws_rate_limit_blocks_excess_messages(mock_llm, client: TestClient):
    thread_id = str(uuid.uuid4())

    with client.websocket_connect(f"/api/v1/ws/agent/{thread_id}/chat") as ws:
        ws.send_json(
            {
                "id": "rl_init",
                "type": "chat_message",
                "prompt": "init",
                "context": _base_context(),
            }
        )
        _recv_until_type(ws, "connected")
        _drain_until_done(ws)

        ws.send_json(
            {
                "id": "rl_msg_1",
                "type": "chat_message",
                "prompt": "first allowed",
                "context": _base_context(),
            }
        )
        _drain_until_done(ws)

        ws.send_json(
            {
                "id": "rl_msg_2",
                "type": "chat_message",
                "prompt": "should be rate limited",
                "context": _base_context(),
            }
        )
        rate_error = _recv_until_type(ws, "error")
        assert rate_error.get("code") == "RATE_LIMITED"


@patch("routes.agent_ws.REQUEST_TRACK_TTL_SECONDS", 1)
@patch(
    "routes.agent_ws._generate_agent_response_with_history",
    new_callable=AsyncMock,
    return_value="ttl baseline response",
)
def test_ws_stale_request_id_cleanup_allows_reuse(mock_llm, client: TestClient):
    thread_id = str(uuid.uuid4())

    with client.websocket_connect(f"/api/v1/ws/agent/{thread_id}/chat") as ws:
        ws.send_json(
            {
                "id": "ttl_init",
                "type": "chat_message",
                "prompt": "init",
                "context": _base_context(),
            }
        )
        _recv_until_type(ws, "connected")
        _drain_until_done(ws)

        ws.send_json(
            {
                "id": "ttl_req_1",
                "type": "chat_message",
                "prompt": "first",
                "context": _base_context(),
            }
        )
        _drain_until_done(ws)

        ws.send_json(
            {
                "id": "ttl_req_1",
                "type": "chat_message",
                "prompt": "duplicate immediate",
                "context": _base_context(),
            }
        )
        ack = _recv_until_type(ws, "ack")
        assert ack.get("status") == "duplicate_request_ignored"

        time.sleep(1.2)
        ws.send_json(
            {
                "id": "ttl_req_1",
                "type": "chat_message",
                "prompt": "after ttl",
                "context": _base_context(),
            }
        )
        # Should be processed again instead of duplicate ack after stale cleanup.
        stream = _drain_until_done(ws)
        assert any(
            evt.get("type") in {"assistant_delta", "text_delta"} for evt in stream
        )


@patch(
    "routes.agent_ws._generate_agent_response_with_history",
    new_callable=AsyncMock,
    return_value="baseline for invalid execute_tool",
)
def test_ws_rejects_unknown_execute_tool(mock_llm, client: TestClient):
    thread_id = str(uuid.uuid4())

    with client.websocket_connect(f"/api/v1/ws/agent/{thread_id}/chat") as ws:
        ws.send_json(
            {
                "id": "init_unknown_tool",
                "type": "chat_message",
                "prompt": "init",
                "context": _base_context(),
            }
        )
        _recv_until_type(ws, "connected")
        _drain_until_done(ws)

        ws.send_json(
            {
                "id": "bad_tool_req",
                "type": "execute_tool",
                "call_id": "bad_tool_call",
                "tool": "dropDatabase",
                "args": {},
            }
        )

        error_event = _recv_until_type(ws, "error")
        assert error_event.get("request_id") == "bad_tool_req"
        assert error_event.get("code") == "UNKNOWN_TOOL"


@patch(
    "routes.agent_ws._generate_agent_response_with_history",
    new_callable=AsyncMock,
    return_value="baseline for invalid args",
)
def test_ws_rejects_execute_tool_with_non_object_args(mock_llm, client: TestClient):
    thread_id = str(uuid.uuid4())

    with client.websocket_connect(f"/api/v1/ws/agent/{thread_id}/chat") as ws:
        ws.send_json(
            {
                "id": "init_bad_args",
                "type": "chat_message",
                "prompt": "init",
                "context": _base_context(),
            }
        )
        _recv_until_type(ws, "connected")
        _drain_until_done(ws)

        ws.send_json(
            {
                "id": "bad_args_req",
                "type": "execute_tool",
                "call_id": "bad_args_call",
                "tool": "createFile",
                "args": "not_an_object",
            }
        )

        error_event = _recv_until_type(ws, "error")
        assert error_event.get("request_id") == "bad_args_req"
        assert error_event.get("code") == "INVALID_TOOL_ARGS"


@patch(
    "routes.agent_ws._generate_agent_response_with_history",
    new_callable=AsyncMock,
    return_value="baseline for missing tool result",
)
def test_ws_rejects_tool_result_without_result_payload(mock_llm, client: TestClient):
    thread_id = str(uuid.uuid4())

    with client.websocket_connect(f"/api/v1/ws/agent/{thread_id}/chat") as ws:
        ws.send_json(
            {
                "id": "init_missing_result",
                "type": "chat_message",
                "prompt": "init",
                "context": _base_context(),
            }
        )
        _recv_until_type(ws, "connected")
        _drain_until_done(ws)

        ws.send_json(
            {
                "id": "missing_result_req",
                "type": "tool_result",
                "call_id": "tc_missing_result",
            }
        )

        error_event = _recv_until_type(ws, "error")
        assert error_event.get("request_id") == "missing_result_req"
        assert error_event.get("code") == "INVALID_TOOL_RESULT"


@patch(
    "routes.agent_ws._execute_tool_call",
    new_callable=AsyncMock,
    return_value={"success": True, "content": "ok from tool"},
)
@patch(
    "routes.agent_ws._generate_agent_response_with_history",
    new_callable=AsyncMock,
)
def test_ws_end_to_end_tool_call_execution_roundtrip(
    mock_llm,
    mock_execute_tool,
    client: TestClient,
):
    thread_id = str(uuid.uuid4())
    mock_llm.side_effect = [
        '```json\n{"tool":"readFile","args":{"artifact_id":"abc"}}\n```',
        "Tool execution complete.",
    ]

    with client.websocket_connect(f"/api/v1/ws/agent/{thread_id}/chat") as ws:
        ws.send_json(
            {
                "id": "roundtrip_init",
                "type": "chat_message",
                "prompt": "read the current file",
                "context": _base_context(),
            }
        )
        _recv_until_type(ws, "connected")

        tool_call_evt = _recv_until_type(ws, "tool_call")
        assert tool_call_evt.get("tool") == "readFile"
        call_id = tool_call_evt.get("call_id")
        assert call_id

        ws.send_json(
            {
                "id": "roundtrip_exec",
                "type": "execute_tool",
                "call_id": call_id,
                "tool": "readFile",
                "args": {"artifact_id": "abc"},
            }
        )

        tool_result_evt = _recv_until_type(ws, "tool_result")
        assert tool_result_evt.get("call_id") == call_id
        result_payload = tool_result_evt.get("result") or {}
        assert result_payload.get("success") is True

        stream = _drain_until_done(ws)
        assert any(
            evt.get("type") in {"assistant_delta", "text_delta"} for evt in stream
        )
        assert mock_execute_tool.await_count == 1
