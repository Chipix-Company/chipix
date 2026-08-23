import os
import sys
import importlib.util
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

os.environ["CHIPVERIFY_SECRET_KEY"] = "test-eda-contract-secret-key"
TEST_DB_PATH = Path(__file__).resolve().parent / "test_eda_api_contracts.db"
os.environ["DATABASE_URL"] = f"sqlite:///{TEST_DB_PATH.as_posix()}"
BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

_main_spec = importlib.util.spec_from_file_location(
    "chipverify_backend_main_eda", BACKEND_ROOT / "main.py"
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
        "org_id": "ORG-EDA-TEST",
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
        json={"name": "EDA Workspace", "description": "EDA API test project"},
    )
    assert response.status_code == 200
    return response.json()


def test_eda_toolchain_status_and_netlist_render_contract(client: TestClient):
    headers = _auth_headers(client)
    project = _create_project(client, headers)

    toolchain_response = client.get("/api/v1/eda/toolchain/status", headers=headers)
    assert toolchain_response.status_code == 200
    tools = toolchain_response.json()["tools"]
    assert {"slang", "verible", "yosys", "netlistsvg", "verilator"}.issubset(set(tools.keys()))

    netlist_response = client.post(
        "/api/v1/eda/netlist/render",
        headers=headers,
        json={
            "project_id": project["id"],
            "filename": "counter.sv",
            "source": """
                module child(input logic clk, output logic flag);
                  assign flag = clk;
                endmodule
                module counter(input logic clk, input logic rst_n, output logic done);
                  child u_child(.clk(clk), .flag(done));
                endmodule
            """,
            "top_module": "counter",
        },
    )
    assert netlist_response.status_code == 200, netlist_response.text
    payload = netlist_response.json()
    assert payload["top_module"] == "counter"
    assert payload["cache_key"]
    assert payload["schematic_svg"].startswith("<svg")
    assert len(payload["hierarchy"]) >= 1
    assert payload["tool_messages"]


def test_eda_simulation_contract_handles_missing_or_present_verilator(client: TestClient):
    headers = _auth_headers(client)
    project = _create_project(client, headers)

    create_response = client.post(
        "/api/v1/eda/simulations",
        headers=headers,
        json={
            "project_id": project["id"],
            "filename": "pulse.sv",
            "rtl_source": """
                module pulse(input logic clk, input logic rst_n, output logic done);
                  always_ff @(posedge clk or negedge rst_n) begin
                    if (!rst_n) done <= 1'b0;
                    else done <= ~done;
                  end
                endmodule
            """,
            "top_module": "pulse",
        },
    )
    assert create_response.status_code == 200, create_response.text
    simulation_id = create_response.json()["simulation_id"]

    status_response = client.get(
        f"/api/v1/eda/simulations/{simulation_id}",
        headers=headers,
    )
    assert status_response.status_code == 200
    status_payload = status_response.json()
    assert status_payload["status"] in {"queued", "failed", "completed"}
    assert "diagnostics" in status_payload

    with client.stream(
        "GET",
        f"/api/v1/eda/simulations/{simulation_id}/events?after_seq=0",
        headers=headers,
    ) as stream_response:
        assert stream_response.status_code == 200
        stream_text = ""
        for chunk in stream_response.iter_text():
            stream_text += chunk
            if '"type": "end"' in stream_text:
                break
    assert '"type": "simulation_event"' in stream_text or '"type": "end"' in stream_text
