"""
Agent 3 — Mental Model Builder

Cross-references the specification (from Agent 1) with the RTL analysis
(from Agent 2) to build a comprehensive verification blueprint.

This is the "thinking" phase — the AI plans what needs to be verified
BEFORE writing any verification code.

Output: MentalModel — drives everything in Phase 3 (file generation).
"""

from __future__ import annotations

import json
import re

from core.ai_client import AIClient
from core.logger import get_logger
from core.models import (
    AssertionTarget,
    CoveragePoint,
    DesignSpecification,
    FileToGenerate,
    InputSequence,
    MentalModel,
    RTLAnalysis,
    TestScenario,
)

logger = get_logger("Agent3.MentalModel")

# ── System prompt for verification planning ───────────────────────────
SYSTEM_PROMPT = """\
You are a world-class chip verification architect.

TASK: Given a design specification and RTL analysis, create a comprehensive
verification plan (mental model) that will drive the generation of all
verification files.

You must think deeply about:
1. What needs to be verified (every spec requirement → test scenario)
2. What assertions are needed (every behavioral rule → SVA property)
3. What coverage is needed (every signal, state, transition → coverpoint)
4. What stimulus patterns will exercise the design thoroughly
5. What corner cases could reveal bugs

RULES:
1. Every spec requirement MUST map to at least one test scenario.
2. Every behavioral rule MUST have an assertion.
3. Coverage must target ≥95% for lines, branches, and toggles.
4. Include at least 5 corner cases.
5. Be specific — use actual signal names from the RTL.

OUTPUT FORMAT — Return ONLY a valid JSON object:
{
    "verification_plan": [
        {
            "name": "test_name",
            "description": "what this test verifies",
            "priority": "high|medium|low",
            "spec_requirement": "which spec requirement this maps to",
            "stimulus": "what inputs to drive",
            "expected_output": "what should happen"
        }
    ],
    "assertion_plan": [
        {
            "name": "assertion_name",
            "property_description": "what property to check in plain English",
            "assertion_type": "assert|assume|cover",
            "related_signals": ["signal1", "signal2"],
            "spec_requirement": "traceability to spec"
        }
    ],
    "coverage_plan": [
        {
            "name": "coverpoint_name",
            "signal": "signal to cover",
            "cover_type": "coverpoint|cross|transition",
            "bins_description": "what bins to create",
            "target_percentage": 100.0
        }
    ],
    "spec_to_rtl_mapping": {
        "spec requirement text": "RTL construct that implements it"
    },
    "critical_paths": [
        "string — high-priority areas to focus testing"
    ],
    "corner_cases": [
        "string — edge case that must be tested"
    ],
    "clock_scheme": "string — e.g., 10ns period, 50% duty cycle",
    "reset_sequence": "string — e.g., Assert rst_n low for 20ns, then release",
    "input_sequences": [
        {
            "name": "sequence_name",
            "description": "what this sequence does",
            "steps": ["step 1", "step 2"]
        }
    ]
}
"""


class MentalModelBuilder:
    """Agent 3: Builds a verification blueprint from spec + RTL analysis."""

    def __init__(self, ai_client: AIClient) -> None:
        self.ai_client = ai_client

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    async def build(
        self,
        spec: DesignSpecification,
        rtl_code: str,
        rtl_analysis: RTLAnalysis,
    ) -> MentalModel:
        """Build the mental model from specification and RTL analysis.

        Args:
            spec: Parsed specification (from Agent 1)
            rtl_code: Raw RTL source code
            rtl_analysis: Structured RTL analysis (from Agent 2)

        Returns:
            MentalModel — the complete verification blueprint
        """
        logger.info("[*]  Mental Model: Building verification blueprint …")

        # Call active AI provider/runtime with structured context so the client can
        # enforce prompt budgets when the local context window is limited.
        response = await self.ai_client.generate_with_context(
            system_prompt=SYSTEM_PROMPT,
            context_blocks={
                "DESIGN SPECIFICATION": self._format_spec(spec),
                "RTL SOURCE CODE": rtl_code,
                "RTL STRUCTURAL ANALYSIS": self._format_rtl_analysis(rtl_analysis),
            },
            focus_instruction=(
                "Build a complete verification mental model with strict spec traceability "
                "and exact DUT signal names."
            ),
            task_instruction=(
                "Return only valid JSON using the exact schema from the system prompt. "
                "Do not include markdown fences or commentary."
            ),
            temperature=0.5,
        )

        # Parse response into MentalModel
        mental_model = self._parse_response(response)

        # Never let downstream generation run with an empty plan.
        if (
            not mental_model.verification_plan
            or not mental_model.assertion_plan
            or not mental_model.coverage_plan
        ):
            logger.warning(
                "Mental model response was incomplete; augmenting with deterministic fallback plan."
            )
            fallback = self._build_fallback_model(spec, rtl_analysis)
            if not mental_model.verification_plan:
                mental_model.verification_plan = fallback.verification_plan
            if not mental_model.assertion_plan:
                mental_model.assertion_plan = fallback.assertion_plan
            if not mental_model.coverage_plan:
                mental_model.coverage_plan = fallback.coverage_plan
            if not mental_model.corner_cases:
                mental_model.corner_cases = fallback.corner_cases
            if not mental_model.critical_paths:
                mental_model.critical_paths = fallback.critical_paths
            if not mental_model.input_sequences:
                mental_model.input_sequences = fallback.input_sequences
            if not mental_model.clock_scheme:
                mental_model.clock_scheme = fallback.clock_scheme
            if not mental_model.reset_sequence:
                mental_model.reset_sequence = fallback.reset_sequence

        # Inject the file generation order
        mental_model.files_to_generate = self._build_file_order(rtl_analysis)

        logger.info("[*]  Mental Model: %s", mental_model.summary)
        return mental_model

    # ------------------------------------------------------------------
    # Prompt assembly
    # ------------------------------------------------------------------
    def _build_prompt(
        self,
        spec: DesignSpecification,
        rtl_code: str,
        rtl_analysis: RTLAnalysis,
    ) -> str:
        """Assemble the structured prompt for the AI runtime."""

        # Spec summary
        spec_section = self._format_spec(spec)

        # RTL summary (from regex analysis)
        rtl_summary = self._format_rtl_analysis(rtl_analysis)

        return (
            f"╔══════════════════════════════════════════════════════════════╗\n"
            f"║  BUILDING VERIFICATION MENTAL MODEL                         ║\n"
            f"╚══════════════════════════════════════════════════════════════╝\n\n"
            f"═══ DESIGN SPECIFICATION ═══════════════════════════════════════\n"
            f"{spec_section}\n\n"
            f"═══ RTL SOURCE CODE ═════════════════════════════════════════════\n"
            f"{rtl_code}\n\n"
            f"═══ RTL STRUCTURAL ANALYSIS ══════════════════════════════════════\n"
            f"{rtl_summary}\n\n"
            f"═══ YOUR TASK ══════════════════════════════════════════════════\n"
            f"Create the comprehensive verification mental model as specified.\n"
            f"Use the EXACT signal names from the RTL code above.\n"
            f"Cover EVERY requirement from the specification.\n"
        )

    def _format_spec(self, spec: DesignSpecification) -> str:
        """Format the spec into a structured text block."""
        lines = [
            f"Module: {spec.module_name}",
            f"Description: {spec.description}",
            "",
            "Ports:",
        ]
        for port in spec.ports:
            lines.append(f"  {port.declaration}  — {port.description}")

        lines.append("\nFunctional Requirements:")
        for i, req in enumerate(spec.functional_requirements, 1):
            lines.append(f"  R{i}: {req}")

        lines.append("\nConstraints:")
        for c in spec.constraints:
            lines.append(f"  • {c}")

        lines.append(f"\nReset Strategy: {spec.reset_strategy}")
        lines.append(f"Clock Domains: {', '.join(spec.clock_domains) or 'single'}")

        lines.append("\nEdge Cases:")
        for e in spec.edge_cases:
            lines.append(f"  • {e}")

        return "\n".join(lines)

    def _format_rtl_analysis(self, analysis: RTLAnalysis) -> str:
        """Format the RTL analysis into a structured text block."""
        lines = [
            f"Top module: {analysis.top_module}",
            f"Code style: {analysis.code_style}",
            f"Clock signal: {analysis.clock_signal}",
            f"Reset signal: {analysis.reset_signal}",
            f"Has FSM: {'yes — states: ' + ', '.join(analysis.fsm_states) if analysis.has_fsm else 'no'}",
        ]

        lines.append("\nPorts:")
        for name, port in analysis.port_map.items():
            lines.append(f"  {port.declaration}")

        if analysis.always_blocks:
            lines.append(f"\nAlways blocks ({len(analysis.always_blocks)}):")
            for blk in analysis.always_blocks:
                desc = f" — {blk.description}" if blk.description else ""
                lines.append(f"  {blk.block_type} @({blk.sensitivity}){desc}")

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Response parsing
    # ------------------------------------------------------------------
    def _parse_response(self, response: str) -> MentalModel:
        """Parse the model JSON response into a MentalModel."""
        json_str = self._extract_json(response)

        try:
            data = json.loads(json_str)
        except json.JSONDecodeError as e:
            logger.error("Failed to parse mental model JSON: %s", e)
            return MentalModel()

        # Build verification plan
        verification_plan = [
            TestScenario(
                name=t.get("name", ""),
                description=t.get("description", ""),
                priority=t.get("priority", "medium"),
                spec_requirement=t.get("spec_requirement", ""),
                stimulus=t.get("stimulus", ""),
                expected_output=t.get("expected_output", ""),
            )
            for t in data.get("verification_plan", [])
        ]

        # Build assertion plan
        assertion_plan = [
            AssertionTarget(
                name=a.get("name", ""),
                property_description=a.get("property_description", ""),
                assertion_type=a.get("assertion_type", "assert"),
                related_signals=a.get("related_signals", []),
                spec_requirement=a.get("spec_requirement", ""),
            )
            for a in data.get("assertion_plan", [])
        ]

        # Build coverage plan
        coverage_plan = [
            CoveragePoint(
                name=c.get("name", ""),
                signal=c.get("signal", ""),
                cover_type=c.get("cover_type", "coverpoint"),
                bins_description=c.get("bins_description", ""),
                target_percentage=float(c.get("target_percentage", 100.0)),
            )
            for c in data.get("coverage_plan", [])
        ]

        # Build input sequences
        input_sequences = [
            InputSequence(
                name=s.get("name", ""),
                description=s.get("description", ""),
                steps=s.get("steps", []),
            )
            for s in data.get("input_sequences", [])
        ]

        return MentalModel(
            verification_plan=verification_plan,
            assertion_plan=assertion_plan,
            coverage_plan=coverage_plan,
            spec_to_rtl_mapping=data.get("spec_to_rtl_mapping", {}),
            critical_paths=data.get("critical_paths", []),
            corner_cases=data.get("corner_cases", []),
            clock_scheme=data.get("clock_scheme", ""),
            reset_sequence=data.get("reset_sequence", ""),
            input_sequences=input_sequences,
        )

    def _build_fallback_model(
        self,
        spec: DesignSpecification,
        rtl_analysis: RTLAnalysis,
    ) -> MentalModel:
        """Create a deterministic minimum viable plan when JSON parsing fails."""
        requirements = spec.functional_requirements or [
            "Validate DUT behavior against the provided specification text."
        ]

        verification_plan: list[TestScenario] = []
        for idx, req in enumerate(requirements[:12], 1):
            verification_plan.append(
                TestScenario(
                    name=f"req_{idx:02d}_test",
                    description=f"Requirement-driven verification for R{idx}.",
                    priority="high" if idx <= 3 else "medium",
                    spec_requirement=req,
                    stimulus="Drive legal and boundary input combinations for this requirement.",
                    expected_output="Observed outputs must satisfy the requirement under all sampled cases.",
                )
            )

        assertion_plan: list[AssertionTarget] = []
        if spec.reset_strategy:
            assertion_plan.append(
                AssertionTarget(
                    name="reset_behavior_assertion",
                    property_description=f"Reset behavior follows spec: {spec.reset_strategy}",
                    assertion_type="assert",
                    related_signals=[rtl_analysis.reset_signal],
                    spec_requirement="Reset strategy",
                )
            )

        related_signal_names = spec.port_names[:6]
        if not related_signal_names:
            related_signal_names = list(rtl_analysis.port_map.keys())[:6]
        for idx, req in enumerate(requirements[:8], 1):
            assertion_plan.append(
                AssertionTarget(
                    name=f"req_{idx:02d}_assertion",
                    property_description=f"Requirement R{idx} must always hold.",
                    assertion_type="assert",
                    related_signals=related_signal_names,
                    spec_requirement=req,
                )
            )

        coverage_plan: list[CoveragePoint] = []
        coverage_ports = spec.ports[:16]
        if not coverage_ports and rtl_analysis.port_map:
            coverage_ports = list(rtl_analysis.port_map.values())[:16]

        for port in coverage_ports:
            coverage_plan.append(
                CoveragePoint(
                    name=f"cp_{port.name}",
                    signal=port.name,
                    cover_type="coverpoint",
                    bins_description="Cover zero, one, boundary, and transition activity where applicable.",
                    target_percentage=95.0,
                )
            )

        cross_port_names = spec.port_names
        if not cross_port_names:
            cross_port_names = list(rtl_analysis.port_map.keys())

        if len(cross_port_names) >= 2:
            coverage_plan.append(
                CoveragePoint(
                    name=f"cx_{cross_port_names[0]}_{cross_port_names[1]}",
                    signal=f"{cross_port_names[0]} x {cross_port_names[1]}",
                    cover_type="cross",
                    bins_description="Cross input interaction coverage for top-level behavior.",
                    target_percentage=95.0,
                )
            )

        corner_cases = spec.edge_cases[:10] or [
            "Reset asserted during active stimulus.",
            "Back-to-back transactions without idle cycles.",
            "Boundary values on all data/control inputs.",
            "Simultaneous control signal toggles.",
            "Sustained maximum throughput traffic.",
        ]

        input_sequences = [
            InputSequence(
                name="reset_then_smoke",
                description="Apply reset, then execute a simple legal transaction sequence.",
                steps=[
                    "Assert reset for a fixed number of cycles.",
                    "Release reset and wait for stabilization.",
                    "Drive a nominal transaction and check outputs.",
                ],
            ),
            InputSequence(
                name="corner_case_sweep",
                description="Exercise edge and boundary conditions from the specification.",
                steps=[
                    "Drive minimum input values.",
                    "Drive maximum input values.",
                    "Toggle controls near clock boundaries.",
                ],
            ),
        ]

        return MentalModel(
            verification_plan=verification_plan,
            assertion_plan=assertion_plan,
            coverage_plan=coverage_plan,
            spec_to_rtl_mapping={},
            critical_paths=[req for req in requirements[:5]],
            corner_cases=corner_cases,
            clock_scheme="10ns period, 50% duty cycle",
            reset_sequence=(
                spec.reset_strategy
                if spec.reset_strategy
                else f"Assert {rtl_analysis.reset_signal} for 2-5 cycles, then release."
            ),
            input_sequences=input_sequences,
        )

    # ------------------------------------------------------------------
    # File generation order
    # ------------------------------------------------------------------
    def _build_file_order(self, analysis: RTLAnalysis) -> list[FileToGenerate]:
        """Return the fixed 11-file generation order with dependency info."""
        return [
            FileToGenerate(
                file_id=1,
                filename="interface.sv",
                display_name="Signal Interface",
                depends_on=[],
                generation_notes="Bundle all DUT signals with modports and clocking blocks.",
            ),
            FileToGenerate(
                file_id=2,
                filename="coverage.sv",
                display_name="Coverage Model",
                depends_on=[],
                generation_notes="Covergroups for all ports, cross-coverage, transitions.",
            ),
            FileToGenerate(
                file_id=3,
                filename="assertions.sv",
                display_name="SVA Assertions",
                depends_on=[],
                generation_notes="assert/assume/cover properties for all spec requirements.",
            ),
            FileToGenerate(
                file_id=4,
                filename="driver.sv",
                display_name="Driver Component",
                depends_on=["interface.sv"],
                generation_notes="Drive stimulus through interface DRIVER modport.",
            ),
            FileToGenerate(
                file_id=5,
                filename="monitor.sv",
                display_name="Monitor Component",
                depends_on=["interface.sv"],
                generation_notes="Observe DUT outputs via interface MONITOR modport.",
            ),
            FileToGenerate(
                file_id=6,
                filename="scoreboard.sv",
                display_name="Scoreboard",
                depends_on=[],
                generation_notes="Reference model + output comparison + PASS/FAIL.",
            ),
            FileToGenerate(
                file_id=7,
                filename="sequences.sv",
                display_name="Test Sequences",
                depends_on=["driver.sv"],
                generation_notes="Named test sequences using driver tasks.",
            ),
            FileToGenerate(
                file_id=8,
                filename="environment.sv",
                display_name="Verification Environment",
                depends_on=["driver.sv", "monitor.sv", "scoreboard.sv", "coverage.sv"],
                generation_notes="Tie all components together, wire mailboxes.",
            ),
            FileToGenerate(
                file_id=9,
                filename="tb.sv",
                display_name="Top Testbench",
                depends_on=["environment.sv", "assertions.sv", "sequences.sv"],
                generation_notes="DUT + environment + clock/reset + run all tests.",
            ),
            FileToGenerate(
                file_id=10,
                filename="Makefile",
                display_name="Build Script",
                depends_on=["tb.sv"],
                generation_notes="iverilog compile + simulate targets.",
            ),
            FileToGenerate(
                file_id=11,
                filename="test_plan.md",
                display_name="Test Plan Document",
                depends_on=["coverage.sv", "assertions.sv", "tb.sv"],
                generation_notes="Human-readable test strategy with traceability.",
            ),
        ]

    @staticmethod
    def _extract_json(text: str) -> str:
        """Extract JSON from a response that may contain markdown code blocks."""
        json_match = re.search(r"```(?:json)?\s*\n(.*?)```", text, re.DOTALL)
        if json_match:
            return json_match.group(1).strip()

        brace_start = text.find("{")
        if brace_start != -1:
            depth = 0
            for i in range(brace_start, len(text)):
                if text[i] == "{":
                    depth += 1
                elif text[i] == "}":
                    depth -= 1
                    if depth == 0:
                        return text[brace_start : i + 1]

        return text.strip()
