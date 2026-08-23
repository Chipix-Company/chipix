"""
Dashboard Aggregator — Combines all verification results into a single dashboard.

Aggregates results from UnitSim, Formal, UVM, Coverage, and Debug into
one unified payload that the frontend dashboard component renders.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class ScenarioStatus:
    """Status of a single test scenario."""
    id: str
    name: str
    category: str = "functional"
    priority: str = "medium"
    status: str = "pending"        # "pass" | "fail" | "running" | "pending"
    message: str = ""
    error_log: str = ""
    duration_ms: int = 0


@dataclass
class VerificationTypeStatus:
    """Status of a verification type (UnitSim/Formal/UVM)."""
    type: str                       # "unitsim" | "formal" | "uvm"
    label: str
    icon: str
    status: str = "not_run"        # "pass" | "fail" | "partial" | "not_run" | "running"
    total: int = 0
    passed: int = 0
    failed: int = 0
    detail: str = ""


@dataclass
class CoverageBar:
    """A single coverage metric bar."""
    label: str
    value: float = 0.0
    target: float = 0.0


@dataclass
class FailureDetail:
    """Detailed info for a failed test."""
    scenario_id: str
    scenario_name: str
    error_message: str = ""
    root_cause: str = ""
    log_excerpt: str = ""
    fix_available: bool = False
    fix_diff: str = ""


@dataclass
class Recommendation:
    """An AI-generated suggestion for improving verification."""
    icon: str = "💡"
    text: str = ""
    severity: str = "info"         # "info" | "warning" | "critical"


@dataclass
class DashboardData:
    """Complete dashboard payload for the frontend."""
    module_name: str = ""
    timestamp: float = 0.0
    overall_status: str = "pending"   # "pass" | "fail" | "partial" | "pending"
    coverage_grade: str = "N/A"

    # KPI stats
    total_scenarios: int = 0
    passed: int = 0
    failed: int = 0
    pending: int = 0
    progress_pct: float = 0.0

    # Coverage
    line_coverage: float = 0.0
    branch_coverage: float = 0.0
    toggle_coverage: float = 0.0
    functional_coverage: float = 0.0
    coverage_bars: List[CoverageBar] = field(default_factory=list)

    # Detailed lists
    scenarios: List[ScenarioStatus] = field(default_factory=list)
    verification_types: List[VerificationTypeStatus] = field(default_factory=list)
    failures: List[FailureDetail] = field(default_factory=list)
    recommendations: List[Recommendation] = field(default_factory=list)

    def to_dict(self) -> Dict:
        return {
            "module_name": self.module_name,
            "timestamp": self.timestamp,
            "overall_status": self.overall_status,
            "coverage_grade": self.coverage_grade,
            "kpis": {
                "total": self.total_scenarios,
                "passed": self.passed,
                "failed": self.failed,
                "pending": self.pending,
                "progress_pct": self.progress_pct,
            },
            "coverage": {
                "line": self.line_coverage,
                "branch": self.branch_coverage,
                "toggle": self.toggle_coverage,
                "functional": self.functional_coverage,
                "bars": [
                    {"label": b.label, "value": b.value, "target": b.target}
                    for b in self.coverage_bars
                ],
                "grade": self.coverage_grade,
            },
            "scenarios": [
                {
                    "id": s.id, "name": s.name, "category": s.category,
                    "priority": s.priority, "status": s.status,
                    "message": s.message, "duration_ms": s.duration_ms,
                }
                for s in self.scenarios
            ],
            "verification_types": [
                {
                    "type": v.type, "label": v.label, "icon": v.icon,
                    "status": v.status, "total": v.total,
                    "passed": v.passed, "failed": v.failed, "detail": v.detail,
                }
                for v in self.verification_types
            ],
            "failures": [
                {
                    "scenario_id": f.scenario_id, "scenario_name": f.scenario_name,
                    "error_message": f.error_message, "root_cause": f.root_cause,
                    "log_excerpt": f.log_excerpt, "fix_available": f.fix_available,
                }
                for f in self.failures
            ],
            "recommendations": [
                {"icon": r.icon, "text": r.text, "severity": r.severity}
                for r in self.recommendations
            ],
        }


def aggregate_dashboard(
    module_name: str = "",
    plan: Any = None,
    unitsim_results: Optional[Dict] = None,
    formal_results: Optional[Dict] = None,
    uvm_results: Optional[Dict] = None,
    coverage_results: Optional[Dict] = None,
    debug_results: Optional[Dict] = None,
) -> DashboardData:
    """
    Aggregate all verification results into a single dashboard payload.

    Args:
        module_name: Design module name
        plan: TestPlan dict with scenarios
        unitsim_results: Results from runUnitSimulation
        formal_results: Results from runFormalVerification
        uvm_results: Results from generateUVMEnvironment
        coverage_results: Results from analyzeCoverage
        debug_results: Results from diagnoseAndFix
    """
    import time

    dashboard = DashboardData(
        module_name=module_name,
        timestamp=time.time(),
    )

    # ── Build scenario list from plan ────────────────────────────
    plan_scenarios = []
    if plan:
        raw_scenarios = plan.get("scenarios", []) if isinstance(plan, dict) else getattr(plan, "scenarios", [])
        for s in raw_scenarios:
            if isinstance(s, dict):
                plan_scenarios.append(ScenarioStatus(
                    id=s.get("id", ""),
                    name=s.get("name", ""),
                    category=s.get("category", "functional"),
                    priority=s.get("priority", "medium"),
                    status="pending",
                ))
            else:
                plan_scenarios.append(ScenarioStatus(
                    id=getattr(s, "id", ""),
                    name=getattr(s, "name", ""),
                    category=getattr(s, "category", "functional"),
                    priority=getattr(s, "priority", "medium"),
                    status="pending",
                ))

    # ── Merge UnitSim results ────────────────────────────────────
    unitsim_pass = 0
    unitsim_fail = 0
    unitsim_total = 0

    if unitsim_results:
        unitsim_pass = unitsim_results.get("pass_count", 0)
        unitsim_fail = unitsim_results.get("fail_count", 0)
        unitsim_total = unitsim_pass + unitsim_fail

        # Map results back to plan scenarios
        test_statuses = unitsim_results.get("test_results", {})
        for scenario in plan_scenarios:
            key = scenario.name
            if key in test_statuses:
                scenario.status = "pass" if test_statuses[key] == "pass" else "fail"
            elif unitsim_total > 0 and scenario.status == "pending":
                # If we ran unitsim but this scenario wasn't in results, mark based on category
                pass

        # Mark failed scenarios from log
        fail_messages = unitsim_results.get("failures", [])
        for msg in fail_messages:
            for scenario in plan_scenarios:
                if scenario.name in str(msg):
                    scenario.status = "fail"
                    scenario.message = str(msg)[:200]

    # ── Merge Formal results ─────────────────────────────────────
    formal_pass = 0
    formal_fail = 0
    formal_total = 0

    if formal_results:
        formal_pass = formal_results.get("properties_passed", 0)
        formal_fail = formal_results.get("properties_failed", 0)
        formal_total = formal_pass + formal_fail

    # ── Merge UVM results ────────────────────────────────────────
    uvm_status = "not_run"
    uvm_detail = ""
    if uvm_results:
        uvm_status = "pass" if uvm_results.get("status") == "success" else "partial"
        uvm_detail = f"{uvm_results.get('files_generated', 0)} files generated"

    # ── Build verification type cards ────────────────────────────
    dashboard.verification_types = [
        VerificationTypeStatus(
            type="unitsim",
            label="Unit Simulation",
            icon="⚡",
            status="pass" if unitsim_fail == 0 and unitsim_pass > 0 else "fail" if unitsim_fail > 0 else "not_run",
            total=unitsim_total,
            passed=unitsim_pass,
            failed=unitsim_fail,
            detail=f"{unitsim_pass}/{unitsim_total} passed" if unitsim_total > 0 else "Not run",
        ),
        VerificationTypeStatus(
            type="formal",
            label="Formal Verification",
            icon="🔒",
            status="pass" if formal_fail == 0 and formal_pass > 0 else "fail" if formal_fail > 0 else "not_run",
            total=formal_total,
            passed=formal_pass,
            failed=formal_fail,
            detail=f"{formal_pass}/{formal_total} properties proven" if formal_total > 0 else "Not run",
        ),
        VerificationTypeStatus(
            type="uvm",
            label="UVM Environment",
            icon="🧪",
            status=uvm_status,
            detail=uvm_detail or "Not run",
        ),
    ]

    # ── Coverage ─────────────────────────────────────────────────
    if coverage_results:
        dashboard.line_coverage = coverage_results.get("line_coverage", 0.0)
        dashboard.branch_coverage = coverage_results.get("branch_coverage", 0.0)
        dashboard.toggle_coverage = coverage_results.get("toggle_coverage", 0.0)
        dashboard.functional_coverage = coverage_results.get("functional_coverage", 0.0)
        dashboard.coverage_grade = coverage_results.get("grade", "N/A")

    dashboard.coverage_bars = [
        CoverageBar(label="Line", value=dashboard.line_coverage, target=90.0),
        CoverageBar(label="Branch", value=dashboard.branch_coverage, target=80.0),
        CoverageBar(label="Toggle", value=dashboard.toggle_coverage, target=70.0),
        CoverageBar(label="Functional", value=dashboard.functional_coverage, target=80.0),
    ]

    # ── Failures detail ──────────────────────────────────────────
    for scenario in plan_scenarios:
        if scenario.status == "fail":
            root_cause = ""
            fix_available = False
            if debug_results:
                root_cause = debug_results.get("root_cause", "")
                fix_available = bool(debug_results.get("diff", ""))

            dashboard.failures.append(FailureDetail(
                scenario_id=scenario.id,
                scenario_name=scenario.name,
                error_message=scenario.message,
                root_cause=root_cause,
                fix_available=fix_available,
            ))

    # ── Compute KPIs ─────────────────────────────────────────────
    dashboard.scenarios = plan_scenarios
    dashboard.total_scenarios = len(plan_scenarios)
    dashboard.passed = sum(1 for s in plan_scenarios if s.status == "pass")
    dashboard.failed = sum(1 for s in plan_scenarios if s.status == "fail")
    dashboard.pending = sum(1 for s in plan_scenarios if s.status == "pending")

    completed = dashboard.passed + dashboard.failed
    dashboard.progress_pct = (
        (completed / dashboard.total_scenarios * 100)
        if dashboard.total_scenarios > 0
        else 0.0
    )

    # ── Overall status ───────────────────────────────────────────
    if dashboard.failed > 0:
        dashboard.overall_status = "fail"
    elif dashboard.pending > 0:
        dashboard.overall_status = "partial"
    elif dashboard.passed > 0:
        dashboard.overall_status = "pass"
    else:
        dashboard.overall_status = "pending"

    # ── Recommendations ──────────────────────────────────────────
    dashboard.recommendations = _generate_recommendations(dashboard)

    logger.info(
        "Dashboard: %s — %d/%d passed, %d failed, %d pending",
        module_name, dashboard.passed, dashboard.total_scenarios,
        dashboard.failed, dashboard.pending,
    )

    return dashboard


def _generate_recommendations(dashboard: DashboardData) -> List[Recommendation]:
    """Generate AI-like recommendations based on verification gaps."""
    recs = []

    # Coverage recommendations
    for bar in dashboard.coverage_bars:
        if 0 < bar.value < bar.target:
            gap = bar.target - bar.value
            severity = "warning" if gap > 20 else "info"
            recs.append(Recommendation(
                icon="📊" if severity == "info" else "⚠️",
                text=f"{bar.label} coverage is {bar.value:.1f}% (target: {bar.target:.0f}%) — add stimulus variation to close the {gap:.1f}% gap",
                severity=severity,
            ))

    # Formal failures
    formal = next((v for v in dashboard.verification_types if v.type == "formal"), None)
    if formal and formal.failed > 0:
        recs.append(Recommendation(
            icon="🔒",
            text=f"{formal.failed} formal properties failed — review counter/FSM logic for edge cases",
            severity="warning",
        ))

    # UVM not run
    uvm = next((v for v in dashboard.verification_types if v.type == "uvm"), None)
    if uvm and uvm.status == "not_run":
        recs.append(Recommendation(
            icon="🧪",
            text="UVM environment not yet run — recommended for constrained random coverage closure",
            severity="info",
        ))

    # Failed tests
    if dashboard.failed > 0:
        recs.append(Recommendation(
            icon="🔧",
            text=f"{dashboard.failed} test(s) failed — use 'Apply Fix' to auto-repair and re-verify",
            severity="critical",
        ))

    # All passing
    if dashboard.failed == 0 and dashboard.passed > 0 and dashboard.pending == 0:
        recs.append(Recommendation(
            icon="✅",
            text="All tests passing — consider adding stress tests for production readiness",
            severity="info",
        ))

    return recs
