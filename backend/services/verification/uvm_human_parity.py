"""Static human-parity benchmark for generated UVM environments.

The benchmark compares generated UVM source files against a human-authored
reference directory. It is intentionally simulator-free: this v1 answers
"how close does the generated source look to human-level UVM?" rather than
"does it pass a commercial UVM simulation?".
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable

from services.verification.uvm_validator import validate_uvm_environment


SV_EXTENSIONS = {".sv", ".svh", ".v", ".vh"}
UVM_EXTENSIONS = SV_EXTENSIONS | {".f", ".flist"}

DEFAULT_REQUIRED_ROLES = [
    "package",
    "interface",
    "seq_item",
    "sequencer",
    "driver",
    "monitor",
    "agent",
    "env",
    "scoreboard",
    "coverage",
    "tests",
    "top",
    "filelist",
]

SCORE_WEIGHTS = {
    "file_completeness": 15,
    "interface_fidelity": 15,
    "uvm_architecture": 15,
    "driver_monitor_quality": 15,
    "scoreboard_quality": 15,
    "coverage_intent": 10,
    "sequence_quality": 10,
    "maintainability": 5,
}


@dataclass
class BenchmarkCase:
    id: str
    top_module: str
    spec_file: str = ""
    rtl_files: list[str] = field(default_factory=list)
    human_uvm_dir: str = "human_uvm"
    generated_uvm_dir: str = "generated_uvm"
    clock: str = "clk"
    reset: dict[str, str] = field(default_factory=dict)
    required_roles: list[str] = field(default_factory=lambda: list(DEFAULT_REQUIRED_ROLES))
    case_dir: str = ""
    functional_points_file: str = "functional_points.json"

    def resolve_path(self, value: str) -> Path:
        path = Path(value)
        if path.is_absolute():
            return path
        base = Path(self.case_dir) if self.case_dir else Path.cwd()
        return (base / path).resolve()

    @property
    def human_dir_path(self) -> Path:
        return self.resolve_path(self.human_uvm_dir)

    @property
    def generated_dir_path(self) -> Path:
        return self.resolve_path(self.generated_uvm_dir)

    @property
    def rtl_file_paths(self) -> list[Path]:
        return [self.resolve_path(path) for path in self.rtl_files]

    @property
    def functional_points_path(self) -> Path:
        return self.resolve_path(self.functional_points_file)


@dataclass
class ScoreCategory:
    name: str
    score: int
    max_score: int
    summary: str = ""


@dataclass
class ParityIssue:
    severity: str
    code: str
    message: str
    file: str = ""
    role: str = ""


@dataclass
class ParityResult:
    case_id: str
    top_module: str
    human_parity_score: int
    categories: list[ScoreCategory]
    issues: list[ParityIssue]
    hard_flags: list[str]
    human_roles: dict[str, list[str]]
    generated_roles: dict[str, list[str]]
    validation: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "top_module": self.top_module,
            "human_parity_score": self.human_parity_score,
            "categories": {item.name: item.score for item in self.categories},
            "category_details": [asdict(item) for item in self.categories],
            "hard_flags": self.hard_flags,
            "issues": [asdict(issue) for issue in self.issues],
            "human_roles": self.human_roles,
            "generated_roles": self.generated_roles,
            "validation": self.validation,
        }


def load_benchmark_case(case_dir: str | Path) -> BenchmarkCase:
    """Load a case from ``benchmark.json`` inside ``case_dir``."""

    root = Path(case_dir).resolve()
    manifest = root / "benchmark.json"
    if not manifest.exists():
        raise FileNotFoundError(f"benchmark.json not found in {root}")
    data = json.loads(manifest.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"benchmark.json must contain an object: {manifest}")
    return _case_from_mapping(data, case_dir=str(root))


def create_direct_case(
    *,
    top_module: str,
    human_uvm_dir: str | Path,
    generated_uvm_dir: str | Path,
    rtl_files: Iterable[str | Path] | None = None,
    spec_file: str | Path | None = None,
    case_id: str | None = None,
) -> BenchmarkCase:
    """Build a case from direct CLI paths instead of a manifest."""

    top = str(top_module or "").strip()
    if not top:
        raise ValueError("--top is required when using direct path mode")
    return BenchmarkCase(
        id=case_id or top,
        top_module=top,
        spec_file=str(spec_file or ""),
        rtl_files=[str(path) for path in (rtl_files or [])],
        human_uvm_dir=str(Path(human_uvm_dir).resolve()),
        generated_uvm_dir=str(Path(generated_uvm_dir).resolve()),
        required_roles=list(DEFAULT_REQUIRED_ROLES),
    )


def evaluate_case(case: BenchmarkCase) -> ParityResult:
    """Compare generated UVM files against human UVM files and score them."""

    issues: list[ParityIssue] = []
    _validate_case_paths(case, issues)

    human_files = _collect_uvm_files(case.human_dir_path)
    generated_files = _collect_uvm_files(case.generated_dir_path)
    human_roles = discover_uvm_roles(case.human_dir_path)
    generated_roles = discover_uvm_roles(case.generated_dir_path)

    if not human_files:
        _add_issue(
            issues,
            "error",
            "human_reference_missing",
            "No human-authored UVM files were found.",
            str(case.human_dir_path),
        )
    if not generated_files:
        _add_issue(
            issues,
            "error",
            "generated_reference_missing",
            "No generated UVM files were found.",
            str(case.generated_dir_path),
        )

    for role in case.required_roles or DEFAULT_REQUIRED_ROLES:
        if role not in generated_roles:
            _add_issue(
                issues,
                "error",
                "missing_required_role",
                f"Generated UVM is missing required role '{role}'.",
                role=role,
            )
        if role in human_roles and role not in generated_roles:
            _add_issue(
                issues,
                "error",
                "missing_human_reference_role",
                f"Human reference includes role '{role}', but generated UVM does not.",
                role=role,
            )

    validation: dict[str, Any] = {}
    if generated_files:
        validation_result = validate_uvm_environment(
            work_dir=case.generated_dir_path,
            generated_files=generated_files,
            top_module=case.top_module,
            dut_rtl_files=case.rtl_file_paths,
        )
        validation = validation_result.to_dict()
        for item in validation_result.issues:
            severity = "error" if item.severity == "error" else "warning"
            _add_issue(
                issues,
                severity,
                f"uvm_validator:{item.code}",
                item.message,
                item.file,
            )

    expected_ports = _expected_ports(case)
    _check_interface_fidelity(case, expected_ports, human_roles, generated_roles, issues)
    _check_placeholders(generated_files, issues)
    _check_scoreboard(generated_roles, issues)
    _check_coverage(case, generated_roles, issues)
    _check_driver_monitor(generated_roles, issues)
    _check_uvm_architecture(generated_files, generated_roles, issues)

    categories = _score_categories(case, issues, human_roles, generated_roles, generated_files)
    total = sum(item.score for item in categories)
    hard_flags = sorted({issue.code for issue in issues if issue.severity == "error"})
    return ParityResult(
        case_id=case.id,
        top_module=case.top_module,
        human_parity_score=total,
        categories=categories,
        issues=issues,
        hard_flags=hard_flags,
        human_roles=_roles_to_strings(human_roles, case.human_dir_path),
        generated_roles=_roles_to_strings(generated_roles, case.generated_dir_path),
        validation=validation,
    )


def write_result_reports(result: ParityResult, out_dir: str | Path) -> None:
    """Write JSON and Markdown reports for a benchmark result."""

    root = Path(out_dir)
    root.mkdir(parents=True, exist_ok=True)
    payload = result.to_dict()
    (root / "scorecard.json").write_text(
        json.dumps(_scorecard_payload(payload), indent=2),
        encoding="utf-8",
    )
    (root / "issues.json").write_text(
        json.dumps(payload["issues"], indent=2),
        encoding="utf-8",
    )
    (root / "comparison_report.md").write_text(
        render_comparison_report(result),
        encoding="utf-8",
    )
    (root / "improvement_plan.md").write_text(
        render_improvement_plan(result),
        encoding="utf-8",
    )


def render_comparison_report(result: ParityResult) -> str:
    lines = [
        f"# UVM Human-Parity Report: {result.case_id}",
        "",
        f"- Top module: `{result.top_module}`",
        f"- Human-parity score: **{result.human_parity_score}/100**",
        "",
        "## Category Scores",
        "",
    ]
    for item in result.categories:
        lines.append(f"- `{item.name}`: {item.score}/{item.max_score} - {item.summary}")
    lines.extend(["", "## Issues", ""])
    if not result.issues:
        lines.append("No issues were detected by the static benchmark.")
    else:
        for issue in result.issues:
            location = f" ({issue.file})" if issue.file else ""
            role = f" [{issue.role}]" if issue.role else ""
            lines.append(f"- **{issue.severity.upper()}** `{issue.code}`{role}: {issue.message}{location}")
    lines.extend(["", "## Role Coverage", ""])
    lines.append(f"- Human roles: {', '.join(sorted(result.human_roles)) or 'none'}")
    lines.append(f"- Generated roles: {', '.join(sorted(result.generated_roles)) or 'none'}")
    return "\n".join(lines) + "\n"


def render_improvement_plan(result: ParityResult) -> str:
    grouped: dict[str, list[ParityIssue]] = {}
    for issue in result.issues:
        grouped.setdefault(issue.code, []).append(issue)

    lines = [
        f"# Improvement Plan: {result.case_id}",
        "",
        f"Current score: **{result.human_parity_score}/100**",
        "",
        "## Priority Fixes",
        "",
    ]
    if not grouped:
        lines.append("No static parity gaps were detected. Next improvement step: run simulator-based validation.")
        return "\n".join(lines) + "\n"

    priority = [
        "generated_reference_missing",
        "missing_required_role",
        "hallucinated_signal",
        "missing_interface_signal",
        "weak_scoreboard",
        "missing_covergroup",
        "coverage_not_spec_grounded",
        "driver_monitor_missing_protocol_activity",
        "placeholder_logic",
    ]
    ordered_codes = sorted(grouped, key=lambda code: priority.index(code) if code in priority else len(priority))
    for code in ordered_codes:
        examples = grouped[code]
        lines.append(f"- `{code}` ({len(examples)} finding(s)): {_recommendation_for(code)}")
    return "\n".join(lines) + "\n"


def discover_uvm_roles(root: str | Path) -> dict[str, list[Path]]:
    """Discover UVM roles from filenames and lightweight source patterns."""

    path = Path(root)
    roles: dict[str, list[Path]] = {}
    if not path.exists() or not path.is_dir():
        return roles

    for file_path in _collect_uvm_files(path):
        name = file_path.name.lower()
        text = _read_text(file_path)
        role_names = _roles_for_file(name, text)
        for role in role_names:
            roles.setdefault(role, []).append(file_path)
    return roles


def _case_from_mapping(data: dict[str, Any], *, case_dir: str = "") -> BenchmarkCase:
    reset = data.get("reset") if isinstance(data.get("reset"), dict) else {}
    return BenchmarkCase(
        id=str(data.get("id") or data.get("top_module") or Path(case_dir).name),
        top_module=str(data.get("top_module") or data.get("id") or ""),
        spec_file=str(data.get("spec_file") or ""),
        rtl_files=[str(item) for item in data.get("rtl_files", []) or []],
        human_uvm_dir=str(data.get("human_uvm_dir") or "human_uvm"),
        generated_uvm_dir=str(data.get("generated_uvm_dir") or "generated_uvm"),
        clock=str(data.get("clock") or "clk"),
        reset={str(k): str(v) for k, v in reset.items()},
        required_roles=[str(item) for item in data.get("required_roles", []) or DEFAULT_REQUIRED_ROLES],
        case_dir=case_dir,
        functional_points_file=str(data.get("functional_points_file") or "functional_points.json"),
    )


def _validate_case_paths(case: BenchmarkCase, issues: list[ParityIssue]) -> None:
    if not case.top_module:
        _add_issue(issues, "error", "missing_top_module", "Benchmark case does not define top_module.")
    if not case.human_dir_path.exists():
        _add_issue(issues, "error", "human_uvm_dir_missing", "Human UVM directory does not exist.", str(case.human_dir_path))
    if not case.generated_dir_path.exists():
        _add_issue(issues, "error", "generated_uvm_dir_missing", "Generated UVM directory does not exist.", str(case.generated_dir_path))
    for rtl in case.rtl_file_paths:
        if not rtl.exists():
            _add_issue(issues, "warning", "rtl_file_missing", "RTL context file does not exist.", str(rtl))


def _collect_uvm_files(root: Path) -> list[Path]:
    if not root.exists() or not root.is_dir():
        return []
    return sorted(
        path
        for path in root.rglob("*")
        if path.is_file() and path.suffix.lower() in UVM_EXTENSIONS
    )


def _roles_for_file(name: str, text: str) -> set[str]:
    roles: set[str] = set()
    stripped = _strip_comments_and_strings(text)

    if name.endswith((".f", ".flist")):
        roles.add("filelist")
    if name.endswith("_pkg.sv") or re.search(r"\bpackage\s+[a-zA-Z_]\w*", stripped):
        roles.add("package")
    if name.endswith(("_if.sv", "_intf.sv")) or re.search(r"\binterface\s+[a-zA-Z_]\w*", stripped):
        roles.add("interface")
    if "seq_item" in name or "sequence_item" in name or "extends uvm_sequence_item" in stripped:
        roles.add("seq_item")
    if "sequencer" in name or "extends uvm_sequencer" in stripped:
        roles.add("sequencer")
    if "driver" in name or "extends uvm_driver" in stripped:
        roles.add("driver")
    if "monitor" in name or "extends uvm_monitor" in stripped:
        roles.add("monitor")
    if name.endswith("_agent.sv") or "extends uvm_agent" in stripped:
        roles.add("agent")
    if name.endswith("_env.sv") or "extends uvm_env" in stripped:
        roles.add("env")
    if "scoreboard" in name or "extends uvm_scoreboard" in stripped:
        roles.add("scoreboard")
    if "coverage" in name or re.search(r"\bcovergroup\b|\bcoverpoint\b", stripped):
        roles.add("coverage")
    if name.endswith(("_test.sv", "_tests.sv")) or "extends uvm_test" in stripped:
        roles.add("tests")
    if name in {"top_tb.sv", "testbench.sv", "hdl_top.sv"} or "run_test" in stripped:
        roles.add("top")
    if "seq_lib" in name or "extends uvm_sequence" in stripped:
        roles.add("sequence_library")
    return roles


def _expected_ports(case: BenchmarkCase) -> set[str]:
    expected = {case.clock}
    reset_name = case.reset.get("name") if isinstance(case.reset, dict) else ""
    if reset_name:
        expected.add(reset_name)
    for rtl in case.rtl_file_paths:
        if rtl.exists():
            expected.update(_extract_ports_from_rtl(rtl, case.top_module))
    return {port for port in expected if port}


def _extract_ports_from_rtl(path: Path, top_module: str) -> set[str]:
    text = _strip_sv_comments(_read_text(path))
    ports: set[str] = set()
    module_match = re.search(rf"\bmodule\s+{re.escape(top_module)}\b([\s\S]*?)\bendmodule\b", text)
    source = module_match.group(1) if module_match else text
    for raw_line in source.splitlines():
        line = raw_line.strip()
        if not re.search(r"\b(input|output|inout)\b", line):
            continue
        line = re.sub(r"\b(input|output|inout|wire|logic|reg|bit|signed|unsigned)\b", " ", line)
        line = re.sub(r"\[[^\]]+\]", " ", line)
        line = line.replace(")", " ").replace(";", " ")
        for part in line.split(","):
            tokens = re.findall(r"\b[a-zA-Z_]\w*\b", part)
            if tokens:
                ports.add(tokens[-1])
    return ports


def _check_interface_fidelity(
    case: BenchmarkCase,
    expected_ports: set[str],
    human_roles: dict[str, list[Path]],
    generated_roles: dict[str, list[Path]],
    issues: list[ParityIssue],
) -> None:
    generated_signals = _signals_for_role(generated_roles, "interface")
    human_signals = _signals_for_role(human_roles, "interface")
    allowed = expected_ports | human_signals | {case.clock, case.reset.get("name", "")}
    if expected_ports and generated_signals:
        for port in sorted(expected_ports - generated_signals):
            _add_issue(
                issues,
                "error",
                "missing_interface_signal",
                f"Generated interface is missing DUT signal '{port}'.",
                role="interface",
            )
    for signal in sorted(generated_signals - allowed):
        if signal and not signal.startswith(("uvm_", "m_", "cfg_")):
            _add_issue(
                issues,
                "error",
                "hallucinated_signal",
                f"Generated interface declares non-DUT signal '{signal}'.",
                role="interface",
            )


def _signals_for_role(roles: dict[str, list[Path]], role: str) -> set[str]:
    signals: set[str] = set()
    for path in roles.get(role, []):
        signals.update(_extract_declared_signals(_read_text(path)))
    return signals


def _extract_declared_signals(text: str) -> set[str]:
    stripped = _strip_sv_comments(text)
    signals: set[str] = set()
    for raw_line in stripped.splitlines():
        line = raw_line.strip()
        if not re.search(r"\b(input|output|inout|logic|wire|reg|bit)\b", line):
            continue
        if re.search(r"\b(class|module|interface|package|function|task)\b", line):
            continue
        line = re.sub(r"\b(input|output|inout|logic|wire|reg|bit|signed|unsigned)\b", " ", line)
        line = re.sub(r"\[[^\]]+\]", " ", line)
        line = line.replace(";", " ").replace(")", " ")
        for part in line.split(","):
            tokens = re.findall(r"\b[a-zA-Z_]\w*\b", part)
            if tokens:
                signals.add(tokens[-1])
    return signals


def _check_placeholders(files: list[Path], issues: list[ParityIssue]) -> None:
    for path in files:
        text = _read_text(path)
        if re.search(r"\bTODO\b|\bFIXME\b|placeholder|not implemented", text, re.IGNORECASE):
            _add_issue(
                issues,
                "warning",
                "placeholder_logic",
                "Generated source contains TODO/FIXME/placeholder text.",
                str(path),
            )


def _check_scoreboard(roles: dict[str, list[Path]], issues: list[ParityIssue]) -> None:
    for path in roles.get("scoreboard", []):
        text = _read_text(path)
        lower = text.lower()
        has_failure_path = any(token in lower for token in ("`uvm_error", "uvm_error", "$error", "uvm_fatal", "fail_count"))
        has_compare = bool(re.search(r"!==|!=|assert\s*\(|compare|expected|predict|reference", lower))
        if not has_failure_path or not has_compare:
            _add_issue(
                issues,
                "error",
                "weak_scoreboard",
                "Scoreboard does not show a real compare plus failure path.",
                str(path),
                "scoreboard",
            )


def _check_coverage(case: BenchmarkCase, roles: dict[str, list[Path]], issues: list[ParityIssue]) -> None:
    coverage_files = roles.get("coverage", [])
    if not coverage_files:
        _add_issue(issues, "error", "missing_covergroup", "Generated UVM has no coverage role.", role="coverage")
        return

    coverage_text = "\n".join(_read_text(path) for path in coverage_files)
    if not re.search(r"\bcovergroup\b", coverage_text):
        _add_issue(issues, "error", "missing_covergroup", "Coverage files do not define a covergroup.", role="coverage")
    if case.functional_points_path.exists():
        tokens = _functional_point_tokens(case.functional_points_path)
        if tokens and not any(token.lower() in coverage_text.lower() for token in tokens):
            _add_issue(
                issues,
                "warning",
                "coverage_not_spec_grounded",
                "Coverage does not reference any functional point ids/names.",
                str(case.functional_points_path),
                "coverage",
            )


def _functional_point_tokens(path: Path) -> set[str]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return set()
    tokens: set[str] = set()

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            for key in ("id", "name", "signal", "point"):
                raw = value.get(key)
                if isinstance(raw, str) and raw.strip():
                    tokens.add(raw.strip())
            for item in value.values():
                visit(item)
        elif isinstance(value, list):
            for item in value:
                visit(item)

    visit(data)
    return tokens


def _check_driver_monitor(roles: dict[str, list[Path]], issues: list[ParityIssue]) -> None:
    for role in ("driver", "monitor"):
        for path in roles.get(role, []):
            text = _read_text(path).lower()
            has_timing = "@(posedge" in text or "@(" in text
            has_vif = "vif" in text or "virtual" in text
            has_reset = "reset" in text or "rst" in text
            if role == "driver":
                has_activity = "seq_item_port" in text or "get_next_item" in text or "start_item" in text
            else:
                has_activity = "analysis_port" in text and ".write" in text
            if not (has_timing and has_vif and has_reset and has_activity):
                _add_issue(
                    issues,
                    "warning",
                    "driver_monitor_missing_protocol_activity",
                    f"Generated {role} lacks timing, reset, virtual interface, or transaction activity.",
                    str(path),
                    role,
                )


def _check_uvm_architecture(files: list[Path], roles: dict[str, list[Path]], issues: list[ParityIssue]) -> None:
    text = "\n".join(_read_text(path) for path in files)
    if files and "`uvm_component_utils" not in text and "`uvm_object_utils" not in text:
        _add_issue(issues, "warning", "missing_uvm_factory_macros", "Generated files do not use UVM factory macros.")
    if roles.get("agent") and "connect_phase" not in text:
        _add_issue(issues, "warning", "missing_connect_phase", "Generated UVM does not define a visible connect_phase.")
    if roles.get("top") and "run_test" not in text:
        _add_issue(issues, "error", "top_missing_run_test", "Top-level UVM file does not call run_test().", role="top")


def _score_categories(
    case: BenchmarkCase,
    issues: list[ParityIssue],
    human_roles: dict[str, list[Path]],
    generated_roles: dict[str, list[Path]],
    generated_files: list[Path],
) -> list[ScoreCategory]:
    required = case.required_roles or DEFAULT_REQUIRED_ROLES
    present = sum(1 for role in required if role in generated_roles)
    completeness = round(SCORE_WEIGHTS["file_completeness"] * present / max(1, len(required)))

    interface_penalty = _issue_count(issues, {"hallucinated_signal", "missing_interface_signal"}) * 3
    arch_penalty = _issue_count(issues, {"missing_uvm_factory_macros", "missing_connect_phase", "top_missing_run_test"}) * 4
    driver_penalty = _issue_count(issues, {"driver_monitor_missing_protocol_activity"}) * 4
    scoreboard_penalty = _issue_count(issues, {"weak_scoreboard"}) * 8
    coverage_penalty = _issue_count(issues, {"missing_covergroup"}) * 8 + _issue_count(issues, {"coverage_not_spec_grounded"}) * 3
    sequence_penalty = 0 if ("sequence_library" in generated_roles or "tests" in generated_roles) else 7
    maintainability_penalty = _issue_count(issues, {"placeholder_logic", "uvm_validator:duplicate_declaration"}) * 2

    if not generated_files:
        return [
            ScoreCategory(name, 0, max_score, "No generated UVM files were available.")
            for name, max_score in SCORE_WEIGHTS.items()
        ]

    return [
        ScoreCategory(
            "file_completeness",
            _clamp(completeness, 0, SCORE_WEIGHTS["file_completeness"]),
            SCORE_WEIGHTS["file_completeness"],
            f"{present}/{len(required)} required roles found.",
        ),
        ScoreCategory(
            "interface_fidelity",
            _score_after_penalty("interface_fidelity", interface_penalty),
            SCORE_WEIGHTS["interface_fidelity"],
            "DUT/interface signal matching.",
        ),
        ScoreCategory(
            "uvm_architecture",
            _score_after_penalty("uvm_architecture", arch_penalty),
            SCORE_WEIGHTS["uvm_architecture"],
            "Factory macros, phases, top-level UVM structure.",
        ),
        ScoreCategory(
            "driver_monitor_quality",
            _score_after_penalty("driver_monitor_quality", driver_penalty),
            SCORE_WEIGHTS["driver_monitor_quality"],
            "Driver and monitor transaction/timing structure.",
        ),
        ScoreCategory(
            "scoreboard_quality",
            _score_after_penalty("scoreboard_quality", scoreboard_penalty if "scoreboard" in generated_roles else SCORE_WEIGHTS["scoreboard_quality"]),
            SCORE_WEIGHTS["scoreboard_quality"],
            "Reference checking and failure-path strength.",
        ),
        ScoreCategory(
            "coverage_intent",
            _score_after_penalty("coverage_intent", coverage_penalty if "coverage" in generated_roles else SCORE_WEIGHTS["coverage_intent"]),
            SCORE_WEIGHTS["coverage_intent"],
            "Covergroup and functional-point grounding.",
        ),
        ScoreCategory(
            "sequence_quality",
            _score_after_penalty("sequence_quality", sequence_penalty),
            SCORE_WEIGHTS["sequence_quality"],
            "Sequence/test presence and stimulus structure.",
        ),
        ScoreCategory(
            "maintainability",
            _score_after_penalty("maintainability", maintainability_penalty),
            SCORE_WEIGHTS["maintainability"],
            "Readability, placeholders, duplicate declarations.",
        ),
    ]


def _score_after_penalty(category: str, penalty: int) -> int:
    return _clamp(SCORE_WEIGHTS[category] - penalty, 0, SCORE_WEIGHTS[category])


def _issue_count(issues: list[ParityIssue], codes: set[str]) -> int:
    return sum(1 for issue in issues if issue.code in codes)


def _clamp(value: int, low: int, high: int) -> int:
    return max(low, min(high, int(value)))


def _scorecard_payload(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "case_id": payload["case_id"],
        "top_module": payload["top_module"],
        "human_parity_score": payload["human_parity_score"],
        "categories": payload["categories"],
        "hard_flags": payload["hard_flags"],
        "validation": payload.get("validation", {}),
    }


def _roles_to_strings(roles: dict[str, list[Path]], root: Path) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    for role, paths in roles.items():
        result[role] = [_display_path(path, root) for path in paths]
    return result


def _display_path(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def _recommendation_for(code: str) -> str:
    recommendations = {
        "generated_reference_missing": "Provide the generated UVM directory or run the generator before scoring.",
        "missing_required_role": "Update generation to emit the full UVM component set for this design.",
        "hallucinated_signal": "Ground interface generation on parsed DUT ports and reject undeclared signal additions.",
        "missing_interface_signal": "Ensure interface/top/driver/monitor preserve every DUT port from the RTL contract.",
        "weak_scoreboard": "Generate real expected-vs-actual comparisons and UVM error paths.",
        "missing_covergroup": "Emit a covergroup with coverpoints tied to the functional-point file.",
        "coverage_not_spec_grounded": "Name or comment coverage points with functional point ids from the benchmark contract.",
        "driver_monitor_missing_protocol_activity": "Strengthen driver/monitor prompts/templates with reset, clocking, VIF, and TLM requirements.",
        "placeholder_logic": "Reject TODO/placeholder output before reporting generation as complete.",
    }
    if code.startswith("uvm_validator:"):
        return "Fix the generated source so it passes the existing UVM static trust gate."
    return "Inspect the generated file and update the generator or prompt contract for this issue."


def _add_issue(
    issues: list[ParityIssue],
    severity: str,
    code: str,
    message: str,
    file: str = "",
    role: str = "",
) -> None:
    issues.append(
        ParityIssue(
            severity=severity,
            code=code,
            message=message,
            file=file,
            role=role,
        )
    )


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""


def _strip_sv_comments(text: str) -> str:
    text = re.sub(r"/\*[\s\S]*?\*/", " ", text or "")
    return re.sub(r"//.*", " ", text)


def _strip_comments_and_strings(text: str) -> str:
    stripped = _strip_sv_comments(text)
    return re.sub(r'"(?:\\.|[^"\\])*"', '""', stripped)
