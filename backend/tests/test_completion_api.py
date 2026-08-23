"""Integration tests for project-scoped completion API."""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

os.environ["CHIPVERIFY_SECRET_KEY"] = "test-completion-api-secret"
TEST_DB_PATH = Path(__file__).resolve().parent / "test_completion_api.db"
os.environ["DATABASE_URL"] = f"sqlite:///{TEST_DB_PATH.as_posix()}"
BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

_main_spec = importlib.util.spec_from_file_location(
    "chipverify_backend_main_completion", BACKEND_ROOT / "main.py"
)
_main_module = importlib.util.module_from_spec(_main_spec)
assert _main_spec and _main_spec.loader
_main_spec.loader.exec_module(_main_module)
app = _main_module.app

from routes import api as api_routes  # noqa: E402
from database.database import Base, engine  # noqa: E402
from llm_provider import TokenUsage  # noqa: E402


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


def _auth_headers(client: TestClient):
    resp = client.post("/api/v1/auth/auto-login")
    assert resp.status_code == 200, resp.text
    token = resp.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def _create_project(client: TestClient, headers: dict):
    resp = client.post(
        "/api/v1/projects",
        headers=headers,
        json={"name": "Completion Project", "description": "completion tests"},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_completion_requires_auth(client):
    resp = client.post(
        "/api/v1/projects/fake/completions",
        json={"segments": {"prefix": "wire ", "suffix": ""}},
    )
    assert resp.status_code in {401, 403, 404}


@patch("llm_provider.complete_fim_sync")
def test_completion_success(mock_fim, client):
    mock_fim.return_value = (
        "clk;",
        TokenUsage(
            input_tokens=10,
            output_tokens=3,
            total_tokens=13,
            provider="nim",
            model="test-model",
        ),
    )

    headers = _auth_headers(client)
    project = _create_project(client, headers)

    resp = client.post(
        f"/api/v1/projects/{project['id']}/completions",
        headers=headers,
        json={
            "language": "systemverilog",
            "segments": {
                "prefix": "wire ",
                "suffix": "\nendmodule",
                "filepath": "top.sv",
            },
        },
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["choices"][0]["text"] == "clk;"
    assert body["id"].startswith("cmpl-")


def test_completion_empty_prompt_rejected(client):
    headers = _auth_headers(client)
    project = _create_project(client, headers)
    resp = client.post(
        f"/api/v1/projects/{project['id']}/completions",
        headers=headers,
        json={"segments": {"prefix": "", "suffix": ""}},
    )
    assert resp.status_code == 400
