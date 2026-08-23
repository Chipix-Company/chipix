"""
Mental Model Service — Persistent Design-Intent Knowledge Base.

This package implements the ChipStack-style MentalModel: a structured,
source-grounded representation of design intent that serves as the
canonical context for all downstream verification agents.

Modules:
    schema   — Dataclasses and JSON schema for the mental model
    builder  — Construct/update a mental model from RTL + spec
    store    — DB persistence (create, query, freshness checks)
    validator — Validate model fields against the RTL symbol table
"""

from services.mental_model.schema import (
    MentalModelSchema,
    DesignBlock,
    PortInfo,
    ParameterInfo,
    ClockDomain,
    FSMDescription,
    ProtocolBinding,
    Requirement,
    SourceReference,
    VerificationIntent,
    UnitTestIntent,
    FormalPropertyIntent,
    UVMScenarioIntent,
    CoveragePointIntent,
    ModelEvidence,
    OpenQuestion,
)

from services.mental_model.store import (
    create_llm_enriched_mental_model_revision,
    create_mental_model_revision,
    evaluate_mental_model_freshness,
    get_latest_mental_model_revision,
    next_mental_model_revision,
    ensure_fresh_mental_model_revision,
)

__all__ = [
    # Schema types
    "MentalModelSchema",
    "DesignBlock",
    "PortInfo",
    "ParameterInfo",
    "ClockDomain",
    "FSMDescription",
    "ProtocolBinding",
    "Requirement",
    "SourceReference",
    "VerificationIntent",
    "UnitTestIntent",
    "FormalPropertyIntent",
    "UVMScenarioIntent",
    "CoveragePointIntent",
    "ModelEvidence",
    "OpenQuestion",
    # Store functions
    "create_llm_enriched_mental_model_revision",
    "create_mental_model_revision",
    "evaluate_mental_model_freshness",
    "get_latest_mental_model_revision",
    "next_mental_model_revision",
    "ensure_fresh_mental_model_revision",
]
