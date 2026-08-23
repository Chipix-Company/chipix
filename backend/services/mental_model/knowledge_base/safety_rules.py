"""
Functional Safety Rules — Knowledge Base

Rules derived from automotive and industrial safety standards for
safety-critical hardware verification.

Sources:
  - ISO 26262 (Road Vehicles — Functional Safety)
  - IEC 61508 (Functional Safety of E/E/PE Systems)
  - ISO 26262 Part 5 — Hardware Design
  - ISO 26262 Part 11 — Semiconductors
  - FMEDA methodology (Failure Modes, Effects, and Diagnostic Analysis)
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Dict, List


@dataclass
class SafetyRule:
    """A functional safety verification rule.

    Attributes:
        id:              Unique identifier.
        standard:        Source standard (ISO 26262, IEC 61508).
        section_ref:     Section reference in the standard.
        asil_level:      Minimum ASIL level this rule applies to.
        title:           Short title.
        description:     What to verify.
        safety_mechanism:The safety mechanism being verified.
        failure_mode:    The failure mode this rule guards against.
        diagnostic_coverage: Expected DC level (High/Medium/Low).
        trigger_signals: Signals that indicate applicability.
        severity:        ``"P0"`` | ``"P1"`` | ``"P2"``.
        verification_approach: How to verify.
        rationale:       Why this matters for safety.
    """
    id: str
    standard: str
    section_ref: str
    asil_level: str
    title: str
    description: str
    safety_mechanism: str
    failure_mode: str
    diagnostic_coverage: str
    trigger_signals: List[str]
    severity: str
    verification_approach: str
    rationale: str


# ═══════════════════════════════════════════════════════════════════════
# Lockstep & Redundancy Checking
# ═══════════════════════════════════════════════════════════════════════

_LOCKSTEP_RULES: List[SafetyRule] = [
    SafetyRule(
        id="SAFETY-LOCK-001", standard="ISO 26262", section_ref="Part 5, Annex D — Table D.5",
        asil_level="ASIL-D",
        title="Lockstep comparator detects single-bit error in main core",
        description="Inject a single-bit fault into the main core's output. Verify the lockstep comparator detects the mismatch within the specified detection latency.",
        safety_mechanism="Dual-core lockstep comparison",
        failure_mode="Silent data corruption in processor output",
        diagnostic_coverage="High (>99%)",
        trigger_signals=["lockstep", "compare", "checker", "redundant", "mismatch"],
        severity="P0", verification_approach="uvm",
        rationale="Lockstep is the primary safety mechanism for ASIL-D processors. If the comparator fails to detect, no other mechanism catches the error.",
    ),
    SafetyRule(
        id="SAFETY-LOCK-002", standard="ISO 26262", section_ref="Part 5, Annex D",
        asil_level="ASIL-D",
        title="Lockstep comparator detects multi-cycle error",
        description="Inject a persistent fault (stuck-at) in the main core. Verify the comparator detects the divergence even when the fault affects only specific instruction types.",
        safety_mechanism="Dual-core lockstep comparison",
        failure_mode="Latent fault in computation path",
        diagnostic_coverage="High (>99%)",
        trigger_signals=["lockstep", "compare", "stuck", "fault", "inject"],
        severity="P0", verification_approach="uvm",
        rationale="Stuck-at faults may only manifest with specific inputs — must be tested with diverse stimuli.",
    ),
    SafetyRule(
        id="SAFETY-LOCK-003", standard="ISO 26262", section_ref="Part 5, Annex D",
        asil_level="ASIL-D",
        title="Lockstep delay counter correctness",
        description="If the lockstep core runs with a fixed cycle delay (typically 2-4 cycles), verify the comparison aligns correctly and doesn't have an off-by-one error.",
        safety_mechanism="Delayed lockstep comparison",
        failure_mode="False alarm or missed detection due to misaligned comparison",
        diagnostic_coverage="High",
        trigger_signals=["lockstep", "delay", "offset", "compare", "align"],
        severity="P0", verification_approach="formal",
        rationale="Off-by-one in lockstep delay causes continuous false alarms or missed real errors.",
    ),
    SafetyRule(
        id="SAFETY-LOCK-004", standard="ISO 26262", section_ref="Part 5, Annex E",
        asil_level="ASIL-C",
        title="TMR voter logic correctness",
        description="In a triple-modular-redundancy (TMR) system, inject single and double faults. Verify the voter output matches the majority for single faults and detects double faults.",
        safety_mechanism="Triple Modular Redundancy (TMR)",
        failure_mode="Voter passes incorrect majority value",
        diagnostic_coverage="High (single fault), Medium (double fault)",
        trigger_signals=["tmr", "voter", "majority", "redundant", "triple"],
        severity="P0", verification_approach="formal",
        rationale="TMR voter is the last line of defense — incorrect voting defeats the entire redundancy scheme.",
    ),
]


# ═══════════════════════════════════════════════════════════════════════
# Watchdog & Timer Safety
# ═══════════════════════════════════════════════════════════════════════

_WATCHDOG_RULES: List[SafetyRule] = [
    SafetyRule(
        id="SAFETY-WDG-001", standard="ISO 26262", section_ref="Part 5, Annex E — E.12",
        asil_level="ASIL-B",
        title="Watchdog timeout triggers safe state entry",
        description="Allow the watchdog timer to expire without service. Verify it triggers the specified safe state action (reset, NMI, or safe-state pin assertion).",
        safety_mechanism="Independent watchdog timer",
        failure_mode="System hangs without recovery",
        diagnostic_coverage="High",
        trigger_signals=["watchdog", "wdog", "timeout", "expire", "service", "kick"],
        severity="P0", verification_approach="uvm",
        rationale="Watchdog is the primary software execution monitor — if it doesn't fire, hung software is undetected.",
    ),
    SafetyRule(
        id="SAFETY-WDG-002", standard="ISO 26262", section_ref="Part 5, Annex E",
        asil_level="ASIL-B",
        title="Window watchdog rejects early service",
        description="Service the window watchdog before the open window begins. Verify it triggers a reset/error — early service indicates software timing error.",
        safety_mechanism="Window watchdog timer",
        failure_mode="Software execution timing error not detected",
        diagnostic_coverage="High",
        trigger_signals=["window", "watchdog", "early", "service", "kick"],
        severity="P0", verification_approach="uvm",
        rationale="Window watchdog catches both late (hung) and early (racing) software — tests temporal correctness.",
    ),
    SafetyRule(
        id="SAFETY-WDG-003", standard="ISO 26262", section_ref="Part 5, Annex E",
        asil_level="ASIL-C",
        title="Watchdog independent clock source",
        description="Verify the watchdog timer uses an independent clock source (not derived from the main system clock). If the main clock fails, the watchdog must still operate.",
        safety_mechanism="Independent clock for watchdog",
        failure_mode="Clock failure disables both system and watchdog",
        diagnostic_coverage="High",
        trigger_signals=["watchdog", "clock", "independent", "osc", "ring_osc"],
        severity="P0", verification_approach="formal",
        rationale="Common-mode clock failure that kills both system and watchdog = no safety mechanism active.",
    ),
    SafetyRule(
        id="SAFETY-WDG-004", standard="ISO 26262", section_ref="Part 5, Annex E",
        asil_level="ASIL-D",
        title="Watchdog cannot be disabled by software in production",
        description="Verify that the watchdog disable bit is locked (by fuse or lifecycle state) in production. Software should only be able to service, not stop.",
        safety_mechanism="Watchdog lock mechanism",
        failure_mode="Malicious or errant software disables watchdog",
        diagnostic_coverage="High",
        trigger_signals=["watchdog", "disable", "lock", "fuse", "lifecycle"],
        severity="P0", verification_approach="uvm",
        rationale="Disabling the watchdog removes the primary software execution monitor.",
    ),
]


# ═══════════════════════════════════════════════════════════════════════
# Memory Protection & ECC Safety
# ═══════════════════════════════════════════════════════════════════════

_MEMORY_SAFETY_RULES: List[SafetyRule] = [
    SafetyRule(
        id="SAFETY-MEM-001", standard="ISO 26262", section_ref="Part 5, Annex D — D.2",
        asil_level="ASIL-B",
        title="ECC corrects all single-bit errors in SRAM",
        description="Inject single-bit errors at every bit position in the SRAM word + ECC field. Verify every single-bit error is corrected and the correction is logged.",
        safety_mechanism="ECC (SECDED)",
        failure_mode="Silent data corruption in SRAM",
        diagnostic_coverage="High (>99%)",
        trigger_signals=["ecc", "sram", "correct", "sec", "single_bit", "inject"],
        severity="P0", verification_approach="unitsim",
        rationale="SRAM ECC is typically the highest-coverage safety mechanism in an SoC.",
    ),
    SafetyRule(
        id="SAFETY-MEM-002", standard="ISO 26262", section_ref="Part 5, Annex D",
        asil_level="ASIL-B",
        title="ECC detects all double-bit errors in SRAM",
        description="Inject double-bit errors (adjacent and non-adjacent) in SRAM. Verify DED (double-error detection) flag is raised for all combinations.",
        safety_mechanism="ECC (SECDED)",
        failure_mode="Undetected multi-bit corruption",
        diagnostic_coverage="High (>99%)",
        trigger_signals=["ecc", "ded", "double_bit", "uncorrectable", "detect"],
        severity="P0", verification_approach="unitsim",
        rationale="Undetected double-bit error in safety-critical data = hazardous system behaviour.",
    ),
    SafetyRule(
        id="SAFETY-MEM-003", standard="ISO 26262", section_ref="Part 5, Annex D",
        asil_level="ASIL-D",
        title="ECC scrubbing detects latent errors",
        description="Enable ECC scrubbing (background read-correct-write). Verify it detects and corrects accumulated single-bit errors before they become double-bit.",
        safety_mechanism="ECC scrubbing / patrol read",
        failure_mode="Single-bit error accumulates to multi-bit (latent fault becomes detected)",
        diagnostic_coverage="Medium",
        trigger_signals=["scrub", "patrol", "ecc", "background", "correct"],
        severity="P1", verification_approach="uvm",
        rationale="Without scrubbing, soft errors accumulate and eventually exceed ECC correction capability.",
    ),
    SafetyRule(
        id="SAFETY-MEM-004", standard="ISO 26262", section_ref="Part 5, Annex D",
        asil_level="ASIL-B",
        title="Parity coverage on register file",
        description="Inject single-bit errors in the register file. Verify parity checking detects the corruption when the register is read.",
        safety_mechanism="Parity on register file",
        failure_mode="Silent register corruption",
        diagnostic_coverage="Medium (~50% for even/odd parity)",
        trigger_signals=["register", "parity", "regfile", "rf_parity", "reg_err"],
        severity="P0", verification_approach="unitsim",
        rationale="Register file corruption directly affects all computations — must be detected.",
    ),
    SafetyRule(
        id="SAFETY-MEM-005", standard="ISO 26262", section_ref="Part 5, Annex D",
        asil_level="ASIL-C",
        title="MPU/PMP prevents unprivileged access to safety-critical memory",
        description="From unprivileged code, attempt to read/write/execute memory regions marked as privileged-only. Verify all accesses are denied with the correct exception.",
        safety_mechanism="Memory Protection Unit (MPU/PMP)",
        failure_mode="Unprivileged code corrupts safety-critical data",
        diagnostic_coverage="High",
        trigger_signals=["mpu", "pmp", "region", "privilege", "fault", "access"],
        severity="P0", verification_approach="uvm",
        rationale="MPU bypass allows errant application code to corrupt safety-critical OS data.",
    ),
    SafetyRule(
        id="SAFETY-MEM-006", standard="ISO 26262", section_ref="Part 11 — Semiconductor",
        asil_level="ASIL-B",
        title="ROM integrity check (CRC/checksum on boot ROM)",
        description="Verify the boot ROM integrity check runs at startup and detects any corruption (bit flip in ROM content).",
        safety_mechanism="ROM CRC/checksum verification",
        failure_mode="Corrupted ROM code executed",
        diagnostic_coverage="High",
        trigger_signals=["rom", "crc", "checksum", "integrity", "boot", "verify"],
        severity="P0", verification_approach="unitsim",
        rationale="Corrupted boot code that is not detected leads to unpredictable system behaviour.",
    ),
]


# ═══════════════════════════════════════════════════════════════════════
# Clock & Voltage Monitoring
# ═══════════════════════════════════════════════════════════════════════

_CLOCK_SAFETY_RULES: List[SafetyRule] = [
    SafetyRule(
        id="SAFETY-CLK-001", standard="ISO 26262", section_ref="Part 5, Annex D — D.14",
        asil_level="ASIL-B",
        title="Clock monitor detects frequency drift above threshold",
        description="Drift the clock frequency above the upper threshold. Verify the clock monitor raises an alert within the specified detection latency.",
        safety_mechanism="Clock frequency monitor",
        failure_mode="System operates at wrong frequency — timing violations",
        diagnostic_coverage="High",
        trigger_signals=["clk_monitor", "freq", "threshold", "alert", "drift"],
        severity="P0", verification_approach="uvm",
        rationale="Wrong clock frequency causes timing violations that corrupt data silently.",
    ),
    SafetyRule(
        id="SAFETY-CLK-002", standard="ISO 26262", section_ref="Part 5, Annex D",
        asil_level="ASIL-B",
        title="Clock monitor detects frequency drift below threshold",
        description="Drift the clock frequency below the lower threshold. Verify detection.",
        safety_mechanism="Clock frequency monitor",
        failure_mode="System too slow — misses real-time deadlines",
        diagnostic_coverage="High",
        trigger_signals=["clk_monitor", "freq", "threshold", "alert", "slow"],
        severity="P0", verification_approach="uvm",
        rationale="Too-slow clock causes real-time deadline misses in safety-critical control loops.",
    ),
    SafetyRule(
        id="SAFETY-CLK-003", standard="ISO 26262", section_ref="Part 5, Annex D",
        asil_level="ASIL-C",
        title="Clock monitor detects stuck clock (no edges)",
        description="Stop the monitored clock entirely. Verify the monitor detects the stuck condition within the specified latency.",
        safety_mechanism="Clock activity monitor",
        failure_mode="System halted — no processing occurs",
        diagnostic_coverage="High",
        trigger_signals=["clk_monitor", "stuck", "stop", "activity", "edge"],
        severity="P0", verification_approach="uvm",
        rationale="Stuck clock = complete system failure. Must be detected by an independent monitor.",
    ),
    SafetyRule(
        id="SAFETY-CLK-004", standard="ISO 26262", section_ref="Part 5, Annex E",
        asil_level="ASIL-B",
        title="Voltage monitor detects out-of-range condition",
        description="Simulate a voltage droop or spike. Verify the voltage monitor raises an alert and the system enters safe state if the condition persists.",
        safety_mechanism="Supply voltage monitor (PoR/BoD)",
        failure_mode="Logic operates outside safe voltage — unpredictable behaviour",
        diagnostic_coverage="High",
        trigger_signals=["voltage", "por", "bod", "brownout", "power_good", "vdd"],
        severity="P0", verification_approach="uvm",
        rationale="Under-voltage causes logic to operate outside timing margins — silent data corruption.",
    ),
]


# ═══════════════════════════════════════════════════════════════════════
# Safe State & Error Handling
# ═══════════════════════════════════════════════════════════════════════

_SAFE_STATE_RULES: List[SafetyRule] = [
    SafetyRule(
        id="SAFETY-SS-001", standard="ISO 26262", section_ref="Part 5, Section 7.4.3",
        asil_level="ASIL-B",
        title="Safe state reached within FTTI",
        description="Trigger a detectable fault. Measure the time from fault occurrence to safe state entry. Verify it is within the Fault Tolerant Time Interval (FTTI).",
        safety_mechanism="Safe state transition mechanism",
        failure_mode="System remains in hazardous state too long",
        diagnostic_coverage="High",
        trigger_signals=["safe_state", "safe", "ftti", "fault", "detect", "transition"],
        severity="P0", verification_approach="uvm",
        rationale="FTTI violation = hazardous state persists long enough to cause harm.",
    ),
    SafetyRule(
        id="SAFETY-SS-002", standard="ISO 26262", section_ref="Part 5, Section 7.4.3",
        asil_level="ASIL-C",
        title="Safe state outputs are correct",
        description="After entering safe state, verify all safety-critical outputs are in their defined safe values (e.g. motors off, valves closed, brakes applied).",
        safety_mechanism="Safe state output clamping",
        failure_mode="Wrong output in safe state — still hazardous",
        diagnostic_coverage="High",
        trigger_signals=["safe_state", "output", "clamp", "safe_val", "shutdown"],
        severity="P0", verification_approach="unitsim",
        rationale="Entering safe state but with wrong outputs defeats the purpose of the safety mechanism.",
    ),
    SafetyRule(
        id="SAFETY-SS-003", standard="ISO 26262", section_ref="Part 5, Section 7.4.3",
        asil_level="ASIL-D",
        title="Multiple simultaneous faults reach safe state",
        description="Inject two independent faults simultaneously. Verify the system still reaches safe state (not just single-fault tolerance).",
        safety_mechanism="Multi-fault handling",
        failure_mode="Dual-point failure bypasses safety mechanisms",
        diagnostic_coverage="Medium",
        trigger_signals=["fault", "inject", "dual", "multi", "safe_state"],
        severity="P0", verification_approach="uvm",
        rationale="ISO 26262 ASIL-D requires analysis of multi-point faults (dual-point failure metric).",
    ),
    SafetyRule(
        id="SAFETY-SS-004", standard="IEC 61508", section_ref="Part 2, Section 7.4",
        asil_level="ASIL-B",
        title="Diagnostic test interval (online self-test) coverage",
        description="Run the periodic online self-test (BIST). Verify it detects injected faults within the diagnostic test interval.",
        safety_mechanism="Online self-test / periodic BIST",
        failure_mode="Latent fault accumulation",
        diagnostic_coverage="Medium",
        trigger_signals=["bist", "self_test", "diagnostic", "online", "periodic"],
        severity="P1", verification_approach="uvm",
        rationale="Periodic self-test catches latent faults before they combine with another fault to cause harm.",
    ),
    SafetyRule(
        id="SAFETY-SS-005", standard="ISO 26262", section_ref="Part 11",
        asil_level="ASIL-B",
        title="Error reporting chain completeness",
        description="Verify that every safety mechanism's error output is connected through the error reporting chain to the top-level safe state controller. No error must be silently swallowed.",
        safety_mechanism="Error aggregation / alert handler",
        failure_mode="Detected fault not reported — no safe state entry",
        diagnostic_coverage="High",
        trigger_signals=["alert", "error", "report", "chain", "aggregate", "handler"],
        severity="P0", verification_approach="formal",
        rationale="A safety mechanism that detects a fault but can't report it is worthless.",
    ),
    SafetyRule(
        id="SAFETY-SS-006", standard="ISO 26262", section_ref="Part 5, Annex D",
        asil_level="ASIL-C",
        title="FSM encoding prevents single-fault state corruption",
        description="Verify the FSM uses a fault-tolerant encoding (one-hot with parity, or Hamming-distance ≥3). Inject single-bit flips on state register and verify detection.",
        safety_mechanism="FSM encoding with error detection",
        failure_mode="Single bit flip on state register = jump to wrong state",
        diagnostic_coverage="High",
        trigger_signals=["fsm", "state", "encode", "one_hot", "parity", "hamming"],
        severity="P0", verification_approach="formal",
        rationale="An unprotected FSM can jump to any state on a single bit flip — catastrophic.",
    ),
]


# ═══════════════════════════════════════════════════════════════════════
# Aggregate + Lookup
# ═══════════════════════════════════════════════════════════════════════

SAFETY_RULES: List[SafetyRule] = (
    _LOCKSTEP_RULES
    + _WATCHDOG_RULES
    + _MEMORY_SAFETY_RULES
    + _CLOCK_SAFETY_RULES
    + _SAFE_STATE_RULES
)


def get_all_safety_rules() -> List[SafetyRule]:
    """Return all functional safety rules."""
    return list(SAFETY_RULES)


def get_safety_rules_by_asil(asil: str) -> List[SafetyRule]:
    """Return safety rules for a given ASIL level or higher."""
    asil_order = {"ASIL-A": 0, "ASIL-B": 1, "ASIL-C": 2, "ASIL-D": 3}
    threshold = asil_order.get(asil.upper(), 0)
    return [r for r in SAFETY_RULES if asil_order.get(r.asil_level, 0) >= threshold]


def get_safety_rules_by_mechanism(mechanism_keyword: str) -> List[SafetyRule]:
    """Return rules that involve a specific safety mechanism."""
    kw = mechanism_keyword.lower()
    return [r for r in SAFETY_RULES if kw in r.safety_mechanism.lower()]
