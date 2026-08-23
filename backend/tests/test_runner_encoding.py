import os
import sys
import tempfile
import uuid
import importlib.util
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

# Configure test environment before importing app modules.
os.environ["CHIPVERIFY_SECRET_KEY"] = "test-runner-encoding-secret"
TEST_DB_PATH = Path(__file__).resolve().parent / "test_runner_encoding.db"
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
from database.database import Base, SessionLocal, engine  # noqa: E402
from database.models import Run  # noqa: E402
from original_core.core.logger import _SafeTextStream  # noqa: E402
from routes import api as api_routes  # noqa: E402
from services import runner  # noqa: E402


def _reset_database():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)


def setup_function():
    _reset_database()
    runner.JOBS.clear()


def teardown_function():
    _reset_database()
    runner.JOBS.clear()


def test_safe_text_stream_replaces_unencodable_characters():
    class Cp1252StrictStream:
        encoding = "cp1252"

        def __init__(self):
            self.buffer = []

        def write(self, text):
            text.encode(self.encoding, errors="strict")
            self.buffer.append(text)
            return len(text)

        def flush(self):
            return None

    stream = Cp1252StrictStream()
    safe_stream = _SafeTextStream(stream)

    safe_stream.write("Output path: C:/Users/aqeed/文档/run\n")

    assert "".join(stream.buffer) == "Output path: C:/Users/aqeed/??/run\n"


def test_runner_captures_non_ascii_prints_without_unicode_failure(tmp_path: Path):
    run_id = str(uuid.uuid4())
    output_dir = tmp_path / "文档-output"
    output_dir.mkdir(parents=True, exist_ok=True)

    class FakeOrchestrator:
        def __init__(self, output_dir: str, use_uvm: bool):
            self.output_dir = output_dir
            self.use_uvm = use_uvm

        async def run(self, rtl_file: str, spec_file: str):
            _ = rtl_file, spec_file
            print(f"Output path: {self.output_dir}")
            print("stderr path marker", file=sys.stderr)
            return {}

    with patch("services.runner.ChipVerifyOrchestrator", FakeOrchestrator):
        runner.run_pipeline_thread(
            run_id,
            rtl_path=str(tmp_path / "rtl.sv"),
            spec_path=str(tmp_path / "spec.txt"),
            output_dir=str(output_dir),
        )

    status = runner.get_job_status(run_id)
    assert status["status"] == "completed"
    assert any("Output path:" in line for line in status["logs"])
    assert any("文档-output" in line for line in status["logs"])
    assert any("stderr path marker" in line for line in status["logs"])
    assert not any("charmap" in line for line in status["logs"])


def test_start_run_accepts_non_ascii_output_dir_without_encoding_failure():
    with TestClient(app) as client, tempfile.TemporaryDirectory(
        prefix="chipverify-文档-"
    ) as temp_dir:
        outputs_root = Path(temp_dir) / "outputs"
        observed = {}

        def fake_start_job(run_id: str, rtl_path: str, spec_path: str, output_dir: str):
            observed["output_dir"] = output_dir
            _ = rtl_path, spec_path
            db = SessionLocal()
            try:
                run = db.query(Run).filter(Run.id == run_id).first()
                assert run is not None
                run.status = "completed"
                run.execution_time = "1s"
                run.verification_summary = '{"result": "ok"}'
                db.commit()
            finally:
                db.close()

        app.dependency_overrides[api_routes._check_license_validity] = lambda: {
            "org_id": "ORG-TEST",
            "max_seats": 100,
        }
        try:
            with patch("routes.api.OUTPUTS_DIR", outputs_root), patch(
                "routes.api.start_job", side_effect=fake_start_job
            ):
                create_project_response = client.post(
                    "/api/v1/projects",
                    json={
                        "name": "Encoding Project",
                        "description": "Non-ASCII path test",
                    },
                )
                assert (
                    create_project_response.status_code == 200
                ), create_project_response.text
                project_id = create_project_response.json()["id"]

                start_run_response = client.post(
                    f"/api/v1/projects/{project_id}/runs",
                    data={"prompt": "Verify encoding-safe startup"},
                )
                assert start_run_response.status_code == 201, start_run_response.text
                run_id = start_run_response.json()["run_id"]

                status_response = client.get(f"/api/v1/runs/{run_id}/status")
                assert status_response.status_code == 200
                assert status_response.json()["status"] == "completed"
        finally:
            app.dependency_overrides.pop(api_routes._check_license_validity, None)

        assert "文档" in observed["output_dir"]


def test_duplicate_spec_upload_is_rejected():
    with TestClient(app) as client:
        create_project_response = client.post(
            "/api/v1/projects",
            json={
                "name": "Duplicate Upload Project",
                "description": "Duplicate artifact test",
            },
        )
        assert create_project_response.status_code == 200, create_project_response.text
        project_id = create_project_response.json()["id"]

        first_upload = client.post(
            f"/api/v1/projects/{project_id}/artifacts/spec",
            data={
                "content": "SPEC: counter increments each cycle",
                "source": "test_upload",
            },
        )
        assert first_upload.status_code == 201, first_upload.text

        duplicate_upload = client.post(
            f"/api/v1/projects/{project_id}/artifacts/spec",
            data={
                "content": "SPEC: counter increments each cycle",
                "source": "test_upload",
            },
        )
        assert duplicate_upload.status_code == 409, duplicate_upload.text
        assert (
            "identical SPEC artifact already exists".lower()
            in duplicate_upload.text.lower()
        )
