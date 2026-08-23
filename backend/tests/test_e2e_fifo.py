"""
End-to-End Test — FIFO Benchmark Verification Pipeline.

Tests the complete agentic verification loop on the fifo benchmark:
1. Mental model construction
2. UnitSim testbench generation
3. Formal SVA generation
4. UVM environment generation
5. Debug/diagnosis service
6. Coverage analysis

Uses the benchmark at backend/benchmarks/fifo/fifo.sv
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

import pytest

# Add backend to path
BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

# Runs full in-process pipelines (mental model, UnitSim, formal, UVM) using
# fake analyzers only; no external binaries required.
pytestmark = pytest.mark.integration


# ─── Helpers ─────────────────────────────────────────────────────────

FIFO_DIR = BACKEND / "benchmarks" / "fifo"
FIFO_SV = FIFO_DIR / "fifo.sv"
FIFO_SPEC = FIFO_DIR / "spec.txt"


def _read_fifo_rtl() -> str:
    return FIFO_SV.read_text(encoding="utf-8")


def _read_fifo_spec() -> str:
    if FIFO_SPEC.exists():
        return FIFO_SPEC.read_text(encoding="utf-8")
    return "Synchronous FIFO with configurable depth and width, full/empty flags."


def _fake_fifo_slang_analyzer_class():
    from services.mental_model.slang_analyzer import SlangAnalysisResult

    ports = [
        {"name": "clk", "direction": "input", "width": 1, "line": 2, "port_type": "logic", "bus_range": ""},
        {"name": "rst_n", "direction": "input", "width": 1, "line": 3, "port_type": "logic", "bus_range": ""},
        {"name": "wr_en", "direction": "input", "width": 1, "line": 4, "port_type": "logic", "bus_range": ""},
        {"name": "data_in", "direction": "input", "width": 8, "line": 5, "port_type": "logic", "bus_range": "[7:0]"},
        {"name": "rd_en", "direction": "input", "width": 1, "line": 6, "port_type": "logic", "bus_range": ""},
        {"name": "data_out", "direction": "output", "width": 8, "line": 7, "port_type": "logic", "bus_range": "[7:0]"},
        {"name": "full", "direction": "output", "width": 1, "line": 8, "port_type": "logic", "bus_range": ""},
        {"name": "empty", "direction": "output", "width": 1, "line": 9, "port_type": "logic", "bus_range": ""},
        {"name": "count", "direction": "output", "width": 4, "line": 10, "port_type": "logic", "bus_range": "[3:0]"},
    ]

    class FakeSlangAnalyzer:
        def analyze_project(self, root_path, rtl_files, *, include_dirs=None, defines=None, top_module=None):
            return SlangAnalysisResult(
                module_index={
                    "fifo": {
                        "name": "fifo",
                        "definition_kind": "Module",
                        "file": "fifo.sv",
                        "line_range": (1, 81),
                        "line_count": 81,
                        "ports": ports,
                        "parameters": [
                            {"name": "WIDTH", "default_value": "8"},
                            {"name": "DEPTH", "default_value": "8"},
                        ],
                        "instantiations": [],
                        "always_blocks": [],
                        "fsm_candidates": [],
                        "register_fields": [],
                        "existing_assertions": [],
                        "cdc_crossings": [],
                        "internal_symbols": ["wr_ptr", "rd_ptr", "fifo_count"],
                        "parser_engine": "slang",
                    }
                },
                symbol_table={
                    "fifo": sorted(
                        {item["name"] for item in ports}
                        | {"WIDTH", "DEPTH", "wr_ptr", "rd_ptr", "fifo_count"}
                    )
                },
                parser_version="slang-test",
                diagnostics=[],
            )

        def version(self):
            return "slang-test"

    return FakeSlangAnalyzer


# ═══════════════════════════════════════════════════════════════════════
# Test 1: Mental Model Construction
# ═══════════════════════════════════════════════════════════════════════

def test_mental_model_builder(monkeypatch):
    """Test that the mental model builder can parse the FIFO RTL."""
    from services.mental_model import builder

    monkeypatch.setattr(builder, "SlangStructuralAnalyzer", _fake_fifo_slang_analyzer_class())

    # Phase 1: Parse RTL structure (no LLM)
    result = builder.parse_rtl_structure(str(FIFO_SV))
    assert result, "parse_rtl_structure returned empty result"
    assert result.get("parser_engine") == "slang"

    # modules is a list of dicts, each with a 'name' key
    modules = result.get("modules", [])
    module_names = [m.get("name", "") for m in modules] if isinstance(modules, list) else list(modules.keys())
    assert "fifo" in module_names, f"Module 'fifo' not found. Got: {module_names}"

    # Find the fifo module
    fifo = next((m for m in modules if m.get("name") == "fifo"), None) if isinstance(modules, list) else modules.get("fifo")
    assert fifo, "Could not find fifo module dict"
    ports = fifo.get("ports", [])

    # Verify port detection — ports is a list of dicts with 'name' key
    expected_ports = {"clk", "rst_n", "wr_en", "data_in", "rd_en", "data_out", "full", "empty", "count"}
    found_ports = {p.get("name", "") for p in ports} if isinstance(ports, list) else set(ports.keys())
    assert expected_ports.issubset(found_ports), (
        f"Missing ports: {expected_ports - found_ports}"
    )

    # Phase 2: Structural index
    index, hierarchy = builder.build_structural_index(str(FIFO_DIR), [str(FIFO_SV)])
    assert index, "build_structural_index returned empty"
    assert index["fifo"].get("parser_engine") == "slang"

    print(f"✅ Mental model parsed: {len(ports)} ports, module 'fifo' found")


def test_scan_classifies_verilog_txt_as_rtl():
    """RTL saved as .txt (e.g. syn_fifo.txt) must be included in project scan."""
    from services.mental_model.builder import scan_project_folder

    with tempfile.TemporaryDirectory() as tmp:
        rtl_txt = Path(tmp) / "syn_fifo.txt"
        rtl_txt.write_text(FIFO_SV.read_text(encoding="utf-8"), encoding="utf-8")
        scan = scan_project_folder(tmp)
        assert "syn_fifo.txt" in scan.rtl_files, (
            f"Expected syn_fifo.txt in rtl_files, got rtl={scan.rtl_files} spec={scan.spec_files}"
        )


# ═══════════════════════════════════════════════════════════════════════
# Test 2: UnitSim Testbench Generation
# ═══════════════════════════════════════════════════════════════════════

def test_mental_model_builder_uses_regex_fallback_when_slang_missing(monkeypatch):
    """TruthCore can keep demos working with a clearly marked regex fallback."""
    from services.mental_model import builder
    from services.mental_model.slang_analyzer import SlangAnalysisError

    class MissingSlangAnalyzer:
        def __init__(self):
            raise SlangAnalysisError("Slang required", missing_tool=True)

    monkeypatch.setattr(builder, "SlangStructuralAnalyzer", MissingSlangAnalyzer)
    monkeypatch.delenv("CHIPVERIFY_REQUIRE_SLANG", raising=False)
    result = builder.parse_rtl_structure(str(FIFO_SV))
    assert result.get("parser_engine") == "regex_fallback"
    modules = result.get("modules", [])
    assert any(item.get("name") == "fifo" for item in modules)


def test_mental_model_builder_can_require_slang(monkeypatch):
    """Strict production mode should still fail clearly when Slang is unavailable."""
    from services.mental_model import builder
    from services.mental_model.slang_analyzer import SlangAnalysisError

    class MissingSlangAnalyzer:
        def __init__(self):
            raise SlangAnalysisError("Slang required", missing_tool=True)

    monkeypatch.setattr(builder, "SlangStructuralAnalyzer", MissingSlangAnalyzer)
    monkeypatch.setenv("CHIPVERIFY_REQUIRE_SLANG", "1")
    with pytest.raises(SlangAnalysisError):
        builder.parse_rtl_structure(str(FIFO_SV))


def test_testbench_generation():
    """Test that testbench_gen produces valid SystemVerilog testbench."""
    from services.verification.testbench_gen import generate_testbench_from_plan, TestPlan, TestScenario, TestCheck

    # Build a test plan matching the FIFO
    plan = TestPlan(
        module_name="fifo",
        scenarios=[
            TestScenario(
                id="sc_001",
                name="basic_write_read",
                description="Write a value and read it back",
                checks=[
                    TestCheck(
                        signal="data_out",
                        expected="8'hAB",
                        condition="===",
                    ),
                ],
            ),
        ],
    )

    work_dir = tempfile.mkdtemp(prefix="fifo_tb_test_")

    # testbench_gen expects a design object with .clock_domains attribute
    from services.mental_model.schema import DesignBlock, PortInfo, ParameterInfo, ClockDomain
    design = DesignBlock(
        top_module="fifo",
        ports=[
            PortInfo(name="clk", direction="input", width=1),
            PortInfo(name="rst_n", direction="input", width=1),
            PortInfo(name="wr_en", direction="input", width=1),
            PortInfo(name="data_in", direction="input", width=8),
            PortInfo(name="rd_en", direction="input", width=1),
            PortInfo(name="data_out", direction="output", width=8),
            PortInfo(name="full", direction="output", width=1),
            PortInfo(name="empty", direction="output", width=1),
            PortInfo(name="count", direction="output", width=5),
        ],
        parameters=[
            ParameterInfo(name="DEPTH", default_value="16"),
            ParameterInfo(name="WIDTH", default_value="8"),
        ],
        clock_domains=[
            ClockDomain(name="clk", associated_reset="rst_n", reset_polarity="active_low"),
        ],
    )

    output = generate_testbench_from_plan(plan, design=design)

    assert output, "generate_testbench_from_plan returned None"

    # GeneratedTestbench has testbench_file (content or path)
    tb_content = ""
    if hasattr(output, "testbench_file") and output.testbench_file:
        tb_path = Path(output.testbench_file)
        if tb_path.exists():
            tb_content = tb_path.read_text(encoding="utf-8")
        else:
            tb_content = output.testbench_file  # might be content itself
    elif hasattr(output, "code"):
        tb_content = output.code
    elif hasattr(output, "content"):
        tb_content = output.content

    # If content-based output, write to file for verification
    if tb_content and not Path(tb_content).exists():
        tb_file = Path(work_dir) / "fifo_tb.sv"
        tb_file.write_text(tb_content, encoding="utf-8")
        assert "fifo" in tb_content or "module" in tb_content, "Generated testbench doesn't reference fifo"
        print(f"✅ Testbench generated: {len(tb_content)} chars")
    else:
        print(f"✅ Testbench generated: output={type(output).__name__}")


# ═══════════════════════════════════════════════════════════════════════
# Test 3: Formal SVA Generation
# ═══════════════════════════════════════════════════════════════════════

def test_formal_gen():
    """Test that formal_gen produces SVA assertions for the FIFO."""
    from services.verification.formal_gen import generate_formal_from_model

    model = _make_fifo_model()
    work_dir = tempfile.mkdtemp(prefix="fifo_formal_test_")

    output = generate_formal_from_model(model, work_dir=work_dir)
    assert output, "generate_formal_from_model returned None"
    assert output.property_count > 0, "No properties generated"

    # Check SVA file
    if output.assertion_file:
        sva_path = Path(output.assertion_file)
        assert sva_path.exists(), f"SVA file not found: {sva_path}"
        content = sva_path.read_text(encoding="utf-8")
        assert "assert" in content.lower() or "property" in content.lower(), \
            "No assertions found in SVA file"

    print(f"✅ Formal generated: {output.property_count} properties")


# ═══════════════════════════════════════════════════════════════════════
# Test 4: UVM Environment Generation
# ═══════════════════════════════════════════════════════════════════════

def test_uvm_gen():
    """Test that uvm_gen produces a complete UVM environment."""
    from services.verification.uvm_gen import generate_uvm_from_model

    model = _make_fifo_model()
    work_dir = tempfile.mkdtemp(prefix="fifo_uvm_test_")

    output = generate_uvm_from_model(model=model, work_dir=work_dir)

    assert output, "generate_uvm_from_model returned None"
    assert len(output.all_files) >= 10, (
        f"Expected at least 10 UVM files, got {len(output.all_files)}"
    )

    # Check critical files exist
    for attr in ["pkg_file", "interface_file", "driver_file", "monitor_file",
                 "scoreboard_file", "agent_file", "env_file", "test_file", "top_file"]:
        path = getattr(output, attr, "")
        assert path and Path(path).exists(), f"Missing UVM file: {attr}"

    # Verify package content
    pkg = Path(output.pkg_file).read_text(encoding="utf-8")
    assert "package fifo_pkg" in pkg, "Package declaration missing"
    assert "uvm_pkg" in pkg, "UVM import missing"

    # Verify interface content
    intf = Path(output.interface_file).read_text(encoding="utf-8")
    assert "interface fifo_if" in intf, "Interface declaration missing"
    assert "data_in" in intf, "data_in signal missing from interface"

    # Verify driver
    drv = Path(output.driver_file).read_text(encoding="utf-8")
    assert "class fifo_driver" in drv, "Driver class missing"
    assert "uvm_driver" in drv, "Driver doesn't extend uvm_driver"

    # Verify top
    top = Path(output.top_file).read_text(encoding="utf-8")
    assert "fifo dut" in top, "DUT instantiation missing from top"
    assert "run_test" in top, "run_test call missing from top"

    print(f"✅ UVM generated: {len(output.all_files)} files, summary: {output.summary}")


# ═══════════════════════════════════════════════════════════════════════
# Test 5: SymbiYosys Configuration
# ═══════════════════════════════════════════════════════════════════════

def test_symbiyosys_config():
    """Test that SymbiYosys .sby config can be generated."""
    from services.eda.symbiyosys import generate_sby_config

    work_dir = tempfile.mkdtemp(prefix="fifo_sby_test_")
    sva_file = Path(work_dir) / "fifo_sva.sv"
    sva_file.write_text("// placeholder SVA\n", encoding="utf-8")

    sby_path = generate_sby_config(
        rtl_files=[str(FIFO_SV)],
        sva_file=str(sva_file),
        top_module="fifo",
        work_dir=work_dir,
        mode="bmc",
        depth=20,
    )

    assert sby_path, "No .sby path returned"
    sby = Path(sby_path)
    assert sby.exists(), f".sby file not found: {sby_path}"

    content = sby.read_text(encoding="utf-8")
    assert "fifo" in content, "Module name not in .sby"
    assert "bmc" in content, "Mode not in .sby"
    assert "depth 20" in content, "Depth not in .sby"

    print(f"✅ SymbiYosys config generated: {sby.name}")


# ═══════════════════════════════════════════════════════════════════════
# Test 6: Debug Service
# ═══════════════════════════════════════════════════════════════════════

def test_debug_classification():
    """Test failure classification patterns."""
    from services.verification.debug_service import classify_failure

    # Test RTL bug classification
    result = classify_failure(
        "ERROR: data_out mismatch at time 150ns: expected 0x42 got 0xFF"
    )
    assert result["classification"] == "rtl_bug", (
        f"Expected 'rtl_bug', got '{result['classification']}'"
    )
    assert result["confidence"] > 0

    # Test syntax error
    result = classify_failure("Error: syntax error near module")
    assert result["classification"] == "syntax"

    # Test assertion failure — use patterns that survive lowercasing
    result = classify_failure("Error-[ASRT]: Assertion failed in prop_check at time 200ns")
    assert result["classification"] == "assertion_failure", (
        f"Expected 'assertion_failure', got '{result['classification']}'"
    )

    print("✅ Debug classification: all patterns matched")


def test_debug_signal_correlation():
    """Test signal extraction from error logs."""
    from services.verification.debug_service import correlate_signals

    signals = correlate_signals(
        "ERROR: data_out mismatch. wr_ptr = 5, rd_ptr = 3"
    )
    assert any("data_out" in s for s in signals), "data_out not found"
    assert len(signals) > 0

    print(f"✅ Signal correlation: found {signals}")


# ═══════════════════════════════════════════════════════════════════════
# Test 7: Coverage Parser
# ═══════════════════════════════════════════════════════════════════════

def test_coverage_log_parser():
    """Test coverage parsing from log text."""
    from services.verification.coverage_parser import parse_coverage_from_log

    report = parse_coverage_from_log(
        "line_coverage: 87.5%\nbranch_coverage: 72.3%\ntoggle_coverage: 65.1%"
    )
    assert report.line_coverage == 87.5
    assert report.branch_coverage == 72.3
    assert report.toggle_coverage == 65.1
    assert report.grade in ("C", "D")  # avg ~75%

    print(f"✅ Coverage parser: L={report.line_coverage}% grade={report.grade}")


# ═══════════════════════════════════════════════════════════════════════
# Test 8: Tool Registration
# ═══════════════════════════════════════════════════════════════════════

def test_all_tools_registered():
    """Verify all tools are properly registered."""
    import agent_tools.orchestrator_tool
    import agent_tools.unitsim_tools
    import agent_tools.formal_tools
    import agent_tools.coverage_tools
    import agent_tools.debug_tools
    import agent_tools.uvm_tools
    import agent_tools.evolve_tools
    import agent_tools.multimodal_tools
    import agent_tools.plan_tuning_tools
    import agent_tools.dashboard_tools

    from agent_tools import VERIFICATION_TOOL_HANDLERS, VERIFICATION_TOOL_DEFINITIONS

    expected_handlers = {
        "analyzeDesign", "runVerification", "verifyBlock",
        "generateTestbench", "runUnitSimulation",
        "generateFormalProperties", "runFormalVerification",
        "analyzeCoverage", "debugFailure", "diagnoseAndFix",
        "generateUVMEnvironment",
        "detectDesignChanges", "evolveVerification",
        "parseDesignImage",
        "tuneTestPlan",
        "getVerificationDashboard",
    }

    registered = set(VERIFICATION_TOOL_HANDLERS.keys())
    missing = expected_handlers - registered
    assert not missing, f"Missing tool handlers: {missing}"

    # Check definitions
    def_names = {d["function"]["name"] for d in VERIFICATION_TOOL_DEFINITIONS}
    assert "diagnoseAndFix" in def_names
    assert "analyzeDesign" in def_names
    assert "detectDesignChanges" in def_names
    assert "tuneTestPlan" in def_names
    assert "getVerificationDashboard" in def_names

    print(f"All tools registered: {len(registered)} handlers, {len(VERIFICATION_TOOL_DEFINITIONS)} definitions")


# ═══════════════════════════════════════════════════════════════════════
# Test 9: Evolve — Design Change Detection
# ═══════════════════════════════════════════════════════════════════════

def test_evolve_detect_changes():
    """Test that the evolve system detects RTL port and FSM changes."""
    from services.verification.evolve_service import SelfHealingTestbench

    old_rtl = _read_fifo_rtl()

    # Simulate adding a new port: use regex to handle variable whitespace
    import re
    new_rtl = re.sub(
        r"(output\s+logic\s+empty)",
        r"\1,\n    output logic              almost_full",
        old_rtl,
    )
    new_rtl = new_rtl + "\n// localparam STATE_FLUSH = 3'd5;\n"

    healer = SelfHealingTestbench(ai_client=None)
    changes = healer.detect_rtl_changes(old_rtl, new_rtl)

    assert len(changes) > 0, "No changes detected"

    # Check port addition was found
    port_adds = [c for c in changes if c.change_type == "port_added"]
    assert any("almost_full" in c.name for c in port_adds), (
        f"almost_full port not detected. Found: {[c.name for c in port_adds]}"
    )

    print(f"✅ Evolve: detected {len(changes)} changes — {[c.change_type for c in changes]}")


def test_evolve_regression_intelligence():
    """Test failure prediction based on RTL diffs."""
    from services.verification.evolve_service import RegressionIntelligence

    regr = RegressionIntelligence()

    # Add some history
    regr.record_result("test_reset", True)
    regr.record_result("test_basic_operation", True)
    regr.record_result("test_fsm", False, failure_reason="FSM stuck")
    regr.record_result("test_fsm", False, failure_reason="FSM deadlock")
    regr.record_result("test_protocol", True)

    # Predict with an RTL diff that modifies FSM logic
    diff = "+  localparam STATE_FLUSH = 3'd5;\n-  STATE_IDLE: next = STATE_ACTIVE;\n+  STATE_IDLE: next = STATE_FLUSH;"
    test_names = ["test_reset", "test_basic_operation", "test_fsm", "test_protocol"]

    preds = regr.predict_failures(diff, test_names)
    assert len(preds) > 0, "No predictions generated"

    # test_fsm should be highest risk (it has both historical failures AND FSM diff match)
    fsm_pred = next((p for p in preds if p.test_name == "test_fsm"), None)
    assert fsm_pred, "test_fsm prediction not found"
    assert fsm_pred.fail_probability > 0.3, (
        f"test_fsm probability too low: {fsm_pred.fail_probability}"
    )

    # Minimal suite
    suite = regr.generate_minimal_suite(preds, max_tests=3)
    assert len(suite) > 0, "No tests in regression suite"

    print(f"✅ Regression intel: {len(preds)} predictions, suite={suite}")


# ═══════════════════════════════════════════════════════════════════════
# Test 10: Schema — TransactionFlow + ArbitrationPolicy
# ═══════════════════════════════════════════════════════════════════════

def test_transaction_flow_schema():
    """Test that TransactionFlow and ArbitrationPolicy work in DesignBlock."""
    from services.mental_model.schema import (
        DesignBlock, TransactionFlow, ArbitrationPolicy, PortInfo, ClockDomain,
    )

    design = DesignBlock(
        top_module="axi_interconnect",
        description="AXI4 interconnect with round-robin arbitration",
        ports=[
            PortInfo(name="ACLK", direction="input", width=1),
            PortInfo(name="ARESETn", direction="input", width=1),
            PortInfo(name="AWADDR", direction="input", width=32),
        ],
        clock_domains=[
            ClockDomain(name="ACLK", associated_reset="ARESETn"),
        ],
        transaction_flows=[
            TransactionFlow(
                name="AXI Write Burst",
                protocol="AXI4",
                description="Single-beat write with BRESP",
                steps=[
                    {"phase": "Address", "signals": {"AWVALID": "1", "AWADDR": "0x100"}, "cycles": "1"},
                    {"phase": "Data", "signals": {"WVALID": "1", "WDATA": "0xDEAD"}, "cycles": "1"},
                    {"phase": "Response", "signals": {"BVALID": "1", "BRESP": "OKAY"}, "cycles": "1"},
                ],
                latency_cycles=3,
                throughput="1 beat/cycle",
                constraints=["AWREADY must respond within 16 cycles"],
            ),
        ],
        arbitration_policies=[
            ArbitrationPolicy(
                name="master_arb",
                masters=["cpu", "dma", "gpu"],
                scheme="round_robin",
                priority_levels=4,
                qos_support=True,
                qos_levels=4,
                bandwidth_allocation={"cpu": "50%", "dma": "30%", "gpu": "20%"},
                starvation_prevention=True,
                description="Round-robin with QoS for 3 masters",
            ),
        ],
    )

    # Verify transaction flows
    assert len(design.transaction_flows) == 1
    flow = design.transaction_flows[0]
    assert flow.name == "AXI Write Burst"
    assert flow.latency_cycles == 3
    assert len(flow.steps) == 3
    assert flow.steps[0]["phase"] == "Address"

    # Verify arbitration
    assert len(design.arbitration_policies) == 1
    arb = design.arbitration_policies[0]
    assert arb.scheme == "round_robin"
    assert len(arb.masters) == 3
    assert arb.qos_support is True
    assert arb.bandwidth_allocation["cpu"] == "50%"

    # Verify JSON serialization round-trip
    from services.mental_model.schema import MentalModelSchema
    model = MentalModelSchema(design=design)
    json_str = model.to_json()
    model2 = MentalModelSchema.from_json(json_str)
    assert model2.design.top_module == "axi_interconnect"
    assert len(model2.design.transaction_flows) == 1
    assert len(model2.design.arbitration_policies) == 1

    print(f"Schema: TransactionFlow + ArbitrationPolicy serialize/deserialize OK")


# ═══════════════════════════════════════════════════════════════════════
# Test 11: Multimodal — Image Parser JSON Extraction
# ═══════════════════════════════════════════════════════════════════════

def test_image_parser_json_extraction():
    """Test that the image parser can build DesignBlock from JSON."""
    from services.mental_model.image_parser import _parse_llm_json, _build_design_block

    # Simulate what a Vision LLM would return for a FIFO block diagram
    llm_response = '''
    ```json
    {
        "top_module": "fifo",
        "description": "Synchronous FIFO with configurable depth",
        "modules": ["fifo", "fifo_ctrl", "fifo_mem"],
        "hierarchy": {"fifo": ["fifo_ctrl", "fifo_mem"]},
        "ports": [
            {"name": "clk", "direction": "input", "width": 1, "description": "System clock"},
            {"name": "data_in", "direction": "input", "width": 8, "description": "Write data"},
            {"name": "data_out", "direction": "output", "width": 8, "description": "Read data"},
            {"name": "full", "direction": "output", "width": 1, "description": "FIFO full flag"}
        ],
        "parameters": [
            {"name": "DEPTH", "default_value": "16", "description": "FIFO depth"}
        ],
        "fsms": [
            {
                "name": "wr_fsm",
                "states": ["IDLE", "WRITE", "FULL"],
                "transitions": [
                    {"from": "IDLE", "to": "WRITE", "condition": "wr_en"},
                    {"from": "WRITE", "to": "FULL", "condition": "count == DEPTH-1"}
                ]
            }
        ],
        "clock_domains": [
            {"name": "clk", "frequency": "100MHz", "reset": "rst_n", "reset_polarity": "active_low"}
        ],
        "transaction_flows": [
            {
                "name": "Write Transaction",
                "steps": [{"phase": "Write", "signals": {"wr_en": "1"}, "cycles": "1"}],
                "latency_cycles": 1
            }
        ]
    }
    ```
    '''

    # Parse JSON from markdown-fenced response
    parsed = _parse_llm_json(llm_response)
    assert parsed is not None, "JSON parsing failed"
    assert parsed["top_module"] == "fifo"

    # Build DesignBlock
    design = _build_design_block(parsed, "diagram.png")
    assert design.top_module == "fifo"
    assert len(design.ports) == 4
    assert len(design.fsms) == 1
    assert design.fsms[0].name == "wr_fsm"
    assert len(design.fsms[0].states) == 3
    assert len(design.modules) == 3
    assert len(design.hierarchy_tree) == 1
    assert len(design.sub_instances) == 2  # fifo_ctrl, fifo_mem
    assert len(design.clock_domains) == 1
    assert len(design.transaction_flows) == 1
    assert design.parameters[0].name == "DEPTH"

    print(f"Image parser: extracted {len(design.ports)} ports, "
          f"{len(design.fsms)} FSMs, {len(design.modules)} modules from simulated LLM response")


def test_image_parser_metadata():
    """Test image metadata extraction without LLM."""
    from services.mental_model.image_parser import parse_image_metadata_only
    import tempfile

    # Create a dummy PNG file
    tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
    tmp.write(b"\x89PNG\r\n" + b"\x00" * 100)  # minimal PNG-like header
    tmp.close()

    meta = parse_image_metadata_only(tmp.name)
    assert meta["format"] == "png"
    assert meta["supported"] is True
    assert meta["size_bytes"] > 0

    # Test unsupported format
    tmp2 = tempfile.NamedTemporaryFile(suffix=".xyz", delete=False)
    tmp2.write(b"not an image")
    tmp2.close()
    meta2 = parse_image_metadata_only(tmp2.name)
    assert meta2["supported"] is False

    # Clean up
    Path(tmp.name).unlink(missing_ok=True)
    Path(tmp2.name).unlink(missing_ok=True)

    print(f"Image metadata: format={meta['format']}, size={meta['size_human']}")


# ═══════════════════════════════════════════════════════════════════════
# Test 12: Plan Tuning — Natural Language Feedback
# ═══════════════════════════════════════════════════════════════════════

def test_plan_tuning_add_scenario():
    """Test adding a scenario via natural language feedback."""
    from agent_tools.plan_tuning_tools import handle_tune_test_plan

    # Create a minimal plan
    plan = {
        "module_name": "fifo",
        "scenarios": [
            {"id": "TP-001", "name": "test_reset_behavior", "priority": "critical",
             "category": "reset", "description": "Verify reset"},
            {"id": "TP-002", "name": "test_write_basic", "priority": "high",
             "category": "functional", "description": "Basic write"},
        ],
        "total_scenarios": 2,
    }

    # Add a new scenario via NL
    result = asyncio.run(handle_tune_test_plan(
        project_id="test-123",
        plan_json=json.dumps(plan),
        feedback="Add an overflow stress test",
    ))
    data = json.loads(result)
    assert data["status"] == "tuned"
    assert data["total_scenarios"] == 3
    assert any("overflow" in c.get("description", "").lower() for c in data["changes_applied"])

    print(f"Plan tuning ADD: {data['total_scenarios']} scenarios after adding overflow test")


def test_plan_tuning_remove_scenario():
    """Test removing a scenario via natural language."""
    from agent_tools.plan_tuning_tools import handle_tune_test_plan

    plan = {
        "module_name": "fifo",
        "scenarios": [
            {"id": "TP-001", "name": "test_reset_behavior", "priority": "critical",
             "category": "reset", "description": "Verify reset"},
            {"id": "TP-002", "name": "test_stress_rapid_toggle", "priority": "low",
             "category": "stress", "description": "Stress test"},
        ],
        "total_scenarios": 2,
    }

    result = asyncio.run(handle_tune_test_plan(
        project_id="test-123",
        plan_json=json.dumps(plan),
        feedback="Remove the stress test",
    ))
    data = json.loads(result)
    assert data["status"] == "tuned"
    assert data["total_scenarios"] == 1

    print(f"Plan tuning REMOVE: {data['total_scenarios']} scenarios after removing stress")


def test_plan_tuning_show():
    """Test showing the plan summary."""
    from agent_tools.plan_tuning_tools import handle_tune_test_plan

    plan = {
        "module_name": "fifo",
        "scenarios": [
            {"id": "TP-001", "name": "test_reset", "priority": "critical",
             "category": "reset", "description": "Reset test"},
        ],
        "total_scenarios": 1,
    }

    result = asyncio.run(handle_tune_test_plan(
        project_id="test-123",
        plan_json=json.dumps(plan),
        feedback="",
        action="show",
    ))
    data = json.loads(result)
    assert data["total_scenarios"] == 1
    assert data["scenarios"][0]["name"] == "test_reset"

    print(f"Plan tuning SHOW: displayed {data['total_scenarios']} scenarios")


# ═══════════════════════════════════════════════════════════════════════
# Test 13: Strategy Recommender — Verification Type Suggestions
# ═══════════════════════════════════════════════════════════════════════

def test_strategy_recommender():
    """Test that the recommender suggests correct verification types."""
    from services.verification.strategy_recommender import recommend_verification_strategy
    from services.mental_model.schema import (
        MentalModelSchema, DesignBlock, PortInfo, FSMDescription,
        ProtocolBinding, ClockDomain,
    )

    # ── Test 1: Simple FIFO (no FSMs) → UnitSim only ────────────
    model = _make_fifo_model()
    strategy = recommend_verification_strategy(model)

    assert "unitsim" in strategy.recommended_types, "UnitSim should always be recommended"
    assert strategy.design_complexity == "simple"
    assert len(strategy.recommendations) == 3  # always 3 types returned

    display = strategy.to_display()
    assert "✅" in display
    assert "Unit Simulation" in display

    # ── Test 2: Complex design (FSMs + protocols + CDC) → all 3 ─
    complex_design = DesignBlock(
        top_module="axi_bridge",
        ports=[PortInfo(name=f"p{i}", direction="input", width=8) for i in range(20)],
        fsms=[
            FSMDescription(name="rd_fsm", states=["IDLE", "READ", "DONE"]),
            FSMDescription(name="wr_fsm", states=["IDLE", "WRITE", "RESP"]),
        ],
        protocols=[
            ProtocolBinding(protocol="AXI4", role="slave"),
            ProtocolBinding(protocol="APB", role="master"),
        ],
        clock_domains=[
            ClockDomain(name="aclk", associated_reset="arst_n"),
            ClockDomain(name="pclk", associated_reset="prst_n"),
        ],
        has_cdc_crossings=True,
        modules=["axi_bridge", "rd_channel", "wr_channel", "cdc_sync", "arb"],
    )
    complex_model = MentalModelSchema(design=complex_design)

    strategy2 = recommend_verification_strategy(complex_model)
    assert "unitsim" in strategy2.recommended_types
    assert "formal" in strategy2.recommended_types, "Formal should be recommended — FSMs present"
    assert "uvm" in strategy2.recommended_types, "UVM should be recommended — complex design"
    assert strategy2.design_complexity == "complex"

    d = strategy2.to_dict()
    assert len(d["recommended_types"]) == 3

    print(f"Simple FIFO: {strategy.recommended_types} ({strategy.design_complexity})")
    print(f"Complex AXI: {strategy2.recommended_types} ({strategy2.design_complexity})")


# ═══════════════════════════════════════════════════════════════════════
# Test 14: Dashboard Aggregator
# ═══════════════════════════════════════════════════════════════════════

def test_dashboard_aggregator():
    """Test dashboard aggregation with mixed pass/fail results."""
    from services.verification.dashboard_aggregator import aggregate_dashboard

    plan = {
        "scenarios": [
            {"id": "TP-001", "name": "test_reset", "category": "reset", "priority": "critical"},
            {"id": "TP-002", "name": "test_write", "category": "functional", "priority": "high"},
            {"id": "TP-003", "name": "test_overflow", "category": "stress", "priority": "medium"},
        ],
    }

    unitsim = {
        "pass_count": 2,
        "fail_count": 1,
        "test_results": {"test_reset": "pass", "test_write": "pass", "test_overflow": "fail"},
        "failures": ["test_overflow: Assertion failed at time 150"],
    }

    coverage = {
        "line_coverage": 87.5,
        "branch_coverage": 72.0,
        "toggle_coverage": 45.2,
        "functional_coverage": 0.0,
        "grade": "B",
    }

    dashboard = aggregate_dashboard(
        module_name="fifo",
        plan=plan,
        unitsim_results=unitsim,
        coverage_results=coverage,
    )

    # KPIs
    assert dashboard.total_scenarios == 3
    assert dashboard.passed == 2
    assert dashboard.failed == 1
    assert dashboard.overall_status == "fail"  # has failures
    assert dashboard.progress_pct == 100.0  # all completed

    # Coverage
    assert dashboard.line_coverage == 87.5
    assert dashboard.coverage_grade == "B"

    # Verification types
    assert len(dashboard.verification_types) == 3
    unitsim_card = dashboard.verification_types[0]
    assert unitsim_card.type == "unitsim"
    assert unitsim_card.passed == 2
    assert unitsim_card.failed == 1

    # Failures
    assert len(dashboard.failures) == 1
    assert dashboard.failures[0].scenario_name == "test_overflow"

    # Recommendations
    assert len(dashboard.recommendations) > 0

    # Dict output
    d = dashboard.to_dict()
    assert d["kpis"]["passed"] == 2
    assert d["kpis"]["failed"] == 1
    assert len(d["scenarios"]) == 3
    assert len(d["recommendations"]) > 0

    print(f"Dashboard: {dashboard.passed}/{dashboard.total_scenarios} passed, grade={dashboard.coverage_grade}")
    print(f"Recommendations: {len(dashboard.recommendations)}")


def test_dashboard_recommendations():
    """Test that recommendations are generated for coverage gaps."""
    from services.verification.dashboard_aggregator import aggregate_dashboard

    # All passing, low coverage
    dashboard = aggregate_dashboard(
        module_name="fifo",
        plan={"scenarios": [
            {"id": "TP-001", "name": "test_reset", "category": "reset", "priority": "critical"},
        ]},
        unitsim_results={"pass_count": 1, "fail_count": 0, "test_results": {"test_reset": "pass"}},
        coverage_results={"line_coverage": 50.0, "branch_coverage": 30.0, "toggle_coverage": 0.0, "grade": "D"},
    )

    # Should have coverage gap recommendations
    cov_recs = [r for r in dashboard.recommendations if "coverage" in r.text.lower() or "Coverage" in r.text]
    assert len(cov_recs) >= 1, "Should recommend improving coverage"

    # Should have UVM recommendation
    uvm_recs = [r for r in dashboard.recommendations if "UVM" in r.text]
    assert len(uvm_recs) >= 1, "Should recommend running UVM"

    print(f"Recommendations generated: {len(dashboard.recommendations)}")
    for r in dashboard.recommendations:
        print(f"  {r.icon} [{r.severity}] {r.text}")


# ═══════════════════════════════════════════════════════════════════════
# Helper: Build a FIFO model dict
# ═══════════════════════════════════════════════════════════════════════


def _make_fifo_model():
    """Build a model dict that matches the fifo benchmark."""
    from services.mental_model.schema import (
        MentalModelSchema, DesignBlock, PortInfo,
        ParameterInfo, ClockDomain, VerificationIntent,
    )

    design = DesignBlock(
        top_module="fifo",
        ports=[
            PortInfo(name="clk", direction="input", width=1),
            PortInfo(name="rst_n", direction="input", width=1),
            PortInfo(name="wr_en", direction="input", width=1),
            PortInfo(name="data_in", direction="input", width=8),
            PortInfo(name="rd_en", direction="input", width=1),
            PortInfo(name="data_out", direction="output", width=8),
            PortInfo(name="full", direction="output", width=1),
            PortInfo(name="empty", direction="output", width=1),
            PortInfo(name="count", direction="output", width=5),
        ],
        parameters=[
            ParameterInfo(name="DEPTH", default_value="16"),
            ParameterInfo(name="WIDTH", default_value="8"),
        ],
        clock_domains=[
            ClockDomain(
                name="clk",
                associated_reset="rst_n",
                reset_polarity="active_low",
            ),
        ],
    )

    return MentalModelSchema(design=design)


# ═══════════════════════════════════════════════════════════════════════
# Run all tests
# ═══════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])

