"""
Phase E — Fixer & Reporter Agent (Agent 6).

Auto-debugging and report generation:
  1. Analyze simulation failures
  2. Use LLM to diagnose root cause
  3. Generate targeted fixes (diff-based patching)
  4. Re-run simulation to verify fixes
  5. Generate comprehensive final report
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from pathlib import Path

import config
from core.ai_client import AIClient
from core.logger import get_logger
from agents.test_runner import SimulationResult, RegressionResult

logger = get_logger("Fixer")


@dataclass
class FixAttempt:
    """Record of a single fix attempt."""
    filename: str
    original_code: str
    fixed_code: str
    error_message: str
    diagnosis: str
    fix_description: str
    success: bool = False
    attempt_number: int = 1


@dataclass
class VerificationReport:
    """Comprehensive final verification report."""
    module_name: str = ""
    total_files: int = 0
    total_lines: int = 0
    generation_time: float = 0.0

    # Test results
    tests_run: int = 0
    tests_passed: int = 0
    tests_failed: int = 0

    # Coverage
    line_coverage: float = 0.0
    branch_coverage: float = 0.0
    functional_coverage: float = 0.0

    # Fix attempts
    fix_attempts: list[FixAttempt] = field(default_factory=list)
    auto_fixed: int = 0

    # Confidence
    overall_confidence: int = 0

    # Sign-off
    sign_off_ready: bool = False
    sign_off_blockers: list[str] = field(default_factory=list)


class FixerAgent:
    """Agent 6: Auto-debug failures and generate fixes."""

    def __init__(self, ai_client: AIClient, output_dir: str | Path = "output") -> None:
        self.ai_client = ai_client
        self.output_dir = Path(output_dir)
        self.verification_dir = self.output_dir / "verification"
        self.fix_history: list[FixAttempt] = []

    # ------------------------------------------------------------------
    # Diagnose failure
    # ------------------------------------------------------------------

    async def diagnose(self, sim_result: SimulationResult) -> str:
        """Use LLM to diagnose a simulation failure.

        Args:
            sim_result: The failed simulation result

        Returns:
            Diagnosis string with root cause analysis
        """
        if sim_result.passed:
            return "No failure to diagnose — simulation passed."

        # Collect relevant context
        error_block = "\n".join(sim_result.error_messages[:15])

        # Read relevant source files
        source_context = self._collect_source_files()

        prompt = """\
You are an expert SystemVerilog verification debugger.

TASK: Diagnose the root cause of this simulation failure.

SIMULATION OUTPUT (errors):
{errors}

RULES:
1. Identify the EXACT line(s) causing the error
2. Explain WHY the error occurs
3. Suggest a SPECIFIC fix
4. Do NOT hallucinate — if unsure, say so

OUTPUT FORMAT:
ROOT CAUSE: <one-line summary>
FILE: <filename>
LINE: <line number or range>
EXPLANATION: <detailed explanation>
FIX: <specific code change>
"""

        context = {
            "SIMULATION ERRORS": error_block,
            "SIMULATION LOG (last 50 lines)": "\n".join(sim_result.stdout.splitlines()[-50:]),
            "SOURCE FILES": source_context,
        }

        response = await self.ai_client.generate_with_context(
            system_prompt=prompt.format(errors=error_block),
            context_blocks=context,
            focus_instruction="Diagnose the simulation failure",
            task_instruction="Find the root cause and suggest a fix",
        )

        return response

    # ------------------------------------------------------------------
    # Auto-fix
    # ------------------------------------------------------------------

    async def auto_fix(
        self,
        sim_result: SimulationResult,
        max_attempts: int = 3,
    ) -> list[FixAttempt]:
        """Attempt to automatically fix simulation failures.

        Args:
            sim_result: Failed simulation result
            max_attempts: Maximum fix attempts

        Returns:
            List of fix attempts
        """
        if sim_result.passed:
            return []

        attempts = []

        for attempt in range(1, max_attempts + 1):
            logger.info("Fix attempt %d/%d...", attempt, max_attempts)

            # Diagnose
            diagnosis = await self.diagnose(sim_result)

            # Extract the file and fix from diagnosis
            fix_info = self._parse_diagnosis(diagnosis)

            if not fix_info:
                logger.warning("Could not parse fix from diagnosis")
                continue

            filename = fix_info.get("file", "")
            original_code = fix_info.get("original", "")
            fixed_code = fix_info.get("fix", "")

            if not filename or not fixed_code:
                continue

            # Apply fix
            file_path = self.verification_dir / filename
            if not file_path.exists():
                logger.warning("File not found: %s", file_path)
                continue

            current_content = file_path.read_text(encoding="utf-8")

            # Generate the fixed file using LLM
            fix_prompt = f"""\
Fix the following error in {filename}:

ERROR: {sim_result.error_messages[0] if sim_result.error_messages else 'Unknown'}

DIAGNOSIS: {diagnosis}

CURRENT FILE CONTENT:
{current_content}

Generate the COMPLETE fixed file. Keep ALL working code intact.
Only change what is necessary to fix the error.
"""

            fixed_content = await self.ai_client.generate_with_context(
                system_prompt="You are a SystemVerilog debugger. Fix the code.",
                context_blocks={"CURRENT CODE": current_content},
                focus_instruction=f"Fix {filename}",
                task_instruction=fix_prompt,
                temperature=0.3,
            )

            # Extract code from response
            sv_match = re.search(r"```(?:systemverilog|verilog|sv)\s*\n(.*?)```", fixed_content, re.DOTALL)
            if sv_match:
                fixed_content = sv_match.group(1).strip()

            fix_attempt = FixAttempt(
                filename=filename,
                original_code=current_content,
                fixed_code=fixed_content,
                error_message=sim_result.error_messages[0] if sim_result.error_messages else "",
                diagnosis=diagnosis[:500],
                fix_description=fix_info.get("explanation", ""),
                attempt_number=attempt,
            )

            # Save the fix
            file_path.write_text(fixed_content, encoding="utf-8")
            logger.info("Applied fix to %s", filename)

            attempts.append(fix_attempt)
            self.fix_history.append(fix_attempt)

            # Note: caller should re-compile and re-run to check if fix worked
            break

        return attempts

    def _parse_diagnosis(self, diagnosis: str) -> dict:
        """Parse the diagnosis to extract file, line, and fix info."""
        result = {}

        file_match = re.search(r"FILE:\s*(\S+)", diagnosis, re.IGNORECASE)
        if file_match:
            result["file"] = file_match.group(1)

        line_match = re.search(r"LINE:\s*(\d+)", diagnosis, re.IGNORECASE)
        if line_match:
            result["line"] = int(line_match.group(1))

        explanation_match = re.search(r"EXPLANATION:\s*(.*?)(?:\n(?:FIX|ROOT)|$)", diagnosis, re.DOTALL | re.IGNORECASE)
        if explanation_match:
            result["explanation"] = explanation_match.group(1).strip()

        fix_match = re.search(r"FIX:\s*(.*?)$", diagnosis, re.DOTALL | re.IGNORECASE)
        if fix_match:
            result["fix"] = fix_match.group(1).strip()

        return result

    def _collect_source_files(self) -> str:
        """Collect all source files as context."""
        context_parts = []
        if self.verification_dir.exists():
            for sv_file in sorted(self.verification_dir.glob("*.sv"))[:10]:  # Limit to 10 files
                try:
                    content = sv_file.read_text(encoding="utf-8")
                    context_parts.append(f"--- {sv_file.name} ---\n{content}")
                except Exception:
                    pass
        return "\n\n".join(context_parts)

    # ------------------------------------------------------------------
    # Report generation
    # ------------------------------------------------------------------

    def generate_report(
        self,
        module_name: str,
        generated_files: dict,
        regression_result: RegressionResult | None = None,
        coverage_data: dict | None = None,
        generation_time: float = 0.0,
    ) -> VerificationReport:
        """Generate a comprehensive verification report."""
        report = VerificationReport()
        report.module_name = module_name
        report.total_files = len(generated_files)
        report.total_lines = sum(
            len(v.get("content", "").splitlines()) if isinstance(v, dict) else 0
            for v in generated_files.values()
        )
        report.generation_time = generation_time

        # Test results
        if regression_result:
            report.tests_run = regression_result.total_tests
            report.tests_passed = regression_result.passed_tests
            report.tests_failed = regression_result.failed_tests

        # Coverage
        if coverage_data:
            report.line_coverage = coverage_data.get("line", 0)
            report.branch_coverage = coverage_data.get("branch", 0)
            report.functional_coverage = coverage_data.get("functional", 0)

        # Fix history
        report.fix_attempts = self.fix_history
        report.auto_fixed = sum(1 for f in self.fix_history if f.success)

        # Sign-off assessment
        report.sign_off_ready = True
        if regression_result and regression_result.failed_tests > 0:
            report.sign_off_ready = False
            report.sign_off_blockers.append(f"{regression_result.failed_tests} tests failing")

        if coverage_data and coverage_data.get("line", 0) < 80:
            report.sign_off_ready = False
            report.sign_off_blockers.append(f"Line coverage {coverage_data.get('line', 0):.1f}% < 80%")

        return report

    def write_report_markdown(self, report: VerificationReport) -> str:
        """Generate markdown report and save to disk."""
        md = [
            f"# Verification Report: {report.module_name}",
            "",
            "## Summary",
            f"| Metric | Value |",
            f"|--------|-------|",
            f"| Files Generated | {report.total_files} |",
            f"| Total Lines | {report.total_lines} |",
            f"| Generation Time | {report.generation_time:.1f}s |",
            f"| Tests Run | {report.tests_run} |",
            f"| Tests Passed | {report.tests_passed} |",
            f"| Tests Failed | {report.tests_failed} |",
            f"| Line Coverage | {report.line_coverage:.1f}% |",
            f"| Branch Coverage | {report.branch_coverage:.1f}% |",
            f"| Functional Coverage | {report.functional_coverage:.1f}% |",
            f"| Auto-Fixed Issues | {report.auto_fixed} |",
            "",
        ]

        if report.fix_attempts:
            md.append("## Fix History")
            for fix in report.fix_attempts:
                status = "Fixed" if fix.success else "Attempted"
                md.append(f"- [{status}] {fix.filename}: {fix.fix_description[:80]}")
            md.append("")

        if report.sign_off_ready:
            md.append("## Sign-Off: READY")
        else:
            md.append("## Sign-Off: NOT READY")
            md.append("### Blockers:")
            for blocker in report.sign_off_blockers:
                md.append(f"- {blocker}")

        content = "\n".join(md)

        # Save
        report_path = self.output_dir / "reports" / "verification_report.md"
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(content, encoding="utf-8")

        return content
