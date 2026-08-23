from __future__ import annotations

import sys
from pathlib import Path


BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))


def test_llm_block_enrichment_preserves_constraints_and_aliases():
    from services.mental_model.builder import _apply_llm_result_to_design_block
    from services.mental_model.schema import DesignBlock, PortInfo

    block = DesignBlock(
        top_module="gpio",
        ports=[
            PortInfo(name="wr_en", direction="input", width=1),
            PortInfo(name="cio_gpio_o", direction="output", width=32),
        ],
    )

    _apply_llm_result_to_design_block(
        block,
        {
            "constraints": ["Address must target a documented register."],
            "expected_behaviors": [
                {
                    "id": "EB-001",
                    "stimulus": {"write_enable": "1"},
                    "expected_output": {"data_out": "32'h1"},
                    "latency_cycles": 2,
                }
            ],
            "transaction_flows": [
                {
                    "name": "csr_write",
                    "steps": [
                        {"phase": "drive", "signals": {"write_enable": "1"}},
                    ],
                    "constraints": ["READY must respond within two cycles."],
                    "throughput": "one transfer per cycle",
                }
            ],
        },
    )

    assert "Address must target a documented register." in block.constraints
    assert "READY must respond within two cycles." in block.constraints
    assert block.expected_behaviors[0].stimulus == {"wr_en": "1"}
    assert block.expected_behaviors[0].expected_output == {"cio_gpio_o": "32'h1"}
    assert block.transaction_flows[0].constraints == ["READY must respond within two cycles."]
    assert block.transaction_flows[0].throughput == "one transfer per cycle"


def test_single_agent_uvm_uses_register_map_and_protocol_context(tmp_path):
    from services.verification.uvm_gen import generate_uvm_from_model

    model = {
        "design": {
            "top_module": "apb_regs",
            "ports": [
                {"name": "clk", "direction": "input", "width": 1},
                {"name": "rst_n", "direction": "input", "width": 1},
                {"name": "Paddr", "direction": "input", "width": 32},
                {"name": "Pwdata", "direction": "input", "width": 32},
                {"name": "Pwrite", "direction": "input", "width": 1},
                {"name": "Psel", "direction": "input", "width": 1},
                {"name": "Penable", "direction": "input", "width": 1},
                {"name": "Pready", "direction": "output", "width": 1},
                {"name": "Prdata", "direction": "output", "width": 32},
            ],
            "protocols": [
                {
                    "protocol": "APB",
                    "port_group": [
                        "Paddr",
                        "Pwdata",
                        "Pwrite",
                        "Psel",
                        "Penable",
                        "Pready",
                        "Prdata",
                    ],
                }
            ],
            "register_map": [
                {"name": "CTRL", "offset": 0, "addr_signal": "Paddr"},
                {"name": "STATUS", "offset": 4, "addr_signal": "Paddr"},
            ],
            "constraints": ["Paddr must select a documented register offset."],
        },
        "requirements": [
            {"id": "REQ-001", "text": "APB reads and writes use legal register offsets."}
        ],
        "verification": {
            "uvm_scenarios": [{"name": "legal_apb_access", "description": "Access mapped CSRs"}],
        },
    }

    output = generate_uvm_from_model(model, str(tmp_path))
    seq_item = Path(output.seq_item_file).read_text(encoding="utf-8")
    driver = Path(output.driver_file).read_text(encoding="utf-8")
    monitor = Path(output.monitor_file).read_text(encoding="utf-8")
    scoreboard = Path(output.scoreboard_file).read_text(encoding="utf-8")

    assert "Paddr inside {0, 4};" in seq_item
    assert "Paddr inside {[0:255]}" not in seq_item
    assert "Paddr must select a documented register offset." in seq_item
    assert "APB transfer: setup phase followed by access phase" in driver
    assert "while (vif.Pready !== 1'b1)" in driver
    assert "if (vif.monitor_cb.Psel === 1'b1" in monitor
    assert "APB protocol checker" in scoreboard
