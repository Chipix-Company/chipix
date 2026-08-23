import os
import sys
import uuid
import importlib.util
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

os.environ["CHIPVERIFY_SECRET_KEY"] = "test-tools-secret-key"
TEST_DB_PATH = Path(__file__).resolve().parent / "test_tools_api_contracts.db"
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
from database.database import Base, SessionLocal, engine  # noqa: E402
from database.models import Run, RunEvent  # noqa: E402


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


def _register_and_auth(client: TestClient):
    response = client.post("/api/v1/auth/auto-login")
    assert response.status_code == 200, response.text
    payload = response.json()
    headers = {"Authorization": f"Bearer {payload['access_token']}"}
    return headers


def _create_project(client: TestClient, headers: dict, name: str):
    response = client.post(
        "/api/v1/projects",
        headers=headers,
        json={"name": name, "description": "Tools contract project"},
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_tools_create_file_contract_success(client: TestClient):
    headers = _register_and_auth(client)
    project = _create_project(client, headers, "Tools CreateFile Project")

    response = client.post(
        "/api/v1/tools/createFile",
        json={
            "project_id": project["id"],
            "filename": "alu.sv",
            "artifact_type": "rtl",
            "content": "module alu; endmodule\n",
        },
    )

    assert response.status_code == 201, response.text
    payload = response.json()
    assert payload["success"] is True
    assert payload["artifact"]["project_id"] == project["id"]
    assert payload["artifact"]["filename"] == "alu.sv"
    assert payload["artifact"]["artifact_type"] == "rtl"


def test_tools_create_file_unknown_project_returns_404(client: TestClient):
    _register_and_auth(client)

    response = client.post(
        "/api/v1/tools/createFile",
        json={
            "project_id": str(uuid.uuid4()),
            "filename": "missing.sv",
            "artifact_type": "rtl",
            "content": "module missing; endmodule\n",
        },
    )

    assert response.status_code == 404
    assert "Project not found" in response.text


def test_tools_list_read_and_apply_contracts(client: TestClient):
    headers = _register_and_auth(client)
    project = _create_project(client, headers, "Tools ListReadApply Project")

    create_response = client.post(
        "/api/v1/tools/createFile",
        json={
            "project_id": project["id"],
            "filename": "counter.sv",
            "artifact_type": "rtl",
            "content": "module counter; endmodule\n",
        },
    )
    assert create_response.status_code == 201, create_response.text
    artifact = create_response.json()["artifact"]

    list_response = client.post(
        "/api/v1/tools/listFiles",
        json={"project_id": project["id"]},
    )
    assert list_response.status_code == 200, list_response.text
    list_payload = list_response.json()
    assert list_payload["success"] is True
    assert any(f["id"] == artifact["id"] for f in list_payload["files"])

    read_response = client.post(
        "/api/v1/tools/readFile",
        json={"artifact_id": artifact["id"]},
    )
    assert read_response.status_code == 200, read_response.text
    read_payload = read_response.json()
    assert read_payload["success"] is True
    assert "module counter" in read_payload["content"]

    apply_response = client.post(
        "/api/v1/tools/applyCodeToFile",
        json={
            "artifact_id": artifact["id"],
            "code": "// appended by test\n",
            "strategy": "smart_insert",
        },
    )
    assert apply_response.status_code == 200, apply_response.text
    apply_payload = apply_response.json()
    assert apply_payload["success"] is True
    assert "appended by test" in apply_payload["new_content"]
    assert apply_payload.get("diff")


def test_assistant_health_contract(client: TestClient):
    response = client.get("/api/v1/assistant/health")
    assert response.status_code == 200, response.text

    payload = response.json()
    assert payload.get("status") == "ok"
    llm = payload.get("llm") or {}
    assert "provider" in llm
    assert "model" in llm
    assert "configured" in llm


def test_tools_run_simulation_prepares_mental_model(client: TestClient):
    headers = _register_and_auth(client)
    project = _create_project(client, headers, "Tools RunSimulation Project")

    spec_response = client.post(
        "/api/v1/tools/createFile",
        json={
            "project_id": project["id"],
            "filename": "fifo_spec.txt",
            "artifact_type": "spec",
            "content": "FIFO must reset cleanly and must not overflow.",
        },
    )
    assert spec_response.status_code == 201, spec_response.text

    rtl_response = client.post(
        "/api/v1/tools/createFile",
        json={
            "project_id": project["id"],
            "filename": "fifo.sv",
            "artifact_type": "rtl",
            "content": (
                "module fifo(input logic clk, input logic rst_n, "
                "output logic empty, output logic full); endmodule\n"
            ),
        },
    )
    assert rtl_response.status_code == 201, rtl_response.text

    captured = {}

    def fake_start_job(run_id: str, rtl_path: str, spec_path: str, output_dir: str):
        captured["run_id"] = run_id
        captured["rtl_path"] = rtl_path
        captured["spec_path"] = spec_path
        captured["output_dir"] = output_dir

    with patch("routes.tools.start_job", side_effect=fake_start_job):
        response = client.post(
            "/api/v1/tools/runSimulation",
            json={"project_id": project["id"]},
        )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["success"] is True
    assert payload["mental_model_revision_id"]
    assert captured["run_id"] == payload["run_id"]

    db = SessionLocal()
    try:
        run = db.query(Run).filter(Run.id == payload["run_id"]).first()
        assert run is not None
        assert run.mental_model_revision_id == payload["mental_model_revision_id"]
        phases = [
            event.phase
            for event in db.query(RunEvent)
            .filter(RunEvent.run_id == run.id)
            .order_by(RunEvent.seq_no.asc())
            .all()
        ]
        assert "mental_model.check" in phases
        assert "mental_model.generated" in phases
        assert "mental_model.persisted" in phases
    finally:
        db.close()
