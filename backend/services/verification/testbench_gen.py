"""
Testbench Generator — Converts test plans into complete SV testbenches.

Follows the 7 mandatory components from UnitSim research:
  1. Signal declarations (from symbol table)
  2. DUT instantiation (with all parameters and ports)
  3. Clock generation (correct frequency)
  4. Reset sequence (correct polarity)
  5. Test scenarios (from test plan)
  6. Self-checking (assert statements)
  7. Watchdog timer (prevent infinite sim)
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional

from services.verification.test_plan import TestPlan, TestScenario, TestCheck

logger = logging.getLogger(__name__)


@dataclass
class GeneratedTestbench:
    """A generated testbench file."""
    filename: str
    content: str
    target_module: str
    test_count: int = 0


def generate_testbench_from_plan(
    plan: TestPlan,
    design: Any,
    clock_period_ns: int = 10,
) -> GeneratedTestbench:
    """
    Generate a complete SV testbench from a test plan + design block.

    Args:
        plan: Test plan with scenarios
        design: DesignBlock from mental model
        clock_period_ns: Clock period
    """
    module_name = plan.module_name
    tb_name = f"tb_{module_name}"
    half_period = clock_period_ns // 2

    # Detect clock/reset
    clk_name, rst_name, rst_active_low = _detect_clock_reset(design)

    # Classify ports
    inputs = [p for p in design.ports if p.direction == "input"]
    outputs = [p for p in design.ports if p.direction == "output"]
    inouts = [p for p in design.ports if p.direction == "inout"]
    stim_inputs = [p for p in inputs if p.name not in (clk_name, rst_name)]
    tb_param_values = _tb_parameter_values(design)

    lines: List[str] = []

    # ── Header ─────────────────────────────────────────────────
    lines.append(f"// ═══════════════════════════════════════════════════════")
    lines.append(f"// Testbench: {tb_name}")
    lines.append(f"// DUT:       {module_name}")
    lines.append(f"// Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"// Tool:      ChipVerify AI — UnitSim Agent")
    lines.append(f"// Tests:     {len(plan.scenarios)} scenarios")
    lines.append(f"// ═══════════════════════════════════════════════════════")
    lines.append("`timescale 1ns/1ps")
    lines.append("")
    lines.append(f"module {tb_name};")
    lines.append("")

    # ── 1. Signal Declarations ─────────────────────────────────
    lines.append("  // ── Signal Declarations ──────────────────────────────")
    lines.append(f"  logic {clk_name};")
    lines.append(f"  logic {rst_name};")
    lines.append("")

    if stim_inputs:
        lines.append("  // DUT Inputs")
        for p in stim_inputs:
            bus_range = _tb_bus_range(p, tb_param_values)
            br = f" {bus_range}" if bus_range else ""
            lines.append(f"  logic{br} {p.name};")
        lines.append("")

    if outputs:
        lines.append("  // DUT Outputs")
        for p in outputs:
            bus_range = _tb_bus_range(p, tb_param_values)
            br = f" {bus_range}" if bus_range else ""
            lines.append(f"  logic{br} {p.name};")
        lines.append("")

    if inouts:
        lines.append("  // DUT Bidirectional")
        for p in inouts:
            bus_range = _tb_bus_range(p, tb_param_values)
            br = f" {bus_range}" if bus_range else ""
            lines.append(f"  wire{br} {p.name};")
        lines.append("")

    # Test result tracking
    lines.append("  // Test result tracking")
    lines.append("  int test_count = 0;")
    lines.append("  int pass_count = 0;")
    lines.append("  int fail_count = 0;")
    lines.append("  string current_test;")
    lines.append("")

    # ── 2. DUT Instantiation ───────────────────────────────────
    lines.append("  // ── DUT Instantiation ────────────────────────────────")
    param_str = ""
    if design.parameters:
        params = ", ".join(f".{p.name}({p.name})" for p in design.parameters)
        param_str = f" #({params})"
        # Parameter declarations
        for p in design.parameters:
            val = p.default_value or "0"
            lines.insert(-1, f"  parameter {p.name} = {val};")

    all_ports = inputs + outputs + inouts
    lines.append(f"  {module_name}{param_str} dut (")
    for i, p in enumerate(all_ports):
        comma = "," if i < len(all_ports) - 1 else ""
        lines.append(f"    .{p.name}({p.name}){comma}")
    lines.append("  );")
    lines.append("")

    # ── 3. Clock Generation ────────────────────────────────────
    lines.append("  // ── Clock Generation ─────────────────────────────────")
    lines.append(f"  initial {clk_name} = 0;")
    lines.append(f"  always #{half_period} {clk_name} = ~{clk_name};")
    lines.append("")

    # ── 4. Reset Sequence ──────────────────────────────────────
    lines.append("  // ── Reset Task ───────────────────────────────────────")
    lines.append("  task automatic apply_reset();")
    if rst_active_low:
        lines.append(f"    {rst_name} = 1'b0;")
        lines.append(f"    repeat(5) @(posedge {clk_name});")
        lines.append(f"    {rst_name} = 1'b1;")
    else:
        lines.append(f"    {rst_name} = 1'b1;")
        lines.append(f"    repeat(5) @(posedge {clk_name});")
        lines.append(f"    {rst_name} = 1'b0;")
    lines.append(f"    @(posedge {clk_name});")
    lines.append("  endtask")
    lines.append("")

    # ── Utility Tasks ──────────────────────────────────────────
    lines.append("  // ── Utility Tasks ────────────────────────────────────")
    lines.append("  task automatic wait_clocks(int n);")
    lines.append(f"    repeat(n) @(posedge {clk_name});")
    lines.append("  endtask")
    lines.append("")

    lines.append("  task automatic check(string name, logic condition);")
    lines.append("    test_count++;")
    lines.append("    if (condition) begin")
    lines.append('      $display("  [PASS] %s: %s", current_test, name);')
    lines.append("      pass_count++;")
    lines.append("    end else begin")
    lines.append('      $display("  [FAIL] %s: %s", current_test, name);')
    lines.append("      fail_count++;")
    lines.append("    end")
    lines.append("  endtask")
    lines.append("")

    lines.append("  task automatic init_inputs();")
    for p in stim_inputs:
        lines.append(f"    {p.name} = '0;")
    lines.append("  endtask")
    lines.append("")

    # ── 5. Test Scenario Tasks ─────────────────────────────────
    lines.append("  // ── Test Scenarios ───────────────────────────────────")
    for scenario in plan.scenarios:
        task_lines = _gen_scenario_task(
            scenario, stim_inputs, outputs, clk_name, rst_name, rst_active_low,
        )
        lines.extend(task_lines)
        lines.append("")

    # ── 6. Main Test Sequence ──────────────────────────────────
    lines.append("  // ── Main Test Sequence ───────────────────────────────")
    lines.append("  initial begin")
    lines.append(f'    $dumpfile("{tb_name}.vcd");')
    lines.append(f"    $dumpvars(0, {tb_name});")
    lines.append("")
    lines.append('    $display("");')
    lines.append(f'    $display("╔═══════════════════════════════════════════╗");')
    lines.append(f'    $display("║  UnitSim: {module_name:<32s}║");')
    lines.append(f'    $display("║  Scenarios: {len(plan.scenarios):<30d}║");')
    lines.append(f'    $display("╚═══════════════════════════════════════════╝");')
    lines.append('    $display("");')
    lines.append("")
    lines.append("    init_inputs();")
    lines.append("")

    for scenario in plan.scenarios:
        lines.append(f"    // {scenario.description[:60]}")
        if scenario.precondition == "after_reset":
            lines.append("    apply_reset();")
        lines.append(f"    {scenario.name}();")
        lines.append("    wait_clocks(5);")
        lines.append("")

    # ── Summary ────────────────────────────────────────────────
    lines.append('    $display("");')
    lines.append(f'    $display("╔═══════════════════════════════════════════╗");')
    lines.append('    $display("║  RESULTS: %0d / %0d passed %s", pass_count, test_count,')
    lines.append('            (fail_count == 0) ? "  ✓" : "  ✗");')
    lines.append("    if (fail_count > 0)")
    lines.append('      $display("║  FAILURES: %0d", fail_count);')
    lines.append(f'    $display("╚═══════════════════════════════════════════╝");')
    lines.append('    $display("");')
    lines.append("")
    lines.append("    if (fail_count > 0)")
    lines.append("      $fatal(1, \"Test failures detected\");")
    lines.append("    $finish;")
    lines.append("  end")
    lines.append("")

    # ── 7. Watchdog Timer ──────────────────────────────────────
    lines.append("  // ── Watchdog Timer ───────────────────────────────────")
    lines.append("  initial begin")
    lines.append("    #1_000_000;  // 1ms timeout")
    lines.append('    $display("[TIMEOUT] Simulation exceeded 1ms");')
    lines.append("    $fatal(1, \"Timeout\");")
    lines.append("  end")
    lines.append("")
    lines.append("endmodule")

    content = "\n".join(lines) + "\n"

    return GeneratedTestbench(
        filename=f"{tb_name}.sv",
        content=content,
        target_module=module_name,
        test_count=len(plan.scenarios),
    )


# ═══════════════════════════════════════════════════════════════════════
# Private — Generate individual test scenario tasks
# ═══════════════════════════════════════════════════════════════════════


def _gen_scenario_task(
    scenario: TestScenario,
    stim_inputs: List[Any],
    outputs: List[Any],
    clk_name: str,
    rst_name: str,
    rst_active_low: bool,
) -> List[str]:
    """Generate a SV task for a single test scenario."""
    lines = []
    lines.append(f"  // Scenario: {scenario.id} — {scenario.description[:60]}")
    lines.append(f"  // Priority: {scenario.priority} | Category: {scenario.category}")
    lines.append(f"  task automatic {scenario.name}();")
    lines.append(f'    current_test = "{scenario.name}";')
    lines.append(f'    $display("── %s ──", current_test);')

    # Generate stimulus based on category
    if scenario.category == "reset":
        _gen_reset_stimulus(lines, outputs, rst_name, rst_active_low, clk_name, scenario.checks)
    elif scenario.category == "boundary":
        _gen_boundary_stimulus(lines, scenario, stim_inputs, outputs, clk_name)
    elif scenario.category == "stress":
        _gen_stress_stimulus(lines, stim_inputs, outputs, clk_name)
    else:
        _gen_functional_stimulus(lines, scenario, stim_inputs, outputs, clk_name)

    lines.append("  endtask")
    return lines


def _gen_reset_stimulus(lines, outputs, rst_name, rst_active_low, clk_name, checks):
    """Generate reset test body."""
    rst_assert = "1'b0" if rst_active_low else "1'b1"
    rst_release = "1'b1" if rst_active_low else "1'b0"

    lines.append(f"    {rst_name} = {rst_assert};")
    lines.append(f"    repeat(5) @(posedge {clk_name});")

    # Check outputs at reset
    for check in checks[:10]:
        lines.append(
            f'    check("{_sv_string(check.signal)} at reset", '
            f'{check.signal} === {_sv_literal(check.expected)});'
        )

    if not checks and outputs:
        for o in outputs[:6]:
            lines.append(f'    check("{o.name} at reset", {o.name} === \'0);')

    lines.append(f"    {rst_name} = {rst_release};")
    lines.append(f"    @(posedge {clk_name});")


def _gen_boundary_stimulus(lines, scenario, stim_inputs, outputs, clk_name):
    """Generate boundary value test body."""
    for step in scenario.stimulus:
        if step.signal and step.value:
            lines.append(f"    {step.signal} = {_sv_literal(step.value)};  // {step.description}")
            lines.append(f"    @(posedge {clk_name});")
        elif step.action == "wait":
            lines.append(f"    wait_clocks({step.cycles});")
    _gen_known_output_checks(lines, outputs, "Boundary outputs remain known")


def _gen_stress_stimulus(lines, stim_inputs, outputs, clk_name):
    """Generate stress test body."""
    lines.append("    // Rapid random toggle for 100 cycles")
    lines.append("    for (int cycle = 0; cycle < 100; cycle++) begin")
    for p in stim_inputs[:4]:
        lines.append(f"      {p.name} = $urandom;")
    lines.append(f"      @(posedge {clk_name});")
    lines.append("    end")
    _gen_known_output_checks(lines, outputs, "Stress outputs remain known")


def _gen_functional_stimulus(lines, scenario, stim_inputs, outputs, clk_name):
    """Generate functional test body."""
    # Use stimulus steps if available
    if scenario.stimulus:
        for step in scenario.stimulus:
            if step.action == "set" and step.signal:
                lines.append(f"    {step.signal} = {_sv_literal(step.value)};  // {step.description}")
            elif step.action == "toggle" and step.signal:
                lines.append(f"    {step.signal} = ~{step.signal};  // {step.description}")
            elif step.action == "wait":
                lines.append(f"    wait_clocks({step.cycles});")
            elif step.action == "assert":
                lines.append(f"    // {step.description}")
    else:
        # Default: toggle first input, wait, check
        if stim_inputs:
            inp = stim_inputs[0]
            lines.append(f"    {inp.name} = 1;")
            lines.append(f"    wait_clocks(10);")

    # Add checks
    for check in scenario.checks[:10]:
        lines.append(
            f'    check("{_sv_string(check.description or check.signal)}", '
            f'{check.signal} {check.condition} {_sv_literal(check.expected)});'
        )

    if not scenario.checks:
        _gen_known_output_checks(lines, outputs, f"{scenario.id} outputs remain known")


def _gen_known_output_checks(lines, outputs, label: str):
    """Emit a non-placeholder sanity check for observable outputs."""
    sampled = [o.name for o in outputs[:8] if getattr(o, "name", "")]
    if not sampled:
        lines.append(f'    $display("{_sv_string(label)}: no observable outputs in mental model");')
        return
    expr = sampled[0] if len(sampled) == 1 else "{" + ", ".join(sampled) + "}"
    lines.append(f'    check("{_sv_string(label)}", !$isunknown({expr}));')


def _tb_parameter_values(design: Any) -> Dict[str, int]:
    """Collect numeric parameter values and conservative derived width defaults."""
    values: Dict[str, int] = {}
    for param in getattr(design, "parameters", []) or []:
        name = str(getattr(param, "name", "") or "").strip()
        value = _parse_int_literal(getattr(param, "default_value", ""))
        if name and value is not None:
            values[name] = value

    for port in getattr(design, "ports", []) or []:
        bus_range = str(getattr(port, "bus_range", "") or "")
        for symbol in re.findall(r"\b[A-Za-z_]\w*\b", bus_range):
            if symbol not in values:
                inferred = _infer_width_symbol(symbol, port, values)
                if inferred is not None:
                    values[symbol] = inferred
    return values


def _tb_bus_range(port: Any, param_values: Optional[Dict[str, int]] = None) -> str:
    """Return a self-contained numeric range for TB-local signals.

    DUT port ranges may be symbolic, for example ``[ALGN_SIZE_WIDTH-1:0]``.
    Those symbols are not automatically visible in the testbench scope, so
    TB declarations use the resolved mental-model width instead.
    """
    bus_range = str(getattr(port, "bus_range", "") or "").strip()
    resolved_width = _resolve_bus_width(bus_range, param_values or {})
    if resolved_width and resolved_width > 1:
        return f"[{resolved_width - 1}:0]"

    try:
        width = int(getattr(port, "width", 1) or 1)
    except (TypeError, ValueError):
        width = 1
    if width > 1:
        return f"[{width - 1}:0]"
    if re.fullmatch(r"\[\s*\d+\s*:\s*\d+\s*\]", bus_range):
        return bus_range
    return ""


def _parse_int_literal(value: Any) -> Optional[int]:
    text = str(value if value is not None else "").strip()
    if not text:
        return None
    if re.fullmatch(r"\d+", text):
        return int(text)
    if re.fullmatch(r"0x[0-9a-fA-F_]+", text):
        return int(text.replace("_", ""), 16)
    if re.fullmatch(r"\d+'[sS]?[dD][0-9_]+", text):
        return int(text.split("'")[-1][1:].replace("_", ""), 10)
    if re.fullmatch(r"\d+'[sS]?[hH][0-9a-fA-F_]+", text):
        return int(text.split("'")[-1][1:].replace("_", ""), 16)
    if re.fullmatch(r"\d+'[sS]?[bB][01_]+", text):
        return int(text.split("'")[-1][1:].replace("_", ""), 2)
    return None


def _infer_width_symbol(symbol: str, port: Any, values: Dict[str, int]) -> Optional[int]:
    upper = symbol.upper()
    name = str(getattr(port, "name", "") or "").lower()
    if "DATA_WIDTH" in upper:
        return values.get("DATA_WIDTH") or values.get("ALGN_DATA_WIDTH") or values.get("APB_DATA_WIDTH") or 32
    if "ADDR_WIDTH" in upper or name.endswith("addr"):
        return values.get("ADDR_WIDTH") or values.get("APB_ADDR_WIDTH") or 32
    if "OFFSET_WIDTH" in upper or name.endswith("offset"):
        return values.get("OFFSET_WIDTH") or values.get("ALGN_OFFSET_WIDTH") or 2
    if "SIZE_WIDTH" in upper or name.endswith("size"):
        return values.get("SIZE_WIDTH") or values.get("ALGN_SIZE_WIDTH") or 3
    if upper.endswith("_WIDTH"):
        return values.get(upper) or 1
    return None


def _resolve_bus_width(bus_range: str, values: Dict[str, int]) -> Optional[int]:
    match = re.fullmatch(r"\[\s*(.+?)\s*:\s*(.+?)\s*\]", bus_range or "")
    if not match:
        return None
    msb = _eval_int_expr(match.group(1), values)
    lsb = _eval_int_expr(match.group(2), values)
    if msb is None or lsb is None:
        return None
    return abs(msb - lsb) + 1


def _eval_int_expr(expr: str, values: Dict[str, int]) -> Optional[int]:
    text = str(expr or "").strip()
    if not text:
        return None

    def repl(match: re.Match[str]) -> str:
        name = match.group(0)
        if name in values:
            return str(values[name])
        return name

    text = re.sub(r"\b[A-Za-z_]\w*\b", repl, text)
    if re.search(r"[A-Za-z_]", text) or not re.fullmatch(r"[0-9+\-*/%() \t]+", text):
        return None
    try:
        return int(eval(text, {"__builtins__": {}}, {}))
    except Exception:
        return None


def _sv_literal(value: Any) -> str:
    """Normalize common LLM/spec literals into SystemVerilog syntax."""
    text = str(value if value is not None else "0").strip()
    if not text:
        return "0"
    lower = text.lower()
    if lower in {"true", "high"}:
        return "1'b1"
    if lower in {"false", "low"}:
        return "1'b0"
    if re.fullmatch(r"0x[0-9a-fA-F_]+", text):
        return "'h" + text[2:]
    if re.fullmatch(r"0b[01_xzXZ]+", text):
        return "'b" + text[2:]
    if re.fullmatch(r"\d+", text):
        return text
    if re.fullmatch(r"\d+'[sS]?[bBoOdDhH][0-9a-fA-F_xzXZ]+", text):
        return text
    if re.fullmatch(r"'[sS]?[bBoOdDhH][0-9a-fA-F_xzXZ]+", text):
        return text
    if text in {"'0", "'1"}:
        return text
    # Never emit natural-language intent tokens as raw SV identifiers.
    return "'0"


def _sv_string(value: Any) -> str:
    return str(value or "").replace("\\", "\\\\").replace('"', '\\"')[:160]


def _detect_clock_reset(design: Any):
    """Detect clock and reset signals from design."""
    clk_name = "clk"
    rst_name = "rst_n"
    rst_active_low = True

    if design.clock_domains:
        cd = design.clock_domains[0]
        clk_name = cd.name
        rst_name = cd.associated_reset or "rst_n"
        rst_active_low = cd.reset_polarity == "active_low"
    else:
        # Fallback: scan ports
        for p in design.ports:
            n = p.name.lower()
            if any(k in n for k in ("clk", "clock")):
                clk_name = p.name
            if any(k in n for k in ("rst", "reset")):
                rst_name = p.name
                rst_active_low = "n" in n or "_n" in n

    return clk_name, rst_name, rst_active_low
