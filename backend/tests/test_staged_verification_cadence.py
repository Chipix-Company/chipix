import os
import sys
from pathlib import Path
from types import SimpleNamespace


BACKEND_ROOT = Path(__file__).resolve().parents[1]
ORIGINAL_CORE_ROOT = BACKEND_ROOT / "original_core"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))
if str(ORIGINAL_CORE_ROOT) not in sys.path:
    sys.path.insert(0, str(ORIGINAL_CORE_ROOT))

os.environ.setdefault("CHIPVERIFY_SECRET_KEY", "test-secret-key-for-staged-cadence")

from routes.staged_verification import _run_xcelium_after_uvm_validation
from simulator_plugins.base import ToolDetectionResult


class _FakePlugin:
    def __init__(self, detection):
        self.detection = detection

    def detect(self):
        return self.detection


class _FakeRunner:
    def __init__(self, status="passed"):
        self.status = status
        self.calls = []

    def run(self, request, **kwargs):
        self.calls.append((request, kwargs))
        progress = kwargs.get("progress_callback")
        if progress:
            progress({
                "type": "phase_start",
                "phase": "compile",
                "command": ["/cadence/bin/xrun", "-compile", "-f", str(request.filelist)],
                "message": "Xcelium compile round 1 started",
            })
            progress({
                "type": "phase_complete",
                "phase": "compile",
                "returncode": 0,
                "passed": True,
                "message": "Xcelium compile round 1 passed",
            })
        results_dir = request.run_dir / "results"
        results_dir.mkdir(parents=True, exist_ok=True)
        verdict = results_dir / "verdict.json"
        verdict.write_text('{"status": "passed"}\n', encoding="utf-8")
        return SimpleNamespace(
            to_dict=lambda: {
                "simulator": "xcelium",
                "run_id": request.run_dir.name,
                "status": self.status,
                "run_dir": str(request.run_dir),
                "logs": {},
                "analysis": {"status": self.status},
            }
        )


def _detection(status="ready", *, run_ready=True):
    return ToolDetectionResult(
        name="xcelium",
        available=status != "not_installed",
        path="/cadence/bin/xrun" if status != "not_installed" else "",
        version="xrun 25.03" if status != "not_installed" else "",
        uvm_available=True,
        platform_supported=True,
        details={
            "status": status,
            "license_ready": run_ready,
            "run_ready": run_ready,
        },
    )


def test_green_cadence_runs_after_uvm_validation(tmp_path):
    from services.verification.uvm_gen import (
        reset_uvm_progress_callback,
        set_uvm_progress_callback,
    )

    filelist = tmp_path / "uvm.f"
    filelist.write_text("top_tb.sv\n", encoding="utf-8")
    runner = _FakeRunner(status="passed")
    progress_events = []
    token = set_uvm_progress_callback(progress_events.append)
    try:
        result = _run_xcelium_after_uvm_validation(
            uvm_out=SimpleNamespace(filelist=str(filelist)),
            uvm_dir=tmp_path,
            validation={"trusted": True},
            compile_gate={"passed": True},
            mental_model={"requirements": []},
            rtl_files=[],
            plugin=_FakePlugin(_detection()),
            runner=runner,
        )
    finally:
        reset_uvm_progress_callback(token)

    assert result["status"] == "passed"
    assert result["attempted"] is True
    assert result["required"] is True
    assert result["detection"]["run_ready"] is True
    assert len(runner.calls) == 1
    request, _kwargs = runner.calls[0]
    assert request.filelist != Path(filelist)
    assert request.filelist.exists()
    assert request.filelist.parent.name == "generated"
    assert request.top_module == "top_tb"
    assert request.auto_repair_generated is True
    assert request.max_repair_rounds == 2
    assert request.replay_failures is True
    assert request.enforce_closure is True
    assert any(path.endswith("verdict.json") for path in result["artifact_paths"])
    event_types = [event["type"] for event in progress_events]
    assert event_types == [
        "cadence_detect",
        "cadence_ready",
        "cadence_regression_manifest",
        "cadence_sandbox_integrity",
        "cadence_start",
        "cadence_phase_start",
        "cadence_phase_complete",
        "cadence_complete",
    ]


def test_non_green_cadence_is_recorded_and_not_invoked(tmp_path):
    filelist = tmp_path / "uvm.f"
    filelist.write_text("top_tb.sv\n", encoding="utf-8")
    runner = _FakeRunner()

    result = _run_xcelium_after_uvm_validation(
        uvm_out=SimpleNamespace(filelist=str(filelist)),
        uvm_dir=tmp_path,
        validation={"trusted": True},
        compile_gate={"passed": True},
        mental_model={},
        rtl_files=[],
        plugin=_FakePlugin(_detection("no_license", run_ready=False)),
        runner=runner,
    )

    assert result["status"] == "skipped"
    assert result["attempted"] is False
    assert result["required"] is False
    assert result["detection"]["status"] == "no_license"
    assert "no license" in result["reason"].lower()
    assert runner.calls == []


def test_static_gate_failure_blocks_green_cadence(tmp_path):
    filelist = tmp_path / "uvm.f"
    filelist.write_text("top_tb.sv\n", encoding="utf-8")
    runner = _FakeRunner()

    result = _run_xcelium_after_uvm_validation(
        uvm_out=SimpleNamespace(filelist=str(filelist)),
        uvm_dir=tmp_path,
        validation={"trusted": False},
        compile_gate={"passed": True},
        mental_model={},
        rtl_files=[],
        plugin=_FakePlugin(_detection()),
        runner=runner,
    )

    assert result["status"] == "skipped"
    assert "validation" in result["reason"].lower()
    assert runner.calls == []
