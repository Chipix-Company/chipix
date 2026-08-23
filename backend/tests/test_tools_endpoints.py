import importlib.util
import os
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

os.environ["CHIPVERIFY_SECRET_KEY"] = "test-tools-secret"
TEST_DB_PATH = Path(__file__).resolve().parent / "test_tools_endpoints.db"
os.environ["DATABASE_URL"] = f"sqlite:///{TEST_DB_PATH.as_posix()}"
BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

_main_spec = importlib.util.spec_from_file_location(
    "chipverify_backend_main_tools", BACKEND_ROOT / "main.py"
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


@pytest.fixture(autouse=True)
def override_license_dependency():
    app.dependency_overrides[api_routes._check_license_validity] = lambda: {
        "org_id": "ORG-TOOLS-TEST",
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
        json={"name": "Tools Endpoint Test", "description": "tool endpoint tests"},
    )
    assert response.status_code == 200
    return response.json()


def test_tools_create_list_read_and_apply_flow(client: TestClient):
    headers = _auth_headers(client)
    project = _create_project(client, headers)

    create_spec = client.post(
        "/api/v1/tools/createFile",
        headers=headers,
        json={
            "project_id": project["id"],
            "filename": "spec.md",
            "artifact_type": "spec",
            "content": "spec: verify reset behavior",
        },
    )
    assert create_spec.status_code == 201
    assert create_spec.json()["success"] is True

    create_rtl = client.post(
        "/api/v1/tools/createFile",
        headers=headers,
        json={
            "project_id": project["id"],
            "filename": "dut.sv",
            "artifact_type": "rtl",
            "content": "module dut;\nendmodule\n",
        },
    )
    assert create_rtl.status_code == 201
    rtl_artifact_id = create_rtl.json()["artifact"]["id"]

    list_files = client.post(
        "/api/v1/tools/listFiles",
        headers=headers,
        json={"project_id": project["id"]},
    )
    assert list_files.status_code == 200
    files = list_files.json().get("files", [])
    assert any(f.get("filename") == "spec.md" for f in files)
    assert any(f.get("filename") == "dut.sv" for f in files)

    read_rtl = client.post(
        "/api/v1/tools/readFile",
        headers=headers,
        json={"artifact_id": rtl_artifact_id},
    )
    assert read_rtl.status_code == 200
    assert "module dut;" in read_rtl.json().get("content", "")

    apply_code = client.post(
        "/api/v1/tools/applyCodeToFile",
        headers=headers,
        json={
            "artifact_id": rtl_artifact_id,
            "code": "assign ready = 1'b1;",
            "strategy": "smart_insert",
        },
    )
    assert apply_code.status_code == 200
    assert apply_code.json()["success"] is True

    reread_rtl = client.post(
        "/api/v1/tools/readFile",
        headers=headers,
        json={"artifact_id": rtl_artifact_id},
    )
    assert reread_rtl.status_code == 200
    updated_content = reread_rtl.json().get("content", "")
    assert "assign ready = 1'b1;" in updated_content


def test_tools_read_file_requires_existing_artifact(client: TestClient):
    headers = _auth_headers(client)

    response = client.post(
        "/api/v1/tools/readFile",
        headers=headers,
        json={"artifact_id": "00000000-0000-0000-0000-000000000000"},
    )

    assert response.status_code == 404
    assert "artifact not found" in response.text.lower()
