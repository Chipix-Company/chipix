"""Generate simulator closure and sign-off summaries."""

from __future__ import annotations

from typing import Any


def build_closure_report(
    *,
    analysis: dict[str, Any],
    evidence: list[dict[str, Any]],
    coverage: dict[str, Any] | None = None,
    rtl_integrity: dict[str, Any] | None = None,
) -> dict[str, Any]:
    coverage = coverage or {}
    rtl_integrity = rtl_integrity or {}
    status = analysis.get("status") or "unknown"
    dut_bugs = [item for item in evidence if str(item.get("verdict")) in {"dut_bug", "dut_or_reset_bug"}]
    uvm_bugs = [item for item in evidence if str(item.get("verdict")) == "uvm_bug"]
    ambiguous = [item for item in evidence if str(item.get("verdict")) in {"ambiguous", "needs_spec_review"}]
    coverage_closure = coverage.get("closure") if isinstance(coverage.get("closure"), dict) else {}
    coverage_ready = not coverage_closure or coverage_closure.get("status") == "closed"

    signoff_ready = (
        status == "passed"
        and not dut_bugs
        and not ambiguous
        and rtl_integrity.get("passed", True)
        and coverage_ready
    )
    if signoff_ready:
        headline = "Verification completed with no blocking simulator diagnostics."
    elif dut_bugs:
        headline = f"Verification found {len(dut_bugs)} likely DUT issue(s)."
    elif uvm_bugs:
        headline = f"Verification found {len(uvm_bugs)} generated-UVM issue(s) to repair."
    elif not coverage_ready:
        headline = "Simulation passed, but functional and/or code coverage closure remains open."
    else:
        headline = "Verification needs review with the attached evidence."

    return {
        "status": "signoff_ready" if signoff_ready else "needs_action",
        "headline": headline,
        "simulator_status": status,
        "dut_bug_count": len(dut_bugs),
        "uvm_bug_count": len(uvm_bugs),
        "ambiguous_count": len(ambiguous),
        "coverage": coverage,
        "rtl_integrity": rtl_integrity,
        "recommendation": _recommendation(signoff_ready, dut_bugs, uvm_bugs, ambiguous, coverage),
    }


def _recommendation(signoff_ready: bool, dut_bugs: list, uvm_bugs: list, ambiguous: list, coverage: dict[str, Any]) -> str:
    if signoff_ready:
        metrics = coverage.get("metrics") or {}
        if metrics:
            return "Review coverage metrics and waivers, then mark the run as closed."
        return "Run passed. Add coverage data before final tapeout-style sign-off."
    if uvm_bugs:
        return "Apply or review generated-collateral repairs, then rerun Xcelium."
    if dut_bugs:
        return "Review DUT evidence, fix RTL intentionally, and rerun the same tests."
    if ambiguous:
        return "Map the ambiguous failures to requirements or add more waveform/spec context."
    return "Inspect simulator logs and rerun after addressing blocking diagnostics."
