"""Live completion API test against real LLM (optional).

Run:
  CHIPVERIFY_COMPLETION_PROVIDER=gemini CHIPVERIFY_COMPLETION_MODEL=gemini-2.5-flash \\
  RUN_LIVE_COMPLETION_TESTS=1 pytest tests/test_completion_live.py -q -s
"""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

os.environ.setdefault("CHIPVERIFY_SECRET_KEY", "test-completion-live-secret")
TEST_DB_PATH = Path(__file__).resolve().parent / "test_completion_live.db"
os.environ["DATABASE_URL"] = f"sqlite:///{TEST_DB_PATH.as_posix()}"
BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

_main_spec = importlib.util.spec_from_file_location(
    "chipverify_backend_main_completion_live", BACKEND_ROOT / "main.py"
)
_main_module = importlib.util.module_from_spec(_main_spec)
assert _main_spec and _main_spec.loader
_main_spec.loader.exec_module(_main_module)
app = _main_module.app

from routes import api as api_routes  # noqa: E402
from database.database import Base, engine  # noqa: E402


pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        os.getenv("RUN_LIVE_COMPLETION_TESTS", "").lower() not in {"1", "true", "yes"},
        reason="Set RUN_LIVE_COMPLETION_TESTS=1 to hit a real LLM",
    ),
]


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
    from fastapi.testclient import TestClient

    with TestClient(app) as test_client:
        yield test_client


def test_live_completion_endpoint(client):
    provider = os.getenv("CHIPVERIFY_COMPLETION_PROVIDER", "gemini")
    model = os.getenv("CHIPVERIFY_COMPLETION_MODEL", "gemini-2.5-flash")

    resp = client.post("/api/v1/auth/auto-login")
    assert resp.status_code == 200
    headers = {"Authorization": f"Bearer {resp.json()['access_token']}"}

    project_resp = client.post(
        "/api/v1/projects",
        headers=headers,
        json={"name": "Live Completion", "description": "live test"},
    )
    assert project_resp.status_code == 200
    project_id = project_resp.json()["id"]

    with patch.dict(
        os.environ,
        {
            "CHIPVERIFY_COMPLETION_PROVIDER": provider,
            "CHIPVERIFY_COMPLETION_MODEL": model,
            "CHIPVERIFY_COMPLETION_ENABLED": "true",
        },
    ):
        completion_resp = client.post(
            f"/api/v1/projects/{project_id}/completions",
            headers=headers,
            json={
                "language": "systemverilog",
                "segments": {
                    "prefix": (
                        "module counter (\n"
                        "  input logic clk,\n"
                        "  output logic [7:0] count\n"
                        ");\n"
                        "  always_ff @(posedge clk) count <= "
                    ),
                    "suffix": ";\nendmodule\n",
                    "filepath": "counter.sv",
                },
                "debug_options": {"disable_rag": True},
            },
        )

    assert completion_resp.status_code == 200, completion_resp.text
    body = completion_resp.json()
    text = body["choices"][0]["text"]
    assert body["id"].startswith("cmpl-")
    assert text.strip(), f"Expected non-empty completion, got {text!r}"
    assert "```" not in text
    print(f"LIVE completion ({provider}/{model}): {text!r}")
