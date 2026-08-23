from __future__ import annotations

import subprocess
import sys
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_ROOT.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))


from services.verification.uvm_human_parity import create_direct_case, evaluate_case


def test_valid_direct_folder_comparison(tmp_path):
    rtl = _write_rtl(tmp_path)
    human = tmp_path / "human_uvm"
    generated = tmp_path / "generated_uvm"
    _write_uvm_set(human)
    _write_uvm_set(generated)

    result = evaluate_case(
        create_direct_case(
            top_module="fifo",
            human_uvm_dir=human,
            generated_uvm_dir=generated,
            rtl_files=[rtl],
        )
    )

    assert result.human_parity_score >= 80
    assert "missing_required_role" not in {issue.code for issue in result.issues}
    assert "weak_scoreboard" not in {issue.code for issue in result.issues}


def test_missing_generated_role_lowers_score(tmp_path):
    rtl = _write_rtl(tmp_path)
    human = tmp_path / "human_uvm"
    generated = tmp_path / "generated_uvm"
    _write_uvm_set(human)
    _write_uvm_set(generated, include_monitor=False)

    result = evaluate_case(
        create_direct_case(
            top_module="fifo",
            human_uvm_dir=human,
            generated_uvm_dir=generated,
            rtl_files=[rtl],
        )
    )

    assert result.human_parity_score < 100
    assert "missing_required_role" in {issue.code for issue in result.issues}


def test_hallucinated_signal_is_reported(tmp_path):
    rtl = _write_rtl(tmp_path)
    human = tmp_path / "human_uvm"
    generated = tmp_path / "generated_uvm"
    _write_uvm_set(human)
    _write_uvm_set(generated, extra_interface_signal="ghost_signal")

    result = evaluate_case(
        create_direct_case(
            top_module="fifo",
            human_uvm_dir=human,
            generated_uvm_dir=generated,
            rtl_files=[rtl],
        )
    )

    assert "hallucinated_signal" in {issue.code for issue in result.issues}


def test_pass_only_scoreboard_is_reported(tmp_path):
    rtl = _write_rtl(tmp_path)
    human = tmp_path / "human_uvm"
    generated = tmp_path / "generated_uvm"
    _write_uvm_set(human)
    _write_uvm_set(generated, weak_scoreboard=True)

    result = evaluate_case(
        create_direct_case(
            top_module="fifo",
            human_uvm_dir=human,
            generated_uvm_dir=generated,
            rtl_files=[rtl],
        )
    )

    assert "weak_scoreboard" in {issue.code for issue in result.issues}


def test_cli_case_writes_reports(tmp_path):
    rtl = _write_rtl(tmp_path / "case")
    case_dir = tmp_path / "case"
    human = case_dir / "human_uvm"
    generated = case_dir / "generated_uvm"
    _write_uvm_set(human)
    _write_uvm_set(generated)
    (case_dir / "benchmark.json").write_text(
        """{
  "id": "fifo",
  "top_module": "fifo",
  "rtl_files": ["rtl/fifo.sv"],
  "human_uvm_dir": "human_uvm",
  "generated_uvm_dir": "generated_uvm",
  "clock": "clk",
  "reset": {"name": "rst_n", "active": "low"},
  "required_roles": [
    "package", "interface", "seq_item", "sequencer", "driver", "monitor",
    "agent", "env", "scoreboard", "coverage", "tests", "top", "filelist"
  ]
}
""",
        encoding="utf-8",
    )
    assert rtl.exists()

    out_dir = tmp_path / "reports"
    proc = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "backend" / "scripts" / "run_uvm_human_parity.py"),
            "--case",
            str(case_dir),
            "--out",
            str(out_dir),
        ],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        check=False,
    )

    assert proc.returncode == 0, proc.stderr
    for name in ["scorecard.json", "issues.json", "comparison_report.md", "improvement_plan.md"]:
        assert (out_dir / "fifo" / name).exists(), name


def _write_rtl(root: Path) -> Path:
    rtl_dir = root / "rtl"
    rtl_dir.mkdir(parents=True, exist_ok=True)
    rtl = rtl_dir / "fifo.sv"
    rtl.write_text(
        """module fifo (
  input logic clk,
  input logic rst_n,
  input logic wr_en,
  input logic [7:0] data_in,
  input logic rd_en,
  output logic [7:0] data_out,
  output logic full,
  output logic empty,
  output logic [4:0] count
);
endmodule
""",
        encoding="utf-8",
    )
    return rtl


def _write_uvm_set(
    root: Path,
    *,
    include_monitor: bool = True,
    extra_interface_signal: str = "",
    weak_scoreboard: bool = False,
) -> None:
    root.mkdir(parents=True, exist_ok=True)
    files = {
        "fifo_pkg.sv": """package fifo_pkg;
  import uvm_pkg::*;
  class fifo_seq_item extends uvm_sequence_item;
    `uvm_object_utils(fifo_seq_item)
    rand bit wr_en;
    rand bit rd_en;
    rand bit [7:0] data_in;
    bit [7:0] data_out;
    function new(string name = "fifo_seq_item"); super.new(name); endfunction
  endclass
endpackage
""",
        "fifo_if.sv": f"""interface fifo_if;
  logic clk;
  logic rst_n;
  logic wr_en;
  logic [7:0] data_in;
  logic rd_en;
  logic [7:0] data_out;
  logic full;
  logic empty;
  logic [4:0] count;
  {f"logic {extra_interface_signal};" if extra_interface_signal else ""}
endinterface
""",
        "fifo_seq_item.sv": """class fifo_local_seq_item extends uvm_sequence_item;
  `uvm_object_utils(fifo_local_seq_item)
  function new(string name = "fifo_local_seq_item"); super.new(name); endfunction
endclass
""",
        "fifo_sequencer.sv": """class fifo_sequencer extends uvm_sequencer #(fifo_seq_item);
  `uvm_component_utils(fifo_sequencer)
  function new(string name = "fifo_sequencer", uvm_component parent = null); super.new(name, parent); endfunction
endclass
""",
        "fifo_seq_lib.sv": """class fifo_base_seq extends uvm_sequence #(fifo_seq_item);
  `uvm_object_utils(fifo_base_seq)
  task body();
    fifo_seq_item item = fifo_seq_item::type_id::create("item");
    start_item(item);
    assert(item.randomize());
    finish_item(item);
  endtask
endclass
""",
        "fifo_driver.sv": """class fifo_driver extends uvm_driver #(fifo_seq_item);
  `uvm_component_utils(fifo_driver)
  virtual fifo_if vif;
  function new(string name = "fifo_driver", uvm_component parent = null); super.new(name, parent); endfunction
  task run_phase(uvm_phase phase);
    forever begin
      seq_item_port.get_next_item(req);
      @(posedge vif.clk);
      if (!vif.rst_n) begin vif.wr_en <= 0; end
      vif.wr_en <= req.wr_en;
      vif.rd_en <= req.rd_en;
      vif.data_in <= req.data_in;
      seq_item_port.item_done();
    end
  endtask
endclass
""",
        "fifo_monitor.sv": """class fifo_monitor extends uvm_monitor;
  `uvm_component_utils(fifo_monitor)
  virtual fifo_if vif;
  uvm_analysis_port #(fifo_seq_item) analysis_port;
  function new(string name = "fifo_monitor", uvm_component parent = null);
    super.new(name, parent);
    analysis_port = new("analysis_port", this);
  endfunction
  task run_phase(uvm_phase phase);
    fifo_seq_item item;
    forever begin
      @(posedge vif.clk);
      if (vif.rst_n) begin
        item = fifo_seq_item::type_id::create("item");
        item.data_out = vif.data_out;
        analysis_port.write(item);
      end
    end
  endtask
endclass
""",
        "fifo_agent.sv": """class fifo_agent extends uvm_agent;
  `uvm_component_utils(fifo_agent)
  fifo_driver drv;
  fifo_monitor mon;
  fifo_sequencer sqr;
  function new(string name = "fifo_agent", uvm_component parent = null); super.new(name, parent); endfunction
  function void build_phase(uvm_phase phase);
    drv = fifo_driver::type_id::create("drv", this);
    mon = fifo_monitor::type_id::create("mon", this);
    sqr = fifo_sequencer::type_id::create("sqr", this);
  endfunction
  function void connect_phase(uvm_phase phase);
    drv.seq_item_port.connect(sqr.seq_item_export);
  endfunction
endclass
""",
        "fifo_env.sv": """class fifo_env extends uvm_env;
  `uvm_component_utils(fifo_env)
  fifo_agent agt;
  fifo_scoreboard sb;
  function new(string name = "fifo_env", uvm_component parent = null); super.new(name, parent); endfunction
  function void build_phase(uvm_phase phase);
    agt = fifo_agent::type_id::create("agt", this);
    sb = fifo_scoreboard::type_id::create("sb", this);
  endfunction
  function void connect_phase(uvm_phase phase);
    agt.mon.analysis_port.connect(sb.analysis_export);
  endfunction
endclass
""",
        "fifo_scoreboard.sv": _scoreboard_text(weak_scoreboard),
        "fifo_coverage.sv": """class fifo_coverage extends uvm_subscriber #(fifo_seq_item);
  `uvm_component_utils(fifo_coverage)
  covergroup fifo_cg;
    wr_cp: coverpoint item.wr_en;
    rd_cp: coverpoint item.rd_en;
  endgroup
  fifo_seq_item item;
  function new(string name = "fifo_coverage", uvm_component parent = null);
    super.new(name, parent);
    fifo_cg = new();
  endfunction
  function void write(fifo_seq_item t);
    item = t;
    fifo_cg.sample();
  endfunction
endclass
""",
        "fifo_tests.sv": """class fifo_base_test extends uvm_test;
  `uvm_component_utils(fifo_base_test)
  fifo_env env;
  function new(string name = "fifo_base_test", uvm_component parent = null); super.new(name, parent); endfunction
  function void build_phase(uvm_phase phase);
    env = fifo_env::type_id::create("env", this);
  endfunction
endclass
""",
        "top_tb.sv": """module top_tb;
  import uvm_pkg::*;
  import fifo_pkg::*;
  fifo_if intf();
  fifo dut(.clk(intf.clk), .rst_n(intf.rst_n), .wr_en(intf.wr_en), .data_in(intf.data_in),
           .rd_en(intf.rd_en), .data_out(intf.data_out), .full(intf.full), .empty(intf.empty), .count(intf.count));
  initial run_test("fifo_base_test");
endmodule
""",
    }
    if not include_monitor:
        files.pop("fifo_monitor.sv")

    for name, content in files.items():
        (root / name).write_text(content, encoding="utf-8")

    filelist_entries = ["fifo.sv"] + list(files)
    if not include_monitor and "fifo_monitor.sv" in filelist_entries:
        filelist_entries.remove("fifo_monitor.sv")
    (root / "fifo_uvm.f").write_text("\n".join(filelist_entries) + "\n", encoding="utf-8")


def _scoreboard_text(weak: bool) -> str:
    if weak:
        return """class fifo_scoreboard extends uvm_scoreboard;
  `uvm_component_utils(fifo_scoreboard)
  int pass_count;
  function void write(fifo_seq_item item);
    pass_count++;
  endfunction
endclass
"""
    return """class fifo_scoreboard extends uvm_scoreboard;
  `uvm_component_utils(fifo_scoreboard)
  uvm_analysis_imp #(fifo_seq_item, fifo_scoreboard) analysis_export;
  bit [7:0] expected[$];
  function new(string name = "fifo_scoreboard", uvm_component parent = null);
    super.new(name, parent);
    analysis_export = new("analysis_export", this);
  endfunction
  function void write(fifo_seq_item item);
    bit [7:0] predicted;
    if (expected.size()) predicted = expected.pop_front();
    if (item.data_out !== predicted) begin
      `uvm_error("FIFO_SCB", "expected output mismatch")
    end
  endfunction
endclass
"""
