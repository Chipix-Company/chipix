"""One-shot live verification for svls integration."""
from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

os.environ.setdefault("CHIPVERIFY_SECRET_KEY", "verify-svls-live")
db = BACKEND_ROOT / "tests" / "test_svls_live_verify.db"
os.environ["DATABASE_URL"] = f"sqlite:///{db.as_posix()}"

spec = importlib.util.spec_from_file_location("chipverify_main", BACKEND_ROOT / "main.py")
mod = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(mod)

from fastapi.testclient import TestClient  # noqa: E402
from database.database import Base, engine  # noqa: E402
from routes import api as api_routes  # noqa: E402
from services.sv_lint.service import get_svls_lint_service  # noqa: E402
from services.sv_lint.toolchain import resolve_svls_binary, svls_available  # noqa: E402

Base.metadata.create_all(bind=engine)
app = mod.app
app.dependency_overrides[api_routes._check_license_validity] = lambda: {"valid": True}

BAD_SV = "module A(input logic clk)\nalways_ff @(posedge clk) begin\nend\nendmodule\n"

with TestClient(app) as client:
    token = client.post("/api/v1/auth/auto-login").json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    proj = client.post(
        "/api/v1/projects",
        headers=headers,
        json={"name": "SVLS Verify", "description": "live"},
    ).json()
    toolchain = client.get("/api/v1/eda/toolchain/status", headers=headers).json()
    lint_resp = client.post(
        f"/api/v1/projects/{proj['id']}/lint",
        headers=headers,
        json={"filepath": "bad.sv", "content": BAD_SV},
    )

service_result = get_svls_lint_service().lint_source(
    filepath="bad.sv",
    content=BAD_SV,
    project_root=BACKEND_ROOT / "outputs" / "verify_svls_live",
)

print("=== SVLS Live Verification ===")
print(f"svls_binary: {resolve_svls_binary()}")
print(f"svls_available: {svls_available()}")
print(f"toolchain_svls: {toolchain.get('svls')}")
print(f"lint_api_status: {lint_resp.status_code}")
print(f"lint_api_body: {lint_resp.json() if lint_resp.headers.get('content-type','').startswith('application/json') else lint_resp.text}")
print(f"service_result: {service_result.to_dict()}")
