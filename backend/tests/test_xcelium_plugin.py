import os
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from simulator_plugins.coverage_reader import parse_coverage_report
from simulator_plugins.log_pipeline import analyze_large_logs, cluster_diagnostics
from simulator_plugins.repair_loop import apply_generated_repairs
from simulator_plugins.sandbox import create_run_sandbox, verify_rtl_unchanged
from simulator_plugins.spec_grounded_classifier import build_evidence_bundles
from simulator_plugins.vcd_analyzer import WaveformAnalyzer
from simulator_plugins.xcelium import XceliumPlugin
from simulator_plugins.xcelium_runner import XceliumRunner
from simulator_plugins.base import SimulatorRunRequest, ToolDetectionResult
from services.verification.uvm_log_parser import parse_uvm_logs


CADENCE_LOG = """
xmvlog: *E,NOPBIND (apb_sequencer.sv,13|13): Package apb_pkg could not be bound.
xmvlog: *E,NOIPRT (apb_sequencer.sv,36|15): Unrecognized declaration 'apb_agent_config' of unknown type.
xrun: *E,VLGERR: An error occurred during parsing.
"""


def test_xcelium_detection_reports_linux_tool(monkeypatch):
    monkeypatch.setattr("simulator_plugins.xcelium.platform.system", lambda: "Linux")
    monkeypatch.setattr("simulator_plugins.xcelium.shutil.which", lambda name: "/cadence/bin/xrun")

    def fake_run(command, **kwargs):
        if "-version" in command:
            return SimpleNamespace(returncode=0, stdout="TOOL: xrun(64) 25.03\n", stderr="")
        return SimpleNamespace(returncode=0, stdout="uvm help\n", stderr="")

    monkeypatch.setattr("simulator_plugins.xcelium.subprocess.run", fake_run)

    result = XceliumPlugin().detect()

    assert result.available is True
    assert result.path == "/cadence/bin/xrun"
    assert "xrun" in result.version
    assert result.uvm_available is True


def test_xcelium_syntax_check_parses_cadence_errors(monkeypatch, tmp_path):
    plugin = XceliumPlugin()
    monkeypatch.setattr(
        plugin, "detect",
        lambda: ToolDetectionResult(name="xcelium", available=True, path="xrun"),
    )
    src = tmp_path / "bad.sv"
    src.write_text("module bad; assign x = ;\nendmodule\n", encoding="utf-8")

    def fake_run(command, **kwargs):
        assert "-compile" in command
        return SimpleNamespace(
            returncode=1,
            stdout="",
            stderr=(
                "xmvlog: *E,NOIPRT (bad.sv,1|18): syntax error near ';'.\n"
                "xrun: *E,VLGERR: An error occurred during parsing.\n"
            ),
        )

    monkeypatch.setattr("simulator_plugins.xcelium.subprocess.run", fake_run)
    result = plugin.check_syntax(src)
    assert result["status"] == "failed"
    assert result["error_count"] >= 1
    assert any(d["code"] == "NOIPRT" for d in result["diagnostics"])


def test_xcelium_syntax_check_reports_unavailable(monkeypatch, tmp_path):
    plugin = XceliumPlugin()
    monkeypatch.setattr(
        plugin, "detect",
        lambda: ToolDetectionResult(name="xcelium", available=False, guidance="install xrun"),
    )
    src = tmp_path / "x.sv"
    src.write_text("module x; endmodule\n", encoding="utf-8")
    result = plugin.check_syntax(src)
    assert result["status"] == "unavailable"
    assert result["available"] is False


def test_xcelium_detection_two_phase_status_fields(monkeypatch):
    monkeypatch.setattr("simulator_plugins.xcelium.platform.system", lambda: "Linux")
    monkeypatch.setattr("simulator_plugins.xcelium.shutil.which", lambda name: "/cadence/bin/xrun")
    monkeypatch.delenv("CDS_LIC_FILE", raising=False)
    monkeypatch.delenv("LM_LICENSE_FILE", raising=False)
    monkeypatch.setattr(
        "simulator_plugins.xcelium.subprocess.run",
        lambda command, **kwargs: SimpleNamespace(returncode=0, stdout="xrun 25.03\n", stderr=""),
    )
    result = XceliumPlugin().refresh()
    # Binary present + Linux but no license -> available, but not run_ready.
    assert result.available is True
    assert result.details["status"] == "no_license"
    assert result.details["run_ready"] is False


def test_cadence_setup_imports_path_and_license(monkeypatch, tmp_path):
    """CHIPVERIFY_CADENCE_SETUP must source the lab script and import its PATH /
    license vars while leaving the backend's own Python runtime vars alone."""
    from simulator_plugins import env_setup

    script = tmp_path / "cadence_setup.sh"
    script.write_text("setenv PATH /cadence/bin:$PATH\n", encoding="utf-8")
    monkeypatch.setenv(env_setup.SETUP_ENV_VAR, str(script))
    monkeypatch.setenv("PYTHONPATH", "/backend/keep")
    monkeypatch.delenv("CDS_LIC_FILE", raising=False)

    dump = "\0".join(
        [
            "PATH=/cadence/bin:/usr/bin",
            "CDS_LIC_FILE=5280@lic.edu",
            "PYTHONPATH=/should/not/clobber",
            "SITE_ONLY=1",
        ]
    )
    monkeypatch.setattr(
        "simulator_plugins.env_setup.subprocess.run",
        lambda *a, **k: SimpleNamespace(returncode=0, stdout=dump, stderr=""),
    )

    env_setup.reset_cadence_env_cache()
    applied = env_setup.ensure_cadence_env()

    assert "PATH" in applied
    assert os.environ["PATH"] == "/cadence/bin:/usr/bin"
    assert os.environ["CDS_LIC_FILE"] == "5280@lic.edu"
    # Brand-new site var is imported; protected backend var is preserved.
    assert os.environ["SITE_ONLY"] == "1"
    assert os.environ["PYTHONPATH"] == "/backend/keep"
    env_setup.reset_cadence_env_cache()


def test_cadence_setup_makes_detection_find_xrun(monkeypatch, tmp_path):
    """After sourcing the setup script, detect() should see xrun + license that
    were not visible in the inherited environment."""
    import simulator_plugins.env_setup as env_setup

    script = tmp_path / "site_setup.sh"
    script.write_text("setenv CDS_LIC_FILE 5280@lic.edu\n", encoding="utf-8")
    monkeypatch.setenv(env_setup.SETUP_ENV_VAR, str(script))
    monkeypatch.delenv("CDS_LIC_FILE", raising=False)
    monkeypatch.delenv("LM_LICENSE_FILE", raising=False)
    monkeypatch.delenv("CHIPVERIFY_XCELIUM_BIN", raising=False)

    dump = "\0".join(["PATH=/cadence/bin:/usr/bin", "CDS_LIC_FILE=5280@lic.edu"])

    # env_setup and xcelium share the same ``subprocess`` module, so a single
    # dispatching mock serves both: the env-dump shell call vs. ``xrun -version``.
    def fake_run(command, **kwargs):
        joined = " ".join(command) if isinstance(command, list) else str(command)
        if "env -0" in joined:
            return SimpleNamespace(returncode=0, stdout=dump, stderr="")
        return SimpleNamespace(returncode=0, stdout="xrun 25.03\n", stderr="")

    monkeypatch.setattr("simulator_plugins.xcelium.subprocess.run", fake_run)
    monkeypatch.setattr("simulator_plugins.xcelium.platform.system", lambda: "Linux")
    monkeypatch.setattr("simulator_plugins.xcelium.shutil.which", lambda name: "/cadence/bin/xrun")

    env_setup.reset_cadence_env_cache()
    result = XceliumPlugin().refresh()

    assert result.details["status"] == "ready"
    assert result.details["run_ready"] is True
    assert "CDS_LIC_FILE" in result.details["setup_applied"]
    env_setup.reset_cadence_env_cache()


def test_manual_config_persists_applies_and_clears(monkeypatch, tmp_path):
    """A manual override must persist to disk, apply to the environment, and on
    clear revert to the launch-time default rather than wiping it."""
    import simulator_plugins.cadence_config as cc

    cfg_path = tmp_path / "cadence_config.json"
    monkeypatch.setenv(cc.CONFIG_PATH_ENV, str(cfg_path))
    # Launch default: backend was started pointing at /launch/xrun.
    monkeypatch.setenv("CHIPVERIFY_XCELIUM_BIN", "/launch/xrun")
    monkeypatch.setitem(cc._ENV_DEFAULTS, "CHIPVERIFY_XCELIUM_BIN", "/launch/xrun")

    manual_xrun = tmp_path / "xrun"
    manual_xrun.write_text("#!/bin/sh\n", encoding="utf-8")

    config = cc.set_cadence_config(xrun_bin=str(manual_xrun), license="5280@lic.edu")
    assert config["xrun_bin"] == str(manual_xrun)
    assert cfg_path.is_file()
    assert cc.load_cadence_config()["license"] == "5280@lic.edu"
    # Manual override wins over the launch default.
    assert os.environ["CHIPVERIFY_XCELIUM_BIN"] == str(manual_xrun)
    assert os.environ["CDS_LIC_FILE"] == "5280@lic.edu"

    # Clearing one field reverts that var to the launch default, keeps the rest.
    cc.set_cadence_config(xrun_bin="")
    assert os.environ["CHIPVERIFY_XCELIUM_BIN"] == "/launch/xrun"
    assert "xrun_bin" not in cc.load_cadence_config()
    assert os.environ["CDS_LIC_FILE"] == "5280@lic.edu"


def test_manual_config_rejects_missing_binary(monkeypatch, tmp_path):
    import simulator_plugins.cadence_config as cc

    monkeypatch.setenv(cc.CONFIG_PATH_ENV, str(tmp_path / "cadence_config.json"))
    with pytest.raises(cc.CadenceConfigError):
        cc.set_cadence_config(xrun_bin=str(tmp_path / "does_not_exist_xrun"))


def test_manual_config_makes_detection_ready(monkeypatch, tmp_path):
    """End-to-end: pointing detection at xrun + license via the manual override
    should flip status to ready without any launch-time env."""
    import simulator_plugins.cadence_config as cc
    from simulator_plugins.xcelium import XceliumPlugin

    monkeypatch.setenv(cc.CONFIG_PATH_ENV, str(tmp_path / "cadence_config.json"))
    monkeypatch.delenv("CHIPVERIFY_XCELIUM_BIN", raising=False)
    monkeypatch.delenv("CDS_LIC_FILE", raising=False)
    monkeypatch.delenv("LM_LICENSE_FILE", raising=False)
    monkeypatch.delenv("CHIPVERIFY_CADENCE_SETUP", raising=False)
    for field in ("xrun_bin", "license"):
        monkeypatch.setitem(cc._ENV_DEFAULTS, cc._FIELD_TO_ENV[field], None)

    manual_xrun = tmp_path / "xrun"
    manual_xrun.write_text("#!/bin/sh\n", encoding="utf-8")

    monkeypatch.setattr("simulator_plugins.xcelium.platform.system", lambda: "Linux")
    monkeypatch.setattr(
        "simulator_plugins.xcelium.subprocess.run",
        lambda command, **kwargs: SimpleNamespace(returncode=0, stdout="xrun 25.03\n", stderr=""),
    )

    cc.set_cadence_config(xrun_bin=str(manual_xrun), license="5280@lic.edu")
    result = XceliumPlugin().refresh()

    assert result.path == str(manual_xrun)
    assert result.details["status"] == "ready"
    assert result.details["run_ready"] is True
    cc.clear_cadence_config()


def test_sandbox_copies_generated_and_links_rtl_safely(tmp_path):
    generated = tmp_path / "generated_src"
    rtl = tmp_path / "rtl"
    generated.mkdir()
    rtl.mkdir()
    pkg = generated / "demo_pkg.sv"
    tb = generated / "top_tb.sv"
    dut = rtl / "demo.sv"
    pkg.write_text("package demo_pkg; endpackage\n", encoding="utf-8")
    tb.write_text("module top_tb; endmodule\n", encoding="utf-8")
    dut.write_text("module demo; endmodule\n", encoding="utf-8")

    manifest = create_run_sandbox(
        base_dir=tmp_path / "runs",
        generated_files=[pkg, tb],
        rtl_root=rtl,
        run_id="run_test",
    )

    assert Path(manifest.filelist).exists()
    filelist = Path(manifest.filelist).read_text(encoding="utf-8")
    assert "demo.sv" in filelist
    assert "demo_pkg.sv" in filelist
    assert verify_rtl_unchanged(manifest)["passed"] is True


def test_large_log_pipeline_filters_structures_and_clusters():
    result = analyze_large_logs([CADENCE_LOG], simulator="xcelium")

    assert result["analysis"]["status"] == "failed"
    assert "apb_pkg" in result["filtered_log"]
    assert result["clusters"]
    assert any(cluster["first"]["code"] == "NOPBIND" for cluster in result["clusters"])


def test_vcd_analyzer_extracts_snapshot_and_xz(tmp_path):
    vcd = tmp_path / "simulation.vcd"
    vcd.write_text(
        """
$timescale 1ns $end
$scope module top_tb $end
$var wire 1 ! clk $end
$var wire 8 " data $end
$upscope $end
$enddefinitions $end
#0
0!
bxxxxxxxx "
#10
1!
b10101010 "
""",
        encoding="utf-8",
    )

    analyzer = WaveformAnalyzer(vcd)

    assert analyzer.get_signals_at_time(10)["top_tb.clk"] == "1"
    assert analyzer.get_signals_at_time(10)["top_tb.data"] == "10101010"
    assert "top_tb.data" in analyzer.detect_x_z_signals(0)


def test_spec_grounded_classifier_marks_compile_failures_as_uvm_bug():
    pipeline = analyze_large_logs([CADENCE_LOG], simulator="xcelium")
    bundles = build_evidence_bundles(
        clusters=pipeline["clusters"],
        mental_model={
            "requirements": [
                {
                    "id": "REQ-001",
                    "text": "APB transactions shall compile through the generated sequencer.",
                }
            ]
        },
    )

    assert bundles
    assert bundles[0].verdict == "uvm_bug"
    assert bundles[0].confidence == 1.0


def test_generated_repair_loop_edits_only_sandbox_generated_files(tmp_path):
    generated = tmp_path / "run" / "generated"
    generated.mkdir(parents=True)
    pkg = generated / "cfs_aligner_core_pkg.sv"
    sequencer = generated / "apb_sequencer.sv"
    pkg.write_text(
        """
package cfs_aligner_core_pkg;
  import uvm_pkg::*;
  class apb_sequence_item extends uvm_sequence_item;
  endclass
endpackage
""",
        encoding="utf-8",
    )
    sequencer.write_text(
        """
`include "uvm_macros.svh"
import uvm_pkg::*;
import apb_pkg::*;
class apb_sequencer extends uvm_sequencer #(apb_sequence_item);
  `uvm_component_utils(apb_sequencer)
  apb_agent_config m_cfg;
  function new(string name = "apb_sequencer", uvm_component parent = null);
    super.new(name, parent);
  endfunction
endclass
""",
        encoding="utf-8",
    )

    analysis = parse_uvm_logs([CADENCE_LOG], simulator="xcelium")
    repair_round = apply_generated_repairs(
        analysis=analysis,
        run_dir=tmp_path / "run",
        round_index=1,
    )

    repaired = sequencer.read_text(encoding="utf-8")
    assert repair_round.status == "repaired"
    assert "import apb_pkg::*;" not in repaired
    assert "apb_agent_config" not in repaired
    assert (tmp_path / "run" / "results" / "repair_round_1.json").exists()


CLEAN_UVM_LOG = """
UVM_INFO @ 0: reporter [RNTST] Running test my_test...
UVM_INFO top_tb.sv(42) @ 100: uvm_test_top [BODY] sending transaction
--- UVM Report Summary ---
** Report counts by severity
UVM_INFO    :   12
UVM_WARNING :    0
UVM_ERROR   :    0
UVM_FATAL   :    0
$finish called from file "top_tb.sv"
"""

FAILING_UVM_LOG = """
UVM_INFO @ 0: reporter [RNTST] Running test my_test...
UVM_ERROR top_tb.sv(88) @ 250: uvm_test_top [SCBD] mismatch: exp=5 got=7
--- UVM Report Summary ---
** Report counts by severity
UVM_INFO    :   10
UVM_WARNING :    0
UVM_ERROR   :    1
UVM_FATAL   :    0
$finish called
"""

INCOMPLETE_UVM_LOG = """
UVM_INFO @ 0: reporter [RNTST] Running test my_test...
UVM_INFO top_tb.sv(42) @ 100: uvm_test_top [BODY] sending transaction
"""


def test_clean_uvm_summary_is_not_misreported_as_failed():
    analysis = parse_uvm_logs([CLEAN_UVM_LOG], simulator="xcelium")
    # "UVM_ERROR : 0" must NOT be treated as an error.
    assert analysis.status == "passed"
    assert analysis.blocking_errors == 0


def test_uvm_error_count_marks_run_failed():
    analysis = parse_uvm_logs([FAILING_UVM_LOG], simulator="xcelium")
    assert analysis.status == "failed"
    assert any(d.code == "UVM_ERROR" for d in analysis.diagnostics)
    assert any(rc.category == "uvm_test_failure" for rc in analysis.root_causes)


def test_incomplete_simulation_is_failed_even_without_errors():
    # Sim started (UVM_INFO) but never reached a completion marker -> crash/hang.
    analysis = parse_uvm_logs([INCOMPLETE_UVM_LOG], simulator="xcelium")
    assert analysis.status == "failed"
    assert any(d.code == "NOFINISH" for d in analysis.diagnostics)


def test_coverage_reader_parses_metrics_and_gaps(tmp_path):
    report = tmp_path / "coverage.txt"
    report.write_text(
        """
Functional Coverage: 87.5%
Statement Coverage: 91.0%
Branch Coverage: 82.2%
MISSING: addr bin 0xff
UNCOVERED: demo.sv line 42
""",
        encoding="utf-8",
    )

    parsed = parse_coverage_report(report)

    assert parsed["metrics"]["functional"] == 87.5
    assert parsed["metrics"]["statement"] == 91.0
    assert len(parsed["gaps"]) == 2


def test_xcelium_runner_mock_log_creates_result_files(tmp_path):
    run_dir = tmp_path / "run_mock"
    filelist = run_dir / "generated" / "xcelium_filelist.f"
    filelist.parent.mkdir(parents=True)
    filelist.write_text("", encoding="utf-8")

    result = XceliumRunner().run(
        SimulatorRunRequest(
            run_dir=run_dir,
            filelist=filelist,
            dry_run=True,
            mock_logs={"compile": CADENCE_LOG},
        )
    )

    assert result.status == "failed"
    assert (run_dir / "results" / "analysis.json").exists()
    assert (run_dir / "results" / "evidence_chain.json").exists()
    assert (run_dir / "results" / "verdict.json").exists()


def test_xcelium_runner_repairs_and_recompiles_generated_files(tmp_path, monkeypatch):
    run_dir = tmp_path / "run_loop"
    generated = run_dir / "generated"
    generated.mkdir(parents=True)
    filelist = generated / "xcelium_filelist.f"
    pkg = generated / "cfs_aligner_core_pkg.sv"
    sequencer = generated / "apb_sequencer.sv"
    pkg.write_text(
        """
package cfs_aligner_core_pkg;
  import uvm_pkg::*;
  class apb_sequence_item extends uvm_sequence_item;
  endclass
endpackage
""",
        encoding="utf-8",
    )
    sequencer.write_text(
        """
import uvm_pkg::*;
import apb_pkg::*;
class apb_sequencer extends uvm_sequencer #(apb_sequence_item);
  `uvm_component_utils(apb_sequencer)
  apb_agent_config m_cfg;
endclass
""",
        encoding="utf-8",
    )
    filelist.write_text(f"{pkg}\n{sequencer}\n", encoding="utf-8")

    class FakePlugin:
        def detect(self):
            return ToolDetectionResult(name="xcelium", available=True, path="xrun")

    calls = {"compile": 0}

    def fake_run(command, cwd, capture_output, text, timeout, check):
        cwd_path = Path(cwd)
        if "-compile" in command:
            calls["compile"] += 1
            log_name = command[command.index("-logfile") + 1]
            if calls["compile"] == 1:
                (cwd_path / log_name).write_text(CADENCE_LOG, encoding="utf-8")
                return SimpleNamespace(returncode=1, stdout="", stderr="")
            (cwd_path / log_name).write_text("xmvlog: compile clean\n", encoding="utf-8")
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        if "-elaborate" in command:
            (cwd_path / "elaborate.log").write_text("elaborate clean\n", encoding="utf-8")
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        (cwd_path / "simulation.log").write_text("UVM Report Summary\n** Report counts by severity\n", encoding="utf-8")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("simulator_plugins.xcelium_runner.subprocess.run", fake_run)

    progress_events = []
    result = XceliumRunner(plugin=FakePlugin()).run(
        SimulatorRunRequest(
            run_dir=run_dir,
            filelist=filelist,
            max_repair_rounds=2,
        ),
        progress_callback=progress_events.append,
    )

    repaired = sequencer.read_text(encoding="utf-8")
    assert calls["compile"] == 2
    assert result.repair_history
    assert result.status == "passed"
    assert "import apb_pkg::*;" not in repaired
    assert "apb_agent_config" not in repaired
    assert (run_dir / "results" / "feedback_memory.json").exists()
    phase_events = [event for event in progress_events if event["type"] in {"phase_start", "phase_complete"}]
    assert [event["type"] for event in phase_events] == [
        "phase_start",
        "phase_complete",
        "phase_start",
        "phase_complete",
        "phase_start",
        "phase_complete",
        "phase_start",
        "phase_complete",
    ]
    assert [event["phase"] for event in phase_events] == [
        "compile",
        "compile",
        "compile",
        "compile",
        "elaborate",
        "elaborate",
        "simulate",
        "simulate",
    ]
    assert any(event["type"] == "coverage_complete" for event in progress_events)
    assert any(event["type"] == "spec_audit_complete" for event in progress_events)
    assert any(event["type"] == "report_complete" for event in progress_events)
    assert (run_dir / "results" / "coverage_closure.json").exists()
    assert Path(result.report["path"]).exists()
