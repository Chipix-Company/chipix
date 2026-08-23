"""
Mental Model Schema — Structured Design-Intent Knowledge Base.

Defines the full JSON-serializable schema for a mental model revision.
Every field is source-grounded: it carries a SourceReference pointing
back to the exact file + line (or spec page) that justifies it.

This schema is designed to scale from a 50-line FIFO to a 500K-line SoC
by using hierarchical block-level models that can be built incrementally.

Usage:
    model = MentalModelSchema(
        project_id="uuid",
        revision=1,
        design=DesignBlock(top_module="fifo", ...),
        requirements=[...],
        verification=VerificationIntent(...),
    )
    json_str = model.to_json()
    model2 = MentalModelSchema.from_json(json_str)
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field, asdict
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════
# Source Traceability — Every fact links back to where it came from
# ═══════════════════════════════════════════════════════════════════════


@dataclass
class SourceReference:
    """Where a fact came from. Used for traceability and anti-hallucination."""
    file: str = ""            # e.g., "fifo.sv" or "spec.pdf"
    line: int = 0             # line number in RTL (0 = unknown)
    page: int = 0             # page number in spec (0 = N/A)
    section: str = ""         # spec section e.g., "3.2.1"
    artifact_id: str = ""     # DB artifact UUID
    excerpt: str = ""         # short excerpt of the source text


# ═══════════════════════════════════════════════════════════════════════
# Design Structure — Ports, Parameters, Clocks, FSMs, Protocols
# ═══════════════════════════════════════════════════════════════════════


@dataclass
class PortInfo:
    """A single I/O port of a module."""
    name: str
    direction: str = "input"  # "input" | "output" | "inout"
    width: int = 1
    bus_range: str = ""       # e.g., "[7:0]"
    port_type: str = "logic"  # logic | wire | reg
    description: str = ""
    protocol_role: str = ""   # e.g., "AXI AWADDR", "handshake valid"
    source_ref: SourceReference = field(default_factory=SourceReference)


@dataclass
class ParameterInfo:
    """A module parameter or localparam."""
    name: str
    default_value: str = ""
    description: str = ""
    source_ref: SourceReference = field(default_factory=SourceReference)


@dataclass
class ClockDomain:
    """A clock domain in the design."""
    name: str                 # e.g., "clk_sys"
    frequency: str = ""       # e.g., "100MHz"
    associated_reset: str = ""
    reset_polarity: str = "active_low"  # "active_low" | "active_high"
    reset_type: str = "synchronous"     # "synchronous" | "asynchronous"
    source_ref: SourceReference = field(default_factory=SourceReference)


@dataclass
class FSMDescription:
    """A finite state machine found in the design."""
    name: str                 # e.g., "wr_fsm"
    state_signal: str = ""    # e.g., "wr_state"
    states: List[str] = field(default_factory=list)  # ["IDLE", "WRITE", "DONE"]
    transitions: List[Dict[str, str]] = field(default_factory=list)
    encoding: str = ""        # "one-hot" | "binary" | "gray"
    source_ref: SourceReference = field(default_factory=SourceReference)


@dataclass
class ProtocolBinding:
    """A protocol detected on a group of ports."""
    protocol: str             # "AXI4" | "AXI4-Lite" | "APB" | "valid-ready" | etc.
    role: str = ""            # "master" | "slave" | "N/A"
    port_group: List[str] = field(default_factory=list)  # Ports belonging to this protocol
    description: str = ""
    source_ref: SourceReference = field(default_factory=SourceReference)


@dataclass
class TransactionFlow:
    """A representative transaction flow for a protocol interface.

    Captures step-by-step signal sequences (e.g. AXI write burst),
    expected latency, and throughput — used for documentation and
    scoreboard reference model generation.
    """
    name: str                 # "AXI Write Burst"
    protocol: str = ""        # "AXI4"
    description: str = ""     # "Single-beat write with BRESP"
    steps: List[Dict[str, str]] = field(default_factory=list)
    # Each step: {"phase": "Address", "signals": {"AWVALID": "1", "AWADDR": "0x100"}, "cycles": "1"}
    latency_cycles: int = 0   # Expected total latency
    throughput: str = ""      # "1 beat/cycle", "4 GB/s"
    constraints: List[str] = field(default_factory=list)  # ["AWREADY must respond within 16 cycles"]
    source_ref: SourceReference = field(default_factory=SourceReference)


@dataclass
class ArbitrationPolicy:
    """Traffic arbitration and QoS policy for multi-master designs.

    Captures how contention between masters is resolved,
    priority levels, bandwidth allocation, and QoS support.
    """
    name: str                 # "round_robin", "fixed_priority", "weighted_round_robin"
    masters: List[str] = field(default_factory=list)   # ["cpu", "dma", "gpu"]
    scheme: str = ""          # "round_robin" | "fixed_priority" | "weighted" | "tdma"
    priority_levels: int = 0  # Number of priority levels (0 = no priority)
    qos_support: bool = False
    qos_levels: int = 0       # Number of QoS levels
    bandwidth_allocation: Dict[str, str] = field(default_factory=dict)
    # e.g., {"cpu": "50%", "dma": "30%", "gpu": "20%"}
    starvation_prevention: bool = False
    description: str = ""
    source_ref: SourceReference = field(default_factory=SourceReference)


@dataclass
class RegisterField:
    """A single register in a register bank.

    Extracted from RTL ``always_ff`` blocks via regex (confidence=1.0)
    or inferred by the LLM from the spec (confidence < 1.0).
    Used by the UVM scoreboard generator to build register models.
    """
    name: str
    bank: str = ""                # Module/register-bank namespace for relative offsets
    offset: int = 0               # Byte offset or address
    width: int = 32               # Bit width
    reset_value: str = "0"        # Reset value (hex or decimal string)
    access: str = "RW"            # "RW" | "RO" | "WO" | "W1C" | "RC"
    fields: List[Dict[str, Any]] = field(default_factory=list)  # Bit-field slices
    description: str = ""
    confidence: float = 1.0       # 1.0 = parser-derived, <1.0 = LLM-inferred
    source_ref: SourceReference = field(default_factory=SourceReference)


@dataclass
class ExpectedBehavior:
    """Stimulus → expected-output mapping for reference-model generation.

    Each entry describes: "When you drive *stimulus*, after *latency_cycles*
    clock cycles, you should observe *expected_output*."

    These are consumed by:
    - UVM scoreboard generator  (Phase 1) to build ``compare()`` logic
    - UnitSim testbench generator (Phase 2) to replace placeholder checks
    """
    id: str
    stimulus: Dict[str, str] = field(default_factory=dict)
    # e.g., {"wr_en": "1", "addr": "0x00", "wr_data": "0xFF"}
    expected_output: Dict[str, str] = field(default_factory=dict)
    # e.g., {"rd_data": "0xFF", "ack": "1"}
    latency_cycles: int = 1
    precondition: str = ""        # e.g., "after_reset", "fifo_not_full"
    description: str = ""
    requirement_ids: List[str] = field(default_factory=list)
    confidence: float = 0.5       # LLM-inferred by default
    source_ref: SourceReference = field(default_factory=SourceReference)


@dataclass
class SubModuleInstance:
    """An instantiated sub-module."""
    instance_name: str        # e.g., "u_fifo_ctrl"
    module_name: str          # e.g., "fifo_ctrl"
    file: str = ""            # e.g., "fifo_ctrl.sv"
    source_ref: SourceReference = field(default_factory=SourceReference)


@dataclass
class DesignBlock:
    """
    Hierarchical design block description.
    For industry-scale: each module gets its own DesignBlock.
    The top-level block references sub-blocks by name.
    """
    # Identity
    top_module: str = ""
    description: str = ""

    # Hierarchy
    modules: List[str] = field(default_factory=list)
    hierarchy_tree: Dict[str, List[str]] = field(default_factory=dict)
    sub_instances: List[SubModuleInstance] = field(default_factory=list)

    # Interface
    ports: List[PortInfo] = field(default_factory=list)
    parameters: List[ParameterInfo] = field(default_factory=list)

    # Timing
    clock_domains: List[ClockDomain] = field(default_factory=list)
    has_cdc_crossings: bool = False
    cdc_crossings: List[Dict[str, str]] = field(default_factory=list)

    # Behavior
    fsms: List[FSMDescription] = field(default_factory=list)
    protocols: List[ProtocolBinding] = field(default_factory=list)
    register_map: List[Dict[str, Any]] = field(default_factory=list)

    # Structured register fields (for UVM register model generation)
    register_fields: List[RegisterField] = field(default_factory=list)

    # Expected behaviors (stimulus → output mappings for reference models)
    expected_behaviors: List[ExpectedBehavior] = field(default_factory=list)

    # Existing assertions parsed from RTL (assert/assume/cover property)
    existing_assertions: List[Dict[str, str]] = field(default_factory=list)

    # Transaction flows (step-by-step protocol sequences)
    transaction_flows: List[TransactionFlow] = field(default_factory=list)

    # Design/protocol constraints captured from specs or LLM analysis.
    constraints: List[str] = field(default_factory=list)

    # Arbitration and QoS policies (for multi-master SoC designs)
    arbitration_policies: List[ArbitrationPolicy] = field(default_factory=list)

    # Metrics (from structural parsing, no LLM needed)
    total_lines: int = 0
    total_files: int = 0
    total_always_blocks: int = 0
    code_style: str = "systemverilog"  # "verilog" | "systemverilog"



# ═══════════════════════════════════════════════════════════════════════
# Requirements — Source-Grounded, Traceable
# ═══════════════════════════════════════════════════════════════════════


class RequirementPriority(str, Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


@dataclass
class Requirement:
    """A single design requirement, traced to spec and RTL."""
    id: str                   # e.g., "REQ-001"
    text: str                 # Natural language requirement
    priority: str = "medium"
    category: str = ""        # "functional" | "timing" | "protocol" | "safety"
    spec_ref: SourceReference = field(default_factory=SourceReference)
    rtl_refs: List[SourceReference] = field(default_factory=list)
    confidence: float = 1.0   # 0.0-1.0, how sure we are this is correct
    needs_human_answer: bool = False
    verified_by: List[str] = field(default_factory=list)  # IDs of tests/assertions


# ═══════════════════════════════════════════════════════════════════════
# Verification Intent — What to test, assert, cover
# ═══════════════════════════════════════════════════════════════════════


@dataclass
class UnitTestIntent:
    """A unit test scenario to be generated."""
    id: str
    name: str
    description: str = ""
    requirement_ids: List[str] = field(default_factory=list)
    stimulus: str = ""
    expected_behavior: str = ""
    priority: str = "medium"
    status: str = "planned"   # "planned" | "generated" | "passed" | "failed"


@dataclass
class FormalPropertyIntent:
    """A formal property (assertion/assumption/cover) to be generated."""
    id: str
    name: str
    property_type: str = "assert"  # "assert" | "assume" | "cover"
    description: str = ""
    requirement_ids: List[str] = field(default_factory=list)
    related_signals: List[str] = field(default_factory=list)
    sva_sketch: str = ""      # Optional SVA pseudocode
    status: str = "planned"


@dataclass
class UVMScenarioIntent:
    """A UVM test scenario to be generated."""
    id: str
    name: str
    description: str = ""
    requirement_ids: List[str] = field(default_factory=list)
    sequence_type: str = ""   # "directed" | "constrained_random" | "stress"
    stimulus_pattern: str = ""
    checker_description: str = ""
    status: str = "planned"


@dataclass
class CoveragePointIntent:
    """A coverage point to be generated."""
    id: str
    name: str
    signal: str = ""
    cover_type: str = "coverpoint"  # "coverpoint" | "cross" | "transition"
    bins_description: str = ""
    requirement_ids: List[str] = field(default_factory=list)
    target_percentage: float = 100.0
    status: str = "planned"


@dataclass
class VerificationIntent:
    """Complete verification plan derived from the mental model."""
    unit_tests: List[UnitTestIntent] = field(default_factory=list)
    formal_properties: List[FormalPropertyIntent] = field(default_factory=list)
    uvm_scenarios: List[UVMScenarioIntent] = field(default_factory=list)
    coverage_points: List[CoveragePointIntent] = field(default_factory=list)


# ═══════════════════════════════════════════════════════════════════════
# Evidence — Results from verification runs feed back into the model
# ═══════════════════════════════════════════════════════════════════════


@dataclass
class ModelEvidence:
    """Evidence from a verification run that updates the mental model."""
    id: str
    run_id: str = ""
    agent: str = ""           # "unitsim" | "formal" | "uvm" | "debug"
    evidence_type: str = ""   # "test_result" | "proof" | "coverage" | "bug_found"
    summary: str = ""
    details: Dict[str, Any] = field(default_factory=dict)
    requirement_ids: List[str] = field(default_factory=list)
    timestamp: str = ""


# ═══════════════════════════════════════════════════════════════════════
# Open Questions — Things the model couldn't determine
# ═══════════════════════════════════════════════════════════════════════


@dataclass
class OpenQuestion:
    """An ambiguity the mental model couldn't resolve automatically."""
    id: str
    question: str
    context: str = ""         # What triggered this question
    suggested_answer: str = ""
    blocking: bool = False    # If True, agents must not proceed until answered
    answered: bool = False
    answer: str = ""
    source_ref: SourceReference = field(default_factory=SourceReference)


# ═══════════════════════════════════════════════════════════════════════
# Project Scan — File system understanding
# ═══════════════════════════════════════════════════════════════════════


@dataclass
class ProjectScan:
    """Results of scanning a project's folder structure."""
    root_path: str = ""
    total_files: int = 0
    total_lines: int = 0
    file_types: Dict[str, int] = field(default_factory=dict)
    directories: Dict[str, int] = field(default_factory=dict)
    rtl_files: List[str] = field(default_factory=list)
    spec_files: List[str] = field(default_factory=list)
    testbench_files: List[str] = field(default_factory=list)
    uvm_files: List[str] = field(default_factory=list)
    formal_files: List[str] = field(default_factory=list)
    filelist_files: List[str] = field(default_factory=list)
    estimated_complexity: str = "small"  # "small" | "medium" | "large" | "very_large"


# ═══════════════════════════════════════════════════════════════════════
# Top-Level Mental Model Schema
# ═══════════════════════════════════════════════════════════════════════


@dataclass
class MentalModelSchema:
    """
    The complete mental model for a project revision.

    This is the ChipStack-style "living knowledge base" that:
    1. Captures design intent from RTL + spec
    2. Provides context for all downstream agents
    3. Receives evidence back from verification runs
    4. Evolves as the design changes

    Designed to scale from simple counters to full SoCs via
    hierarchical block-level models and incremental updates.
    """
    # Identity
    project_id: str = ""
    revision: int = 1
    schema_version: str = "1.0"
    created_at: str = ""
    updated_at: str = ""

    # Source tracking (for incremental updates)
    source_checksums: Dict[str, str] = field(default_factory=dict)

    # Parser provenance. TruthCore structural facts are produced by Slang.
    parser_engine: str = "slang"
    parser_version: str = ""
    parser_diagnostics: List[Dict[str, Any]] = field(default_factory=list)

    # Project structure
    project_scan: ProjectScan = field(default_factory=ProjectScan)

    # Design understanding
    design: DesignBlock = field(default_factory=DesignBlock)

    # Block-level models (for industry-scale hierarchical analysis)
    block_models: Dict[str, DesignBlock] = field(default_factory=dict)

    # Symbol table (all signal names that actually exist in RTL)
    symbol_table: Dict[str, List[str]] = field(default_factory=dict)

    # Matched verification knowledge-base rules and planning hints.
    knowledge_base: Dict[str, Any] = field(default_factory=dict)

    # Requirements with source traceability
    requirements: List[Requirement] = field(default_factory=list)

    # Verification intent
    verification: VerificationIntent = field(default_factory=VerificationIntent)

    # Evidence from runs
    evidence: List[ModelEvidence] = field(default_factory=list)

    # Risks and known issues
    risks: List[str] = field(default_factory=list)

    # Open questions requiring human input
    open_questions: List[OpenQuestion] = field(default_factory=list)

    # ── Serialization ──────────────────────────────────────────────

    def to_dict(self) -> Dict[str, Any]:
        """Convert to JSON-serializable dictionary."""
        return asdict(self)

    def to_json(self, indent: int = 2) -> str:
        """Serialize to JSON string."""
        return json.dumps(self.to_dict(), indent=indent, default=str)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "MentalModelSchema":
        """Reconstruct from a plain dictionary (e.g., from JSON)."""
        data = dict(data or {})
        # Rebuild nested dataclasses from dicts
        if "project_scan" in data and isinstance(data["project_scan"], dict):
            data["project_scan"] = _rebuild_dataclass(ProjectScan, data["project_scan"])
        if "design" in data and isinstance(data["design"], dict):
            data["design"] = _rebuild_design_block(data["design"])
        if "block_models" in data and isinstance(data["block_models"], dict):
            data["block_models"] = {
                k: _rebuild_design_block(v) for k, v in data["block_models"].items()
            }
        if "requirements" in data:
            data["requirements"] = [
                _rebuild_requirement(r) for r in data["requirements"]
            ]
        if "verification" in data and isinstance(data["verification"], dict):
            data["verification"] = _rebuild_verification_intent(data["verification"])
        if "evidence" in data:
            data["evidence"] = [
                _rebuild_dataclass(ModelEvidence, e) if isinstance(e, dict) else e
                for e in data["evidence"]
            ]
        if "open_questions" in data:
            data["open_questions"] = [
                _rebuild_open_question(q) for q in data["open_questions"]
            ]
        allowed_fields = set(cls.__dataclass_fields__.keys())
        unknown_fields = sorted(set(data.keys()) - allowed_fields)
        if unknown_fields:
            logger.warning(
                "Dropping unknown MentalModelSchema fields during load: %s",
                ", ".join(unknown_fields[:20]),
            )
        data = {k: v for k, v in data.items() if k in allowed_fields}
        return cls(**data)

    @classmethod
    def from_json(cls, json_str: str) -> "MentalModelSchema":
        """Deserialize from JSON string."""
        return cls.from_dict(json.loads(json_str))

    # ── Utility ────────────────────────────────────────────────────

    @property
    def summary(self) -> str:
        """Human-readable summary of the mental model."""
        parts = [
            f"MentalModel rev{self.revision}: ",
            f"top={self.design.top_module}, ",
            f"{len(self.design.modules)} modules, ",
            f"{len(self.design.ports)} ports, ",
            f"{len(self.requirements)} requirements, ",
            f"{len(self.verification.unit_tests)} unit tests, ",
            f"{len(self.verification.formal_properties)} formal props, ",
            f"{len(self.verification.uvm_scenarios)} UVM scenarios, ",
            f"{len(self.verification.coverage_points)} coverage points, ",
            f"{len(self.open_questions)} open questions",
        ]
        # Include enrichment counts when populated
        if self.design.register_fields:
            parts.append(f", {len(self.design.register_fields)} registers")
        if self.design.expected_behaviors:
            parts.append(f", {len(self.design.expected_behaviors)} expected behaviors")
        if self.design.constraints:
            parts.append(f", {len(self.design.constraints)} constraints")
        if self.design.existing_assertions:
            parts.append(f", {len(self.design.existing_assertions)} existing assertions")
        kb_hints = self.knowledge_base.get("plan_hints", {}) if isinstance(self.knowledge_base, dict) else {}
        if isinstance(kb_hints, dict):
            kb_count = sum(len(v) for v in kb_hints.values() if isinstance(v, list))
            if kb_count:
                parts.append(f", {kb_count} KB hints")
        return "".join(parts)

    @property
    def has_blocking_questions(self) -> bool:
        """True if there are unanswered blocking questions."""
        return any(
            q.blocking and not q.answered for q in self.open_questions
        )

    @property
    def unanswered_questions(self) -> List[OpenQuestion]:
        """All unanswered open questions."""
        return [q for q in self.open_questions if not q.answered]

    @property
    def covered_requirements(self) -> List[Requirement]:
        """Requirements that have at least one verification target."""
        return [r for r in self.requirements if r.verified_by]

    @property
    def uncovered_requirements(self) -> List[Requirement]:
        """Requirements with no verification target yet."""
        return [r for r in self.requirements if not r.verified_by]

    def compute_source_checksums(self, file_contents: Dict[str, str]) -> Dict[str, str]:
        """Compute SHA-256 checksums for source files."""
        return {
            path: hashlib.sha256(content.encode()).hexdigest()
            for path, content in file_contents.items()
        }

    def sources_changed(self, new_checksums: Dict[str, str]) -> bool:
        """Check if any source files have changed since last build."""
        if not self.source_checksums:
            return True
        return self.source_checksums != new_checksums

    def get_changed_files(self, new_checksums: Dict[str, str]) -> List[str]:
        """Return list of files that changed since last build."""
        changed = []
        for path, checksum in new_checksums.items():
            if self.source_checksums.get(path) != checksum:
                changed.append(path)
        # Also check for deleted files
        for path in self.source_checksums:
            if path not in new_checksums:
                changed.append(path)
        return changed


# ═══════════════════════════════════════════════════════════════════════
# Private helpers — Rebuild nested dataclasses from dicts
# ═══════════════════════════════════════════════════════════════════════


def _rebuild_source_ref(data: Any) -> SourceReference:
    if isinstance(data, dict):
        return _rebuild_dataclass(SourceReference, data)
    return data if isinstance(data, SourceReference) else SourceReference()


def _dataclass_kwargs(cls: Any, data: Dict[str, Any]) -> Dict[str, Any]:
    allowed = set(getattr(cls, "__dataclass_fields__", {}).keys())
    payload = dict(data or {})
    unknown_fields = sorted(set(payload.keys()) - allowed)
    if unknown_fields:
        logger.warning(
            "Dropping unknown %s fields during load: %s",
            getattr(cls, "__name__", str(cls)),
            ", ".join(unknown_fields[:20]),
        )
    return {k: v for k, v in payload.items() if k in allowed}


def _rebuild_dataclass(cls: Any, data: Dict[str, Any]) -> Any:
    return cls(**_dataclass_kwargs(cls, data))


def _rebuild_with_source_ref(cls: Any, data: Dict[str, Any]) -> Any:
    payload = dict(data or {})
    payload["source_ref"] = _rebuild_source_ref(payload.get("source_ref", {}))
    return _rebuild_dataclass(cls, payload)


def _rebuild_design_block(data: Dict[str, Any]) -> DesignBlock:
    data = dict(data or {})
    if "ports" in data:
        data["ports"] = [
            _rebuild_with_source_ref(PortInfo, p)
            if isinstance(p, dict) else p
            for p in data["ports"]
        ]
    if "parameters" in data:
        data["parameters"] = [
            _rebuild_with_source_ref(ParameterInfo, p)
            if isinstance(p, dict) else p
            for p in data["parameters"]
        ]
    if "clock_domains" in data:
        data["clock_domains"] = [
            _rebuild_with_source_ref(ClockDomain, c)
            if isinstance(c, dict) else c
            for c in data["clock_domains"]
        ]
    if "fsms" in data:
        data["fsms"] = [
            _rebuild_with_source_ref(FSMDescription, f)
            if isinstance(f, dict) else f
            for f in data["fsms"]
        ]
    if "protocols" in data:
        data["protocols"] = [
            _rebuild_with_source_ref(ProtocolBinding, p)
            if isinstance(p, dict) else p
            for p in data["protocols"]
        ]
    if "sub_instances" in data:
        data["sub_instances"] = [
            _rebuild_with_source_ref(SubModuleInstance, s)
            if isinstance(s, dict) else s
            for s in data["sub_instances"]
        ]
    if "register_fields" in data:
        data["register_fields"] = [
            _rebuild_with_source_ref(RegisterField, r)
            if isinstance(r, dict) else r
            for r in data["register_fields"]
        ]
    if "expected_behaviors" in data:
        data["expected_behaviors"] = [
            _rebuild_with_source_ref(ExpectedBehavior, e)
            if isinstance(e, dict) else e
            for e in data["expected_behaviors"]
        ]
    # existing_assertions is List[Dict[str, str]] — no rebuild needed
    if "transaction_flows" in data:
        data["transaction_flows"] = [
            _rebuild_with_source_ref(TransactionFlow, t)
            if isinstance(t, dict) else t
            for t in data["transaction_flows"]
        ]
    if "arbitration_policies" in data:
        data["arbitration_policies"] = [
            _rebuild_with_source_ref(ArbitrationPolicy, a)
            if isinstance(a, dict) else a
            for a in data["arbitration_policies"]
        ]
    return _rebuild_dataclass(DesignBlock, data)


def _rebuild_requirement(data: Any) -> Requirement:
    if isinstance(data, dict):
        data = dict(data)
        data["spec_ref"] = _rebuild_source_ref(data.get("spec_ref", {}))
        data["rtl_refs"] = [
            _rebuild_source_ref(r) for r in data.get("rtl_refs", [])
        ]
        return _rebuild_dataclass(Requirement, data)
    return data


def _rebuild_verification_intent(data: Dict[str, Any]) -> VerificationIntent:
    data = dict(data or {})
    return VerificationIntent(
        unit_tests=[
            _rebuild_dataclass(UnitTestIntent, t) if isinstance(t, dict) else t
            for t in data.get("unit_tests", [])
        ],
        formal_properties=[
            _rebuild_dataclass(FormalPropertyIntent, f) if isinstance(f, dict) else f
            for f in data.get("formal_properties", [])
        ],
        uvm_scenarios=[
            _rebuild_dataclass(UVMScenarioIntent, u) if isinstance(u, dict) else u
            for u in data.get("uvm_scenarios", [])
        ],
        coverage_points=[
            _rebuild_dataclass(CoveragePointIntent, c) if isinstance(c, dict) else c
            for c in data.get("coverage_points", [])
        ],
    )


def _rebuild_open_question(data: Any) -> OpenQuestion:
    if isinstance(data, dict):
        data = dict(data)
        data["source_ref"] = _rebuild_source_ref(data.get("source_ref", {}))
        return _rebuild_dataclass(OpenQuestion, data)
    return data
