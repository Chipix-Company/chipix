from __future__ import annotations

from dataclasses import asdict, dataclass, field
import os
from typing import Any


@dataclass
class SvDiagnostic:
    line: int
    col: int
    end_line: int
    end_col: int
    severity: str
    rule: str
    message: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "line": self.line,
            "col": self.col,
            "endLine": self.end_line,
            "endCol": self.end_col,
            "severity": self.severity,
            "rule": self.rule,
            "message": self.message,
        }

    @property
    def is_error(self) -> bool:
        return self.severity.lower() in {"error", "1"}


@dataclass
class SvLintResult:
    passed: bool
    tool_available: bool
    diagnostics: list[SvDiagnostic] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    skipped: bool = False
    skip_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "tool_available": self.tool_available,
            "diagnostics": [d.to_dict() for d in self.diagnostics],
            "errors": list(self.errors),
            "warnings": list(self.warnings),
            "skipped": self.skipped,
            "skip_reason": self.skip_reason,
        }

    @property
    def has_blocking_issues(self) -> bool:
        if any(d.is_error for d in self.diagnostics):
            return True
        return bool(self.errors)

    @property
    def has_agent_blocking_issues(self) -> bool:
        """Block syntax errors; optionally promote style warnings to errors."""
        if self.errors:
            return True
        if any(d.is_error for d in self.diagnostics):
            return True
        return (os.environ.get("CHIPVERIFY_SVLS_BLOCK_WARNINGS") or "").strip().lower() in {
            "1",
            "true",
            "yes",
        } and bool(self.diagnostics)
