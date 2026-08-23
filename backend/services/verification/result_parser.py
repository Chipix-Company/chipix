"""
Simulation Result Parser — Parse simulator logs into structured results.

Parses output from iverilog/vvp, Verilator, and commercial simulators
into a unified result format with:
  - Test pass/fail counts
  - Assertion failure details
  - Error classification
  - Coverage extraction (if available)
"""

from __future__ import annotations

import re
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List

logger = logging.getLogger(__name__)


@dataclass
class TestResult:
    """A single test result."""
    name: str
    status: str = "unknown"  # "pass", "fail", "error", "timeout"
    message: str = ""
    line_number: int = 0


@dataclass
class AssertionFailure:
    """A single assertion failure."""
    file: str = ""
    line: int = 0
    signal: str = ""
    expected: str = ""
    actual: str = ""
    message: str = ""
    timestamp: str = ""


@dataclass
class ParsedSimResult:
    """Fully parsed simulation result."""
    overall_status: str = "unknown"  # "pass", "fail", "error", "timeout"
    tests: List[TestResult] = field(default_factory=list)
    assertion_failures: List[AssertionFailure] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    total_tests: int = 0
    passed: int = 0
    failed: int = 0
    compile_errors: int = 0
    sim_time: str = ""
    raw_log: str = ""

    @property
    def summary(self) -> str:
        return (
            f"{self.overall_status.upper()}: "
            f"{self.passed}/{self.total_tests} passed, "
            f"{self.failed} failed, "
            f"{len(self.assertion_failures)} assertion failures"
        )


# Patterns for parsing simulation output
PASS_PATTERNS = [
    re.compile(r"\[PASS\]\s*(.+)", re.IGNORECASE),
    re.compile(r"PASSED:\s*(.+)", re.IGNORECASE),
    re.compile(r"TEST\s+(\S+)\s+PASSED", re.IGNORECASE),
]

FAIL_PATTERNS = [
    re.compile(r"\[FAIL\]\s*(.+)", re.IGNORECASE),
    re.compile(r"FAILED:\s*(.+)", re.IGNORECASE),
    re.compile(r"TEST\s+(\S+)\s+FAILED", re.IGNORECASE),
    re.compile(r"\$error\s*\(\s*\"(.+?)\"\s*\)", re.IGNORECASE),
]

ASSERT_PATTERNS = [
    re.compile(r"Assertion\s+failed.*?at\s+(\S+):(\d+)", re.IGNORECASE),
    re.compile(r"assert.*?failed", re.IGNORECASE),
    re.compile(r"\$fatal.*?(\S+)", re.IGNORECASE),
]

ERROR_PATTERNS = [
    re.compile(r"^ERROR:?\s*(.+)", re.IGNORECASE | re.MULTILINE),
    re.compile(r"^\*E,\s*(.+)", re.MULTILINE),  # VCS format
    re.compile(r"%Error:\s*(.+)", re.IGNORECASE),  # Verilator format
]

WARNING_PATTERNS = [
    re.compile(r"^WARNING:?\s*(.+)", re.IGNORECASE | re.MULTILINE),
    re.compile(r"%Warning:\s*(.+)", re.IGNORECASE),
]

RESULTS_PATTERN = re.compile(
    r"RESULTS:\s*(\d+)\s*/\s*(\d+)", re.IGNORECASE
)

TIMEOUT_PATTERNS = [
    re.compile(r"TIMEOUT", re.IGNORECASE),
    re.compile(r"timed?\s*out", re.IGNORECASE),
]


def parse_simulation_log(log: str) -> ParsedSimResult:
    """
    Parse a simulation log into structured results.

    Works with:
      - iverilog/vvp output
      - Verilator output
      - VCS output format
      - Our generated testbench [PASS]/[FAIL] markers
    """
    result = ParsedSimResult(raw_log=log)

    for line in log.splitlines():
        line_s = line.strip()
        if not line_s:
            continue

        # Check for PASS
        for pat in PASS_PATTERNS:
            m = pat.search(line_s)
            if m:
                test_name = m.group(1).strip()
                result.tests.append(TestResult(
                    name=test_name, status="pass", message=line_s,
                ))
                result.passed += 1
                break

        # Check for FAIL
        for pat in FAIL_PATTERNS:
            m = pat.search(line_s)
            if m:
                test_name = m.group(1).strip()
                result.tests.append(TestResult(
                    name=test_name, status="fail", message=line_s,
                ))
                result.failed += 1
                break

        # Check for assertion failures
        for pat in ASSERT_PATTERNS:
            m = pat.search(line_s)
            if m:
                af = AssertionFailure(message=line_s)
                if m.lastindex and m.lastindex >= 2:
                    af.file = m.group(1)
                    af.line = int(m.group(2))
                result.assertion_failures.append(af)
                break

        # Check for errors
        for pat in ERROR_PATTERNS:
            m = pat.search(line_s)
            if m:
                result.errors.append(m.group(1).strip())
                break

        # Check for warnings
        for pat in WARNING_PATTERNS:
            m = pat.search(line_s)
            if m:
                result.warnings.append(m.group(1).strip())
                break

        # Check for timeout
        for pat in TIMEOUT_PATTERNS:
            if pat.search(line_s):
                result.overall_status = "timeout"

    # Try to extract summary line (our format: "RESULTS: X / Y passed")
    m = RESULTS_PATTERN.search(log)
    if m:
        result.passed = int(m.group(1))
        result.total_tests = int(m.group(2))
        result.failed = result.total_tests - result.passed
    else:
        result.total_tests = result.passed + result.failed

    # Determine overall status
    if result.overall_status != "timeout":
        if result.errors or result.compile_errors > 0:
            result.overall_status = "error"
        elif result.failed > 0 or result.assertion_failures:
            result.overall_status = "fail"
        elif result.passed > 0:
            result.overall_status = "pass"
        else:
            result.overall_status = "unknown"

    logger.info(f"Parsed sim result: {result.summary}")
    return result
