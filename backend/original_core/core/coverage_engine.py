"""
Phase D — Coverage Closure Engine.

Automated coverage-driven verification loop:
  1. Parse coverage reports from simulation
  2. Identify uncovered bins/states/transitions
  3. Generate targeted test sequences to hit uncovered areas
  4. Track progress toward coverage goals

Supports: iverilog VCD-based coverage, VCS, Questa report formats.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import config
from core.ai_client import AIClient
from core.logger import get_logger

logger = get_logger("CoverageEngine")


@dataclass
class CoverageBin:
    """A single coverage bin."""
    name: str
    hits: int = 0
    goal: int = 1
    covered: bool = False

    @property
    def percentage(self) -> float:
        if self.goal == 0:
            return 100.0
        return min(100.0, (self.hits / self.goal) * 100.0)


@dataclass
class CoveragePoint:
    """A single coverpoint with bins."""
    name: str
    bins: list[CoverageBin] = field(default_factory=list)
    total_bins: int = 0
    covered_bins: int = 0

    @property
    def percentage(self) -> float:
        if self.total_bins == 0:
            return 100.0
        return (self.covered_bins / self.total_bins) * 100.0


@dataclass
class CoverageGroup:
    """A covergroup with coverpoints."""
    name: str
    coverpoints: list[CoveragePoint] = field(default_factory=list)
    overall_percentage: float = 0.0


@dataclass
class CoverageReport:
    """Full coverage report from simulation."""
    covergroups: list[CoverageGroup] = field(default_factory=list)
    line_coverage: float = 0.0
    branch_coverage: float = 0.0
    toggle_coverage: float = 0.0
    functional_coverage: float = 0.0
    assertion_coverage: float = 0.0

    # Derived
    uncovered_bins: list[str] = field(default_factory=list)
    uncovered_states: list[str] = field(default_factory=list)
    uncovered_transitions: list[str] = field(default_factory=list)

    @property
    def overall_coverage(self) -> float:
        metrics = [m for m in [self.line_coverage, self.branch_coverage,
                               self.toggle_coverage, self.functional_coverage] if m > 0]
        if not metrics:
            return 0.0
        return sum(metrics) / len(metrics)

    @property
    def meets_goal(self) -> bool:
        return self.overall_coverage >= 95.0

    @property
    def summary(self) -> str:
        return (
            f"Coverage: overall={self.overall_coverage:.1f}%, "
            f"line={self.line_coverage:.1f}%, branch={self.branch_coverage:.1f}%, "
            f"functional={self.functional_coverage:.1f}%, "
            f"uncovered_bins={len(self.uncovered_bins)}"
        )


class CoverageEngine:
    """Parse coverage reports and drive coverage closure."""

    def __init__(self, ai_client: AIClient) -> None:
        self.ai_client = ai_client
        self.iteration = 0
        self.history: list[CoverageReport] = []

    # ------------------------------------------------------------------
    # Parse coverage reports
    # ------------------------------------------------------------------

    def parse_report(self, report_path: Path) -> CoverageReport:
        """Parse a coverage report file.

        Auto-detects format: VCS, Questa, or custom text format.
        """
        if not report_path.exists():
            logger.warning("Coverage report not found: %s", report_path)
            return CoverageReport()

        content = report_path.read_text(encoding="utf-8", errors="replace")

        # Try different parsers
        if "Coverage Summary" in content or "covergroup" in content.lower():
            return self._parse_text_report(content)
        elif content.strip().startswith("{"):
            return self._parse_json_report(content)
        else:
            return self._parse_text_report(content)

    def _parse_text_report(self, content: str) -> CoverageReport:
        """Parse a text-format coverage report."""
        report = CoverageReport()

        # Extract line coverage
        line_match = re.search(r"line\s+coverage[:\s]+(\d+\.?\d*)%", content, re.IGNORECASE)
        if line_match:
            report.line_coverage = float(line_match.group(1))

        # Extract branch coverage
        branch_match = re.search(r"branch\s+coverage[:\s]+(\d+\.?\d*)%", content, re.IGNORECASE)
        if branch_match:
            report.branch_coverage = float(branch_match.group(1))

        # Extract toggle coverage
        toggle_match = re.search(r"toggle\s+coverage[:\s]+(\d+\.?\d*)%", content, re.IGNORECASE)
        if toggle_match:
            report.toggle_coverage = float(toggle_match.group(1))

        # Extract functional coverage
        func_match = re.search(r"functional\s+coverage[:\s]+(\d+\.?\d*)%", content, re.IGNORECASE)
        if func_match:
            report.functional_coverage = float(func_match.group(1))

        # Extract uncovered bins
        for match in re.finditer(r"(?:uncovered|missed)\s+(?:bin|point)[:\s]+(\S+)", content, re.IGNORECASE):
            report.uncovered_bins.append(match.group(1))

        # Extract uncovered states
        for match in re.finditer(r"(?:unreached|uncovered)\s+state[:\s]+(\S+)", content, re.IGNORECASE):
            report.uncovered_states.append(match.group(1))

        return report

    def _parse_json_report(self, content: str) -> CoverageReport:
        """Parse a JSON-format coverage report."""
        import json
        try:
            data = json.loads(content)
            report = CoverageReport()
            report.line_coverage = data.get("line_coverage", 0)
            report.branch_coverage = data.get("branch_coverage", 0)
            report.toggle_coverage = data.get("toggle_coverage", 0)
            report.functional_coverage = data.get("functional_coverage", 0)
            report.uncovered_bins = data.get("uncovered_bins", [])
            return report
        except Exception as e:
            logger.warning("Failed to parse JSON coverage report: %s", e)
            return CoverageReport()

    # ------------------------------------------------------------------
    # Coverage gap analysis
    # ------------------------------------------------------------------

    def analyze_gaps(self, report: CoverageReport) -> dict:
        """Analyze coverage gaps and recommend targeted tests.

        Returns:
            {
                "gaps": [...],
                "recommendations": [...],
                "priority_order": [...],
            }
        """
        gaps = []
        recommendations = []

        # Line coverage gap
        if report.line_coverage < 95:
            gap = 95 - report.line_coverage
            gaps.append(f"Line coverage: {report.line_coverage:.1f}% (need {gap:.1f}% more)")
            recommendations.append("Add tests that exercise uncovered code paths")

        # Branch coverage gap
        if report.branch_coverage < 90:
            gap = 90 - report.branch_coverage
            gaps.append(f"Branch coverage: {report.branch_coverage:.1f}% (need {gap:.1f}% more)")
            recommendations.append("Add tests for both true/false branches of conditionals")

        # Functional coverage gap
        if report.functional_coverage < 95:
            gap = 95 - report.functional_coverage
            gaps.append(f"Functional coverage: {report.functional_coverage:.1f}% (need {gap:.1f}% more)")
            recommendations.append("Add directed tests targeting uncovered functional scenarios")

        # Uncovered bins
        for ubin in report.uncovered_bins:
            gaps.append(f"Uncovered bin: {ubin}")
            recommendations.append(f"Create directed sequence to hit bin '{ubin}'")

        # Uncovered states
        for state in report.uncovered_states:
            gaps.append(f"Unreached state: {state}")
            recommendations.append(f"Create sequence that reaches state '{state}'")

        # Uncovered transitions
        for trans in report.uncovered_transitions:
            gaps.append(f"Uncovered transition: {trans}")
            recommendations.append(f"Create sequence that exercises transition '{trans}'")

        return {
            "gaps": gaps,
            "recommendations": recommendations,
            "priority_order": self._prioritize(gaps),
            "meets_goal": report.meets_goal,
        }

    def _prioritize(self, gaps: list[str]) -> list[int]:
        """Prioritize gaps: states > bins > coverage metrics."""
        priorities = []
        for i, gap in enumerate(gaps):
            if "state" in gap.lower():
                priorities.append((0, i))
            elif "transition" in gap.lower():
                priorities.append((1, i))
            elif "bin" in gap.lower():
                priorities.append((2, i))
            else:
                priorities.append((3, i))
        priorities.sort()
        return [idx for _, idx in priorities]

    # ------------------------------------------------------------------
    # Generate targeted tests
    # ------------------------------------------------------------------

    async def generate_targeted_tests(
        self,
        report: CoverageReport,
        existing_tests: str,
        rtl_code: str,
        module_name: str,
    ) -> str:
        """Use LLM to generate targeted tests for uncovered areas.

        Args:
            report: Current coverage report
            existing_tests: Current test code
            rtl_code: RTL design code
            module_name: DUT module name

        Returns:
            Generated test code (SystemVerilog)
        """
        gaps = self.analyze_gaps(report)

        if gaps["meets_goal"]:
            logger.info("Coverage goal met! No targeted tests needed.")
            return ""

        prompt = f"""\
You are a UVM verification engineer. Generate TARGETED test sequences
to close the following coverage gaps:

COVERAGE GAPS:
{chr(10).join(f'  - {g}' for g in gaps['gaps'])}

RECOMMENDATIONS:
{chr(10).join(f'  - {r}' for r in gaps['recommendations'])}

RULES:
1. Generate UVM sequences that SPECIFICALLY target the uncovered areas
2. Use constrained random with directed constraints
3. Each sequence should have a clear name: {module_name}_targeted_seq_N
4. Add comments explaining which gap each sequence targets
5. Must work with the existing testbench structure

Generate ONLY the new sequences as SystemVerilog code.
"""

        context = {
            "RTL CODE": rtl_code,
            "EXISTING TESTS": existing_tests,
            "COVERAGE REPORT": report.summary,
        }

        response = await self.ai_client.generate_with_context(
            system_prompt=prompt,
            context_blocks=context,
            focus_instruction=f"Generate targeted tests for {module_name} to close coverage gaps",
            task_instruction="Create sequences that hit uncovered bins, states, and transitions",
        )

        self.iteration += 1
        self.history.append(report)

        return response

    # ------------------------------------------------------------------
    # Coverage progress tracking
    # ------------------------------------------------------------------

    def get_progress(self) -> dict:
        """Get coverage progress across iterations."""
        if not self.history:
            return {"iterations": 0, "trend": "none"}

        trend = "improving"
        if len(self.history) >= 2:
            latest = self.history[-1].overall_coverage
            previous = self.history[-2].overall_coverage
            if latest <= previous:
                trend = "stagnant"

        return {
            "iterations": len(self.history),
            "current_coverage": self.history[-1].overall_coverage,
            "trend": trend,
            "history": [r.overall_coverage for r in self.history],
        }
