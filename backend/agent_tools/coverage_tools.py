"""
Coverage Tools — Coverage analysis, gap detection, test suggestions.

Uses NEW coverage_parser.py to extract real coverage metrics from
simulation output files (Verilator .dat, Icarus VCD).
"""

from __future__ import annotations

import json
import logging
from typing import Any

from agent_tools import register_tool

logger = logging.getLogger(__name__)


async def handle_analyze_coverage(
    project_id: str,
    coverage_data: str = "",
    db_session: Any = None,
    ai_client: Any = None,
    **kwargs: Any,
) -> str:
    """Analyze coverage results and identify gaps."""
    try:
        # ── Try new coverage parser ───────────────────────────────
        work_dir = kwargs.get("work_dir", "")
        vcd_path = kwargs.get("vcd_path", "")
        coverage_dat_path = kwargs.get("coverage_dat_path", "")
        rtl_path = kwargs.get("rtl_path", "")

        report = None

        try:
            from services.verification.coverage_parser import (
                auto_parse_coverage,
                parse_verilator_coverage,
                parse_icarus_coverage,
                parse_coverage_from_log,
            )

            if coverage_dat_path:
                report = parse_verilator_coverage(coverage_dat_path)
            elif vcd_path:
                report = parse_icarus_coverage(vcd_path, rtl_path)
            elif work_dir:
                report = auto_parse_coverage(work_dir, vcd_path, rtl_path)
            elif coverage_data:
                report = parse_coverage_from_log(coverage_data)

        except ImportError:
            logger.info("coverage_parser not available, using regex fallback")

        # ── Fallback: regex-based parsing from raw text ───────────
        if report is None and coverage_data:
            metrics = _parse_coverage_metrics_legacy(coverage_data)
            report_dict = {
                "line_coverage": metrics.get("line_coverage", 0),
                "branch_coverage": metrics.get("branch_coverage", 0),
                "toggle_coverage": metrics.get("toggle_coverage", 0),
                "functional_coverage": metrics.get("functional_coverage", 0),
                "grade": _compute_grade_legacy(metrics),
                "source": "regex_fallback",
                "uncovered_count": {"lines": 0, "branches": 0, "toggles": 0},
                "uncovered_lines": [],
                "uncovered_branches": [],
            }
        elif report is not None:
            report_dict = report.to_dict()
        else:
            report_dict = {
                "line_coverage": 0,
                "branch_coverage": 0,
                "toggle_coverage": 0,
                "functional_coverage": 0,
                "grade": "N/A",
                "source": "none",
                "uncovered_count": {"lines": 0, "branches": 0, "toggles": 0},
            }

        # ── Gap analysis ──────────────────────────────────────────
        gaps = []
        suggestions = []

        line_cov = report_dict.get("line_coverage", 0)
        branch_cov = report_dict.get("branch_coverage", 0)
        toggle_cov = report_dict.get("toggle_coverage", 0)

        if line_cov > 0 and line_cov < 90:
            gaps.append({
                "type": "line", "current": line_cov,
                "target": 90, "severity": "high",
            })
            suggestions.append("Add directed tests for uncovered RTL branches")

        if branch_cov > 0 and branch_cov < 80:
            gaps.append({
                "type": "branch", "current": branch_cov,
                "target": 80, "severity": "high",
            })
            suggestions.append("Add tests for untaken branches in FSMs and conditionals")

        if toggle_cov > 0 and toggle_cov < 70:
            gaps.append({
                "type": "toggle", "current": toggle_cov,
                "target": 70, "severity": "medium",
            })
            suggestions.append("Add stimulus to toggle all port bits")

        # ── Check requirement coverage from mental model ──────────
        model = kwargs.get("mental_model")
        if not model and db_session:
            try:
                from agent_tools.mental_model_tools import _load_mental_model
                model = await _load_mental_model(project_id, db_session)
            except Exception:
                model = None

        if model:
            try:
                verification = model.verification if hasattr(model, "verification") else None
                if verification:
                    unit_tests = getattr(verification, "unit_tests", [])
                    requirements = getattr(model, "requirements", [])
                    covered_req_ids = set()
                    for ut in unit_tests:
                        covered_req_ids.update(getattr(ut, "requirement_ids", []))
                    uncovered = [
                        r for r in requirements
                        if getattr(r, "id", "") not in covered_req_ids
                    ]
                    if uncovered:
                        for req in uncovered[:10]:
                            gaps.append({
                                "type": "requirement",
                                "requirement_id": getattr(req, "id", ""),
                                "text": getattr(req, "text", "")[:100],
                                "severity": "critical" if getattr(req, "priority", "") in ("high", "critical") else "medium",
                            })
                        suggestions.append(
                            f"{len(uncovered)} requirements have no verification target"
                        )
            except Exception as e:
                logger.warning(f"Model requirement check failed: {e}")

        return json.dumps({
            "status": "analyzed",
            "metrics": report_dict,
            "gaps_count": len(gaps),
            "gaps": gaps[:20],
            "suggestions": suggestions[:10],
            "coverage_grade": report_dict.get("grade", "N/A"),
        }, indent=2)

    except Exception as e:
        logger.exception(f"Coverage analysis failed: {e}")
        return json.dumps({"error": f"Coverage analysis failed: {str(e)}"})


# ─── Legacy Fallback Helpers ─────────────────────────────────────────

def _parse_coverage_metrics_legacy(data: str) -> dict:
    """Parse coverage data from text using regex."""
    import re
    metrics = {}
    for name, pattern in [
        ("line_coverage", r"line[_\s]*coverage[:\s]*([\d.]+)\s*%?"),
        ("branch_coverage", r"branch[_\s]*coverage[:\s]*([\d.]+)\s*%?"),
        ("toggle_coverage", r"toggle[_\s]*coverage[:\s]*([\d.]+)\s*%?"),
        ("functional_coverage", r"functional[_\s]*coverage[:\s]*([\d.]+)\s*%?"),
    ]:
        m = re.search(pattern, data, re.I)
        if m:
            metrics[name] = float(m.group(1))
    return metrics


def _compute_grade_legacy(metrics: dict) -> str:
    """Compute coverage grade."""
    if not metrics:
        return "N/A"
    avg = sum(metrics.values()) / len(metrics)
    if avg >= 95:
        return "A"
    elif avg >= 85:
        return "B"
    elif avg >= 70:
        return "C"
    elif avg >= 50:
        return "D"
    return "F"


# Register handler
register_tool("analyzeCoverage", handle_analyze_coverage)
