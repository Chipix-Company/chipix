"""
Knowledge Base for Chip Verification Corner Cases
==================================================

A curated collection of corner case templates, protocol compliance rules,
signal-inference rules, silicon errata, formal properties, functional safety
rules, power-aware verification rules, and open-source VPlan testpoints.

**Rule Sources:**

1. **Official Protocol Specifications** — ARM AMBA, NXP I2C, IEEE JTAG,
   Motorola SPI, 16550 UART, OpenCores Wishbone
2. **Open-Source Verification Plans** — OpenTitan (Google/lowRISC),
   PULP Platform (ETH Zurich), CHIPS Alliance (Caliptra)
3. **Industry Best Practices** — DVCON, Accellera UVM, Cummings SNUG
4. **Silicon Errata** — ARM Cortex, Intel x86, RISC-V cores, OpenTitan bugs
5. **MITRE CWE** — Hardware weakness enumeration
6. **Formal Property Libraries** — OVL, ABV, SymbiYosys patterns
7. **Safety Standards** — ISO 26262, IEC 61508
8. **Power-Aware Verification** — IEEE 1801 UPF, CPF methodology

Every rule is traceable to its source via ``provenance`` / ``spec_ref`` fields.

**Module Summary:**

=========================== ======= =======================================
Module                      Rules   Description
=========================== ======= =======================================
protocol_rules               75     Protocol compliance (AXI, APB, SPI…)
category_rules              140     Corner cases by design category
signal_rules                 58     Signal-pattern inference rules
errata_rules                 60     Silicon errata & CWE patterns
safety_rules                 24     ISO 26262 / IEC 61508 safety rules
power_rules                  26     UPF/CPF power-aware rules
formal_property_rules        40     OVL-derived formal SVA patterns
vplan_rules                  58     Open-source VPlan testpoints
=========================== ======= =======================================
**Total: 481 rules**

Usage
-----
::

    from services.mental_model.knowledge_base import (
        # Protocol compliance
        get_rules_for_protocol, get_p0_rules, get_all_rules,
        # Category corner cases
        get_templates_for_category, get_universal_templates,
        get_templates_for_trait, get_all_templates,
        # Signal inference
        match_signal_rules, get_all_signal_rules,
        # Silicon errata
        get_all_errata, get_errata_for_category, match_errata_by_signals,
        # Functional safety
        get_all_safety_rules, get_safety_rules_by_asil,
        # Power-aware
        get_all_power_rules, match_power_rules_by_signals,
        # Formal properties
        get_all_formal_properties, match_formal_properties,
        # VPlan testpoints
        get_all_vplan_rules, get_vplan_rules_for_category,
    )
"""

# ─── Protocol Rules ──────────────────────────────────────────────────
from services.mental_model.knowledge_base.protocol_rules import (
    ProtocolRule,
    PROTOCOL_RULES,
    get_rules_for_protocol,
    get_all_rules,
    get_p0_rules,
)

# ─── Category Corner Cases ───────────────────────────────────────────
from services.mental_model.knowledge_base.category_rules import (
    CornerCaseTemplate,
    get_templates_for_category,
    get_universal_templates,
    get_templates_for_trait,
    get_all_templates,
    ALL_TEMPLATES,
    UNIVERSAL_TEMPLATES,
    STORAGE_TEMPLATES,
    PROTOCOL_BRIDGE_TEMPLATES,
    PROCESSOR_CORE_TEMPLATES,
    PERIPHERAL_TEMPLATES,
    CRYPTO_SECURITY_TEMPLATES,
    DMA_DATA_MOVER_TEMPLATES,
    BUS_INTERCONNECT_TEMPLATES,
    MEMORY_CONTROLLER_TEMPLATES,
    DSP_DATAPATH_TEMPLATES,
    CLOCK_RESET_PMU_TEMPLATES,
    ARBITER_SCHEDULER_TEMPLATES,
    TEST_DEBUG_TEMPLATES,
    NETWORK_PACKET_TEMPLATES,
    CDC_TEMPLATES,
)

# ─── Signal Inference ────────────────────────────────────────────────
from services.mental_model.knowledge_base.signal_rules import (
    SignalInferenceRule,
    SIGNAL_RULES,
    get_all_signal_rules,
    match_signal_rules,
)

# ─── Silicon Errata & CWE ───────────────────────────────────────────
from services.mental_model.knowledge_base.errata_rules import (
    ErrataRule,
    ERRATA_RULES,
    get_all_errata,
    get_errata_by_source,
    get_errata_for_category,
    match_errata_by_signals,
)

# ─── Functional Safety ──────────────────────────────────────────────
from services.mental_model.knowledge_base.safety_rules import (
    SafetyRule,
    SAFETY_RULES,
    get_all_safety_rules,
    get_safety_rules_by_asil,
    get_safety_rules_by_mechanism,
)

# ─── Power-Aware Verification ───────────────────────────────────────
from services.mental_model.knowledge_base.power_rules import (
    PowerRule,
    POWER_RULES,
    get_all_power_rules,
    get_power_rules_by_concept,
    match_power_rules_by_signals,
)

# ─── Formal Property Library ────────────────────────────────────────
from services.mental_model.knowledge_base.formal_property_rules import (
    FormalPropertyRule,
    FORMAL_PROPERTY_RULES,
    get_all_formal_properties,
    get_formal_properties_by_class,
    match_formal_properties,
)

# ─── Open-Source VPlan Testpoints ────────────────────────────────────
from services.mental_model.knowledge_base.vplan_rules import (
    VPlanRule,
    VPLAN_RULES,
    get_all_vplan_rules,
    get_vplan_rules_by_project,
    get_vplan_rules_for_category,
)


__all__ = [
    # Protocol rules
    "ProtocolRule", "PROTOCOL_RULES",
    "get_rules_for_protocol", "get_all_rules", "get_p0_rules",
    # Category corner cases
    "CornerCaseTemplate",
    "get_templates_for_category", "get_universal_templates",
    "get_templates_for_trait", "get_all_templates",
    "ALL_TEMPLATES", "UNIVERSAL_TEMPLATES",
    "STORAGE_TEMPLATES", "PROTOCOL_BRIDGE_TEMPLATES",
    "PROCESSOR_CORE_TEMPLATES", "PERIPHERAL_TEMPLATES",
    "CRYPTO_SECURITY_TEMPLATES", "DMA_DATA_MOVER_TEMPLATES",
    "BUS_INTERCONNECT_TEMPLATES", "MEMORY_CONTROLLER_TEMPLATES",
    "DSP_DATAPATH_TEMPLATES", "CLOCK_RESET_PMU_TEMPLATES",
    "ARBITER_SCHEDULER_TEMPLATES", "TEST_DEBUG_TEMPLATES",
    "NETWORK_PACKET_TEMPLATES", "CDC_TEMPLATES",
    # Signal inference
    "SignalInferenceRule", "SIGNAL_RULES",
    "get_all_signal_rules", "match_signal_rules",
    # Silicon errata
    "ErrataRule", "ERRATA_RULES",
    "get_all_errata", "get_errata_by_source",
    "get_errata_for_category", "match_errata_by_signals",
    # Functional safety
    "SafetyRule", "SAFETY_RULES",
    "get_all_safety_rules", "get_safety_rules_by_asil",
    "get_safety_rules_by_mechanism",
    # Power-aware
    "PowerRule", "POWER_RULES",
    "get_all_power_rules", "get_power_rules_by_concept",
    "match_power_rules_by_signals",
    # Formal properties
    "FormalPropertyRule", "FORMAL_PROPERTY_RULES",
    "get_all_formal_properties", "get_formal_properties_by_class",
    "match_formal_properties",
    # VPlan testpoints
    "VPlanRule", "VPLAN_RULES",
    "get_all_vplan_rules", "get_vplan_rules_by_project",
    "get_vplan_rules_for_category",
]
