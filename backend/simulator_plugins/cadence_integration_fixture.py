"""Hardcoded FIFO UVM fixture for Cadence integration smoke tests.

Materializes the benchmark FIFO RTL plus a minimal UVM environment, then runs
the same compile-gate path production uses for generated UVM collateral. This
lets users validate Cadence wiring without uploading RTL or generating a plan.
"""

from __future__ import annotations

import shutil
import sys
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

BACKEND_ROOT = Path(__file__).resolve().parents[1]


def _resolve_fifo_rtl() -> Path:
    """Locate the benchmark FIFO RTL across dev and packaged layouts.

    PyInstaller bundles ``--add-data`` content under ``sys._MEIPASS``, which is
    not always the same directory as ``__file__``'s parent, so check both before
    falling back to the dev path (the caller uses an embedded source if neither
    exists).
    """
    candidates = [BACKEND_ROOT / "benchmarks" / "fifo" / "fifo.sv"]
    meipass = getattr(sys, "_MEIPASS", "")
    if meipass:
        candidates.append(Path(meipass) / "benchmarks" / "fifo" / "fifo.sv")
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0]


FIFO_RTL = _resolve_fifo_rtl()

# Inline copy of benchmarks/fifo/fifo.sv. Packaged builds do not bundle the
# benchmarks/ directory, so the smoke fixture cannot rely on FIFO_RTL existing
# on disk — fall back to this embedded source so the test runs anywhere.
FIFO_RTL_SOURCE = """// Synchronous FIFO — Cadence integration smoke fixture (embedded fallback).
module fifo #(
  parameter DEPTH = 16,
  parameter WIDTH = 8
)(
  input  logic              clk,
  input  logic              rst_n,
  input  logic              wr_en,
  input  logic [WIDTH-1:0]  data_in,
  input  logic              rd_en,
  output logic [WIDTH-1:0]  data_out,
  output logic              full,
  output logic              empty,
  output logic [$clog2(DEPTH):0] count
);

  logic [WIDTH-1:0] mem [0:DEPTH-1];
  logic [$clog2(DEPTH)-1:0] wr_ptr;
  logic [$clog2(DEPTH)-1:0] rd_ptr;
  logic [$clog2(DEPTH):0]   fifo_count;

  assign full  = (fifo_count == DEPTH);
  assign empty = (fifo_count == 0);
  assign count = fifo_count;

  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      wr_ptr <= '0;
    end else if (wr_en && !full) begin
      mem[wr_ptr] <= data_in;
      wr_ptr <= wr_ptr + 1'b1;
    end
  end

  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      rd_ptr   <= '0;
      data_out <= '0;
    end else if (rd_en && !empty) begin
      data_out <= mem[rd_ptr];
      rd_ptr   <= rd_ptr + 1'b1;
    end
  end

  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      fifo_count <= '0;
    end else begin
      case ({wr_en && !full, rd_en && !empty})
        2'b10:   fifo_count <= fifo_count + 1'b1;
        2'b01:   fifo_count <= fifo_count - 1'b1;
        default: fifo_count <= fifo_count;
      endcase
    end
  end
endmodule
"""

FIFO_IF = """interface fifo_if(input logic clk, input logic rst_n);
  logic wr_en;
  logic [7:0] data_in;
  logic rd_en;
  logic [7:0] data_out;
  logic full;
  logic empty;
  logic [4:0] count;

  modport dut (
    input  clk,
    input  rst_n,
    input  wr_en,
    input  data_in,
    input  rd_en,
    output data_out,
    output full,
    output empty,
    output count
  );

  modport drv (
    input  clk,
    input  rst_n,
    output wr_en,
    output data_in,
    output rd_en
  );
endinterface
"""

FIFO_PKG = """package fifo_pkg;
  import uvm_pkg::*;
  `include "uvm_macros.svh"

  class fifo_seq_item extends uvm_sequence_item;
    `uvm_object_utils(fifo_seq_item)
    rand bit wr_en;
    rand bit rd_en;
    rand bit [7:0] data_in;
    bit [7:0] data_out;
    function new(string name = "fifo_seq_item");
      super.new(name);
    endfunction
  endclass

  class fifo_base_seq extends uvm_sequence #(fifo_seq_item);
    `uvm_object_utils(fifo_base_seq)
    task body();
      fifo_seq_item item = fifo_seq_item::type_id::create("item");
      start_item(item);
      void'(item.randomize() with { wr_en dist {0:=1, 1:=3}; rd_en dist {0:=3, 1:=1}; });
      finish_item(item);
    endtask
  endclass

  class fifo_sequencer extends uvm_sequencer #(fifo_seq_item);
    `uvm_component_utils(fifo_sequencer)
    function new(string name = "fifo_sequencer", uvm_component parent = null);
      super.new(name, parent);
    endfunction
  endclass

  class fifo_driver extends uvm_driver #(fifo_seq_item);
    `uvm_component_utils(fifo_driver)
    virtual fifo_if vif;
    function new(string name = "fifo_driver", uvm_component parent = null);
      super.new(name, parent);
    endfunction
    function void build_phase(uvm_phase phase);
      super.build_phase(phase);
      if (!uvm_config_db#(virtual fifo_if)::get(this, "", "vif", vif)) begin
        `uvm_fatal("NOVIF", "virtual fifo_if not found")
      end
    endfunction
    task run_phase(uvm_phase phase);
      forever begin
        seq_item_port.get_next_item(req);
        @(posedge vif.clk);
        if (vif.rst_n) begin
          vif.wr_en <= req.wr_en;
          vif.rd_en <= req.rd_en;
          vif.data_in <= req.data_in;
        end
        seq_item_port.item_done();
      end
    endtask
  endclass

  class fifo_monitor extends uvm_monitor;
    `uvm_component_utils(fifo_monitor)
    virtual fifo_if vif;
    uvm_analysis_port #(fifo_seq_item) analysis_port;
    function new(string name = "fifo_monitor", uvm_component parent = null);
      super.new(name, parent);
      analysis_port = new("analysis_port", this);
    endfunction
    function void build_phase(uvm_phase phase);
      super.build_phase(phase);
      if (!uvm_config_db#(virtual fifo_if)::get(this, "", "vif", vif)) begin
        `uvm_fatal("NOVIF", "virtual fifo_if not found")
      end
    endfunction
    task run_phase(uvm_phase phase);
      fifo_seq_item item;
      forever begin
        @(posedge vif.clk);
        if (vif.rst_n) begin
          item = fifo_seq_item::type_id::create("item");
          item.wr_en = vif.wr_en;
          item.rd_en = vif.rd_en;
          item.data_in = vif.data_in;
          item.data_out = vif.data_out;
          analysis_port.write(item);
        end
      end
    endtask
  endclass

  class fifo_agent extends uvm_agent;
    `uvm_component_utils(fifo_agent)
    fifo_driver drv;
    fifo_monitor mon;
    fifo_sequencer sqr;
    function new(string name = "fifo_agent", uvm_component parent = null);
      super.new(name, parent);
    endfunction
    function void build_phase(uvm_phase phase);
      super.build_phase(phase);
      drv = fifo_driver::type_id::create("drv", this);
      mon = fifo_monitor::type_id::create("mon", this);
      sqr = fifo_sequencer::type_id::create("sqr", this);
    endfunction
    function void connect_phase(uvm_phase phase);
      drv.seq_item_port.connect(sqr.seq_item_export);
    endfunction
  endclass

  class fifo_env extends uvm_env;
    `uvm_component_utils(fifo_env)
    fifo_agent agt;
    function new(string name = "fifo_env", uvm_component parent = null);
      super.new(name, parent);
    endfunction
    function void build_phase(uvm_phase phase);
      super.build_phase(phase);
      agt = fifo_agent::type_id::create("agt", this);
    endfunction
  endclass

  class fifo_base_test extends uvm_test;
    `uvm_component_utils(fifo_base_test)
    fifo_env env;
    function new(string name = "fifo_base_test", uvm_component parent = null);
      super.new(name, parent);
    endfunction
    function void build_phase(uvm_phase phase);
      super.build_phase(phase);
      env = fifo_env::type_id::create("env", this);
    endfunction
    task run_phase(uvm_phase phase);
      fifo_base_seq seq = fifo_base_seq::type_id::create("seq");
      phase.raise_objection(this);
      seq.start(env.agt.sqr);
      repeat (8) @(posedge env.agt.drv.vif.clk);
      phase.drop_objection(this);
    endtask
  endclass
endpackage
"""

TOP_TB = """module top_tb;
  import uvm_pkg::*;
  import fifo_pkg::*;

  logic clk;
  logic rst_n;
  fifo_if intf(clk, rst_n);

  fifo dut (
    .clk(intf.clk),
    .rst_n(intf.rst_n),
    .wr_en(intf.wr_en),
    .data_in(intf.data_in),
    .rd_en(intf.rd_en),
    .data_out(intf.data_out),
    .full(intf.full),
    .empty(intf.empty),
    .count(intf.count)
  );

  initial begin
    clk = 0;
    forever #5 clk = ~clk;
  end

  initial begin
    rst_n = 0;
    intf.wr_en = 0;
    intf.rd_en = 0;
    intf.data_in = 0;
    #20;
    rst_n = 1;
  end

  initial begin
    uvm_config_db#(virtual fifo_if)::set(null, "*", "vif", intf);
    run_test("fifo_base_test");
  end
endmodule
"""


def materialize_fifo_fixture(work_dir: Path) -> dict[str, str]:
    """Write the FIFO RTL + minimal UVM collateral into ``work_dir``."""
    work_dir.mkdir(parents=True, exist_ok=True)
    # Prefer the on-disk benchmark RTL; fall back to the embedded source so the
    # fixture works in packaged builds that do not ship benchmarks/.
    try:
        if FIFO_RTL.exists():
            shutil.copy2(FIFO_RTL, work_dir / "fifo.sv")
        else:
            (work_dir / "fifo.sv").write_text(FIFO_RTL_SOURCE, encoding="utf-8")
    except OSError:
        (work_dir / "fifo.sv").write_text(FIFO_RTL_SOURCE, encoding="utf-8")
    (work_dir / "fifo_if.sv").write_text(FIFO_IF, encoding="utf-8")
    (work_dir / "fifo_pkg.sv").write_text(FIFO_PKG, encoding="utf-8")
    (work_dir / "top_tb.sv").write_text(TOP_TB, encoding="utf-8")

    filelist = work_dir / "fifo_uvm.f"
    filelist.write_text(
        "\n".join(
            [
                f"+incdir+{work_dir}",
                str(work_dir / "fifo.sv"),
                str(work_dir / "fifo_if.sv"),
                str(work_dir / "fifo_pkg.sv"),
                str(work_dir / "top_tb.sv"),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    return {
        "fixture": "fifo",
        "work_dir": str(work_dir),
        "filelist": str(filelist),
        "top_module": "top_tb",
        "uvm_testname": "fifo_base_test",
        "rtl_file": str(work_dir / "fifo.sv"),
    }


def run_cadence_fifo_integration_test(
    *,
    work_dir: Path | None = None,
    timeout_seconds: int = 180,
) -> dict[str, Any]:
    """Public entry point — always returns a structured result, never raises.

    Any unexpected failure becomes a ``status="error"`` payload so the API
    surfaces a readable banner instead of a 500.
    """
    started = time.time()
    try:
        return _run_integration_test_impl(
            work_dir=work_dir,
            timeout_seconds=timeout_seconds,
        )
    except Exception as exc:  # pragma: no cover - defensive catch-all
        return _finalize(
            started,
            status="error",
            message=f"Cadence integration test could not run: {exc}",
            detection=None,
            fixture=None,
            compile_gate=None,
            timeout_seconds=timeout_seconds,
            runner=None,
            run_summary=None,
        )


LOG_EXCERPT_LIMIT = 1200


def _read_log_excerpt(
    *,
    log_path: str = "",
    stdout: str = "",
    stderr: str = "",
    limit: int = LOG_EXCERPT_LIMIT,
) -> str:
    chunks: list[str] = []
    if log_path:
        path = Path(log_path)
        if path.is_file():
            try:
                chunks.append(path.read_text(encoding="utf-8", errors="ignore"))
            except OSError:
                pass
    if stdout:
        chunks.append(stdout)
    if stderr:
        chunks.append(stderr)
    combined = "\n".join(chunks).strip()
    if not combined:
        return ""
    return combined[-limit:]


def _phase_display_name(phase: str) -> str:
    lowered = phase.lower()
    if "compile" in lowered:
        return "Compile"
    if lowered == "elaborate":
        return "Elaborate"
    if lowered.startswith("simulate"):
        return "Simulate"
    return phase.replace("_", " ").title()


def _command_uses_xrun(command: list[Any] | None) -> bool:
    parts = [str(part).lower() for part in (command or [])]
    return any("xrun" in part for part in parts)


def _command_passed(cmd: dict[str, Any]) -> bool:
    return cmd.get("returncode") == 0 and not bool(cmd.get("timed_out"))


def _build_run_summary(
    runner_payload: dict[str, Any],
    *,
    work_dir: Path | None = None,
) -> dict[str, Any]:
    """Summarize real xrun invocations and tail log output for the UI."""
    commands = runner_payload.get("commands") or []
    logs = runner_payload.get("logs") or {}
    steps: list[dict[str, Any]] = []
    xrun_count = 0

    for cmd in commands:
        command_list = list(cmd.get("command") or [])
        command_str = " ".join(str(part) for part in command_list)
        uses_xrun = _command_uses_xrun(command_list)
        if uses_xrun:
            xrun_count += 1
        phase = str(cmd.get("phase") or "")
        log_path = str(cmd.get("log_path") or "")
        if not log_path:
            for key, value in logs.items():
                if key in phase or phase.startswith(key):
                    log_path = str(value)
                    break
        passed = _command_passed(cmd)
        steps.append(
            {
                "phase": phase,
                "label": _phase_display_name(phase),
                "command": command_str,
                "returncode": cmd.get("returncode"),
                "passed": passed,
                "timed_out": bool(cmd.get("timed_out")),
                "uses_xrun": uses_xrun,
                "log_path": log_path,
                "log_excerpt": _read_log_excerpt(
                    log_path=log_path,
                    stdout=str(cmd.get("stdout") or ""),
                    stderr=str(cmd.get("stderr") or ""),
                ),
            }
        )

    flags = _pipeline_phase_flags([step["phase"] for step in steps])
    xrun_steps = [step for step in steps if step["uses_xrun"]]
    all_xrun_passed = bool(xrun_steps) and all(step["passed"] for step in xrun_steps)
    regression = runner_payload.get("regression") or {}
    simulate_ok = regression.get("status") == "passed"
    real_execution = bool(
        xrun_count >= 3
        and flags["compile"]
        and flags["elaborate"]
        and flags["simulate"]
        and all_xrun_passed
        and simulate_ok
    )

    summary_parts = []
    for step in steps:
        if not step["uses_xrun"]:
            continue
        status_word = "passed" if step["passed"] else "failed"
        summary_parts.append(f"{step['label']} {status_word} (rc={step['returncode']})")

    analysis = runner_payload.get("analysis") or {}
    return {
        "real_execution": real_execution,
        "xrun_invocations": xrun_count,
        "steps": steps,
        "summary": "; ".join(summary_parts) if summary_parts else "No xrun commands executed.",
        "analysis_status": analysis.get("status") or "",
        "regression": regression,
        "work_dir": str(work_dir) if work_dir else "",
    }


def _run_fifo_xcelium_pipeline(
    fixture: dict[str, str],
    root: Path,
    *,
    timeout_seconds: int,
) -> dict[str, Any]:
    """Run compile → elaborate → simulate on the FIFO fixture via XceliumRunner."""
    from simulator_plugins.base import SimulatorRunRequest
    from simulator_plugins.xcelium_runner import XceliumRunner

    request = SimulatorRunRequest(
        run_dir=root,
        filelist=Path(fixture["filelist"]),
        top_module=fixture["top_module"],
        uvm_testname=fixture["uvm_testname"],
        timeout_seconds=timeout_seconds,
        coverage=False,
        waveform=False,
        dry_run=False,
        auto_repair_generated=False,
        max_repair_rounds=0,
        replay_failures=False,
        emit_events=False,
        run_id=root.name,
    )
    result = XceliumRunner().run(request)
    payload = result.to_dict()
    phases = [str(cmd.get("phase") or "") for cmd in (payload.get("commands") or [])]
    return {
        "status": payload.get("status"),
        "phases": phases,
        "regression": payload.get("regression") or {},
        "commands": payload.get("commands") or [],
        "logs": payload.get("logs") or {},
        "warnings": payload.get("warnings") or [],
        "analysis": payload.get("analysis") or {},
    }


def _pipeline_phase_flags(phases: list[str]) -> dict[str, bool]:
    normalized = [phase.lower() for phase in phases]
    return {
        "compile": any("compile" in phase for phase in normalized),
        "elaborate": any(phase == "elaborate" for phase in normalized),
        "simulate": any(phase.startswith("simulate_") for phase in normalized),
    }


def _compile_gate_from_runner(
    runner_payload: dict[str, Any],
    run_summary: dict[str, Any],
) -> dict[str, Any]:
    phases = runner_payload.get("phases") or []
    flags = _pipeline_phase_flags(phases)
    real_cadence = bool(run_summary.get("real_execution"))
    passed = real_cadence
    errors: list[str] = []
    if not flags["compile"]:
        errors.append("Cadence xrun compile did not run.")
    elif not any(step.get("uses_xrun") and "compile" in str(step.get("phase", "")).lower() and step.get("passed") for step in run_summary.get("steps") or []):
        errors.append("Cadence xrun compile command failed or did not invoke xrun.")
    elif not flags["elaborate"]:
        errors.append("Cadence xrun compile ran but elaboration did not run.")
    elif not flags["simulate"]:
        errors.append(
            "Cadence compile and elaborate ran but simulation did not run. "
            "Check the license checkout (NOLICN) and lab setup script."
        )
    elif not real_cadence:
        failed_steps = [
            step["label"]
            for step in (run_summary.get("steps") or [])
            if step.get("uses_xrun") and not step.get("passed")
        ]
        if failed_steps:
            errors.append(f"Cadence commands failed: {', '.join(failed_steps)}.")
        else:
            errors.append("Cadence simulation ran but the FIFO UVM test did not pass.")
    return {
        "status": "passed" if passed else "failed",
        "passed": passed,
        "method": "cadence_xrun_uvm_pipeline" if flags["compile"] else "static+uvm_tool_skipped",
        "checked_files": 4,
        "errors": errors,
        "warnings": list(runner_payload.get("warnings") or []),
        "simulator": "xcelium" if flags["compile"] else "",
        "details": {
            "phases": phases,
            "regression": runner_payload.get("regression") or {},
            "run_summary": run_summary.get("summary") or "",
            "xrun_invocations": run_summary.get("xrun_invocations") or 0,
        },
    }


def _outcome_from_runner(
    runner_payload: dict[str, Any],
    run_summary: dict[str, Any],
) -> tuple[str, str]:
    if run_summary.get("real_execution"):
        return (
            "passed",
            "Cadence verification passed — xrun compile, elaborate, and simulate ran on the FIFO fixture.",
        )

    flags = _pipeline_phase_flags(runner_payload.get("phases") or [])
    summary = run_summary.get("summary") or "No xrun commands executed."

    if flags["compile"] and not flags["simulate"]:
        return (
            "failed",
            f"FIFO verification stopped before simulation — {summary}",
        )
    if flags["simulate"] and not run_summary.get("real_execution"):
        return (
            "failed",
            f"Cadence commands ran but verification did not fully pass — {summary}",
        )
    if not flags["compile"]:
        return (
            "failed",
            "Cadence xrun did not run on the FIFO fixture. Check the lab setup script, license, and xrun path.",
        )
    return (
        "failed",
        f"Cadence FIFO verification failed — {summary}",
    )


def _run_integration_test_impl(
    *,
    work_dir: Path | None = None,
    timeout_seconds: int = 180,
) -> dict[str, Any]:
    """Run the FIFO fixture through detection + the production XceliumRunner path."""
    from simulator_plugins.xcelium import XceliumPlugin

    started = time.time()
    plugin = XceliumPlugin()
    detection = plugin.detect()
    detection_payload = detection.to_dict() if hasattr(detection, "to_dict") else asdict(detection)

    status = (detection_payload.get("details") or {}).get("status") or (
        "ready" if detection_payload.get("available") else "not_installed"
    )
    if status == "unsupported_platform":
        return _finalize(
            started,
            status="blocked",
            message="Cadence Xcelium requires a Linux host.",
            detection=detection_payload,
            fixture=None,
            compile_gate=None,
            runner=None,
            run_summary=None,
        )
    if not detection_payload.get("available"):
        guidance = detection_payload.get("guidance") or "xrun was not found."
        return _finalize(
            started,
            status="blocked",
            message=guidance,
            detection=detection_payload,
            fixture=None,
            compile_gate=None,
            runner=None,
            run_summary=None,
        )

    import tempfile

    root = work_dir or Path(tempfile.mkdtemp(prefix="cadence_fifo_probe_"))
    fixture = materialize_fifo_fixture(root)
    runner_payload = _run_fifo_xcelium_pipeline(
        fixture,
        root,
        timeout_seconds=timeout_seconds,
    )
    run_summary = _build_run_summary(runner_payload, work_dir=root)
    compile_payload = _compile_gate_from_runner(runner_payload, run_summary)
    outcome, message = _outcome_from_runner(runner_payload, run_summary)

    return _finalize(
        started,
        status=outcome,
        message=message,
        detection=detection_payload,
        fixture=fixture,
        compile_gate=compile_payload,
        runner=runner_payload,
        run_summary=run_summary,
        timeout_seconds=timeout_seconds,
    )


def _finalize(
    started: float,
    *,
    status: str,
    message: str,
    detection: dict[str, Any] | None,
    fixture: dict[str, str] | None,
    compile_gate: dict[str, Any] | None,
    runner: dict[str, Any] | None = None,
    run_summary: dict[str, Any] | None = None,
    timeout_seconds: int = 180,
) -> dict[str, Any]:
    runner_payload = runner or {}
    summary_payload = run_summary or {}
    phases = runner_payload.get("phases") or (compile_gate or {}).get("details", {}).get("phases") or []
    return {
        "status": status,
        "message": message,
        "fixture": (fixture or {}).get("fixture") or "fifo",
        "duration_ms": int((time.time() - started) * 1000),
        "timeout_seconds": timeout_seconds,
        "detection": detection or {},
        "fixture_paths": fixture or {},
        "compile_gate": compile_gate or {},
        "runner": runner_payload,
        "run_summary": summary_payload,
        "phases": phases,
    }
