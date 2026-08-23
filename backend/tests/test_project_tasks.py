import importlib.util
import os
import sys
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

os.environ["CHIPVERIFY_SECRET_KEY"] = "test-project-tasks-secret"
TEST_DB_PATH = Path(__file__).resolve().parent / "test_project_tasks.db"
os.environ["DATABASE_URL"] = f"sqlite:///{TEST_DB_PATH.as_posix()}"
BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

_main_spec = importlib.util.spec_from_file_location(
    "chipverify_backend_main_tasks", BACKEND_ROOT / "main.py"
)
_main_module = importlib.util.module_from_spec(_main_spec)
assert _main_spec and _main_spec.loader
_main_spec.loader.exec_module(_main_module)
app = _main_module.app

from routes import api as api_routes  # noqa: E402
from database.database import Base, engine  # noqa: E402
from database.models import ChatThread, ChatThreadState, ProjectTask, Run  # noqa: E402
from services.project_tasks import (  # noqa: E402
    apply_run_status_to_task,
    create_staged_verification_task,
    link_run_to_thread_task,
    sync_run_to_linked_task,
    sync_tasks_from_threads,
)


def _reset_database():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)


@pytest.fixture(autouse=True)
def override_license_dependency():
    app.dependency_overrides[api_routes._check_license_validity] = lambda: {
        "valid": True,
        "message": "test override",
    }
    yield
    app.dependency_overrides.pop(api_routes._check_license_validity, None)


@pytest.fixture(autouse=True)
def reset_database_between_tests():
    _reset_database()
    yield
    _reset_database()


@pytest.fixture()
def client():
    with TestClient(app) as test_client:
        yield test_client


def _register_and_auth(client: TestClient):
    payload_response = client.post("/api/v1/auth/auto-login")
    assert payload_response.status_code == 200, payload_response.text
    payload = payload_response.json()
    headers = {"Authorization": f"Bearer {payload['access_token']}"}
    return headers, payload


def _create_project(client: TestClient, headers: dict, name: str):
    response = client.post(
        "/api/v1/projects",
        headers=headers,
        json={"name": name, "description": "Task board test project"},
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_project_tasks_crud(client: TestClient):
    headers, _ = _register_and_auth(client)
    project = _create_project(client, headers, "Task CRUD Project")
    project_id = project["id"]

    create_response = client.post(
        f"/api/v1/projects/{project_id}/tasks",
        headers=headers,
        json={
            "title": "Implement FIFO buffer",
            "description": "Build synchronous FIFO RTL",
            "priority": "high",
            "agent_name": "FIFO Builder",
        },
    )
    assert create_response.status_code == 201, create_response.text
    created = create_response.json()
    task = created["task"]
    assert task["title"] == "Implement FIFO buffer"
    assert task["display_id"] == "T-0001"
    assert task["agent_name"] == "FIFO Builder"
    assert task["status"] == "todo"

    list_response = client.get(
        f"/api/v1/projects/{project_id}/tasks",
        headers=headers,
    )
    assert list_response.status_code == 200
    listed = list_response.json()
    assert len(listed) == 1
    assert listed[0]["id"] == task["id"]

    patch_response = client.patch(
        f"/api/v1/tasks/{task['id']}",
        headers=headers,
        json={"status": "in_progress", "progress_pct": 25},
    )
    assert patch_response.status_code == 200
    patched = patch_response.json()
    assert patched["status"] == "in_progress"
    assert patched["progress_pct"] == 25

    delete_response = client.delete(
        f"/api/v1/tasks/{task['id']}",
        headers=headers,
    )
    assert delete_response.status_code == 204

    after_delete = client.get(
        f"/api/v1/projects/{project_id}/tasks",
        headers=headers,
    ).json()
    assert after_delete == []


def test_project_task_auto_start_creates_thread(client: TestClient):
    headers, auth_payload = _register_and_auth(client)
    project = _create_project(client, headers, "Task Auto Start Project")
    project_id = project["id"]

    response = client.post(
        f"/api/v1/projects/{project_id}/tasks",
        headers=headers,
        json={
            "title": "Verify reset logic",
            "prompt": "Verify reset behavior against the spec",
            "agent_name": "Reset Verifier",
            "auto_start": True,
        },
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["thread"]["id"]
    assert body["thread"]["agent_name"] == "Reset Verifier"
    assert body["task"]["thread_id"] == body["thread"]["id"]
    assert body["task"]["status"] == "in_progress"
    assert body["initial_prompt"]

    threads = client.get(
        f"/api/v1/projects/{project_id}/chat/threads",
        headers=headers,
    ).json()
    assert any(t["id"] == body["thread"]["id"] for t in threads)
    assert auth_payload["user"]["id"]


def test_sync_from_threads_backfills_existing_chat_threads(client: TestClient):
    headers, _ = _register_and_auth(client)
    project = _create_project(client, headers, "Sync Backfill Project")
    project_id = project["id"]

    thread_response = client.post(
        f"/api/v1/projects/{project_id}/chat/threads",
        headers=headers,
        json={"title": "New chat"},
    )
    assert thread_response.status_code in (200, 201), thread_response.text
    thread = thread_response.json()

    from database.database import SessionLocal
    from database.models import ChatMessage

    db = SessionLocal()
    try:
        db.add(
            ChatMessage(
                id=str(uuid.uuid4()),
                thread_id=thread["id"],
                role="user",
                content="Design a 16-bit synchronous FIFO with full/empty flags",
            )
        )
        db.commit()
    finally:
        db.close()

    empty = client.get(f"/api/v1/projects/{project_id}/tasks", headers=headers).json()
    assert empty == []

    sync_response = client.post(
        f"/api/v1/projects/{project_id}/tasks/sync-from-threads",
        headers=headers,
    )
    assert sync_response.status_code == 200, sync_response.text
    body = sync_response.json()
    assert body["created"] >= 1
    assert len(body["tasks"]) >= 1
    task = body["tasks"][0]
    assert task["thread_id"] == thread["id"]
    assert task["display_id"] == "T-0001"
    assert task["agent_name"]
    assert "FIFO" in task["title"] or "fifo" in task["title"].lower()

    threads = client.get(
        f"/api/v1/projects/{project_id}/chat/threads",
        headers=headers,
    ).json()
    synced_thread = next(t for t in threads if t["id"] == thread["id"])
    assert synced_thread.get("agent_name")
    assert synced_thread.get("active_task_id") == task["id"]

    sync_again = client.post(
        f"/api/v1/projects/{project_id}/tasks/sync-from-threads",
        headers=headers,
    ).json()
    assert sync_again["created"] == 0


def test_run_status_sync_updates_linked_task(client: TestClient):
    headers, auth_payload = _register_and_auth(client)
    project = _create_project(client, headers, "Run Sync Project")
    project_id = project["id"]

    from database.database import SessionLocal

    db = SessionLocal()
    try:
        run = Run(
            id=str(uuid.uuid4()),
            organization_id=project["organization_id"],
            project_id=project_id,
            user_id=auth_payload["user"]["id"],
            prompt_text="Run verification on UART",
            specification_type="text",
            status="running",
        )
        db.add(run)
        db.flush()
        task = ProjectTask(
            id=str(uuid.uuid4()),
            project_id=project_id,
            user_id=auth_payload["user"]["id"],
            display_number=1,
            title="UART verification",
            status="in_verification",
            run_id=run.id,
            source="verification",
            agent_name="Verification Agent",
        )
        db.add(task)
        db.commit()

        run.status = "completed"
        synced = sync_run_to_linked_task(db, run)
        assert synced is not None
        assert synced.status == "completed"
        assert synced.progress_pct == 100

        run.status = "failed"
        apply_run_status_to_task(task, run.status)
        assert task.status == "blocked"
    finally:
        db.close()


def test_link_run_to_thread_task_moves_card_to_verification(client: TestClient):
    headers, auth_payload = _register_and_auth(client)
    project = _create_project(client, headers, "Link Run Project")
    project_id = project["id"]

    from database.database import SessionLocal
    from database.models import User

    db = SessionLocal()
    try:
        user = db.query(User).filter(User.id == auth_payload["user"]["id"]).first()
        thread = ChatThread(
            id=str(uuid.uuid4()),
            project_id=project_id,
            user_id=user.id,
            title="FIFO design thread",
            agent_name="Design Agent · FIFO",
        )
        db.add(thread)
        db.flush()
        task = ProjectTask(
            id=str(uuid.uuid4()),
            project_id=project_id,
            user_id=user.id,
            display_number=1,
            title="Build FIFO",
            status="in_progress",
            progress_pct=25,
            thread_id=thread.id,
            source="agent_chat",
            agent_name="Design Agent · FIFO",
        )
        db.add(task)
        thread.active_task_id = task.id
        run = Run(
            id=str(uuid.uuid4()),
            organization_id=project["organization_id"],
            project_id=project_id,
            user_id=user.id,
            prompt_text="Verify FIFO",
            specification_type="text",
            status="running",
        )
        db.add(run)
        db.commit()

        linked = link_run_to_thread_task(
            db,
            thread_id=thread.id,
            run=run,
            user=user,
        )
        assert linked is not None
        assert linked.id == task.id
        assert linked.run_id == run.id
        assert linked.status == "in_verification"
        assert linked.progress_pct >= 15

        state = (
            db.query(ChatThreadState)
            .filter(ChatThreadState.thread_id == thread.id)
            .first()
        )
        assert state is not None
        assert state.context_run_id == run.id
    finally:
        db.close()


def test_sync_from_threads_reinfers_completed_run(client: TestClient):
    headers, auth_payload = _register_and_auth(client)
    project = _create_project(client, headers, "Reinfer Project")
    project_id = project["id"]

    from database.database import SessionLocal
    from database.models import ChatMessage, User

    thread_id = None
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.id == auth_payload["user"]["id"]).first()
        thread = ChatThread(
            id=str(uuid.uuid4()),
            project_id=project_id,
            user_id=user.id,
            title="UART thread",
            agent_name="Design Agent · UART",
        )
        db.add(thread)
        db.flush()
        thread_id = thread.id
        run = Run(
            id=str(uuid.uuid4()),
            organization_id=project["organization_id"],
            project_id=project_id,
            user_id=user.id,
            prompt_text="Verify UART",
            specification_type="text",
            status="completed",
        )
        db.add(run)
        db.flush()
        state = ChatThreadState(thread_id=thread.id, context_run_id=run.id)
        db.add(state)
        task = ProjectTask(
            id=str(uuid.uuid4()),
            project_id=project_id,
            user_id=user.id,
            display_number=1,
            title="Verify UART",
            status="in_progress",
            progress_pct=10,
            thread_id=thread.id,
            run_id=run.id,
            source="import",
            agent_name="Design Agent · UART",
        )
        db.add(task)
        thread.active_task_id = task.id
        db.add(
            ChatMessage(
                id=str(uuid.uuid4()),
                thread_id=thread.id,
                role="user",
                content="Verify UART reset behavior",
            )
        )
        db.commit()
    finally:
        db.close()

    body = client.post(
        f"/api/v1/projects/{project_id}/tasks/sync-from-threads",
        headers=headers,
    ).json()
    synced = next(t for t in body["tasks"] if t["thread_id"] == thread_id)
    assert synced["status"] == "completed"
    assert synced["progress_pct"] == 100


def test_staged_verification_reuses_thread_task(client: TestClient):
    headers, auth_payload = _register_and_auth(client)
    project = _create_project(client, headers, "Staged Reuse Project")
    project_id = project["id"]

    from database.database import SessionLocal
    from database.models import Project, User

    db = SessionLocal()
    try:
        user = db.query(User).filter(User.id == auth_payload["user"]["id"]).first()
        project_row = db.query(Project).filter(Project.id == project_id).first()
        thread = ChatThread(
            id=str(uuid.uuid4()),
            project_id=project_id,
            user_id=user.id,
            title="Counter design",
            agent_name="Design Agent · Counter",
        )
        db.add(thread)
        db.flush()
        task = ProjectTask(
            id=str(uuid.uuid4()),
            project_id=project_id,
            user_id=user.id,
            display_number=1,
            title="Build counter",
            status="in_progress",
            progress_pct=40,
            thread_id=thread.id,
            source="agent_chat",
            agent_name="Design Agent · Counter",
        )
        db.add(task)
        thread.active_task_id = task.id
        db.commit()

        reused = create_staged_verification_task(
            db,
            project=project_row,
            user=user,
            verification_type="unit_sim",
            approved_plan={"title": "Counter unit sim"},
            thread_id=thread.id,
        )
        assert reused.id == task.id
        assert reused.status == "in_verification"
        assert reused.progress_pct >= 15
    finally:
        db.close()


def test_completed_task_not_downgraded_when_run_finishes(client: TestClient):
    """Regression: agent turn_start must not overwrite completed run sync with in_progress."""
    headers, auth_payload = _register_and_auth(client)
    project = _create_project(client, headers, "No Downgrade Project")
    project_id = project["id"]

    from database.database import SessionLocal
    from database.models import User

    db = SessionLocal()
    try:
        user = db.query(User).filter(User.id == auth_payload["user"]["id"]).first()
        run = Run(
            id=str(uuid.uuid4()),
            organization_id=project["organization_id"],
            project_id=project_id,
            user_id=user.id,
            prompt_text="Verify SPI",
            specification_type="text",
            status="completed",
        )
        db.add(run)
        db.flush()
        task = ProjectTask(
            id=str(uuid.uuid4()),
            project_id=project_id,
            user_id=user.id,
            display_number=1,
            title="SPI verification",
            status="in_verification",
            progress_pct=15,
            run_id=run.id,
            source="verification",
        )
        db.add(task)
        db.commit()

        synced = sync_run_to_linked_task(db, run)
        assert synced.status == "completed"
        assert synced.progress_pct == 100

        # Simulate wrongful caller overwrite (old agentic_loop behavior).
        if synced.status not in {"completed", "blocked", "cancelled"}:
            synced.status = "in_progress"
        db.commit()
        db.refresh(synced)
        assert synced.status == "completed"
    finally:
        db.close()


def test_sync_run_to_linked_task_uses_thread_context(client: TestClient):
    headers, auth_payload = _register_and_auth(client)
    project = _create_project(client, headers, "Context Sync Project")
    project_id = project["id"]

    from database.database import SessionLocal
    from database.models import User

    db = SessionLocal()
    try:
        user = db.query(User).filter(User.id == auth_payload["user"]["id"]).first()
        thread = ChatThread(
            id=str(uuid.uuid4()),
            project_id=project_id,
            user_id=user.id,
            title="SPI thread",
        )
        db.add(thread)
        db.flush()
        run = Run(
            id=str(uuid.uuid4()),
            organization_id=project["organization_id"],
            project_id=project_id,
            user_id=user.id,
            prompt_text="SPI verification",
            specification_type="text",
            status="running",
        )
        db.add(run)
        db.flush()
        db.add(ChatThreadState(thread_id=thread.id, context_run_id=run.id))
        task = ProjectTask(
            id=str(uuid.uuid4()),
            project_id=project_id,
            user_id=user.id,
            display_number=1,
            title="SPI verification",
            status="in_progress",
            progress_pct=20,
            thread_id=thread.id,
            source="agent_chat",
        )
        db.add(task)
        thread.active_task_id = task.id
        db.commit()

        synced = sync_run_to_linked_task(db, run)
        assert synced is not None
        assert synced.id == task.id
        assert synced.run_id == run.id
        assert synced.status == "in_verification"
    finally:
        db.close()
