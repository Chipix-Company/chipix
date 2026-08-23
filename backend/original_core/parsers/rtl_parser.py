"""
RTL file parser.

Reads Verilog / SystemVerilog files and extracts structural information
using regex-based parsing (not a full HDL parser, but sufficient for
feeding into the AI agents).
"""

from __future__ import annotations

import re
from pathlib import Path

from core.logger import get_logger
from core.models import (
    AlwaysBlock,
    ModuleInfo,
    PortDefinition,
    PortDirection,
    RTLAnalysis,
    SignalInfo,
)

logger = get_logger("Parser.RTL")


def parse_rtl(file_path: str | Path) -> tuple[str, RTLAnalysis]:
    """Parse a Verilog/SV file and return (raw_code, RTLAnalysis).

    The raw code is always returned for direct inclusion in LLM context.
    The RTLAnalysis provides structured metadata.
    """
    file_path = Path(file_path)
    if not file_path.exists():
        raise FileNotFoundError(f"RTL file not found: {file_path}")

    raw_code = file_path.read_text(encoding="utf-8")

    analysis = RTLAnalysis(raw_code=raw_code)

    # Detect code style
    if "always_ff" in raw_code or "always_comb" in raw_code or "logic " in raw_code:
        analysis.code_style = "systemverilog"
    else:
        analysis.code_style = "verilog"

    # Extract modules
    analysis.modules = _extract_modules(raw_code)

    # Set top module (first module found, or largest)
    if analysis.modules:
        analysis.top_module = analysis.modules[0].name
        # Build port map from top module
        for port in analysis.modules[0].ports:
            analysis.port_map[port.name] = port

    # Extract parameters
    analysis.parameters = _extract_parameters(raw_code)

    # Extract internal signals
    analysis.internal_signals = _extract_internal_signals(raw_code)

    # Extract always blocks
    analysis.always_blocks = _extract_always_blocks(raw_code)

    # Extract FSM states
    analysis.fsm_states = _extract_fsm_states(raw_code)

    # Extract instantiations
    analysis.instantiations = _extract_instantiations(raw_code)

    logger.info(
        "Parsed RTL: %s | %d modules | %d ports | %d signals | %d always blocks | FSM: %s",
        analysis.code_style,
        len(analysis.modules),
        len(analysis.port_map),
        len(analysis.internal_signals),
        len(analysis.always_blocks),
        "yes" if analysis.has_fsm else "no",
    )

    return raw_code, analysis


# ═══════════════════════════════════════════════════════════════════════
# Internal extraction functions
# ═══════════════════════════════════════════════════════════════════════


def _extract_modules(code: str) -> list[ModuleInfo]:
    """Find all module declarations and their ports."""
    modules = []

    # Match: module <name> (...); ... endmodule
    module_pattern = re.compile(
        r"module\s+(\w+)\s*(?:#\s*\(.*?\))?\s*\((.*?)\)\s*;",
        re.DOTALL,
    )

    for match in module_pattern.finditer(code):
        name = match.group(1)
        ports_block = match.group(2)

        # Try ANSI-style ports first (input/output in port list)
        ports = _parse_port_list(ports_block)

        # If no ports found, try Verilog-95 style (ports in module body)
        if not ports:
            # Get module body up to endmodule
            body_start = match.end()
            endmod = re.search(r"\bendmodule\b", code[body_start:])
            if endmod:
                body = code[body_start:body_start + endmod.start()]
                ports = _parse_v95_ports(ports_block, body)

        # Find line range
        start_line = code[:match.start()].count("\n") + 1
        end_match = re.search(r"\bendmodule\b", code[match.end():])
        end_line = start_line + code[match.start():match.end() + (end_match.end() if end_match else 0)].count("\n")

        modules.append(ModuleInfo(
            name=name,
            ports=ports,
            line_range=(start_line, end_line),
        ))

    return modules


def _parse_v95_ports(port_names_block: str, module_body: str) -> list[PortDefinition]:
    """Parse Verilog-95 style port declarations from the module body.

    In V95 style, the module header has only port names:
        module M(a, b, c);
    And the body contains the actual declarations:
        input a;
        input [1:0] b;
        output reg [7:0] c;
    """
    # Get port names from header
    header_names = set()
    for name in re.findall(r"\b(\w+)\b", port_names_block):
        header_names.add(name)

    ports = []
    seen = set()

    # Match: input/output/inout [reg|wire] [width] name1, name2, ...;
    pattern = re.compile(
        r"^\s*(input|output|inout)\s+"
        r"(?:(reg|wire|logic|integer)\s+)?"
        r"(?:(\[[^\]]+\])\s*)?"
        r"([^;]+);",
        re.MULTILINE | re.IGNORECASE,
    )

    for match in pattern.finditer(module_body):
        direction_str = match.group(1).lower()
        port_type = match.group(2) or "wire"
        bus_range = match.group(3) or ""
        names_str = match.group(4)

        # Calculate width from bus range
        width = 1
        if bus_range:
            range_match = re.match(r"\[(\d+):(\d+)\]", bus_range)
            if range_match:
                width = abs(int(range_match.group(1)) - int(range_match.group(2))) + 1

        # Parse comma-separated names (e.g., "Hclk, Hresetn, Hreadyout")
        for name_part in names_str.split(","):
            name = name_part.strip()
            if not name or not re.match(r"^\w+$", name):
                continue
            if name in seen:
                continue
            seen.add(name)

            try:
                direction = PortDirection(direction_str)
            except ValueError:
                continue

            ports.append(PortDefinition(
                name=name,
                direction=direction,
                width=width,
                bus_range=bus_range,
                port_type=port_type,
            ))

    return ports


def _parse_port_list(ports_block: str) -> list[PortDefinition]:
    """Parse a port declaration block into PortDefinition list."""
    ports = []

    # Pattern: input/output/inout [logic|wire|reg] [width] name
    port_pattern = re.compile(
        r"(input|output|inout)\s+"
        r"(?:(logic|wire|reg|integer)\s+)?"
        r"(?:(\[[\d:]+\])\s+)?"
        r"(\w+)",
        re.IGNORECASE,
    )

    for match in port_pattern.finditer(ports_block):
        direction_str = match.group(1).lower()
        port_type = match.group(2) or "logic"
        bus_range = match.group(3) or ""
        name = match.group(4)

        # Calculate width from bus range
        width = 1
        if bus_range:
            range_match = re.match(r"\[(\d+):(\d+)\]", bus_range)
            if range_match:
                width = abs(int(range_match.group(1)) - int(range_match.group(2))) + 1

        direction = PortDirection(direction_str)
        ports.append(PortDefinition(
            name=name,
            direction=direction,
            width=width,
            bus_range=bus_range,
            port_type=port_type,
        ))

    return ports


def _extract_parameters(code: str) -> dict[str, str]:
    """Extract parameter declarations."""
    params = {}
    pattern = re.compile(r"\bparameter\s+(?:\w+\s+)?(\w+)\s*=\s*([^;,\)]+)", re.IGNORECASE)
    for match in pattern.finditer(code):
        params[match.group(1)] = match.group(2).strip()
    return params


def _extract_internal_signals(code: str) -> list[SignalInfo]:
    """Extract wire/reg/logic declarations that are NOT ports."""
    signals = []

    # Match internal signal declarations (not inside port list)
    pattern = re.compile(
        r"^\s*(wire|reg|logic|integer)\s+(?:(\[[\d:]+\])\s+)?(\w+)\s*[;=]",
        re.MULTILINE | re.IGNORECASE,
    )

    for match in pattern.finditer(code):
        sig_type = match.group(1)
        bus_range = match.group(2) or ""
        name = match.group(3)

        width = 1
        if bus_range:
            range_match = re.match(r"\[(\d+):(\d+)\]", bus_range)
            if range_match:
                width = abs(int(range_match.group(1)) - int(range_match.group(2))) + 1

        signals.append(SignalInfo(
            name=name,
            signal_type=sig_type,
            width=width,
            bus_range=bus_range,
        ))

    return signals


def _extract_always_blocks(code: str) -> list[AlwaysBlock]:
    """Extract always / always_ff / always_comb blocks."""
    blocks = []

    # Match always blocks with their sensitivity lists
    pattern = re.compile(
        r"(always_ff|always_comb|always)\s*@?\s*\(([^)]*)\)",
        re.IGNORECASE,
    )

    for match in pattern.finditer(code):
        block_type = match.group(1)
        sensitivity = match.group(2).strip()
        line = code[:match.start()].count("\n") + 1

        blocks.append(AlwaysBlock(
            block_type=block_type,
            sensitivity=sensitivity,
            line_range=(line, line),  # Approximate
        ))

    return blocks


def _extract_fsm_states(code: str) -> list[str]:
    """Detect FSM state names from enum or parameter declarations."""
    states = []

    # Pattern 1: typedef enum {...} state_t;
    enum_match = re.search(
        r"typedef\s+enum\s*(?:logic\s*\[[\d:]+\])?\s*\{([^}]+)\}",
        code,
        re.DOTALL | re.IGNORECASE,
    )
    if enum_match:
        enum_body = enum_match.group(1)
        states = [s.strip().split("=")[0].strip()
                  for s in enum_body.split(",")
                  if s.strip()]
        return states

    # Pattern 2: parameter IDLE = ..., RUN = ..., etc.
    state_pattern = re.compile(
        r"parameter\s+(\w*(?:IDLE|INIT|READY|RUN|DONE|WAIT|START|STOP|ERROR|STATE)\w*)\s*=",
        re.IGNORECASE,
    )
    for match in state_pattern.finditer(code):
        states.append(match.group(1))

    return states


def _extract_instantiations(code: str) -> list[str]:
    """Find module instantiations (excluding primitives)."""
    instances = []

    # Pattern: <module_name> [#(...)] <instance_name> (...)
    pattern = re.compile(
        r"^\s*(\w+)\s+(?:#\s*\(.*?\)\s+)?(\w+)\s*\(",
        re.MULTILINE,
    )

    # Keywords that look like instantiations but aren't
    keywords = {
        "module", "endmodule", "function", "endfunction", "task", "endtask",
        "always", "always_ff", "always_comb", "initial", "assign", "if",
        "else", "begin", "end", "case", "for", "while", "generate",
        "wire", "reg", "logic", "input", "output", "inout", "parameter",
        "localparam", "integer", "real", "time",
    }

    for match in pattern.finditer(code):
        module_name = match.group(1)
        if module_name.lower() not in keywords:
            instances.append(module_name)

    return instances
