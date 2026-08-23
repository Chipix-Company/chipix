import os
import sys
import shutil
import uuid
import importlib.util
import io
import zipfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

# Configure test environment before importing app modules.
os.environ["CHIPVERIFY_SECRET_KEY"] = "test-contract-secret-key"
TEST_DB_PATH = Path(__file__).resolve().parent / "test_api_contracts.db"
TEST_TMP_ROOT = Path(__file__).resolve().parent / "tmp"
TEST_TMP_ROOT.mkdir(exist_ok=True)
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
from services.block_smoke import (  # noqa: E402
    _extract_ansi_ports,
    _generated_testbench,
    run_block_smoke_verification,
)


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
    payload_response = client.post("/api/v1/auth/auto-login")
    assert payload_response.status_code == 200, payload_response.text
    payload = payload_response.json()
    headers = {"Authorization": f"Bearer {payload['access_token']}"}
    return headers, payload


def _create_project(client: TestClient, headers: dict, name: str):
    response = client.post(
        "/api/v1/projects",
        headers=headers,
        json={"name": name, "description": "Contract test project"},
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_auth_and_project_contracts(client: TestClient):
    headers, register_payload = _register_and_auth(client)

    register_attempt = client.post(
        "/api/v1/auth/register",
        json={
            "email": f"contract-{uuid.uuid4().hex[:8]}@example.local",
            "password": "ContractPass123!",
            "full_name": "Contract User",
        },
    )
    assert register_attempt.status_code == 403

    me_response = client.get("/api/v1/auth/me", headers=headers)
    assert me_response.status_code == 200
    me_payload = me_response.json()
    assert me_payload["user"]["id"] == register_payload["user"]["id"]

    projects_no_headers_response = client.get("/api/v1/projects")
    assert projects_no_headers_response.status_code == 200

    projects_response = client.get("/api/v1/projects", headers=headers)
    assert projects_response.status_code == 200
    assert len(projects_response.json()) >= 1

    created_project = _create_project(client, headers, "API Contract Project")
    project_id = created_project["id"]

    get_project_response = client.get(f"/api/v1/projects/{project_id}", headers=headers)
    assert get_project_response.status_code == 200
    assert get_project_response.json()["name"] == "API Contract Project"


def test_artifact_revision_contracts(client: TestClient):
    headers, _ = _register_and_auth(client)
    project = _create_project(client, headers, "Artifact Contract Project")
    project_id = project["id"]

    spec_response = client.post(
        f"/api/v1/projects/{project_id}/artifacts/spec",
        headers=headers,
        data={
            "content": "SPEC: counter increments every clock",
            "source": "contract_test",
        },
    )
    assert spec_response.status_code == 201, spec_response.text
    spec_payload = spec_response.json()
    spec_artifact_id = spec_payload["artifact"]["id"]
    assert spec_payload["artifact"]["metadata"]["analysis"]["status"] == "parsed"
    assert (
        "counter increments"
        in spec_payload["artifact"]["metadata"]["analysis"]["preview_text"]
    )

    rtl_response = client.post(
        f"/api/v1/projects/{project_id}/artifacts/rtl",
        headers=headers,
        data={"source": "contract_test"},
        files={
            "artifact_file": (
                "counter.sv",
                "module counter(input logic clk, input logic rst_n, output logic done);\nendmodule\n",
                "text/plain",
            )
        },
    )
    assert rtl_response.status_code == 201, rtl_response.text
    rtl_payload = rtl_response.json()
    rtl_artifact_id = rtl_payload["artifact"]["id"]
    assert rtl_payload["artifact"]["metadata"]["analysis"]["status"] == "parsed"
    assert rtl_payload["artifact"]["metadata"]["analysis"]["parser"] == "rtl_parser"
    assert (
        rtl_payload["artifact"]["metadata"]["analysis"]["detected_extension"] == ".sv"
    )
    assert rtl_payload["artifact"]["metadata"]["analysis"]["module_count"] >= 1
    ast_graph = rtl_payload["artifact"]["metadata"]["analysis"].get("ast_visualization")
    assert ast_graph is not None
    assert ast_graph.get("status") in {"parsed", "error"}
    assert (
        rtl_payload["artifact"]["metadata"]["analysis"]["modules"][0]["name"]
        == "counter"
    )

    list_response = client.get(
        f"/api/v1/projects/{project_id}/artifacts", headers=headers
    )
    assert list_response.status_code == 200
    list_payload = list_response.json()
    assert len(list_payload["artifacts"]["spec"]) == 1
    assert len(list_payload["artifacts"]["rtl"]) == 1

    get_spec_response = client.get(
        f"/api/v1/projects/{project_id}/artifacts/{spec_artifact_id}?include_content=true",
        headers=headers,
    )
    assert get_spec_response.status_code == 200
    assert "counter increments" in get_spec_response.json()["content"]
    assert get_spec_response.json()["content_source"] == "parsed_spec_text"

    set_active_response = client.patch(
        f"/api/v1/projects/{project_id}/artifacts/active",
        headers=headers,
        json={
            "spec_artifact_id": spec_artifact_id,
            "rtl_artifact_id": rtl_artifact_id,
        },
    )
    assert set_active_response.status_code == 200
    active_payload = set_active_response.json()["active"]
    assert active_payload["active_spec_artifact_id"] == spec_artifact_id
    assert active_payload["active_rtl_artifact_id"] == rtl_artifact_id

    rename_response = client.patch(
        f"/api/v1/projects/{project_id}/artifacts/{spec_artifact_id}/rename",
        headers=headers,
        json={"filename": "spec_counter_v2.txt"},
    )
    assert rename_response.status_code == 200, rename_response.text
    assert rename_response.json()["artifact"]["filename"] == "spec_counter_v2.txt"

    mental_model_response = client.post(
        f"/api/v1/projects/{project_id}/mental-models/build",
        headers=headers,
        json={
            "spec_artifact_id": spec_artifact_id,
            "rtl_artifact_id": rtl_artifact_id,
        },
    )
    assert mental_model_response.status_code == 201, mental_model_response.text
    mental_model_payload = mental_model_response.json()["mental_model"]
    assert mental_model_payload["revision"] == 1
    assert mental_model_payload["source_spec_artifact_id"] == spec_artifact_id
    assert mental_model_payload["source_rtl_artifact_id"] == rtl_artifact_id
    assert mental_model_payload["content"]["kind"] == "chipstack_style_mental_model"
    assert mental_model_payload["content"]["design"]["top_module"] == "counter"
    assert mental_model_payload["content"]["requirements"]
    assert mental_model_payload["content"]["verification_intent"]["unit_tests"]
    assert mental_model_payload["content"]["visualizer"]["requirement_matrix"]
    assert mental_model_payload["content"]["vplan"]["schema_version"] == "dv.vplan.1"
    assert mental_model_payload["content"]["vplan"]["requirements"]
    assert (
        mental_model_payload["content"]["signoff_readiness"]["overall_status"]
        != "signoff_ready"
    )
    mental_model_run = mental_model_response.json()["run"]
    assert mental_model_run["status"] == "completed"
    assert mental_model_run["specification_type"] == "mental_model"
    assert mental_model_run["mental_model_revision_id"] == mental_model_payload["id"]

    mental_model_events_response = client.get(
        f"/api/v1/runs/{mental_model_run['id']}/events",
        headers=headers,
    )
    assert mental_model_events_response.status_code == 200
    mental_model_events = mental_model_events_response.json()["events"]
    assert [event["seq_no"] for event in mental_model_events] == [1, 2, 3, 4]
    assert mental_model_events[-1]["phase"] == "mental_model.persisted"

    mental_model_status_response = client.get(
        f"/api/v1/runs/{mental_model_run['id']}/status",
        headers=headers,
    )
    assert mental_model_status_response.status_code == 200
    assert mental_model_status_response.json()["status"] == "completed"
    assert mental_model_status_response.json()["events_count"] == 4

    latest_mental_model_response = client.get(
        f"/api/v1/projects/{project_id}/mental-models/latest",
        headers=headers,
    )
    assert latest_mental_model_response.status_code == 200
    assert (
        latest_mental_model_response.json()["mental_model"]["id"]
        == mental_model_payload["id"]
    )

    vplan_response = client.get(
        f"/api/v1/projects/{project_id}/vplan",
        headers=headers,
    )
    assert vplan_response.status_code == 200, vplan_response.text
    vplan_payload = vplan_response.json()["vplan"]
    assert vplan_payload["stage"] in {"V0", "V1"}
    assert vplan_payload["summary"]["total_requirements"] >= 1
    assert vplan_payload["requirements"][0]["status"] in {
        "planned",
        "missing_intent",
    }

    readiness_response = client.get(
        f"/api/v1/projects/{project_id}/chipstack-readiness",
        headers=headers,
    )
    assert readiness_response.status_code == 200
    readiness_payload = readiness_response.json()
    assert readiness_payload["latest_mental_model"]["id"] == mental_model_payload["id"]
    assert readiness_payload["vplan"]["schema_version"] == "dv.vplan.1"
    assert (
        readiness_payload["industry_readiness"]["overall_status"]
        != "signoff_ready"
    )
    gate_ids = {
        gate["id"] for gate in readiness_payload["industry_readiness"]["gates"]
    }
    assert {
        "vplan.exists",
        "coverage.measured",
        "formal.measured",
        "execution.rtl_plus_tb",
    }.issubset(gate_ids)
    agent_ids = {agent["id"] for agent in readiness_payload["agents"]}
    assert {
        "mental_model",
        "unit_testing",
        "formal",
        "uvm_generator",
        "auto_fixer",
        "rtl_optimization",
        "design_upgrade",
        "soc",
        "signoff",
    }.issubset(agent_ids)

    patch_response = client.post(
        f"/api/v1/projects/{project_id}/patch-proposals",
        headers=headers,
        json={
            "source_agent": "DebugAgent",
            "target_artifact_id": rtl_artifact_id,
            "title": "Fix counter done flag",
            "reason": "Debug agent found a one-cycle-late done flag.",
            "diff_text": "--- a/counter.sv\n+++ b/counter.sv\n@@\n- assign done = 0;\n+ assign done = 1;\n",
            "metadata": {"tests_to_rerun": ["unit_counter_done"]},
        },
    )
    assert patch_response.status_code == 201, patch_response.text
    proposal_id = patch_response.json()["patch_proposal"]["id"]

    decision_response = client.patch(
        f"/api/v1/projects/{project_id}/patch-proposals/{proposal_id}",
        headers=headers,
        json={"status": "approved", "note": "Looks safe for focused rerun"},
    )
    assert decision_response.status_code == 200, decision_response.text
    assert decision_response.json()["patch_proposal"]["status"] == "approved"


def test_generated_artifact_sync_contracts(client: TestClient):
    headers, register_payload = _register_and_auth(client)
    project = _create_project(client, headers, "Generated Artifact Sync Project")
    project_id = project["id"]
    run_id = str(uuid.uuid4())

    run_output_dir = TEST_TMP_ROOT / f"run-output-{uuid.uuid4().hex}"
    try:
        (run_output_dir / "inputs").mkdir(parents=True, exist_ok=True)
        (run_output_dir / "reports").mkdir(parents=True, exist_ok=True)
        (run_output_dir / "uvm").mkdir(parents=True, exist_ok=True)

        (run_output_dir / "inputs" / "spec.txt").write_text(
            "input file should be excluded",
            encoding="utf-8",
        )
        (run_output_dir / "reports" / "summary.json").write_text(
            '{"status":"ok"}',
            encoding="utf-8",
        )
        (run_output_dir / "uvm" / "tb.sv").write_text(
            'module tb; initial begin $display("sync"); end endmodule\n',
            encoding="utf-8",
        )
        (run_output_dir / "runtime.log").write_text(
            "pipeline completed\n",
            encoding="utf-8",
        )

        db = SessionLocal()
        try:
            db.add(
                Run(
                    id=run_id,
                    organization_id=register_payload["organization"]["id"],
                    project_id=project_id,
                    user_id=register_payload["user"]["id"],
                    prompt_text="Sync generated outputs",
                    specification_type="text",
                    status="completed",
                    execution_time="3s",
                    gpu_used=False,
                    output_path=str(run_output_dir),
                )
            )
            db.commit()
        finally:
            db.close()

        first_sync_response = client.post(
            f"/api/v1/projects/{project_id}/runs/{run_id}/sync-artifacts",
            headers=headers,
        )
        assert first_sync_response.status_code == 200, first_sync_response.text
        first_sync_payload = first_sync_response.json()
        assert first_sync_payload["created_count"] == 3
        assert first_sync_payload["discovered_count"] == 3

        list_response = client.get(
            f"/api/v1/projects/{project_id}/artifacts",
            headers=headers,
        )
        assert list_response.status_code == 200
        generated_artifacts = list_response.json()["artifacts"]["generated"]
        assert len(generated_artifacts) == 3
        relative_paths = {
            artifact["metadata"]["relative_path"] for artifact in generated_artifacts
        }
        assert relative_paths == {"reports/summary.json", "uvm/tb.sv", "runtime.log"}

        runtime_artifact = next(
            artifact
            for artifact in generated_artifacts
            if artifact["metadata"]["relative_path"] == "runtime.log"
        )
        rename_generated_response = client.patch(
            f"/api/v1/projects/{project_id}/artifacts/{runtime_artifact['id']}/rename",
            headers=headers,
            json={"filename": "runtime-renamed.log"},
        )
        assert (
            rename_generated_response.status_code == 200
        ), rename_generated_response.text
        renamed_artifact = rename_generated_response.json()["artifact"]
        assert renamed_artifact["filename"] == "runtime-renamed.log"
        assert renamed_artifact["metadata"]["relative_path"] == "runtime-renamed.log"

        second_sync_response = client.post(
            f"/api/v1/projects/{project_id}/runs/{run_id}/sync-artifacts",
            headers=headers,
        )
        assert second_sync_response.status_code == 200, second_sync_response.text
        second_sync_payload = second_sync_response.json()
        assert second_sync_payload["created_count"] == 0
        assert second_sync_payload["skipped_count"] == 3
    finally:
        shutil.rmtree(run_output_dir, ignore_errors=True)


def test_runs_events_and_chat_contracts(client: TestClient):
    headers, _ = _register_and_auth(client)
    project = _create_project(client, headers, "Runtime Contract Project")
    project_id = project["id"]

    def fake_start_job(run_id: str, rtl_path: str, spec_path: str, output_dir: str):
        _ = rtl_path, spec_path, output_dir
        db = SessionLocal()
        try:
            run = db.query(Run).filter(Run.id == run_id).first()
            assert run is not None
            assert run.mental_model_revision_id
            run.status = "completed"
            run.execution_time = "1s"
            run.verification_summary = '{"result": "ok"}'

            max_seq = (
                db.query(RunEvent)
                .filter(RunEvent.run_id == run_id)
                .order_by(RunEvent.seq_no.desc())
                .first()
            )
            next_seq = int(max_seq.seq_no if max_seq else 0) + 1
            messages = [
                "Run queued.",
                "Mock pipeline started.",
                "Mock pipeline finished.",
            ]
            for index, message in enumerate(messages, start=1):
                db.add(
                    RunEvent(
                        id=str(uuid.uuid4()),
                        run_id=run_id,
                        seq_no=next_seq + index - 1,
                        event_kind="log",
                        phase="test",
                        level="info",
                        message=message,
                    )
                )
            db.commit()
        finally:
            db.close()

    with patch("routes.api.start_job", side_effect=fake_start_job):
        start_run_response = client.post(
            f"/api/v1/projects/{project_id}/runs",
            headers=headers,
            data={"prompt": "Verify a simple counter"},
        )
    assert start_run_response.status_code == 201, start_run_response.text
    start_run_payload = start_run_response.json()
    run_id = start_run_payload["run_id"]
    assert start_run_payload["mental_model_revision_id"]

    status_response = client.get(f"/api/v1/runs/{run_id}/status", headers=headers)
    assert status_response.status_code == 200
    status_payload = status_response.json()
    assert status_payload["status"] in {"completed", "running"}
    assert status_payload["mental_model_revision_id"] == start_run_payload["mental_model_revision_id"]
    assert any("Mock pipeline finished." in line for line in status_payload["logs"])

    events_response = client.get(
        f"/api/v1/runs/{run_id}/events?after_seq=1",
        headers=headers,
    )
    assert events_response.status_code == 200
    events_payload = events_response.json()
    assert any(
        event["message"] == "Mock pipeline finished."
        for event in events_payload["events"]
    )

    agents_response = client.get(
        f"/api/v1/projects/{project_id}/agents",
        headers=headers,
    )
    assert agents_response.status_code == 200
    agents_payload = agents_response.json()
    assert agents_payload["project_id"] == project_id
    assert agents_payload["run_id"] == run_id
    assert agents_payload["run_status"] in {"completed", "running"}
    assert len(agents_payload["agents"]) >= 9
    agent_ids = {agent["id"] for agent in agents_payload["agents"]}
    assert {"mental_model", "unit_testing", "formal", "auto_fixer"}.issubset(
        agent_ids
    )
    assert {"id", "name", "description", "status"}.issubset(
        set(agents_payload["agents"][0].keys())
    )

    with client.stream(
        "GET",
        f"/api/v1/runs/{run_id}/events/stream?after_seq=0",
        headers=headers,
    ) as stream_response:
        assert stream_response.status_code == 200
        stream_text = ""
        for chunk in stream_response.iter_text():
            stream_text += chunk
            if "event: end" in stream_text:
                break

    assert "event: run_event" in stream_text
    assert "event: end" in stream_text

    create_thread_response = client.post(
        f"/api/v1/projects/{project_id}/chat/threads",
        headers=headers,
        json={"title": "Contract Chat"},
    )
    assert create_thread_response.status_code == 201
    thread_id = create_thread_response.json()["id"]

    ask_response = client.post(
        f"/api/v1/chat/threads/{thread_id}/ask",
        headers=headers,
        json={"prompt": "Why did the run fail?"},
    )
    assert ask_response.status_code == 200
    ask_payload = ask_response.json()
    assert ask_payload["user_message"]["role"] == "user"
    assert ask_payload["assistant_message"]["role"] == "assistant"

    messages_response = client.get(
        f"/api/v1/chat/threads/{thread_id}/messages",
        headers=headers,
    )
    assert messages_response.status_code == 200
    assert len(messages_response.json()["messages"]) >= 2


def test_start_run_accepts_zipped_rtl_project_folder(client: TestClient):
    headers, _ = _register_and_auth(client)
    project = _create_project(client, headers, "RTL Folder Upload Project")
    project_id = project["id"]

    archive_buffer = io.BytesIO()
    with zipfile.ZipFile(archive_buffer, "w") as archive:
        archive.writestr(
            "rtl/top.sv",
            "\n".join(
                [
                    "module top(input logic clk, input logic rst_n, output logic done);",
                    "  child u_child(.clk(clk), .rst_n(rst_n), .done(done));",
                    "endmodule",
                ]
            ),
        )
        archive.writestr(
            "rtl/child.sv",
            "\n".join(
                [
                    "module child(input logic clk, input logic rst_n, output logic done);",
                    "  assign done = rst_n;",
                    "endmodule",
                ]
            ),
        )
    archive_buffer.seek(0)

    captured = {}

    def fake_start_job(run_id: str, rtl_path: str, spec_path: str, output_dir: str):
        captured["run_id"] = run_id
        captured["rtl_path"] = rtl_path
        captured["spec_path"] = spec_path
        captured["output_dir"] = output_dir

        rtl_project_dir = Path(rtl_path)
        assert rtl_project_dir.is_dir()
        assert (rtl_project_dir / "rtl" / "top.sv").exists()
        assert (rtl_project_dir / "rtl" / "child.sv").exists()

    with patch("routes.api.start_job", side_effect=fake_start_job):
        response = client.post(
            f"/api/v1/projects/{project_id}/runs",
            headers=headers,
            data={"prompt": "Verify the top module with its child module"},
            files={
                "rtl_file": (
                    "rtl_project.zip",
                    archive_buffer.getvalue(),
                    "application/zip",
                )
            },
        )

    assert response.status_code == 201, response.text
    payload = response.json()
    assert payload["mental_model_revision_id"]
    assert payload["rtl_input"]["kind"] == "archive"
    assert payload["rtl_input"]["rtl_file_count"] == 2
    assert captured["rtl_path"].endswith("rtl_upload_project")


def test_start_run_block_smoke_profile_falls_back_to_legacy_pipeline(client: TestClient):
    headers, _ = _register_and_auth(client)
    project = _create_project(client, headers, "Legacy Fallback Profile Project")
    project_id = project["id"]
    captured = {}

    def fake_start_job(
        run_id: str,
        rtl_path: str,
        spec_path: str,
        output_dir: str,
    ):
        captured["run_id"] = run_id
        captured["rtl_path"] = rtl_path
        captured["spec_path"] = spec_path
        captured["output_dir"] = output_dir

    with patch("routes.api.start_job", side_effect=fake_start_job):
        response = client.post(
            f"/api/v1/projects/{project_id}/runs",
            headers=headers,
            data={
                "prompt": "The counter must reset to zero and should increment each cycle.",
                "verification_profile": "block_smoke",
            },
            files={
                "rtl_file": (
                    "counter.sv",
                    "\n".join(
                        [
                            "module counter(input logic clk, input logic rst_n, output logic [3:0] count);",
                            "  always_ff @(posedge clk or negedge rst_n) begin",
                            "    if (!rst_n) count <= 0;",
                            "    else count <= count + 1;",
                            "  end",
                            "endmodule",
                        ]
                    ),
                    "text/plain",
                )
            },
        )

    assert response.status_code == 201, response.text
    payload = response.json()
    assert payload["verification_profile"] == "legacy"
    assert payload["mental_model_revision_id"]
    assert captured["rtl_path"].endswith("rtl_counter.sv")
    assert captured["spec_path"].endswith("spec.txt")


def test_block_smoke_reports_blocked_when_icarus_missing():
    workspace = TEST_TMP_ROOT / f"block-smoke-{uuid.uuid4().hex}"
    rtl_path = workspace / "counter.sv"
    output_dir = workspace / "out"
    workspace.mkdir(parents=True, exist_ok=True)
    rtl_path.write_text(
        "\n".join(
            [
                "module counter(input logic clk, input logic rst_n, output logic [3:0] count);",
                "  always_ff @(posedge clk or negedge rst_n) begin",
                "    if (!rst_n) count <= 0;",
                "    else count <= count + 1;",
                "  end",
                "endmodule",
            ]
        ),
        encoding="utf-8",
    )

    try:
        with patch(
            "services.block_smoke.detect_toolchain_status",
            return_value={
                "iverilog": {
                    "name": "iverilog",
                    "available": False,
                    "guidance": "Install Icarus Verilog.",
                },
                "vvp": {
                    "name": "vvp",
                    "available": False,
                    "guidance": "Install vvp.",
                },
            },
        ):
            result = run_block_smoke_verification(
                rtl_path=rtl_path,
                output_dir=output_dir,
                mental_model={"design": {"top_module": "counter"}},
            )

        assert result["status"] == "blocked"
        assert result["reason"] == "blocked_tool_missing"
        assert set(result["missing_tools"]) == {"iverilog", "vvp"}
        assert Path(result["artifacts"]["report"]).exists()
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


def test_block_smoke_generates_connected_fifo_stimulus_for_ansi_ports():
    workspace = TEST_TMP_ROOT / f"block-smoke-ansi-{uuid.uuid4().hex}"
    rtl_path = workspace / "fifo.sv"
    workspace.mkdir(parents=True, exist_ok=True)
    rtl_path.write_text(
        "\n".join(
            [
                "module fifo (",
                "  input logic clk,",
                "  input logic rst,",
                "  input logic wr,",
                "  input logic rd,",
                "  input logic [7:0] data_in,",
                "  output logic [7:0] data_out,",
                "  output logic empty,",
                "  output logic full,",
                "  output logic [3:0] fifo_cnt",
                ");",
                "  always_ff @(posedge clk or negedge rst) begin",
                "    if (!rst) fifo_cnt <= 0;",
                "    else if (wr && !full) fifo_cnt <= fifo_cnt + 1;",
                "  end",
                "endmodule",
            ]
        ),
        encoding="utf-8",
    )

    try:
        ports = _extract_ansi_ports(rtl_path, "fifo")
        testbench = _generated_testbench(
            "tb_fifo",
            "fifo",
            {"ports": ports, "source_path": str(rtl_path)},
            "block_smoke.vcd",
        )

        assert "fifo dut();" not in testbench
        assert ".clk(clk)" in testbench
        assert ".rst(rst)" in testbench
        assert ".wr(wr)" in testbench
        assert ".rd(rd)" in testbench
        assert "wire  empty;" in testbench
        assert "wr = 1'b1;" in testbench
        assert "rd = 1'b1;" in testbench
        assert "data_in = 'hA5;" in testbench
        assert "BLOCK_SMOKE_FAIL:first_read_data_should_match_first_write" in testbench
        assert "BLOCK_SMOKE_FAIL_ERRORS=%0d" in testbench
        assert "rst = 0;" in testbench
        assert "rst = 1;" in testbench
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


def test_chat_thread_lifecycle_and_pagination(client: TestClient):
    headers, register_payload = _register_and_auth(client)
    project = _create_project(client, headers, "Chat Lifecycle Contract Project")
    project_id = project["id"]

    create_thread_response = client.post(
        f"/api/v1/projects/{project_id}/chat/threads",
        headers=headers,
        json={"title": "Lifecycle Thread"},
    )
    assert create_thread_response.status_code == 201
    thread_id = create_thread_response.json()["id"]

    run_id = str(uuid.uuid4())
    db = SessionLocal()
    try:
        db.add(
            Run(
                id=run_id,
                organization_id=register_payload["organization"]["id"],
                project_id=project_id,
                user_id=register_payload["user"]["id"],
                prompt_text="Context run for chat pinning",
                specification_type="text",
                status="failed",
                execution_time="2s",
                gpu_used=False,
            )
        )
        db.commit()
    finally:
        db.close()

    rename_and_pin_response = client.patch(
        f"/api/v1/chat/threads/{thread_id}",
        headers=headers,
        json={"title": "Renamed Lifecycle Thread", "context_run_id": run_id},
    )
    assert rename_and_pin_response.status_code == 200
    renamed_payload = rename_and_pin_response.json()
    assert renamed_payload["title"] == "Renamed Lifecycle Thread"
    assert renamed_payload["context_run_id"] == run_id
    assert renamed_payload["archived"] is False

    observed: dict[str, str | None] = {"run_id": None}

    async def _fake_ai_response(_prompt: str, _history: list[dict], run: Run | None):
        observed["run_id"] = run.id if run else None
        return "Mock assistant response"

    mock_ai = AsyncMock(side_effect=_fake_ai_response)
    with patch(
        "routes.api._generate_debug_assistant_response_with_history", new=mock_ai
    ):
        ask_response = client.post(
            f"/api/v1/chat/threads/{thread_id}/ask",
            headers=headers,
            json={"prompt": "Use pinned run context"},
        )
        assert ask_response.status_code == 200
        assert mock_ai.await_count == 1
        assert observed["run_id"] == run_id

        for index in range(3):
            follow_up_response = client.post(
                f"/api/v1/chat/threads/{thread_id}/ask",
                headers=headers,
                json={"prompt": f"Follow-up question {index + 1}"},
            )
            assert follow_up_response.status_code == 200

    page_one_response = client.get(
        f"/api/v1/chat/threads/{thread_id}/messages?limit=3",
        headers=headers,
    )
    assert page_one_response.status_code == 200
    page_one_payload = page_one_response.json()
    assert len(page_one_payload["messages"]) == 3
    assert page_one_payload["pagination"]["has_more"] is True
    assert page_one_payload["pagination"]["next_cursor"]

    page_two_response = client.get(
        (
            f"/api/v1/chat/threads/{thread_id}/messages?limit=3"
            f"&before_message_id={page_one_payload['pagination']['next_cursor']}"
        ),
        headers=headers,
    )
    assert page_two_response.status_code == 200
    page_two_payload = page_two_response.json()
    assert len(page_two_payload["messages"]) == 3
    page_one_ids = {message["id"] for message in page_one_payload["messages"]}
    page_two_ids = {message["id"] for message in page_two_payload["messages"]}
    assert page_one_ids.isdisjoint(page_two_ids)

    archive_response = client.patch(
        f"/api/v1/chat/threads/{thread_id}",
        headers=headers,
        json={"archived": True},
    )
    assert archive_response.status_code == 200
    assert archive_response.json()["archived"] is True

    default_threads_response = client.get(
        f"/api/v1/projects/{project_id}/chat/threads",
        headers=headers,
    )
    assert default_threads_response.status_code == 200
    assert all(thread["id"] != thread_id for thread in default_threads_response.json())

    archived_threads_response = client.get(
        f"/api/v1/projects/{project_id}/chat/threads?include_archived=true",
        headers=headers,
    )
    assert archived_threads_response.status_code == 200
    assert any(thread["id"] == thread_id for thread in archived_threads_response.json())

    archived_ask_response = client.post(
        f"/api/v1/chat/threads/{thread_id}/ask",
        headers=headers,
        json={"prompt": "This should fail while archived"},
    )
    assert archived_ask_response.status_code == 409

    unarchive_response = client.patch(
        f"/api/v1/chat/threads/{thread_id}",
        headers=headers,
        json={"archived": False},
    )
    assert unarchive_response.status_code == 200
    assert unarchive_response.json()["archived"] is False

    delete_response = client.delete(
        f"/api/v1/chat/threads/{thread_id}",
        headers=headers,
    )
    assert delete_response.status_code == 200
    assert delete_response.json()["deleted"] is True

    threads_after_delete_response = client.get(
        f"/api/v1/projects/{project_id}/chat/threads?include_archived=true",
        headers=headers,
    )
    assert threads_after_delete_response.status_code == 200
    assert all(
        thread["id"] != thread_id for thread in threads_after_delete_response.json()
    )


def test_multiple_chat_threads_per_project(client: TestClient):
    """Multi-thread per project — the invariant the thread-first UI relies on.

    Three threads coexist in the same project; messages stay scoped to their
    own thread; list ordering reflects most-recently-active first; the
    cross-thread isolation guards (list, message-list, delete) hold.
    """
    headers, _ = _register_and_auth(client)
    project = _create_project(client, headers, "Multi-thread Contract Project")
    project_id = project["id"]

    # Create three threads with distinct titles.
    thread_ids = {}
    for label in ("Alpha", "Beta", "Gamma"):
        r = client.post(
            f"/api/v1/projects/{project_id}/chat/threads",
            headers=headers,
            json={"title": f"Thread {label}"},
        )
        assert r.status_code == 201, r.text
        thread_ids[label] = r.json()["id"]
    assert len({*thread_ids.values()}) == 3, "Each thread must have a unique id"

    async def _fake_ai(_prompt: str, _history: list[dict], _run):
        return "ok"

    mock_ai = AsyncMock(side_effect=_fake_ai)
    with patch(
        "routes.api._generate_debug_assistant_response_with_history", new=mock_ai
    ):
        # 1 message in Alpha
        r = client.post(
            f"/api/v1/chat/threads/{thread_ids['Alpha']}/ask",
            headers=headers,
            json={"prompt": "Hello from Alpha"},
        )
        assert r.status_code == 200, r.text

        # 2 messages in Beta (so it becomes the most-recently-active)
        for prompt in ("Beta msg 1", "Beta msg 2"):
            r = client.post(
                f"/api/v1/chat/threads/{thread_ids['Beta']}/ask",
                headers=headers,
                json={"prompt": prompt},
            )
            assert r.status_code == 200, r.text

    # Listing returns all three.
    list_response = client.get(
        f"/api/v1/projects/{project_id}/chat/threads",
        headers=headers,
    )
    assert list_response.status_code == 200
    listed = list_response.json()
    listed_ids = [t["id"] for t in listed]
    for tid in thread_ids.values():
        assert tid in listed_ids, f"Expected {tid} in list response"

    # Beta should sort first (most recently updated by ask).
    assert listed_ids[0] == thread_ids["Beta"], (
        f"Expected Beta first by updated_at; got {listed_ids}"
    )

    # Messages stay scoped to their own thread.
    # Each ask creates 2 ChatMessage rows (user + assistant).
    alpha_msgs = client.get(
        f"/api/v1/chat/threads/{thread_ids['Alpha']}/messages",
        headers=headers,
    ).json()
    assert len(alpha_msgs["messages"]) == 2  # 1 user + 1 assistant

    beta_msgs = client.get(
        f"/api/v1/chat/threads/{thread_ids['Beta']}/messages",
        headers=headers,
    ).json()
    assert len(beta_msgs["messages"]) == 4  # 2 user + 2 assistant

    gamma_msgs = client.get(
        f"/api/v1/chat/threads/{thread_ids['Gamma']}/messages",
        headers=headers,
    ).json()
    assert gamma_msgs["messages"] == []

    # Deleting one thread leaves the others intact.
    delete_response = client.delete(
        f"/api/v1/chat/threads/{thread_ids['Alpha']}",
        headers=headers,
    )
    assert delete_response.status_code == 200

    after_delete = client.get(
        f"/api/v1/projects/{project_id}/chat/threads",
        headers=headers,
    ).json()
    after_delete_ids = {t["id"] for t in after_delete}
    assert thread_ids["Alpha"] not in after_delete_ids
    assert thread_ids["Beta"] in after_delete_ids
    assert thread_ids["Gamma"] in after_delete_ids

    # Fetching messages for the deleted thread returns 404.
    deleted_msgs = client.get(
        f"/api/v1/chat/threads/{thread_ids['Alpha']}/messages",
        headers=headers,
    )
    assert deleted_msgs.status_code == 404


def test_chat_thread_title_rename_via_patch(client: TestClient):
    """Auto-title flow: the frontend creates a thread with a default title,
    then PATCHes the title once the first user message determines a useful
    name. This test pins the PATCH path that flow depends on."""
    headers, _ = _register_and_auth(client)
    project = _create_project(client, headers, "Auto Title Contract Project")
    project_id = project["id"]

    create_response = client.post(
        f"/api/v1/projects/{project_id}/chat/threads",
        headers=headers,
        json={"title": "Project Chat"},
    )
    assert create_response.status_code == 201
    thread_id = create_response.json()["id"]
    assert create_response.json()["title"] == "Project Chat"

    derived = "Build a UART transceiver with APB interface and 16-byte FIFOs"
    rename_response = client.patch(
        f"/api/v1/chat/threads/{thread_id}",
        headers=headers,
        json={"title": derived},
    )
    assert rename_response.status_code == 200
    assert rename_response.json()["title"] == derived

    # Empty titles are rejected.
    empty_response = client.patch(
        f"/api/v1/chat/threads/{thread_id}",
        headers=headers,
        json={"title": "   "},
    )
    assert empty_response.status_code == 400


def test_chat_thread_rewind_truncates_messages(client: TestClient):
    headers, _ = _register_and_auth(client)
    project = _create_project(client, headers, "Rewind Contract Project")
    project_id = project["id"]

    create_thread_response = client.post(
        f"/api/v1/projects/{project_id}/chat/threads",
        headers=headers,
        json={"title": "Rewind Thread"},
    )
    assert create_thread_response.status_code == 201
    thread_id = create_thread_response.json()["id"]

    mock_ai = AsyncMock(return_value="Assistant reply")
    with patch(
        "routes.api._generate_debug_assistant_response_with_history", new=mock_ai
    ):
        assert client.post(
            f"/api/v1/chat/threads/{thread_id}/ask",
            headers=headers,
            json={"prompt": "First question"},
        ).status_code == 200
        assert client.post(
            f"/api/v1/chat/threads/{thread_id}/ask",
            headers=headers,
            json={"prompt": "Second question"},
        ).status_code == 200

    before = client.get(
        f"/api/v1/chat/threads/{thread_id}/messages?limit=20",
        headers=headers,
    )
    assert before.status_code == 200
    assert len(before.json()["messages"]) == 4

    first_user_id = next(
        m["id"] for m in before.json()["messages"] if m["role"] == "user"
    )

    rewind_response = client.post(
        f"/api/v1/chat/threads/{thread_id}/rewind",
        headers=headers,
        json={"message_id": first_user_id, "artifact_ids": ["art-placeholder"]},
    )
    assert rewind_response.status_code == 200
    rewind_payload = rewind_response.json()
    assert rewind_payload["ok"] is True
    assert rewind_payload["messages_deleted"] == 4
    assert rewind_payload["artifact_revert"]["status"] == "deferred"
    assert rewind_payload["artifact_revert"]["requested_artifact_ids"] == [
        "art-placeholder"
    ]

    after = client.get(
        f"/api/v1/chat/threads/{thread_id}/messages?limit=20",
        headers=headers,
    )
    assert after.status_code == 200
    assert after.json()["messages"] == []

    bad_role_response = client.post(
        f"/api/v1/chat/threads/{thread_id}/rewind",
        headers=headers,
        json={"message_id": str(uuid.uuid4())},
    )
    assert bad_role_response.status_code == 404


def test_project_archive_cancel_and_artifact_download_contracts(client: TestClient):
    headers, register_payload = _register_and_auth(client)
    project = _create_project(client, headers, "Archive Contract Project")
    project_id = project["id"]

    spec_response = client.post(
        f"/api/v1/projects/{project_id}/artifacts/spec",
        headers=headers,
        data={
            "content": "SPEC: arbiter grant policy",
            "source": "contract_test",
        },
    )
    assert spec_response.status_code == 201, spec_response.text
    spec_artifact_id = spec_response.json()["artifact"]["id"]

    artifact_download_response = client.get(
        f"/api/v1/projects/{project_id}/artifacts/{spec_artifact_id}/download",
        headers=headers,
    )
    assert artifact_download_response.status_code == 200
    assert artifact_download_response.headers.get("content-disposition")

    delete_artifact_response = client.delete(
        f"/api/v1/projects/{project_id}/artifacts/{spec_artifact_id}",
        headers=headers,
    )
    assert delete_artifact_response.status_code == 200
    assert delete_artifact_response.json()["deleted"] is True

    run_id = str(uuid.uuid4())
    db = SessionLocal()
    try:
        db.add(
            Run(
                id=run_id,
                organization_id=register_payload["organization"]["id"],
                project_id=project_id,
                user_id=register_payload["user"]["id"],
                prompt_text="Cancelable run",
                specification_type="text",
                status="running",
                gpu_used=False,
            )
        )
        db.commit()
    finally:
        db.close()

    cancel_response = client.post(f"/api/v1/runs/{run_id}/cancel", headers=headers)
    assert cancel_response.status_code == 200
    cancel_payload = cancel_response.json()
    assert cancel_payload["run_id"] == run_id
    assert cancel_payload["status"] == "cancelled"
    assert cancel_payload["cancelled"] is True

    archive_project_response = client.delete(
        f"/api/v1/projects/{project_id}",
        headers=headers,
    )
    assert archive_project_response.status_code == 200
    assert archive_project_response.json()["deleted"] is True
    assert archive_project_response.json()["status"] == "archived"

    projects_response = client.get("/api/v1/projects", headers=headers)
    assert projects_response.status_code == 200
    assert all(item["id"] != project_id for item in projects_response.json())


def test_user_documentation_index_and_content(client):
    headers, _ = _register_and_auth(client)

    index_response = client.get("/api/v1/docs/index", headers=headers)
    assert index_response.status_code == 200
    docs = index_response.json().get("docs") or []
    assert docs, "Documentation/ should expose at least one user guide"
    assert docs[0]["name"] == "getting-started.md"
    assert docs[0].get("title")
    assert docs[0].get("description")
    assert isinstance(docs[0].get("updated_at"), int)
    assert all(not d["name"].lower().endswith("readme.md") for d in docs)

    readme_response = client.get("/api/v1/docs/README.md", headers=headers)
    assert readme_response.status_code == 404

    doc_response = client.get("/api/v1/docs/getting-started.md", headers=headers)
    assert doc_response.status_code == 200
    payload = doc_response.json()
    assert payload["name"] == "getting-started.md"
    assert "Chipix Studio" in payload.get("content", "")
    assert payload.get("title")
    assert isinstance(payload.get("updated_at"), int)
