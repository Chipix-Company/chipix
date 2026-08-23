"""
Data models shared across all agents and phases.

Every agent consumes and produces these structured types.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Callable, Optional


# ═══════════════════════════════════════════════════════════════════════
# Phase 1 Models — Spec Parser
# ═══════════════════════════════════════════════════════════════════════


class PortDirection(str, Enum):
    INPUT = "input"
    OUTPUT = "output"
    INOUT = "inout"


@dataclass
class PortDefinition:
    """A single I/O port from the specification or RTL."""
    name: str
    direction: PortDirection
    width: int = 1                    # bit-width (1 = scalar)
    description: str = ""             # from spec: e.g., "active-low reset"
    bus_range: str = ""               # e.g., "[3:0]"
    port_type: str = "logic"          # logic, wire, reg, etc.

    @property
    def declaration(self) -> str:
        """Return a SystemVerilog port declaration string."""
        width_str = f" {self.bus_range}" if self.bus_range else ""
        return f"{self.direction.value} {self.port_type}{width_str} {self.name}"


@dataclass
class DesignSpecification:
    """Structured output from Agent 1 (Spec Parser).
    
    Contains ALL requirements extracted from the user's specification document.
    """
    module_name: str = ""
    description: str = ""
    ports: list[PortDefinition] = field(default_factory=list)
    functional_requirements: list[str] = field(default_factory=list)
    constraints: list[str] = field(default_factory=list)
    clock_domains: list[str] = field(default_factory=list)
    reset_strategy: str = ""           # e.g., "asynchronous active-low"
    edge_cases: list[str] = field(default_factory=list)
    performance_targets: dict[str, str] = field(default_factory=dict)
    protocol_info: str = ""            # bus protocol if any
    raw_text: str = ""                 # original parsed text

    @property
    def port_names(self) -> list[str]:
        return [p.name for p in self.ports]

    @property
    def input_ports(self) -> list[PortDefinition]:
        return [p for p in self.ports if p.direction == PortDirection.INPUT]

    @property
    def output_ports(self) -> list[PortDefinition]:
        return [p for p in self.ports if p.direction == PortDirection.OUTPUT]


# ═══════════════════════════════════════════════════════════════════════
# Phase 1 Models — RTL Analyzer
# ═══════════════════════════════════════════════════════════════════════


@dataclass
class SignalInfo:
    """An internal signal (wire/reg) found in the RTL."""
    name: str
    signal_type: str = "logic"         # wire, reg, logic, integer
    width: int = 1
    bus_range: str = ""
    description: str = ""


@dataclass
class AlwaysBlock:
    """An always block found in the RTL."""
    block_type: str = ""               # "always_ff", "always_comb", "always @(...)"
    sensitivity: str = ""              # e.g., "posedge clk or negedge rst_n"
    line_range: tuple[int, int] = (0, 0)
    description: str = ""              # AI-generated description of what it does


@dataclass
class ModuleInfo:
    """A Verilog/SV module found in the RTL."""
    name: str
    ports: list[PortDefinition] = field(default_factory=list)
    parameters: dict[str, str] = field(default_factory=dict)
    line_range: tuple[int, int] = (0, 0)


@dataclass
class RTLAnalysis:
    """Structured output from Agent 2 (RTL Analyzer).

    Contains ALL structural/behavioral information extracted from the RTL code.
    """
    modules: list[ModuleInfo] = field(default_factory=list)
    top_module: str = ""
    port_map: dict[str, PortDefinition] = field(default_factory=dict)
    internal_signals: list[SignalInfo] = field(default_factory=list)
    always_blocks: list[AlwaysBlock] = field(default_factory=list)
    fsm_states: list[str] = field(default_factory=list)
    instantiations: list[str] = field(default_factory=list)
    parameters: dict[str, str] = field(default_factory=dict)
    code_style: str = "systemverilog"  # "verilog" or "systemverilog"
    raw_code: str = ""

    @property
    def port_names(self) -> list[str]:
        return list(self.port_map.keys())

    @property
    def has_fsm(self) -> bool:
        return len(self.fsm_states) > 0

    @property
    def clock_signal(self) -> str:
        """Best guess at the clock signal name."""
        for name in self.port_map:
            if name.lower() in ("clk", "clock", "sys_clk", "i_clk"):
                return name
        return "clk"

    @property
    def reset_signal(self) -> str:
        """Best guess at the reset signal name."""
        for name in self.port_map:
            if any(r in name.lower() for r in ("rst", "reset", "rstn", "rst_n")):
                return name
        return "rst_n"


# ═══════════════════════════════════════════════════════════════════════
# Phase 2 Models — Mental Model
# ═══════════════════════════════════════════════════════════════════════


@dataclass
class TestScenario:
    """A single verification scenario to be tested."""
    name: str                          # e.g., "reset_test"
    description: str = ""              # What this test verifies
    priority: str = "medium"           # "high", "medium", "low"
    spec_requirement: str = ""         # Which spec requirement it maps to
    stimulus: str = ""                 # Input pattern description
    expected_output: str = ""          # Expected behavior


@dataclass
class AssertionTarget:
    """Something that needs an SVA assertion."""
    name: str                          # e.g., "reset_clears_counter"
    property_description: str = ""     # Natural language description
    assertion_type: str = "assert"     # "assert", "assume", "cover"
    related_signals: list[str] = field(default_factory=list)
    spec_requirement: str = ""         # Traceability to spec


@dataclass
class CoveragePoint:
    """A functional coverage point."""
    name: str                          # e.g., "cp_count_overflow"
    signal: str = ""                   # Which signal to cover
    cover_type: str = "coverpoint"     # "coverpoint", "cross", "transition"
    bins_description: str = ""         # What bins to create
    target_percentage: float = 100.0


@dataclass
class InputSequence:
    """A stimulus pattern for the driver."""
    name: str                          # e.g., "count_to_max"
    description: str = ""
    steps: list[str] = field(default_factory=list)  # Human-readable steps


@dataclass
class FileToGenerate:
    """Specification of one file the generator must produce."""
    file_id: int
    filename: str
    display_name: str
    depends_on: list[str] = field(default_factory=list)
    generation_notes: str = ""         # AI's notes on how to generate this file


@dataclass
class MentalModel:
    """Structured output from Agent 3 (Mental Model Builder).

    The complete verification blueprint that drives file generation.
    """
    # Verification strategy
    verification_plan: list[TestScenario] = field(default_factory=list)
    assertion_plan: list[AssertionTarget] = field(default_factory=list)
    coverage_plan: list[CoveragePoint] = field(default_factory=list)

    # File generation plan
    files_to_generate: list[FileToGenerate] = field(default_factory=list)

    # Design understanding
    spec_to_rtl_mapping: dict[str, str] = field(default_factory=dict)
    critical_paths: list[str] = field(default_factory=list)
    corner_cases: list[str] = field(default_factory=list)

    # Stimulus strategy
    clock_scheme: str = ""             # e.g., "10ns period, 50% duty cycle"
    reset_sequence: str = ""           # e.g., "Assert rst_n low for 20ns, then release"
    input_sequences: list[InputSequence] = field(default_factory=list)

    # Summary for logging
    @property
    def summary(self) -> str:
        return (
            f"MentalModel: {len(self.verification_plan)} test scenarios, "
            f"{len(self.assertion_plan)} assertions, "
            f"{len(self.coverage_plan)} coverage points, "
            f"{len(self.files_to_generate)} files to generate, "
            f"{len(self.corner_cases)} corner cases"
        )


# ═══════════════════════════════════════════════════════════════════════
# Phase 3 Models — File Generator
# ═══════════════════════════════════════════════════════════════════════


@dataclass
class ValidationResult:
    """Result of validating a generated file."""
    passed: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def error_summary(self) -> str:
        return "; ".join(self.errors) if self.errors else "No errors"


@dataclass
class GeneratedFile:
    """A single generated verification file + metadata."""
    filename: str
    content: str
    generated_at: datetime = field(default_factory=datetime.now)
    validation: ValidationResult = field(default_factory=lambda: ValidationResult(passed=True))
    attempts: int = 1
    generation_time_seconds: float = 0.0

    @property
    def line_count(self) -> int:
        return len(self.content.splitlines())


@dataclass
class FileGenerationContext:
    """All context needed for a single file generation LLM call."""
    file_id: int
    filename: str
    system_prompt: str
    user_prompt: str                   # Fully assembled with context
    rtl_code: str = ""
    spec_text: str = ""
    dependency_files: dict[str, str] = field(default_factory=dict)  # filename → content
    mental_model_section: str = ""     # Relevant section from mental model
    focus_instruction: str = ""        # The FOCUS INSTRUCTION block


# ═══════════════════════════════════════════════════════════════════════
# Phase 4 & 5 Models — Test Runner & Fixer (stubs for later)
# ═══════════════════════════════════════════════════════════════════════


@dataclass
class CoverageMetrics:
    """Simulation coverage results."""
    line_coverage: float = 0.0
    branch_coverage: float = 0.0
    toggle_coverage: float = 0.0

    def summary(self) -> str:
        return (
            f"Line: {self.line_coverage:.1f}%  |  "
            f"Branch: {self.branch_coverage:.1f}%  |  "
            f"Toggle: {self.toggle_coverage:.1f}%"
        )


@dataclass
class TestResults:
    """Aggregated test-run results."""
    pass_rate: float = 0.0
    total_tests: int = 0
    passed_tests: int = 0
    failed_tests: int = 0
    assertion_failures: int = 0
    error_log: str = ""
    sim_log: str = ""
    coverage: CoverageMetrics = field(default_factory=CoverageMetrics)
    compile_success: bool = True

    @property
    def all_passed(self) -> bool:
        return self.pass_rate >= 100.0 and self.assertion_failures == 0
