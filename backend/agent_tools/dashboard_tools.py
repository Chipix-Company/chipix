"""
Dashboard Tools — Automated verification dashboard.

This is an AUTOMATION tool — it reads output files directly from the
verification output directory (logs, coverage reports, formal results)
and aggregates them into dashboard data. NO LLM involved.

File reading pattern:
  output_dir/
    ├── simulation.log     → parsed by result_parser
    ├── coverage.json      → parsed by coverage_parser
    ├── formal_results.json → formal properties status
    ├── uvm_results.json   → UVM generation status
    ├── test_plan.json     → original plan scenarios
    └── debug_results.json → failure diagnosis
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Optional

from agent_tools import register_tool

logger = logging.getLogger(__name__)


async def handle_get_verification_dashboard(
    project_id: str,
    module_name: str = "",
    output_dir: str = "",
    **kwargs: Any,
) -> str:
    """
    Automated dashboard: reads all verification output files from disk
    and aggregates them into a unified dashboard payload.

    No LLM involved — pure file reading + parsing.
    """
    from services.verification.dashboard_aggregator import aggregate_dashboard

    out_path = Path(output_dir) if output_dir else None

    # ── Auto-read files from output directory ────────────────────
    plan = _read_json_file(out_path, "test_plan.json")
    unitsim = _read_unitsim_results(out_path)
    formal = _read_json_file(out_path, "formal_results.json")
    uvm = _read_json_file(out_path, "uvm_results.json")
    coverage = _read_coverage_results(out_path)
    debug = _read_json_file(out_path, "debug_results.json")

    # ── Aggregate ────────────────────────────────────────────────
    dashboard = aggregate_dashboard(
        module_name=module_name,
        plan=plan,
        unitsim_results=unitsim,
        formal_results=formal,
        uvm_results=uvm,
        coverage_results=coverage,
        debug_results=debug,
    )

    return json.dumps(dashboard.to_dict(), indent=2)


def _read_json_file(out_path: Optional[Path], filename: str) -> Optional[dict]:
    """Read a JSON file from the output directory."""
    if not out_path:
        return None
    filepath = out_path / filename
    if not filepath.exists():
        return None
    try:
        return json.loads(filepath.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("Failed to read %s: %s", filepath, e)
        return None


def _read_unitsim_results(out_path: Optional[Path]) -> Optional[dict]:
    """Read UnitSim results — try JSON first, then parse raw log."""
    if not out_path:
        return None

    # Try structured JSON
    json_result = _read_json_file(out_path, "unitsim_results.json")
    if json_result:
        return json_result

    # Fall back to parsing raw simulation log
    log_file = out_path / "simulation.log"
    if not log_file.exists():
        return None

    try:
        from services.verification.result_parser import parse_simulation_log
        log_text = log_file.read_text(encoding="utf-8", errors="replace")
        parsed = parse_simulation_log(log_text)
        return {
            "pass_count": parsed.pass_count,
            "fail_count": parsed.fail_count,
            "test_results": {t.name: t.status for t in parsed.tests},
            "output": log_text[:5000],
            "failures": [t.message for t in parsed.tests if t.status == "fail"],
        }
    except Exception as e:
        logger.warning("Failed to parse simulation.log: %s", e)
        return None


def _read_coverage_results(out_path: Optional[Path]) -> Optional[dict]:
    """Read coverage results — try JSON first, then parse coverage files."""
    if not out_path:
        return None

    # Try structured JSON
    json_result = _read_json_file(out_path, "coverage.json")
    if json_result:
        return json_result

    # Fall back to parsing coverage log
    cov_log = out_path / "coverage.log"
    if cov_log.exists():
        try:
            from services.verification.coverage_parser import parse_coverage_from_log
            log_text = cov_log.read_text(encoding="utf-8", errors="replace")
            report = parse_coverage_from_log(log_text)
            return {
                "line_coverage": report.line_coverage,
                "branch_coverage": report.branch_coverage,
                "toggle_coverage": report.toggle_coverage,
                "functional_coverage": getattr(report, "functional_coverage", 0.0),
                "grade": report.grade,
            }
        except Exception as e:
            logger.warning("Failed to parse coverage.log: %s", e)

    return None


# Register tool
register_tool("getVerificationDashboard", handle_get_verification_dashboard)
