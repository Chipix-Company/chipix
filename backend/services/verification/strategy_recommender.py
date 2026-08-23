"""
Verification Strategy Recommender.

Analyzes the mental model and recommends which verification types to run.
The recommendation is a SUGGESTION — the user makes the final decision.

Decision Logic:
  - UnitSim: ALWAYS recommended (fast, basic sanity)
  - Formal:  Recommended if FSMs, protocols, or assertions detected
  - UVM:     Recommended if design is complex (CDC, many interfaces, deep hierarchy)

Several flags may be true at once on non-trivial RTL (e.g. FIFO with inferred protocols):
that is intentional — each row explains a different verification angle. The UI should
highlight “recommended” per-row (badge), not imply only one strategy applies.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List

logger = logging.getLogger(__name__)


@dataclass
class VerificationRecommendation:
    """A single verification type recommendation."""
    type: str             # "unitsim" | "formal" | "uvm"
    label: str            # "Unit Simulation"
    recommended: bool     # Should we suggest it?
    reasons: List[str] = field(default_factory=list)   # Why recommended/not
    confidence: str = ""  # "high" | "medium" | "low"


@dataclass
class StrategyRecommendation:
    """Complete verification strategy recommendation for a design."""
    recommendations: List[VerificationRecommendation] = field(default_factory=list)
    summary: str = ""
    design_complexity: str = ""  # "simple" | "medium" | "complex"

    @property
    def recommended_types(self) -> List[str]:
        return [r.type for r in self.recommendations if r.recommended]

    def to_display(self) -> str:
        """Format for showing to the user."""
        lines = [f"**Design Complexity: {self.design_complexity.upper()}**\n"]
        lines.append("Based on your design, I recommend:\n")
        for r in self.recommendations:
            icon = "✅" if r.recommended else "⬜"
            lines.append(f"  {icon} **{r.label}** — {', '.join(r.reasons)}")
        lines.append("")
        lines.append("You can run any combination — just tell me which ones you'd like.")
        return "\n".join(lines)

    def to_dict(self) -> Dict:
        return {
            "design_complexity": self.design_complexity,
            "recommendations": [
                {
                    "type": r.type,
                    "label": r.label,
                    "recommended": r.recommended,
                    "reasons": r.reasons,
                    "confidence": r.confidence,
                }
                for r in self.recommendations
            ],
            "recommended_types": self.recommended_types,
            "summary": self.summary,
        }


def recommend_verification_strategy(model: Any) -> StrategyRecommendation:
    """
    Analyze a MentalModelSchema and recommend verification types.

    Args:
        model: MentalModelSchema instance (or dict with .design)

    Returns:
        StrategyRecommendation with suggestions for each type
    """
    # Support both attribute-based (dataclass/SimpleNamespace) and dict-based models
    if isinstance(model, dict):
        _design = model.get("design", model)
        _model = model
    else:
        _design = model.design if hasattr(model, "design") else model
        _model = model

    def _get(obj, key, default=None):
        """Get a value from an object or dict."""
        if isinstance(obj, dict):
            return obj.get(key, default)
        return getattr(obj, key, default)

    def _len(obj, key, default=0):
        """Get length of a list attribute from object or dict."""
        val = _get(obj, key)
        if val is None:
            return default
        if isinstance(val, (list, tuple)):
            return len(val)
        return default

    # ── Extract design features ──────────────────────────────────
    num_ports = _len(_design, "ports")
    num_fsms = _len(_design, "fsms")
    num_protocols = _len(_design, "protocols")
    num_modules = _len(_design, "modules")
    num_clocks = _len(_design, "clock_domains")
    has_cdc = bool(_get(_design, "has_cdc_crossings", False))
    num_reqs = _len(_model, "requirements")
    num_arb = _len(_design, "arbitration_policies")
    total_lines = _get(_design, "total_lines", 0) or 0

    # ── Determine complexity ─────────────────────────────────────
    complexity_score = 0
    complexity_score += min(num_ports // 5, 3)       # Up to 3 for many ports
    complexity_score += min(num_fsms * 2, 4)          # FSMs add complexity
    complexity_score += min(num_protocols * 2, 4)     # Protocols add complexity
    complexity_score += min(num_modules // 3, 3)      # Deep hierarchy
    complexity_score += 3 if has_cdc else 0           # CDC is always complex
    complexity_score += min(num_clocks - 1, 2) if num_clocks > 1 else 0  # Multi-clock
    complexity_score += min(num_arb * 2, 3)           # Arbitration
    complexity_score += 1 if total_lines > 500 else 0

    if complexity_score <= 3:
        complexity = "simple"
    elif complexity_score <= 8:
        complexity = "medium"
    else:
        complexity = "complex"

    # ── UnitSim — Always recommended ─────────────────────────────
    unitsim_reasons = ["Fast functional sanity check", "Always recommended"]
    unitsim = VerificationRecommendation(
        type="unitsim",
        label="Unit Simulation",
        recommended=True,
        reasons=unitsim_reasons,
        confidence="high",
    )

    # ── Formal — Recommended if FSMs, protocols, or properties ──
    formal_reasons = []
    formal_recommended = False

    if num_fsms > 0:
        formal_reasons.append(f"{num_fsms} FSM(s) detected — formal can prove state reachability")
        formal_recommended = True
    if num_protocols > 0:
        formal_reasons.append(f"{num_protocols} protocol(s) — formal verifies handshake compliance")
        formal_recommended = True
    if has_cdc:
        formal_reasons.append("CDC crossings — formal can check synchronizer properties")
        formal_recommended = True
    if not formal_recommended:
        formal_reasons.append("No FSMs or protocols detected — formal is optional")

    formal = VerificationRecommendation(
        type="formal",
        label="Formal Verification",
        recommended=formal_recommended,
        reasons=formal_reasons,
        confidence="high" if num_fsms > 0 else "medium",
    )

    # ── UVM — Recommended for complex designs ────────────────────
    uvm_reasons = []
    uvm_recommended = False

    if complexity == "complex":
        uvm_reasons.append("Complex design — UVM provides constrained random stimulus")
        uvm_recommended = True
    if num_protocols > 1:
        uvm_reasons.append(f"{num_protocols} interfaces — UVM agents can drive each independently")
        uvm_recommended = True
    if has_cdc:
        uvm_reasons.append("CDC crossings — UVM provides async driver synchronization")
        uvm_recommended = True
    if num_arb > 0:
        uvm_reasons.append(f"Arbitration logic — UVM can generate multi-master traffic")
        uvm_recommended = True
    if num_reqs > 10:
        uvm_reasons.append(f"{num_reqs} requirements — UVM coverage model tracks completeness")
        uvm_recommended = True
    if not uvm_recommended:
        uvm_reasons.append("Design is simple enough for UnitSim — UVM is optional")

    uvm = VerificationRecommendation(
        type="uvm",
        label="UVM Environment",
        recommended=uvm_recommended,
        reasons=uvm_reasons,
        confidence="high" if complexity == "complex" else "low",
    )

    # ── Build summary ────────────────────────────────────────────
    rec_types = [r.label for r in [unitsim, formal, uvm] if r.recommended]
    summary = f"{complexity.capitalize()} design ({num_ports} ports, {num_fsms} FSMs, {num_protocols} protocols). Suggesting: {', '.join(rec_types)}."

    strategy = StrategyRecommendation(
        recommendations=[unitsim, formal, uvm],
        summary=summary,
        design_complexity=complexity,
    )

    logger.info("Verification strategy: %s", summary)
    return strategy
