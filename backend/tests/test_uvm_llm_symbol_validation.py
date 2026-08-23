from pathlib import Path
import sys


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))


def _registry():
    from services.verification.uvm_gen import _UVMGeneratedSymbolRegistry

    return _UVMGeneratedSymbolRegistry(
        allowed_packages={"uvm_pkg", "cfs_aligner_core_pkg"},
        generated_types={
            "apb_sequence_item",
            "apb_sequencer",
            "apb_sequence",
            "apb_driver",
            "apb_monitor",
            "apb_agent",
            "md_rx_sequencer",
            "md_tx_sequencer",
            "cfs_aligner_core_env_config",
            "cfs_aligner_core_virtual_sequencer",
            "cfs_aligner_core_base_vseq",
        },
    )


def _apb_sequencer_scaffold():
    return """class apb_sequencer extends uvm_sequencer #(apb_sequence_item);
    `uvm_component_utils(apb_sequencer)
    function new(string name = "apb_sequencer", uvm_component parent = null);
        super.new(name, parent);
    endfunction
endclass : apb_sequencer
"""


def test_rejects_non_generated_agent_package_import():
    from services.verification.uvm_gen import _validate_llm_uvm_content

    content = """import uvm_pkg::*;
import apb_pkg::*;
class apb_sequencer extends uvm_sequencer #(apb_sequence_item);
    `uvm_component_utils(apb_sequencer)
    function new(string name = "apb_sequencer", uvm_component parent = null);
        super.new(name, parent);
    endfunction
endclass : apb_sequencer
"""

    accepted, reason = _validate_llm_uvm_content(
        "apb_sequencer.sv",
        _apb_sequencer_scaffold(),
        content,
        set(),
        _registry(),
    )

    assert not accepted
    assert reason == "unknown package import apb_pkg"


def test_rejects_non_generated_agent_config_type():
    from services.verification.uvm_gen import _validate_llm_uvm_content

    content = """import uvm_pkg::*;
class apb_sequencer extends uvm_sequencer #(apb_sequence_item);
    `uvm_component_utils(apb_sequencer)
    apb_agent_config m_cfg;
    function new(string name = "apb_sequencer", uvm_component parent = null);
        super.new(name, parent);
    endfunction
endclass : apb_sequencer
"""

    accepted, reason = _validate_llm_uvm_content(
        "apb_sequencer.sv",
        _apb_sequencer_scaffold(),
        content,
        set(),
        _registry(),
    )

    assert not accepted
    assert reason == "unknown generated type apb_agent_config"


def test_rejects_role_qualified_virtual_sequencer_handles():
    from services.verification.uvm_gen import _validate_llm_uvm_content

    scaffold = """class cfs_aligner_core_virtual_sequencer extends uvm_sequencer;
    `uvm_component_utils(cfs_aligner_core_virtual_sequencer)
    apb_sequencer apb_sqr;
    md_rx_sequencer md_rx_sqr;
    md_tx_sequencer md_tx_sqr;
    function new(string name = "cfs_aligner_core_virtual_sequencer", uvm_component parent = null);
        super.new(name, parent);
    endfunction
endclass : cfs_aligner_core_virtual_sequencer
"""
    content = scaffold.replace("apb_sequencer apb_sqr;", "apb_master_sequencer apb_sqr;")

    accepted, reason = _validate_llm_uvm_content(
        "cfs_aligner_core_virtual_sequencer.sv",
        scaffold,
        content,
        set(),
        _registry(),
    )

    assert not accepted
    assert reason == "unknown generated type apb_master_sequencer"


def test_accepts_canonical_generated_types_and_package():
    from services.verification.uvm_gen import _validate_llm_uvm_content

    content = """import uvm_pkg::*;
import cfs_aligner_core_pkg::*;
class apb_sequencer extends uvm_sequencer #(apb_sequence_item);
    `uvm_component_utils(apb_sequencer)
    function new(string name = "apb_sequencer", uvm_component parent = null);
        super.new(name, parent);
    endfunction
endclass : apb_sequencer
"""

    accepted, reason = _validate_llm_uvm_content(
        "apb_sequencer.sv",
        _apb_sequencer_scaffold(),
        content,
        set(),
        _registry(),
    )

    assert accepted, reason


def test_multi_agent_generation_uses_canonical_package_and_sequencers(tmp_path):
    from services.verification.uvm_gen import generate_uvm_from_model

    model = {
        "design": {
            "top_module": "cfs_aligner_core",
            "ports": [
                {"name": "clk", "direction": "input", "width": 1},
                {"name": "rst_n", "direction": "input", "width": 1},
                {"name": "Paddr", "direction": "input", "width": 32},
                {"name": "Pwdata", "direction": "input", "width": 32},
                {"name": "Prdata", "direction": "output", "width": 32},
                {"name": "Pwrite", "direction": "input", "width": 1},
                {"name": "Psel", "direction": "input", "width": 1},
                {"name": "Penable", "direction": "input", "width": 1},
                {"name": "Pready", "direction": "output", "width": 1},
                {"name": "md_rx_data", "direction": "input", "width": 32},
                {"name": "md_rx_valid", "direction": "input", "width": 1},
                {"name": "md_tx_data", "direction": "output", "width": 32},
                {"name": "md_tx_valid", "direction": "output", "width": 1},
            ],
            "protocols": [
                {"name": "apb", "port_group": ["Paddr", "Pwdata", "Prdata", "Pwrite", "Psel", "Penable", "Pready"]},
                {"name": "md_rx", "port_group": ["md_rx_data", "md_rx_valid"]},
                {"name": "md_tx", "port_group": ["md_tx_data", "md_tx_valid"]},
            ],
        },
        "verification": {
            "uvm_agents": [
                {"name": "apb_agent", "type": "active", "ports": ["Paddr", "Pwdata", "Prdata", "Pwrite", "Psel", "Penable", "Pready"]},
                {"name": "md_rx_agent", "type": "active", "ports": ["md_rx_data", "md_rx_valid"]},
                {"name": "md_tx_agent", "type": "passive", "ports": ["md_tx_data", "md_tx_valid"]},
            ],
            "uvm_scenarios": [{"name": "nominal", "description": "Nominal multi-interface scenario"}],
        },
    }

    output = generate_uvm_from_model(model=model, work_dir=str(tmp_path))
    generated_names = {Path(path).name for path in output.all_files}

    assert "cfs_aligner_core_pkg.sv" in generated_names
    assert "apb_sequencer.sv" in generated_names
    assert "md_rx_sequencer.sv" in generated_names
    assert "md_tx_sequencer.sv" in generated_names
    assert "cfs_aligner_core_virtual_sequencer.sv" in generated_names

    combined = "\n".join(
        Path(path).read_text(encoding="utf-8", errors="ignore")
        for path in output.all_files
        if str(path).endswith((".sv", ".svh", ".f"))
    )
    forbidden = [
        "import apb_pkg::*",
        "apb_agent_config",
        "apb_master_sequencer",
        "md_rx_master_sequencer",
        "md_tx_slave_sequencer",
    ]
    for token in forbidden:
        assert token not in combined

    filelist = Path(output.filelist).read_text(encoding="utf-8")
    assert "cfs_aligner_core_pkg.sv" in filelist
    assert "top_tb.sv" in filelist
