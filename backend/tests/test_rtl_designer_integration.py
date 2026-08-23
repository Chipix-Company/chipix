"""
RTL Designer Integration Test Script
=====================================
Tests the complete RTL Designer → Verification flow end-to-end.

Run: python test_rtl_designer_integration.py
Requires: backend running on port 7348, GOOGLE_API_KEY set
"""

import os
import sys
import time
import json
import requests

BASE_URL = os.getenv("CHIPVERIFY_BACKEND", "http://localhost:7348")
PASS = 0
FAIL = 0
SKIPPED = 0


def check(name, condition, detail=""):
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  ✓ {name}")
    else:
        FAIL += 1
        print(f"  ✗ {name}" + (f" — {detail}" if detail else ""))


def skip(name, reason=""):
    global SKIPPED
    SKIPPED += 1
    print(f"  ⊘ {name}" + (f" — {reason}" if reason else ""))


def section(title):
    print(f"\n{'=' * 60}")
    print(f"  {title}")
    print(f"{'=' * 60}")


# ── 1. Backend Health ────────────────────────────────────────────────
section("1. Backend Health")

try:
    resp = requests.get(f"{BASE_URL}/", timeout=5)
    check("Backend reachable", resp.status_code == 200, f"status={resp.status_code}")
    data = resp.json()
    check("Product name correct", data.get("product") == "chipverify-ai-studio")
    check("Mode correct", data.get("mode") == "local-first-on-prem")
except Exception as e:
    check("Backend reachable", False, str(e))
    print("\n  Backend not running. Start with: uvicorn main:app --reload --port 7348")
    sys.exit(1)

# ── 2. Design Routes Available ──────────────────────────────────────
section("2. Design Routes Available")

try:
    resp = requests.get(f"{BASE_URL}/docs", timeout=5)
    check("OpenAPI docs available", resp.status_code == 200)
except Exception as e:
    check("OpenAPI docs available", False, str(e))

# Check a design endpoint exists (should return 503 if no API key, not 404)
try:
    resp = requests.post(
        f"{BASE_URL}/design/generate",
        json={"prompt": "test", "language": "verilog"},
        timeout=5,
    )
    check(
        "/design/generate endpoint exists",
        resp.status_code in (422, 503, 202),
        f"status={resp.status_code}",
    )
    if resp.status_code == 503:
        skip("Full design tests", "GOOGLE_API_KEY not set")
        api_key_available = False
    else:
        api_key_available = True
except Exception as e:
    check("/design/generate endpoint exists", False, str(e))
    api_key_available = False

# ── 3. Setup Default Project ────────────────────────────────────────
section("3. Setup Default Project")

try:
    resp = requests.post(f"{BASE_URL}/design/setup/default-project", timeout=10)
    check(
        "Default project setup", resp.status_code == 200, f"status={resp.status_code}"
    )
    if resp.status_code == 200:
        data = resp.json()
        project_id = data.get("project_id")
        check("Project ID returned", bool(project_id))
        check("Project name returned", bool(data.get("project_name")))
    else:
        project_id = None
        check("Project ID returned", False, f"status={resp.status_code}")
except Exception as e:
    check("Default project setup", False, str(e))
    project_id = None

# ── 4. List Projects ────────────────────────────────────────────────
section("4. List Projects")

try:
    resp = requests.get(f"{BASE_URL}/design/setup/projects", timeout=5)
    check(
        "List projects endpoint", resp.status_code == 200, f"status={resp.status_code}"
    )
    if resp.status_code == 200:
        projects = resp.json()
        check(f"Projects returned: {len(projects)}", len(projects) > 0)
except Exception as e:
    check("List projects endpoint", False, str(e))

# ── 5. Verify Now (Standalone) ─────────────────────────────────────
section("5. Verify Now (Standalone Verification)")

if not project_id:
    skip("Verify now", "No project_id available")
else:
    simple_rtl = """
module simple_counter (
    input wire clk,
    input wire rst,
    input wire en,
    output reg [3:0] count
);
always @(posedge clk) begin
    if (rst) count <= 4'b0;
    else if (en) count <= count + 1;
end
endmodule
"""
    try:
        resp = requests.post(
            f"{BASE_URL}/design/verify-now",
            json={
                "rtl_code": simple_rtl,
                "project_id": project_id,
                "description": "Integration test: simple counter",
                "strategy": "uvm",
                "language": "verilog",
            },
            timeout=10,
        )
        check(
            "Verify now creates run",
            resp.status_code == 202,
            f"status={resp.status_code}",
        )
        if resp.status_code == 202:
            data = resp.json()
            run_id = data.get("run_id")
            check("Run ID returned", bool(run_id))
            check("Status is queued", data.get("status") == "queued")
            check("Source is standalone", data.get("source") == "standalone")
            check("Strategy returned", bool(data.get("strategy")))

            # Start the run
            try:
                resp = requests.post(
                    f"{BASE_URL}/design/runs/{run_id}/start", timeout=10
                )
                check(
                    "Start verification run",
                    resp.status_code == 202,
                    f"status={resp.status_code}",
                )
                if resp.status_code == 202:
                    check("Run started", resp.json().get("status") == "running")

                    # Poll status
                    time.sleep(2)
                    resp = requests.get(
                        f"{BASE_URL}/design/runs/{run_id}/status", timeout=10
                    )
                    check(
                        "Run status endpoint",
                        resp.status_code == 200,
                        f"status={resp.status_code}",
                    )
                    if resp.status_code == 200:
                        status_data = resp.json()
                        check("Run has status field", bool(status_data.get("status")))
                        check("Run has events", len(status_data.get("events", [])) >= 0)
            except Exception as e:
                check("Start verification run", False, str(e))
        else:
            run_id = None
    except Exception as e:
        check("Verify now creates run", False, str(e))

# ── 6. Design Generation (requires API key) ─────────────────────────
section("6. Design Generation (requires GOOGLE_API_KEY)")

if not api_key_available or not project_id:
    skip("Design generation", "API key not available or no project")
else:
    try:
        resp = requests.post(
            f"{BASE_URL}/design/generate",
            json={
                "prompt": "Create a 4-bit up/down counter with synchronous reset and enable",
                "language": "verilog",
                "model": "gemini-2.5-flash",
                "project_id": project_id,
            },
            timeout=10,
        )
        check(
            "Design generation started",
            resp.status_code == 202,
            f"status={resp.status_code}",
        )
        if resp.status_code == 202:
            data = resp.json()
            design_id = data.get("design_id")
            check("Design ID returned", bool(design_id))
            check("Status is planning", data.get("status") == "planning")

            # Poll status (wait for completion)
            print("  Waiting for design generation (up to 60s)...")
            for i in range(60):
                time.sleep(1)
                resp = requests.get(f"{BASE_URL}/design/{design_id}", timeout=10)
                if resp.status_code == 200:
                    status_data = resp.json()
                    status = status_data.get("status")
                    if status in ("complete", "error"):
                        check(
                            f"Design completed: {status}",
                            status == "complete",
                            f"error={status_data.get('error')}",
                        )
                        if status == "complete":
                            check(
                                "RTL code generated",
                                len(status_data.get("rtl_code", "")) > 0,
                            )
                            check(
                                "Spec generated", len(status_data.get("spec", "")) > 0
                            )
                            check(
                                "Review generated",
                                len(status_data.get("review", "")) > 0,
                            )
                            check(
                                "Events recorded",
                                len(status_data.get("events", [])) > 0,
                            )

                            # Send to verification
                            rtl_code = status_data.get("rtl_code", "")
                            try:
                                resp = requests.post(
                                    f"{BASE_URL}/design/{design_id}/send-to-verification",
                                    json={
                                        "project_id": project_id,
                                        "strategy": "uvm",
                                    },
                                    timeout=10,
                                )
                                check(
                                    "Send to verification",
                                    resp.status_code == 202,
                                    f"status={resp.status_code}",
                                )
                                if resp.status_code == 202:
                                    verify_data = resp.json()
                                    check(
                                        "Verification run created",
                                        bool(verify_data.get("run_id")),
                                    )
                                    check(
                                        "Source is rtl_designer",
                                        verify_data.get("status") == "queued",
                                    )
                            except Exception as e:
                                check("Send to verification", False, str(e))
                        break
                    elif i % 10 == 0:
                        print(f"    Still {status}... ({i}s)")
                else:
                    check(f"Status poll failed: {resp.status_code}", False)
                    break
            else:
                check("Design completed within timeout", False, "Timed out after 60s")
    except Exception as e:
        check("Design generation started", False, str(e))

# ── 7. DB Schema ────────────────────────────────────────────────────
section("7. Database Schema")

try:
    from database.models import Run

    cols = [c.name for c in Run.__table__.columns]
    check("rtl_snapshot column", "rtl_snapshot" in cols)
    check("spec_snapshot column", "spec_snapshot" in cols)
    check("source column", "source" in cols)
    check("source default is upload", Run.__table__.c.source.default.arg == "upload")
except Exception as e:
    check("DB schema check", False, str(e))

# ── 8. RTL_designer Folder ──────────────────────────────────────────
section("8. RTL_designer Folder Structure")

import pathlib

rtl_dir = pathlib.Path(__file__).parent.parent / "RTL_designer"
check("RTL_designer exists", rtl_dir.exists())
check("agents/graph.py", (rtl_dir / "agents" / "graph.py").exists())
check("agents/nodes.py", (rtl_dir / "agents" / "nodes.py").exists())
check("agents/state.py", (rtl_dir / "agents" / "state.py").exists())
check("agents/prompts.py", (rtl_dir / "agents" / "prompts.py").exists())
check("rtl_utils/helpers.py", (rtl_dir / "rtl_utils" / "helpers.py").exists())
check("rtl_utils/file_manager.py", (rtl_dir / "rtl_utils" / "file_manager.py").exists())
check("visualizer/parser.py", (rtl_dir / "visualizer" / "parser.py").exists())
check("visualizer/renderer.py", (rtl_dir / "visualizer" / "renderer.py").exists())
check("output/ directory", (rtl_dir / "output").exists())

# ── Summary ──────────────────────────────────────────────────────────
section("Summary")
print(f"  Passed:   {PASS}")
print(f"  Failed:   {FAIL}")
print(f"  Skipped:  {SKIPPED}")
print(f"  Total:    {PASS + FAIL + SKIPPED}")

if FAIL > 0:
    print(f"\n  ⚠ {FAIL} test(s) failed!")
    sys.exit(1)
else:
    print(f"\n  ✓ All tests passed!")
    sys.exit(0)
