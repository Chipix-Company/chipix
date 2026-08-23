"""Tests for the Cadence FIFO integration smoke fixture."""

from pathlib import Path
import sys

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from simulator_plugins.cadence_integration_fixture import (
    materialize_fifo_fixture,
    run_cadence_fifo_integration_test,
)


def _fake_xrun_command(phase: str, *, returncode: int = 0) -> dict:
    flag = "-compile"
    if phase == "elaborate":
        flag = "-elaborate"
    elif phase.startswith("simulate"):
        flag = "-R"
    return {
        "phase": phase,
        "command": ["/cadence/bin/xrun", flag, "-sv", "-uvm", "-f", "fifo_uvm.f"],
        "returncode": returncode,
        "log_path": "",
        "stdout": f"{phase} completed",
        "stderr": "",
        "timed_out": False,
    }


def _fake_runner_payload(
    *,
    phases,
    regression_status="passed",
    runner_status="passed",
    command_returncode=0,
):
    commands = [
        _fake_xrun_command(phase, returncode=command_returncode)
        for phase in phases
    ]
    return {
        "status": runner_status,
        "phases": phases,
        "regression": {"status": regression_status, "passed": 1 if regression_status == "passed" else 0},
        "commands": commands,
        "logs": {},
        "warnings": [],
        "analysis": {"status": "passed"},
    }


def test_materialize_fifo_fixture_writes_expected_files(tmp_path):
    paths = materialize_fifo_fixture(tmp_path)

    assert paths["fixture"] == "fifo"
    assert Path(paths["filelist"]).exists()
    assert Path(paths["rtl_file"]).exists()
    assert (tmp_path / "fifo_pkg.sv").exists()
    assert (tmp_path / "top_tb.sv").exists()

    filelist = Path(paths["filelist"]).read_text(encoding="utf-8")
    assert "fifo.sv" in filelist
    assert "fifo_pkg.sv" in filelist
    assert "top_tb.sv" in filelist


def test_integration_test_blocked_on_unsupported_platform(monkeypatch, tmp_path):
    class FakeDetection:
        def to_dict(self):
            return {
                "available": False,
                "details": {"status": "unsupported_platform"},
                "guidance": "Linux only",
            }

    class FakePlugin:
        def detect(self):
            return FakeDetection()

    monkeypatch.setattr(
        "simulator_plugins.xcelium.XceliumPlugin",
        FakePlugin,
    )

    result = run_cadence_fifo_integration_test(work_dir=tmp_path)
    assert result["status"] == "blocked"
    assert "Linux" in result["message"]


def test_integration_test_passes_with_mocked_cadence_pipeline(monkeypatch, tmp_path):
    class FakeDetection:
        def to_dict(self):
            return {
                "available": True,
                "path": "/cadence/bin/xrun",
                "details": {"status": "ready"},
            }

    class FakePlugin:
        def detect(self):
            return FakeDetection()

    monkeypatch.setattr("simulator_plugins.xcelium.XceliumPlugin", FakePlugin)
    monkeypatch.setattr(
        "simulator_plugins.cadence_integration_fixture._run_fifo_xcelium_pipeline",
        lambda fixture, root, timeout_seconds=180: _fake_runner_payload(
            phases=["compile_round_1", "elaborate", "simulate_default"],
        ),
    )

    result = run_cadence_fifo_integration_test(work_dir=tmp_path)
    assert result["status"] == "passed"
    assert result["compile_gate"]["method"] == "cadence_xrun_uvm_pipeline"
    assert result["run_summary"]["real_execution"] is True
    assert result["run_summary"]["xrun_invocations"] == 3
    assert len(result["run_summary"]["steps"]) == 3
    assert all(step["uses_xrun"] for step in result["run_summary"]["steps"])


def test_integration_test_fails_when_cadence_compile_fails(monkeypatch, tmp_path):
    class FakeDetection:
        def to_dict(self):
            return {
                "available": True,
                "path": "/cadence/bin/xrun",
                "details": {"status": "ready"},
            }

    class FakePlugin:
        def detect(self):
            return FakeDetection()

    monkeypatch.setattr("simulator_plugins.xcelium.XceliumPlugin", FakePlugin)
    monkeypatch.setattr(
        "simulator_plugins.cadence_integration_fixture._run_fifo_xcelium_pipeline",
        lambda fixture, root, timeout_seconds=180: _fake_runner_payload(phases=[]),
    )

    result = run_cadence_fifo_integration_test(work_dir=tmp_path)
    assert result["status"] == "failed"
    assert result["run_summary"]["real_execution"] is False
    assert "did not run" in result["message"].lower()


def test_integration_test_fails_when_xrun_command_fails(monkeypatch, tmp_path):
    class FakeDetection:
        def to_dict(self):
            return {
                "available": True,
                "path": "/cadence/bin/xrun",
                "details": {"status": "ready"},
            }

    class FakePlugin:
        def detect(self):
            return FakeDetection()

    monkeypatch.setattr("simulator_plugins.xcelium.XceliumPlugin", FakePlugin)
    monkeypatch.setattr(
        "simulator_plugins.cadence_integration_fixture._run_fifo_xcelium_pipeline",
        lambda fixture, root, timeout_seconds=180: _fake_runner_payload(
            phases=["compile_round_1", "elaborate", "simulate_default"],
            command_returncode=1,
        ),
    )

    result = run_cadence_fifo_integration_test(work_dir=tmp_path)
    assert result["status"] == "failed"
    assert result["run_summary"]["real_execution"] is False


def test_integration_test_fails_when_simulation_does_not_run(monkeypatch, tmp_path):
    class FakeDetection:
        def to_dict(self):
            return {
                "available": True,
                "path": "/cadence/bin/xrun",
                "details": {"status": "ready"},
            }

    class FakePlugin:
        def detect(self):
            return FakeDetection()

    monkeypatch.setattr("simulator_plugins.xcelium.XceliumPlugin", FakePlugin)
    monkeypatch.setattr(
        "simulator_plugins.cadence_integration_fixture._run_fifo_xcelium_pipeline",
        lambda fixture, root, timeout_seconds=180: _fake_runner_payload(
            phases=["compile_round_1", "elaborate"],
        ),
    )

    result = run_cadence_fifo_integration_test(work_dir=tmp_path)
    assert result["status"] == "failed"
    assert "simulation" in result["message"].lower()


def test_integration_test_fails_when_simulation_fails(monkeypatch, tmp_path):
    class FakeDetection:
        def to_dict(self):
            return {
                "available": True,
                "path": "/cadence/bin/xrun",
                "details": {"status": "ready"},
            }

    class FakePlugin:
        def detect(self):
            return FakeDetection()

    monkeypatch.setattr("simulator_plugins.xcelium.XceliumPlugin", FakePlugin)
    monkeypatch.setattr(
        "simulator_plugins.cadence_integration_fixture._run_fifo_xcelium_pipeline",
        lambda fixture, root, timeout_seconds=180: _fake_runner_payload(
            phases=["compile_round_1", "elaborate", "simulate_default"],
            regression_status="failed",
        ),
    )

    result = run_cadence_fifo_integration_test(work_dir=tmp_path)
    assert result["status"] == "failed"
    assert "verification" in result["message"].lower()


def test_materialize_uses_inline_fallback_when_benchmark_missing(monkeypatch, tmp_path):
    """Packaged builds do not ship benchmarks/; the fixture must still produce a
    compilable fifo.sv from the embedded fallback instead of raising (500)."""
    import simulator_plugins.cadence_integration_fixture as fx

    monkeypatch.setattr(fx, "FIFO_RTL", Path("/nonexistent/benchmarks/fifo/fifo.sv"))
    paths = fx.materialize_fifo_fixture(tmp_path)
    rtl = Path(paths["rtl_file"]).read_text(encoding="utf-8")
    assert "module fifo" in rtl
    assert (tmp_path / "fifo.sv").exists()


def test_integration_test_never_raises(monkeypatch, tmp_path):
    """Any failure in the impl must surface as a structured error, not a 500."""
    import simulator_plugins.cadence_integration_fixture as fx

    def boom(**_kwargs):
        raise RuntimeError("simulated failure")

    monkeypatch.setattr(fx, "_run_integration_test_impl", boom)
    result = fx.run_cadence_fifo_integration_test(work_dir=tmp_path)
    assert result["status"] == "error"
    assert "simulated failure" in result["message"]
