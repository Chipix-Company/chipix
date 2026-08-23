from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))


def _model_with_fifo_kb() -> SimpleNamespace:
    ports = [
        SimpleNamespace(name="clk", direction="input", width=1, bus_range=""),
        SimpleNamespace(name="rst", direction="input", width=1, bus_range=""),
        SimpleNamespace(name="wr", direction="input", width=1, bus_range=""),
        SimpleNamespace(name="rd", direction="input", width=1, bus_range=""),
        SimpleNamespace(name="data_in", direction="input", width=8, bus_range="[7:0]"),
        SimpleNamespace(name="data_out", direction="output", width=8, bus_range="[7:0]"),
        SimpleNamespace(name="empty", direction="output", width=1, bus_range=""),
        SimpleNamespace(name="full", direction="output", width=1, bus_range=""),
        SimpleNamespace(name="fifo_cnt", direction="output", width=4, bus_range="[3:0]"),
    ]
    return SimpleNamespace(
        design=SimpleNamespace(
            top_module="fifo",
            description="FIFO with count, empty, and full flags",
            ports=ports,
            parameters=[],
            modules=["fifo"],
            clock_domains=[],
            protocols=[],
            fsms=[],
            expected_behaviors=[],
            transaction_flows=[],
            register_fields=[],
        ),
        requirements=[
            SimpleNamespace(id="REQ-001", text="The empty flag must assert when fifo_cnt is zero.", priority="high"),
        ],
        verification=SimpleNamespace(formal_properties=[]),
        project_scan=SimpleNamespace(root_path="", rtl_files=[]),
        symbol_table={"fifo": ["wr_ptr", "rd_ptr", "fifo_cnt"]},
        block_models={},
        knowledge_base={
            "formal_properties": [
                {
                    "id": "FP-FIFO-003",
                    "title": "Empty flag only when count is zero",
                    "description": "Empty flag must be asserted if and only if the FIFO count is zero.",
                    "library_source": "OVL pattern",
                    "property_class": "flag_correctness",
                    "severity": "P0",
                    "property_type": "assert",
                    "trigger_patterns": ["empty", "count"],
                    "sva_template": "assert property (@(posedge {clk}) {empty} == ({count} == 0));",
                },
                {
                    "id": "FP-FIFO-005",
                    "title": "No push when full",
                    "description": "No write should be accepted when full unless a read frees space.",
                    "library_source": "Formal best practice",
                    "property_class": "data_integrity",
                    "severity": "P0",
                    "property_type": "assert",
                    "trigger_patterns": ["full", "push", "pop"],
                    "sva_template": "assert property (@(posedge {clk}) {full} && !{pop} |-> !{push});",
                },
            ]
        },
        evidence=[],
        open_questions=[],
        risks=[],
        living_agent={},
    )


def test_formal_uses_knowledge_base_fifo_rules():
    from services.verification.formal_gen import generate_formal_from_model, generate_formal_plan_from_model

    model = _model_with_fifo_kb()
    plan = generate_formal_plan_from_model(model)
    assertion_ids = {item["id"] for item in plan["assertions"]}
    assert "FP-FIFO-003" in assertion_ids
    assert "FP-FIFO-005" in assertion_ids

    work_dir = tempfile.mkdtemp(prefix="formal_kb_fifo_")
    output = generate_formal_from_model(model, work_dir=work_dir, approved_plan=plan)
    content = Path(output.assertion_file).read_text(encoding="utf-8")
    assert "empty == (fifo_cnt == 0)" in content
    assert "(full && !rd) |-> !wr" in content
    assert not output.validation_errors


def test_formal_log_parser_classifies_failures_and_unknown_symbols():
    from services.verification.formal_log_parser import parse_formal_logs

    sby_log = """
SBY 12:00:00 [prove] engine_0: Status returned by engine: FAIL
SBY 12:00:00 [prove] Assert failed in fifo_sva.a_fp_fifo_003
SBY 12:00:01 [prove] writing trace to fifo/engine_0/trace.vcd
"""
    analysis = parse_formal_logs([sby_log], tool="symbiyosys")
    assert analysis.status == "failed"
    assert any(cause.category == "property_failed" for cause in analysis.root_causes)

    xrun_log = "xmvlog: *E,CUVUNF (fifo_sva.sv,21|12): Hierarchical name component lookup failed for 'ghost_signal'."
    analysis = parse_formal_logs([xrun_log], tool="xcelium")
    assert analysis.status == "error"
    assert any(diag.root_cause == "ungrounded_formal_reference" for diag in analysis.diagnostics)


def _tlul_ports():
    return [
        SimpleNamespace(name="clk_i", direction="input", width=1, bus_range=""),
        SimpleNamespace(name="rst_ni", direction="input", width=1, bus_range=""),
        SimpleNamespace(name="a_valid", direction="input", width=1, bus_range=""),
        SimpleNamespace(name="a_ready", direction="output", width=1, bus_range=""),
        SimpleNamespace(name="a_opcode", direction="input", width=3, bus_range="[2:0]"),
        SimpleNamespace(name="a_param", direction="input", width=3, bus_range="[2:0]"),
        SimpleNamespace(name="a_size", direction="input", width=3, bus_range="[2:0]"),
        SimpleNamespace(name="a_source", direction="input", width=4, bus_range="[3:0]"),
        SimpleNamespace(name="a_address", direction="input", width=32, bus_range="[31:0]"),
        SimpleNamespace(name="a_mask", direction="input", width=4, bus_range="[3:0]"),
        SimpleNamespace(name="a_data", direction="input", width=32, bus_range="[31:0]"),
        SimpleNamespace(name="d_valid", direction="output", width=1, bus_range=""),
        SimpleNamespace(name="d_ready", direction="input", width=1, bus_range=""),
        SimpleNamespace(name="d_opcode", direction="output", width=3, bus_range="[2:0]"),
        SimpleNamespace(name="d_source", direction="output", width=4, bus_range="[3:0]"),
        SimpleNamespace(name="d_data", direction="output", width=32, bus_range="[31:0]"),
        SimpleNamespace(name="d_error", direction="output", width=1, bus_range=""),
    ]


def _model_with_tlul_kb() -> SimpleNamespace:
    model = SimpleNamespace(
        design=SimpleNamespace(
            top_module="tlul_device",
            description="TileLink-UL device endpoint",
            ports=_tlul_ports(),
            parameters=[],
            modules=["tlul_device"],
            clock_domains=[],
            protocols=[
                {
                    "protocol": "TileLink-UL",
                    "role": "device",
                    "port_group": [p.name for p in _tlul_ports() if p.name not in {"clk_i", "rst_ni"}],
                }
            ],
            fsms=[],
            expected_behaviors=[],
            transaction_flows=[],
            register_fields=[],
        ),
        requirements=[
            SimpleNamespace(id="REQ-TLUL-001", text="TL-UL payload must remain stable while valid is asserted and ready is low.", priority="high"),
        ],
        verification=SimpleNamespace(formal_properties=[]),
        project_scan=SimpleNamespace(root_path="", rtl_files=[]),
        symbol_table={"tlul_device": []},
        block_models={},
        knowledge_base={},
        evidence=[],
        open_questions=[],
        risks=[],
        living_agent={},
    )
    from services.mental_model.knowledge_integration import build_knowledge_base_context

    model.knowledge_base = build_knowledge_base_context(model)
    return model


def test_tlul_is_inferred_as_first_class_protocol():
    from services.mental_model.builder import _infer_protocol_bindings_from_ports
    from services.mental_model.schema import PortInfo

    ports = [
        PortInfo(name=p.name, direction=p.direction, width=p.width, bus_range=p.bus_range)
        for p in _tlul_ports()
    ]
    protocols = _infer_protocol_bindings_from_ports(ports)
    assert protocols
    assert protocols[0].protocol == "TileLink-UL"
    assert protocols[0].role == "device"
    assert {"a_valid", "a_ready", "d_valid", "d_ready"} <= set(protocols[0].port_group)


def test_formal_uses_tlul_channel_stability_rules():
    from services.verification.formal_gen import generate_formal_from_model, generate_formal_plan_from_model

    model = _model_with_tlul_kb()
    kb_rule_ids = {item["id"] for item in model.knowledge_base.get("protocol_rules", [])}
    assert "TLUL-A-01" in kb_rule_ids
    assert "TLUL-D-02" in kb_rule_ids

    plan = generate_formal_plan_from_model(model)
    assertion_ids = {item["id"] for item in plan["assertions"]}
    cover_ids = {item["id"] for item in plan["covers"]}
    assert {"FP-TLUL-001", "FP-TLUL-002", "FP-TLUL-003", "FP-TLUL-004"} <= assertion_ids
    assert "FP-TLUL-005" in cover_ids

    work_dir = tempfile.mkdtemp(prefix="formal_kb_tlul_")
    output = generate_formal_from_model(model, work_dir=work_dir, approved_plan=plan)
    content = Path(output.assertion_file).read_text(encoding="utf-8")
    assert "(a_valid && !a_ready) |=> a_valid" in content
    assert "$stable({a_opcode, a_param, a_size, a_source, a_address, a_mask, a_data})" in content
    assert "(d_valid && !d_ready) |=> d_valid" in content
    assert "$stable({d_opcode, d_source, d_data, d_error})" in content
    assert not output.validation_errors
