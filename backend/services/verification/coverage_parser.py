"""
Coverage Parser — Parse coverage data from Icarus Verilog and Verilator.

Extracts line, branch, toggle, and functional coverage metrics from
simulation output files and produces structured CoverageReport objects.
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# ─── Data Types ──────────────────────────────────────────────────────

@dataclass
class UncoveredItem:
    """A single uncovered line, branch, or signal."""
    file: str
    line: int = 0
    item_type: str = ""  # "line" | "branch" | "toggle"
    signal_name: str = ""
    detail: str = ""


@dataclass
class CoverageReport:
    """Structured coverage analysis result."""
    line_coverage: float = 0.0       # percentage 0-100
    branch_coverage: float = 0.0     # percentage 0-100
    toggle_coverage: float = 0.0     # percentage 0-100
    functional_coverage: float = 0.0 # percentage 0-100
    uncovered_lines: List[UncoveredItem] = field(default_factory=list)
    uncovered_branches: List[UncoveredItem] = field(default_factory=list)
    uncovered_toggles: List[UncoveredItem] = field(default_factory=list)
    raw_data: Dict[str, Any] = field(default_factory=dict)
    grade: str = "N/A"
    source: str = ""  # "icarus" | "verilator" | "vcs" | "manual"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "line_coverage": round(self.line_coverage, 2),
            "branch_coverage": round(self.branch_coverage, 2),
            "toggle_coverage": round(self.toggle_coverage, 2),
            "functional_coverage": round(self.functional_coverage, 2),
            "grade": self.grade,
            "source": self.source,
            "uncovered_count": {
                "lines": len(self.uncovered_lines),
                "branches": len(self.uncovered_branches),
                "toggles": len(self.uncovered_toggles),
            },
            "uncovered_lines": [
                {"file": u.file, "line": u.line, "detail": u.detail}
                for u in self.uncovered_lines[:50]
            ],
            "uncovered_branches": [
                {"file": u.file, "line": u.line, "detail": u.detail}
                for u in self.uncovered_branches[:50]
            ],
        }


def compute_coverage_grade(report: CoverageReport) -> str:
    """Compute overall coverage grade from metrics."""
    metrics = [
        v for v in [
            report.line_coverage,
            report.branch_coverage,
            report.toggle_coverage,
            report.functional_coverage,
        ] if v > 0
    ]
    if not metrics:
        return "N/A"
    avg = sum(metrics) / len(metrics)
    if avg >= 95:
        return "A"
    elif avg >= 85:
        return "B"
    elif avg >= 70:
        return "C"
    elif avg >= 50:
        return "D"
    return "F"


# ─── Verilator Coverage Parser ──────────────────────────────────────

def parse_verilator_coverage(coverage_dat_path: str) -> CoverageReport:
    """
    Parse Verilator --coverage output file (.dat format).

    Verilator coverage data format:
        C '<filename>' <lineno> <column> <count> <type> <hier> <comment>

    Types:
        - 'l' = line coverage
        - 'b' = branch coverage  
        - 't' = toggle coverage
        - 'f' = functional coverage (coverpoints)
    """
    report = CoverageReport(source="verilator")

    dat_path = Path(coverage_dat_path)
    if not dat_path.exists():
        logger.warning(f"Coverage file not found: {coverage_dat_path}")
        return report

    try:
        with open(dat_path, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()
    except OSError as e:
        logger.warning(f"Cannot read coverage file: {e}")
        return report

    # Parse coverage entries
    line_total, line_hit = 0, 0
    branch_total, branch_hit = 0, 0
    toggle_total, toggle_hit = 0, 0
    func_total, func_hit = 0, 0

    # Verilator .dat format: each line is a coverage point
    # Format: C '<file>' <line> <col> <count> <type> <hier> <comment>
    re_entry = re.compile(
        r"C\s+'([^']+)'\s+(\d+)\s+(\d+)\s+(\d+)\s+(\w+)\s+(\S+)\s*(.*)"
    )

    # Also handle the info/annotation format:
    # "C" <file> <line> <count> "t" <toggle_type> <signal>
    re_simple = re.compile(
        r"C\s+(\S+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\w)"
    )

    for line in content.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("//"):
            continue

        m = re_entry.match(line)
        if not m:
            m = re_simple.match(line)
            if not m:
                continue

        if len(m.groups()) >= 5:
            filename = m.group(1)
            lineno = int(m.group(2))
            count = int(m.group(4) if len(m.groups()) >= 5 else m.group(3))
            cov_type = m.group(5).lower() if len(m.groups()) >= 5 else "l"

            if cov_type in ("l", "line"):
                line_total += 1
                if count > 0:
                    line_hit += 1
                else:
                    report.uncovered_lines.append(UncoveredItem(
                        file=filename, line=lineno, item_type="line",
                    ))

            elif cov_type in ("b", "branch"):
                branch_total += 1
                if count > 0:
                    branch_hit += 1
                else:
                    report.uncovered_branches.append(UncoveredItem(
                        file=filename, line=lineno, item_type="branch",
                    ))

            elif cov_type in ("t", "toggle"):
                toggle_total += 1
                if count > 0:
                    toggle_hit += 1
                else:
                    signal = m.group(7).strip() if len(m.groups()) >= 7 else ""
                    report.uncovered_toggles.append(UncoveredItem(
                        file=filename, line=lineno, item_type="toggle",
                        signal_name=signal,
                    ))

            elif cov_type in ("f", "func", "point"):
                func_total += 1
                if count > 0:
                    func_hit += 1

    # Compute percentages
    report.line_coverage = (line_hit / line_total * 100) if line_total > 0 else 0
    report.branch_coverage = (branch_hit / branch_total * 100) if branch_total > 0 else 0
    report.toggle_coverage = (toggle_hit / toggle_total * 100) if toggle_total > 0 else 0
    report.functional_coverage = (func_hit / func_total * 100) if func_total > 0 else 0

    report.raw_data = {
        "line": {"hit": line_hit, "total": line_total},
        "branch": {"hit": branch_hit, "total": branch_total},
        "toggle": {"hit": toggle_hit, "total": toggle_total},
        "functional": {"hit": func_hit, "total": func_total},
    }

    report.grade = compute_coverage_grade(report)

    logger.info(
        f"Verilator coverage parsed: L={report.line_coverage:.1f}% "
        f"B={report.branch_coverage:.1f}% T={report.toggle_coverage:.1f}% "
        f"Grade={report.grade}"
    )
    return report


# ─── Icarus VCD Toggle Coverage ─────────────────────────────────────

def parse_icarus_coverage(
    vcd_path: str,
    rtl_path: str = "",
) -> CoverageReport:
    """
    Analyze an Icarus VCD file to compute toggle coverage.

    Toggle coverage = percentage of signals that changed value
    at least once during simulation. This is derived from VCD
    value-change dump analysis.

    Also attempts to extract line coverage from Icarus compilation
    output if available (requires -g coverage-line flag).
    """
    report = CoverageReport(source="icarus")

    vcd_file = Path(vcd_path)
    if not vcd_file.exists():
        logger.warning(f"VCD file not found: {vcd_path}")
        return report

    try:
        signals_total, signals_toggled = _analyze_vcd_toggles(str(vcd_file))
        if signals_total > 0:
            report.toggle_coverage = (signals_toggled / signals_total) * 100

        report.raw_data = {
            "toggle": {"hit": signals_toggled, "total": signals_total},
            "source_vcd": str(vcd_file),
        }

    except Exception as e:
        logger.warning(f"VCD toggle analysis failed: {e}")

    # Try to parse Icarus line coverage report if available
    cov_report = vcd_file.parent / "coverage.txt"
    if cov_report.exists():
        _parse_icarus_line_coverage(cov_report, report)

    report.grade = compute_coverage_grade(report)

    logger.info(
        f"Icarus coverage parsed: T={report.toggle_coverage:.1f}% "
        f"L={report.line_coverage:.1f}% Grade={report.grade}"
    )
    return report


def _analyze_vcd_toggles(vcd_path: str) -> tuple[int, int]:
    """
    Analyze VCD file for signal toggle coverage.

    Returns (total_signals, toggled_signals).
    A signal is "toggled" if it has at least one value-change record.
    """
    signals: Dict[str, bool] = {}  # signal_id -> has_toggled
    in_defs = False

    # Regex for VCD variable definitions
    re_var = re.compile(r"\$var\s+\w+\s+\d+\s+(\S+)\s+(\S+)")
    # Value change: single bit "0x" or "1x" or "#timestamp"
    re_change = re.compile(r"^[01xzXZ](\S+)")
    re_vector_change = re.compile(r"^[bBrR]\S+\s+(\S+)")

    try:
        with open(vcd_path, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue

                # Variable definition section
                if line.startswith("$var"):
                    m = re_var.match(line)
                    if m:
                        sig_id = m.group(1)
                        signals[sig_id] = False
                    continue

                if line.startswith("$enddefinitions"):
                    in_defs = True
                    continue

                if not in_defs:
                    continue

                # Skip timestamps
                if line.startswith("#"):
                    continue

                # Value change records
                m = re_change.match(line)
                if m:
                    sig_id = m.group(1)
                    if sig_id in signals:
                        signals[sig_id] = True
                    continue

                m = re_vector_change.match(line)
                if m:
                    sig_id = m.group(1)
                    if sig_id in signals:
                        signals[sig_id] = True

    except OSError as e:
        logger.warning(f"Failed to read VCD: {e}")

    total = len(signals)
    toggled = sum(1 for v in signals.values() if v)
    return total, toggled


def _parse_icarus_line_coverage(
    cov_report_path: Path,
    report: CoverageReport,
) -> None:
    """Parse Icarus line coverage report if available."""
    try:
        content = cov_report_path.read_text(encoding="utf-8", errors="ignore")

        # Look for summary line
        m = re.search(r"(\d+)\s+of\s+(\d+)\s+lines?\s+covered", content, re.I)
        if m:
            hit = int(m.group(1))
            total = int(m.group(2))
            report.line_coverage = (hit / total * 100) if total > 0 else 0
            report.raw_data["line"] = {"hit": hit, "total": total}

        # Extract uncovered lines
        for m in re.finditer(
            r"(\S+\.(?:sv|v)):(\d+)\s+.*?NOT\s+COVERED", content, re.I
        ):
            report.uncovered_lines.append(UncoveredItem(
                file=m.group(1), line=int(m.group(2)), item_type="line",
            ))

    except Exception as e:
        logger.warning(f"Failed to parse line coverage: {e}")


# ─── Generic Log Parser ─────────────────────────────────────────────

def parse_coverage_from_log(log_text: str) -> CoverageReport:
    """
    Extract coverage metrics from free-form simulation log text.

    Handles patterns like:
      "Line coverage: 87.5%"
      "Branch coverage: 72.3%"
      "Toggle coverage: 65.1%"
      "85 of 100 lines covered"
    """
    report = CoverageReport(source="log_parse")

    # Percentage patterns
    for metric, pattern in [
        ("line_coverage", r"line[_\s]*coverage[:\s]*([\d.]+)\s*%?"),
        ("branch_coverage", r"branch[_\s]*coverage[:\s]*([\d.]+)\s*%?"),
        ("toggle_coverage", r"toggle[_\s]*coverage[:\s]*([\d.]+)\s*%?"),
        ("functional_coverage", r"functional[_\s]*coverage[:\s]*([\d.]+)\s*%?"),
    ]:
        m = re.search(pattern, log_text, re.I)
        if m:
            setattr(report, metric, float(m.group(1)))

    # "X of Y lines covered" pattern
    m = re.search(r"(\d+)\s+of\s+(\d+)\s+lines?\s+covered", log_text, re.I)
    if m and report.line_coverage == 0:
        hit, total = int(m.group(1)), int(m.group(2))
        if total > 0:
            report.line_coverage = hit / total * 100

    report.grade = compute_coverage_grade(report)
    return report


# ─── Auto-Detect and Parse ──────────────────────────────────────────

def auto_parse_coverage(
    work_dir: str,
    vcd_path: str = "",
    rtl_path: str = "",
) -> CoverageReport:
    """
    Auto-detect coverage data format and parse.

    Checks for:
    1. Verilator coverage.dat
    2. Icarus VCD file
    3. Coverage log files
    """
    work = Path(work_dir)

    # Check for Verilator coverage
    for dat_name in ["coverage.dat", "default.dat"]:
        dat = work / dat_name
        if dat.exists():
            return parse_verilator_coverage(str(dat))

    # Check for Verilator coverage in logs/ subdir
    logs_dir = work / "logs"
    if logs_dir.exists():
        for dat in logs_dir.glob("*.dat"):
            return parse_verilator_coverage(str(dat))

    # Check for VCD file
    if vcd_path and Path(vcd_path).exists():
        return parse_icarus_coverage(vcd_path, rtl_path)

    # Try any VCD in work dir
    for vcd in work.glob("*.vcd"):
        return parse_icarus_coverage(str(vcd), rtl_path)

    # Try parsing log files
    for log_name in ["sim.log", "simulation.log", "run.log"]:
        log_file = work / log_name
        if log_file.exists():
            content = log_file.read_text(encoding="utf-8", errors="ignore")
            report = parse_coverage_from_log(content)
            if report.line_coverage > 0 or report.toggle_coverage > 0:
                return report

    logger.info(f"No coverage data found in {work_dir}")
    return CoverageReport(source="none")
