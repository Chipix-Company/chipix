"""
Symbol Table — Global Name Registry for Anti-Hallucination.

Every signal, module, class, task, port, covergroup is registered here.
LLM-generated code MUST only use names from this table.

This is the #1 defense against hallucinated identifiers.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from core.logger import get_logger

logger = get_logger("SymbolTable")


class SymbolKind(str, Enum):
    """Categories of symbols that can be registered."""
    MODULE = "module"
    PORT = "port"
    SIGNAL = "signal"
    PARAMETER = "parameter"
    CLASS = "class"
    TASK = "task"
    FUNCTION = "function"
    INTERFACE = "interface"
    MODPORT = "modport"
    CLOCKING = "clocking_block"
    COVERGROUP = "covergroup"
    COVERPOINT = "coverpoint"
    PROPERTY = "property"
    SEQUENCE = "sequence"
    TYPEDEF = "typedef"
    ENUM_VALUE = "enum_value"
    INSTANCE = "instance"
    MAILBOX = "mailbox"
    MACRO = "macro"


@dataclass
class Symbol:
    """A single registered symbol."""
    name: str
    kind: SymbolKind
    source_file: str = ""         # Which file defined this symbol
    parent: str = ""              # Parent scope (e.g., class name for a task)
    data_type: str = ""           # e.g., "logic [3:0]", "int"
    direction: str = ""           # For ports: input/output/inout
    width: int = 0                # Bit width for ports/signals
    description: str = ""         # Human-readable description

    @property
    def qualified_name(self) -> str:
        """Fully qualified name (e.g., 'counter_driver.drive_transaction')."""
        if self.parent:
            return f"{self.parent}.{self.name}"
        return self.name


class SymbolTable:
    """Global name registry for the entire verification project.

    Usage:
        st = SymbolTable()
        st.register("counter_4bit", SymbolKind.MODULE, "counter.sv")
        st.register("clk", SymbolKind.PORT, "counter.sv", parent="counter_4bit")

        # Check if a name exists
        st.exists("clk")  # True
        st.exists("invented_signal")  # False

        # Generate constraint text for LLM prompts
        constraint_text = st.generate_constraint_block()
    """

    def __init__(self) -> None:
        self._symbols: dict[str, list[Symbol]] = {}   # name → all symbols with that name
        self._by_kind: dict[SymbolKind, list[Symbol]] = {}
        self._by_file: dict[str, list[Symbol]] = {}

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def register(
        self,
        name: str,
        kind: SymbolKind,
        source_file: str = "",
        parent: str = "",
        data_type: str = "",
        direction: str = "",
        width: int = 0,
        description: str = "",
    ) -> Symbol:
        """Register a new symbol in the table."""
        sym = Symbol(
            name=name,
            kind=kind,
            source_file=source_file,
            parent=parent,
            data_type=data_type,
            direction=direction,
            width=width,
            description=description,
        )

        # Index by name
        self._symbols.setdefault(name, []).append(sym)

        # Index by kind
        self._by_kind.setdefault(kind, []).append(sym)

        # Index by source file
        if source_file:
            self._by_file.setdefault(source_file, []).append(sym)

        return sym

    # ------------------------------------------------------------------
    # Bulk registration from parsed data
    # ------------------------------------------------------------------

    def register_from_rtl(self, analysis) -> int:
        """Register all symbols from an RTLAnalysis object.

        Args:
            analysis: RTLAnalysis object from the RTL Analyzer

        Returns:
            Number of symbols registered
        """
        count = 0

        # Register modules
        for module in analysis.modules:
            self.register(module.name, SymbolKind.MODULE, source_file="rtl")
            count += 1

            # Register ports
            for port in module.ports:
                self.register(
                    port.name, SymbolKind.PORT,
                    source_file="rtl",
                    parent=module.name,
                    direction=port.direction.value,
                    width=port.width,
                    data_type=port.port_type,
                )
                count += 1

        # Register parameters
        for param_name, param_val in analysis.parameters.items():
            self.register(param_name, SymbolKind.PARAMETER, source_file="rtl")
            count += 1

        # Register internal signals
        for sig in analysis.internal_signals:
            self.register(
                sig.name, SymbolKind.SIGNAL,
                source_file="rtl",
                data_type=sig.signal_type,
                width=sig.width,
            )
            count += 1

        # Register FSM states
        for state in analysis.fsm_states:
            self.register(state, SymbolKind.ENUM_VALUE, source_file="rtl")
            count += 1

        logger.info("Registered %d symbols from RTL analysis", count)
        return count

    def register_from_generated_file(self, filename: str, content: str) -> int:
        """Parse a generated SV file and register new symbols.

        Extracts: classes, tasks, functions, interfaces, modports,
        covergroups, properties, etc.
        """
        count = 0

        # Interface declarations
        for match in re.finditer(r"\binterface\s+(\w+)", content):
            self.register(match.group(1), SymbolKind.INTERFACE, source_file=filename)
            count += 1

        # Modport declarations
        for match in re.finditer(r"\bmodport\s+(\w+)", content):
            self.register(match.group(1), SymbolKind.MODPORT, source_file=filename)
            count += 1

        # Clocking block declarations
        for match in re.finditer(r"\bclocking\s+(\w+)", content):
            self.register(match.group(1), SymbolKind.CLOCKING, source_file=filename)
            count += 1

        # Class declarations
        for match in re.finditer(r"\bclass\s+(\w+)", content):
            class_name = match.group(1)
            self.register(class_name, SymbolKind.CLASS, source_file=filename)
            count += 1

        # Task declarations (with parent class context)
        current_class = ""
        for line in content.splitlines():
            class_match = re.match(r"\s*class\s+(\w+)", line)
            if class_match:
                current_class = class_match.group(1)

            task_match = re.match(r"\s*(?:virtual\s+)?task\s+(\w+)", line)
            if task_match:
                self.register(
                    task_match.group(1), SymbolKind.TASK,
                    source_file=filename, parent=current_class,
                )
                count += 1

            func_match = re.match(r"\s*(?:virtual\s+)?function\s+\w+\s+(\w+)", line)
            if func_match:
                self.register(
                    func_match.group(1), SymbolKind.FUNCTION,
                    source_file=filename, parent=current_class,
                )
                count += 1

            if re.match(r"\s*endclass", line):
                current_class = ""

        # Covergroup declarations
        for match in re.finditer(r"\bcovergroup\s+(\w+)", content):
            self.register(match.group(1), SymbolKind.COVERGROUP, source_file=filename)
            count += 1

        # Coverpoint declarations
        for match in re.finditer(r"\bcoverpoint\s+\w+.*?(\w+)\s*;", content):
            self.register(match.group(1), SymbolKind.COVERPOINT, source_file=filename)
            count += 1

        # Property declarations
        for match in re.finditer(r"\bproperty\s+(\w+)", content):
            self.register(match.group(1), SymbolKind.PROPERTY, source_file=filename)
            count += 1

        # Module declarations (for tb_top etc.)
        for match in re.finditer(r"\bmodule\s+(\w+)", content):
            self.register(match.group(1), SymbolKind.MODULE, source_file=filename)
            count += 1

        if count > 0:
            logger.info("Registered %d symbols from %s", count, filename)
        return count

    # ------------------------------------------------------------------
    # Lookup
    # ------------------------------------------------------------------

    def exists(self, name: str) -> bool:
        """Check if a symbol name is registered."""
        return name in self._symbols

    def lookup(self, name: str) -> list[Symbol]:
        """Get all symbols with the given name."""
        return self._symbols.get(name, [])

    def lookup_kind(self, kind: SymbolKind) -> list[Symbol]:
        """Get all symbols of a specific kind."""
        return self._by_kind.get(kind, [])

    def lookup_file(self, filename: str) -> list[Symbol]:
        """Get all symbols from a specific file."""
        return self._by_file.get(filename, [])

    def get_names_by_kind(self, kind: SymbolKind) -> list[str]:
        """Get all names of a specific kind."""
        return [s.name for s in self._by_kind.get(kind, [])]

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def validate_names_in_code(self, code: str) -> dict:
        """Check identifiers in generated code against the symbol table.

        Returns:
            {
                "known_names": [...],     # Names found in symbol table
                "unknown_names": [...],   # Names NOT in symbol table (potential hallucinations)
                "grounding_score": float  # 0.0-1.0, ratio of known names
            }
        """
        # Extract all identifiers from the code
        identifiers = set(re.findall(r"\b([a-zA-Z_]\w*)\b", code))

        # Filter out SV keywords, numbers, common types
        sv_keywords = {
            "module", "endmodule", "class", "endclass", "function", "endfunction",
            "task", "endtask", "begin", "end", "if", "else", "for", "while",
            "case", "endcase", "default", "return", "input", "output", "inout",
            "wire", "reg", "logic", "integer", "real", "string", "bit", "byte",
            "int", "void", "virtual", "extends", "implements", "new", "null",
            "this", "super", "static", "local", "protected", "rand", "randc",
            "constraint", "solve", "before", "dist", "inside", "foreach",
            "always", "always_ff", "always_comb", "always_latch", "initial",
            "assign", "typedef", "enum", "struct", "union", "packed",
            "interface", "endinterface", "modport", "clocking", "endclocking",
            "property", "endproperty", "sequence", "endsequence",
            "assert", "assume", "cover", "restrict", "expect",
            "covergroup", "endgroup", "coverpoint", "cross", "bins",
            "posedge", "negedge", "edge", "iff", "throughout", "within",
            "timescale", "include", "define", "ifdef", "ifndef", "endif",
            "generate", "endgenerate", "genvar", "parameter", "localparam",
            "mailbox", "semaphore", "event", "automatic", "wait", "fork",
            "join", "join_any", "join_none", "disable", "force", "release",
            "display", "monitor", "finish", "dumpfile", "dumpvars",
            "time", "realtime", "signed", "unsigned",
            "uvm_component", "uvm_object", "uvm_test", "uvm_env",
            "uvm_agent", "uvm_driver", "uvm_monitor", "uvm_sequencer",
            "uvm_sequence", "uvm_sequence_item", "uvm_scoreboard",
            "uvm_subscriber", "uvm_tlm_analysis_fifo",
            "run_phase", "build_phase", "connect_phase", "main_phase",
            "uvm_config_db", "uvm_info", "uvm_error", "uvm_warning",
            "uvm_fatal", "get_type_name", "get_full_name",
            # Common types & constants
            "TRUE", "FALSE", "true", "false", "ns", "ps", "us", "ms",
        }

        identifiers -= sv_keywords

        # Also remove pure numbers and very short names (i, j, k, etc.)
        identifiers = {
            name for name in identifiers
            if len(name) > 1 and not name.isdigit()
        }

        known = {name for name in identifiers if self.exists(name)}
        unknown = identifiers - known

        total = len(identifiers)
        score = len(known) / total if total > 0 else 1.0

        return {
            "known_names": sorted(known),
            "unknown_names": sorted(unknown),
            "grounding_score": round(score, 3),
        }

    # ------------------------------------------------------------------
    # Constraint block for LLM prompts
    # ------------------------------------------------------------------

    def generate_constraint_block(self) -> str:
        """Generate a constraint text block to inject into LLM prompts.

        This tells the LLM: "You MUST only use these names."
        """
        lines = [
            "+--- REGISTERED NAMES (You MUST only use these) ---------------+",
        ]

        # Modules
        modules = self.get_names_by_kind(SymbolKind.MODULE)
        if modules:
            lines.append(f"|  Modules: {', '.join(modules)}")

        # Interfaces
        interfaces = self.get_names_by_kind(SymbolKind.INTERFACE)
        if interfaces:
            lines.append(f"|  Interfaces: {', '.join(interfaces)}")

        # Ports (grouped by parent module)
        ports = self.lookup_kind(SymbolKind.PORT)
        if ports:
            port_str = ", ".join(sorted(set(p.name for p in ports)))
            lines.append(f"|  Ports: {port_str}")

        # Classes
        classes = self.get_names_by_kind(SymbolKind.CLASS)
        if classes:
            lines.append(f"|  Classes: {', '.join(classes)}")

        # Tasks (grouped by class)
        tasks = self.lookup_kind(SymbolKind.TASK)
        if tasks:
            by_class: dict[str, list[str]] = {}
            for t in tasks:
                by_class.setdefault(t.parent or "global", []).append(t.name)
            for cls, task_names in by_class.items():
                lines.append(f"|  Tasks ({cls}): {', '.join(task_names)}")

        # Modports
        modports = self.get_names_by_kind(SymbolKind.MODPORT)
        if modports:
            lines.append(f"|  Modports: {', '.join(modports)}")

        # Covergroups
        covergroups = self.get_names_by_kind(SymbolKind.COVERGROUP)
        if covergroups:
            lines.append(f"|  Covergroups: {', '.join(covergroups)}")

        # Properties
        properties = self.get_names_by_kind(SymbolKind.PROPERTY)
        if properties:
            lines.append(f"|  Properties: {', '.join(properties)}")

        # Parameters
        params = self.get_names_by_kind(SymbolKind.PARAMETER)
        if params:
            lines.append(f"|  Parameters: {', '.join(params)}")

        # Signals
        signals = self.get_names_by_kind(SymbolKind.SIGNAL)
        if signals:
            sig_names = ", ".join(sorted(set(signals)))
            lines.append(f"|  Internal signals: {sig_names}")

        # FSM states
        enums = self.get_names_by_kind(SymbolKind.ENUM_VALUE)
        if enums:
            lines.append(f"|  FSM states/enums: {', '.join(enums)}")

        lines.append("|")
        lines.append("|  WARNING: Do NOT invent new names. Use ONLY the names above.")
        lines.append("+-------------------------------------------------------------+")

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Stats
    # ------------------------------------------------------------------

    @property
    def total_symbols(self) -> int:
        return sum(len(v) for v in self._symbols.values())

    def summary(self) -> str:
        """Human-readable summary of the symbol table."""
        kinds = {}
        for kind, symbols in self._by_kind.items():
            kinds[kind.value] = len(symbols)
        parts = [f"{v} {k}s" for k, v in sorted(kinds.items())]
        return f"SymbolTable: {self.total_symbols} total ({', '.join(parts)})"
