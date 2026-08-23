"""Parse external UVM simulator logs into normalized diagnostics.

The parser is intentionally deterministic. It does not try to repair files by
itself; it only turns Cadence/Questa/VCS compile and simulation messages into a
stable taxonomy that the repair agent and TruthCore evidence can consume.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable, List


CASCADE_CODES = {"CLSSPX", "SVEXTK", "EXPSMC", "EXPENC", "EXPENP", "VLGERR"}


@dataclass
class UVMLogDiagnostic:
    tool: str
    phase: str
    severity: str
    code: str
    message: str
    file: str = ""
    line: int | None = None
    column: int | None = None
    symbol: str = ""
    raw: str = ""
    root_cause: str = "unknown"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class UVMLogRootCause:
    id: str
    category: str
    summary: str
    severity: str
    diagnostics: list[UVMLogDiagnostic] = field(default_factory=list)
    repairable: bool = False

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["diagnostics"] = [diag.to_dict() for diag in self.diagnostics]
        return data


@dataclass
class UVMLogAnalysis:
    tool: str
    status: str
    diagnostics: list[UVMLogDiagnostic]
    root_causes: list[UVMLogRootCause]
    summary: str

    @property
    def blocking_errors(self) -> int:
        return sum(1 for diag in self.diagnostics if diag.severity == "error")

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool": self.tool,
            "status": self.status,
            "summary": self.summary,
            "blocking_errors": self.blocking_errors,
            "diagnostics": [diag.to_dict() for diag in self.diagnostics],
            "root_causes": [cause.to_dict() for cause in self.root_causes],
        }


def parse_uvm_logs(
    logs: Iterable[str | bytes],
    *,
    simulator: str = "auto",
) -> UVMLogAnalysis:
    texts = [_decode_log(log) for log in logs]
    combined = "\n".join(texts)
    tool = _detect_tool(combined, simulator)
    diagnostics = _dedupe_diagnostics(_parse_lines(combined, tool))

    # Cadence/Questa/VCS do NOT return a non-zero exit code merely because a UVM
    # test reported UVM_ERROR/UVM_FATAL — the exit status only reflects tool
    # (compile/elaborate/fatal) errors. So pass/fail must be derived from the log:
    #   1. the authoritative end-of-test "UVM Report Summary" counts, and
    #   2. a completion marker (its absence means the sim crashed/hung).
    summary_counts = _parse_uvm_summary_counts(combined)
    uvm_error_count = summary_counts.get("error", 0)
    uvm_fatal_count = summary_counts.get("fatal", 0)
    blocking = any(diag.severity == "error" for diag in diagnostics)
    started = _simulation_started(combined)
    completed = _has_completion_marker(combined)
    incomplete = started and not completed and not blocking

    if incomplete:
        incomplete_diag = UVMLogDiagnostic(
            tool=tool,
            phase="simulation",
            severity="error",
            code="NOFINISH",
            message=(
                "Simulation started but never reached a completion marker "
                "(UVM report summary / $finish). Likely a crash, hang, or UVM_FATAL "
                "before report — do not trust a zero exit code here."
            ),
            root_cause="simulation_incomplete",
        )
        diagnostics.append(incomplete_diag)

    root_causes = _group_root_causes(diagnostics)
    failed = blocking or uvm_error_count > 0 or uvm_fatal_count > 0 or incomplete
    status = "failed" if failed else "passed"

    if status == "passed":
        summary = (
            f"No blocking UVM compile/simulation errors found in {tool} log"
            + (f"; UVM report summary clean ({summary_counts.get('info', 0)} UVM_INFO)." if completed else ".")
        )
    else:
        parts: list[str] = []
        n_blocking = sum(1 for d in diagnostics if d.severity == "error")
        if n_blocking:
            parts.append(f"{n_blocking} blocking diagnostic(s)")
        if uvm_error_count:
            parts.append(f"{uvm_error_count} UVM_ERROR")
        if uvm_fatal_count:
            parts.append(f"{uvm_fatal_count} UVM_FATAL")
        if incomplete:
            parts.append("simulation did not complete")
        summary = (
            f"UVM run failed: {', '.join(parts) or 'errors detected'} "
            f"across {len(root_causes)} root-cause group(s)."
        )
    return UVMLogAnalysis(
        tool=tool,
        status=status,
        diagnostics=diagnostics,
        root_causes=root_causes,
        summary=summary,
    )


# UVM end-of-test summary lines look like "UVM_ERROR :    3" (spaced colon),
# whereas individual report lines look like "UVM_ERROR file.sv(12) @ 40: ...".
_UVM_SUMMARY_LINE_RE = re.compile(
    r"^\s*UVM_(?:ERROR|FATAL|WARNING|INFO)\s*:\s*\d+\s*$", re.IGNORECASE
)
_UVM_SUMMARY_COUNT_RE = re.compile(
    r"UVM_(ERROR|FATAL|WARNING|INFO)\s*:\s*(\d+)", re.IGNORECASE
)
# Individual UVM error/fatal message — must NOT be the spaced-colon summary form.
_UVM_MESSAGE_RE = re.compile(r"\bUVM_(?P<sev>ERROR|FATAL)\b(?!\s*:\s*\d)", re.IGNORECASE)

_COMPLETION_MARKERS = (
    "uvm report summary",
    "report counts by severity",
    "$finish",
    "simulation complete",
    "simulation stopped",
    "test passed",
    "test failed",
)
_SIM_START_MARKERS = (
    "uvm_info",
    "uvm_testname",
    "running test",
    "uvm_test_top",
    "reporting phase",
    "run phase",
)


def _parse_uvm_runtime_line(line: str, tool: str) -> UVMLogDiagnostic | None:
    """Parse an individual UVM_ERROR/UVM_FATAL message line (not a summary count)."""
    if _UVM_SUMMARY_LINE_RE.match(line):
        return None
    match = _UVM_MESSAGE_RE.search(line)
    if not match:
        return None
    severity = match.group("sev").upper()
    return UVMLogDiagnostic(
        tool="xcelium" if tool == "unknown" else tool,
        phase="simulation",
        severity="error",
        code=f"UVM_{severity}",
        message=line.strip(),
        symbol=_extract_symbol(line),
        raw=line,
    )


def _parse_uvm_summary_counts(text: str) -> dict[str, int]:
    """Return the LAST (authoritative) UVM report summary counts in the log."""
    counts: dict[str, int] = {}
    for match in _UVM_SUMMARY_COUNT_RE.finditer(text):
        counts[match.group(1).lower()] = int(match.group(2))
    return counts


def _has_completion_marker(text: str) -> bool:
    lowered = text.lower()
    return any(marker in lowered for marker in _COMPLETION_MARKERS)


def _simulation_started(text: str) -> bool:
    lowered = text.lower()
    return any(marker in lowered for marker in _SIM_START_MARKERS)


def _decode_log(log: str | bytes) -> str:
    if isinstance(log, bytes):
        return log.decode("utf-8", errors="replace")
    return str(log or "")


def _detect_tool(text: str, requested: str) -> str:
    requested = (requested or "auto").strip().lower()
    if requested and requested != "auto":
        return requested
    lowered = text.lower()
    if "xmvlog:" in lowered or "xrun:" in lowered or "xmsim:" in lowered:
        return "xcelium"
    if "vlog-" in lowered or "questa" in lowered or "modelsim" in lowered:
        return "questa"
    if "vcs" in lowered or "vlogan" in lowered or "ucli" in lowered:
        return "vcs"
    return "unknown"


def _parse_lines(text: str, tool: str) -> list[UVMLogDiagnostic]:
    diagnostics: list[UVMLogDiagnostic] = []
    pending_file: str = ""
    pending_line: int | None = None
    pending_col: int | None = None

    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        if not line.strip():
            continue

        source_match = re.search(
            r"(?P<file>[A-Za-z]:[\\/][^()\s:]+|[^()\s:]+?\.(?:svh?|vh?|v))"
            r"(?:[,:(](?P<line>\d+)(?:[|:,](?P<col>\d+))?)?",
            line,
            re.IGNORECASE,
        )
        if source_match:
            pending_file = _basename(source_match.group("file"))
            pending_line = _to_int(source_match.group("line"))
            pending_col = _to_int(source_match.group("col"))

        diag = (
            _parse_cadence_line(line, tool)
            or _parse_questa_line(line, tool)
            or _parse_vcs_line(line, tool)
            or _parse_uvm_runtime_line(line, tool)
            or _parse_generic_line(line, tool)
        )
        if not diag:
            continue

        if not diag.file and pending_file:
            diag.file = pending_file
        if diag.line is None and pending_line is not None:
            diag.line = pending_line
        if diag.column is None and pending_col is not None:
            diag.column = pending_col
        diag.root_cause = _classify_root_cause(diag)
        diagnostics.append(diag)

    return diagnostics


def _parse_cadence_line(line: str, tool: str) -> UVMLogDiagnostic | None:
    match = re.search(
        r"(?P<prefix>xmvlog|xrun|xmsim):\s+\*(?P<sev>[EWF]),(?P<code>[A-Z0-9]+)"
        r"(?:\s+\((?P<file>[^,()]+?)(?:,(?P<line>\d+)\|(?P<col>\d+))?\))?"
        r":?\s*(?P<msg>.*)",
        line,
    )
    if not match:
        return None
    return UVMLogDiagnostic(
        tool="xcelium" if tool == "unknown" else tool,
        phase=_phase_from_code(match.group("code")),
        severity=_severity(match.group("sev")),
        code=match.group("code"),
        file=_basename(match.group("file")),
        line=_to_int(match.group("line")),
        column=_to_int(match.group("col")),
        symbol=_extract_symbol(match.group("msg")),
        message=(match.group("msg") or "").strip(),
        raw=line,
    )


def _parse_questa_line(line: str, tool: str) -> UVMLogDiagnostic | None:
    match = re.search(
        r"\*\*(?:\s+)?(?P<sev>Error|Warning|Fatal):\s*"
        r"(?:\((?P<file>[^()]+?)\((?P<line>\d+)\)\))?\s*"
        r"(?:vlog-(?P<code>\d+)|vsim-(?P<simcode>\d+))?:?\s*(?P<msg>.*)",
        line,
        re.IGNORECASE,
    )
    if not match:
        return None
    code = match.group("code") or match.group("simcode") or "QUESTA"
    msg = (match.group("msg") or "").strip()
    return UVMLogDiagnostic(
        tool="questa" if tool == "unknown" else tool,
        phase=_phase_from_message(msg),
        severity=_severity(match.group("sev")),
        code=_canonical_code(code, msg),
        file=_basename(match.group("file")),
        line=_to_int(match.group("line")),
        symbol=_extract_symbol(msg),
        message=msg,
        raw=line,
    )


def _parse_vcs_line(line: str, tool: str) -> UVMLogDiagnostic | None:
    match = re.search(
        r"(?P<sev>Error|Warning|Fatal)-\[(?P<code>[A-Z0-9_-]+)\]\s*(?P<msg>.*)",
        line,
        re.IGNORECASE,
    )
    if not match:
        return None
    msg = (match.group("msg") or "").strip()
    return UVMLogDiagnostic(
        tool="vcs" if tool == "unknown" else tool,
        phase=_phase_from_message(msg),
        severity=_severity(match.group("sev")),
        code=_canonical_code(match.group("code"), msg),
        symbol=_extract_symbol(msg),
        message=msg,
        raw=line,
    )


def _parse_generic_line(line: str, tool: str) -> UVMLogDiagnostic | None:
    # UVM report-summary count lines ("UVM_ERROR :  0") and informational UVM
    # lines contain the word "error" but are NOT failures — skip them so a clean
    # run with a zero-count summary is not misreported as failed.
    if _UVM_SUMMARY_LINE_RE.match(line):
        return None
    stripped = line.lstrip()
    if stripped.upper().startswith(("UVM_INFO", "UVM_WARNING")):
        return None
    lowered = line.lower()
    if not any(token in lowered for token in ("error", "fatal", "could not be bound", "not found", "unknown type")):
        return None
    return UVMLogDiagnostic(
        tool=tool,
        phase=_phase_from_message(line),
        severity="error" if "warning" not in lowered else "warning",
        code=_canonical_code("GENERIC", line),
        symbol=_extract_symbol(line),
        message=line.strip(),
        raw=line,
    )


def _canonical_code(code: str, message: str) -> str:
    msg = (message or "").lower()
    if "package" in msg and ("not found" in msg or "could not be bound" in msg or "not defined" in msg):
        return "NOPBIND"
    if "unknown type" in msg or "unrecognized declaration" in msg or "not a type" in msg:
        return "NOIPRT"
    if "visible datatype" in msg or "does not refer to a visible datatype" in msg:
        return "SVNOTY"
    if "expecting" in msg and "endclass" in msg:
        return "EXPENC"
    if "expecting" in msg and "endpackage" in msg:
        return "EXPENP"
    return str(code or "GENERIC").upper()


def _classify_root_cause(diag: UVMLogDiagnostic) -> str:
    code = (diag.code or "").upper()
    msg = (diag.message or "").lower()
    symbol = diag.symbol or ""
    if code in {"UVM_ERROR", "UVM_FATAL"}:
        return "uvm_test_failure"
    if code == "NOFINISH":
        return "simulation_incomplete"
    if code == "NOPBIND":
        if symbol == "uvm_pkg":
            return "uvm_dependency_not_visible"
        if symbol.endswith("_pkg"):
            filename = (diag.file or "").lower()
            if filename in {"top_tb.sv", "testbench.sv"}:
                return "missing_package_or_compile_order"
            return "llm_introduced_unknown_package"
        return "llm_introduced_unknown_package"
    if code == "NOIPRT":
        return "llm_introduced_unknown_class"
    if code == "SVNOTY":
        if symbol.startswith("uvm_") or "uvm_" in msg:
            return "uvm_dependency_not_visible"
        filename = (diag.file or "").lower()
        if filename.endswith(("_sequence_item.sv", "_sequencer.sv", "_driver.sv", "_monitor.sv")):
            return "uvm_dependency_not_visible"
        return "missing_type_or_compile_order"
    if code in CASCADE_CODES:
        return "cascade_parse_error"
    if "package" in msg and ("not found" in msg or "could not be bound" in msg):
        return "missing_package_or_compile_order"
    if "unknown type" in msg or "unrecognized declaration" in msg:
        return "llm_introduced_unknown_class"
    return "unknown"


def _group_root_causes(diagnostics: list[UVMLogDiagnostic]) -> list[UVMLogRootCause]:
    buckets: dict[str, list[UVMLogDiagnostic]] = {}
    for diag in diagnostics:
        buckets.setdefault(diag.root_cause, []).append(diag)

    root_causes: list[UVMLogRootCause] = []
    for category, items in buckets.items():
        non_cascade = [item for item in items if item.code not in CASCADE_CODES]
        symbols = sorted({item.symbol for item in non_cascade if item.symbol})
        label = ", ".join(symbols[:4]) if symbols else f"{len(items)} diagnostic(s)"
        root_causes.append(
            UVMLogRootCause(
                id=f"UVM-RC-{len(root_causes) + 1:03d}",
                category=category,
                severity="error" if any(item.severity == "error" for item in items) else "warning",
                summary=_root_cause_summary(category, label),
                diagnostics=items,
                repairable=category
                in {
                    "llm_introduced_unknown_package",
                    "llm_introduced_unknown_class",
                    "missing_package_or_compile_order",
                    "uvm_dependency_not_visible",
                },
            )
        )

    root_causes.sort(key=lambda item: (not item.repairable, item.category))
    return root_causes


def _root_cause_summary(category: str, label: str) -> str:
    summaries = {
        "llm_introduced_unknown_package": f"Generated file imports a package that was not generated: {label}.",
        "llm_introduced_unknown_class": f"Generated file references a class/type that was not generated: {label}.",
        "missing_package_or_compile_order": f"A package is missing or compiled after a dependent file: {label}.",
        "uvm_dependency_not_visible": f"UVM base package/macros are not visible where required: {label}.",
        "missing_type_or_compile_order": f"A required datatype is not visible, likely due to compile order: {label}.",
        "cascade_parse_error": "Parser cascade after earlier package/type failures.",
        "uvm_test_failure": f"UVM test reported functional errors (checker/scoreboard/assertion): {label}.",
        "simulation_incomplete": "Simulation did not reach completion — likely crash, hang, or UVM_FATAL before report.",
    }
    return summaries.get(category, f"Unclassified UVM diagnostic group: {label}.")


def _dedupe_diagnostics(diagnostics: list[UVMLogDiagnostic]) -> list[UVMLogDiagnostic]:
    seen: set[tuple[Any, ...]] = set()
    deduped: list[UVMLogDiagnostic] = []
    for diag in diagnostics:
        key = (diag.code, diag.file, diag.line, diag.column, diag.symbol, diag.message)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(diag)
    return deduped


def _phase_from_code(code: str) -> str:
    code = (code or "").upper()
    if code in {"NOPBIND", "NOIPRT", "SVNOTY", "CLSSPX", "SVEXTK", "EXPSMC", "EXPENC", "EXPENP", "VLGERR"}:
        return "compile"
    return "unknown"


def _phase_from_message(message: str) -> str:
    msg = (message or "").lower()
    if any(token in msg for token in ("compile", "package", "datatype", "syntax", "parse")):
        return "compile"
    if any(token in msg for token in ("elaborat", "instance", "bind")):
        return "elaboration"
    if any(token in msg for token in ("uvm_error", "simulation", "assertion")):
        return "simulation"
    return "unknown"


def _severity(raw: str) -> str:
    text = (raw or "").strip().lower()
    if text in {"e", "error", "fatal", "f"}:
        return "error"
    if text in {"w", "warning"}:
        return "warning"
    return "info"


def _extract_symbol(message: str) -> str:
    text = message or ""
    patterns = [
        r"Package\s+([a-zA-Z_]\w*)\s+could not be bound",
        r"package\s+['\"]?([a-zA-Z_]\w*)['\"]?",
        r"Unrecognized declaration\s+'([a-zA-Z_]\w*)'",
        r"unknown type\s+'?([a-zA-Z_]\w*)'?",
        r"identifier\s+'?([a-zA-Z_]\w*)'?",
        r"\b([a-zA-Z_]\w*)\s+could not be bound",
        r"\b(uvm_[a-zA-Z_]\w*)\b",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return match.group(1)
    return ""


def _basename(value: str | None) -> str:
    if not value:
        return ""
    text = str(value).strip().strip('"').strip("'")
    return Path(text.replace("\\", "/")).name


def _to_int(value: str | None) -> int | None:
    try:
        return int(value) if value not in {None, ""} else None
    except ValueError:
        return None
