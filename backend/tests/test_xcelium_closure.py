import json
import sys
from pathlib import Path
from types import SimpleNamespace


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from services.verification.uvm_gen import generate_uvm_from_model
from simulator_plugins.base import SimulatorRunRequest, ToolDetectionResult
from simulator_plugins.coverage_reader import parse_coverage_report
from simulator_plugins.diagnostic_report import (
    build_findings,
    build_integrity,
    build_traceability,
    deterministic_seed,
    write_closure_markdown,
)
from simulator_plugins.xcelium_runner import XceliumRunner


def test_functional_and_code_coverage_closure_is_explicit():
    result = parse_coverage_report(
        """
Functional Coverage: 96.5%
Statement Coverage: 94.0%
Branch Coverage: 91.0%
Toggle Coverage: 89.0%
FSM Coverage: 98.0%
Assertion Coverage: 100.0%
UNCOVERED: dut.sv line 42
""",
        targets={"functional": 95, "code": 90},
    )

    assert result["functional"] == {
        "achieved": 96.5,
        "target": 95.0,
        "closed": True,
        "gaps": [],
    }
    assert result["code"]["achieved"] == 89.0
    assert result["code"]["closed"] is False
    assert result["closure"]["status"] == "open"


def test_generator_emits_regression_manifest_and_runtime_test_selection(tmp_path):
    model = {
        "design": {
            "top_module": "demo",
            "ports": [
                {"name": "clk", "direction": "input", "width": 1},
                {"name": "rst_n", "direction": "input", "width": 1},
                {"name": "a", "direction": "input", "width": 1},
                {"name": "y", "direction": "output", "width": 1},
            ],
            "clock_domains": [{"name": "clk", "associated_reset": "rst_n"}],
        },
        "requirements": [{"id": "REQ-001", "text": "y shall follow a"}],
        "verification": {
            "uvm_scenarios": [{
                "id": "SCN-001",
                "name": "follow_a",
                "description": "Check y follows a",
                "requirement_ids": ["REQ-001"],
            }],
            "uvm_scoreboard_checks": [{"id": "CHK-001", "requirement_ids": ["REQ-001"]}],
            "uvm_coverage_points": [{"id": "COV-001", "signal": "y", "requirement_ids": ["REQ-001"], "target_percentage": 95}],
        },
    }

    output = generate_uvm_from_model(model, str(tmp_path))
    manifest = json.loads(Path(output.regression_manifest).read_text(encoding="utf-8"))
    top = Path(output.top_file).read_text(encoding="utf-8")

    assert manifest["tests"][0]["test_class"] == "demo_base_test"
    scenario = next(test for test in manifest["tests"] if test["id"] == "SCN-001")
    assert scenario["test_class"] == "demo_follow_a_test"
    assert scenario["requirement_ids"] == ["REQ-001"]
    assert scenario["checkers"] == ["CHK-001"]
    assert scenario["coverpoints"] == ["COV-001"]
    assert manifest["coverage_targets"]["functional"] == 95.0
    assert "run_test();" in top
    assert 'run_test("demo_base_test")' not in top


def test_traceability_and_confirmed_finding_require_replay_spec_checker_and_rtl(tmp_path):
    rtl = tmp_path / "demo.sv"
    rtl.write_text("module demo; endmodule\n", encoding="utf-8")
    import hashlib
    checksum = hashlib.sha256(rtl.read_bytes()).hexdigest()
    tests = [{
        "id": "SCN-001",
        "test_class": "demo_follow_a_test",
        "requirement_ids": ["REQ-001"],
        "checkers": ["CHK-001"],
        "assertions": [],
        "coverpoints": ["COV-001"],
    }]
    replay = {"kind": "replay", "status": "failed", "seed": 77, "log": "replay.log"}
    executions = [{
        "kind": "primary",
        "test_id": "SCN-001",
        "test_class": "demo_follow_a_test",
        "requirement_ids": ["REQ-001"],
        "seed": 77,
        "status": "failed",
        "log": "simulation.log",
        "waveform": "failure.vcd",
        "replay": replay,
        "analysis": {"diagnostics": [{"phase": "simulation", "message": "y mismatch", "file": "demo.sv", "line": 1}]},
    }]
    mental_model = {"requirements": [{
        "id": "REQ-001",
        "text": "The output y shall follow input a",
        "priority": "high",
        "rtl_refs": [{"file": "demo.sv", "line": 1}],
    }]}
    coverage = parse_coverage_report("Functional Coverage: 100%\nCode Coverage: 95%", targets={"functional": 100, "code": 90})
    traceability = build_traceability(
        mental_model=mental_model,
        tests=tests,
        executions=executions,
        coverage=coverage,
        spec_pages=[{"page_num": 3, "source_line_start": 20, "text": "3.1 Behavior\nThe output y shall follow input a"}],
        spec_metadata={"artifact_id": "spec-1", "filename": "demo_spec.pdf"},
    )
    integrity = build_integrity(
        compile_passed=True,
        elaborate_passed=True,
        static_validation={"trusted": True},
        rtl_checksums={str(rtl): checksum},
        repair_history=[],
    )
    regression = {"executions": executions}
    findings = build_findings(regression=regression, integrity=integrity, traceability=traceability)

    assert traceability["rows"][0]["spec_ref"]["page"] == 3
    assert traceability["rows"][0]["spec_ref"]["line"] == 21
    assert findings[0]["tier"] == "Confirmed"
    report = write_closure_markdown(
        run_dir=tmp_path,
        run_id="run-1",
        simulator_version="xrun 25.03",
        regression={"total": 1, "passed": 0, "failed": 1},
        integrity=integrity,
        coverage=coverage,
        traceability=traceability,
        findings=findings,
        environment={"license_configured": True},
        warnings=[],
    )
    text = Path(report["path"]).read_text(encoding="utf-8")
    assert "# Design Verification Bug Report" in text
    assert "Functional and code coverage closure" in text
    assert "page 3, line 21" in text


def test_runner_executes_each_test_and_same_seed_replay(tmp_path, monkeypatch):
    class FakePlugin:
        def detect(self):
            return ToolDetectionResult(name="xcelium", available=True, path="xrun", version="xrun test")

    def fake_run(command, cwd, capture_output, text, timeout, check):
        cwd = Path(cwd)
        logfile = command[command.index("-logfile") + 1]
        if "-compile" in command or "-elaborate" in command:
            (cwd / logfile).write_text("clean\n", encoding="utf-8")
        elif "+UVM_TESTNAME=demo_fail_test" in command:
            (cwd / logfile).write_text(
                "UVM_ERROR demo_scoreboard.sv(10) @ 40: y mismatch\nUVM Report Summary\nUVM_ERROR : 1\nUVM_FATAL : 0\n",
                encoding="utf-8",
            )
        else:
            (cwd / logfile).write_text("UVM Report Summary\nUVM_ERROR : 0\nUVM_FATAL : 0\n", encoding="utf-8")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("simulator_plugins.xcelium_runner.subprocess.run", fake_run)
    monkeypatch.setattr("simulator_plugins.xcelium_runner.shutil.which", lambda _name: None)
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    filelist = run_dir / "files.f"
    filelist.write_text("", encoding="utf-8")
    tests = [
        {"id": "smoke", "test_class": "demo_base_test"},
        {"id": "fail", "test_class": "demo_fail_test", "requirement_ids": ["REQ-001"], "checkers": ["CHK-1"]},
    ]
    result = XceliumRunner(plugin=FakePlugin()).run(
        SimulatorRunRequest(run_dir=run_dir, filelist=filelist, regression_tests=tests, replay_failures=True),
    )

    primary = [item for item in result.regression["executions"] if item["kind"] == "primary"]
    failed = next(item for item in primary if item["test_id"] == "fail")
    assert result.regression["total"] == 2
    assert result.regression["passed"] == 1
    assert result.regression["failed"] == 1
    assert failed["replay"]["status"] == "failed"
    assert failed["seed"] == failed["replay"]["seed"]
    assert failed["seed"] == deterministic_seed("run", "fail")
