"""Tests for Cadence wiring + trust decision in the UVM compile gate."""

from pathlib import Path
import sys

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from services.verification.compile_gate import (
    decide_uvm_trust,
    run_compile_gate,
)


UVM_SV = """package demo_pkg;
  import uvm_pkg::*;
  `include "uvm_macros.svh"
  class demo_test extends uvm_test;
    `uvm_component_utils(demo_test)
    function new(string name, uvm_component parent);
      super.new(name, parent);
    endfunction
  endclass
endpackage
"""


# ── decide_uvm_trust ────────────────────────────────────────────────────────

def test_static_validation_failure_without_simulator_is_validation_failed():
    decision = decide_uvm_trust(
        validation={"trusted": False},
        compile_gate={"passed": True, "method": "static+uvm_tool_skipped", "simulator": ""},
    )
    assert decision["status"] == "validation_failed"
    assert decision["trusted"] is False
    assert decision["authoritative_compile"] is False


def test_real_cadence_compile_pass_overrides_static_validator():
    """The exact bug: static validator flags errors, but a real passing Cadence
    UVM compile must make the run trusted (authoritative)."""
    decision = decide_uvm_trust(
        validation={"trusted": False, "errors": 7},
        compile_gate={"passed": True, "method": "cadence_xrun_uvm", "simulator": "xcelium"},
    )
    assert decision["status"] == "validated"
    assert decision["trusted"] is True
    assert decision["authoritative_compile"] is True


def test_real_cadence_compile_failure_is_compile_gate_failed():
    decision = decide_uvm_trust(
        validation={"trusted": True},
        compile_gate={"passed": False, "method": "cadence_xrun_uvm", "simulator": "xcelium"},
    )
    assert decision["status"] == "compile_gate_failed"
    assert decision["trusted"] is False
    # A real compile that ran is authoritative whether it passes OR fails — the
    # simulator's verdict decides, not the static validator.
    assert decision["authoritative_compile"] is True


def test_real_cadence_failure_overrides_static_validator_errors():
    """The reported bug: Cadence connected, static validator screams 7 false
    errors — but if the real compile FAILS we report compile_gate_failed with
    the real reason, and if it PASSES we report validated. The static validator
    must never be what fails (or passes) a Cadence-connected run."""
    failed = decide_uvm_trust(
        validation={"trusted": False, "errors": 7},
        compile_gate={"passed": False, "method": "cadence_xrun_uvm", "simulator": "xcelium"},
    )
    assert failed["status"] == "compile_gate_failed"
    assert failed["authoritative_compile"] is True

    passed = decide_uvm_trust(
        validation={"trusted": False, "errors": 7},
        compile_gate={"passed": True, "method": "cadence_xrun_uvm", "simulator": "xcelium"},
    )
    assert passed["status"] == "validated"
    assert passed["trusted"] is True


def test_static_only_clean_run_is_validated():
    decision = decide_uvm_trust(
        validation={"trusted": True},
        compile_gate={"passed": True, "method": "static+uvm_tool_skipped", "simulator": ""},
    )
    assert decision["status"] == "validated"
    assert decision["trusted"] is True


# ── run_compile_gate UVM branch ─────────────────────────────────────────────

def test_uvm_gate_uses_cadence_when_available(monkeypatch, tmp_path):
    src = tmp_path / "demo_pkg.sv"
    src.write_text(UVM_SV, encoding="utf-8")

    monkeypatch.setattr(
        "services.verification.compile_gate._run_cadence_uvm_compile",
        lambda sv_files, file_paths, top_module, timeout=240: {
            "passed": True,
            "simulator": "xcelium",
            "method": "cadence_xrun_uvm",
            "errors": [],
            "warnings": [],
        },
    )
    result = run_compile_gate(file_paths=[src], top_module="demo")
    assert result.passed is True
    assert result.simulator == "xcelium"
    assert result.method == "cadence_xrun_uvm"


def test_uvm_gate_falls_back_to_skip_without_cadence(monkeypatch, tmp_path):
    src = tmp_path / "demo_pkg.sv"
    src.write_text(UVM_SV, encoding="utf-8")

    monkeypatch.setattr(
        "services.verification.compile_gate._run_cadence_uvm_compile",
        lambda sv_files, file_paths, top_module, timeout=240: None,
    )
    result = run_compile_gate(file_paths=[src], top_module="demo")
    assert result.passed is True  # static skip never false-fails
    assert result.method == "static+uvm_tool_skipped"
    assert result.simulator == ""


def test_uvm_gate_reports_real_cadence_failure(monkeypatch, tmp_path):
    src = tmp_path / "demo_pkg.sv"
    src.write_text(UVM_SV, encoding="utf-8")

    monkeypatch.setattr(
        "services.verification.compile_gate._run_cadence_uvm_compile",
        lambda sv_files, file_paths, top_module, timeout=240: {
            "passed": False,
            "simulator": "xcelium",
            "method": "cadence_xrun_uvm",
            "errors": ["Cadence xrun UVM compile failed:\n*E,NOPBIND ..."],
            "warnings": [],
        },
    )
    result = run_compile_gate(file_paths=[src], top_module="demo")
    assert result.passed is False
    assert result.method == "cadence_xrun_uvm"
    assert any("NOPBIND" in e for e in result.errors)
