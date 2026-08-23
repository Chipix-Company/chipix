"""
Verilog/SystemVerilog parser for the Design Visualizer.
Extracts module structure into a JSON-friendly format for D3.js rendering.
"""

from __future__ import annotations
import re
from dataclasses import dataclass, field, asdict
from typing import Optional


@dataclass
class Port:
    name: str
    direction: str  # "input", "output", "inout"
    width: str      # e.g., "1", "[7:0]", "[DATA_WIDTH-1:0]"
    port_type: str = ""  # "wire", "reg", "logic", etc.


@dataclass
class Parameter:
    name: str
    value: str


@dataclass
class InternalSignal:
    name: str
    signal_type: str  # "wire", "reg", "logic"
    width: str


@dataclass
class Submodule:
    instance_name: str
    module_type: str
    connections: dict  # port_name -> signal_name


@dataclass
class AlwaysBlock:
    block_type: str   # "sequential", "combinational"
    sensitivity: str  # e.g., "posedge clk or negedge rst_n"
    description: str


@dataclass
class ModuleInfo:
    name: str = ""
    parameters: list[Parameter] = field(default_factory=list)
    ports: list[Port] = field(default_factory=list)
    internal_signals: list[InternalSignal] = field(default_factory=list)
    submodules: list[Submodule] = field(default_factory=list)
    always_blocks: list[AlwaysBlock] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def _strip_markdown(code: str) -> str:
    """
    Robustly strip all markdown code block fences.
    Handles: ```verilog, ```systemverilog, ```sv, ``` (bare), etc.
    """
    # Remove opening fences with optional language tag
    code = re.sub(r"```[a-zA-Z0-9_]*\s*\n?", "", code)
    # Remove bare closing fences
    code = code.replace("```", "")
    return code


def _clean_code(code: str) -> str:
    """Remove comments from Verilog code for easier parsing."""
    code = re.sub(r"//.*$", "", code, flags=re.MULTILINE)
    code = re.sub(r"/\*.*?\*/", "", code, flags=re.DOTALL)
    return code


def _parse_width(width_str: str) -> str:
    """Parse a width specification, returning a clean string."""
    width_str = width_str.strip()
    if not width_str:
        return "1"
    return width_str


def parse_verilog(code: str) -> list[ModuleInfo]:
    """
    Parse Verilog/SystemVerilog code and extract module information.
    Returns a list of ModuleInfo objects (one per module found).
    Uses a character-level scanner to handle complex, multi-line port lists.
    """
    # 1. Strip markdown code fences
    code = _strip_markdown(code)

    # 2. Clean comments
    cleaned = _clean_code(code)

    modules = []

    # 3. Find all module...endmodule blocks
    for mod_match in re.finditer(r"\bmodule\b", cleaned):
        start = mod_match.start()
        end_search = re.search(r"\bendmodule\b", cleaned[start:])
        if not end_search:
            continue
        block = cleaned[start: start + end_search.end()]

        # Extract name
        name_match = re.match(r"module\s+(\w+)", block)
        if not name_match:
            continue

        module = ModuleInfo()
        module.name = name_match.group(1)

        # 4. Optional parameter block: module name #(params) (ports);
        param_match = re.search(r"module\s+\w+\s*#\s*\((.*?)\)\s*\(", block, re.DOTALL)
        if param_match:
            _parse_parameters("#(" + param_match.group(1) + ")", module)
            scan_from = param_match.end() - 1  # position of '(' that starts port list
        else:
            # Start scanning for '(' after module name
            scan_from = name_match.end()

        # 5. Scan for matching port-list parentheses
        port_start = None
        port_end = None
        depth = 0
        for i in range(scan_from, len(block)):
            ch = block[i]
            if ch == '(':
                if depth == 0:
                    port_start = i + 1
                depth += 1
            elif ch == ')':
                depth -= 1
                if depth == 0:
                    port_end = i
                    break

        port_block = block[port_start:port_end] if (port_start is not None and port_end is not None) else ""
        # Body: everything after closing ); up to endmodule
        body = ""
        if port_end is not None:
            tail = block[port_end:]
            semi = tail.find(";")
            if semi != -1:
                body = tail[semi + 1:]

        # 6. Parse ports
        _parse_ansi_ports(port_block, module)
        if not module.ports:
            _parse_non_ansi_ports(port_block, body, module)

        # 7. Parse body elements
        _parse_internal_signals(body, module)
        _parse_submodules(body, module)
        _parse_always_blocks(body, module)

        modules.append(module)

    return modules


def _parse_parameters(param_block: str, module: ModuleInfo):
    """Extract parameters from the #(...) block."""
    inner = re.sub(r"^#\s*\(", "", param_block.strip())
    inner = re.sub(r"\)\s*$", "", inner)
    param_pattern = r"parameter\s+(?:\w+\s+)?(\w+)\s*=\s*([^,\)]+)"
    for match in re.finditer(param_pattern, inner):
        module.parameters.append(Parameter(
            name=match.group(1).strip(),
            value=match.group(2).strip(),
        ))


def _parse_ansi_ports(port_block: str, module: ModuleInfo):
    """Parse ANSI-style port declarations (direction in the port list)."""
    port_pattern = (
        r"(input|output|inout)\s+"
        r"(?:(wire|reg|logic)\s+)?"
        r"(\[.*?\]\s+)?"
        r"(\w+)"
    )
    for match in re.finditer(port_pattern, port_block, re.DOTALL):
        direction = match.group(1)
        port_type = match.group(2) or ""
        width = _parse_width(match.group(3) or "")
        name = match.group(4)
        module.ports.append(Port(name=name, direction=direction, width=width, port_type=port_type))


def _parse_non_ansi_ports(port_block: str, body: str, module: ModuleInfo):
    """Parse non-ANSI style ports declared in body."""
    port_names = [p.strip() for p in port_block.split(",") if p.strip() and re.match(r"^\w+$", p.strip())]
    decl_pattern = r"(input|output|inout)\s+(?:(wire|reg|logic)\s+)?(\[.*?\]\s+)?(\w+)\s*;"
    declarations = {}
    for match in re.finditer(decl_pattern, body):
        declarations[match.group(4)] = {
            "direction": match.group(1),
            "port_type": match.group(2) or "",
            "width": _parse_width(match.group(3) or ""),
        }
    for name in port_names:
        if name in declarations:
            d = declarations[name]
            module.ports.append(Port(
                name=name, direction=d["direction"],
                width=d["width"], port_type=d["port_type"],
            ))


def _parse_internal_signals(body: str, module: ModuleInfo):
    """Extract internal wire/reg/logic declarations."""
    port_names = {p.name for p in module.ports}
    signal_pattern = r"(?:^|\s)(wire|reg|logic)\s+(\[.*?\]\s+)?(\w+)\s*;"
    for match in re.finditer(signal_pattern, body):
        name = match.group(3)
        if name not in port_names:
            module.internal_signals.append(InternalSignal(
                name=name,
                signal_type=match.group(1),
                width=_parse_width(match.group(2) or ""),
            ))


def _parse_submodules(body: str, module: ModuleInfo):
    """Extract submodule instantiations."""
    inst_pattern = r"(\w+)\s+(?:#\s*\(.*?\)\s*)?(\w+)\s*\((.*?)\)\s*;"
    keywords = {
        "module", "endmodule", "input", "output", "inout", "wire", "reg",
        "logic", "assign", "always", "always_ff", "always_comb", "initial",
        "parameter", "localparam", "generate", "if", "else", "case", "begin",
        "end", "for", "while", "integer", "genvar", "function", "task",
    }
    for match in re.finditer(inst_pattern, body, re.DOTALL):
        mod_type = match.group(1)
        if mod_type.lower() in keywords:
            continue
        inst_name = match.group(2)
        conn_block = match.group(3)
        connections = {}
        for conn in re.finditer(r"\.(\w+)\s*\(([^)]*)\)", conn_block):
            connections[conn.group(1)] = conn.group(2).strip()
        module.submodules.append(Submodule(
            instance_name=inst_name,
            module_type=mod_type,
            connections=connections,
        ))


def _parse_always_blocks(body: str, module: ModuleInfo):
    """Extract always block types and sensitivities."""
    for match in re.finditer(r"always_ff\s*@\s*\(([^)]+)\)", body):
        module.always_blocks.append(AlwaysBlock(
            block_type="sequential",
            sensitivity=match.group(1).strip(),
            description="Sequential logic (always_ff)",
        ))
    for match in re.finditer(r"always\s*@\s*\((posedge|negedge)([^)]+)\)", body):
        module.always_blocks.append(AlwaysBlock(
            block_type="sequential",
            sensitivity=(match.group(1) + match.group(2)).strip(),
            description="Sequential logic (always)",
        ))
    for pat in [r"always_comb", r"always\s*@\s*\(\s*\*\s*\)"]:
        for match in re.finditer(pat, body):
            module.always_blocks.append(AlwaysBlock(
                block_type="combinational",
                sensitivity="*",
                description="Combinational logic",
            ))
