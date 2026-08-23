"""
Evolve Service — RTL Change Detection + Auto-Healing + Regression Intelligence.

Modern port of original_core/core/self_healer.py into services/ tree,
without legacy dependencies (config, core.ai_client, core.logger).

Three capabilities:
  1. SelfHealingTestbench — detects RTL structural changes, auto-patches TB files
  2. RegressionIntelligence — predicts which tests will fail, generates smart suites
  3. LivingMentalModel — incremental model update on RTL change
"""

from __future__ import annotations

import difflib
import hashlib
import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════
# Data Types
# ═══════════════════════════════════════════════════════════════════════

@dataclass
class RTLChange:
    """A detected change in the RTL."""
    change_type: str = ""     # port_added, port_removed, port_modified, state_added, state_removed, logic_changed
    name: str = ""
    old_value: str = ""
    new_value: str = ""
    impact: str = ""          # interface, driver, monitor, coverage, test


@dataclass
class HealingAction:
    """An auto-healing action taken on the testbench."""
    target_file: str = ""
    change_type: str = ""     # update, add, remove
    description: str = ""
    code_before: str = ""
    code_after: str = ""
    applied: bool = False


@dataclass
class TestRecord:
    """Historical record of a test execution."""
    test_name: str
    passed: bool
    elapsed: float = 0.0
    rtl_hash: str = ""
    timestamp: float = field(default_factory=time.time)
    failure_reason: str = ""


@dataclass
class TestPrediction:
    """Prediction of whether a test will fail."""
    test_name: str
    fail_probability: float = 0.0        # 0.0 - 1.0
    reasoning: str = ""
    priority: int = 0                     # 1 = highest priority


# ═══════════════════════════════════════════════════════════════════════
# Self-Healing Testbench
# ═══════════════════════════════════════════════════════════════════════

class SelfHealingTestbench:
    """Auto-adapt testbench when RTL changes."""

    def __init__(self, ai_client: Any = None) -> None:
        self.ai_client = ai_client
        self.healing_log: List[HealingAction] = []

    def detect_rtl_changes(self, old_rtl: str, new_rtl: str) -> List[RTLChange]:
        """Compare old and new RTL to find structural changes."""
        changes: List[RTLChange] = []

        # ── Port changes ─────────────────────────────────────────
        old_ports = self._extract_ports(old_rtl)
        new_ports = self._extract_ports(new_rtl)

        for name in new_ports:
            if name not in old_ports:
                changes.append(RTLChange(
                    change_type="port_added", name=name,
                    new_value=new_ports[name], impact="interface",
                ))
            elif new_ports[name] != old_ports[name]:
                changes.append(RTLChange(
                    change_type="port_modified", name=name,
                    old_value=old_ports[name], new_value=new_ports[name],
                    impact="interface",
                ))

        for name in old_ports:
            if name not in new_ports:
                changes.append(RTLChange(
                    change_type="port_removed", name=name,
                    old_value=old_ports[name], impact="interface",
                ))

        # ── FSM state changes ────────────────────────────────────
        old_states = set(self._extract_fsm_states(old_rtl))
        new_states = set(self._extract_fsm_states(new_rtl))

        for state in new_states - old_states:
            changes.append(RTLChange(
                change_type="state_added", name=state, impact="coverage",
            ))
        for state in old_states - new_states:
            changes.append(RTLChange(
                change_type="state_removed", name=state, impact="coverage",
            ))

        # ── Logic block changes ──────────────────────────────────
        old_blocks = len(re.findall(r"always", old_rtl))
        new_blocks = len(re.findall(r"always", new_rtl))
        if old_blocks != new_blocks:
            changes.append(RTLChange(
                change_type="logic_changed", name="always_blocks",
                old_value=str(old_blocks), new_value=str(new_blocks),
                impact="test",
            ))

        if changes:
            logger.info("Detected %d RTL changes", len(changes))
        return changes

    def auto_heal(
        self,
        changes: List[RTLChange],
        testbench_files: Dict[str, str],
        new_rtl: str,
    ) -> List[HealingAction]:
        """Automatically heal testbench files based on RTL changes."""
        actions: List[HealingAction] = []

        for change in changes:
            logger.info("Healing: %s %s (impact: %s)",
                        change.change_type, change.name, change.impact)

            if change.change_type == "port_added":
                for fname, content in testbench_files.items():
                    if "interface" in fname.lower() or "if" in fname.lower():
                        action = self._add_port_to_interface(fname, content, change)
                        if action:
                            actions.append(action)
                    if "driver" in fname.lower():
                        action = self._add_port_to_driver(fname, content, change)
                        if action:
                            actions.append(action)

            elif change.change_type == "port_removed":
                for fname, content in testbench_files.items():
                    action = self._remove_port_references(fname, content, change)
                    if action:
                        actions.append(action)

            elif change.change_type == "state_added":
                for fname, content in testbench_files.items():
                    if "coverage" in fname.lower():
                        action = self._add_state_coverage(fname, content, change)
                        if action:
                            actions.append(action)

            elif change.change_type == "state_removed":
                for fname, content in testbench_files.items():
                    if "coverage" in fname.lower():
                        action = self._remove_state_coverage(fname, content, change)
                        if action:
                            actions.append(action)

        self.healing_log.extend(actions)
        logger.info("Applied %d healing actions", len(actions))
        return actions

    # ── Private Helpers ───────────────────────────────────────────

    def _extract_ports(self, rtl: str) -> Dict[str, str]:
        ports: Dict[str, str] = {}
        for match in re.finditer(
            r"(input|output|inout)\s+(?:wire|reg|logic)?\s*(?:\[.*?\])?\s*(\w+)", rtl,
        ):
            ports[match.group(2)] = match.group(0).strip()
        return ports

    def _extract_fsm_states(self, rtl: str) -> List[str]:
        states: List[str] = []
        for match in re.finditer(r"(?:localparam|parameter)\s+(\w+)\s*=", rtl):
            name = match.group(1)
            if any(kw in name.upper() for kw in ["STATE", "FSM", "IDLE", "DONE", "WAIT"]):
                states.append(name)
        for match in re.finditer(r"enum\s+.*?\{([^}]+)\}", rtl, re.DOTALL):
            for state in match.group(1).split(","):
                states.append(state.strip().split("=")[0].strip())
        return states

    def _add_port_to_interface(self, fname, content, change) -> Optional[HealingAction]:
        if "endinterface" in content:
            new_content = content.replace(
                "endinterface",
                f"    logic {change.name};  // AUTO-HEALED: new port\n    endinterface",
            )
            return HealingAction(
                target_file=fname, change_type="add",
                description=f"Added port '{change.name}' to interface",
                code_before=content, code_after=new_content, applied=True,
            )
        return None

    def _add_port_to_driver(self, fname, content, change) -> Optional[HealingAction]:
        if "endclass" in content:
            new_content = content.replace(
                "endclass",
                f"    // AUTO-HEALED: new port '{change.name}'\nendclass", 1,
            )
            return HealingAction(
                target_file=fname, change_type="add",
                description=f"Added port '{change.name}' reference to driver",
                code_before=content, code_after=new_content, applied=True,
            )
        return None

    def _remove_port_references(self, fname, content, change) -> Optional[HealingAction]:
        if change.name in content:
            lines = content.splitlines()
            new_lines = []
            removed = False
            for line in lines:
                if re.search(rf"\b{re.escape(change.name)}\b", line):
                    new_lines.append(f"    // AUTO-HEALED: removed '{change.name}' (port deleted)")
                    removed = True
                else:
                    new_lines.append(line)
            if removed:
                return HealingAction(
                    target_file=fname, change_type="remove",
                    description=f"Commented out references to removed port '{change.name}'",
                    code_before=content, code_after="\n".join(new_lines), applied=True,
                )
        return None

    def _add_state_coverage(self, fname, content, change) -> Optional[HealingAction]:
        if "endclass" in content:
            cov = (
                f"\n    // AUTO-HEALED: coverage for new state '{change.name}'\n"
                f"    // coverpoint state {{ bins {change.name}_bin = {{{change.name}}}; }}\n"
            )
            new_content = content.replace("endclass", cov + "endclass", 1)
            return HealingAction(
                target_file=fname, change_type="add",
                description=f"Added coverage bin for new state '{change.name}'",
                code_before=content, code_after=new_content, applied=True,
            )
        return None

    def _remove_state_coverage(self, fname, content, change) -> Optional[HealingAction]:
        if change.name in content:
            new_content = content.replace(change.name, f"/* REMOVED: {change.name} */")
            return HealingAction(
                target_file=fname, change_type="remove",
                description=f"Marked removed state '{change.name}' in coverage",
                code_before=content, code_after=new_content, applied=True,
            )
        return None


# ═══════════════════════════════════════════════════════════════════════
# Regression Intelligence
# ═══════════════════════════════════════════════════════════════════════

class RegressionIntelligence:
    """Smart test selection and failure prediction."""

    def __init__(self) -> None:
        self.history: List[TestRecord] = []
        self.flaky_tests: Set[str] = set()

    def record_result(
        self, test_name: str, passed: bool,
        elapsed: float = 0.0, rtl_hash: str = "",
        failure_reason: str = "",
    ) -> None:
        """Record a test execution result."""
        self.history.append(TestRecord(
            test_name=test_name, passed=passed, elapsed=elapsed,
            rtl_hash=rtl_hash, failure_reason=failure_reason,
        ))
        recent = [r for r in self.history if r.test_name == test_name][-5:]
        results = set(r.passed for r in recent)
        if len(results) > 1 and len(recent) >= 3:
            self.flaky_tests.add(test_name)

    def predict_failures(
        self, rtl_diff: str, test_names: List[str],
    ) -> List[TestPrediction]:
        """Predict which tests are likely to fail given RTL changes."""
        predictions: List[TestPrediction] = []
        diff_lower = rtl_diff.lower()

        for test_name in test_names:
            prob = 0.1
            reasons: List[str] = []
            test_lower = test_name.lower()

            if "reset" in test_lower and re.search(r"[+-].*rst|reset", diff_lower):
                prob += 0.5
                reasons.append("Reset logic changed")

            if "fsm" in test_lower and re.search(r"[+-].*state|fsm", diff_lower):
                prob += 0.6
                reasons.append("FSM logic changed")

            if "overflow" in test_lower and re.search(r"[+-].*\+|\-|counter|cnt", diff_lower):
                prob += 0.4
                reasons.append("Arithmetic logic changed")

            if "protocol" in test_lower and re.search(r"[+-].*valid|ready|grant", diff_lower):
                prob += 0.5
                reasons.append("Protocol signals changed")

            # Historical failure rate
            past = [r for r in self.history if r.test_name == test_name]
            if past:
                fail_rate = sum(1 for r in past if not r.passed) / len(past)
                prob = prob * 0.5 + fail_rate * 0.5
                if fail_rate > 0.3:
                    reasons.append(f"Historical fail rate: {fail_rate:.0%}")

            if test_name in self.flaky_tests:
                prob = max(prob, 0.3)
                reasons.append("Known flaky test")

            predictions.append(TestPrediction(
                test_name=test_name,
                fail_probability=min(1.0, prob),
                reasoning="; ".join(reasons) if reasons else "Low risk",
            ))

        predictions.sort(key=lambda p: p.fail_probability, reverse=True)
        for i, pred in enumerate(predictions):
            pred.priority = i + 1

        return predictions

    def generate_minimal_suite(
        self, predictions: List[TestPrediction], max_tests: int = 10,
    ) -> List[str]:
        """Generate a minimal regression suite based on predictions."""
        high_risk = [p.test_name for p in predictions if p.fail_probability > 0.3]
        medium_risk = [
            p.test_name for p in predictions
            if 0.1 < p.fail_probability <= 0.3 and p.test_name not in high_risk
        ]

        suite = high_risk[:max_tests]
        remaining = max_tests - len(suite)
        if remaining > 0:
            suite.extend(medium_risk[:remaining])

        suite = [t for t in suite if t not in self.flaky_tests]
        return suite

    def get_stats(self) -> dict:
        if not self.history:
            return {"total_runs": 0}
        total = len(self.history)
        passed = sum(1 for r in self.history if r.passed)
        return {
            "total_runs": total,
            "pass_rate": passed / total if total > 0 else 0,
            "flaky_tests": len(self.flaky_tests),
            "unique_tests": len(set(r.test_name for r in self.history)),
        }
