from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import threading
import time
import uuid
from pathlib import Path

from common.paths import outputs_dir
from typing import Any, Iterator

from services.verilog_parser import parse_verilog

from .toolchain import detect_toolchain_status, get_toolchain_fingerprint
from .vcd import parse_vcd_file, slice_waveform_data

OUTPUTS_DIR = outputs_dir()
SIM_LOCK = threading.Lock()
SIM_JOBS: dict[str, dict[str, Any]] = {}


def _job_dir(simulation_id: str) -> Path:
    path = OUTPUTS_DIR / "eda-cache" / "simulations" / simulation_id
    path.mkdir(parents=True, exist_ok=True)
    return path


def _append_event(job: dict[str, Any], phase: str, message: str, level: str = "info") -> None:
    with SIM_LOCK:
        next_seq = int(job.get("next_seq", 1))
        event = {
            "seq_no": next_seq,
            "phase": phase,
            "level": level,
            "message": message,
            "timestamp": time.time(),
        }
        job.setdefault("events", []).append(event)
        job["next_seq"] = next_seq + 1
        job.setdefault("phases", []).append(phase)


def _generated_testbench(module_name: str, module_data: dict[str, Any]) -> str:
    ports = module_data.get("ports", [])
    inputs = [port for port in ports if port.get("direction") == "input"]
    outputs = [port for port in ports if port.get("direction") == "output"]
    decls = []
    conns = []
    stimulus = []
    has_clock = False
    for port in inputs:
        name = port["name"]
        width = port.get("width") or ""
        decls.append(f"reg {width} {name};".strip())
        conns.append(f".{name}({name})")
        if name.lower() in {"clk", "clock"}:
            has_clock = True
            stimulus.append(f"always #5 {name} = ~{name};")
    for port in outputs:
        name = port["name"]
        width = port.get("width") or ""
        decls.append(f"wire {width} {name};".strip())
        conns.append(f".{name}({name})")

    init_lines = ["initial begin"]
    for port in inputs:
        name = port["name"]
        if name.lower() in {"clk", "clock"}:
            init_lines.append(f"  {name} = 0;")
        elif "rst" in name.lower():
            init_lines.append(f"  {name} = 0;")
            init_lines.append("  #10;")
            init_lines.append(f"  {name} = 1;")
        else:
            init_lines.append(f"  {name} = 0;")
    init_lines.extend(
        [
            '  $dumpfile("dump.vcd");',
            f"  $dumpvars(0, dut);",
            "  #100;",
            "  $finish;",
            "end",
        ]
    )

    return "\n".join(
        [
            f"module tb_{module_name};",
            *decls,
            f"{module_name} dut({', '.join(conns)});",
            *stimulus,
            *init_lines,
            "endmodule",
        ]
    )


def _simulation_worker(job: dict[str, Any]) -> None:
    toolchain = detect_toolchain_status()
    verilator = toolchain["verilator"]
    if not verilator["available"]:
        job["status"] = "failed"
        job["diagnostics"].append(
            {
                "phase": "toolchain_detect",
                "level": "error",
                "message": verilator["guidance"],
            }
        )
        _append_event(job, "failed", verilator["guidance"], "error")
        return

    source = job["rtl_source"]
    filename = job["filename"]
    top_module = job["top_module"]
    job_dir = _job_dir(job["id"])
    rtl_path = job_dir / filename
    tb_path = job_dir / "tb_auto.sv"
    rtl_path.write_text(source, encoding="utf-8")
    _append_event(job, "parse_prep", "Prepared simulation workspace.")

    try:
        ast_modules = parse_verilog(rtl_path)
        module_data = ast_modules.get(top_module) or next(iter(ast_modules.values()))
        if job.get("testbench_source"):
            tb_source = job["testbench_source"]
        else:
            tb_source = _generated_testbench(top_module, module_data)
        tb_path.write_text(tb_source, encoding="utf-8")
    except Exception as exc:
        job["status"] = "failed"
        job["diagnostics"].append({"phase": "parse_prep", "level": "error", "message": str(exc)})
        _append_event(job, "failed", f"Unable to prepare simulation: {exc}", "error")
        return

    build_dir = job_dir / "obj_dir"
    compile_cmd = [
        verilator["path"],
        "--binary",
        "--trace-vcd",
        "--top-module",
        top_module,
        rtl_path.name,
        tb_path.name,
        "--Mdir",
        str(build_dir),
    ]
    _append_event(job, "compile", "Running Verilator compile step.")
    compile_proc = subprocess.run(
        compile_cmd,
        cwd=str(job_dir),
        capture_output=True,
        text=True,
        timeout=90,
        check=False,
    )
    if compile_proc.stdout.strip():
        job["diagnostics"].append({"phase": "compile", "level": "info", "message": compile_proc.stdout.strip()[:4000]})
    if compile_proc.stderr.strip():
        job["diagnostics"].append({"phase": "compile", "level": "warning", "message": compile_proc.stderr.strip()[:4000]})
    if compile_proc.returncode != 0:
        job["status"] = "failed"
        _append_event(job, "failed", "Verilator compilation failed.", "error")
        return

    executable = build_dir / f"V{top_module}"
    if os.name == "nt":
        executable = executable.with_suffix(".exe")
    if not executable.exists():
        candidates = sorted(build_dir.glob("V*"))
        executable = candidates[0] if candidates else executable
    _append_event(job, "simulate", "Executing compiled simulation.")
    run_proc = subprocess.run(
        [str(executable)],
        cwd=str(job_dir),
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    if run_proc.stdout.strip():
        job["diagnostics"].append({"phase": "simulate", "level": "info", "message": run_proc.stdout.strip()[:4000]})
    if run_proc.stderr.strip():
        job["diagnostics"].append({"phase": "simulate", "level": "warning", "message": run_proc.stderr.strip()[:4000]})
    if run_proc.returncode != 0:
        job["status"] = "failed"
        _append_event(job, "failed", "Simulation execution failed.", "error")
        return

    vcd_candidates = [job_dir / "dump.vcd", build_dir / "dump.vcd", *job_dir.glob("*.vcd"), *build_dir.glob("*.vcd")]
    vcd_path = next((candidate for candidate in vcd_candidates if candidate.exists()), None)
    if not vcd_path:
        job["status"] = "failed"
        _append_event(job, "failed", "Simulation completed without a VCD trace.", "error")
        return

    _append_event(job, "waveform_parse", "Parsing VCD waveform data.")
    waveform = parse_vcd_file(vcd_path)
    waveform_path = job_dir / "waveform.json"
    waveform_path.write_text(json.dumps(waveform), encoding="utf-8")
    job["artifacts"] = {
        "vcd_path": str(vcd_path),
        "waveform_path": str(waveform_path),
    }
    job["signal_index"] = waveform.get("signal_index", [])
    job["status"] = "completed"
    _append_event(job, "completed", "Simulation finished successfully.")


def create_simulation_job(
    *,
    project_id: str,
    rtl_source: str,
    filename: str,
    top_module: str,
    testbench_source: str | None = None,
    trace_format: str = "vcd",
) -> dict[str, Any]:
    simulation_id = str(uuid.uuid4())
    toolchain = detect_toolchain_status()
    warnings = []
    if not toolchain["verilator"]["available"]:
        warnings.append(toolchain["verilator"]["guidance"])

    job = {
        "id": simulation_id,
        "project_id": project_id,
        "status": "queued",
        "rtl_source": rtl_source,
        "filename": filename,
        "top_module": top_module,
        "testbench_source": testbench_source,
        "trace_format": trace_format,
        "events": [],
        "next_seq": 1,
        "diagnostics": [],
        "artifacts": {},
        "signal_index": [],
        "toolchain_fingerprint": get_toolchain_fingerprint(),
        "warnings": warnings,
        "created_at": time.time(),
    }
    with SIM_LOCK:
        SIM_JOBS[simulation_id] = job

    _append_event(job, "toolchain_detect", "Simulation job created.")
    worker = threading.Thread(target=_simulation_worker, args=(job,), daemon=True)
    worker.start()
    return {
        "simulation_id": simulation_id,
        "status": job["status"],
        "warnings": warnings,
    }


def get_simulation_job(simulation_id: str) -> dict[str, Any] | None:
    with SIM_LOCK:
        job = SIM_JOBS.get(simulation_id)
        if not job:
            return None
        return {
            "id": job["id"],
            "project_id": job["project_id"],
            "status": job["status"],
            "phases": list(dict.fromkeys(job.get("phases", []))),
            "diagnostics": list(job.get("diagnostics", [])),
            "artifacts": dict(job.get("artifacts", {})),
            "signal_index": list(job.get("signal_index", [])),
            "warnings": list(job.get("warnings", [])),
        }


def iter_simulation_events(simulation_id: str, after_seq: int = 0) -> Iterator[dict[str, Any]]:
    last_seq = after_seq
    while True:
        with SIM_LOCK:
            job = SIM_JOBS.get(simulation_id)
            if not job:
                yield {"type": "end", "status": "not_found", "last_seq": last_seq}
                return
            pending = [event for event in job.get("events", []) if event["seq_no"] > last_seq]
            job_status = job["status"]

        for event in pending:
            last_seq = event["seq_no"]
            yield {"type": "simulation_event", "event": event}

        if job_status in {"completed", "failed"}:
            yield {"type": "end", "status": job_status, "last_seq": last_seq}
            return

        time.sleep(0.25)


def read_waveform_slice(
    simulation_id: str,
    *,
    signals: list[str] | None = None,
    start_time: int | None = None,
    end_time: int | None = None,
) -> dict[str, Any]:
    with SIM_LOCK:
        job = SIM_JOBS.get(simulation_id)
        if not job:
            raise FileNotFoundError("Simulation job not found")
        waveform_path = job.get("artifacts", {}).get("waveform_path")
        vcd_path = job.get("artifacts", {}).get("vcd_path")

    if not waveform_path:
        raise FileNotFoundError("Waveform data is not available for this simulation")

    waveform = json.loads(Path(waveform_path).read_text(encoding="utf-8"))
    sliced = slice_waveform_data(
        waveform,
        signals=signals,
        start_time=start_time,
        end_time=end_time,
    )
    sliced["raw_vcd_path"] = vcd_path
    return sliced
