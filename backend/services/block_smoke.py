from __future__ import annotations

import json
import os
import re
import subprocess
import time
from pathlib import Path
from typing import Any, Callable

from services.eda.toolchain import detect_toolchain_status
from services.rtl_project_package import RTL_SOURCE_SUFFIXES
from services.verilog_parser import parse_verilog

EventCallback = Callable[[str, str, str], None]


def run_block_smoke_verification(
    *,
    rtl_path: str | Path,
    output_dir: str | Path,
    mental_model: dict[str, Any] | None = None,
    event_callback: EventCallback | None = None,
) -> dict[str, Any]:
    """Run a conservative block-level smoke compile/sim flow.

    This is the first industry-grade execution slice: it compiles the original
    DUT RTL together with a generated testbench. If required tools are missing,
    the result is explicitly `blocked`, never a fake pass.
    """

    started_at = time.time()
    root = Path(rtl_path)
    output = Path(output_dir) / "unit_sim"
    output.mkdir(parents=True, exist_ok=True)

    def emit(phase: str, message: str, level: str = "info") -> None:
        if event_callback:
            event_callback(phase, message, level)

    emit("unit_sim.start", "Starting block-level UnitSim smoke verification.")
    source_files = _collect_rtl_files(root)
    if not source_files:
        return _finalize(
            output,
            {
                "status": "blocked",
                "reason": "no_rtl_sources",
                "message": "No Verilog/SystemVerilog RTL source files were found.",
                "artifacts": {},
            },
            started_at,
            emit,
        )

    toolchain = detect_toolchain_status()
    iverilog = toolchain.get("iverilog") or {}
    vvp = toolchain.get("vvp") or {}
    missing = [
        tool.get("name")
        for tool in (iverilog, vvp)
        if not tool.get("available")
    ]
    if missing:
        guidance = [
            tool.get("guidance")
            for tool in (iverilog, vvp)
            if not tool.get("available") and tool.get("guidance")
        ]
        return _finalize(
            output,
            {
                "status": "blocked",
                "reason": "blocked_tool_missing",
                "missing_tools": missing,
                "message": "Block smoke verification needs Icarus Verilog compile/runtime tools.",
                "guidance": guidance,
                "artifacts": {},
            },
            started_at,
            emit,
        )

    emit("unit_sim.parse", f"Parsing {len(source_files)} RTL source files.")
    modules, parser_diagnostics = _parse_project_modules(source_files)
    top_module = _resolve_top_module(mental_model or {}, modules)
    if not top_module or top_module not in modules:
        return _finalize(
            output,
            {
                "status": "blocked",
                "reason": "top_module_unknown",
                "message": "Unable to identify a DUT top module for block smoke verification.",
                "parser_diagnostics": parser_diagnostics,
                "artifacts": {},
            },
            started_at,
            emit,
        )

    tb_name = f"tb_{top_module}"
    testbench_path = output / "tb_block_smoke.sv"
    filelist_path = output / "sources.f"
    executable_path = output / "block_smoke.vvp"
    vcd_path = output / "block_smoke.vcd"
    testbench_path.write_text(
        _generated_testbench(tb_name, top_module, modules[top_module], vcd_path.name),
        encoding="utf-8",
    )
    filelist_path.write_text(
        "\n".join(
            [_filelist_path(path, output) for path in source_files]
            + [_filelist_path(testbench_path, output)]
        )
        + "\n",
        encoding="utf-8",
    )

    compile_cmd = [
        str(iverilog["path"]),
        "-g2012",
        "-s",
        tb_name,
        "-o",
        str(executable_path.resolve()),
        "-f",
        str(filelist_path.resolve()),
    ]
    emit("unit_sim.compile", f"Compiling DUT {top_module} plus generated smoke testbench.")
    compile_proc = subprocess.run(
        compile_cmd,
        cwd=str(output),
        capture_output=True,
        text=True,
        timeout=90,
        check=False,
    )
    _write_process_logs(output, "compile", compile_proc)
    if compile_proc.returncode != 0:
        return _finalize(
            output,
            {
                "status": "failed",
                "reason": "compile_failed",
                "message": "DUT plus generated smoke testbench did not compile.",
                "top_module": top_module,
                "command": compile_cmd,
                "stdout": compile_proc.stdout[-4000:],
                "stderr": compile_proc.stderr[-4000:],
                "parser_diagnostics": parser_diagnostics,
                "artifacts": {
                    "testbench": str(testbench_path),
                    "filelist": str(filelist_path),
                },
            },
            started_at,
            emit,
        )

    run_cmd = [str(vvp["path"]), str(executable_path.resolve())]
    emit("unit_sim.simulate", "Executing block smoke simulation.")
    run_proc = subprocess.run(
        run_cmd,
        cwd=str(output),
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    _write_process_logs(output, "simulate", run_proc)
    if run_proc.returncode != 0:
        return _finalize(
            output,
            {
                "status": "failed",
                "reason": "simulation_failed",
                "message": "Compiled block smoke simulation returned a non-zero exit code.",
                "top_module": top_module,
                "command": run_cmd,
                "stdout": run_proc.stdout[-4000:],
                "stderr": run_proc.stderr[-4000:],
                "artifacts": {
                    "testbench": str(testbench_path),
                    "filelist": str(filelist_path),
                    "executable": str(executable_path),
                },
            },
            started_at,
            emit,
        )

    artifacts = {
        "testbench": str(testbench_path),
        "filelist": str(filelist_path),
        "executable": str(executable_path),
    }
    if vcd_path.exists():
        artifacts["vcd"] = str(vcd_path)

    return _finalize(
        output,
        {
            "status": "completed",
            "reason": "smoke_passed",
            "message": "Block smoke verification compiled and simulated the DUT with a generated testbench.",
            "top_module": top_module,
            "source_file_count": len(source_files),
            "parser_diagnostics": parser_diagnostics,
            "artifacts": artifacts,
        },
        started_at,
        emit,
    )


def _collect_rtl_files(root: Path) -> list[Path]:
    if root.is_file() and root.suffix.lower() in RTL_SOURCE_SUFFIXES:
        return [root]
    if not root.is_dir():
        return []
    return sorted(
        path
        for path in root.rglob("*")
        if path.is_file() and path.suffix.lower() in RTL_SOURCE_SUFFIXES
    )


def _parse_project_modules(source_files: list[Path]) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    modules: dict[str, dict[str, Any]] = {}
    diagnostics: list[dict[str, Any]] = []
    for path in source_files:
        try:
            parsed, metadata = parse_verilog(path, return_metadata=True)
            for module_name, module_data in parsed.items():
                module_data = dict(module_data)
                module_data["source_path"] = str(path)
                if not module_data.get("ports"):
                    module_data["ports"] = _extract_ansi_ports(path, module_name)
                modules[module_name] = module_data
            if metadata.get("errors"):
                diagnostics.append(
                    {
                        "file": str(path),
                        "parser_backend": metadata.get("parser_backend"),
                        "errors": metadata.get("errors"),
                    }
                )
        except Exception as exc:
            diagnostics.append({"file": str(path), "errors": [str(exc)]})
    return modules, diagnostics


def _resolve_top_module(
    mental_model: dict[str, Any], modules: dict[str, dict[str, Any]]
) -> str | None:
    design = mental_model.get("design") if isinstance(mental_model, dict) else {}
    hinted = str((design or {}).get("top_module") or "").strip()
    if hinted in modules:
        return hinted

    instantiated = {
        str(instance.get("module_type"))
        for module in modules.values()
        for instance in module.get("instances") or []
        if instance.get("module_type")
    }
    candidates = [name for name in modules if name not in instantiated]
    if candidates:
        return candidates[0]
    return next(iter(modules.keys()), None)


def _generated_testbench(
    tb_name: str,
    top_module: str,
    module_data: dict[str, Any],
    vcd_name: str,
) -> str:
    ports = module_data.get("ports") or []
    declarations = []
    connections = []
    clock_lines = []
    init_lines = ["initial begin"]
    clock_name = ""
    input_names: list[str] = []
    output_names: list[str] = []
    declarations.append("  integer smoke_errors;")
    init_lines.append("  smoke_errors = 0;")

    for port in ports:
        name = str(port.get("name") or "").strip()
        if not name:
            continue
        direction = str(port.get("direction") or "input").lower()
        width = _clean_width(str(port.get("width") or ""))
        decl_type = "wire" if direction in {"output", "inout"} else "reg"
        declarations.append(f"  {decl_type} {width} {name};".replace("  ;", ";"))
        connections.append(f".{name}({name})")
        if direction in {"output", "inout"}:
            output_names.append(name)

        lower = name.lower()
        if direction == "input" and _is_clock(lower):
            clock_name = name
            init_lines.append(f"  {name} = 0;")
            clock_lines.append(f"  always #5 {name} = ~{name};")
        elif direction == "input" and _is_reset(lower):
            active_low = _reset_is_active_low(name, module_data)
            init_lines.append(f"  {name} = {'0' if active_low else '1'};")
            init_lines.append("  #20;")
            init_lines.append(f"  {name} = {'1' if active_low else '0'};")
        elif direction == "input":
            input_names.append(name)
            init_lines.append(f"  {name} = '0;")

    init_lines.extend(
        [
            f'  $dumpfile("{vcd_name}");',
            "  $dumpvars(0, dut);",
        ]
    )
    init_lines.extend(_generic_stimulus_lines(input_names, output_names, clock_name))
    init_lines.extend(
        [
            "  #120;",
            "  if (smoke_errors == 0) begin",
            '    $display("BLOCK_SMOKE_PASS");',
            "  end else begin",
            '    $display("BLOCK_SMOKE_FAIL_ERRORS=%0d", smoke_errors);',
            "    $fatal(1);",
            "  end",
            "  $finish;",
            "end",
        ]
    )
    instance = f"  {top_module} dut({', '.join(connections)});"
    if not connections:
        instance = f"  {top_module} dut();"

    return "\n".join(
        [
            "`timescale 1ns/1ps",
            f"module {tb_name};",
            *declarations,
            instance,
            *clock_lines,
            *init_lines,
            "endmodule",
            "",
        ]
    )


def _extract_ansi_ports(path: Path, module_name: str) -> list[dict[str, str]]:
    text = path.read_text(encoding="utf-8", errors="replace")
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    text = re.sub(r"//.*", "", text)
    pattern = re.compile(
        rf"\bmodule\s+{re.escape(module_name)}\s*(?:#\s*\(.*?\)\s*)?\((?P<ports>.*?)\)\s*;",
        re.S,
    )
    match = pattern.search(text)
    if not match:
        return []

    ports: list[dict[str, str]] = []
    current_direction = "input"
    current_width = ""
    for item in _split_port_items(match.group("ports")):
        raw = " ".join(item.strip().split())
        if not raw:
            continue

        direction_seen = False
        direction_match = re.match(r"^(input|output|inout)\b\s*(.*)$", raw, re.I)
        if direction_match:
            direction_seen = True
            current_direction = direction_match.group(1).lower()
            raw = direction_match.group(2).strip()

        raw = re.sub(r"\b(wire|reg|logic|bit|signed|unsigned)\b", " ", raw, flags=re.I)
        raw = " ".join(raw.split())
        width_match = re.search(r"(\[[^\]]+\])", raw)
        width = width_match.group(1) if width_match else ("" if direction_seen else current_width)
        if width_match:
            current_width = width
            raw = raw.replace(width, " ")
        elif direction_seen:
            current_width = ""

        raw = raw.split("=", 1)[0].strip()
        name_match = re.search(r"([A-Za-z_][A-Za-z0-9_$]*)\s*(?:\[[^\]]+\])?\s*$", raw)
        if not name_match:
            continue
        ports.append(
            {
                "name": name_match.group(1),
                "direction": current_direction,
                "width": width,
            }
        )
    return ports


def _split_port_items(raw_ports: str) -> list[str]:
    items: list[str] = []
    start = 0
    depth = 0
    for index, char in enumerate(raw_ports):
        if char in "([":
            depth += 1
        elif char in ")]" and depth > 0:
            depth -= 1
        elif char == "," and depth == 0:
            items.append(raw_ports[start:index])
            start = index + 1
    items.append(raw_ports[start:])
    return items


def _clean_width(width: str) -> str:
    width = " ".join(width.split())
    return f"{width} " if width else ""


def _is_clock(lower_name: str) -> bool:
    return lower_name in {"clk", "clock"} or lower_name.endswith("_clk")


def _is_reset(lower_name: str) -> bool:
    return "rst" in lower_name or "reset" in lower_name


def _reset_is_active_low(name: str, module_data: dict[str, Any]) -> bool:
    lower = name.lower()
    if lower.endswith("_n") or lower.endswith("n"):
        return True
    source_path = module_data.get("source_path")
    if source_path:
        text = Path(str(source_path)).read_text(encoding="utf-8", errors="replace")
        if re.search(rf"\bnegedge\s+{re.escape(name)}\b", text):
            return True
        if re.search(rf"\bif\s*\(\s*!\s*{re.escape(name)}\s*\)", text):
            return True
    return False


def _generic_stimulus_lines(
    input_names: list[str], output_names: list[str], clock_name: str
) -> list[str]:
    if not input_names:
        return []

    by_lower = {name.lower(): name for name in input_names}
    outputs_by_lower = {name.lower(): name for name in output_names}
    write_name = _first_matching_name(
        by_lower, ("wr", "wen", "write", "push", "valid_in", "in_valid")
    )
    read_name = _first_matching_name(
        by_lower, ("rd", "ren", "read", "pop", "ready_in", "out_ready")
    )
    data_name = _first_matching_name(
        by_lower, ("data_in", "din", "wdata", "wr_data", "write_data", "in_data")
    )
    data_out_name = _first_matching_name(
        outputs_by_lower, ("data_out", "dout", "rdata", "rd_data", "read_data", "out_data")
    )
    empty_name = _first_matching_name(outputs_by_lower, ("empty",))
    full_name = _first_matching_name(outputs_by_lower, ("full",))
    count_name = _first_matching_name(
        outputs_by_lower, ("fifo_cnt", "fifo_count", "count", "cnt", "level")
    )
    tick = f"@(posedge {clock_name});" if clock_name else "#10;"

    lines = ["  repeat (2) " + tick, "  #1;"]
    if empty_name:
        lines.extend(_check_lines(f"{empty_name} === 1'b1", "reset_empty_should_be_1"))
    if full_name:
        lines.extend(_check_lines(f"{full_name} === 1'b0", "reset_full_should_be_0"))
    if count_name:
        lines.extend(_check_lines(f"{count_name} == 0", "reset_count_should_be_0"))
    if data_name and write_name:
        lines.extend(
            [
                f"  {data_name} = 'hA5;",
                f"  {write_name} = 1'b1;",
                f"  {tick}",
                "  #1;",
            ]
        )
        if empty_name:
            lines.extend(_check_lines(f"{empty_name} === 1'b0", "after_write_empty_should_be_0"))
        if full_name:
            lines.extend(_check_lines(f"{full_name} === 1'b0", "after_one_write_full_should_be_0"))
        if count_name:
            lines.extend(_check_lines(f"{count_name} == 1", "after_one_write_count_should_be_1"))
        lines.extend([f"  {write_name} = 1'b0;", f"  {tick}", "  #1;"])
    if data_name and write_name:
        lines.extend(
            [
                f"  {data_name} = 'h3C;",
                f"  {write_name} = 1'b1;",
                f"  {tick}",
                "  #1;",
            ]
        )
        if count_name:
            lines.extend(_check_lines(f"{count_name} == 2", "after_two_writes_count_should_be_2"))
        lines.extend(
            [
                f"  {write_name} = 1'b0;",
                f"  {tick}",
                "  #1;",
            ]
        )
    if read_name:
        lines.extend(
            [
                f"  {read_name} = 1'b1;",
                f"  {tick}",
                "  #1;",
            ]
        )
        if data_out_name:
            lines.extend(_check_lines(f"{data_out_name} == 'hA5", "first_read_data_should_match_first_write"))
        if count_name:
            lines.extend(_check_lines(f"{count_name} == 1", "after_one_read_count_should_be_1"))
        lines.extend(
            [
                f"  {read_name} = 1'b0;",
                f"  repeat (2) {tick}",
                "  #1;",
            ]
        )
    return lines


def _check_lines(condition: str, label: str) -> list[str]:
    return [
        f"  if (!({condition})) begin",
        f'    $display("BLOCK_SMOKE_FAIL:{label}");',
        "    smoke_errors = smoke_errors + 1;",
        "  end",
    ]


def _first_matching_name(by_lower: dict[str, str], candidates: tuple[str, ...]) -> str:
    for candidate in candidates:
        if candidate in by_lower:
            return by_lower[candidate]
    for lower, name in by_lower.items():
        if any(candidate in lower for candidate in candidates):
            return name
    return ""


def _tool_path(path: Path) -> str:
    return path.resolve().as_posix()


def _filelist_path(path: Path, base_dir: Path) -> str:
    # Icarus filelists on Windows do not reliably parse drive-qualified C:/ paths.
    return os.path.relpath(path.resolve(), base_dir.resolve()).replace("\\", "/")


def _write_process_logs(output: Path, label: str, proc: subprocess.CompletedProcess[str]) -> None:
    (output / f"{label}.stdout.log").write_text(proc.stdout or "", encoding="utf-8")
    (output / f"{label}.stderr.log").write_text(proc.stderr or "", encoding="utf-8")


def _finalize(
    output: Path,
    result: dict[str, Any],
    started_at: float,
    emit: EventCallback,
) -> dict[str, Any]:
    result["elapsed_seconds"] = round(time.time() - started_at, 3)
    report_path = output / "block_smoke_report.json"
    result.setdefault("artifacts", {})["report"] = str(report_path)
    report_path.write_text(json.dumps(result, indent=2), encoding="utf-8")

    status = result.get("status") or "failed"
    level = "error" if status in {"failed", "blocked"} else "info"
    emit(f"unit_sim.{status}", str(result.get("message") or status), level)
    return result
