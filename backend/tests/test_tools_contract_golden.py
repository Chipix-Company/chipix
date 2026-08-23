"""Golden contract tests for Tools REST API response shapes."""

import importlib.util
import os
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

os.environ["CHIPVERIFY_SECRET_KEY"] = "test-tools-golden-secret"
TEST_DB_PATH = Path(__file__).resolve().parent / "golden_tools_contract.db"
os.environ["DATABASE_URL"] = f"sqlite:///{TEST_DB_PATH.as_posix()}"
BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

_main_spec = importlib.util.spec_from_file_location(
    "chipverify_backend_main_golden_tools", BACKEND_ROOT / "main.py"
)
_main_module = importlib.util.module_from_spec(_main_spec)
assert _main_spec and _main_spec.loader
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


@pytest.fixture(scope="session", autouse=True)
def cleanup_golden_tools_db():
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


def _create_project(client: TestClient, headers: dict):
    response = client.post(
        "/api/v1/projects",
        headers=headers,
        json={"name": "Tools Golden Contract", "description": "golden contract tests"},
    )
    assert response.status_code == 200
    return response.json()


def test_tools_create_file_contract(client: TestClient):
    headers = _auth_headers(client)
    project = _create_project(client, headers)
    spec_content = "# Reset spec\nVerify async reset deassertion."

    response = client.post(
        "/api/v1/tools/createFile",
        headers=headers,
        json={
            "project_id": project["id"],
            "filename": "golden_spec.md",
            "artifact_type": "spec",
            "content": spec_content,
        },
    )

    assert response.status_code == 201
    payload = response.json()
    assert set(payload.keys()) >= {"success", "artifact"}
    assert payload["success"] is True
    artifact = payload["artifact"]
    assert set(artifact.keys()) >= {"id", "artifact_type", "revision"}
    assert artifact["artifact_type"] == "spec"
    assert isinstance(artifact["revision"], int)
    assert artifact["revision"] >= 1


def test_tools_list_files_contract(client: TestClient):
    headers = _auth_headers(client)
    project = _create_project(client, headers)

    for idx, filename in enumerate(("golden_a.md", "golden_b.md"), start=1):
        create_resp = client.post(
            "/api/v1/tools/createFile",
            headers=headers,
            json={
                "project_id": project["id"],
                "filename": filename,
                "artifact_type": "spec",
                "content": f"spec body {idx}",
            },
        )
        assert create_resp.status_code == 201

    response = client.post(
        "/api/v1/tools/listFiles",
        headers=headers,
        json={"project_id": project["id"]},
    )

    assert response.status_code == 200
    payload = response.json()
    assert set(payload.keys()) >= {"success", "files"}
    assert payload["success"] is True
    assert isinstance(payload["files"], list)
    assert len(payload["files"]) >= 2


def test_tools_read_file_contract(client: TestClient):
    headers = _auth_headers(client)
    project = _create_project(client, headers)
    expected_content = "golden read contract content"

    create_resp = client.post(
        "/api/v1/tools/createFile",
        headers=headers,
        json={
            "project_id": project["id"],
            "filename": "golden_read.md",
            "artifact_type": "spec",
            "content": expected_content,
        },
    )
    assert create_resp.status_code == 201
    artifact_id = create_resp.json()["artifact"]["id"]

    response = client.post(
        "/api/v1/tools/readFile",
        headers=headers,
        json={"artifact_id": artifact_id},
    )

    assert response.status_code == 200
    payload = response.json()
    assert set(payload.keys()) >= {"success", "content"}
    assert payload["success"] is True
    assert payload["content"] == expected_content
