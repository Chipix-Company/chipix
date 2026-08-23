"""
Cross-Reference Validator + Confidence Scorer.

After a file is generated:
  1. Cross-reference: every name used must exist in the symbol table
  2. Confidence score: 0-100 based on compilation + validation + grounding
  3. Pipeline gate: block if score < 50, retry if < 70

This is the quality gate that ensures enterprise-grade output.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from core.compiler import CompileResult
from core.logger import get_logger
from core.symbol_table import SymbolTable

logger = get_logger("Validator")


@dataclass
class ValidationReport:
    """Comprehensive validation report for a generated file."""
    filename: str

    # Compilation
    compilation_passed: bool = False
    compile_errors: list[str] = field(default_factory=list)

    # Symbol grounding
    grounding_score: float = 0.0       # 0.0-1.0
    known_names: list[str] = field(default_factory=list)
    unknown_names: list[str] = field(default_factory=list)

    # Structure checks
    required_keywords_present: bool = True
    missing_keywords: list[str] = field(default_factory=list)
    forbidden_patterns_found: list[str] = field(default_factory=list)

    # Line count
    line_count: int = 0
    min_lines_ok: bool = True
    max_lines_ok: bool = True

    # Overall
    confidence_score: int = 0          # 0-100
    decision: str = "accept"           # "accept", "retry", "halt"

    @property
    def passed(self) -> bool:
        return self.decision == "accept"

    @property
    def summary(self) -> str:
        return (
            f"{self.filename}: confidence={self.confidence_score}/100, "
            f"decision={self.decision}, "
            f"compiled={'Yes' if self.compilation_passed else 'No'}, "
            f"grounding={self.grounding_score:.0%}, "
            f"unknown_names={len(self.unknown_names)}"
        )


class Validator:
    """Cross-reference validator and confidence scorer."""

    def __init__(self, symbol_table: SymbolTable) -> None:
        self.symbol_table = symbol_table

    def validate(
        self,
        filename: str,
        content: str,
        compile_result: CompileResult | None = None,
        required_keywords: list[str] | None = None,
        forbidden_patterns: list[str] | None = None,
        min_lines: int = 5,
        max_lines: int = 2000,
    ) -> ValidationReport:
        """Run full validation on a generated file.

        1. Compilation check
        2. Symbol grounding check
        3. Keyword/pattern checks
        4. Line count check
        5. Compute confidence score
        6. Decide: accept, retry, or halt
        """
        report = ValidationReport(filename=filename)
        report.line_count = len(content.splitlines())

        # 1. Compilation
        if compile_result is not None:
            report.compilation_passed = compile_result.success
            report.compile_errors = [e.message for e in compile_result.errors]
        else:
            report.compilation_passed = True  # Assume pass if no result

        # 2. Symbol grounding (only for SV files)
        if filename.endswith((".sv", ".v", ".svh")):
            grounding = self.symbol_table.validate_names_in_code(content)
            report.grounding_score = grounding["grounding_score"]
            report.known_names = grounding["known_names"]
            report.unknown_names = grounding["unknown_names"]
        else:
            report.grounding_score = 1.0

        # 3. Required keywords
        if required_keywords:
            content_lower = content.lower()
            for kw in required_keywords:
                if kw.lower() not in content_lower:
                    report.required_keywords_present = False
                    report.missing_keywords.append(kw)

        # 4. Forbidden patterns
        if forbidden_patterns:
            content_lower = content.lower()
            for pat in forbidden_patterns:
                if pat.lower() in content_lower:
                    report.forbidden_patterns_found.append(pat)

        # 5. Line count
        report.min_lines_ok = report.line_count >= min_lines
        report.max_lines_ok = report.line_count <= max_lines

        # 6. Compute confidence score
        report.confidence_score = self._compute_confidence(report)

        # 7. Decision
        if report.confidence_score >= 70:
            report.decision = "accept"
        elif report.confidence_score >= 50:
            report.decision = "retry"
        else:
            report.decision = "halt"

        logger.info(
            "Validated %s: %s (confidence=%d, grounding=%.0f%%, compiled=%s)",
            filename, report.decision, report.confidence_score,
            report.grounding_score * 100, report.compilation_passed,
        )

        return report

    def _compute_confidence(self, report: ValidationReport) -> int:
        """Compute a 0-100 confidence score.

        Weighted formula:
          40% — Compilation (pass = 40, fail = 0)
          25% — Symbol grounding (0.0-1.0 → 0-25)
          15% — Required keywords present (all = 15, missing = 0)
          10% — No forbidden patterns (clean = 10, found = 0)
          10% — Line count in range (ok = 10, out = 0)
        """
        score = 0

        # Compilation (40%)
        if report.compilation_passed:
            score += 40

        # Symbol grounding (25%)
        score += int(report.grounding_score * 25)

        # Required keywords (15%)
        if report.required_keywords_present:
            score += 15
        elif report.missing_keywords:
            # Partial credit
            total_required = len(report.missing_keywords) + len([
                k for k in (report.known_names or [])
            ])
            if total_required > 0:
                present_ratio = 1.0 - (len(report.missing_keywords) / max(total_required, 1))
                score += int(present_ratio * 15)

        # Forbidden patterns (10%)
        if not report.forbidden_patterns_found:
            score += 10

        # Line count (10%)
        if report.min_lines_ok and report.max_lines_ok:
            score += 10
        elif report.min_lines_ok or report.max_lines_ok:
            score += 5

        return min(100, max(0, score))
