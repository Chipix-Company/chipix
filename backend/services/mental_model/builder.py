"""
Mental Model Builder — Constructs the mental model from RTL + spec.

Strategy: Parse First, LLM Second.
  Phase 1: Scan folder (zero LLM)
  Phase 2: Structural index via Slang, with explicit regex fallback if enabled
  Phase 3: LLM per block for design intent
  Phase 4: Cross-block integration (bounded ReAct agent with source-grounded tools)
  Phase 5: Verification intent (LLM + spec + model)
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from services.mental_model.schema import (
    ClockDomain,
    DesignBlock,
    ExpectedBehavior,
    FSMDescription,
    MentalModelSchema,
    OpenQuestion,
    ParameterInfo,
    PortInfo,
    ProjectScan,
    ProtocolBinding,
    RegisterField,
    Requirement,
    SourceReference,
    SubModuleInstance,
    TransactionFlow,
    VerificationIntent,
    UnitTestIntent,
    FormalPropertyIntent,
    UVMScenarioIntent,
    CoveragePointIntent,
)
from services.mental_model.slang_analyzer import (
    SlangAnalysisError,
    SlangStructuralAnalyzer,
)
from services.rtl_project_package import (
    archive_for_extracted_rtl_root,
    extract_rtl_archive,
    find_extracted_rtl_root,
    is_rtl_archive_filename,
    is_rtl_source_filename,
    short_rtl_extract_root,
)

logger = logging.getLogger(__name__)

_SPEC_PROMPT_EXCERPT_CHARS = int(
    os.environ.get("CHIPVERIFY_SPEC_PROMPT_EXCERPT_CHARS", "12000")
)
_RTL_PROMPT_EXCERPT_CHARS = int(
    os.environ.get("CHIPVERIFY_RTL_PROMPT_EXCERPT_CHARS", "12000")
)


def _spec_excerpt_for_prompt(spec_text: str) -> str:
    """Bound spec text for per-module LLM prompts; allow more when index-backed."""
    limit = _SPEC_PROMPT_EXCERPT_CHARS
    if "=== SPEC DOCUMENT INDEX ===" in spec_text:
        limit = max(limit, 16000)
    return spec_text[:limit]


def _middle_omitted_excerpt(text: str, limit: int) -> str:
    text = str(text or "")
    if limit <= 0 or len(text) <= limit:
        return text
    head_len = max(limit * 2 // 3, 1)
    tail_len = max(limit - head_len, 1)
    if head_len + tail_len >= len(text):
        return text[:limit]
    omitted = len(text) - head_len - tail_len
    return (
        text[:head_len]
        + f"\n\n// ... omitted {omitted} characters from the middle ...\n\n"
        + text[-tail_len:]
    )


def _rtl_excerpt_for_prompt(rtl_content: str) -> str:
    """Bound RTL text while retaining both declarations and trailing behavior."""
    return _middle_omitted_excerpt(rtl_content, _RTL_PROMPT_EXCERPT_CHARS)


# ═══════════════════════════════════════════════════════════════════════
# RTL File Extensions
# ═══════════════════════════════════════════════════════════════════════

RTL_EXTENSIONS = {".v", ".sv", ".svh", ".vh", ".vhd", ".vhdl"}
SPEC_EXTENSIONS = {".pdf", ".docx", ".doc", ".txt", ".md", ".rst"}
TB_EXTENSIONS = {".sv", ".v"}
TB_PATTERNS = {"_tb", "_test", "testbench", "tb_"}
UVM_PATTERNS = {"uvm_", "_agent", "_driver", "_monitor", "_scoreboard", "_env", "_seq"}
FORMAL_PATTERNS = {"_sva", "_assert", "_prop", "bind_", "_formal"}
FILELIST_EXTENSIONS = {".f", ".flist"}


from common.env import env_bool as _env_flag
def _regex_parser_fallback_enabled() -> bool:
    """Allow demos to keep working when Slang is not installed."""
    if _env_flag("CHIPVERIFY_REQUIRE_SLANG", False):
        return False
    return _env_flag("CHIPVERIFY_ALLOW_REGEX_PARSER_FALLBACK", True)


# ═══════════════════════════════════════════════════════════════════════
# Phase 1: Project Scanner (Zero LLM)
# ═══════════════════════════════════════════════════════════════════════


def scan_project_folder(root_path: str) -> ProjectScan:
    """
    Scan a project folder to understand its structure.
    Pure file-system traversal — no LLM needed.
    """
    root = Path(root_path)
    if not root.exists():
        raise FileNotFoundError(f"Project folder not found: {root_path}")

    scan = ProjectScan(root_path=str(root))
    file_types: Dict[str, int] = {}
    directories: Dict[str, int] = {}

    for dirpath, dirnames, filenames in os.walk(root):
        # Skip hidden and build directories
        dirnames[:] = [
            d for d in dirnames
            if not d.startswith(".") and d not in {"__pycache__", "node_modules", ".git"}
        ]
        rel_dir = os.path.relpath(dirpath, root)
        dir_count = len(filenames)
        if rel_dir != ".":
            directories[rel_dir + "/"] = dir_count

        for fname in filenames:
            fpath = os.path.join(dirpath, fname)
            ext = os.path.splitext(fname)[1].lower()
            file_types[ext] = file_types.get(ext, 0) + 1
            scan.total_files += 1

            # Count lines for RTL files
            if ext in RTL_EXTENSIONS:
                try:
                    with open(fpath, "r", encoding="utf-8", errors="ignore") as f:
                        line_count = sum(1 for _ in f)
                    scan.total_lines += line_count
                except OSError:
                    pass

            # Classify files
            rel_path = os.path.relpath(fpath, root)
            fname_lower = fname.lower()

            if ext in FILELIST_EXTENSIONS:
                scan.filelist_files.append(rel_path)
            elif ext in RTL_EXTENSIONS:
                if any(p in fname_lower for p in UVM_PATTERNS):
                    scan.uvm_files.append(rel_path)
                elif any(p in fname_lower for p in FORMAL_PATTERNS):
                    scan.formal_files.append(rel_path)
                elif any(p in fname_lower for p in TB_PATTERNS):
                    scan.testbench_files.append(rel_path)
                else:
                    scan.rtl_files.append(rel_path)
            elif ext in SPEC_EXTENSIONS:
                # RTL uploaded as .txt (e.g. syn_fifo.txt) must still be parsed.
                if _file_contains_verilog_module(fpath):
                    scan.rtl_files.append(rel_path)
                else:
                    scan.spec_files.append(rel_path)

    scan.file_types = file_types
    scan.directories = directories

    # Estimate complexity
    if scan.total_lines < 500:
        scan.estimated_complexity = "small"
    elif scan.total_lines < 5000:
        scan.estimated_complexity = "medium"
    elif scan.total_lines < 50000:
        scan.estimated_complexity = "large"
    else:
        scan.estimated_complexity = "very_large"

    logger.info(
        f"Project scan: {scan.total_files} files, {scan.total_lines} lines, "
        f"{len(scan.rtl_files)} RTL, {len(scan.spec_files)} specs — "
        f"complexity: {scan.estimated_complexity}"
    )
    return scan


# ═══════════════════════════════════════════════════════════════════════
# Phase 2: Structural Index (Zero LLM — Regex Parsing)
# ═══════════════════════════════════════════════════════════════════════

# Regex patterns for Verilog/SystemVerilog structural parsing
RE_MODULE = re.compile(
    r"^\s*module\s+(\w+)\s*", re.MULTILINE
)
RE_ENDMODULE = re.compile(r"^\s*endmodule", re.MULTILINE)
RE_PORT = re.compile(
    r"^\s*(input|output|inout)\s+(wire|reg|logic)?\s*"
    r"(?:\[([^\]]+)\])?\s*(\w+)",
    re.MULTILINE,
)
RE_PORT_DECL = re.compile(
    r"(?m)^\s*(input|output|inout)\b([^;]*);"
)
RE_PARAMETER = re.compile(
    r"^\s*(?:parameter|localparam)\s+(?:\w+\s+)?(\w+)\s*=\s*([^;,\)]+)",
    re.MULTILINE,
)
RE_INSTANTIATION = re.compile(
    r"^\s*(\w+)\s+(?:#\s*\([^)]*\)\s*)?(\w+)\s*\(",
    re.MULTILINE,
)
RE_ALWAYS = re.compile(
    r"^\s*(always_ff|always_comb|always_latch|always)\s*@?\s*\(?([^)]*)\)?",
    re.MULTILINE,
)
RE_FSM_ENUM = re.compile(
    r"enum\s+\w*\s*\{([^}]+)\}",
    re.MULTILINE,
)
RE_CASE_STATE = re.compile(
    r"case\s*\(\s*(\w+)\s*\)",
    re.MULTILINE,
)
RE_CASE_BLOCK = re.compile(
    r"case\s*\(\s*(\w+)\s*\)([\s\S]*?)endcase",
    re.MULTILINE,
)

# ── New enrichment patterns ──────────────────────────────────────────

# Register write: if (wr_en) reg_name <= data;  OR  reg[addr] <= data;
RE_REG_WRITE = re.compile(
    r"if\s*\(\s*(\w+)\s*\)\s*(\w+)\s*(?:\[\s*(\w+)\s*\])?\s*<=\s*(\w+)",
    re.MULTILINE,
)

# Register read: data <= reg_name;  OR  data <= reg[addr];
RE_REG_READ = re.compile(
    r"(\w+)\s*<=?\s*(\w+)\s*\[\s*(\w+)\s*\]",
    re.MULTILINE,
)

# Register write inside a guarded begin/end block:
#   if (wr_en) begin
#     regs[addr] <= wr_data;
#   end
RE_REG_WRITE_BLOCK = re.compile(
    r"if\s*\(\s*([^)]+?)\s*\)\s*begin(?P<body>[\s\S]{0,1200}?)\bend\b",
    re.MULTILINE,
)

RE_NB_ASSIGN = re.compile(
    r"\b(\w+)\s*(?:\[\s*([^\]]+?)\s*\])?\s*<=\s*([^;]+?)\s*;",
    re.MULTILINE,
)

RE_SIGNAL_DECL = re.compile(
    r"\b(?:logic|reg|bit)\s*(\[[^\]]+\])?\s+([^;]+);",
    re.MULTILINE,
)

# Existing assertions: assert property (...);  assume property (...);  cover property (...);
RE_ASSERT_PROPERTY = re.compile(
    r"(assert|assume|cover)\s+property\s*\(([^;]{1,500})\)\s*;",
    re.MULTILINE | re.DOTALL,
)

# Inline assertions: assert (...) else ...;
RE_ASSERT_INLINE = re.compile(
    r"assert\s*\(([^;]{1,300})\)",
    re.MULTILINE,
)

# CDC synchronizer pattern: 2-stage FF chain across clock domains
RE_CDC_SYNC = re.compile(
    r"(\w+_sync(?:_r)?|\w+_meta|\w+_ff[12])\s*<=\s*(\w+)",
    re.MULTILINE,
)

# Common Verilog keywords that aren't module instantiations
def _file_contains_verilog_module(file_path: str) -> bool:
    """True when a text file contains at least one module/endmodule pair."""
    try:
        sample = Path(file_path).read_text(encoding="utf-8", errors="ignore")[:250_000]
    except OSError:
        return False
    sample = sample.lstrip("\ufeff")
    stripped = _strip_verilog_comments_preserve_lines(sample)
    return bool(RE_MODULE.search(stripped) and RE_ENDMODULE.search(stripped))


VERILOG_KEYWORDS = {
    "module", "endmodule", "input", "output", "inout", "wire", "reg",
    "logic", "assign", "always", "always_ff", "always_comb", "always_latch",
    "initial", "begin", "end", "if", "else", "case", "endcase", "for",
    "while", "generate", "endgenerate", "function", "endfunction", "task",
    "endtask", "parameter", "localparam", "integer", "real", "genvar",
    "posedge", "negedge", "or", "and", "not", "buf", "xor",
}

PORT_TYPE_TOKENS = {
    "wire", "reg", "logic", "bit", "tri", "tri0", "tri1", "wand", "wor",
    "supply0", "supply1", "var",
}
PORT_QUALIFIER_TOKENS = {
    "signed", "unsigned", "automatic", "static",
}


def _strip_verilog_comments_preserve_lines(text: str) -> str:
    """Remove comments while preserving line numbers for source references."""
    def _block_repl(match: re.Match) -> str:
        return "\n" * match.group(0).count("\n")

    text = re.sub(r"/\*[\s\S]*?\*/", _block_repl, text)
    return re.sub(r"//.*", "", text)


def _split_top_level_commas(text: str) -> List[str]:
    """Split comma lists while ignoring commas inside brackets/parentheses."""
    items: List[str] = []
    start = 0
    bracket_depth = 0
    paren_depth = 0
    brace_depth = 0
    for index, char in enumerate(text):
        if char == "[":
            bracket_depth += 1
        elif char == "]" and bracket_depth:
            bracket_depth -= 1
        elif char == "(":
            paren_depth += 1
        elif char == ")" and paren_depth:
            paren_depth -= 1
        elif char == "{":
            brace_depth += 1
        elif char == "}" and brace_depth:
            brace_depth -= 1
        elif char == "," and not (bracket_depth or paren_depth or brace_depth):
            items.append(text[start:index].strip())
            start = index + 1
    tail = text[start:].strip()
    if tail:
        items.append(tail)
    return items


def _find_matching_paren(text: str, open_index: int) -> int:
    depth = 0
    for index in range(open_index, len(text)):
        char = text[index]
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return index
    return -1


def _extract_module_port_header(mod_content: str) -> Tuple[str, int]:
    """Return the raw module port list and its offset inside mod_content."""
    module_match = RE_MODULE.search(mod_content)
    if not module_match:
        return "", -1

    index = module_match.end()
    while index < len(mod_content) and mod_content[index].isspace():
        index += 1

    if index < len(mod_content) and mod_content[index] == "#":
        index += 1
        while index < len(mod_content) and mod_content[index].isspace():
            index += 1
        if index < len(mod_content) and mod_content[index] == "(":
            param_end = _find_matching_paren(mod_content, index)
            if param_end == -1:
                return "", -1
            index = param_end + 1

    while index < len(mod_content) and mod_content[index].isspace():
        index += 1
    if index >= len(mod_content) or mod_content[index] != "(":
        return "", -1

    port_start = index + 1
    port_end = _find_matching_paren(mod_content, index)
    if port_end == -1:
        return "", -1
    return mod_content[port_start:port_end], port_start


def _parse_port_decl_item(
    item: str,
    direction: str,
    line: int,
    default_port_type: str = "logic",
    default_bus_range: str = "",
) -> Tuple[Optional[Dict[str, Any]], str, str]:
    """Parse one declared port item, carrying type/range for comma continuations."""
    raw = item.strip()
    if not raw:
        return None, default_port_type, default_bus_range

    raw = re.sub(r"\(\*[\s\S]*?\*\)", " ", raw).strip()
    raw = re.split(r"\s*=\s*", raw, maxsplit=1)[0].strip()

    range_match = re.search(r"\[[^\]]+\]", raw)
    bus_range = range_match.group(0) if range_match else default_bus_range

    port_type = default_port_type or "logic"
    for token in re.findall(r"\b[a-zA-Z_]\w*\b", raw):
        lower = token.lower()
        if lower in PORT_TYPE_TOKENS:
            port_type = lower
            break

    without_ranges = re.sub(r"\[[^\]]+\]", " ", raw)
    tokens = re.findall(r"\b[a-zA-Z_]\w*\b", without_ranges)
    name = ""
    for token in reversed(tokens):
        lower = token.lower()
        if lower in PORT_TYPE_TOKENS or lower in PORT_QUALIFIER_TOKENS:
            continue
        if lower in {"input", "output", "inout"}:
            continue
        name = token
        break

    if not name or name.lower() in VERILOG_KEYWORDS:
        return None, port_type, bus_range

    return {
        "name": name,
        "direction": direction,
        "port_type": port_type or "logic",
        "bus_range": bus_range,
        "width": _parse_width(bus_range),
        "line": line,
    }, port_type, bus_range


def _parse_port_decl_list(
    direction: str,
    decl_body: str,
    line: int,
    default_port_type: str = "logic",
    default_bus_range: str = "",
) -> List[Dict[str, Any]]:
    ports: List[Dict[str, Any]] = []
    port_type = default_port_type
    bus_range = default_bus_range
    for item in _split_top_level_commas(decl_body):
        parsed, port_type, bus_range = _parse_port_decl_item(
            item,
            direction,
            line,
            port_type,
            bus_range,
        )
        if parsed:
            ports.append(parsed)
    return ports


def _parse_ansi_header_ports(header: str, base_line: int) -> List[Dict[str, Any]]:
    """Parse ANSI-style ports from a module header."""
    ports: List[Dict[str, Any]] = []
    direction = ""
    port_type = "logic"
    bus_range = ""
    for item in _split_top_level_commas(header):
        candidate = item.strip()
        line = base_line
        match = re.match(r"^(input|output|inout)\b([\s\S]*)$", candidate)
        if match:
            direction = match.group(1)
            candidate = match.group(2).strip()
            port_type = "logic"
            bus_range = ""
        if not direction:
            continue
        parsed, port_type, bus_range = _parse_port_decl_item(
            candidate,
            direction,
            line,
            port_type,
            bus_range,
        )
        if parsed:
            ports.append(parsed)
    return ports


def _merge_port(ports: List[Dict[str, Any]], seen: Dict[str, int], port: Dict[str, Any]) -> None:
    name = str(port.get("name") or "")
    if not name:
        return
    if name not in seen:
        seen[name] = len(ports)
        ports.append(port)
        return

    existing = ports[seen[name]]
    for key in ("direction", "port_type", "bus_range"):
        if port.get(key) and (not existing.get(key) or existing.get(key) == "logic"):
            existing[key] = port[key]
    if int(existing.get("width") or 1) <= 1 and int(port.get("width") or 1) > 1:
        existing["width"] = port["width"]
    if port.get("line"):
        existing["line"] = min(int(existing.get("line") or port["line"]), int(port["line"]))


def _clean_state_name(raw_state: str) -> str:
    """Normalize enum/case labels into plain state names."""
    state = re.split(r"\s*=", str(raw_state or "").strip(), maxsplit=1)[0].strip()
    return re.sub(r"[^a-zA-Z0-9_]", "", state)


def _populate_fsm_transition_candidates(
    fsm_candidates: List[Dict[str, Any]],
    mod_content: str,
    mod_start: int,
) -> None:
    """Extract simple case-based transition arcs without using the LLM."""
    if not fsm_candidates:
        return

    def _candidate_for_signal(signal: str) -> Optional[Dict[str, Any]]:
        for candidate in fsm_candidates:
            if candidate.get("state_signal") == signal:
                return candidate
        return None

    def _last_condition(prefix: str) -> str:
        matches = re.findall(r"\bif\s*\(([^;\n]{1,240})\)", prefix, re.MULTILINE)
        return re.sub(r"\s+", " ", matches[-1].strip()) if matches else "default"

    branch_re = re.compile(
        r"(?ms)^\s*(?P<state>[a-zA-Z_]\w*)\s*:\s*(?:begin)?(?P<body>.*?)(?=^\s*(?:[a-zA-Z_]\w*|default)\s*:|\Z)"
    )
    state_assign_re = re.compile(
        r"\b(?P<target>[a-zA-Z_]\w*state\w*|next_[a-zA-Z_]\w*)\s*(?:<=|=)\s*(?P<next>[a-zA-Z_]\w*)\s*;",
        re.IGNORECASE,
    )

    for case_match in RE_CASE_BLOCK.finditer(mod_content):
        state_signal = case_match.group(1)
        case_body = case_match.group(2)
        candidate = _candidate_for_signal(state_signal)
        if candidate is None:
            candidate = {"state_signal": state_signal, "type": "case"}
            fsm_candidates.append(candidate)

        transitions = candidate.setdefault("transitions", [])
        states = set(candidate.get("states") or [])
        seen = {
            (
                str(item.get("from", "")),
                str(item.get("to", "")),
                str(item.get("condition", "")),
            )
            for item in transitions
            if isinstance(item, dict)
        }
        for branch in branch_re.finditer(case_body):
            current_state = _clean_state_name(branch.group("state"))
            if not current_state or current_state.lower() == "default":
                continue
            states.add(current_state)
            branch_body = branch.group("body") or ""
            for assign in state_assign_re.finditer(branch_body):
                target_signal = assign.group("target")
                if target_signal != state_signal and "state" not in target_signal.lower():
                    continue
                next_state = _clean_state_name(assign.group("next"))
                if not next_state:
                    continue
                states.add(next_state)
                condition = _last_condition(branch_body[:assign.start()])
                key = (current_state, next_state, condition)
                if key in seen:
                    continue
                seen.add(key)
                line = mod_start + mod_content[: case_match.start(2) + branch.start() + assign.start()].count("\n")
                transitions.append({
                    "from": current_state,
                    "to": next_state,
                    "condition": condition,
                    "line": str(line),
                })
        if states:
            candidate["states"] = sorted(states)
    _merge_enum_case_fsm_candidates(fsm_candidates)


def _merge_enum_case_fsm_candidates(fsm_candidates: List[Dict[str, Any]]) -> None:
    """Merge enum-only FSM candidates into matching case-based candidates."""
    case_candidates = [
        candidate
        for candidate in fsm_candidates
        if isinstance(candidate, dict) and candidate.get("state_signal")
    ]
    for enum_candidate in list(fsm_candidates):
        if not isinstance(enum_candidate, dict) or enum_candidate.get("state_signal"):
            continue
        enum_states = {
            _clean_state_name(state)
            for state in enum_candidate.get("states", [])
            if _clean_state_name(state)
        }
        if not enum_states:
            continue
        for case_candidate in case_candidates:
            case_states = {
                _clean_state_name(state)
                for state in case_candidate.get("states", [])
                if _clean_state_name(state)
            }
            if enum_states & case_states:
                case_candidate["states"] = sorted(enum_states | case_states)
                fsm_candidates.remove(enum_candidate)
                break


def _parse_rtl_structure_regex(file_path: str) -> Dict[str, Any]:
    """
    Parse a single RTL file for structural information.
    Returns module name, ports, parameters, instantiations, FSMs.
    No LLM needed — pure regex parsing.
    """
    try:
        with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()
    except OSError as e:
        logger.warning(f"Cannot read {file_path}: {e}")
        return {}

    content = content.lstrip("\ufeff")
    parse_content = _strip_verilog_comments_preserve_lines(content)
    lines = content.splitlines()
    result: Dict[str, Any] = {
        "file": file_path,
        "line_count": len(lines),
        "modules": [],
    }

    # Find all modules
    for m in RE_MODULE.finditer(parse_content):
        mod_name = m.group(1)
        mod_start = parse_content[:m.start()].count("\n") + 1

        # Find endmodule
        end_match = RE_ENDMODULE.search(parse_content, m.end())
        mod_end = parse_content[:end_match.start()].count("\n") + 1 if end_match else len(lines)
        mod_content = parse_content[m.start():end_match.end() if end_match else len(parse_content)]

        # Parse ports
        ports: List[Dict[str, Any]] = []
        seen_ports: Dict[str, int] = {}

        header, header_offset = _extract_module_port_header(mod_content)
        body_search_offset = 0
        if header:
            header_line = mod_start + mod_content[:header_offset].count("\n")
            for port in _parse_ansi_header_ports(header, header_line):
                _merge_port(ports, seen_ports, port)
            header_close = _find_matching_paren(mod_content, header_offset - 1)
            header_semi = mod_content.find(";", header_close) if header_close != -1 else -1
            if header_semi != -1:
                body_search_offset = header_semi + 1

        for pm in RE_PORT_DECL.finditer(mod_content, body_search_offset):
            direction = pm.group(1)
            decl_body = pm.group(2)
            line = mod_start + mod_content[:pm.start()].count("\n")
            for port in _parse_port_decl_list(direction, decl_body, line):
                _merge_port(ports, seen_ports, port)

        # Parse parameters
        parameters = []
        for pm in RE_PARAMETER.finditer(mod_content):
            parameters.append({
                "name": pm.group(1),
                "default_value": pm.group(2).strip(),
            })

        # Parse instantiations
        instantiations = []
        for im in RE_INSTANTIATION.finditer(mod_content):
            inst_module = im.group(1)
            inst_name = im.group(2)
            if inst_module.lower() not in VERILOG_KEYWORDS:
                instantiations.append({
                    "module": inst_module,
                    "instance": inst_name,
                })

        # Detect FSMs
        fsm_candidates = []
        for em in RE_FSM_ENUM.finditer(mod_content):
            states = [
                state
                for state in (_clean_state_name(s) for s in em.group(1).split(","))
                if state
            ]
            fsm_candidates.append({"states": states, "type": "enum"})
        for cm in RE_CASE_STATE.finditer(mod_content):
            signal = cm.group(1)
            if signal.lower() not in {"1'b0", "1'b1", "0", "1"}:
                fsm_candidates.append({"state_signal": signal, "type": "case"})
        _populate_fsm_transition_candidates(fsm_candidates, mod_content, mod_start)

        # Count always blocks
        always_blocks = []
        for am in RE_ALWAYS.finditer(mod_content):
            always_blocks.append({
                "type": am.group(1),
                "sensitivity": am.group(2).strip() if am.group(2) else "",
            })

        # ── New enrichment extractions ────────────────────────────

        # Extract register write patterns
        register_fields = _extract_register_fields(mod_content, mod_start, mod_name)

        # Extract existing assertions from RTL
        existing_assertions = _extract_existing_assertions(mod_content, mod_start)

        # Detect CDC synchronizer patterns
        cdc_crossings = _detect_cdc_crossings(
            mod_content, always_blocks, ports, mod_start
        )

        result["modules"].append({
            "name": mod_name,
            "line_range": (mod_start, mod_end),
            "ports": ports,
            "parameters": parameters,
            "instantiations": instantiations,
            "always_blocks": always_blocks,
            "fsm_candidates": fsm_candidates,
            "register_fields": register_fields,
            "existing_assertions": existing_assertions,
            "cdc_crossings": cdc_crossings,
        })

    return result


def parse_rtl_structure(file_path: str) -> Dict[str, Any]:
    """
    Parse a single RTL file, preferring Slang and falling back to regex when enabled.

    Fallback output is marked as ``parser_engine=regex_fallback`` so downstream
    agents can treat it as degraded, non-compiler-grounded structure.
    """
    path = Path(file_path).resolve()
    root = path.parent
    try:
        analyzer = SlangStructuralAnalyzer()
        result = analyzer.analyze_project(str(root), [path.name])
    except SlangAnalysisError as exc:
        if not _regex_parser_fallback_enabled():
            raise
        logger.warning("Slang parse unavailable for %s; using regex fallback: %s", path, exc)
        parsed = _parse_rtl_structure_regex(str(path))
        if not parsed.get("modules"):
            raise
        parsed["parser_engine"] = "regex_fallback"
        parsed["slang_version"] = ""
        parsed["diagnostics"] = [
            {
                "severity": "warning",
                "message": f"Slang parse unavailable; used regex fallback: {exc}",
                "parser_engine": "regex_fallback",
            }
        ]
        for module in parsed.get("modules", []):
            if isinstance(module, dict):
                module.setdefault("definition_kind", "Module")
                module.setdefault("file", str(path))
                module["parser_engine"] = "regex_fallback"
        return parsed

    if not result.module_index:
        if _regex_parser_fallback_enabled():
            parsed = _parse_rtl_structure_regex(str(path))
            if parsed.get("modules"):
                parsed["parser_engine"] = "regex_fallback"
                parsed["slang_version"] = result.parser_version
                parsed["diagnostics"] = [
                    {
                        "severity": "warning",
                        "message": (
                            "Slang produced no module/interface/package definitions; "
                            "used regex fallback."
                        ),
                        "parser_engine": "regex_fallback",
                    }
                ]
                for module in parsed.get("modules", []):
                    if isinstance(module, dict):
                        module.setdefault("definition_kind", "Module")
                        module.setdefault("file", str(path))
                        module["parser_engine"] = "regex_fallback"
                return parsed
        raise SlangAnalysisError(
            f"Slang parsed {path.name} but found no module, interface, or package definitions."
        )
    modules = []
    for module in result.module_index.values():
        module_file = str(module.get("file") or "")
        if not module_file or Path(module_file).name == path.name:
            modules.append(module)
    return {
        "file": str(path),
        "line_count": sum(
            int(module.get("line_count") or 0)
            for module in modules
        ),
        "modules": modules,
        "parser_engine": result.parser_engine,
        "slang_version": result.parser_version,
        "diagnostics": result.diagnostics,
    }


# ═══════════════════════════════════════════════════════════════════════
# Phase 2b: Enrichment Extractors (Zero LLM — Regex)
# ═══════════════════════════════════════════════════════════════════════


def _extract_register_fields(
    mod_content: str,
    mod_start: int,
    module_name: str = "",
) -> List[Dict[str, Any]]:
    """Extract register write patterns from always_ff blocks.

    Detects patterns like:
        if (wr_en) cfg_reg <= wr_data;
        if (wr_en) regs[addr] <= wr_data;

    Returns a list of register field dicts (later converted to RegisterField).
    """
    registers: Dict[str, Dict[str, Any]] = {}
    widths: Dict[str, int] = {}
    reset_values: Dict[str, str] = {}

    for decl in RE_SIGNAL_DECL.finditer(mod_content):
        width = _parse_width(decl.group(1) or "")
        names_part = decl.group(2)
        for raw_name in names_part.split(","):
            name_match = re.search(r"\b(\w+)\b", raw_name.strip())
            if name_match:
                widths[name_match.group(1)] = width

    for reset_match in re.finditer(
        r"if\s*\([^)]*(?:rst|reset)[^)]*\)\s*(?:begin)?(?P<body>[\s\S]{0,800}?)(?:end|else)",
        mod_content,
        re.IGNORECASE | re.MULTILINE,
    ):
        for assign in RE_NB_ASSIGN.finditer(reset_match.group("body")):
            reset_values.setdefault(assign.group(1), assign.group(3).strip())

    def _is_probable_write_enable(expr: str) -> bool:
        lower = expr.lower()
        if "rst" in lower or "reset" in lower:
            return False
        if any(op in expr for op in ("<=", ">=", "==", "!=", "&&", "||")):
            return False
        return bool(re.search(r"\b(?:wr|write|we|enable|en|valid|load|set)\w*\b", lower))

    def _record_register(
        *,
        reg_name: str,
        write_enable: str,
        addr_signal: str,
        data_signal: str,
        line: int,
    ) -> None:
        if not reg_name or reg_name.lower() in VERILOG_KEYWORDS:
            return
        key = reg_name if not addr_signal else f"{reg_name}[{addr_signal}]"
        if key in registers:
            return
        registers[key] = {
            "name": reg_name,
            "bank": module_name,
            "offset": len(registers) * 4,
            "offset_basis": "module_relative",
            "width": widths.get(reg_name, 32),
            "reset_value": reset_values.get(reg_name, "0"),
            "write_enable": write_enable,
            "addr_signal": addr_signal,
            "data_signal": data_signal,
            "line": line,
            "access": "RW",
            "confidence": 0.8,
        }

    for m in RE_REG_WRITE.finditer(mod_content):
        write_enable = m.group(1)
        reg_name = m.group(2)
        addr_signal = m.group(3) or ""
        data_signal = m.group(4)
        line = mod_start + mod_content[:m.start()].count("\n")
        if not _is_probable_write_enable(write_enable):
            continue
        _record_register(
            reg_name=reg_name,
            write_enable=write_enable,
            addr_signal=addr_signal,
            data_signal=data_signal.strip(),
            line=line,
        )

    for block in RE_REG_WRITE_BLOCK.finditer(mod_content):
        write_enable = re.sub(r"\s+", " ", block.group(1).strip())
        if not _is_probable_write_enable(write_enable):
            continue
        body = block.group("body")
        for assign in RE_NB_ASSIGN.finditer(body):
            reg_name = assign.group(1)
            addr_signal = (assign.group(2) or "").strip()
            data_signal = assign.group(3).strip()
            line = mod_start + mod_content[: block.start("body") + assign.start()].count("\n")
            _record_register(
                reg_name=reg_name,
                write_enable=write_enable,
                addr_signal=addr_signal,
                data_signal=data_signal,
                line=line,
            )

    # Also detect read patterns to mark RO registers
    read_targets: set = set()
    for m in RE_REG_READ.finditer(mod_content):
        read_targets.add(m.group(2))

    # Mark registers that are only read (not written) as RO
    # (Note: these would only appear if we find them via different patterns)

    return list(registers.values())


def _extract_existing_assertions(
    mod_content: str,
    mod_start: int,
) -> List[Dict[str, str]]:
    """Extract assert/assume/cover property statements from RTL.

    These are pre-existing SVA properties the designer already wrote.
    They inform the formal agent and help avoid duplicate generation.
    """
    assertions: List[Dict[str, str]] = []
    seen_bodies: set = set()

    # Formal properties: assert/assume/cover property (...)
    for m in RE_ASSERT_PROPERTY.finditer(mod_content):
        prop_type = m.group(1).lower()
        body = m.group(2).strip()
        body_clean = re.sub(r"\s+", " ", body)
        if body_clean in seen_bodies:
            continue
        seen_bodies.add(body_clean)
        line = mod_start + mod_content[:m.start()].count("\n")
        assertions.append({
            "type": prop_type,
            "body": body_clean[:300],
            "line": str(line),
        })

    # Inline assertions: assert (condition)
    for m in RE_ASSERT_INLINE.finditer(mod_content):
        body = m.group(1).strip()
        body_clean = re.sub(r"\s+", " ", body)
        # Skip if we already have this as a property assertion
        if body_clean in seen_bodies:
            continue
        # Skip trivial assertions
        if len(body_clean) < 5:
            continue
        seen_bodies.add(body_clean)
        line = mod_start + mod_content[:m.start()].count("\n")
        assertions.append({
            "type": "assert_inline",
            "body": body_clean[:300],
            "line": str(line),
        })

    return assertions


def _detect_cdc_crossings(
    mod_content: str,
    always_blocks: List[Dict[str, Any]],
    ports: List[Dict[str, Any]],
    mod_start: int,
) -> List[Dict[str, str]]:
    """Detect CDC synchronizer patterns and multi-clock-domain crossings.

    Detects:
    1. Naming convention patterns: *_sync, *_meta, *_ff1/*_ff2
    2. Multiple clock domains in always_ff sensitivity lists
    3. Signals driven by one clock and read by another
    """
    crossings: List[Dict[str, str]] = []
    seen: set = set()

    # Strategy 1: Naming-convention synchronizers
    for m in RE_CDC_SYNC.finditer(mod_content):
        sync_reg = m.group(1)
        source_signal = m.group(2)
        key = f"{source_signal}->{sync_reg}"
        if key not in seen:
            seen.add(key)
            line = mod_start + mod_content[:m.start()].count("\n")
            crossings.append({
                "type": "synchronizer",
                "source": source_signal,
                "dest": sync_reg,
                "line": str(line),
            })

    # Strategy 2: Multiple clock domains detected from always_ff blocks
    clock_signals: set = set()
    for ab in always_blocks:
        if ab["type"] in ("always_ff", "always"):
            sens = ab.get("sensitivity", "")
            for clk_match in re.finditer(r"posedge\s+(\w+)|negedge\s+(\w+)", sens):
                clk = clk_match.group(1) or clk_match.group(2)
                # Filter out reset signals
                if clk and not any(r in clk.lower() for r in ("rst", "reset")):
                    clock_signals.add(clk)

    if len(clock_signals) > 1:
        crossings.append({
            "type": "multi_clock_domain",
            "clocks": ", ".join(sorted(clock_signals)),
            "note": f"Module uses {len(clock_signals)} clock domains: {', '.join(sorted(clock_signals))}",
        })

    return crossings


def _build_regex_structural_index(
    root_path: str,
    rtl_files: List[str],
    *,
    reason: str = "",
) -> Tuple[Dict[str, Any], Dict[str, List[str]], Dict[str, Any]]:
    """Build degraded structural index from the legacy regex parser."""
    root = Path(root_path)
    module_index: Dict[str, Any] = {}
    symbol_table: Dict[str, List[str]] = {}

    for rel_path in rtl_files:
        abs_path = Path(rel_path)
        if not abs_path.is_absolute():
            abs_path = root / rel_path
        parsed = _parse_rtl_structure_regex(str(abs_path))
        if not parsed or not parsed.get("modules"):
            continue
        try:
            display_path = str(abs_path.resolve().relative_to(root.resolve()))
        except Exception:
            display_path = str(rel_path)

        for mod in parsed.get("modules", []):
            if not isinstance(mod, dict):
                continue
            mod_name = str(mod.get("name") or "").strip()
            if not mod_name:
                continue
            normalized = dict(mod)
            normalized.setdefault("definition_kind", "Module")
            normalized["file"] = display_path
            normalized["line_count"] = parsed.get("line_count", 0)
            normalized["internal_symbols"] = sorted({
                str(item.get("name") or "")
                for item in normalized.get("register_fields", []) or []
                if isinstance(item, dict) and item.get("name")
            })
            normalized["parser_engine"] = "regex_fallback"
            module_index[mod_name] = normalized

            symbols: set[str] = set()
            symbols.update(
                str(port.get("name") or "")
                for port in normalized.get("ports", []) or []
                if isinstance(port, dict)
            )
            symbols.update(
                str(param.get("name") or "")
                for param in normalized.get("parameters", []) or []
                if isinstance(param, dict)
            )
            symbols.update(normalized.get("internal_symbols", []) or [])
            symbol_table[mod_name] = sorted(symbol for symbol in symbols if symbol)

    if not module_index:
        raise SlangAnalysisError(
            "Slang was unavailable and regex fallback found no RTL modules."
        )

    metadata = {
        "parser_engine": "regex_fallback",
        "parser_version": "",
        "parser_diagnostics": [
            {
                "severity": "warning",
                "message": (
                    "Slang parser unavailable or failed; TruthCore used degraded "
                    f"regex fallback. {reason}".strip()
                ),
                "parser_engine": "regex_fallback",
            }
        ],
    }
    return module_index, symbol_table, metadata


def build_structural_index(
    root_path: str,
    rtl_files: List[str],
    *,
    target_module: Optional[str] = None,
    return_metadata: bool = False,
) -> Tuple[Dict[str, Any], Dict[str, List[str]]] | Tuple[Dict[str, Any], Dict[str, List[str]], Dict[str, Any]]:
    """
    Build complete structural index for all RTL files using Slang first.

    If Slang is missing/failing and fallback is enabled, return degraded
    regex-derived structure with ``parser_engine=regex_fallback`` metadata.
    Regex is also used after successful Slang parsing for supplemental
    enrichment fields that are not yet normalized from the Slang AST.
    Returns: (module_index, symbol_table)
    """
    try:
        analyzer = SlangStructuralAnalyzer()
        slang_result = analyzer.analyze_project(
            root_path,
            rtl_files,
            top_module=target_module,
        )
    except SlangAnalysisError as exc:
        if not _regex_parser_fallback_enabled():
            raise
        logger.warning(
            "Slang structural analysis unavailable for %s; using regex fallback: %s",
            root_path,
            exc,
        )
        module_index, symbol_table, metadata = _build_regex_structural_index(
            root_path,
            rtl_files,
            reason=str(exc),
        )
        if return_metadata:
            return module_index, symbol_table, metadata
        return module_index, symbol_table

    module_index: Dict[str, Any] = slang_result.module_index
    symbol_table: Dict[str, List[str]] = slang_result.symbol_table
    if not module_index:
        if _regex_parser_fallback_enabled():
            module_index, symbol_table, metadata = _build_regex_structural_index(
                root_path,
                rtl_files,
                reason="Slang produced no module, interface, or package definitions.",
            )
            if return_metadata:
                return module_index, symbol_table, metadata
            return module_index, symbol_table
        raise SlangAnalysisError(
            "Slang parsed the RTL but found no module, interface, or package definitions."
        )

    root = Path(root_path)
    for rel_path in rtl_files:
        abs_path = str(root / rel_path)
        parsed = _parse_rtl_structure_regex(abs_path)
        if not parsed or not parsed.get("modules"):
            continue

        for mod in parsed["modules"]:
            mod_name = mod["name"]
            if mod_name not in module_index:
                continue
            slang_mod = module_index[mod_name]
            slang_mod["file"] = slang_mod.get("file") or rel_path
            slang_mod["line_count"] = slang_mod.get("line_count") or parsed.get("line_count", 0)
            slang_mod["line_range"] = slang_mod.get("line_range") or mod.get("line_range", (0, 0))
            for key in (
                "always_blocks",
                "fsm_candidates",
                "register_fields",
                "existing_assertions",
                "cdc_crossings",
            ):
                if mod.get(key):
                    slang_mod[key] = mod.get(key)

            # Build symbol table entry
            symbols = list(symbol_table.get(mod_name, []))
            symbols.extend(r["name"] for r in mod.get("register_fields", []))
            symbol_table[mod_name] = sorted({s for s in symbols if s})

    metadata = {
        "parser_engine": slang_result.parser_engine,
        "parser_version": slang_result.parser_version,
        "parser_diagnostics": slang_result.diagnostics,
    }
    if return_metadata:
        return module_index, symbol_table, metadata
    return module_index, symbol_table


def build_hierarchy_tree(module_index: Dict[str, Any]) -> Dict[str, List[str]]:
    """Build parent → children hierarchy from instantiations."""
    tree: Dict[str, List[str]] = {}
    for mod_name, mod_info in module_index.items():
        children = [
            inst["module"] for inst in mod_info.get("instantiations", [])
            if inst["module"] in module_index
        ]
        if children:
            tree[mod_name] = children
    return tree


def find_top_module(
    module_index: Dict[str, Any],
    hierarchy: Dict[str, List[str]],
) -> str:
    """Find the top-level module (instantiated by nobody)."""
    all_modules = set(module_index.keys())
    instantiated = set()
    for children in hierarchy.values():
        instantiated.update(children)
    top_candidates = all_modules - instantiated
    if len(top_candidates) == 1:
        return top_candidates.pop()
    if not top_candidates:
        logger.warning("No unambiguous top module found; using module with most ports")
        top_candidates = all_modules
    # Heuristic: pick the one with the most ports
    best = ""
    best_ports = -1
    for c in top_candidates:
        n_ports = len(module_index[c].get("ports", []))
        if n_ports > best_ports:
            best = c
            best_ports = n_ports
    return best


# ═══════════════════════════════════════════════════════════════════════
# Phase 3-5: LLM-Assisted Analysis
# ═══════════════════════════════════════════════════════════════════════


async def build_block_model_with_llm(
    module_name: str,
    module_info: Dict[str, Any],
    rtl_content: str,
    spec_text: str,
    ai_client: Any,
) -> Dict[str, Any]:
    """
    Use LLM to analyze a single module and generate design understanding.
    Returns dict with description, protocols, requirements, etc.
    """
    # Build a focused prompt with structural facts
    ports_str = "\n".join(
        f"  {p['direction']} {p.get('port_type', 'logic')} "
        f"{p.get('bus_range', '')} {p['name']}"
        for p in module_info.get("ports", [])
    )
    params_str = "\n".join(
        f"  {p['name']} = {p['default_value']}"
        for p in module_info.get("parameters", [])
    )
    insts_str = "\n".join(
        f"  {i['module']} {i['instance']}"
        for i in module_info.get("instantiations", [])
    )
    fsm_str = ", ".join(
        str(f.get("states", f.get("state_signal", "")))
        for f in module_info.get("fsm_candidates", [])
    )

    # Include enrichment context from Phase 2b extractors
    reg_fields = module_info.get("register_fields", [])
    reg_str = "\n".join(
        f"  {r['name']} (wr_en={r.get('write_enable', '?')}, data={r.get('data_signal', '?')})"
        for r in reg_fields[:16]
    ) if reg_fields else "  (none detected)"

    existing_assertions = module_info.get("existing_assertions", [])
    assert_str = "\n".join(
        f"  {a['type']}: {a['body'][:120]}"
        for a in existing_assertions[:10]
    ) if existing_assertions else "  (none)"

    cdc_crossings = module_info.get("cdc_crossings", [])
    cdc_str = "\n".join(
        f"  {c.get('type', 'unknown')}: {c.get('source', '')} -> {c.get('dest', '')}"
        for c in cdc_crossings[:8]
    ) if cdc_crossings else "  (none detected)"

    prompt = f"""Analyze this RTL module for verification planning.

MODULE: {module_name}
FILE: {module_info.get('file', 'unknown')}
LINES: {module_info.get('line_range', (0, 0))}

PORTS (ground truth from parser):
{ports_str or '  (none found)'}

PARAMETERS:
{params_str or '  (none)'}

SUB-INSTANCES:
{insts_str or '  (none)'}

FSM CANDIDATES: {fsm_str or 'none'}

REGISTERS DETECTED (from parser):
{reg_str}

EXISTING ASSERTIONS (from parser):
{assert_str}

CDC CROSSINGS (from parser):
{cdc_str}

RTL SOURCE:
```systemverilog
{_rtl_excerpt_for_prompt(rtl_content)}
```

{f'SPECIFICATION EXCERPT:{chr(10)}{_spec_excerpt_for_prompt(spec_text)}' if spec_text else '(No spec provided)'}

Respond in this JSON format. JSON ONLY. The first character must be {{ and the last character must be }}:
{{
  "description": "What this module does (1-2 sentences)",
  "protocols": [{{"protocol": "name", "role": "master|slave", "port_group": ["port1"]}}],
  "clock_domains": [{{"name": "clk_name", "reset": "rst_name", "polarity": "active_low"}}],
  "fsms": [{{"name": "fsm_name", "states": ["S0","S1"], "state_signal": "sig"}}],
  "requirements": [{{"id": "REQ-001", "text": "requirement text", "priority": "high"}}],
  "constraints": ["legal input ranges, protocol ordering rules, timing bounds, or reset assumptions"],
  "expected_behaviors": [
    {{
      "id": "EB-001",
      "stimulus": {{"signal_name": "value"}},
      "expected_output": {{"signal_name": "value"}},
      "latency_cycles": 1,
      "precondition": "after_reset",
      "description": "When X is driven, Y should be observed"
    }}
  ],
  "transaction_flows": [
    {{
      "name": "flow_name",
      "protocol": "protocol_name_or_empty",
      "steps": [{{"phase": "step_name", "signals": {{"sig": "val"}}, "cycles": "1"}}],
      "description": "Step-by-step transaction sequence"
    }}
  ],
  "risks": ["risk description"],
  "open_questions": [{{"question": "What is X?", "blocking": false}}]
}}

RULES:
- ONLY use signal names that appear in PORTS above
- If uncertain, add an open_question instead of guessing
- Link requirements to specific port/signal behavior
- Extract constraints for valid address ranges, legal field values, protocol timing/order, and reset/clock assumptions
- For expected_behaviors: describe concrete input→output mappings with actual signal names and values
- For transaction_flows: describe step-by-step signal sequences for protocol transactions
- Use REGISTERS DETECTED and EXISTING ASSERTIONS context to inform your analysis
"""

    system_prompt = (
        "You are a senior verification engineer analyzing RTL for a mental model. "
        "Be precise. Only reference signals that exist in the structural facts. "
        "Respond with valid JSON only."
    )

    try:
        if hasattr(ai_client, "chat"):
            response = await ai_client.chat(system_prompt, prompt)
        else:
            # Fallback: return structure-only model
            return _structure_only_model(module_name, module_info, "missing_chat_method")

        # Parse JSON from response
        text = response if isinstance(response, str) else str(response)
        parsed = _parse_llm_json_object(text)
        if isinstance(parsed, dict):
            parsed["_llm_status"] = "parsed"
            return parsed

        logger.warning(
            "LLM response for %s was not parseable JSON; asking for one repair pass.",
            module_name,
        )
        repaired = await _repair_block_model_json(
            module_name=module_name,
            module_info=module_info,
            rtl_content=rtl_content,
            spec_text=spec_text,
            previous_response=text,
            ai_client=ai_client,
        )
        if isinstance(repaired, dict):
            repaired["_llm_status"] = "parsed"
            return repaired

        logger.warning(
            "LLM response for %s was not parseable JSON. response_excerpt=%r",
            module_name,
            text[:500],
        )
        return _structure_only_model(module_name, module_info, "structure_with_llm_attempt")

    except Exception as e:
        logger.warning(f"LLM analysis failed for {module_name}: {e}")
        return _structure_only_model(module_name, module_info, "llm_error")


def _parse_llm_json_object(text: str) -> Optional[Dict[str, Any]]:
    """Parse a JSON object from raw LLM output, including fenced JSON."""
    import json

    if not text or not text.strip():
        return None

    cleaned = text.strip()
    fence_match = re.search(
        r"```(?:json|JSON)?\s*([\s\S]*?)```",
        cleaned,
    )
    candidates = []
    if fence_match:
        candidates.append(fence_match.group(1).strip())
    candidates.append(cleaned)

    decoder = json.JSONDecoder()
    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            pass

        for match in re.finditer(r"\{", candidate):
            try:
                parsed, _end = decoder.raw_decode(candidate[match.start():])
            except Exception:
                continue
            if isinstance(parsed, dict):
                return parsed

    return None


async def _repair_block_model_json(
    module_name: str,
    module_info: Dict[str, Any],
    rtl_content: str,
    spec_text: str,
    previous_response: str,
    ai_client: Any,
) -> Optional[Dict[str, Any]]:
    """Ask the LLM once more for strict JSON if the first answer was prose/tool text."""
    ports = [
        {
            "name": p.get("name", ""),
            "direction": p.get("direction", ""),
            "width": p.get("width", 1),
            "bus_range": p.get("bus_range", ""),
        }
        for p in module_info.get("ports", [])
    ]
    system_prompt = (
        "You are a JSON formatter for a chip verification mental model. "
        "Return only valid JSON. No markdown. No prose."
    )
    prompt = f"""The previous LLM response was not valid JSON:
{previous_response[:1200]}

Create the required mental-model JSON for this RTL module.

Allowed module name: {module_name}
Allowed ports JSON:
{ports}

RTL excerpt:
```systemverilog
{rtl_content[:3000]}
```

Spec excerpt:
{spec_text[:1500] if spec_text else "(none)"}

Return exactly one JSON object with these keys:
{{
  "description": "",
  "protocols": [],
  "clock_domains": [],
  "fsms": [],
  "requirements": [],
  "constraints": [],
  "expected_behaviors": [],
  "transaction_flows": [],
  "risks": [],
  "open_questions": []
}}

Rules:
- Only use port names from Allowed ports JSON.
- If unsure, add an open question.
- Do not include markdown fences.
"""
    try:
        if not hasattr(ai_client, "chat"):
            return None
        response = await ai_client.chat(system_prompt, prompt)
        return _parse_llm_json_object(response if isinstance(response, str) else str(response))
    except Exception as exc:
        logger.warning("LLM JSON repair failed for %s: %s", module_name, exc)
        return None


def _structure_only_model(
    name: str,
    info: Dict[str, Any],
    reason: str = "no_llm_available",
) -> Dict[str, Any]:
    """Fallback: build model from structural parsing only (no LLM)."""
    if reason == "structure_with_llm_attempt":
        question = (
            f"LLM was called for {name} but did not return valid structured JSON; "
            "review protocol, behavior, and verification intent before relying on this model."
        )
        risks = ["LLM enrichment fell back to parser-grounded structural facts."]
    elif reason == "llm_error":
        question = f"LLM analysis failed for {name}; manual review is needed for design intent."
        risks = ["LLM enrichment failed; intent fields may be incomplete."]
    else:
        question = f"No LLM available; manual review needed for {name}"
        risks = []
    return {
        "description": f"Module {name} with {len(info.get('ports', []))} ports",
        "protocols": [],
        "clock_domains": [],
        "fsms": [],
        "requirements": [],
        "constraints": [],
        "expected_behaviors": [],
        "transaction_flows": [],
        "risks": risks,
        "open_questions": [
            {"question": question, "blocking": False}
        ],
        "_llm_status": reason,
    }


def _module_analysis_order(
    top_module: str,
    module_index: Dict[str, Any],
    hierarchy: Dict[str, List[str]],
) -> List[str]:
    """Return top-down module order, then any disconnected parsed modules."""
    ordered: List[str] = []
    seen: set[str] = set()

    def visit(module_name: str) -> None:
        if not module_name or module_name in seen or module_name not in module_index:
            return
        seen.add(module_name)
        ordered.append(module_name)
        for child in hierarchy.get(module_name, []):
            visit(child)

    visit(top_module)
    for module_name in module_index:
        visit(module_name)
    return ordered


def _read_module_rtl_content(root: Path, module_info: Dict[str, Any]) -> str:
    """Read only the module's source slice when line data is available."""
    mod_file = str(module_info.get("file") or "")
    if not mod_file:
        return ""
    try:
        content = (root / mod_file).read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""

    line_range = module_info.get("line_range")
    if isinstance(line_range, (tuple, list)) and len(line_range) == 2:
        try:
            start = max(1, int(line_range[0]))
            end = max(start, int(line_range[1]))
            lines = content.splitlines()
            return "\n".join(lines[start - 1:end])
        except Exception:
            return content
    return content


def _apply_llm_result_to_design_block(
    block: DesignBlock,
    llm_result: Dict[str, Any],
) -> None:
    """Merge one module's LLM intent into its source-grounded DesignBlock."""
    block.description = str(llm_result.get("description") or block.description or "")
    protocols = _build_protocol_bindings_from_llm(llm_result.get("protocols", []), block)
    if protocols:
        block.protocols = protocols

    clock_domains = _build_clock_domains_from_llm(
        llm_result.get("clock_domains", []),
        block,
    )
    if clock_domains:
        block.clock_domains = clock_domains

    fsms = _build_fsms_from_llm(llm_result.get("fsms", []), block)
    if fsms:
        block.fsms = fsms

    # ── New enrichment fields from enhanced LLM prompt ─────────────
    _apply_design_constraints(block, llm_result.get("constraints", []))
    _apply_expected_behaviors(block, llm_result.get("expected_behaviors", []))
    _apply_transaction_flows(block, llm_result.get("transaction_flows", []))


_SIGNAL_TOKEN_ALIASES = {
    "address": "addr",
    "addr": "addr",
    "enable": "en",
    "enabled": "en",
    "reset": "rst",
    "rst": "rst",
    "read": "rd",
    "rd": "rd",
    "write": "wr",
    "wr": "wr",
}

_SIGNAL_DIRECTION_TOKENS = {
    "i",
    "in",
    "input",
    "o",
    "out",
    "output",
    "sig",
    "signal",
    "port",
}
_GENERIC_SIGNAL_TOKENS = {"data", "value", "output", "out", "result"}


def _compact_signal_key(name: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(name or "").lower())


def _semantic_signal_key(name: Any) -> str:
    tokens = re.findall(r"[a-zA-Z]+|\d+", str(name or "").lower())
    normalized: List[str] = []
    for token in tokens:
        if token in {"ni", "no"}:
            token = "n"
        if token in _SIGNAL_DIRECTION_TOKENS:
            continue
        normalized.append(_SIGNAL_TOKEN_ALIASES.get(token, token))
    return "_".join(normalized)


def _add_unique_signal_alias(mapping: Dict[str, Optional[str]], key: str, value: str) -> None:
    if not key:
        return
    current = mapping.get(key)
    if current is None and key in mapping:
        return
    if current and current != value:
        mapping[key] = None
    else:
        mapping[key] = value


def _build_signal_resolver(
    ports: List[PortInfo],
    *,
    allow_single_generic: bool = False,
) -> Dict[str, Dict[str, Optional[str]]]:
    exact: Dict[str, Optional[str]] = {}
    compact: Dict[str, Optional[str]] = {}
    semantic: Dict[str, Optional[str]] = {}
    names: List[str] = []
    for port in ports:
        name = str(getattr(port, "name", "") or "").strip()
        if not name:
            continue
        names.append(name)
        _add_unique_signal_alias(exact, name.lower(), name)
        _add_unique_signal_alias(compact, _compact_signal_key(name), name)
        _add_unique_signal_alias(semantic, _semantic_signal_key(name), name)
    fallback = {"__single__": names[0]} if allow_single_generic and len(names) == 1 else {}
    return {"exact": exact, "compact": compact, "semantic": semantic, "fallback": fallback}


def _resolve_grounded_signal_name(
    signal: Any,
    resolver: Dict[str, Dict[str, Optional[str]]],
) -> str:
    raw = str(signal or "").strip()
    if not raw:
        return ""
    for group, key in (
        ("exact", raw.lower()),
        ("compact", _compact_signal_key(raw)),
        ("semantic", _semantic_signal_key(raw)),
    ):
        match = resolver.get(group, {}).get(key)
        if match:
            return match
    semantic_key = _semantic_signal_key(raw)
    if semantic_key in _GENERIC_SIGNAL_TOKENS:
        match = resolver.get("fallback", {}).get("__single__")
        if match:
            return match
    return ""


def _ground_signal_value_map(
    raw_signals: Any,
    resolver: Dict[str, Dict[str, Optional[str]]],
) -> Dict[str, str]:
    if not isinstance(raw_signals, dict):
        return {}
    grounded: Dict[str, str] = {}
    for signal, value in raw_signals.items():
        canonical = _resolve_grounded_signal_name(signal, resolver)
        if canonical:
            grounded[canonical] = value
    return grounded


def _constraint_strings(raw_constraints: Any) -> List[str]:
    if not isinstance(raw_constraints, list):
        raw_constraints = [raw_constraints] if raw_constraints else []
    constraints: List[str] = []
    seen: set[str] = set()
    for raw in raw_constraints:
        if isinstance(raw, dict):
            text = str(
                raw.get("constraint")
                or raw.get("text")
                or raw.get("description")
                or raw.get("rule")
                or ""
            ).strip()
        else:
            text = str(raw or "").strip()
        if not text:
            continue
        key = _requirement_key(text)
        if key in seen:
            continue
        seen.add(key)
        constraints.append(text)
    return constraints


def _apply_design_constraints(block: DesignBlock, raw_constraints: Any) -> None:
    if not hasattr(block, "constraints") or block.constraints is None:
        block.constraints = []
    seen = {_requirement_key(item) for item in block.constraints if item}
    for text in _constraint_strings(raw_constraints):
        key = _requirement_key(text)
        if key in seen:
            continue
        seen.add(key)
        block.constraints.append(text)


def _apply_expected_behaviors(
    block: DesignBlock,
    raw_behaviors: Any,
) -> None:
    """Merge LLM-inferred expected behaviors into the design block.

    Validates that signal names in stimulus/expected_output actually
    exist in the block's port list (anti-hallucination grounding).
    """
    if not isinstance(raw_behaviors, list):
        return

    stimulus_resolver = _build_signal_resolver(
        [p for p in block.ports if getattr(p, "direction", "") != "output"]
    )
    output_resolver = _build_signal_resolver(
        [p for p in block.ports if getattr(p, "direction", "") in {"output", "inout"}],
        allow_single_generic=True,
    )

    def _safe_latency(value: Any, default: int = 1) -> int:
        try:
            return max(0, int(value))
        except (TypeError, ValueError):
            return default

    for idx, raw in enumerate(raw_behaviors):
        if not isinstance(raw, dict):
            continue

        stimulus = raw.get("stimulus", {})
        expected_output = raw.get("expected_output", {})
        if not isinstance(stimulus, dict) or not isinstance(expected_output, dict):
            continue

        # Expected behaviors must have at least one grounded output. A behavior
        # with only valid stimulus is not actionable for a checker.
        grounded_stimulus = _ground_signal_value_map(stimulus, stimulus_resolver)
        grounded_output = _ground_signal_value_map(expected_output, output_resolver)

        if not grounded_output:
            logger.debug(
                "Skipping expected_behavior: no grounded expected outputs in %s",
                set(expected_output.keys()),
            )
            continue

        eb_id = str(raw.get("id", f"EB-{idx + 1:03d}"))
        block.expected_behaviors.append(
            ExpectedBehavior(
                id=eb_id,
                stimulus=grounded_stimulus,
                expected_output=grounded_output,
                latency_cycles=_safe_latency(raw.get("latency_cycles", 1), 1),
                precondition=str(raw.get("precondition", "")),
                description=str(raw.get("description", "")),
                requirement_ids=[
                    str(req_id) for req_id in raw.get("requirement_ids", [])
                ] if isinstance(raw.get("requirement_ids", []), list) else [],
                confidence=0.6,  # LLM-inferred
                source_ref=SourceReference(excerpt="LLM-inferred expected behavior"),
            )
        )


def _apply_transaction_flows(
    block: DesignBlock,
    raw_flows: Any,
) -> None:
    """Merge LLM-inferred transaction flows into the design block.

    Every signal inside a transaction step is filtered against the block port
    list. The flow can survive with descriptive phases, but ungrounded signal
    names must not become downstream stimulus.
    """
    if not isinstance(raw_flows, list):
        return

    signal_resolver = _build_signal_resolver(block.ports)

    def _safe_latency(value: Any, default: int = 0) -> int:
        try:
            return max(0, int(value))
        except (TypeError, ValueError):
            return default

    def _ground_steps(raw_steps: Any) -> List[Dict[str, Any]]:
        if not isinstance(raw_steps, list):
            return []
        grounded_steps: List[Dict[str, Any]] = []
        for raw_step in raw_steps:
            if not isinstance(raw_step, dict):
                continue
            raw_signals = raw_step.get("signals", {})
            signals: Dict[str, str] = {}
            if isinstance(raw_signals, dict):
                for signal, value in raw_signals.items():
                    canonical = _resolve_grounded_signal_name(signal, signal_resolver)
                    if canonical:
                        signals[canonical] = str(value)
            grounded_steps.append({
                "phase": str(raw_step.get("phase", "") or ""),
                "signals": signals,
                "cycles": str(raw_step.get("cycles", "") or ""),
                "description": str(raw_step.get("description", "") or ""),
            })
        return grounded_steps

    for raw in raw_flows:
        if not isinstance(raw, dict):
            continue
        name = str(raw.get("name", "")).strip()
        if not name:
            continue

        steps = _ground_steps(raw.get("steps", []))
        constraints = _constraint_strings(raw.get("constraints", []))
        if constraints:
            _apply_design_constraints(block, constraints)

        block.transaction_flows.append(
            TransactionFlow(
                name=name,
                protocol=str(raw.get("protocol", "")),
                description=str(raw.get("description", "")),
                steps=steps,
                latency_cycles=_safe_latency(raw.get("latency_cycles", 0), 0),
                throughput=str(raw.get("throughput", "")),
                constraints=constraints,
                source_ref=SourceReference(excerpt="LLM-inferred transaction flow"),
            )
        )


def _requirement_key(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower())


_GENERIC_REQUIREMENT_HEADINGS = {
    "technical requirement",
    "technical requirements",
    "project management need",
    "project management needs",
    "functional requirement",
    "functional requirements",
    "verification requirement",
    "verification requirements",
    "design requirement",
    "design requirements",
    "implementation requirement",
    "implementation requirements",
    "requirements",
    "overview",
    "introduction",
    "conclusion",
    "references",
    "appendix",
    "table of contents",
}


def _is_noise_requirement_text(text: str) -> bool:
    """Reject PDF table-of-contents rows and section headings misread as requirements."""
    cleaned = re.sub(r"\s+", " ", str(text or "")).strip()
    if not cleaned:
        return True
    lowered = cleaned.lower().strip(" .:-")
    if lowered in _GENERIC_REQUIREMENT_HEADINGS:
        return True
    if re.search(r"\.{3,}\s*\d+\s*$", cleaned):
        return True
    if re.fullmatch(r"[A-Za-z][A-Za-z0-9 /\-&]{2,80}\s+\d{1,3}", cleaned):
        return lowered.rsplit(" ", 1)[0] in _GENERIC_REQUIREMENT_HEADINGS
    if re.fullmatch(r"(?:chapter|section|table|figure|page)\s+\d+.*", lowered):
        return True
    if len(cleaned.split()) <= 4 and not re.search(
        r"\b(shall|must|should|will|verify|support|capture|drive|drives|assert|deassert|clear|clears|read|reads|write|writes|return|returns|translate|translates|latch|latches|generate|generates|hold|holds|stall|stalls)\b",
        lowered,
    ):
        return True
    return False


def _append_llm_requirements(
    raw_requirements: Any,
    requirements: List[Requirement],
    *,
    next_index: int,
    module_name: str,
    module_file: str,
    spec_text: str,
    seen: set[str],
    id_map: Optional[Dict[str, str]] = None,
) -> int:
    if not isinstance(raw_requirements, list):
        return next_index

    for raw in raw_requirements:
        if not isinstance(raw, dict):
            continue
        original_req_id = str(raw.get("id") or "").strip()
        text = str(raw.get("text") or raw.get("description") or "").strip()
        if not text or _is_noise_requirement_text(text):
            continue
        key = _requirement_key(text)
        if key in seen:
            existing = next(
                (req for req in requirements if _requirement_key(req.text) == key),
                None,
            )
            if id_map is not None and original_req_id and existing is not None:
                id_map[original_req_id] = existing.id
            continue
        seen.add(key)

        req_id = original_req_id
        if not req_id or any(req.id == req_id for req in requirements):
            req_id = f"REQ-{next_index:03d}"
        next_index += 1
        if id_map is not None and original_req_id:
            id_map[original_req_id] = req_id

        requirements.append(
            Requirement(
                id=req_id,
                text=text,
                priority=str(raw.get("priority") or "medium"),
                category=str(raw.get("category") or "functional"),
                spec_ref=SourceReference(
                    file="active_spec",
                    excerpt=spec_text[:240] if spec_text else "",
                ),
                rtl_refs=[
                    SourceReference(
                        file=module_file,
                        section=module_name,
                    )
                ] if module_file else [],
                confidence=_safe_float(raw.get("confidence"), 0.8),
            )
        )
    return next_index


def _safe_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _append_llm_open_questions(
    raw_questions: Any,
    open_questions: List[OpenQuestion],
    *,
    module_name: str,
    module_file: str,
) -> None:
    if not isinstance(raw_questions, list):
        return

    existing = {q.question.strip().lower() for q in open_questions}
    for raw in raw_questions:
        if isinstance(raw, dict):
            question = str(raw.get("question") or "").strip()
            blocking = bool(raw.get("blocking", False))
        else:
            question = str(raw or "").strip()
            blocking = False
        if not question or question.lower() in existing:
            continue
        existing.add(question.lower())
        open_questions.append(
            OpenQuestion(
                id=f"OQ-{str(uuid.uuid4())[:8]}",
                question=question,
                context=module_name,
                blocking=blocking,
                source_ref=SourceReference(file=module_file, section=module_name),
            )
        )


def _merge_unique_strings(target: List[str], values: Any, *, prefix: str = "") -> None:
    if not isinstance(values, list):
        return
    existing = {item.strip().lower() for item in target}
    for value in values:
        text = str(value or "").strip()
        if not text:
            continue
        if prefix and not text.startswith(prefix):
            text = f"{prefix}{text}"
        key = text.lower()
        if key not in existing:
            target.append(text)
            existing.add(key)


MENTAL_MODEL_INTEGRATION_AGENT_MAX_STEPS = 10


def _block_summary_for_integration(
    module_name: str,
    block: DesignBlock,
    result: Dict[str, Any],
    *,
    detailed: bool = False,
) -> Dict[str, Any]:
    limit_ports = 160 if detailed else 40
    limit_instances = 80 if detailed else 20
    limit_requirements = 24 if detailed else 8
    limit_questions = 16 if detailed else 5
    return {
        "module": module_name,
        "file": _module_file_from_block(block),
        "ports": [
            {
                "name": port.name,
                "direction": port.direction,
                "width": port.width,
                "bus_range": port.bus_range,
                "description": port.description,
            }
            for port in block.ports[:limit_ports]
        ],
        "parameters": [
            {
                "name": param.name,
                "default_value": param.default_value,
                "description": param.description,
            }
            for param in block.parameters[:40 if detailed else 12]
        ],
        "instances": [
            {
                "module": inst.module_name,
                "instance": inst.instance_name,
            }
            for inst in block.sub_instances[:limit_instances]
        ],
        "description": block.description or result.get("description", ""),
        "protocols": [
            {
                "protocol": protocol.protocol,
                "role": protocol.role,
                "ports": protocol.port_group,
                "description": protocol.description,
            }
            for protocol in block.protocols
        ],
        "clock_domains": [
            {
                "clock": clock.name,
                "reset": clock.associated_reset,
                "reset_polarity": clock.reset_polarity,
            }
            for clock in block.clock_domains
        ],
        "fsms": [
            {
                "name": fsm.name,
                "state_signal": fsm.state_signal,
                "states": fsm.states,
            }
            for fsm in block.fsms
        ],
        "requirements": [
            raw
            for raw in result.get("requirements", [])[:limit_requirements]
            if isinstance(raw, dict)
        ],
        "constraints": list(getattr(block, "constraints", []) or [])[:limit_questions],
        "risks": [
            str(risk)
            for risk in result.get("risks", [])[:limit_questions]
        ] if isinstance(result.get("risks", []), list) else [],
        "open_questions": [
            raw
            for raw in result.get("open_questions", [])[:limit_questions]
            if isinstance(raw, dict)
        ],
    }


def _integration_result_template() -> Dict[str, Any]:
    return {
        "top_description": "",
        "protocols": [],
        "requirements": [],
        "constraints": [],
        "risks": [],
        "open_questions": [],
        "verification_intent": {
            "unit_tests": [],
            "formal_properties": [],
            "uvm_scenarios": [],
            "coverage_points": [],
        },
    }


def _coerce_list(value: Any) -> List[Any]:
    return value if isinstance(value, list) else []


def _remap_requirement_ids_in_intent(
    raw_intent: Any,
    id_map: Dict[str, str],
) -> Dict[str, Any]:
    """Keep integration-agent traceability aligned after requirement IDs are de-duped."""
    if not isinstance(raw_intent, dict):
        return {}
    if not id_map:
        return raw_intent

    remapped: Dict[str, Any] = {}
    for key, value in raw_intent.items():
        if isinstance(value, list):
            remapped_items = []
            for item in value:
                if not isinstance(item, dict):
                    remapped_items.append(item)
                    continue
                cleaned = dict(item)
                cleaned["requirement_ids"] = [
                    id_map.get(str(req_id), str(req_id))
                    for req_id in _coerce_list(cleaned.get("requirement_ids"))
                ]
                remapped_items.append(cleaned)
            remapped[key] = remapped_items
        else:
            remapped[key] = value
    return remapped


def _next_available_requirement_id(
    existing_req_ids: set[str],
    used_req_ids: set[str],
) -> str:
    occupied = existing_req_ids | used_req_ids
    highest = 0
    for req_id in occupied:
        match = re.fullmatch(r"REQ-(\d+)", str(req_id or ""))
        if match:
            highest = max(highest, int(match.group(1)))

    next_index = highest + 1
    while True:
        candidate = f"REQ-{next_index:03d}"
        if candidate not in occupied:
            return candidate
        next_index += 1


def _sanitize_integration_candidate(
    candidate: Any,
    design: DesignBlock,
    block_models: Dict[str, DesignBlock],
    *,
    existing_req_ids: Optional[set[str]] = None,
) -> Tuple[Optional[Dict[str, Any]], List[str]]:
    """Validate and prune a project-level integration candidate."""
    if not isinstance(candidate, dict):
        return None, ["final answer is not a JSON object"]

    allowed_top_ports = _design_port_names(design)
    known_modules = set(block_models.keys())
    cleaned = _integration_result_template()
    errors: List[str] = []

    description = str(
        candidate.get("top_description")
        or candidate.get("description")
        or getattr(design, "description", "")
        or f"{getattr(design, 'top_module', '') or 'Top'} design"
        or ""
    ).strip()
    cleaned["top_description"] = description
    if not description:
        errors.append("top_description is empty")

    protocols = []
    for raw in _coerce_list(candidate.get("protocols")):
        if not isinstance(raw, dict):
            continue
        port_group = [
            str(port)
            for port in _coerce_list(raw.get("port_group") or raw.get("ports"))
            if str(port) in allowed_top_ports
        ]
        protocol = str(raw.get("protocol") or raw.get("name") or "").strip()
        if not protocol:
            continue
        protocols.append({
            "protocol": protocol,
            "role": str(raw.get("role") or ""),
            "port_group": port_group,
            "description": str(raw.get("description") or ""),
        })
    cleaned["protocols"] = protocols

    cleaned["constraints"] = _constraint_strings(candidate.get("constraints", []))

    requirements = []
    seen_reqs: set[str] = set()
    occupied_req_ids = {str(req_id) for req_id in (existing_req_ids or set()) if str(req_id)}
    used_req_ids: set[str] = set()
    req_id_map: Dict[str, str] = {}
    for raw in _coerce_list(candidate.get("requirements")):
        if not isinstance(raw, dict):
            continue
        text = str(raw.get("text") or raw.get("description") or "").strip()
        if not text:
            continue
        key = _requirement_key(text)
        if key in seen_reqs:
            continue
        seen_reqs.add(key)
        raw_req_id = str(raw.get("id") or "").strip()
        req_id = raw_req_id
        if not req_id or req_id in occupied_req_ids or req_id in used_req_ids:
            req_id = _next_available_requirement_id(occupied_req_ids, used_req_ids)
        used_req_ids.add(req_id)
        if raw_req_id:
            req_id_map.setdefault(raw_req_id, req_id)
        requirements.append({
            "id": req_id,
            "text": text,
            "priority": str(raw.get("priority") or "medium"),
            "category": str(raw.get("category") or "functional"),
            "confidence": _safe_float(raw.get("confidence"), 0.8),
        })
    cleaned["requirements"] = requirements

    cleaned["risks"] = [
        str(risk).strip()
        for risk in _coerce_list(candidate.get("risks"))
        if str(risk).strip()
    ]

    open_questions = []
    for raw in _coerce_list(candidate.get("open_questions")):
        if isinstance(raw, dict):
            question = str(raw.get("question") or "").strip()
            blocking = bool(raw.get("blocking", False))
        else:
            question = str(raw or "").strip()
            blocking = False
        if question:
            open_questions.append({"question": question, "blocking": blocking})
    cleaned["open_questions"] = open_questions
    if not requirements and not open_questions and not existing_req_ids:
        errors.append("requirements is empty and no open question explains the gap")

    raw_intent = candidate.get("verification_intent")
    if not isinstance(raw_intent, dict):
        raw_intent = {}
    intent = cleaned["verification_intent"]

    for index, raw in enumerate(_coerce_list(raw_intent.get("unit_tests")), start=1):
        if not isinstance(raw, dict):
            continue
        name = safe_identifier(str(raw.get("name") or f"unit_test_{index}"))
        intent["unit_tests"].append({
            "name": name,
            "description": str(raw.get("description") or ""),
            "requirement_ids": [
                req_id_map.get(str(req_id), str(req_id))
                for req_id in _coerce_list(raw.get("requirement_ids"))
            ],
            "priority": str(raw.get("priority") or "medium"),
            "stimulus": str(raw.get("stimulus") or ""),
            "expected_behavior": str(raw.get("expected_behavior") or ""),
        })

    for index, raw in enumerate(_coerce_list(raw_intent.get("formal_properties")), start=1):
        if not isinstance(raw, dict):
            continue
        related_signals = [
            str(signal)
            for signal in _coerce_list(raw.get("related_signals"))
            if str(signal) in allowed_top_ports
        ]
        intent["formal_properties"].append({
            "name": safe_identifier(str(raw.get("name") or f"formal_property_{index}")),
            "description": str(raw.get("description") or ""),
            "property_type": str(raw.get("property_type") or "assert"),
            "related_signals": related_signals,
            "requirement_ids": [
                req_id_map.get(str(req_id), str(req_id))
                for req_id in _coerce_list(raw.get("requirement_ids"))
            ],
            "sva_sketch": str(raw.get("sva_sketch") or ""),
        })

    for index, raw in enumerate(_coerce_list(raw_intent.get("uvm_scenarios")), start=1):
        if not isinstance(raw, dict):
            continue
        intent["uvm_scenarios"].append({
            "name": safe_identifier(str(raw.get("name") or f"uvm_scenario_{index}")),
            "description": str(raw.get("description") or ""),
            "sequence_type": str(raw.get("sequence_type") or "directed"),
            "requirement_ids": [
                req_id_map.get(str(req_id), str(req_id))
                for req_id in _coerce_list(raw.get("requirement_ids"))
            ],
            "stimulus_pattern": str(raw.get("stimulus_pattern") or ""),
            "checker_description": str(raw.get("checker_description") or ""),
        })

    for index, raw in enumerate(_coerce_list(raw_intent.get("coverage_points")), start=1):
        if not isinstance(raw, dict):
            continue
        signal = str(raw.get("signal") or "")
        if signal and signal not in allowed_top_ports:
            signal = ""
        intent["coverage_points"].append({
            "name": safe_identifier(str(raw.get("name") or f"coverage_point_{index}")),
            "signal": signal,
            "cover_type": str(raw.get("cover_type") or "coverpoint"),
            "bins_description": str(raw.get("bins_description") or ""),
            "requirement_ids": [
                req_id_map.get(str(req_id), str(req_id))
                for req_id in _coerce_list(raw.get("requirement_ids"))
            ],
        })

    referenced_modules = {
        str(name)
        for name in _coerce_list(candidate.get("referenced_modules"))
    }
    unknown_modules = sorted(referenced_modules - known_modules)
    if unknown_modules:
        errors.append(f"unknown referenced modules: {', '.join(unknown_modules)}")

    return cleaned, errors


def _run_integration_agent_tool(
    tool_name: str,
    tool_input: Any,
    *,
    top_module: str,
    design: DesignBlock,
    block_models: Dict[str, DesignBlock],
    block_llm_results: Dict[str, Dict[str, Any]],
    hierarchy: Dict[str, List[str]],
    spec_text: str,
    existing_req_ids: Optional[set[str]] = None,
) -> Dict[str, Any]:
    if not isinstance(tool_input, dict):
        tool_input = {}

    if tool_name == "list_modules":
        return {
            "top_module": top_module,
            "modules": list(block_models.keys()),
            "module_count": len(block_models),
        }

    if tool_name == "get_hierarchy":
        return {"hierarchy": hierarchy}

    if tool_name == "get_top_ports":
        return {
            "top_module": top_module,
            "ports": [
                {
                    "name": port.name,
                    "direction": port.direction,
                    "width": port.width,
                    "bus_range": port.bus_range,
                }
                for port in design.ports
            ],
        }

    if tool_name == "get_block_model":
        module_name = str(tool_input.get("module") or tool_input.get("module_name") or "")
        block = block_models.get(module_name)
        if block is None:
            return {
                "error": f"unknown module: {module_name}",
                "available_modules": list(block_models.keys()),
            }
        return {
            "block": _block_summary_for_integration(
                module_name,
                block,
                block_llm_results.get(module_name, {}),
                detailed=True,
            )
        }

    if tool_name == "get_spec_excerpt":
        start = max(0, int(tool_input.get("start", 0) or 0))
        length = int(tool_input.get("length", 2500) or 2500)
        length = max(500, min(length, 6000))
        return {
            "start": start,
            "length": length,
            "excerpt": spec_text[start:start + length],
            "has_more": start + length < len(spec_text),
        }

    if tool_name == "validate_candidate":
        candidate = (
            tool_input.get("candidate")
            or tool_input.get("mental_model")
            or tool_input.get("final")
            or {}
        )
        cleaned, errors = _sanitize_integration_candidate(
            candidate,
            design,
            block_models,
            existing_req_ids=existing_req_ids,
        )
        return {
            "valid": bool(cleaned is not None and not errors),
            "errors": errors,
            "sanitized_candidate": cleaned,
        }

    return {
        "error": f"unknown tool: {tool_name}",
        "available_tools": [
            "list_modules",
            "get_hierarchy",
            "get_top_ports",
            "get_block_model",
            "get_spec_excerpt",
            "validate_candidate",
            "finish",
        ],
    }


async def run_mental_model_integration_agent(
    top_module: str,
    design: DesignBlock,
    block_models: Dict[str, DesignBlock],
    block_llm_results: Dict[str, Dict[str, Any]],
    hierarchy: Dict[str, List[str]],
    spec_text: str,
    ai_client: Any,
    *,
    max_steps: int = MENTAL_MODEL_INTEGRATION_AGENT_MAX_STEPS,
    existing_req_ids: Optional[set[str]] = None,
) -> Dict[str, Any]:
    """Bounded ReAct-style source-grounded integration agent."""
    if not hasattr(ai_client, "chat"):
        return {"_llm_status": "missing_chat_method", "_integration_mode": "react_agent"}

    module_overview = [
        _block_summary_for_integration(
            module_name,
            block,
            block_llm_results.get(module_name, {}),
            detailed=False,
        )
        for module_name, block in block_models.items()
    ]
    system_prompt = (
        "You are ChipStack MentalModelAgent. You integrate parsed RTL facts, "
        "block-level mental models, and the spec into one source-grounded final "
        "mental model. You must use only the provided tools and return JSON only."
    )
    transcript: List[Dict[str, Any]] = [{
        "role": "system_context",
        "content": {
            "top_module": top_module,
            "module_overview": module_overview,
            "top_ports": [port.name for port in design.ports],
            "existing_requirement_ids": sorted(existing_req_ids or []),
            "rules": [
                "Only reference modules returned by list_modules/get_block_model.",
                "Only use top-level ports in protocols, formal related_signals, and coverage signal.",
                "Do not reuse existing_requirement_ids for new integrated requirements.",
                "Use open_questions when spec/RTL intent is ambiguous.",
                "Before finish, validate the candidate with validate_candidate.",
            ],
            "tools": {
                "list_modules": {},
                "get_hierarchy": {},
                "get_top_ports": {},
                "get_block_model": {"module": "module name"},
                "get_spec_excerpt": {"start": 0, "length": 2500},
                "validate_candidate": {"candidate": "final mental model JSON"},
                "finish": {"candidate": "validated final mental model JSON"},
            },
            "final_schema": _integration_result_template(),
        },
    }]
    last_validation: Optional[Dict[str, Any]] = None

    for step in range(1, max_steps + 1):
        prompt = f"""Continue the bounded ReAct integration.

Conversation JSON:
{json.dumps(transcript[-12:], indent=2)}

Return exactly one JSON object with one of these forms:
{{
  "thought": "short reasoning",
  "action": "list_modules|get_hierarchy|get_top_ports|get_block_model|get_spec_excerpt|validate_candidate",
  "action_input": {{}}
}}

or, when finished:
{{
  "thought": "short reasoning",
  "action": "finish",
  "action_input": {{"candidate": <final mental model JSON>}}
}}
"""
        try:
            response = await ai_client.chat(system_prompt, prompt)
        except Exception as exc:
            return {
                "_llm_status": "integration_agent_error",
                "_integration_mode": "react_agent",
                "error": str(exc),
                "_agent_steps": step,
            }

        action = _parse_llm_json_object(response if isinstance(response, str) else str(response))
        if not isinstance(action, dict):
            transcript.append({
                "role": "observation",
                "content": {
                    "error": "agent response was not valid JSON",
                    "excerpt": str(response)[:500],
                },
            })
            continue

        if "top_description" in action or "verification_intent" in action:
            cleaned, errors = _sanitize_integration_candidate(
                action,
                design,
                block_models,
                existing_req_ids=existing_req_ids,
            )
            if cleaned is not None and not errors:
                cleaned["_llm_status"] = "parsed"
                cleaned["_integration_mode"] = "react_agent_direct_final"
                cleaned["_agent_steps"] = step
                return cleaned
            transcript.append({
                "role": "observation",
                "content": {"valid": False, "errors": errors},
            })
            continue

        tool_name = str(action.get("action") or action.get("tool") or "").strip()
        tool_input = action.get("action_input") or action.get("input") or {}
        transcript.append({
            "role": "assistant_action",
            "content": {
                "thought": str(action.get("thought") or ""),
                "action": tool_name,
                "action_input": tool_input,
            },
        })

        if tool_name == "finish":
            candidate = {}
            if isinstance(tool_input, dict):
                candidate = (
                    tool_input.get("candidate")
                    or tool_input.get("mental_model")
                    or tool_input.get("final")
                    or {}
                )
            cleaned, errors = _sanitize_integration_candidate(
                candidate,
                design,
                block_models,
                existing_req_ids=existing_req_ids,
            )
            if cleaned is not None and not errors:
                cleaned["_llm_status"] = "parsed"
                cleaned["_integration_mode"] = "react_agent"
                cleaned["_agent_steps"] = step
                return cleaned
            transcript.append({
                "role": "observation",
                "content": {
                    "valid": False,
                    "errors": errors,
                    "hint": "Fix the candidate and call validate_candidate or finish again.",
                },
            })
            continue

        observation = _run_integration_agent_tool(
            tool_name,
            tool_input,
            top_module=top_module,
            design=design,
            block_models=block_models,
            block_llm_results=block_llm_results,
            hierarchy=hierarchy,
            spec_text=spec_text,
            existing_req_ids=existing_req_ids,
        )
        if tool_name == "validate_candidate":
            last_validation = observation
        transcript.append({"role": "observation", "content": observation})

        if (
            tool_name == "validate_candidate"
            and observation.get("valid")
            and isinstance(observation.get("sanitized_candidate"), dict)
        ):
            candidate = observation["sanitized_candidate"]
            candidate["_llm_status"] = "parsed"
            candidate["_integration_mode"] = "react_agent_validated"
            candidate["_agent_steps"] = step
            return candidate

    return {
        "_llm_status": "integration_agent_max_steps",
        "_integration_mode": "react_agent",
        "_agent_steps": max_steps,
        "last_validation": last_validation,
    }


async def integrate_block_models_with_llm(
    top_module: str,
    design: DesignBlock,
    block_models: Dict[str, DesignBlock],
    block_llm_results: Dict[str, Dict[str, Any]],
    hierarchy: Dict[str, List[str]],
    spec_text: str,
    ai_client: Any,
    existing_req_ids: Optional[set[str]] = None,
) -> Dict[str, Any]:
    """Phase 4/5: integrate block summaries via bounded agent, then fallback."""
    agent_result = await run_mental_model_integration_agent(
        top_module,
        design,
        block_models,
        block_llm_results,
        hierarchy,
        spec_text,
        ai_client,
        existing_req_ids=existing_req_ids,
    )
    if agent_result.get("_llm_status") == "parsed":
        return agent_result

    logger.warning(
        "Mental model integration agent did not finish cleanly for %s: %s; using single-call fallback.",
        top_module,
        agent_result.get("_llm_status"),
    )
    fallback = await _integrate_block_models_single_call(
        top_module,
        design,
        block_models,
        block_llm_results,
        hierarchy,
        spec_text,
        ai_client,
        existing_req_ids=existing_req_ids,
    )
    fallback["_integration_agent_status"] = agent_result.get("_llm_status")
    fallback["_integration_agent_steps"] = agent_result.get("_agent_steps", 0)
    if fallback.get("_llm_status") == "parsed":
        fallback["_integration_mode"] = "single_call_fallback"
    return fallback


async def _integrate_block_models_single_call(
    top_module: str,
    design: DesignBlock,
    block_models: Dict[str, DesignBlock],
    block_llm_results: Dict[str, Dict[str, Any]],
    hierarchy: Dict[str, List[str]],
    spec_text: str,
    ai_client: Any,
    existing_req_ids: Optional[set[str]] = None,
) -> Dict[str, Any]:
    """Phase 4/5: integrate block summaries into a project-level intent model."""
    module_summaries = []
    for module_name, block in block_models.items():
        result = block_llm_results.get(module_name, {})
        module_summaries.append({
            "module": module_name,
            "file": _module_file_from_block(block),
            "ports": [
                {
                    "name": port.name,
                    "direction": port.direction,
                    "width": port.width,
                }
                for port in block.ports[:80]
            ],
            "instances": [
                {
                    "module": inst.module_name,
                    "instance": inst.instance_name,
                }
                for inst in block.sub_instances[:40]
            ],
            "description": block.description or result.get("description", ""),
            "protocols": [
                {
                    "protocol": protocol.protocol,
                    "role": protocol.role,
                    "ports": protocol.port_group,
                }
                for protocol in block.protocols
            ],
            "requirements": [
                str(req.get("text") or "")
                for req in result.get("requirements", [])[:12]
                if isinstance(req, dict)
            ],
            "constraints": list(getattr(block, "constraints", []) or [])[:12],
            "open_questions": [
                str(q.get("question") or "")
                for q in result.get("open_questions", [])[:8]
                if isinstance(q, dict)
            ],
        })

    allowed_top_ports = [port.name for port in design.ports]
    system_prompt = (
        "You are a senior chip verification architect building a source-grounded "
        "mental model from block-level RTL analyses. Return valid JSON only."
    )
    prompt = f"""Integrate these block-level RTL mental models into one project-level source of truth.

TOP MODULE: {top_module}

HIERARCHY JSON:
{json.dumps(hierarchy, indent=2)}

TOP-LEVEL ALLOWED PORTS:
{json.dumps(allowed_top_ports)}

BLOCK SUMMARIES JSON:
{json.dumps(module_summaries, indent=2)}

SPECIFICATION EXCERPT:
{spec_text[:6000] if spec_text else "(No spec provided)"}

EXISTING REQUIREMENT IDS TO AVOID FOR NEW REQUIREMENTS:
{json.dumps(sorted(existing_req_ids or []))}

Return JSON only:
{{
  "top_description": "overall design intent in 2-4 sentences",
  "protocols": [{{"protocol": "name", "role": "master|slave|bridge|n/a", "port_group": ["top_port"]}}],
  "requirements": [{{"id": "REQ-001", "text": "system requirement", "priority": "high", "category": "functional", "confidence": 0.9}}],
  "constraints": ["top-level legal ranges, protocol ordering, timing, or reset constraints"],
  "risks": ["risk or verification concern"],
  "open_questions": [{{"question": "question", "blocking": false}}],
  "verification_intent": {{
    "unit_tests": [{{"name": "test_name", "description": "what to test", "requirement_ids": ["REQ-001"], "priority": "high"}}],
    "formal_properties": [{{"name": "prop_name", "description": "what to prove", "property_type": "assert", "related_signals": ["top_port"], "requirement_ids": ["REQ-001"]}}],
    "uvm_scenarios": [{{"name": "scenario_name", "description": "scenario", "sequence_type": "directed", "requirement_ids": ["REQ-001"]}}],
    "coverage_points": [{{"name": "cp_name", "signal": "top_port", "cover_type": "coverpoint", "bins_description": "bins", "requirement_ids": ["REQ-001"]}}]
  }}
}}

Rules:
- Use only top-level ports from TOP-LEVEL ALLOWED PORTS inside protocols, related_signals, and coverage signal fields.
- Do not reuse IDs from EXISTING REQUIREMENT IDS for new integrated requirements.
- Do not invent module names. Use only modules from BLOCK SUMMARIES JSON.
- Preserve source-grounded constraints that affect legal stimulus or expected protocol timing.
- Prefer open_questions over guessing missing behavior.
- Preserve spec intent when it conflicts with incomplete RTL inference.
"""
    try:
        if not hasattr(ai_client, "chat"):
            return {"_llm_status": "missing_chat_method"}
        response = await ai_client.chat(system_prompt, prompt)
        parsed = _parse_llm_json_object(response if isinstance(response, str) else str(response))
        if isinstance(parsed, dict):
            cleaned, errors = _sanitize_integration_candidate(
                parsed,
                design,
                block_models,
                existing_req_ids=existing_req_ids,
            )
            if cleaned is not None and not errors:
                cleaned["_llm_status"] = "parsed"
                cleaned["_integration_mode"] = "single_call"
                return cleaned
            repaired = await _repair_integration_json(
                top_module=top_module,
                design=design,
                block_models=block_models,
                previous_response=json.dumps(parsed)[:6000],
                errors=errors,
                ai_client=ai_client,
                existing_req_ids=existing_req_ids,
            )
            if repaired is not None:
                repaired["_llm_status"] = "parsed"
                repaired["_integration_mode"] = "single_call_repair"
                return repaired
            return {
                "_llm_status": "integration_unparseable",
                "_integration_mode": "single_call",
                "errors": errors,
            }
        repaired = await _repair_integration_json(
            top_module=top_module,
            design=design,
            block_models=block_models,
            previous_response=str(response)[:6000],
            errors=["response was not parseable JSON"],
            ai_client=ai_client,
            existing_req_ids=existing_req_ids,
        )
        if repaired is not None:
            repaired["_llm_status"] = "parsed"
            repaired["_integration_mode"] = "single_call_repair"
            return repaired
        return {"_llm_status": "integration_unparseable"}
    except Exception as exc:
        logger.warning("Cross-block LLM integration failed for %s: %s", top_module, exc)
        return {"_llm_status": "integration_error", "error": str(exc)}


async def _repair_integration_json(
    *,
    top_module: str,
    design: DesignBlock,
    block_models: Dict[str, DesignBlock],
    previous_response: str,
    errors: List[str],
    ai_client: Any,
    existing_req_ids: Optional[set[str]] = None,
) -> Optional[Dict[str, Any]]:
    """Ask once for a strict integration JSON repair before giving up."""
    if not hasattr(ai_client, "chat"):
        return None

    allowed_top_ports = [port.name for port in design.ports]
    modules = sorted(block_models.keys())
    system_prompt = (
        "You repair chip verification mental-model JSON. Return exactly one "
        "valid JSON object. No markdown. No prose."
    )
    prompt = f"""The previous project-level integration output was rejected.

ERRORS:
{json.dumps(errors, indent=2)}

PREVIOUS OUTPUT:
{previous_response}

TOP MODULE: {top_module}
KNOWN MODULES:
{json.dumps(modules)}

TOP-LEVEL ALLOWED PORTS:
{json.dumps(allowed_top_ports)}

EXISTING REQUIREMENT IDS:
{json.dumps(sorted(existing_req_ids or []))}

Return one JSON object matching this schema:
{json.dumps(_integration_result_template(), indent=2)}

Rules:
- Use only known modules.
- Use only top-level allowed ports in protocol port_group, formal related_signals, and coverage signal.
- If existing requirement IDs are provided, verification_intent items may reference them.
- Add new requirements only when they are source-grounded.
- If intent is ambiguous, add open_questions instead of guessing.
- No markdown fences. JSON object only.
"""
    try:
        response = await ai_client.chat(system_prompt, prompt)
        parsed = _parse_llm_json_object(response if isinstance(response, str) else str(response))
        cleaned, repair_errors = _sanitize_integration_candidate(
            parsed,
            design,
            block_models,
            existing_req_ids=existing_req_ids,
        )
        if cleaned is not None and not repair_errors:
            return cleaned
        logger.warning(
            "Integration JSON repair for %s still failed: %s",
            top_module,
            repair_errors,
        )
        return None
    except Exception as exc:
        logger.warning("Integration JSON repair failed for %s: %s", top_module, exc)
        return None


def _module_file_from_block(block: DesignBlock) -> str:
    if block.ports and block.ports[0].source_ref.file:
        return block.ports[0].source_ref.file
    if block.parameters and block.parameters[0].source_ref.file:
        return block.parameters[0].source_ref.file
    if block.sub_instances and block.sub_instances[0].source_ref.file:
        return block.sub_instances[0].source_ref.file
    return ""


def _build_verification_intent_from_llm(
    raw_intent: Any,
    requirements: List[Requirement],
    design: DesignBlock,
    symbol_table: Dict[str, List[str]],
) -> VerificationIntent:
    """Build verification intent from integrated LLM output, with deterministic fallback."""
    fallback = _generate_verification_intent(requirements, design)
    if not isinstance(raw_intent, dict):
        return fallback

    req_ids = {req.id for req in requirements}
    all_symbols = {
        str(symbol)
        for symbols in symbol_table.values()
        for symbol in symbols
    }
    top_ports = _design_port_names(design)
    allowed_signals = all_symbols | top_ports

    unit_tests: List[UnitTestIntent] = []
    for index, raw in enumerate(raw_intent.get("unit_tests") or [], start=1):
        if not isinstance(raw, dict):
            continue
        unit_tests.append(
            UnitTestIntent(
                id=f"UT-{index:03d}",
                name=safe_identifier(str(raw.get("name") or f"unit_test_{index}")),
                description=str(raw.get("description") or ""),
                requirement_ids=[
                    str(req_id)
                    for req_id in raw.get("requirement_ids", [])
                    if str(req_id) in req_ids
                ],
                stimulus=str(raw.get("stimulus") or ""),
                expected_behavior=str(raw.get("expected_behavior") or ""),
                priority=str(raw.get("priority") or "medium"),
            )
        )

    formal_properties: List[FormalPropertyIntent] = []
    for index, raw in enumerate(raw_intent.get("formal_properties") or [], start=1):
        if not isinstance(raw, dict):
            continue
        formal_properties.append(
            FormalPropertyIntent(
                id=f"FP-{index:03d}",
                name=safe_identifier(str(raw.get("name") or f"formal_property_{index}")),
                property_type=str(raw.get("property_type") or "assert"),
                description=str(raw.get("description") or ""),
                requirement_ids=[
                    str(req_id)
                    for req_id in raw.get("requirement_ids", [])
                    if str(req_id) in req_ids
                ],
                related_signals=[
                    str(signal)
                    for signal in raw.get("related_signals", [])
                    if str(signal) in allowed_signals
                ],
                sva_sketch=str(raw.get("sva_sketch") or ""),
            )
        )

    uvm_scenarios: List[UVMScenarioIntent] = []
    for index, raw in enumerate(raw_intent.get("uvm_scenarios") or [], start=1):
        if not isinstance(raw, dict):
            continue
        uvm_scenarios.append(
            UVMScenarioIntent(
                id=f"UVM-{index:03d}",
                name=safe_identifier(str(raw.get("name") or f"uvm_scenario_{index}")),
                description=str(raw.get("description") or ""),
                requirement_ids=[
                    str(req_id)
                    for req_id in raw.get("requirement_ids", [])
                    if str(req_id) in req_ids
                ],
                sequence_type=str(raw.get("sequence_type") or "directed"),
                stimulus_pattern=str(raw.get("stimulus_pattern") or ""),
                checker_description=str(raw.get("checker_description") or ""),
            )
        )

    coverage_points: List[CoveragePointIntent] = []
    for index, raw in enumerate(raw_intent.get("coverage_points") or [], start=1):
        if not isinstance(raw, dict):
            continue
        signal = str(raw.get("signal") or "")
        if signal and signal not in allowed_signals:
            signal = ""
        coverage_points.append(
            CoveragePointIntent(
                id=f"CP-{index:03d}",
                name=safe_identifier(str(raw.get("name") or f"coverage_point_{index}")),
                signal=signal,
                cover_type=str(raw.get("cover_type") or "coverpoint"),
                bins_description=str(raw.get("bins_description") or ""),
                requirement_ids=[
                    str(req_id)
                    for req_id in raw.get("requirement_ids", [])
                    if str(req_id) in req_ids
                ],
            )
        )

    if not any([unit_tests, formal_properties, uvm_scenarios, coverage_points]):
        return fallback

    return VerificationIntent(
        unit_tests=unit_tests or fallback.unit_tests,
        formal_properties=formal_properties or fallback.formal_properties,
        uvm_scenarios=uvm_scenarios,
        coverage_points=coverage_points or fallback.coverage_points,
    )


def safe_identifier(value: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9_]+", "_", value.strip())
    cleaned = re.sub(r"_+", "_", cleaned).strip("_")
    return cleaned or "item"


# ═══════════════════════════════════════════════════════════════════════
# Full Build Pipeline
# ═══════════════════════════════════════════════════════════════════════


async def build_mental_model(
    root_path: str,
    project_id: str = "",
    spec_text: str = "",
    target_module: Optional[str] = None,
    ai_client: Any = None,
) -> MentalModelSchema:
    """
    Build a complete mental model for a project.

    Args:
        root_path: Path to project folder
        project_id: DB project UUID
        spec_text: Extracted specification text
        target_module: If set, only deep-analyze this module
        ai_client: LLM client for intent analysis
    """
    now = datetime.now(timezone.utc).isoformat()

    # Phase 1: Scan folder
    logger.info(f"Phase 1: Scanning {root_path}")
    project_scan = scan_project_folder(root_path)

    # Phase 2: Structural index
    logger.info("Phase 2: Building structural index")
    module_index, symbol_table, parser_metadata = build_structural_index(
        root_path,
        project_scan.rtl_files,
        target_module=target_module,
        return_metadata=True,
    )
    hierarchy = build_hierarchy_tree(module_index)
    top_module = target_module or find_top_module(module_index, hierarchy)
    if not top_module and module_index:
        top_module = sorted(module_index.keys())[0]
        logger.warning(
            "Could not infer a unique top module; defaulting analysis to %s",
            top_module,
        )
    if not module_index and project_scan.rtl_files:
        logger.warning(
            "Structural parser found 0 modules in %d RTL file(s) under %s",
            len(project_scan.rtl_files),
            root_path,
        )

    # Build source checksums
    source_checksums = {}
    root = Path(root_path)
    for rel_path in project_scan.rtl_files:
        abs_path = root / rel_path
        try:
            content = abs_path.read_text(encoding="utf-8", errors="ignore")
            source_checksums[rel_path] = hashlib.sha256(content.encode()).hexdigest()
        except OSError:
            pass

    # Convert structural index to schema types
    design = _build_design_block(top_module, module_index, hierarchy, project_scan)
    block_models = {
        module_name: _build_module_design_block(
            module_name,
            module_index,
            hierarchy,
            project_scan,
            aggregate=False,
        )
        for module_name in module_index
    }

    # Phase 3-5: LLM analysis (if client available)
    # Seed source-grounded requirements from the spec before LLM enrichment.
    # The LLM can add detail, but a valid spec should not end up with zero
    # requirements just because a provider returns partial/empty JSON arrays.
    requirements: List[Requirement] = _extract_requirements_from_spec(spec_text)
    open_questions: List[OpenQuestion] = []
    risks: List[str] = []
    verification = VerificationIntent()
    llm_status = "not_requested"
    block_llm_results: Dict[str, Dict[str, Any]] = {}
    block_llm_statuses: Dict[str, str] = {}
    integration_status = "not_requested"
    integration_mode = "not_requested"
    integration_agent_status = "not_requested"
    integration_agent_steps = 0

    if ai_client and top_module and top_module in module_index:
        analysis_modules = _module_analysis_order(top_module, module_index, hierarchy)
        logger.info(
            "Phase 3: LLM per-block analysis for %d module(s): %s",
            len(analysis_modules),
            ", ".join(analysis_modules),
        )

        req_counter = len(requirements) + 1
        seen_requirements: set[str] = {
            _requirement_key(req.text)
            for req in requirements
            if getattr(req, "text", "")
        }

        for module_name in analysis_modules:
            module_info = module_index[module_name]
            rtl_content = _read_module_rtl_content(root, module_info)
            llm_result = await build_block_model_with_llm(
                module_name,
                module_info,
                rtl_content,
                spec_text,
                ai_client,
            )
            block_llm_results[module_name] = llm_result
            block_status = str(llm_result.get("_llm_status") or "parsed")
            block_llm_statuses[module_name] = block_status

            block = block_models.get(module_name)
            if block is not None:
                _apply_llm_result_to_design_block(block, llm_result)

            module_file = str(module_info.get("file") or "")
            req_counter = _append_llm_requirements(
                llm_result.get("requirements", []),
                requirements,
                next_index=req_counter,
                module_name=module_name,
                module_file=module_file,
                spec_text=spec_text,
                seen=seen_requirements,
            )
            _append_llm_open_questions(
                llm_result.get("open_questions", []),
                open_questions,
                module_name=module_name,
                module_file=module_file,
            )
            _merge_unique_strings(
                risks,
                llm_result.get("risks", []),
                prefix=f"{module_name}: ",
            )

        if top_module in block_models:
            top_block = block_models[top_module]
            design.description = top_block.description or design.description
            if top_block.protocols:
                design.protocols = top_block.protocols
            if top_block.clock_domains:
                design.clock_domains = top_block.clock_domains
            if top_block.fsms:
                design.fsms = top_block.fsms
            if top_block.expected_behaviors:
                design.expected_behaviors = top_block.expected_behaviors
            if top_block.transaction_flows:
                design.transaction_flows = top_block.transaction_flows
            if getattr(top_block, "constraints", None):
                design.constraints = list(top_block.constraints)

        logger.info("Phase 4/5: LLM cross-block integration for %s", top_module)
        integration = await integrate_block_models_with_llm(
            top_module,
            design,
            block_models,
            block_llm_results,
            hierarchy,
            spec_text,
            ai_client,
            existing_req_ids={req.id for req in requirements},
        )
        integration_status = str(integration.get("_llm_status") or "not_requested")
        integration_mode = str(integration.get("_integration_mode") or "unknown")
        integration_agent_status = str(
            integration.get("_integration_agent_status")
            or ("parsed" if integration_mode.startswith("react_agent") else "not_used")
        )
        integration_agent_steps = int(integration.get("_agent_steps") or integration.get("_integration_agent_steps") or 0)
        if integration_status == "parsed":
            design.description = str(
                integration.get("top_description")
                or integration.get("description")
                or design.description
                or ""
            )
            integrated_protocols = _build_protocol_bindings_from_llm(
                integration.get("protocols", []),
                design,
            )
            if integrated_protocols:
                design.protocols = integrated_protocols
            _apply_design_constraints(design, integration.get("constraints", []))

            integration_req_id_map: Dict[str, str] = {}
            req_counter = _append_llm_requirements(
                integration.get("requirements", []),
                requirements,
                next_index=req_counter,
                module_name=top_module,
                module_file=str(module_index[top_module].get("file") or ""),
                spec_text=spec_text,
                seen=seen_requirements,
                id_map=integration_req_id_map,
            )
            _append_llm_open_questions(
                integration.get("open_questions", []),
                open_questions,
                module_name=top_module,
                module_file=str(module_index[top_module].get("file") or ""),
            )
            _merge_unique_strings(risks, integration.get("risks", []))
            verification_intent = _remap_requirement_ids_in_intent(
                integration.get("verification_intent", {}),
                integration_req_id_map,
            )
            verification = _build_verification_intent_from_llm(
                verification_intent,
                requirements,
                design,
                symbol_table,
            )
        else:
            verification = _generate_verification_intent(requirements, design)

        parsed_blocks = sum(
            1 for status in block_llm_statuses.values() if status == "parsed"
        )
        if parsed_blocks == len(block_llm_statuses) and integration_status == "parsed":
            llm_status = "multi_block_parsed"
        elif parsed_blocks > 0:
            llm_status = "multi_block_partial"
        elif any(status == "structure_with_llm_attempt" for status in block_llm_statuses.values()):
            llm_status = "structure_with_llm_attempt"
        else:
            llm_status = "llm_error"

    # Assemble the model
    model = MentalModelSchema(
        project_id=project_id,
        revision=1,
        schema_version="1.0",
        created_at=now,
        updated_at=now,
        parser_engine=str(parser_metadata.get("parser_engine") or "slang"),
        parser_version=str(parser_metadata.get("parser_version") or ""),
        parser_diagnostics=list(parser_metadata.get("parser_diagnostics") or []),
        source_checksums=source_checksums,
        project_scan=project_scan,
        design=design,
        block_models=block_models,
        symbol_table=symbol_table,
        requirements=requirements,
        verification=verification,
        risks=risks,
        open_questions=open_questions,
    )
    model._llm_status = llm_status
    model._llm_block_count = len(block_models)
    model._llm_analyzed_modules = list(block_llm_statuses.keys())
    model._llm_block_statuses = block_llm_statuses
    model._llm_integration_status = integration_status
    model._llm_integration_mode = integration_mode
    model._llm_integration_agent_status = integration_agent_status
    model._llm_integration_agent_steps = integration_agent_steps
    _attach_knowledge_base_context(model)

    logger.info(f"Mental model built: {model.summary}")
    return model


def _attach_knowledge_base_context(model: MentalModelSchema) -> None:
    """Attach matched verification knowledge without changing core model facts."""
    try:
        from services.mental_model.knowledge_integration import (
            build_knowledge_base_context,
        )

        model.knowledge_base = build_knowledge_base_context(model)
    except Exception as exc:
        logger.warning("Knowledge-base matching skipped: %s", exc)


def _build_design_block(
    top_module: str,
    module_index: Dict[str, Any],
    hierarchy: Dict[str, List[str]],
    scan: ProjectScan,
) -> DesignBlock:
    """Convert parsed module data into a DesignBlock."""
    return _build_module_design_block(
        top_module,
        module_index,
        hierarchy,
        scan,
        aggregate=True,
    )


def _build_module_design_block(
    module_name: str,
    module_index: Dict[str, Any],
    hierarchy: Dict[str, List[str]],
    scan: ProjectScan,
    *,
    aggregate: bool = False,
) -> DesignBlock:
    """Convert parsed module data into a module-scoped DesignBlock."""
    top_module = module_name
    mod_info = module_index.get(top_module, {})

    ports = [
        PortInfo(
            name=p["name"],
            direction=p["direction"],
            width=p.get("width", 1),
            bus_range=p.get("bus_range", ""),
            port_type=p.get("port_type", "logic"),
            source_ref=SourceReference(
                file=mod_info.get("file", ""),
                line=p.get("line", 0),
            ),
        )
        for p in mod_info.get("ports", [])
    ]

    parameters = [
        ParameterInfo(
            name=p["name"],
            default_value=p.get("default_value", ""),
            source_ref=SourceReference(file=mod_info.get("file", "")),
        )
        for p in mod_info.get("parameters", [])
    ]

    sub_instances = [
        SubModuleInstance(
            instance_name=i["instance"],
            module_name=i["module"],
            source_ref=SourceReference(file=mod_info.get("file", "")),
        )
        for i in mod_info.get("instantiations", [])
    ]

    # Detect clocks/resets from port names
    clock_domains = _detect_clock_domains(ports)
    module_lines = 0
    line_range = mod_info.get("line_range")
    if isinstance(line_range, tuple) and len(line_range) == 2:
        module_lines = max(0, int(line_range[1]) - int(line_range[0]) + 1)
    elif isinstance(line_range, list) and len(line_range) == 2:
        module_lines = max(0, int(line_range[1]) - int(line_range[0]) + 1)
    module_lines = module_lines or int(mod_info.get("line_count") or 0)
    module_hierarchy = (
        hierarchy
        if aggregate
        else ({top_module: hierarchy.get(top_module, [])} if hierarchy.get(top_module) else {})
    )

    # ── Phase 2b enrichment data ────────────────────────────────────
    register_fields_list = [
        RegisterField(
            name=r["name"],
            bank=top_module,
            offset=int(r.get("offset", 0) or 0),
            width=int(r.get("width", 32) or 32),
            reset_value=str(r.get("reset_value", "0")),
            access=str(r.get("access", "RW")),
            description=(
                f"bank={top_module}, offset_basis={r.get('offset_basis', 'module_relative')}, "
                f"relative_offset={r.get('offset', 0)}, "
                f"write_enable={r.get('write_enable', '?')}, data={r.get('data_signal', '?')}"
            ),
            confidence=0.8,
            source_ref=SourceReference(
                file=mod_info.get("file", ""),
                line=r.get("line", 0),
            ),
        )
        for r in mod_info.get("register_fields", [])
    ]

    existing_assertions_list = mod_info.get("existing_assertions", [])
    cdc_crossings_list = mod_info.get("cdc_crossings", [])
    register_map_list = [
        {
            "name": r.get("name", ""),
            "bank": top_module,
            "offset": r.get("offset", 0),
            "offset_basis": r.get("offset_basis", "module_relative"),
            "width": r.get("width", 32),
            "reset_value": r.get("reset_value", "0"),
            "access": r.get("access", "RW"),
            "write_enable": r.get("write_enable", ""),
            "addr_signal": r.get("addr_signal", ""),
            "data_signal": r.get("data_signal", ""),
            "line": r.get("line", 0),
        }
        for r in mod_info.get("register_fields", [])
    ]

    # Detect has_cdc_crossings from parser data
    has_cdc = len(cdc_crossings_list) > 0
    parser_fsms = _build_parser_fsms_from_candidates(mod_info)
    inferred_protocols = _infer_protocol_bindings_from_ports(ports)
    inferred_expected_behaviors = _infer_expected_behaviors_from_protocols(
        top_module,
        ports,
        inferred_protocols,
    )
    inferred_transaction_flows = _infer_transaction_flows_from_protocols(
        top_module,
        ports,
        inferred_protocols,
    )
    inferred_constraints: List[str] = []
    for flow in inferred_transaction_flows:
        for constraint in getattr(flow, "constraints", []) or []:
            if constraint not in inferred_constraints:
                inferred_constraints.append(constraint)

    return DesignBlock(
        top_module=top_module,
        modules=list(module_index.keys()) if aggregate else [top_module],
        hierarchy_tree=module_hierarchy,
        sub_instances=sub_instances,
        ports=ports,
        parameters=parameters,
        clock_domains=clock_domains,
        has_cdc_crossings=has_cdc,
        cdc_crossings=cdc_crossings_list,
        fsms=parser_fsms,
        protocols=inferred_protocols,
        register_map=register_map_list,
        register_fields=register_fields_list,
        expected_behaviors=inferred_expected_behaviors,
        transaction_flows=inferred_transaction_flows,
        constraints=inferred_constraints,
        existing_assertions=existing_assertions_list,
        total_lines=(
            scan.total_lines
            if aggregate
            else module_lines
        ),
        total_files=len(scan.rtl_files) if aggregate else (1 if mod_info else 0),
        total_always_blocks=(
            sum(len(m.get("always_blocks", [])) for m in module_index.values())
            if aggregate
            else len(mod_info.get("always_blocks", []))
        ),
    )


def _design_port_names(design: DesignBlock) -> set[str]:
    return {p.name for p in design.ports if p.name}


def _source_ref_for_signal(design: DesignBlock, signal_name: str) -> SourceReference:
    for port in design.ports:
        if port.name == signal_name:
            return port.source_ref
    return SourceReference()


def _build_parser_fsms_from_candidates(
    mod_info: Dict[str, Any],
) -> List[FSMDescription]:
    fsms: List[FSMDescription] = []
    case_state_sets = [
        {
            state
            for state in (_clean_state_name(item) for item in (raw.get("states") or []))
            if state
        }
        for raw in (mod_info.get("fsm_candidates", []) or [])
        if isinstance(raw, dict) and raw.get("state_signal")
    ]
    for index, raw in enumerate(mod_info.get("fsm_candidates", []) or [], start=1):
        if not isinstance(raw, dict):
            continue
        state_signal = str(raw.get("state_signal") or "").strip()
        states = [
            state
            for state in (_clean_state_name(item) for item in (raw.get("states") or []))
            if state
        ]
        transitions = [
            {
                "from": str(item.get("from") or ""),
                "to": str(item.get("to") or ""),
                "condition": str(item.get("condition") or "default"),
                "line": str(item.get("line") or ""),
            }
            for item in (raw.get("transitions") or [])
            if isinstance(item, dict) and item.get("from") and item.get("to")
        ]
        if not state_signal and not transitions and states:
            state_set = set(states)
            if any(state_set and state_set <= case_states for case_states in case_state_sets):
                continue
        if not state_signal and not states and not transitions:
            continue
        fsms.append(
            FSMDescription(
                name=state_signal or f"fsm_{index}",
                state_signal=state_signal,
                states=states,
                transitions=transitions,
                source_ref=SourceReference(
                    file=mod_info.get("file", ""),
                    line=int(transitions[0].get("line") or 0) if transitions else 0,
                    excerpt="Parser-inferred FSM candidate",
                ),
            )
        )
    return fsms


def _classify_protocol_variant(protocol: str, port_group: List[str]) -> str:
    names = {str(port).lower() for port in port_group}
    compact_names = {name.replace("_", "") for name in names}
    proto = str(protocol or "").strip()
    proto_lower = proto.lower()
    tlul_required = {"avalid", "aready", "dvalid", "dready"}

    if (
        "tilelink" in proto_lower
        or "tl-ul" in proto_lower
        or "tlul" in proto_lower.replace("_", "").replace("-", "")
        or tlul_required <= compact_names
    ):
        return "TileLink-UL"
    if any(name.startswith("tdata") or name.endswith("tdata") for name in compact_names):
        return "AXI4-Stream"
    if "axi" in proto_lower or any(
        any(token in name for token in ("awvalid", "awaddr", "wvalid", "bready", "arvalid", "rready"))
        for name in compact_names
    ):
        if any(
            any(token in name for token in ("awlen", "arlen", "awburst", "arburst", "awid", "arid", "wlast", "rlast"))
            for name in compact_names
        ):
            return "AXI4"
        return "AXI4-Lite"
    if "apb" in proto_lower or any(name in compact_names for name in {"psel", "penable", "pready", "paddr", "pwdata", "prdata"}):
        return "APB"
    if "ahb" in proto_lower or any(name in compact_names for name in {"haddr", "htrans", "hready", "hwdata", "hrdata"}):
        return "AHB"
    if any(name.endswith("valid") for name in names) and any(name.endswith("ready") for name in names):
        return "valid-ready"
    return proto


def _infer_protocol_role(
    protocol: str,
    port_group: List[str],
    by_name: Dict[str, PortInfo],
) -> str:
    """Infer the DUT role from common bus signal directions."""
    protocol_lower = str(protocol or "").lower()

    def direction_for(signal_tokens: tuple[str, ...]) -> str:
        for name in port_group:
            compact = str(name).lower().replace("_", "")
            if any(token in compact for token in signal_tokens):
                return str(by_name.get(name).direction if by_name.get(name) else "").lower()
        return ""

    if "axi4-stream" in protocol_lower:
        tvalid_dir = direction_for(("tvalid",))
        tready_dir = direction_for(("tready",))
        if tvalid_dir == "input" and tready_dir == "output":
            return "slave"
        if tvalid_dir == "output" and tready_dir == "input":
            return "master"
    if "axi" in protocol_lower:
        awvalid_dir = direction_for(("awvalid", "arvalid", "wvalid"))
        awready_dir = direction_for(("awready", "arready", "wready"))
        if awvalid_dir == "input" and awready_dir == "output":
            return "slave"
        if awvalid_dir == "output" and awready_dir == "input":
            return "master"
    if "apb" in protocol_lower:
        psel_dir = direction_for(("psel", "paddr", "penable", "pwrite"))
        pready_dir = direction_for(("pready", "prdata"))
        if psel_dir == "input" and pready_dir == "output":
            return "slave"
        if psel_dir == "output" and pready_dir == "input":
            return "master"
    if "ahb" in protocol_lower:
        haddr_dir = direction_for(("haddr", "htrans", "hwrite"))
        hready_dir = direction_for(("hreadyout", "hrdata", "hresp"))
        if haddr_dir == "input" and hready_dir == "output":
            return "slave"
        if haddr_dir == "output" and hready_dir == "input":
            return "master"
    if "tilelink" in protocol_lower or "tlul" in protocol_lower.replace("-", "").replace("_", ""):
        a_valid_dir = direction_for(("avalid",))
        a_ready_dir = direction_for(("aready",))
        if a_valid_dir == "input" and a_ready_dir == "output":
            return "device"
        if a_valid_dir == "output" and a_ready_dir == "input":
            return "host"
    if "valid-ready" in protocol_lower:
        valid_dir = direction_for(("valid", "vld"))
        ready_dir = direction_for(("ready", "rdy"))
        if valid_dir == "input" and ready_dir == "output":
            return "sink"
        if valid_dir == "output" and ready_dir == "input":
            return "source"
    return ""


def _infer_protocol_bindings_from_ports(ports: List[PortInfo]) -> List[ProtocolBinding]:
    by_name = {port.name: port for port in ports if port.name}
    lower_to_name = {name.lower(): name for name in by_name}

    def _contains_any(name: str, tokens: tuple[str, ...]) -> bool:
        compact = name.lower().replace("_", "")
        return any(token in compact for token in tokens)

    inferred: List[ProtocolBinding] = []
    protocol_specs = [
        (
            "TileLink-UL",
            (
                "avalid", "aready", "aopcode", "aparam", "asize", "asource",
                "aaddress", "amask", "adata", "auser",
                "dvalid", "dready", "dopcode", "dparam", "dsize", "dsource",
                "dsink", "ddata", "derror", "duser",
            ),
        ),
        (
            "AXI",
            (
                "awvalid", "awready", "awaddr", "awprot", "awlen", "awburst",
                "wvalid", "wready", "wdata", "wstrb", "wlast",
                "bvalid", "bready", "bresp",
                "arvalid", "arready", "araddr", "arprot", "arlen", "arburst",
                "rvalid", "rready", "rdata", "rresp", "rlast",
                "tvalid", "tready", "tdata", "tlast", "tkeep", "tstrb",
            ),
        ),
        ("APB", ("psel", "penable", "pready", "paddr", "pwdata", "prdata", "pwrite", "pslverr")),
        ("AHB", ("haddr", "htrans", "hready", "hreadyout", "hwdata", "hrdata", "hwrite", "hresp")),
    ]
    used_ports: set[str] = set()
    for proto_name, tokens in protocol_specs:
        group = [
            original
            for lower, original in lower_to_name.items()
            if _contains_any(lower, tokens)
        ]
        if len(group) < 3:
            continue
        protocol = _classify_protocol_variant(proto_name, group)
        role = _infer_protocol_role(protocol, group, by_name)
        inferred.append(
            ProtocolBinding(
                protocol=protocol,
                role=role,
                port_group=group,
                description=(
                    f"Parser-inferred {protocol} {role + ' ' if role else ''}"
                    "interface from port names"
                ),
                source_ref=_source_ref_for_signal(
                    DesignBlock(ports=ports),
                    group[0],
                ),
            )
        )
        used_ports.update(group)

    valid_ports = [
        name for name in by_name
        if name not in used_ports and name.lower().endswith("valid")
    ]
    ready_names = {name.lower() for name in by_name if name not in used_ports and name.lower().endswith("ready")}
    handshake_group: List[str] = []
    for valid_name in valid_ports:
        stem = valid_name[:-5]
        if f"{stem}ready".lower() in ready_names or "ready" in ready_names:
            handshake_group.append(valid_name)
            handshake_group.extend(
                name for name in by_name
                if name not in used_ports and name.lower().startswith(stem.lower())
            )
    if handshake_group:
        unique_group = []
        for name in handshake_group:
            if name not in unique_group:
                unique_group.append(name)
        inferred.append(
            ProtocolBinding(
                protocol="valid-ready",
                role=_infer_protocol_role("valid-ready", unique_group, by_name),
                port_group=unique_group,
                description="Parser-inferred valid/ready handshake from port names",
                source_ref=_source_ref_for_signal(DesignBlock(ports=ports), unique_group[0]),
            )
        )

    return inferred


def _compact_signal_name(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(name or "").lower())


def _find_protocol_port(
    ports: List[PortInfo],
    port_group: List[str],
    *tokens: str,
) -> str:
    grouped = {name for name in port_group if name}
    candidates = [
        port.name
        for port in ports
        if port.name and (not grouped or port.name in grouped)
    ]
    compact_tokens = tuple(_compact_signal_name(token) for token in tokens if token)
    for name in candidates:
        compact = _compact_signal_name(name)
        if compact in compact_tokens:
            return name
    for name in candidates:
        compact = _compact_signal_name(name)
        if any(compact.endswith(token) or token in compact for token in compact_tokens):
            return name
    return ""


def _protocol_source_ref(ports: List[PortInfo], port_group: List[str]) -> SourceReference:
    for name in port_group:
        for port in ports:
            if port.name == name:
                ref = port.source_ref or SourceReference()
                return SourceReference(
                    file=ref.file,
                    line=ref.line,
                    page=ref.page,
                    section=ref.section,
                    artifact_id=ref.artifact_id,
                    excerpt="Parser-inferred protocol intent from port-level structure",
                )
    return SourceReference(excerpt="Parser-inferred protocol intent from port-level structure")


def _infer_expected_behaviors_from_protocols(
    module_name: str,
    ports: List[PortInfo],
    protocols: List[ProtocolBinding],
) -> List[ExpectedBehavior]:
    behaviors: List[ExpectedBehavior] = []
    for index, protocol in enumerate(protocols or [], start=1):
        proto = str(protocol.protocol or "").lower()
        group = list(protocol.port_group or [])
        source_ref = _protocol_source_ref(ports, group)
        if "apb" in proto:
            psel = _find_protocol_port(ports, group, "psel")
            penable = _find_protocol_port(ports, group, "penable")
            pwrite = _find_protocol_port(ports, group, "pwrite")
            paddr = _find_protocol_port(ports, group, "paddr")
            pready = _find_protocol_port(ports, group, "pready")
            prdata = _find_protocol_port(ports, group, "prdata")
            pslverr = _find_protocol_port(ports, group, "pslverr", "slverr")
            stimulus = {name: value for name, value in [
                (psel, "1"),
                (penable, "1"),
                (pwrite, "0_or_1"),
                (paddr, "stable_address"),
            ] if name}
            expected = {name: value for name, value in [
                (pready, "eventual_ready_or_wait_state"),
                (prdata, "valid_read_data_when_read_completes"),
                (pslverr, "error_status_only_for_completed_error_access"),
            ] if name}
            if stimulus and expected:
                behaviors.append(
                    ExpectedBehavior(
                        id=f"EB-{module_name}-APB-{index:02d}",
                        stimulus=stimulus,
                        expected_output=expected,
                        latency_cycles=1,
                        precondition="after_reset_and_legal_apb_setup",
                        description=(
                            "APB access behavior inferred from PSEL/PENABLE/PREADY-style "
                            "ports: setup must be followed by access, outputs are meaningful "
                            "when the access phase completes."
                        ),
                        confidence=0.45,
                        source_ref=source_ref,
                    )
                )
            continue

        if "tilelink" in proto or "tl-ul" in proto or "tlul" in proto:
            for channel, valid_tokens, ready_tokens in [
                ("A", ("a_valid", "avalid"), ("a_ready", "aready")),
                ("D", ("d_valid", "dvalid"), ("d_ready", "dready")),
            ]:
                valid = _find_protocol_port(ports, group, *valid_tokens)
                ready = _find_protocol_port(ports, group, *ready_tokens)
                if valid and ready:
                    behaviors.append(
                        ExpectedBehavior(
                            id=f"EB-{module_name}-TLUL-{channel}-{index:02d}",
                            stimulus={valid: "1", ready: "0_or_1"},
                            expected_output={ready: "backpressure_or_acceptance_handshake"},
                            latency_cycles=1,
                            precondition="after_reset",
                            description=(
                                f"TileLink-UL {channel}-channel valid/ready handshake: payload "
                                "must remain stable while VALID is asserted and READY is low."
                            ),
                            confidence=0.45,
                            source_ref=source_ref,
                        )
                    )
            continue

        if "valid-ready" in proto:
            valid = _find_protocol_port(ports, group, "valid")
            ready = _find_protocol_port(ports, group, "ready")
            if valid and ready:
                behaviors.append(
                    ExpectedBehavior(
                        id=f"EB-{module_name}-VR-{index:02d}",
                        stimulus={valid: "1", ready: "0_or_1"},
                        expected_output={ready: "backpressure_or_acceptance_handshake"},
                        latency_cycles=1,
                        precondition="after_reset",
                        description=(
                            "Valid/ready behavior inferred from interface naming: payload "
                            "must stay stable under backpressure and transfer when VALID and READY meet."
                        ),
                        confidence=0.45,
                        source_ref=source_ref,
                    )
                )
    return behaviors


def _infer_transaction_flows_from_protocols(
    module_name: str,
    ports: List[PortInfo],
    protocols: List[ProtocolBinding],
) -> List[TransactionFlow]:
    flows: List[TransactionFlow] = []
    for index, protocol in enumerate(protocols or [], start=1):
        proto = str(protocol.protocol or "").lower()
        group = list(protocol.port_group or [])
        source_ref = _protocol_source_ref(ports, group)
        if "apb" in proto:
            psel = _find_protocol_port(ports, group, "psel") or "PSEL"
            penable = _find_protocol_port(ports, group, "penable") or "PENABLE"
            pready = _find_protocol_port(ports, group, "pready") or "PREADY"
            paddr = _find_protocol_port(ports, group, "paddr") or "PADDR"
            pwrite = _find_protocol_port(ports, group, "pwrite") or "PWRITE"
            pwdata = _find_protocol_port(ports, group, "pwdata") or "PWDATA"
            prdata = _find_protocol_port(ports, group, "prdata") or "PRDATA"
            flows.append(
                TransactionFlow(
                    name=f"{module_name}_apb_access_flow",
                    protocol="APB",
                    description="Parser-inferred APB setup/access/wait/complete transaction flow.",
                    steps=[
                        {
                            "phase": "setup",
                            "signals": {psel: "1", penable: "0", paddr: "stable", pwrite: "read_or_write"},
                            "cycles": "1",
                        },
                        {
                            "phase": "access_wait",
                            "signals": {psel: "1", penable: "1", pready: "0"},
                            "cycles": "0_or_more",
                        },
                        {
                            "phase": "access_complete",
                            "signals": {psel: "1", penable: "1", pready: "1", pwdata: "valid_on_write", prdata: "valid_on_read"},
                            "cycles": "1",
                        },
                    ],
                    latency_cycles=1,
                    throughput="one transfer per completed APB access",
                    constraints=[
                        f"{penable} should only be asserted during the access phase after setup.",
                        f"{paddr}, {pwrite}, and write data should remain stable while waiting for {pready}.",
                    ],
                    source_ref=source_ref,
                )
            )
            continue

        if "tilelink" in proto or "tl-ul" in proto or "tlul" in proto:
            flows.append(
                TransactionFlow(
                    name=f"{module_name}_tlul_request_response_flow",
                    protocol="TileLink-UL",
                    description="Parser-inferred TileLink-UL A-channel request and D-channel response flow.",
                    steps=[
                        {"phase": "a_channel_request", "signals": {"a_valid": "1", "a_ready": "0_or_1"}, "cycles": "until handshake"},
                        {"phase": "a_payload_hold", "signals": {"a_valid": "1", "a_ready": "0"}, "cycles": "stall cycles"},
                        {"phase": "d_channel_response", "signals": {"d_valid": "1", "d_ready": "0_or_1"}, "cycles": "until response handshake"},
                    ],
                    constraints=[
                        "A-channel payload must remain stable while a_valid is high and a_ready is low.",
                        "D-channel payload must remain stable while d_valid is high and d_ready is low.",
                    ],
                    source_ref=source_ref,
                )
            )
            continue

        if "valid-ready" in proto:
            valid = _find_protocol_port(ports, group, "valid") or "valid"
            ready = _find_protocol_port(ports, group, "ready") or "ready"
            flows.append(
                TransactionFlow(
                    name=f"{module_name}_valid_ready_flow",
                    protocol="valid-ready",
                    description="Parser-inferred valid/ready transfer flow.",
                    steps=[
                        {"phase": "offer", "signals": {valid: "1"}, "cycles": "producer asserts valid"},
                        {"phase": "backpressure", "signals": {valid: "1", ready: "0"}, "cycles": "payload held stable"},
                        {"phase": "transfer", "signals": {valid: "1", ready: "1"}, "cycles": "one accepted beat"},
                    ],
                    constraints=[f"Payload must remain stable while {valid} is high and {ready} is low."],
                    source_ref=source_ref,
                )
            )
    return flows


def _build_protocol_bindings_from_llm(
    raw_protocols: Any,
    design: DesignBlock,
) -> List[ProtocolBinding]:
    """Build protocol bindings from LLM output, keeping only real parsed ports."""
    if not isinstance(raw_protocols, list):
        return []

    known_ports = _design_port_names(design)
    protocols: List[ProtocolBinding] = []
    for raw in raw_protocols:
        if not isinstance(raw, dict):
            continue
        protocol = str(raw.get("protocol") or raw.get("name") or "").strip()
        if not protocol:
            continue
        raw_group = raw.get("port_group") or raw.get("ports") or []
        if not isinstance(raw_group, list):
            raw_group = []
        port_group = [str(port) for port in raw_group if str(port) in known_ports]
        protocol = _classify_protocol_variant(protocol, port_group)
        role = str(raw.get("role") or "").strip() or _infer_protocol_role(
            protocol,
            port_group,
            {port.name: port for port in design.ports if port.name},
        )
        protocols.append(
            ProtocolBinding(
                protocol=protocol,
                role=role,
                port_group=port_group,
                description=str(raw.get("description") or ""),
                source_ref=(
                    _source_ref_for_signal(design, port_group[0])
                    if port_group
                    else SourceReference()
                ),
            )
        )
    return protocols


def _build_clock_domains_from_llm(
    raw_domains: Any,
    design: DesignBlock,
) -> List[ClockDomain]:
    """Build LLM clock/reset domains, dropping hallucinated port names."""
    if not isinstance(raw_domains, list):
        return []

    known_ports = _design_port_names(design)
    domains: List[ClockDomain] = []
    for raw in raw_domains:
        if not isinstance(raw, dict):
            continue
        clk_name = str(raw.get("name") or raw.get("clock") or "").strip()
        if clk_name not in known_ports:
            continue
        reset_name = str(
            raw.get("associated_reset") or raw.get("reset") or ""
        ).strip()
        if reset_name and reset_name not in known_ports:
            reset_name = ""
        domains.append(
            ClockDomain(
                name=clk_name,
                frequency=str(raw.get("frequency") or ""),
                associated_reset=reset_name,
                reset_polarity=str(
                    raw.get("reset_polarity")
                    or raw.get("polarity")
                    or "active_low"
                ),
                reset_type=str(raw.get("reset_type") or "synchronous"),
                source_ref=_source_ref_for_signal(design, clk_name),
            )
        )
    return domains


def _build_fsms_from_llm(raw_fsms: Any, design: DesignBlock) -> List[FSMDescription]:
    """Build FSM descriptions from LLM output."""
    if not isinstance(raw_fsms, list):
        return []

    fsms: List[FSMDescription] = []
    for raw in raw_fsms:
        if not isinstance(raw, dict):
            continue
        name = str(raw.get("name") or raw.get("state_signal") or "").strip()
        states = raw.get("states") or []
        transitions = raw.get("transitions") or []
        if not name and not states:
            continue
        if not isinstance(states, list):
            states = []
        if not isinstance(transitions, list):
            transitions = []
        state_signal = str(raw.get("state_signal") or "").strip()
        fsms.append(
            FSMDescription(
                name=name or "fsm",
                state_signal=state_signal,
                states=[str(state) for state in states],
                transitions=[
                    transition
                    for transition in transitions
                    if isinstance(transition, dict)
                ],
                encoding=str(raw.get("encoding") or "binary"),
                source_ref=_source_ref_for_signal(design, state_signal),
            )
        )
    return fsms


def _detect_clock_domains(ports: List[PortInfo]) -> List[ClockDomain]:
    """Detect clock/reset pairs from port names."""
    clocks = [p for p in ports if _is_clock(p.name)]
    resets = [p for p in ports if _is_reset(p.name)]

    domains = []
    for clk in clocks:
        # Try to find matching reset
        reset_name = ""
        reset_polarity = "active_low"
        for rst in resets:
            reset_name = rst.name
            if "n" in rst.name.lower() or rst.name.endswith("_n"):
                reset_polarity = "active_low"
            else:
                reset_polarity = "active_high"
            break

        domains.append(ClockDomain(
            name=clk.name,
            associated_reset=reset_name,
            reset_polarity=reset_polarity,
            source_ref=clk.source_ref,
        ))

    return domains


def _is_clock(name: str) -> bool:
    n = name.lower()
    return any(c in n for c in ("clk", "clock", "sys_clk"))


def _is_reset(name: str) -> bool:
    n = name.lower()
    return any(r in n for r in ("rst", "reset", "rstn"))


def _parse_width(bus_range: str) -> int:
    """Parse [7:0] → 8, or return 1 if unparseable."""
    if not bus_range:
        return 1
    normalized = bus_range.strip().strip("[]")
    m = re.match(r"(.+?)\s*:\s*(.+)", normalized)
    if m:
        left = _safe_int_expr(m.group(1))
        right = _safe_int_expr(m.group(2))
        if left is not None and right is not None:
            return abs(left - right) + 1
    return 1


def _safe_int_expr(expr: str) -> Optional[int]:
    text = str(expr or "").strip()
    if not text or re.search(r"[^0-9_+\-*/%() \t]", text):
        return None
    text = text.replace("_", "")
    try:
        value = eval(text, {"__builtins__": {}}, {})
    except Exception:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _generate_verification_intent(
    requirements: List[Requirement],
    design: DesignBlock,
) -> VerificationIntent:
    """Auto-generate initial verification intent from requirements."""
    unit_tests = []
    formal_props = []
    coverage_points = []

    for i, req in enumerate(requirements):
        rid = req.id
        # Generate a unit test for each requirement
        unit_tests.append(UnitTestIntent(
            id=f"UT-{i+1:03d}",
            name=f"test_{rid.lower().replace('-', '_')}",
            description=f"Unit test for: {req.text[:100]}",
            requirement_ids=[rid],
            priority=req.priority,
        ))
        # Generate a formal property for high/critical requirements
        if req.priority in ("high", "critical"):
            formal_props.append(FormalPropertyIntent(
                id=f"FP-{i+1:03d}",
                name=f"prop_{rid.lower().replace('-', '_')}",
                property_type="assert",
                description=f"Formal assertion for: {req.text[:100]}",
                requirement_ids=[rid],
            ))

    # Auto-generate coverage for observable outputs and driven multi-bit inputs.
    for j, port in enumerate(design.ports):
        if port.direction in {"output", "input"} and port.width > 1:
            coverage_points.append(CoveragePointIntent(
                id=f"CP-{j+1:03d}",
                name=f"cp_{port.name}",
                signal=port.name,
                cover_type="coverpoint",
                bins_description=f"Cover all values of {port.name}[{port.width-1}:0]",
            ))

    return VerificationIntent(
        unit_tests=unit_tests,
        formal_properties=formal_props,
        coverage_points=coverage_points,
    )


# ═══════════════════════════════════════════════════════════════════════
# Compatibility shims used by store.py
# ═══════════════════════════════════════════════════════════════════════

SCHEMA_VERSION = "1.0"


def _extract_requirements_from_spec(spec_text: str) -> List[Requirement]:
    """
    Extract Requirement objects from a spec text document.

    Handles common spec formats:
    - Numbered items: "1. The FIFO shall..." or "REQ-001: ..."
    - Must/shall statements
    - Functional bullet points
    """
    import re

    requirements: List[Requirement] = []
    if not spec_text or not spec_text.strip():
        return requirements

    # Pattern 1: Numbered requirements  e.g. "1. The FIFO must..."
    num_pattern = re.compile(
        r"^\s*(?:REQ[-_]?\d+[:\.]?\s*|(\d+)[\.\)]\s+)(.{10,200})",
        re.MULTILINE | re.IGNORECASE,
    )
    # Pattern 2: must/shall statements anywhere in line
    shall_pattern = re.compile(
        r"^[^\n]{0,60}(?:shall|must|should|will)\s+.{5,150}",
        re.MULTILINE | re.IGNORECASE,
    )

    seen: set = set()

    def _add(text: str, priority: str = "medium") -> None:
        text = text.strip().rstrip(".")
        if len(text) < 10 or text in seen or _is_noise_requirement_text(text):
            return
        seen.add(text)
        req_id = f"REQ-{len(requirements) + 1:03d}"
        # Guess category from keywords
        lower = text.lower()
        if any(k in lower for k in ["overflow", "underflow", "full", "empty", "depth", "count"]):
            cat = "functional"
        elif any(k in lower for k in ["latency", "throughput", "clock", "cycle", "timing"]):
            cat = "timing"
        elif any(k in lower for k in ["reset", "power", "enable"]):
            cat = "reset"
        elif any(k in lower for k in ["width", "depth", "size", "parameter"]):
            cat = "parameter"
        else:
            cat = "functional"

        requirements.append(Requirement(
            id=req_id,
            text=text,
            category=cat,
            priority=priority,
            spec_ref=SourceReference(file="spec", excerpt=text[:200]),
        ))

    # Extract numbered items first (higher confidence)
    for m in num_pattern.finditer(spec_text):
        _add(m.group(2) or m.group(0), priority="high")

    # Extract must/shall lines if we didn't get enough
    if len(requirements) < 3:
        for m in shall_pattern.finditer(spec_text):
            _add(m.group(0), priority="medium")
            if len(requirements) >= 20:
                break

    return requirements


def build_source_grounded_mental_model(
    project_id: str,
    spec_artifact: Any = None,
    rtl_artifact: Any = None,
    revision: int = 1,
    **kwargs: Any,
) -> Dict[str, Any]:
    """
    Build a mental model from DB artifact objects.

    This is the entry point used by store.py. It reads the artifact
    files from disk, extracts text, and calls the main builder.

    Returns the model as a plain dict (JSON-serializable).
    """
    import json as _json

    spec_text = ""
    rtl_root = ""

    # Read spec text from artifact (page-indexed excerpt for large docs)
    if spec_artifact and spec_artifact.file_path:
        try:
            from services.document_context import spec_text_for_mental_model

            spec_path = Path(spec_artifact.file_path)
            if spec_path.exists():
                spec_text = spec_text_for_mental_model(
                    organization_id=str(getattr(spec_artifact, "organization_id", "") or ""),
                    project_id=str(getattr(spec_artifact, "project_id", "") or ""),
                    artifact_id=str(getattr(spec_artifact, "id", "") or ""),
                    file_path=spec_path,
                    filename=str(getattr(spec_artifact, "filename", "") or "spec.txt"),
                    checksum_sha256=str(
                        getattr(spec_artifact, "checksum_sha256", "") or ""
                    ),
                )
        except Exception as e:
            logger.warning(f"Failed to read spec artifact: {e}")

    # Determine RTL root path from artifact
    if rtl_artifact and rtl_artifact.file_path:
        rtl_path = Path(rtl_artifact.file_path)
        extracted_root = find_extracted_rtl_root(rtl_path)
        if extracted_root and extracted_root.exists():
            archive = archive_for_extracted_rtl_root(extracted_root)
            if archive:
                extract_dir = short_rtl_extract_root(
                    archive,
                    cache_key=(
                        f"{getattr(rtl_artifact, 'id', '')}:"
                        f"{getattr(rtl_artifact, 'checksum_sha256', '')}:"
                        f"{archive.name}"
                    ),
                )
                if extract_dir != extracted_root:
                    if extract_dir.exists():
                        shutil.rmtree(extract_dir)
                    extract_dir.mkdir(parents=True, exist_ok=True)
                    extract_rtl_archive(archive, extract_dir)
                    rtl_root = str(extract_dir)
                else:
                    rtl_root = str(extracted_root)
            else:
                rtl_root = str(extracted_root)
        elif not rtl_path.exists() and extracted_root:
            archive = archive_for_extracted_rtl_root(extracted_root)
            if archive:
                extract_dir = short_rtl_extract_root(
                    archive,
                    cache_key=(
                        f"{getattr(rtl_artifact, 'id', '')}:"
                        f"{getattr(rtl_artifact, 'checksum_sha256', '')}:"
                        f"{archive.name}"
                    ),
                )
                if extract_dir.exists():
                    shutil.rmtree(extract_dir)
                extract_dir.mkdir(parents=True, exist_ok=True)
                extract_rtl_archive(archive, extract_dir)
                rtl_root = str(extract_dir)
        elif rtl_path.is_dir():
            rtl_root = str(rtl_path)
        elif rtl_path.is_file() and is_rtl_archive_filename(rtl_path.name):
            extract_dir = short_rtl_extract_root(
                rtl_path,
                cache_key=(
                    f"{getattr(rtl_artifact, 'id', '')}:"
                    f"{getattr(rtl_artifact, 'checksum_sha256', '')}:"
                    f"{rtl_path.name}"
                ),
            )
            if extract_dir.exists():
                shutil.rmtree(extract_dir)
            extract_dir.mkdir(parents=True, exist_ok=True)
            extract_rtl_archive(rtl_path, extract_dir)
            rtl_root = str(extract_dir)
        else:
            sandbox = rtl_path.parent / f"_mm_src_{getattr(rtl_artifact, 'id', rtl_path.stem)}"
            sandbox.mkdir(parents=True, exist_ok=True)
            target_name = (
                rtl_path.name
                if is_rtl_source_filename(rtl_path.name)
                else (getattr(rtl_artifact, "filename", None) or rtl_path.name)
            )
            target = sandbox / target_name
            if not target.exists() or target.stat().st_mtime < rtl_path.stat().st_mtime:
                shutil.copy2(rtl_path, target)
            rtl_root = str(sandbox)

    if not rtl_root:
        # No RTL path — return minimal model
        return {
            "project_id": project_id,
            "revision": revision,
            "schema_version": SCHEMA_VERSION,
            "status": "error",
            "message": "No RTL root path could be determined",
        }

    # Scan and build structural model (synchronous path, no LLM)
    try:
        scan = scan_project_folder(rtl_root)
        module_index, symbol_table = build_structural_index(rtl_root, scan.rtl_files)
        hierarchy = build_hierarchy_tree(module_index)
        top = find_top_module(module_index, hierarchy)
        design = _build_design_block(top, module_index, hierarchy, scan)

        requirements: List[Requirement] = _extract_requirements_from_spec(spec_text)
        verification = _generate_verification_intent(requirements, design)

        # Serialize to dict
        model = MentalModelSchema(
            project_id=project_id,
            revision=revision,
            schema_version=SCHEMA_VERSION,
            created_at=datetime.now(timezone.utc).isoformat(),
            updated_at=datetime.now(timezone.utc).isoformat(),
            source_checksums={},
            project_scan=scan,
            design=design,
            symbol_table=symbol_table,
            requirements=requirements,
            verification=verification,
        )
        _attach_knowledge_base_context(model)

        # Convert to dict for JSON serialization
        from dataclasses import asdict
        result = asdict(model)
        result["sources"] = {
            "spec": {
                "artifact_id": spec_artifact.id if spec_artifact else None,
                "checksum_sha256": spec_artifact.checksum_sha256 if spec_artifact else None,
            },
            "rtl": {
                "artifact_id": rtl_artifact.id if rtl_artifact else None,
                "checksum_sha256": rtl_artifact.checksum_sha256 if rtl_artifact else None,
            },
        }
        return result

    except Exception as e:
        logger.exception(f"build_source_grounded_mental_model failed: {e}")
        result = {
            "project_id": project_id,
            "revision": revision,
            "schema_version": SCHEMA_VERSION,
            "status": "error",
            "message": str(e),
        }
        if isinstance(e, SlangAnalysisError):
            result["parser_engine"] = "slang"
            result["parser_diagnostics"] = list(getattr(e, "diagnostics", []) or [])
            result["missing_required_tool"] = bool(getattr(e, "missing_tool", False))
        return result


def summarize_mental_model(content: Dict[str, Any]) -> str:
    """Generate a human-readable summary from a mental model dict."""
    if content.get("status") == "error":
        return f"Mental model build failed: {content.get('message', 'unknown error')}"

    design = content.get("design", {})
    top = design.get("top_module", "unknown")
    n_ports = len(design.get("ports", []))
    n_modules = len(design.get("modules", []))
    n_lines = design.get("total_lines", 0)
    n_reqs = len(content.get("requirements", []))

    parts = [f"Design: {top}"]
    if n_modules > 1:
        parts.append(f"{n_modules} modules")
    parts.append(f"{n_ports} ports")
    if n_lines:
        parts.append(f"{n_lines} RTL lines")
    if n_reqs:
        parts.append(f"{n_reqs} requirements")

    return " · ".join(parts)
