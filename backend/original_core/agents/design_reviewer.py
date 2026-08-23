"""
AGI Module 4 — Design Review AI.

Pre-verification RTL quality check:
  - Combinational loop detection
  - Inferred latch detection
  - Clock domain crossing (CDC) warnings
  - Coding style analysis
  - Common bug pattern detection
  - Complexity metrics

Catches problems BEFORE verification even starts,
just like a senior design engineer reviewing code.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from core.ai_client import AIClient
from core.logger import get_logger

logger = get_logger("DesignReviewer")


@dataclass
class LintIssue:
    """A single lint/review issue."""
    severity: str = "warning"     # error, warning, info, style
    category: str = ""            # latch, cdc, combo_loop, reset, style, etc.
    line: int = 0
    signal: str = ""
    description: str = ""
    suggestion: str = ""

    @property
    def formatted(self) -> str:
        return f"[{self.severity.upper()}] L{self.line} [{self.category}] {self.description}"


@dataclass
class DesignReviewReport:
    """Complete design review report."""
    module_name: str = ""
    issues: list[LintIssue] = field(default_factory=list)
    complexity_score: int = 0           # 1-10
    lines_of_code: int = 0
    always_block_count: int = 0
    fsm_states: int = 0
    port_count: int = 0
    review_time: float = 0.0

    @property
    def error_count(self) -> int:
        return sum(1 for i in self.issues if i.severity == "error")

    @property
    def warning_count(self) -> int:
        return sum(1 for i in self.issues if i.severity == "warning")

    @property
    def clean(self) -> bool:
        return self.error_count == 0

    @property
    def summary(self) -> str:
        return (
            f"Design Review [{self.module_name}]: "
            f"{self.error_count} errors, {self.warning_count} warnings, "
            f"complexity={self.complexity_score}/10, "
            f"{self.lines_of_code} LOC"
        )


class DesignReviewer:
    """Pre-verification RTL code reviewer."""

    def __init__(self, ai_client: AIClient | None = None) -> None:
        self.ai_client = ai_client

    def review(self, rtl_code: str, module_name: str = "") -> DesignReviewReport:
        """Perform comprehensive design review on RTL code.

        This runs local pattern-matching checks (fast, no LLM needed).
        """
        report = DesignReviewReport(
            module_name=module_name,
            lines_of_code=len(rtl_code.splitlines()),
        )

        # Run all checkers
        self._check_inferred_latches(rtl_code, report)
        self._check_combinational_loops(rtl_code, report)
        self._check_clock_domain_crossing(rtl_code, report)
        self._check_reset_coverage(rtl_code, report)
        self._check_x_propagation(rtl_code, report)
        self._check_coding_style(rtl_code, report)
        self._check_common_bugs(rtl_code, report)
        self._compute_complexity(rtl_code, report)

        logger.info("Design review: %s", report.summary)
        return report

    async def ai_review(self, rtl_code: str, module_name: str = "") -> DesignReviewReport:
        """Perform AI-enhanced design review (uses LLM for deeper analysis)."""
        # First do the fast local review
        report = self.review(rtl_code, module_name)

        if not self.ai_client:
            return report

        # Then use LLM for deeper analysis
        prompt = """\
You are a senior RTL design engineer reviewing Verilog code.

REVIEW this RTL for:
1. Potential bugs that would be caught in verification
2. Race conditions or timing issues
3. Missing edge cases in the logic
4. Synthesizability issues
5. Area/performance optimization opportunities

For each issue found, provide:
- SEVERITY: error | warning | info
- LINE: approximate line number
- CATEGORY: bug | timing | logic | synthesis | performance
- DESCRIPTION: what the issue is
- FIX: how to fix it

Be very critical — find everything a senior engineer would catch.
"""

        response = await self.ai_client.generate_with_context(
            system_prompt=prompt,
            context_blocks={"RTL CODE": rtl_code},
            focus_instruction=f"Review {module_name} RTL design",
            task_instruction="Find all potential issues",
            temperature=0.3,
        )

        # Parse AI issues and add to report
        ai_issues = self._parse_ai_review(response)
        report.issues.extend(ai_issues)

        return report

    # ------------------------------------------------------------------
    # Checkers
    # ------------------------------------------------------------------

    def _check_inferred_latches(self, code: str, report: DesignReviewReport) -> None:
        """Detect potentially inferred latches from incomplete case/if statements."""
        lines = code.splitlines()

        # Find always_comb or always @(*) blocks
        in_combo = False
        combo_start = 0
        brace_depth = 0

        for i, line in enumerate(lines, 1):
            stripped = line.strip()

            if re.search(r"always_comb|always\s*@\s*\(\s*\*\s*\)", stripped):
                in_combo = True
                combo_start = i
                brace_depth = 0

            if in_combo:
                brace_depth += stripped.count("begin") - stripped.count("end")

                # Check for if without else
                if re.match(r"\s*if\s*\(", stripped) and brace_depth <= 1:
                    # Look ahead for else
                    has_else = False
                    for j in range(i, min(i + 20, len(lines))):
                        if "else" in lines[j]:
                            has_else = True
                            break
                    if not has_else:
                        report.issues.append(LintIssue(
                            severity="warning",
                            category="latch",
                            line=i,
                            description="Possible inferred latch: if without else in combinational block",
                            suggestion="Add else clause or use default assignment at top of always block",
                        ))

                # Check for case without default
                if re.match(r"\s*case\s*\(", stripped):
                    has_default = False
                    for j in range(i, min(i + 50, len(lines))):
                        if "default" in lines[j]:
                            has_default = True
                            break
                        if "endcase" in lines[j]:
                            break
                    if not has_default:
                        report.issues.append(LintIssue(
                            severity="warning",
                            category="latch",
                            line=i,
                            description="Case without default in combinational block — may infer latch",
                            suggestion="Add default case",
                        ))

                if brace_depth <= 0 and "end" in stripped:
                    in_combo = False

    def _check_combinational_loops(self, code: str, report: DesignReviewReport) -> None:
        """Detect potential combinational feedback loops."""
        # Simple check: assign that references itself
        for i, line in enumerate(code.splitlines(), 1):
            assign_match = re.match(r"\s*assign\s+(\w+)\s*=\s*(.*);", line)
            if assign_match:
                target = assign_match.group(1)
                expr = assign_match.group(2)
                if re.search(rf"\b{re.escape(target)}\b", expr):
                    report.issues.append(LintIssue(
                        severity="error",
                        category="combo_loop",
                        line=i,
                        signal=target,
                        description=f"Combinational loop: '{target}' feeds back to itself",
                        suggestion="Break the feedback loop with a register",
                    ))

    def _check_clock_domain_crossing(self, code: str, report: DesignReviewReport) -> None:
        """Detect potential clock domain crossing issues."""
        # Find all clock signals
        clocks = set()
        for match in re.finditer(r"(?:posedge|negedge)\s+(\w+)", code):
            clocks.add(match.group(1))

        if len(clocks) > 1:
            report.issues.append(LintIssue(
                severity="warning",
                category="cdc",
                description=f"Multiple clock domains detected: {', '.join(sorted(clocks))}. "
                           f"Ensure proper CDC synchronization.",
                suggestion="Use 2-FF synchronizer or handshake for cross-domain signals",
            ))

    def _check_reset_coverage(self, code: str, report: DesignReviewReport) -> None:
        """Check for proper reset handling."""
        lines = code.splitlines()

        # Find all registered outputs
        reg_outputs = set()
        for match in re.finditer(r"(?:output\s+reg|output\s+logic)\s+.*?(\w+)\s*[,;)]", code):
            reg_outputs.add(match.group(1))

        # Check if they're reset
        for reg in reg_outputs:
            has_reset = False
            for line in lines:
                if re.search(rf"{re.escape(reg)}\s*<=\s*.*(?:reset|rst)", line, re.IGNORECASE):
                    has_reset = True
                    break
                if re.search(rf"if\s*\(!?\s*rst", line) and reg in line:
                    has_reset = True
                    break
            if not has_reset:
                report.issues.append(LintIssue(
                    severity="warning",
                    category="reset",
                    signal=reg,
                    description=f"Output register '{reg}' may not be reset properly",
                    suggestion="Ensure all output registers are initialized in the reset block",
                ))

    def _check_x_propagation(self, code: str, report: DesignReviewReport) -> None:
        """Check for potential X propagation risks."""
        # Find signals used before assignment in initial/reset
        for match in re.finditer(r"(\w+)\s*<=\s*(\w+)\s*;", code):
            src = match.group(2)
            if src.upper() in ("X", "Z"):
                report.issues.append(LintIssue(
                    severity="info",
                    category="x_prop",
                    signal=match.group(1),
                    description=f"Signal assigned to {src.upper()} explicitly",
                ))

    def _check_coding_style(self, code: str, report: DesignReviewReport) -> None:
        """Check coding style conventions."""
        lines = code.splitlines()

        for i, line in enumerate(lines, 1):
            # Lines too long
            if len(line) > 120:
                report.issues.append(LintIssue(
                    severity="style",
                    category="style",
                    line=i,
                    description=f"Line too long ({len(line)} chars > 120)",
                ))

            # Blocking assignment in sequential block
            if re.search(r"always_ff|always\s*@\s*\(posedge", line):
                # Check next lines for =
                for j in range(i, min(i + 20, len(lines))):
                    if re.match(r"\s*\w+\s*=\s*(?!.*<=)", lines[j]) and "begin" not in lines[j]:
                        if "=" in lines[j] and "<=" not in lines[j]:
                            report.issues.append(LintIssue(
                                severity="warning",
                                category="style",
                                line=j + 1,
                                description="Blocking assignment (=) in sequential block; use non-blocking (<=)",
                            ))
                            break

    def _check_common_bugs(self, code: str, report: DesignReviewReport) -> None:
        """Check for common RTL bugs."""
        # Sensitivity list issues (old-style always blocks)
        for match in re.finditer(r"always\s*@\s*\(([^*][^)]*)\)", code):
            sens_list = match.group(1)
            # Count signals in sensitivity list vs signals used
            sens_signals = set(re.findall(r"\w+", sens_list))
            if len(sens_signals) < 2:
                report.issues.append(LintIssue(
                    severity="warning",
                    category="sensitivity",
                    description="Incomplete sensitivity list — consider using always @(*) or always_comb",
                ))

    def _compute_complexity(self, code: str, report: DesignReviewReport) -> None:
        """Compute design complexity metrics."""
        report.always_block_count = len(re.findall(r"always", code))
        report.fsm_states = len(set(re.findall(r"(\w+_S\w+|\w+STATE\w+)", code, re.IGNORECASE)))
        report.port_count = len(re.findall(r"(?:input|output|inout)\s", code))

        # Complexity score (1-10)
        loc = report.lines_of_code
        if loc < 50:
            report.complexity_score = 1
        elif loc < 100:
            report.complexity_score = 2
        elif loc < 200:
            report.complexity_score = 3
        elif loc < 500:
            report.complexity_score = 5
        elif loc < 1000:
            report.complexity_score = 7
        else:
            report.complexity_score = 9

        if report.fsm_states > 10:
            report.complexity_score = min(10, report.complexity_score + 2)

    def _parse_ai_review(self, response: str) -> list[LintIssue]:
        """Parse AI review response into LintIssues."""
        issues = []
        for block in re.split(r"\n(?=\s*(?:SEVERITY|Issue \d|-))", response):
            severity_match = re.search(r"SEVERITY:\s*(\w+)", block, re.IGNORECASE)
            line_match = re.search(r"LINE:\s*(\d+)", block, re.IGNORECASE)
            cat_match = re.search(r"CATEGORY:\s*(\w+)", block, re.IGNORECASE)
            desc_match = re.search(r"DESCRIPTION:\s*(.+?)(?:\n|$)", block, re.IGNORECASE)
            fix_match = re.search(r"FIX:\s*(.+?)(?:\n|$)", block, re.IGNORECASE)

            if desc_match:
                issues.append(LintIssue(
                    severity=severity_match.group(1).lower() if severity_match else "info",
                    category=cat_match.group(1).lower() if cat_match else "ai_review",
                    line=int(line_match.group(1)) if line_match else 0,
                    description=desc_match.group(1).strip(),
                    suggestion=fix_match.group(1).strip() if fix_match else "",
                ))
        return issues

    def generate_report_markdown(self, report: DesignReviewReport) -> str:
        """Generate markdown review report."""
        lines = [
            f"# Design Review Report: {report.module_name}",
            "",
            "## Metrics",
            f"| Metric | Value |",
            f"|--------|-------|",
            f"| Lines of Code | {report.lines_of_code} |",
            f"| Complexity | {report.complexity_score}/10 |",
            f"| Always Blocks | {report.always_block_count} |",
            f"| FSM States | {report.fsm_states} |",
            f"| Ports | {report.port_count} |",
            f"| Errors | {report.error_count} |",
            f"| Warnings | {report.warning_count} |",
            "",
            "## Issues",
        ]

        for issue in sorted(report.issues, key=lambda i: {"error": 0, "warning": 1, "info": 2, "style": 3}.get(i.severity, 4)):
            lines.append(f"### [{issue.severity.upper()}] {issue.category} (L{issue.line})")
            lines.append(f"{issue.description}")
            if issue.suggestion:
                lines.append(f"> **Fix:** {issue.suggestion}")
            lines.append("")

        if report.clean:
            lines.append("## Verdict: CLEAN - No critical issues found")
        else:
            lines.append(f"## Verdict: {report.error_count} errors must be fixed before verification")

        return "\n".join(lines)
