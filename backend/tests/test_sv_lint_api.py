"""REST API tests for project lint endpoint."""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

os.environ["CHIPVERIFY_SECRET_KEY"] = "test-sv-lint-api-secret"
TEST_DB_PATH = Path(__file__).resolve().parent / "test_sv_lint_api.db"
os.environ["DATABASE_URL"] = f"sqlite:///{TEST_DB_PATH.as_posix()}"
BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

_main_spec = importlib.util.spec_from_file_location(
    "chipverify_backend_main_sv_lint", BACKEND_ROOT / "main.py"
)
_main_module = importlib.util.module_from_spec(_main_spec)
assert _main_spec and _main_spec.loader
_main_spec.loader.exec_module(_main_module)
app = _main_module.app

from routes import api as api_routes  # noqa: E402
from database.database import Base, engine  # noqa: E402
from services.sv_lint.schemas import SvDiagnostic, SvLintResult  # noqa: E402


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
        json={"name": "Lint Project", "description": "sv lint tests"},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_lint_requires_auth(client):
    resp = client.post(
        "/api/v1/projects/proj-1/lint",
        json={"filepath": "top.sv", "content": "module m; endmodule"},
    )
    assert resp.status_code in {401, 403, 404}


def test_lint_returns_503_when_svls_missing(client):
    headers = _auth_headers(client)
    project = _create_project(client, headers)
    with patch("routes.sv_lint.svls_available", return_value=False):
        resp = client.post(
            f"/api/v1/projects/{project['id']}/lint",
            headers=headers,
            json={"filepath": "top.sv", "content": "module m; endmodule"},
        )
    assert resp.status_code == 503
    body = resp.json()
    assert body["detail"]["tool_available"] is False


def test_lint_returns_diagnostics(client):
    headers = _auth_headers(client)
    project = _create_project(client, headers)
    lint_result = SvLintResult(
        passed=False,
        tool_available=True,
        diagnostics=[
            SvDiagnostic(
                line=0,
                col=0,
                end_line=0,
                end_col=4,
                severity="warning",
                rule="wire_reg",
                message="use logic",
            )
        ],
    )
    with patch("routes.sv_lint.svls_available", return_value=True):
        with patch(
            "routes.sv_lint.get_svls_lint_service"
        ) as mock_service:
            mock_service.return_value.lint_source.return_value = lint_result
            resp = client.post(
                f"/api/v1/projects/{project['id']}/lint",
                headers=headers,
                json={"filepath": "top.sv", "content": "module m; reg a; endmodule"},
            )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["passed"] is False
    assert len(body["diagnostics"]) == 1
    assert body["diagnostics"][0]["rule"] == "wire_reg"


def test_lint_preserves_safe_relative_path_and_version(client):
    headers = _auth_headers(client)
    project = _create_project(client, headers)
    lint_result = SvLintResult(passed=True, tool_available=True)
    with patch("routes.sv_lint.svls_available", return_value=True):
        with patch("routes.sv_lint.get_svls_lint_service") as mock_service:
            mock_service.return_value.lint_source.return_value = lint_result
            resp = client.post(
                f"/api/v1/projects/{project['id']}/lint",
                headers=headers,
                json={
                    "filepath": "rtl/peripheral/top.sv",
                    "content": "module top; endmodule",
                    "version": 9,
                },
            )
    assert resp.status_code == 200
    kwargs = mock_service.return_value.lint_source.call_args.kwargs
    assert kwargs["filepath"] == "rtl/peripheral/top.sv"
    assert kwargs["version"] == 9
