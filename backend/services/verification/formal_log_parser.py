"""Parse external formal verification logs into normalized diagnostics.

This supports the near-term manual loop: users run SymbiYosys/Jasper/Xcelium
outside Chipix, upload the log, and TruthCore receives structured evidence.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable


@dataclass
class FormalLogDiagnostic:
    tool: str
    phase: str
    severity: str
    code: str
    message: str
    file: str = ""
    line: int | None = None
    column: int | None = None
    property_name: str = ""
    symbol: str = ""
    trace_file: str = ""
    raw: str = ""
    root_cause: str = "unknown"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class FormalLogRootCause:
    id: str
    category: str
    summary: str
    severity: str
    diagnostics: list[FormalLogDiagnostic] = field(default_factory=list)
    repairable: bool = False

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["diagnostics"] = [diag.to_dict() for diag in self.diagnostics]
        return data


@dataclass
class FormalLogAnalysis:
    tool: str
    status: str
    diagnostics: list[FormalLogDiagnostic]
    root_causes: list[FormalLogRootCause]
    summary: str

    @property
    def blocking_errors(self) -> int:
        return sum(1 for diag in self.diagnostics if diag.severity == "error")

    @property
    def failed_properties(self) -> int:
        return sum(1 for diag in self.diagnostics if diag.root_cause == "property_failed")

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool": self.tool,
            "status": self.status,
            "summary": self.summary,
            "blocking_errors": self.blocking_errors,
            "failed_properties": self.failed_properties,
            "diagnostics": [diag.to_dict() for diag in self.diagnostics],
            "root_causes": [cause.to_dict() for cause in self.root_causes],
        }


def parse_formal_logs(
    logs: Iterable[str | bytes],
    *,
    tool: str = "auto",
) -> FormalLogAnalysis:
    texts = [_decode_log(log) for log in logs]
    combined = "\n".join(texts)
    detected_tool = _detect_tool(combined, tool)
    diagnostics = _dedupe_diagnostics(_parse_lines(combined, detected_tool))
    root_causes = _group_root_causes(diagnostics)
    status = _status_from_diagnostics(diagnostics, combined)
    summary = _summary(status, detected_tool, diagnostics, root_causes)
    return FormalLogAnalysis(
        tool=detected_tool,
        status=status,
        diagnostics=diagnostics,
        root_causes=root_causes,
        summary=summary,
    )


def _decode_log(log: str | bytes) -> str:
    if isinstance(log, bytes):
        return log.decode("utf-8", errors="replace")
    return str(log or "")


def _detect_tool(text: str, requested: str) -> str:
    requested = (requested or "auto").strip().lower()
    if requested and requested != "auto":
        return requested
    lowered = text.lower()
    if "sby " in lowered or "symbiyosys" in lowered or "smtbmc" in lowered:
        return "symbiyosys"
    if "jasper" in lowered or "jg " in lowered or "proofcore" in lowered:
        return "jasper"
    if "xmvlog:" in lowered or "xrun:" in lowered or "xmsim:" in lowered:
        return "xcelium"
    if "vlog-" in lowered or "questa" in lowered or "modelsim" in lowered:
        return "questa"
    return "unknown"


def _parse_lines(text: str, tool: str) -> list[FormalLogDiagnostic]:
    diagnostics: list[FormalLogDiagnostic] = []
    pending_file = ""
    pending_line: int | None = None
    pending_col: int | None = None

    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        if not line.strip():
            continue
        source_match = re.search(
            r"(?P<file>[A-Za-z]:[\\/][^()\s:]+|[^()\s:]+?\.(?:svh?|vh?|v|sby|tcl))"
            r"(?:[,:(](?P<line>\d+)(?:[|:,](?P<col>\d+))?)?",
            line,
            re.IGNORECASE,
        )
        if source_match:
            pending_file = _basename(source_match.group("file"))
            pending_line = _to_int(source_match.group("line"))
            pending_col = _to_int(source_match.group("col"))

        diag = (
            _parse_sby_line(line, tool)
            or _parse_cadence_line(line, tool)
            or _parse_questa_line(line, tool)
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


def _parse_sby_line(line: str, tool: str) -> FormalLogDiagnostic | None:
    lower = line.lower()
    if "writing trace to" in lower:
        trace = line.rsplit(" ", 1)[-1].strip()
        return FormalLogDiagnostic(
            tool="symbiyosys" if tool == "unknown" else tool,
            phase="trace",
            severity="info",
            code="TRACE",
            message=line.strip(),
            trace_file=trace,
            raw=line,
            root_cause="counterexample_trace",
        )
    if "assert failed" in lower or "status returned by engine: fail" in lower or re.search(r"\bFAIL\b", line):
        return FormalLogDiagnostic(
            tool="symbiyosys" if tool == "unknown" else tool,
            phase="prove",
            severity="error",
            code="ASSERT_FAIL",
            property_name=_extract_property(line),
            message=line.strip(),
            raw=line,
        )
    if "status returned by engine: pass" in lower or re.search(r"\bPASS\b", line):
        return FormalLogDiagnostic(
            tool="symbiyosys" if tool == "unknown" else tool,
            phase="prove",
            severity="info",
            code="ASSERT_PASS",
            property_name=_extract_property(line),
            message=line.strip(),
            raw=line,
        )
    if any(token in lower for token in ("error:", "syntax error", "unknown identifier", "can't resolve")):
        return FormalLogDiagnostic(
            tool="symbiyosys" if tool == "unknown" else tool,
            phase="compile",
            severity="error",
            code=_canonical_code("SBY", line),
            symbol=_extract_symbol(line),
            message=line.strip(),
            raw=line,
        )
    return None


def _parse_cadence_line(line: str, tool: str) -> FormalLogDiagnostic | None:
    match = re.search(
        r"(?P<prefix>xmvlog|xrun|xmsim):\s+\*(?P<sev>[EWF]),(?P<code>[A-Z0-9]+)"
        r"(?:\s+\((?P<file>[^,()]+?)(?:,(?P<line>\d+)\|(?P<col>\d+))?\))?"
        r":?\s*(?P<msg>.*)",
        line,
    )
    if not match:
        return None
    msg = (match.group("msg") or "").strip()
    return FormalLogDiagnostic(
        tool="xcelium" if tool == "unknown" else tool,
        phase="compile",
        severity=_severity(match.group("sev")),
        code=_canonical_code(match.group("code"), msg),
        file=_basename(match.group("file")),
        line=_to_int(match.group("line")),
        column=_to_int(match.group("col")),
        symbol=_extract_symbol(msg),
        message=msg,
        raw=line,
    )


def _parse_questa_line(line: str, tool: str) -> FormalLogDiagnostic | None:
    match = re.search(
        r"\*\*(?:\s+)?(?P<sev>Error|Warning|Fatal):\s*"
        r"(?:\((?P<file>[^()]+?)\((?P<line>\d+)\)\))?\s*"
        r"(?:vlog-(?P<code>\d+)|vsim-(?P<simcode>\d+))?:?\s*(?P<msg>.*)",
        line,
        re.IGNORECASE,
    )
    if not match:
        return None
    msg = (match.group("msg") or "").strip()
    return FormalLogDiagnostic(
        tool="questa" if tool == "unknown" else tool,
        phase="compile",
        severity=_severity(match.group("sev")),
        code=_canonical_code(match.group("code") or match.group("simcode") or "QUESTA", msg),
        file=_basename(match.group("file")),
        line=_to_int(match.group("line")),
        symbol=_extract_symbol(msg),
        message=msg,
        raw=line,
    )


def _parse_generic_line(line: str, tool: str) -> FormalLogDiagnostic | None:
    lowered = line.lower()
    if not any(token in lowered for token in ("error", "failed", "assert", "unknown", "could not", "unresolved", "vacuous")):
        return None
    severity = "warning" if "warning" in lowered or "vacuous" in lowered else "error"
    return FormalLogDiagnostic(
        tool=tool,
        phase="prove" if "assert" in lowered or "proof" in lowered else "compile",
        severity=severity,
        code=_canonical_code("GENERIC", line),
        property_name=_extract_property(line),
        symbol=_extract_symbol(line),
        message=line.strip(),
        raw=line,
    )


def _canonical_code(code: str, message: str) -> str:
    msg = (message or "").lower()
    if "assert failed" in msg or "property failed" in msg or re.search(r"\bfail\b", msg):
        return "ASSERT_FAIL"
    if "vacuous" in msg or "unreachable" in msg:
        return "VACUOUS"
    if (
        "unknown identifier" in msg
        or "unresolved" in msg
        or "can't resolve" in msg
        or "lookup failed" in msg
        or "not declared" in msg
    ):
        return "UNKNOWN_SYMBOL"
    if "syntax" in msg or "parse" in msg or "expecting" in msg:
        return "SVA_SYNTAX"
    if "file not found" in msg or "no such file" in msg:
        return "MISSING_FILE"
    if "package" in msg and ("not found" in msg or "could not be bound" in msg):
        return "NOPBIND"
    return str(code or "GENERIC").upper()


def _classify_root_cause(diag: FormalLogDiagnostic) -> str:
    if diag.code == "ASSERT_FAIL":
        return "property_failed"
    if diag.code in {"UNKNOWN_SYMBOL", "NOPBIND"}:
        return "ungrounded_formal_reference"
    if diag.code in {"SVA_SYNTAX", "SVNOTY", "EXPSMC", "EXPENC", "EXPENP"}:
        return "sva_compile_error"
    if diag.code == "MISSING_FILE":
        return "missing_formal_input"
    if diag.code == "VACUOUS":
        return "vacuity_or_unreachable_cover"
    return "formal_tool_error" if diag.severity == "error" else "informational"


def _group_root_causes(diagnostics: list[FormalLogDiagnostic]) -> list[FormalLogRootCause]:
    groups: dict[str, list[FormalLogDiagnostic]] = {}
    for diag in diagnostics:
        if diag.severity == "info":
            continue
        groups.setdefault(diag.root_cause or "unknown", []).append(diag)
    root_causes: list[FormalLogRootCause] = []
    for category, items in groups.items():
        root_causes.append(
            FormalLogRootCause(
                id=f"FORMAL-{len(root_causes) + 1:03d}",
                category=category,
                summary=_root_summary(category, items),
                severity="error" if any(item.severity == "error" for item in items) else "warning",
                diagnostics=items,
                repairable=category in {"ungrounded_formal_reference", "sva_compile_error", "missing_formal_input"},
            )
        )
    return root_causes


def _root_summary(category: str, diagnostics: list[FormalLogDiagnostic]) -> str:
    first = diagnostics[0] if diagnostics else None
    symbol = f" ({first.symbol})" if first and first.symbol else ""
    if category == "property_failed":
        prop = first.property_name if first else ""
        return f"Formal proof found a failing property{f' {prop}' if prop else ''}."
    if category == "ungrounded_formal_reference":
        return f"Generated formal collateral references an unknown symbol or package{symbol}."
    if category == "sva_compile_error":
        return "Generated SVA has a syntax/type visibility issue."
    if category == "vacuity_or_unreachable_cover":
        return "A property or cover appears vacuous/unreachable; assumptions or coverage goals need review."
    if category == "missing_formal_input":
        return "The formal tool could not find one of the required source files."
    return diagnostics[0].message[:220] if diagnostics else "Formal diagnostic requires review."


def _status_from_diagnostics(diagnostics: list[FormalLogDiagnostic], text: str) -> str:
    if any(diag.root_cause == "property_failed" for diag in diagnostics):
        return "failed"
    if any(diag.severity == "error" for diag in diagnostics):
        return "error"
    lowered = text.lower()
    if "status returned by engine: pass" in lowered or re.search(r"\bpass\b", lowered):
        return "passed"
    if diagnostics:
        return "warning"
    return "unknown"


def _summary(
    status: str,
    tool: str,
    diagnostics: list[FormalLogDiagnostic],
    root_causes: list[FormalLogRootCause],
) -> str:
    if status == "passed":
        return f"Formal log from {tool} reports no blocking failures."
    if status == "failed":
        return f"Formal proof failed: {sum(1 for d in diagnostics if d.root_cause == 'property_failed')} failing property diagnostic(s)."
    if status == "error":
        return f"Formal tool reported {sum(1 for d in diagnostics if d.severity == 'error')} blocking diagnostic(s) across {len(root_causes)} root-cause group(s)."
    return f"Formal log from {tool} produced {len(diagnostics)} diagnostic(s)."


def _dedupe_diagnostics(diagnostics: list[FormalLogDiagnostic]) -> list[FormalLogDiagnostic]:
    seen: set[tuple[Any, ...]] = set()
    result: list[FormalLogDiagnostic] = []
    for diag in diagnostics:
        key = (diag.code, diag.file, diag.line, diag.symbol, diag.property_name, diag.message[:120])
        if key in seen:
            continue
        seen.add(key)
        result.append(diag)
    return result


def _extract_property(message: str) -> str:
    for pattern in (
        r"\b(?:assertion|property)\s+([A-Za-z_][A-Za-z0-9_.$]*)",
        r"\b([A-Za-z_][A-Za-z0-9_.$]*\.(?:a_|c_)[A-Za-z_][A-Za-z0-9_]*)",
        r"\b((?:a_|c_)[A-Za-z_][A-Za-z0-9_]*)",
    ):
        match = re.search(pattern, message or "", re.IGNORECASE)
        if match:
            return match.group(1).strip(".")
    return ""


def _extract_symbol(message: str) -> str:
    for pattern in (
        r"'([^']+)'",
        r'"([^"]+)"',
        r"\b(?:unknown|unresolved|identifier|symbol|package|type)\s+([A-Za-z_][A-Za-z0-9_$]*)",
    ):
        match = re.search(pattern, message or "", re.IGNORECASE)
        if match:
            token = match.group(1).strip()
            if re.match(r"^[A-Za-z_][A-Za-z0-9_$]*$", token):
                return token
    return ""


def _severity(raw: str) -> str:
    value = (raw or "").lower()
    if value.startswith("e") or value.startswith("f"):
        return "error"
    if value.startswith("w"):
        return "warning"
    return "info"


def _basename(path: str | None) -> str:
    if not path:
        return ""
    return Path(str(path).replace("\\", "/")).name


def _to_int(raw: str | None) -> int | None:
    try:
        return int(raw) if raw else None
    except Exception:
        return None
