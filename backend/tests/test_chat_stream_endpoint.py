import importlib.util
import os
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

os.environ["CHIPVERIFY_SECRET_KEY"] = "test-chat-stream-secret"
TEST_DB_PATH = Path(__file__).resolve().parent / "test_chat_stream_endpoint.db"
os.environ["DATABASE_URL"] = f"sqlite:///{TEST_DB_PATH.as_posix()}"
BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

_main_spec = importlib.util.spec_from_file_location(
    "chipverify_backend_main_chat_stream", BACKEND_ROOT / "main.py"
)
assert _main_spec and _main_spec.loader
_main_module = importlib.util.module_from_spec(_main_spec)
_main_spec.loader.exec_module(_main_module)
app = _main_module.app

from database.database import Base, engine  # noqa: E402
from routes import api as api_routes  # noqa: E402


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
        "org_id": "ORG-CHAT-TEST",
        "max_seats": 100,
    }
    yield
    app.dependency_overrides.pop(api_routes._check_license_validity, None)


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client


def _auth_headers(client: TestClient):
    response = client.post("/api/v1/auth/auto-login")
    assert response.status_code == 200
    access_token = response.json()["access_token"]
    return {"Authorization": f"Bearer {access_token}"}


def _create_project(client: TestClient, headers: dict):
    response = client.post(
        "/api/v1/projects",
        headers=headers,
        json={"name": "Chat Stream Test", "description": "chat stream tests"},
    )
    assert response.status_code == 200
    return response.json()


def _create_thread(client: TestClient, headers: dict, project_id: str):
    response = client.post(
        f"/api/v1/projects/{project_id}/chat/threads",
        headers=headers,
        json={"title": "Test Thread"},
    )
    assert response.status_code in (200, 201)
    return response.json()


def test_chat_stream_requires_thread_id(client: TestClient):
    headers = _auth_headers(client)

    response = client.post(
        "/api/v1/chat/stream",
        headers=headers,
        json={"messages": [{"role": "user", "content": "hi"}], "context": {}},
    )

    assert response.status_code == 400
    assert "thread_id is required" in response.text.lower()


def test_chat_stream_rejects_unknown_thread(client: TestClient):
    headers = _auth_headers(client)

    response = client.post(
        "/api/v1/chat/stream",
        headers=headers,
        json={
            "thread_id": "00000000-0000-0000-0000-000000000000",
            "messages": [{"role": "user", "content": "hi"}],
            "context": {},
        },
    )

    assert response.status_code == 404
    assert "thread not found" in response.text.lower()


def test_chat_stream_happy_path_streams_events(client: TestClient, monkeypatch):
    headers = _auth_headers(client)
    project = _create_project(client, headers)
    thread = _create_thread(client, headers, project["id"])

    async def fake_stream(*args, **kwargs):
        class _Chunk:
            def __init__(self, content="", tool_calls=None, finish_reason="stop"):
                self.content = content
                self.tool_calls = tool_calls or []
                self.finish_reason = finish_reason

        yield _Chunk(content="Hello", finish_reason="stop")

    import routes.chat_stream as chat_stream_module

    monkeypatch.setattr(chat_stream_module, "chat_with_tools_stream", fake_stream)

    response = client.post(
        "/api/v1/chat/stream",
        headers=headers,
        json={
            "thread_id": thread["id"],
            "messages": [{"role": "user", "content": "hi there"}],
            "context": {"project_id": project["id"]},
        },
    )

    assert response.status_code == 200
    assert '"type": "text-start"' in response.text
    assert '"type": "text-delta"' in response.text
    assert '"type": "finish"' in response.text


def test_chat_stream_accepts_parts_only_user_message(client: TestClient, monkeypatch):
    headers = _auth_headers(client)
    project = _create_project(client, headers)
    thread = _create_thread(client, headers, project["id"])

    async def fake_stream(*args, **kwargs):
        class _Chunk:
            def __init__(self, content="", tool_calls=None, finish_reason="stop"):
                self.content = content
                self.tool_calls = tool_calls or []
                self.finish_reason = finish_reason

        yield _Chunk(content="Acknowledged", finish_reason="stop")

    import routes.chat_stream as chat_stream_module

    monkeypatch.setattr(chat_stream_module, "chat_with_tools_stream", fake_stream)

    response = client.post(
        "/api/v1/chat/stream",
        headers=headers,
        json={
            "thread_id": thread["id"],
            "messages": [
                {
                    "role": "user",
                    "parts": [{"type": "text", "text": "hi there"}],
                }
            ],
            "context": {"project_id": project["id"]},
        },
    )

    assert response.status_code == 200
    assert '"type": "text-delta"' in response.text

    stored_messages = client.get(
        f"/api/v1/chat/threads/{thread['id']}/messages?limit=10",
        headers=headers,
    )
    assert stored_messages.status_code == 200

    persisted = stored_messages.json().get("messages", [])
    assert any(
        msg.get("role") == "user" and "hi there" in (msg.get("content") or "")
        for msg in persisted
    )


def test_chat_stream_maps_tool_output_parts_to_tool_results(
    client: TestClient, monkeypatch
):
    headers = _auth_headers(client)
    project = _create_project(client, headers)
    thread = _create_thread(client, headers, project["id"])

    captured_messages = []

    async def fake_stream(*args, **kwargs):
        captured_messages.extend(kwargs.get("messages", []))

        class _Chunk:
            def __init__(self, content="", tool_calls=None, finish_reason="stop"):
                self.content = content
                self.tool_calls = tool_calls or []
                self.finish_reason = finish_reason

        yield _Chunk(content="Tool result received", finish_reason="stop")

    import routes.chat_stream as chat_stream_module

    monkeypatch.setattr(chat_stream_module, "chat_with_tools_stream", fake_stream)

    response = client.post(
        "/api/v1/chat/stream",
        headers=headers,
        json={
            "thread_id": thread["id"],
            "messages": [
                {"role": "user", "content": "list files"},
                {
                    "role": "assistant",
                    "parts": [
                        {
                            "type": "tool-listFiles",
                            "toolCallId": "tc_list_1",
                            "state": "output-available",
                            "input": {"project_id": project["id"]},
                            "output": {
                                "success": True,
                                "files": [{"id": "a1", "filename": "a.sv"}],
                            },
                        }
                    ],
                },
            ],
            "context": {"project_id": project["id"]},
        },
    )

    assert response.status_code == 200

    tool_messages = [m for m in captured_messages if m.get("role") == "tool"]
    assert any(m.get("tool_call_id") == "tc_list_1" for m in tool_messages)

    assistant_with_tool_calls = [
        m
        for m in captured_messages
        if m.get("role") == "assistant" and m.get("tool_calls")
    ]
    assert not assistant_with_tool_calls


def test_chat_stream_does_not_recycle_resolved_tool_calls(
    client: TestClient, monkeypatch
):
    headers = _auth_headers(client)
    project = _create_project(client, headers)
    thread = _create_thread(client, headers, project["id"])

    captured_messages = []

    async def fake_stream(*args, **kwargs):
        captured_messages.extend(kwargs.get("messages", []))

        class _Chunk:
            def __init__(self, content="", tool_calls=None, finish_reason="stop"):
                self.content = content
                self.tool_calls = tool_calls or []
                self.finish_reason = finish_reason

        yield _Chunk(content="Done", finish_reason="stop")

    import routes.chat_stream as chat_stream_module

    monkeypatch.setattr(chat_stream_module, "chat_with_tools_stream", fake_stream)

    response = client.post(
        "/api/v1/chat/stream",
        headers=headers,
        json={
            "thread_id": thread["id"],
            "messages": [
                {"role": "user", "content": "list files"},
                {
                    "role": "assistant",
                    "parts": [
                        {
                            "type": "tool-listFiles",
                            "toolCallId": "tc_list_2",
                            "state": "input-available",
                            "input": {"project_id": project["id"]},
                        },
                        {
                            "type": "tool-listFiles",
                            "toolCallId": "tc_list_2",
                            "state": "output-available",
                            "input": {"project_id": project["id"]},
                            "output": {
                                "success": True,
                                "files": [{"id": "f1", "filename": "tb.sv"}],
                            },
                        },
                    ],
                },
            ],
            "context": {"project_id": project["id"]},
        },
    )

    assert response.status_code == 200

    tool_messages = [m for m in captured_messages if m.get("role") == "tool"]
    assert any(m.get("tool_call_id") == "tc_list_2" for m in tool_messages)

    recycled_calls = []
    for message in captured_messages:
        if message.get("role") != "assistant":
            continue
        for tool_call in message.get("tool_calls") or []:
            if tool_call.get("id") == "tc_list_2":
                recycled_calls.append(tool_call)
    assert not recycled_calls
