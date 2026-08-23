"""Static trust checks for generated UVM environments.

These checks only flag genuine corruption — markdown fences, empty/missing
files, unbalanced declarations, missing includes, duplicate declarations and
obvious placeholder scoreboards. They DO NOT guess at "required" UVM roles by
filename, and structural/plan-shape mismatches are advisory warnings, never
blocking errors. A real Cadence ``xrun`` compile is the authority on whether the
collateral is complete (see ``decide_uvm_trust``); when Cadence is connected the
simulator decides, and this static pass is purely informational. Filename-based
role heuristics were removed because they produced false "missing UVM role"
failures on perfectly valid, differently-named collateral.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable, List, Optional


@dataclass
class UVMValidationIssue:
    severity: str
    code: str
    message: str
    file: str = ""


@dataclass
class UVMValidationResult:
    status: str
    trusted: bool
    score: int
    summary: str
    checked_files: int
    errors: int
    warnings: int
    issues: List[UVMValidationIssue] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["issues"] = [asdict(issue) for issue in self.issues]
        return data


SV_EXTENSIONS = {".sv", ".svh", ".v", ".vh"}
TRUST_BLOCKING_SEVERITIES = {"error"}


def validate_uvm_environment(
    *,
    work_dir: str | Path,
    generated_files: Iterable[str | Path],
    top_module: str = "",
    dut_rtl_files: Optional[Iterable[str | Path]] = None,
    generation_plan: Optional[Any] = None,
) -> UVMValidationResult:
    """Validate generated UVM collateral before it is treated as trusted."""

    root = Path(work_dir)
    files = [Path(path) for path in generated_files if str(path)]
    dut_rtl = [Path(path) for path in (dut_rtl_files or []) if str(path)]
    issues: list[UVMValidationIssue] = []

    _check_file_presence(files, issues)

    sv_files = [
        path
        for path in files
        if path.suffix.lower() in SV_EXTENSIONS and path.exists() and path.is_file()
    ]
    for path in sv_files:
        _check_sv_file(path, root, issues)

    _check_duplicate_declarations(sv_files, issues)
    _check_filelist(root, files, dut_rtl, top_module, issues)
    _check_output_matches_plan(files, sv_files, generation_plan, issues)

    errors = sum(1 for issue in issues if issue.severity == "error")
    warnings = sum(1 for issue in issues if issue.severity == "warning")
    trusted = errors == 0
    score = max(0, 100 - errors * 25 - warnings * 5)
    status = "passed" if trusted else "failed"
    summary = (
        f"UVM validation {status}: {len(sv_files)} SV/UVM file(s) checked, "
        f"{errors} error(s), {warnings} warning(s)."
    )

    return UVMValidationResult(
        status=status,
        trusted=trusted,
        score=score,
        summary=summary,
        checked_files=len(sv_files),
        errors=errors,
        warnings=warnings,
        issues=issues,
    )


def _add(
    issues: list[UVMValidationIssue],
    severity: str,
    code: str,
    message: str,
    file: str | Path = "",
) -> None:
    issues.append(
        UVMValidationIssue(
            severity=severity,
            code=code,
            message=message,
            file=str(file) if file else "",
        )
    )


def _check_file_presence(
    files: list[Path],
    issues: list[UVMValidationIssue],
) -> None:
    if not files:
        _add(issues, "error", "no_files", "No generated files were returned.")
        return

    for path in files:
        if not path.exists():
            _add(issues, "error", "missing_file", "Generated file path does not exist.", path)
        elif path.is_file() and path.stat().st_size == 0:
            _add(issues, "error", "empty_file", "Generated file is empty.", path)


def _check_sv_file(path: Path, root: Path, issues: list[UVMValidationIssue]) -> None:
    text = path.read_text(encoding="utf-8", errors="ignore")
    code_text = _strip_sv_comments(text)
    basename = path.name.lower()

    if "```" in text:
        _add(issues, "error", "code_fence", "Generated source still contains markdown code fences.", path)

    if not _balanced_declaration(code_text, "class", r"\bendclass\b"):
        _add(issues, "error", "unbalanced_class", "Class declaration count does not match endclass count.", path)
    if not _balanced_declaration(code_text, "module", r"\bendmodule\b"):
        _add(issues, "error", "unbalanced_module", "Module declaration count does not match endmodule count.", path)
    if not _balanced_declaration(code_text, "interface", r"\bendinterface\b"):
        _add(issues, "error", "unbalanced_interface", "Interface declaration count does not match endinterface count.", path)
    if not _balanced_declaration(code_text, "package", r"\bendpackage\b"):
        _add(issues, "error", "unbalanced_package", "Package declaration count does not match endpackage count.", path)

    includes = re.findall(r'`include\s+"([^"]+)"', text)
    for include in includes:
        if Path(include).name == "uvm_macros.svh":
            continue
        if not (path.parent / include).exists() and not (root / include).exists():
            _add(issues, "error", "missing_include", f"Included file is missing: {include}", path)

    if re.search(r"\bseq\.start\s*\(\s*null\s*\)", text):
        _add(issues, "error", "null_sequence_start", "Sequence starts on null sequencer.", path)

    if re.search(r"\bdrive_item\s*\([^)]*\bitem\b[^)]*\)\s*;", text) and "req." in text:
        body = _extract_task_body(text, "drive_item")
        if body and "req." in body and not re.search(r"\b\w+\s+req\s*;", body):
            _add(issues, "error", "ungrounded_req_reference", "drive_item(item) body references req instead of item.", path)

    if "scoreboard" in basename:
        lower = text.lower()
        has_real_failure_path = "uvm_error" in lower or "fail_count++" in lower or "$error" in lower
        has_todo = "todo" in lower or "placeholder" in lower
        if has_todo:
            _add(issues, "error", "scoreboard_todo", "Scoreboard still contains TODO/placeholder logic.", path)
        if "pass_count++" in text and not has_real_failure_path:
            _add(issues, "error", "scoreboard_no_failure_path", "Scoreboard increments pass count without a failure path.", path)

    if re.search(r"\bTODO\b|\bFIXME\b", text, re.IGNORECASE):
        _add(issues, "warning", "todo_marker", "Generated source contains TODO/FIXME marker.", path)


def _balanced_declaration(text: str, kind: str, end_pattern: str) -> bool:
    starts = 0
    for match in re.finditer(rf"\b{re.escape(kind)}\s+[a-zA-Z_]\w*", text):
        prefix = text[max(0, match.start() - 32):match.start()].lower()
        if kind == "class" and re.search(r"\btypedef\s+$", prefix):
            continue
        starts += 1
    ends = len(re.findall(end_pattern, text))
    return starts == 0 or ends >= starts


def _strip_sv_comments(text: str) -> str:
    text = re.sub(r"/\*[\s\S]*?\*/", "", text)
    return re.sub(r"//.*", "", text)


def _extract_task_body(text: str, task_name: str) -> str:
    match = re.search(
        rf"\btask\b[\s\S]*?\b{re.escape(task_name)}\b[\s\S]*?;(?P<body>[\s\S]*?)\bendtask\b",
        text,
        re.IGNORECASE,
    )
    return match.group("body") if match else ""


def _declared_names(path: Path) -> list[tuple[str, str]]:
    text = _strip_sv_comments(path.read_text(encoding="utf-8", errors="ignore"))
    names: list[tuple[str, str]] = []
    for match in re.finditer(r"\b(class|module|interface|package)\s+([a-zA-Z_]\w*)", text):
        prefix = text[max(0, match.start() - 32):match.start()].lower()
        if re.search(r"\btypedef\s+$", prefix):
            continue
        names.append((match.group(1), match.group(2)))
    return names


def _check_duplicate_declarations(
    sv_files: list[Path],
    issues: list[UVMValidationIssue],
) -> None:
    seen: dict[tuple[str, str], Path] = {}
    for path in sv_files:
        for kind, name in _declared_names(path):
            key = (kind, name)
            if key in seen:
                _add(
                    issues,
                    "error",
                    "duplicate_declaration",
                    f"Duplicate {kind} declaration '{name}' also appears in {seen[key].name}.",
                    path,
                )
            else:
                seen[key] = path


def _check_filelist(
    root: Path,
    files: list[Path],
    dut_rtl: list[Path],
    top_module: str,
    issues: list[UVMValidationIssue],
) -> None:
    filelists = [
        path
        for path in files
        if path.exists() and path.is_file() and path.suffix.lower() in {".f", ".flist"}
    ]
    if not filelists:
        _add(issues, "warning", "missing_filelist", "No UVM filelist was generated.")
        return

    filelist = filelists[0]
    text = filelist.read_text(encoding="utf-8", errors="ignore")
    listed = _filelist_entries(text)

    if "top_tb.sv" not in {Path(item).name for item in listed}:
        _add(issues, "warning", "filelist_missing_top", "Filelist does not include top_tb.sv.", filelist)

    if top_module and not any(top_module in Path(item).name for item in listed):
        _add(issues, "warning", "filelist_top_unclear", "Filelist does not clearly reference the top-module package/test files.", filelist)

    generated_names = {path.name for path in files}
    for item in listed:
        name = Path(item).name
        if name in generated_names:
            continue
        if not (root / item).exists() and not Path(item).exists():
            _add(issues, "warning", "filelist_unresolved_entry", f"Filelist entry could not be resolved: {item}", filelist)

    if dut_rtl:
        dut_names = {path.name for path in dut_rtl}
        listed_names = {Path(item).name for item in listed}
        if not dut_names.intersection(listed_names):
            _add(issues, "warning", "filelist_missing_dut", "Filelist does not include active DUT RTL source files.", filelist)
    else:
        _add(issues, "warning", "dut_sources_unknown", "Active DUT RTL source files were not resolved for filelist validation.", filelist)


def _filelist_entries(text: str) -> list[str]:
    entries: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("//") or line.startswith("#") or line.startswith("+"):
            continue
        entries.append(line)
    return entries


def _check_output_matches_plan(
    files: list[Path],
    sv_files: list[Path],
    generation_plan: Optional[Any],
    issues: list[UVMValidationIssue],
) -> None:
    wrapper = _plan_to_dict(generation_plan)
    if not wrapper:
        return

    has_explicit_policy = bool(wrapper.get("generation_policy"))
    policy = _plan_to_dict(wrapper.get("generation_policy") or wrapper)
    expected_preview = wrapper.get("files_preview") if isinstance(wrapper.get("files_preview"), list) else []
    all_names = {path.name.lower() for path in files}
    sv_names = {path.name.lower() for path in sv_files}

    if has_explicit_policy:
        for expected in expected_preview:
            expected_name = Path(str(expected)).name.lower()
            if expected_name and expected_name not in all_names:
                _add(
                    issues,
                    "warning",
                    "missing_plan_file",
                    f"Generation policy expected {expected_name}, but it was not generated.",
                )

    multi_agent = bool(policy.get("multi_agent"))
    agent_files = sorted(name for name in sv_names if name.endswith("_agent.sv"))
    if not multi_agent and len(agent_files) > 1:
        _add(
            issues,
            "warning",
            "policy_multi_agent_mismatch",
            "Generation policy selected single-agent UVM, but multiple agent bundles were generated.",
        )
    if multi_agent and len(agent_files) < 2:
        _add(
            issues,
            "warning",
            "policy_multi_agent_missing",
            "Generation policy selected multi-agent UVM, but fewer than two agent bundles were generated.",
        )

    optional = {
        str(item).lower()
        for item in (policy.get("optional_files") or [])
    }
    optional_expectations = {
        "ral": ("_ral.sv", "RAL model"),
        "assertions": ("_assertions.sv", "assertion file"),
        "makefile": ("makefile", "Makefile"),
        "env_config": ("_env_config.sv", "environment config"),
        "virtual_sequencer": ("_virtual_sequencer.sv", "virtual sequencer"),
        "virtual_sequence": ("_virtual_sequence.sv", "virtual sequence"),
        "test_plan_doc": ("test_plan.md", "test plan document"),
    }
    for role, (suffix, label) in optional_expectations.items():
        if role not in optional:
            continue
        if not any(name.endswith(suffix) for name in all_names):
            _add(
                issues,
                "warning",
                "missing_policy_optional_file",
                f"Generation policy requested {label}, but it was not generated.",
            )


def _plan_to_dict(value: Optional[Any]) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    if hasattr(value, "to_dict"):
        try:
            data = value.to_dict()
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}
    if hasattr(value, "__dict__"):
        return {
            str(k): v
            for k, v in vars(value).items()
            if not str(k).startswith("_")
        }
    return {}
