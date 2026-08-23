"""
Mental Model Validator — Anti-hallucination validation layer.

Validates every signal name, module name, and port reference in the
mental model against the actual RTL symbol table. This ensures the
LLM hasn't invented signals that don't exist.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Set

from services.mental_model.schema import (
    MentalModelSchema,
    VerificationIntent,
)

logger = logging.getLogger(__name__)


@dataclass
class ValidationResult:
    """Result of validating a mental model."""
    valid: bool = True
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    hallucinated_signals: List[str] = field(default_factory=list)
    stats: Dict[str, int] = field(default_factory=dict)

    @property
    def summary(self) -> str:
        status = "VALID" if self.valid else "INVALID"
        return (
            f"{status}: {len(self.errors)} errors, "
            f"{len(self.warnings)} warnings, "
            f"{len(self.hallucinated_signals)} hallucinated signals"
        )


def validate_mental_model(model: MentalModelSchema) -> ValidationResult:
    """
    Validate the entire mental model against its own symbol table.

    Checks:
    1. All signal names in verification intent exist in symbol table
    2. All module names in hierarchy exist in module index
    3. Required fields are present
    4. Source references are non-empty
    """
    result = ValidationResult()
    all_symbols = _flatten_symbol_table(model.symbol_table)
    all_modules = set(model.design.modules) | set(model.symbol_table.keys())

    # Check required fields
    if not model.design.top_module:
        result.errors.append("Missing top_module in design block")
        result.valid = False

    if not model.design.ports:
        result.warnings.append("Design has no ports defined")

    if not model.requirements:
        result.warnings.append("No requirements found — verification may be incomplete")

    # Validate formal properties reference real signals
    for fp in model.verification.formal_properties:
        for sig in fp.related_signals:
            if sig and sig not in all_symbols:
                result.hallucinated_signals.append(sig)
                result.warnings.append(
                    f"Formal property '{fp.name}' references unknown signal '{sig}'"
                )

    # Validate coverage points reference real signals
    for cp in model.verification.coverage_points:
        if cp.signal and cp.signal not in all_symbols:
            result.hallucinated_signals.append(cp.signal)
            result.warnings.append(
                f"Coverage point '{cp.name}' references unknown signal '{cp.signal}'"
            )

    # Validate sub-instance module names exist
    for inst in model.design.sub_instances:
        if inst.module_name not in all_modules:
            result.warnings.append(
                f"Instance '{inst.instance_name}' references unknown module "
                f"'{inst.module_name}' (may be external IP)"
            )

    # Validate hierarchy tree consistency
    for parent, children in model.design.hierarchy_tree.items():
        if parent not in all_modules:
            result.warnings.append(f"Hierarchy parent '{parent}' not in module list")
        for child in children:
            if child not in all_modules:
                result.warnings.append(
                    f"Hierarchy child '{child}' of '{parent}' not in module list"
                )

    # Check for source references on requirements
    reqs_without_source = 0
    for req in model.requirements:
        if not req.spec_ref.file and not req.rtl_refs:
            reqs_without_source += 1
    if reqs_without_source > 0:
        result.warnings.append(
            f"{reqs_without_source} requirement(s) have no source reference"
        )

    # Compute stats
    result.stats = {
        "total_modules": len(all_modules),
        "total_ports": len(model.design.ports),
        "total_symbols": len(all_symbols),
        "total_requirements": len(model.requirements),
        "total_unit_tests": len(model.verification.unit_tests),
        "total_formal_properties": len(model.verification.formal_properties),
        "total_coverage_points": len(model.verification.coverage_points),
        "total_open_questions": len(model.open_questions),
        "hallucinated_signals": len(result.hallucinated_signals),
    }

    if result.hallucinated_signals:
        result.valid = False

    logger.info(f"Validation: {result.summary}")
    return result


def _flatten_symbol_table(symbol_table: Dict[str, List[str]]) -> Set[str]:
    """Flatten {module: [signals]} into a flat set of all signal names."""
    symbols: Set[str] = set()
    for module_signals in symbol_table.values():
        symbols.update(module_signals)
    return symbols
