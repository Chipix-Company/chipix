"""UVM generation policy derived from the persisted mental model.

The generator should always emit a valid UVM skeleton when the user chooses
UVM. This module decides how much content and optional infrastructure to add
without letting small designs explode into many files.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List


@dataclass
class DesignProfile:
    """Complexity signals extracted from the mental model."""

    port_count: int = 0
    wide_port_count: int = 0
    fsm_count: int = 0
    fsm_total_states: int = 0
    protocol_count: int = 0
    register_field_count: int = 0
    clock_domain_count: int = 0
    has_cdc: bool = False
    existing_assertion_count: int = 0
    requirement_count: int = 0
    sub_instance_count: int = 0
    total_lines: int = 0

    has_axi: bool = False
    has_ahb: bool = False
    has_apb: bool = False
    has_valid_ready: bool = False

    complexity_score: int = 0
    tier: str = "standard"
    independent_interfaces: int = 1
    needs_multi_agent: bool = False


@dataclass
class ContentLimits:
    """Controls how much content goes inside mandatory UVM files."""

    max_sequence_classes: int = 6
    max_test_classes: int = 4
    max_scoreboard_checks: int = 10
    max_coverage_bins: int = 8
    max_requirements: int = 8
    deduplicate_scenarios: bool = True


@dataclass
class UVMGenerationPlan:
    """Generation manifest used by the UVM generator and staged preview."""

    tier: str
    complexity_score: int
    profile: DesignProfile
    mandatory_files: List[str] = field(default_factory=list)
    optional_files: List[str] = field(default_factory=list)
    content_limits: ContentLimits = field(default_factory=ContentLimits)
    multi_agent: bool = False
    agent_names: List[str] = field(default_factory=list)
    rationale: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


MANDATORY_SINGLE_AGENT_ROLES = [
    "pkg",
    "interface",
    "seq_item",
    "driver",
    "monitor",
    "sequencer",
    "agent",
    "scoreboard",
    "env",
    "coverage",
    "seq_lib",
    "tests",
    "tb_top",
    "filelist",
]


def compute_design_profile(model: Any) -> DesignProfile:
    """Read the mental model and compute a deterministic UVM complexity profile."""

    content = _as_model_content(model)
    design = _get_design(content)
    profile = DesignProfile()

    ports = _list(_get(design, "ports", []))
    profile.port_count = len(ports)
    profile.wide_port_count = sum(1 for port in ports if _port_width(port) > 1)

    fsms = _list(_get(design, "fsms", []))
    profile.fsm_count = len(fsms)
    profile.fsm_total_states = sum(
        len(_list(_get(fsm, "states", [])))
        for fsm in fsms
    )

    protocols = _list(_get(design, "protocols", []))
    profile.protocol_count = len(protocols)
    for protocol in protocols:
        name = _protocol_name(protocol)
        if "axi" in name:
            profile.has_axi = True
        if "ahb" in name:
            profile.has_ahb = True
        if "apb" in name:
            profile.has_apb = True
        if "valid" in name or "ready" in name:
            profile.has_valid_ready = True

    profile.register_field_count = len(
        _list(_get(design, "register_fields", []))
        or _list(_get(design, "register_map", []))
    )
    profile.clock_domain_count = len(_list(_get(design, "clock_domains", [])))
    profile.has_cdc = bool(
        _get(design, "has_cdc_crossings", False)
        or _get(design, "cdc_crossings", [])
    )
    profile.existing_assertion_count = len(_list(_get(design, "existing_assertions", [])))
    profile.requirement_count = len(_list(_get(content, "requirements", [])))
    profile.sub_instance_count = len(_list(_get(design, "sub_instances", [])))
    profile.total_lines = _safe_int(_get(design, "total_lines", 0), 0)

    score = 0
    score += min(15, profile.port_count)
    score += min(10, profile.wide_port_count)
    score += min(15, profile.fsm_total_states * 2)
    score += min(15, profile.protocol_count * 5)
    score += min(10, profile.register_field_count * 2)
    score += min(10, max(0, profile.clock_domain_count - 1) * 5)
    score += min(10, profile.sub_instance_count * 3)
    score += min(10, profile.total_lines // 300)
    score += 10 if profile.has_cdc else 0
    score += 5 if profile.has_axi else 0
    score += 4 if profile.has_ahb else 0
    profile.complexity_score = min(100, score)

    if profile.complexity_score < 30:
        profile.tier = "standard"
    elif profile.complexity_score < 65:
        profile.tier = "full"
    else:
        profile.tier = "enterprise"

    profile.independent_interfaces = _count_independent_interfaces(content, profile, protocols)
    explicit_agent_count = _count_explicit_uvm_agents(content)
    if explicit_agent_count >= 2:
        profile.independent_interfaces = max(profile.independent_interfaces, explicit_agent_count)
    profile.needs_multi_agent = (
        profile.independent_interfaces >= 2
        and (
            profile.port_count >= 12
            or profile.protocol_count >= 2
            or profile.tier in {"full", "enterprise"}
            or profile.has_axi
            or profile.has_ahb
            or profile.has_apb
            or explicit_agent_count >= 2
        )
    )

    return profile


def plan_uvm_generation(profile: DesignProfile, model: Any) -> UVMGenerationPlan:
    """Build the UVM generation manifest from a computed profile."""

    plan = UVMGenerationPlan(
        tier=profile.tier,
        complexity_score=profile.complexity_score,
        profile=profile,
        mandatory_files=list(MANDATORY_SINGLE_AGENT_ROLES),
        multi_agent=profile.needs_multi_agent,
    )

    if profile.tier == "standard":
        plan.content_limits = ContentLimits(
            max_sequence_classes=5,
            max_test_classes=3,
            max_scoreboard_checks=8,
            max_coverage_bins=6,
            max_requirements=6,
            deduplicate_scenarios=True,
        )
    elif profile.tier == "full":
        plan.content_limits = ContentLimits(
            max_sequence_classes=10,
            max_test_classes=6,
            max_scoreboard_checks=15,
            max_coverage_bins=12,
            max_requirements=15,
            deduplicate_scenarios=True,
        )
    else:
        plan.content_limits = ContentLimits(
            max_sequence_classes=15,
            max_test_classes=10,
            max_scoreboard_checks=25,
            max_coverage_bins=20,
            max_requirements=25,
            deduplicate_scenarios=False,
        )

    _raise_limits_for_approved_uvm_content(plan, model)

    if (
        profile.has_axi
        or profile.has_ahb
        or profile.has_apb
        or profile.has_cdc
        or profile.existing_assertion_count > 0
        or profile.tier in {"full", "enterprise"}
    ):
        plan.optional_files.append("assertions")

    if profile.register_field_count > 0:
        plan.optional_files.append("ral")

    # Always emit a build helper as support collateral. It is not part of the
    # mandatory UVM role set, but every generated environment should be runnable.
    plan.optional_files.append("makefile")

    if profile.needs_multi_agent:
        plan.agent_names = _derive_agent_names(model)
        plan.optional_files.extend([
            "env_config",
            "virtual_sequencer",
            "virtual_sequence",
        ])
        plan.rationale.append(
            f"{profile.independent_interfaces} independent interfaces require separate UVM agents."
        )
    else:
        plan.agent_names = ["dut_agent"]
        plan.rationale.append(
            "Single DUT agent selected; design is small or has one physical verification interface."
        )

    if profile.tier == "enterprise":
        plan.optional_files.append("test_plan_doc")

    plan.rationale.append(
        f"Tier {profile.tier} selected from complexity score {profile.complexity_score}."
    )
    return plan


def _raise_limits_for_approved_uvm_content(
    plan: UVMGenerationPlan,
    model: Any,
) -> None:
    """Do not let the default tier hide user-approved plan content.

    The staged plan is the baseline contract. If a user refines the plan to add
    more sequences/checks/coverage goals, generation should include them instead
    of silently truncating back to the small-design policy.
    """
    verification = _get(model, "verification", {})
    scenarios = len(_list(_get(verification, "uvm_scenarios", [])))
    checks = len(_list(_get(verification, "uvm_scoreboard_checks", [])))
    coverage = len(_list(_get(verification, "uvm_coverage_points", [])))

    if scenarios:
        # +1 reserves room for the generated base sequence.
        plan.content_limits.max_sequence_classes = max(
            plan.content_limits.max_sequence_classes,
            min(24, scenarios + 1),
        )
        plan.content_limits.max_test_classes = max(
            plan.content_limits.max_test_classes,
            min(16, scenarios),
        )
    if checks:
        plan.content_limits.max_scoreboard_checks = max(
            plan.content_limits.max_scoreboard_checks,
            min(32, checks),
        )
    if coverage:
        plan.content_limits.max_coverage_bins = max(
            plan.content_limits.max_coverage_bins,
            min(32, coverage),
        )


def planned_uvm_files(top: str, plan: UVMGenerationPlan) -> List[str]:
    """Return the expected generated file names for UI preview."""

    top = _safe_name(top or "dut")
    if plan.multi_agent:
        files: List[str] = []
        for raw_agent in plan.agent_names or ["dut_agent"]:
            base = _agent_base(raw_agent)
            files.extend([
                f"{base}_intf.sv",
                f"{base}_sequence_item.sv",
                f"{base}_sequencer.sv",
                f"{base}_sequence.sv",
                f"{base}_driver.sv",
                f"{base}_monitor.sv",
                f"{base}_agent.sv",
            ])
        files.extend([
            f"{top}_pkg.sv",
            f"{top}_scoreboard.sv",
            f"{top}_coverage.sv",
            f"{top}_env_config.sv",
            f"{top}_virtual_sequencer.sv",
            f"{top}_virtual_sequence.sv",
            f"{top}_env.sv",
            f"{top}_tests.sv",
            "top_tb.sv",
            f"{top}_uvm.f",
        ])
    else:
        files = [
            f"{top}_pkg.sv",
            f"{top}_if.sv",
            f"{top}_seq_item.sv",
            f"{top}_driver.sv",
            f"{top}_monitor.sv",
            f"{top}_sequencer.sv",
            f"{top}_agent.sv",
            f"{top}_scoreboard.sv",
            f"{top}_env.sv",
            f"{top}_coverage.sv",
            f"{top}_seq_lib.sv",
            f"{top}_tests.sv",
            "top_tb.sv",
            f"{top}_uvm.f",
        ]

    if "makefile" in plan.optional_files:
        files.append("Makefile")
    if "assertions" in plan.optional_files:
        files.append(f"{top}_assertions.sv")
    if "ral" in plan.optional_files:
        files.append(f"{top}_ral.sv")
    if "test_plan_doc" in plan.optional_files:
        files.append("test_plan.md")
    return _unique(files)


def _as_model_content(model: Any) -> Any:
    if isinstance(model, dict):
        if isinstance(model.get("content"), dict):
            return model["content"]
        if isinstance(model.get("mental_model"), dict):
            nested = model["mental_model"].get("content")
            if isinstance(nested, dict):
                return nested
        return model
    return model


def _get_design(model: Any) -> Any:
    return _get(model, "design", model if isinstance(model, dict) else {})


def _get(obj: Any, key: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _list(value: Any) -> List[Any]:
    return value if isinstance(value, list) else []


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _port_width(port: Any) -> int:
    width = _safe_int(_get(port, "width", 1), 1)
    return max(1, width)


def _protocol_name(protocol: Any) -> str:
    return str(
        _get(protocol, "protocol", "")
        or _get(protocol, "name", "")
        or _get(protocol, "interface", "")
    ).strip().lower()


def _count_independent_interfaces(
    model: Any,
    profile: DesignProfile,
    protocols: List[Any],
) -> int:
    families = {
        _protocol_family(_protocol_name(protocol))
        for protocol in protocols
        if _protocol_family(_protocol_name(protocol))
    }
    if families:
        return max(1, len(families))

    design = _get_design(model)
    port_families = _infer_interface_families_from_ports(_list(_get(design, "ports", [])))
    if len(port_families) >= 2:
        return len(port_families)

    clock_domains = _list(_get(design, "clock_domains", []))
    if len(clock_domains) >= 2 and profile.port_count >= 20:
        return len(clock_domains)

    explicit_agent_count = _count_explicit_uvm_agents(model)
    if explicit_agent_count >= 2:
        return explicit_agent_count

    return 1


def _count_explicit_uvm_agents(model: Any) -> int:
    verification = _get(model, "verification", {})
    agents = _list(_get(verification, "uvm_agents", []))
    real_agents = [
        agent for agent in agents
        if not _is_generic_agent_name(str(_get(agent, "name", "")))
    ]
    return len(real_agents)


def _infer_interface_families_from_ports(ports: List[Any]) -> set[str]:
    """Infer independent bus/interface families when protocol metadata is thin."""
    families: set[str] = set()
    prefix_counts: dict[str, int] = {}
    ahb_signals = {"haddr", "htrans", "hwrite", "hwdata", "hrdata", "hready", "hresp", "hsel"}
    apb_signals = {"paddr", "psel", "penable", "pwrite", "pwdata", "prdata", "pready", "pslverr"}
    tlul_signals = {
        "avalid", "aready", "aopcode", "aparam", "asize", "asource", "aaddress",
        "amask", "adata", "dvalid", "dready", "dopcode", "dparam", "dsize",
        "dsource", "dsink", "ddata", "derror",
    }

    for port in ports:
        name = str(_get(port, "name", "") or "").lower()
        if not name:
            continue
        compact = name.replace("_", "")
        if name.startswith(("axi_", "m_axi", "s_axi")) or compact.startswith(("aw", "ar")):
            families.add("axi")
        if name in ahb_signals or compact in ahb_signals or name.startswith(("ahb_", "h")) and any(sig in compact for sig in ahb_signals):
            families.add("ahb")
        if name in apb_signals or compact in apb_signals or name.startswith(("apb_", "p")) and any(sig in compact for sig in apb_signals):
            families.add("apb")
        if compact in tlul_signals or name.startswith(("tl_", "tlul_", "tilelink_")) or any(sig in compact for sig in tlul_signals):
            families.add("tlul")

        parts = [part for part in name.split("_") if part]
        if len(parts) >= 2 and parts[0] not in {"clk", "rst", "reset", "data"}:
            prefix_counts[parts[0]] = prefix_counts.get(parts[0], 0) + 1
            if len(parts) >= 3:
                prefix = "_".join(parts[:2])
                prefix_counts[prefix] = prefix_counts.get(prefix, 0) + 1

    for prefix, count in prefix_counts.items():
        if count >= 3:
            families.add(prefix)

    return families


def _protocol_family(name: str) -> str:
    lowered = (name or "").lower()
    if "axi" in lowered:
        return "axi"
    if "ahb" in lowered:
        return "ahb"
    if "apb" in lowered:
        return "apb"
    compact = lowered.replace("_", "").replace("-", "")
    if "tilelink" in lowered or "tlul" in compact or "tlul" in lowered:
        return "tlul"
    if "spi" in lowered:
        return "spi"
    if "i2c" in lowered or "iic" in lowered:
        return "i2c"
    if "uart" in lowered:
        return "uart"
    if "valid" in lowered and "ready" in lowered:
        return "valid_ready"
    return lowered.split("_", 1)[0].split("-", 1)[0]


def _derive_agent_names(model: Any) -> List[str]:
    content = _as_model_content(model)
    design = _get_design(content)
    names: List[str] = []

    verification = _get(content, "verification", {})
    for agent in _list(_get(verification, "uvm_agents", [])):
        name = str(_get(agent, "name", "") or "")
        if name and not _is_generic_agent_name(name):
            names.append(name)

    for protocol in _list(_get(design, "protocols", [])):
        name = _protocol_name(protocol)
        family = _protocol_family(name)
        if family:
            names.append(f"{family}_agent")

    return _unique(names) or ["dut_agent"]


def _is_generic_agent_name(name: str) -> bool:
    lowered = name.strip().lower()
    return lowered in {
        "",
        "input_agent",
        "output_agent",
        "dut_agent",
        "misc_agent",
    }


def _safe_name(value: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch == "_" else "_" for ch in str(value or ""))
    cleaned = cleaned.strip("_")
    return cleaned or "dut"


def _agent_base(name: str) -> str:
    base = _safe_name(name.lower())
    return base[:-6] if base.endswith("_agent") else base


def _unique(items: List[str]) -> List[str]:
    seen: set[str] = set()
    result: List[str] = []
    for item in items:
        if not item or item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result
