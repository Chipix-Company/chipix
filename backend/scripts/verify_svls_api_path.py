"""Verify lint through real project artifacts root + API."""
from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))
os.environ.setdefault("CHIPVERIFY_SECRET_KEY", "verify-api-path")
os.environ["DATABASE_URL"] = f"sqlite:///{(BACKEND_ROOT / 'tests' / 'test_svls_api_path.db').as_posix()}"

spec = importlib.util.spec_from_file_location("main", BACKEND_ROOT / "main.py")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

from fastapi.testclient import TestClient  # noqa: E402
from database.database import Base, engine, SessionLocal  # noqa: E402
from database.models import Project  # noqa: E402
from routes import api as api_routes  # noqa: E402
from services.sv_lint.config import project_artifacts_root  # noqa: E402
from services.sv_lint.service import get_svls_lint_service  # noqa: E402

Base.metadata.create_all(bind=engine)
app = mod.app
app.dependency_overrides[api_routes._check_license_validity] = lambda: {"valid": True}

BAD_SV = "module A;\nreg a;\nalways @* begin\n    a = b | c;\nend\nendmodule\n"

with TestClient(app) as client:
    token = client.post("/api/v1/auth/auto-login").json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    proj = client.post(
        "/api/v1/projects",
        headers=headers,
        json={"name": "API Path", "description": "test"},
    ).json()

    db = SessionLocal()
    project = db.query(Project).filter(Project.id == proj["id"]).first()
    root = project_artifacts_root(
        organization_id=project.organization_id,
        project_id=project.id,
    )
    db.close()

    direct = get_svls_lint_service().lint_source(
        filepath="bad.sv",
        content=BAD_SV,
        project_root=root,
    )
    api_resp = client.post(
        f"/api/v1/projects/{proj['id']}/lint",
        headers=headers,
        json={"filepath": "bad.sv", "content": BAD_SV},
    )

print("artifacts_root:", root)
print("direct_diagnostics:", len(direct.diagnostics))
print("api_status:", api_resp.status_code)
print("api_diagnostics:", len(api_resp.json().get("diagnostics", [])))
print("agent_blocks:", direct.has_agent_blocking_issues)
