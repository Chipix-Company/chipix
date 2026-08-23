"""
Power-Aware Verification Rules — Knowledge Base

Rules for verifying power intent (UPF/CPF) and power domain interactions.

Sources:
  - IEEE 1801 (Unified Power Format — UPF)
  - Si2 Common Power Format (CPF)
  - Synopsys / Cadence power-aware simulation methodology
  - Industry best practices for low-power SoC verification
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Dict, List


@dataclass
class PowerRule:
    """A power-aware verification rule.

    Attributes:
        id:               Unique identifier.
        standard:         Source standard or methodology.
        title:            Short title.
        description:      What to verify.
        power_concept:    The power management concept being tested.
        trigger_signals:  Signals indicating applicability.
        severity:         Priority level.
        verification_approach: How to verify.
        rationale:        Why this matters.
        upf_construct:    Related UPF construct (if any).
    """
    id: str
    standard: str
    title: str
    description: str
    power_concept: str
    trigger_signals: List[str]
    severity: str
    verification_approach: str
    rationale: str
    upf_construct: str = ""


# ═══════════════════════════════════════════════════════════════════════
# Power Domain Sequencing
# ═══════════════════════════════════════════════════════════════════════

_DOMAIN_SEQUENCING: List[PowerRule] = [
    PowerRule(
        id="PWR-SEQ-001", standard="IEEE 1801 (UPF)",
        title="Power-up sequence: isolation before power restore",
        description="Verify isolation cells are activated BEFORE the powered-down domain's supply is restored. Measure the timing margin between isolation assertion and power-good.",
        power_concept="Power-up sequencing",
        trigger_signals=["isolate", "iso_en", "power_good", "pwr_on", "supply"],
        severity="P0", verification_approach="formal",
        rationale="If isolation deactivates before power is stable, floating outputs from the off domain propagate X into the on domain.",
        upf_construct="set_isolation -clamp_value",
    ),
    PowerRule(
        id="PWR-SEQ-002", standard="IEEE 1801 (UPF)",
        title="Power-down sequence: isolation before power removal",
        description="Verify isolation cells are activated BEFORE supply is removed from the domain being powered down.",
        power_concept="Power-down sequencing",
        trigger_signals=["isolate", "iso_en", "power_good", "pwr_off", "shutdown"],
        severity="P0", verification_approach="formal",
        rationale="Late isolation during power-down = brief window of X propagation to always-on domain.",
        upf_construct="set_isolation",
    ),
    PowerRule(
        id="PWR-SEQ-003", standard="IEEE 1801 (UPF)",
        title="Retention save before power-down",
        description="Verify retention save signal is asserted and the save operation completes BEFORE power is removed from the domain.",
        power_concept="Retention save sequencing",
        trigger_signals=["retention", "save", "retain", "power_down"],
        severity="P0", verification_approach="uvm",
        rationale="Late save = retention cells capture corrupted values as supply drops = wrong state on restore.",
        upf_construct="set_retention -save_signal",
    ),
    PowerRule(
        id="PWR-SEQ-004", standard="IEEE 1801 (UPF)",
        title="Retention restore after power-up",
        description="Verify retention restore signal is asserted AFTER power is stable and clocks are running, but BEFORE the domain starts normal operation.",
        power_concept="Retention restore sequencing",
        trigger_signals=["retention", "restore", "retain", "power_up"],
        severity="P0", verification_approach="uvm",
        rationale="Early restore before power is stable = metastable retained values. Late restore = domain operates with reset values instead of retained.",
        upf_construct="set_retention -restore_signal",
    ),
    PowerRule(
        id="PWR-SEQ-005", standard="IEEE 1801 (UPF)",
        title="Complete power-down and power-up cycle",
        description="Execute a complete power-down → power-up cycle. Verify all registers in the powered domain either retain their values (if retention) or reset to defaults (if no retention).",
        power_concept="Full power cycle verification",
        trigger_signals=["power_down", "power_up", "pwr_cycle", "domain"],
        severity="P0", verification_approach="uvm",
        rationale="Integration-level test that validates the entire power sequence works end-to-end.",
    ),
    PowerRule(
        id="PWR-SEQ-006", standard="IEEE 1801 (UPF)",
        title="Rapid power cycling (on-off-on stress test)",
        description="Rapidly cycle power on/off/on to the same domain with minimum off-time. Verify state consistency after each cycle.",
        power_concept="Power cycling stress",
        trigger_signals=["power", "cycle", "rapid", "stress", "domain"],
        severity="P1", verification_approach="uvm",
        rationale="Rapid power cycling may expose timing races in the power control sequencer.",
    ),
    PowerRule(
        id="PWR-SEQ-007", standard="IEEE 1801 (UPF)",
        title="Power-down during active transaction",
        description="Initiate power-down while the domain is actively processing a bus transaction. Verify the transaction is either completed or cleanly aborted before power removal.",
        power_concept="Graceful power-down with active traffic",
        trigger_signals=["power_down", "active", "transaction", "busy", "valid"],
        severity="P0", verification_approach="uvm",
        rationale="Power-down mid-transaction can leave bus in broken state (open AXI transaction = deadlock).",
    ),
    PowerRule(
        id="PWR-SEQ-008", standard="IEEE 1801 (UPF)",
        title="Multiple domains powered down simultaneously",
        description="Power down two or more domains at the same time. Verify no ordering dependency between them causes deadlock or data loss.",
        power_concept="Multi-domain power-down",
        trigger_signals=["domain", "power_down", "simultaneous", "multi"],
        severity="P1", verification_approach="uvm",
        rationale="If domain A and domain B have cross-domain signals, simultaneous power-down may violate sequencing.",
    ),
]


# ═══════════════════════════════════════════════════════════════════════
# Isolation Cells
# ═══════════════════════════════════════════════════════════════════════

_ISOLATION_RULES: List[PowerRule] = [
    PowerRule(
        id="PWR-ISO-001", standard="IEEE 1801 (UPF)",
        title="Isolation cell clamp value correctness",
        description="When isolation is active, verify every output from the isolated domain is clamped to the specified value (0, 1, or latched). Check each signal individually.",
        power_concept="Isolation cell functionality",
        trigger_signals=["isolate", "iso_en", "clamp", "output"],
        severity="P0", verification_approach="formal",
        rationale="Wrong clamp value on even one signal can cause downstream logic to malfunction.",
        upf_construct="set_isolation -clamp_value {latch|0|1}",
    ),
    PowerRule(
        id="PWR-ISO-002", standard="IEEE 1801 (UPF)",
        title="Isolation on all cross-domain signals",
        description="Verify that EVERY signal crossing from a switchable domain to an always-on domain passes through an isolation cell. Check for missing isolation.",
        power_concept="Isolation completeness",
        trigger_signals=["domain", "crossing", "isolate", "boundary"],
        severity="P0", verification_approach="formal",
        rationale="Missing isolation cell = one signal carries X into always-on domain = unpredictable behaviour.",
        upf_construct="set_isolation",
    ),
    PowerRule(
        id="PWR-ISO-003", standard="IEEE 1801 (UPF)",
        title="Isolation cell enable polarity",
        description="Verify the isolation enable signal has the correct polarity. Active-high enable should clamp when high; verify the UPF matches the RTL.",
        power_concept="Isolation enable polarity",
        trigger_signals=["iso_en", "isolate", "enable", "polarity"],
        severity="P0", verification_approach="formal",
        rationale="Inverted isolation enable = isolated when domain is ON, exposed when domain is OFF — catastrophic.",
    ),
    PowerRule(
        id="PWR-ISO-004", standard="IEEE 1801 (UPF)",
        title="Isolation cell location (source vs destination)",
        description="Verify isolation cells are placed in the correct power domain — typically in the always-on destination domain, not the switchable source domain.",
        power_concept="Isolation cell placement",
        trigger_signals=["isolate", "domain", "always_on", "switchable"],
        severity="P0", verification_approach="formal",
        rationale="Isolation cell in the switchable domain loses power along with the cell it's supposed to protect.",
        upf_construct="set_isolation -applies_to",
    ),
]


# ═══════════════════════════════════════════════════════════════════════
# Level Shifters
# ═══════════════════════════════════════════════════════════════════════

_LEVEL_SHIFTER_RULES: List[PowerRule] = [
    PowerRule(
        id="PWR-LS-001", standard="IEEE 1801 (UPF)",
        title="Level shifter on all multi-voltage crossings",
        description="Verify every signal crossing between domains with different voltage levels has a level shifter. Check for missing level shifters.",
        power_concept="Level shifter completeness",
        trigger_signals=["level_shift", "voltage", "domain", "crossing", "mv"],
        severity="P0", verification_approach="formal",
        rationale="Missing level shifter causes the receiving domain to see an input outside its valid voltage range — may damage logic.",
        upf_construct="set_level_shifter",
    ),
    PowerRule(
        id="PWR-LS-002", standard="IEEE 1801 (UPF)",
        title="Level shifter direction (high-to-low vs low-to-high)",
        description="Verify the level shifter direction matches the actual voltage relationship between source and destination domains.",
        power_concept="Level shifter directionality",
        trigger_signals=["level_shift", "direction", "h2l", "l2h"],
        severity="P0", verification_approach="formal",
        rationale="Wrong direction level shifter = signal not shifted at all.",
        upf_construct="set_level_shifter -applies_to",
    ),
    PowerRule(
        id="PWR-LS-003", standard="IEEE 1801 (UPF)",
        title="Level shifter behaviour when source domain is off",
        description="When the source domain is powered down, verify the level shifter output is in a defined state (not floating or undefined).",
        power_concept="Level shifter with isolation",
        trigger_signals=["level_shift", "power_down", "source", "off"],
        severity="P0", verification_approach="formal",
        rationale="Level shifter with powered-off input may produce invalid output voltage — must combine with isolation.",
    ),
]


# ═══════════════════════════════════════════════════════════════════════
# Voltage Scaling (DVFS)
# ═══════════════════════════════════════════════════════════════════════

_DVFS_RULES: List[PowerRule] = [
    PowerRule(
        id="PWR-DVFS-001", standard="Industry practice",
        title="DVFS frequency change before voltage change (scale down)",
        description="When scaling down, verify the clock frequency is reduced BEFORE the supply voltage drops. Operating at high frequency with low voltage causes timing violations.",
        power_concept="DVFS scale-down sequence",
        trigger_signals=["dvfs", "freq", "voltage", "scale_down", "opp"],
        severity="P0", verification_approach="uvm",
        rationale="High frequency at low voltage = setup time violations = data corruption.",
    ),
    PowerRule(
        id="PWR-DVFS-002", standard="Industry practice",
        title="DVFS voltage change before frequency change (scale up)",
        description="When scaling up, verify the supply voltage increases BEFORE the clock frequency is raised.",
        power_concept="DVFS scale-up sequence",
        trigger_signals=["dvfs", "freq", "voltage", "scale_up", "opp"],
        severity="P0", verification_approach="uvm",
        rationale="High frequency before voltage stabilises = same timing violation as PWR-DVFS-001.",
    ),
    PowerRule(
        id="PWR-DVFS-003", standard="Industry practice",
        title="DVFS transition during active computation",
        description="Initiate a DVFS operating point change while the domain is actively computing. Verify no data corruption during the transition.",
        power_concept="DVFS transition transparency",
        trigger_signals=["dvfs", "transition", "active", "compute", "busy"],
        severity="P0", verification_approach="uvm",
        rationale="DVFS transition that corrupts in-flight data = intermittent, hard-to-reproduce bugs.",
    ),
    PowerRule(
        id="PWR-DVFS-004", standard="Industry practice",
        title="DVFS voltage settling time respected",
        description="Verify the system waits for the voltage regulator settling time before increasing clock frequency.",
        power_concept="Voltage settling time",
        trigger_signals=["settling", "regulator", "stable", "voltage", "dvfs"],
        severity="P0", verification_approach="uvm",
        rationale="Clock raised before voltage is stable = operating outside spec for the settling duration.",
    ),
]


# ═══════════════════════════════════════════════════════════════════════
# X-Propagation & Corruption Checking
# ═══════════════════════════════════════════════════════════════════════

_XPROP_RULES: List[PowerRule] = [
    PowerRule(
        id="PWR-XPROP-001", standard="Industry practice",
        title="X-propagation from powered-off domain",
        description="When a domain is powered off, verify that no X values propagate from the off domain to any on domain. All cross-domain paths must be isolated.",
        power_concept="X-propagation prevention",
        trigger_signals=["x_prop", "power_off", "domain", "isolate"],
        severity="P0", verification_approach="formal",
        rationale="X reaching active logic can cause both functional failures and pessimistic simulation masking real bugs.",
    ),
    PowerRule(
        id="PWR-XPROP-002", standard="Industry practice",
        title="X-injection on power-off (corruption model)",
        description="When power is removed from a domain, verify the simulator correctly models all registers and memories in that domain as X (corrupted).",
        power_concept="Power-off corruption modelling",
        trigger_signals=["power_off", "corrupt", "x_inject", "domain"],
        severity="P0", verification_approach="uvm",
        rationale="If simulator doesn't corrupt state on power-off, retention bugs won't be caught.",
    ),
    PowerRule(
        id="PWR-XPROP-003", standard="Industry practice",
        title="No X in always-on domain after power event",
        description="After any power domain transition (on→off or off→on), verify that no register or wire in the always-on domain contains X.",
        power_concept="Always-on domain integrity",
        trigger_signals=["always_on", "aon", "x_check", "domain"],
        severity="P0", verification_approach="formal",
        rationale="X in always-on domain means isolation failed — this is a design bug.",
    ),
    PowerRule(
        id="PWR-XPROP-004", standard="Industry practice",
        title="Retention cell does not restore X",
        description="After retention restore, verify that no retention cell contains X. If the save was performed correctly, all retained registers must have valid 0/1 values.",
        power_concept="Retention integrity",
        trigger_signals=["retention", "restore", "x_check", "valid"],
        severity="P0", verification_approach="uvm",
        rationale="Retention cell restoring X means the save happened after power was already dropping — sequencing bug.",
    ),
]


# ═══════════════════════════════════════════════════════════════════════
# Power Intent Consistency
# ═══════════════════════════════════════════════════════════════════════

_INTENT_RULES: List[PowerRule] = [
    PowerRule(
        id="PWR-INT-001", standard="IEEE 1801 (UPF)",
        title="UPF/CPF matches RTL power connectivity",
        description="Verify that every power domain defined in the UPF matches the RTL's actual power supply connections. No domain should be orphaned or misconnected.",
        power_concept="Power intent consistency",
        trigger_signals=["upf", "cpf", "supply", "domain", "connect"],
        severity="P0", verification_approach="formal",
        rationale="UPF/RTL mismatch means simulation doesn't match silicon — all power-aware simulation results are invalid.",
        upf_construct="create_power_domain / connect_supply_net",
    ),
    PowerRule(
        id="PWR-INT-002", standard="IEEE 1801 (UPF)",
        title="All switchable domains have power switch",
        description="Verify every domain declared as switchable in UPF has a corresponding power switch element (header or footer switch).",
        power_concept="Power switch completeness",
        trigger_signals=["switch", "header", "footer", "power_gate"],
        severity="P0", verification_approach="formal",
        rationale="Domain declared switchable but with no switch = power never actually turns off.",
        upf_construct="create_power_switch",
    ),
    PowerRule(
        id="PWR-INT-003", standard="IEEE 1801 (UPF)",
        title="Power state table completeness",
        description="Verify the power state table covers all legal power state combinations. No undefined states should be reachable by the power controller.",
        power_concept="Power state table validation",
        trigger_signals=["pst", "power_state", "state_table", "combination"],
        severity="P1", verification_approach="formal",
        rationale="Undefined power state = simulator doesn't know which cells are on or off = wrong simulation.",
        upf_construct="add_power_state",
    ),
]


# ═══════════════════════════════════════════════════════════════════════
# Aggregate + Lookup
# ═══════════════════════════════════════════════════════════════════════

POWER_RULES: List[PowerRule] = (
    _DOMAIN_SEQUENCING
    + _ISOLATION_RULES
    + _LEVEL_SHIFTER_RULES
    + _DVFS_RULES
    + _XPROP_RULES
    + _INTENT_RULES
)


def get_all_power_rules() -> List[PowerRule]:
    """Return all power-aware verification rules."""
    return list(POWER_RULES)


def get_power_rules_by_concept(concept_keyword: str) -> List[PowerRule]:
    """Return rules related to a specific power concept."""
    kw = concept_keyword.lower()
    return [r for r in POWER_RULES if kw in r.power_concept.lower()]


def match_power_rules_by_signals(signal_names: List[str]) -> List[PowerRule]:
    """Return power rules whose trigger signals match any given signal name."""
    lower_signals = {s.lower() for s in signal_names}
    results = []
    for r in POWER_RULES:
        for pattern in r.trigger_signals:
            if any(pattern.lower() in sig for sig in lower_signals):
                results.append(r)
                break
    return results
