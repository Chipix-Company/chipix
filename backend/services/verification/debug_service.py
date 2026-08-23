"""
Debug Service — Structured failure analysis and auto-fix generation.

Replaces the legacy Fixer agent with a mental-model-aware diagnostic
pipeline: classify → correlate → diagnose → generate fix.
"""

from __future__ import annotations

import difflib
import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# ─── Data Types ──────────────────────────────────────────────────────

@dataclass
class DebugResult:
    """Structured failure diagnosis."""
    classification: str = "unknown"       # "syntax" | "compilation" | "rtl_bug" | etc.
    confidence: float = 0.0               # 0.0 - 1.0
    root_cause: str = ""                  # Human-readable root cause
    affected_signals: List[str] = field(default_factory=list)
    affected_lines: List[Dict[str, Any]] = field(default_factory=list)
    proposed_fix: str = ""                # Fix code
    diff_text: str = ""                   # Unified diff
    explanation: str = ""                 # Why this fix works
    fix_type: str = "unknown"             # "rtl_fix" | "testbench_fix" | "constraint_fix"
    error_excerpt: str = ""               # Key portion of the error

    def to_dict(self) -> Dict[str, Any]:
        return {
            "classification": self.classification,
            "confidence": self.confidence,
            "root_cause": self.root_cause,
            "affected_signals": self.affected_signals,
            "affected_lines": self.affected_lines[:20],
            "proposed_fix": self.proposed_fix,
            "diff_text": self.diff_text,
            "explanation": self.explanation,
            "fix_type": self.fix_type,
            "error_excerpt": self.error_excerpt[:1000],
        }


# ─── Failure Classification Patterns ────────────────────────────────

FAILURE_PATTERNS = {
    "syntax": [
        r"syntax error", r"parse error", r"unexpected token",
        r"near \"", r"Error-\[", r"illegal",
    ],
    "compilation": [
        r"cannot find", r"undeclared", r"undefined",
        r"module .* not found", r"port .* not found",
        r"Unknown module type", r"error: .*not defined",
    ],
    "timing": [
        r"timing violation", r"setup time", r"hold time",
        r"clock period", r"zero delay",
    ],
    "assertion_failure": [
        r"assertion failed", r"assert", r"sva error",
        r"property .* failed", r"error-\[asrt",
    ],
    "rtl_bug": [
        r"mismatch", r"expected .* got", r"scoreboard error",
        r"checker failed", r"data integrity",
        r"ERROR.*expected", r"FAIL.*mismatch",
    ],
    "testbench_bug": [
        r"timeout", r"deadlock", r"driver error",
        r"sequence error", r"stimulus.*fail",
    ],
    "overconstraint": [
        r"vacuous", r"unreachable", r"overconstrained",
        r"no valid", r"unsatisfiable",
    ],
}


def classify_failure(error_log: str) -> Dict[str, Any]:
    """
    Classify failure type based on error log patterns.

    Returns dict with classification, confidence, and all scores.
    """
    log_lower = error_log.lower()
    scores: Dict[str, int] = {}

    for category, patterns in FAILURE_PATTERNS.items():
        score = 0
        for pattern in patterns:
            matches = re.findall(pattern, log_lower)
            score += len(matches)
        if score > 0:
            scores[category] = score

    if not scores:
        return {"classification": "unknown", "confidence": 0.0, "all_scores": {}}

    best = max(scores, key=scores.get)
    total = sum(scores.values())

    return {
        "classification": best,
        "confidence": round(scores[best] / total, 2) if total > 0 else 0.0,
        "all_scores": scores,
    }


# ─── Signal Correlation ─────────────────────────────────────────────

def correlate_signals(
    error_log: str,
    mental_model: Any = None,
) -> List[str]:
    """
    Extract signal names from error log and cross-reference with
    the mental model's symbol table.

    Returns list of affected signals that exist in the design.
    """
    # Extract potential signal names from error
    signal_candidates = set()

    # Common patterns: "signal_name = X", "'signal_name' mismatch"
    for m in re.finditer(r"['\"]?(\w+)['\"]?\s*(?:=|mismatch|expected|got)", error_log):
        sig = m.group(1)
        if len(sig) > 2 and not sig.isdigit():
            signal_candidates.add(sig)

    # Patterns like "at module.signal"
    for m in re.finditer(r"\b(\w+)\.(\w+)\b", error_log):
        signal_candidates.add(m.group(2))

    # Filter against model if available
    if mental_model:
        symbol_table = {}
        if isinstance(mental_model, dict):
            symbol_table = mental_model.get("symbol_table", {})
        elif hasattr(mental_model, "symbol_table"):
            symbol_table = mental_model.symbol_table or {}

        all_symbols = set()
        for symbols in symbol_table.values():
            if isinstance(symbols, list):
                all_symbols.update(symbols)

        if all_symbols:
            matched = signal_candidates & all_symbols
            return sorted(matched) if matched else sorted(signal_candidates)[:10]

    return sorted(signal_candidates)[:10]


# ─── Line Correlation ───────────────────────────────────────────────

def extract_affected_lines(error_log: str) -> List[Dict[str, Any]]:
    """Extract file:line references from error log."""
    lines: List[Dict[str, Any]] = []
    seen = set()

    # Patterns: "file.sv:42:", "file.v(42):", "at file.sv line 42"
    patterns = [
        re.compile(r"(\S+\.(?:sv|v|svh|vh)):(\d+)"),
        re.compile(r"(\S+\.(?:sv|v))\((\d+)\)"),
        re.compile(r"at\s+(\S+\.(?:sv|v))\s+line\s+(\d+)"),
    ]

    for pattern in patterns:
        for m in pattern.finditer(error_log):
            key = (m.group(1), int(m.group(2)))
            if key not in seen:
                seen.add(key)
                lines.append({"file": m.group(1), "line": int(m.group(2))})

    return lines[:20]


# ─── LLM-Assisted Diagnosis ─────────────────────────────────────────

async def diagnose_failure(
    error_log: str,
    rtl_content: str = "",
    testbench_content: str = "",
    mental_model: Any = None,
    ai_client: Any = None,
) -> DebugResult:
    """
    Full diagnostic pipeline:
    1. Pattern-based classification (fast, no LLM)
    2. Signal correlation with mental model
    3. LLM root-cause analysis
    4. LLM fix generation

    Falls back to pattern-only analysis if no LLM available.
    """
    result = DebugResult()
    result.error_excerpt = error_log[:1000]

    # Step 1: Classification
    classification = classify_failure(error_log)
    result.classification = classification["classification"]
    result.confidence = classification["confidence"]

    # Step 2: Signal and line correlation
    result.affected_signals = correlate_signals(error_log, mental_model)
    result.affected_lines = extract_affected_lines(error_log)

    # Determine fix type
    if result.classification in ("syntax", "compilation", "rtl_bug", "timing"):
        result.fix_type = "rtl_fix"
    elif result.classification in ("testbench_bug",):
        result.fix_type = "testbench_fix"
    elif result.classification in ("overconstraint",):
        result.fix_type = "constraint_fix"
    elif result.classification in ("assertion_failure",):
        result.fix_type = "rtl_fix"  # Most likely RTL bug if assertion fails
    else:
        result.fix_type = "rtl_fix"

    # Step 3 & 4: LLM diagnosis + fix generation
    if ai_client:
        try:
            result = await _llm_diagnose_and_fix(
                result, error_log, rtl_content, testbench_content,
                mental_model, ai_client,
            )
        except Exception as e:
            logger.warning(f"LLM diagnosis failed, using pattern-only: {e}")
            result.root_cause = _pattern_based_root_cause(result)
            result.explanation = _recommend_action(result.classification)
    else:
        # No LLM — pattern-only diagnosis
        result.root_cause = _pattern_based_root_cause(result)
        result.explanation = _recommend_action(result.classification)

    return result


async def _llm_diagnose_and_fix(
    result: DebugResult,
    error_log: str,
    rtl_content: str,
    testbench_content: str,
    mental_model: Any,
    ai_client: Any,
) -> DebugResult:
    """Use LLM to diagnose root cause and generate fix."""

    # Build context
    model_summary = ""
    if mental_model:
        if isinstance(mental_model, dict):
            design = mental_model.get("design", {})
            model_summary = (
                f"Top module: {design.get('top_module', 'unknown')}\n"
                f"Ports: {[p.get('name', '') for p in design.get('ports', [])[:20]]}\n"
            )

    prompt = f"""You are a senior verification engineer debugging a failure.

FAILURE CLASSIFICATION: {result.classification} (confidence: {result.confidence})
AFFECTED SIGNALS: {result.affected_signals}
FIX TARGET: {result.fix_type}

ERROR LOG (excerpt):
```
{error_log[:3000]}
```

{f'RTL SOURCE:{chr(10)}```systemverilog{chr(10)}{rtl_content[:4000]}{chr(10)}```' if rtl_content else ''}

{f'TESTBENCH:{chr(10)}```systemverilog{chr(10)}{testbench_content[:2000]}{chr(10)}```' if testbench_content else ''}

{f'DESIGN MODEL:{chr(10)}{model_summary}' if model_summary else ''}

Respond in this JSON format:
{{
  "root_cause": "One sentence explaining the root cause",
  "explanation": "Detailed explanation of why this fix works",
  "fix_code": "The corrected code snippet (only the changed portion)",
  "fix_type": "{result.fix_type}",
  "affected_signals": ["signal1", "signal2"]
}}

RULES:
- Be specific about signal names and line numbers
- Only reference signals that exist in the design
- If the fix is in RTL, provide SystemVerilog code
- If the fix is in the testbench, provide testbench code
"""

    system_prompt = (
        "You are a chip verification debugger. Diagnose failures precisely. "
        "Respond with valid JSON only."
    )

    try:
        if hasattr(ai_client, "chat"):
            response = await ai_client.chat(system_prompt, prompt)
        else:
            return result

        # Parse LLM response
        text = response if isinstance(response, str) else str(response)
        json_match = re.search(r"\{[\s\S]*\}", text)
        if json_match:
            data = json.loads(json_match.group())
            result.root_cause = data.get("root_cause", result.root_cause)
            result.explanation = data.get("explanation", result.explanation)
            result.proposed_fix = data.get("fix_code", "")
            if data.get("affected_signals"):
                result.affected_signals = data["affected_signals"]

            # Generate diff if we have original content and fix
            if result.proposed_fix and rtl_content:
                result.diff_text = _generate_diff(
                    rtl_content, result.proposed_fix, result.fix_type
                )

    except (json.JSONDecodeError, Exception) as e:
        logger.warning(f"LLM response parsing failed: {e}")

    return result


# ─── Fix Patch Generation ───────────────────────────────────────────

async def generate_fix_patch(
    diagnosis: DebugResult,
    original_file_content: str,
    file_path: str = "design.sv",
    ai_client: Any = None,
) -> str:
    """
    Generate a unified diff patch from a diagnosis result.

    If the diagnosis already has a proposed fix, creates a diff.
    Otherwise, uses LLM to generate the fix.
    """
    if not diagnosis.proposed_fix:
        if ai_client:
            # Ask LLM for a fix based on the diagnosis
            prompt = f"""Given this diagnosis, generate a fix for {file_path}:

Root cause: {diagnosis.root_cause}
Classification: {diagnosis.classification}
Affected signals: {diagnosis.affected_signals}

Original code:
```
{original_file_content[:4000]}
```

Respond with ONLY the fixed code (complete file content), no explanation.
"""
            try:
                if hasattr(ai_client, "chat"):
                    response = await ai_client.chat(
                        "You are a RTL code fixer. Output only the corrected code.",
                        prompt,
                    )
                    # Extract code from markdown fencing
                    code_match = re.search(
                        r"```(?:systemverilog|verilog|sv)?\n([\s\S]*?)```",
                        str(response),
                    )
                    if code_match:
                        diagnosis.proposed_fix = code_match.group(1)
                    else:
                        diagnosis.proposed_fix = str(response)
            except Exception as e:
                logger.warning(f"Fix generation failed: {e}")
                return ""
        else:
            return ""

    return _generate_diff(original_file_content, diagnosis.proposed_fix, file_path)


def _generate_diff(
    original: str,
    fixed: str,
    file_path: str = "design.sv",
) -> str:
    """Generate a unified diff between original and fixed content."""
    original_lines = original.splitlines(keepends=True)
    fixed_lines = fixed.splitlines(keepends=True)

    diff = difflib.unified_diff(
        original_lines,
        fixed_lines,
        fromfile=f"a/{file_path}",
        tofile=f"b/{file_path}",
        lineterm="",
    )
    return "\n".join(diff)


# ─── Helpers ─────────────────────────────────────────────────────────

def _pattern_based_root_cause(result: DebugResult) -> str:
    """Generate a root cause description from classification alone."""
    causes = {
        "syntax": "Code syntax error — likely invalid SystemVerilog syntax in generated code.",
        "compilation": "Compilation error — module, port, or signal name mismatch.",
        "timing": "Timing violation — setup/hold or clock constraint issue.",
        "assertion_failure": "Assertion failed — design behavior doesn't match expected property.",
        "rtl_bug": "RTL logic error — output mismatch detected during simulation.",
        "testbench_bug": "Testbench issue — stimulus or checking logic error.",
        "overconstraint": "Formal properties overconstrained — assumptions too restrictive.",
        "unknown": "Unclassified failure — manual review recommended.",
    }
    cause = causes.get(result.classification, causes["unknown"])
    if result.affected_signals:
        cause += f" Affected signals: {', '.join(result.affected_signals[:5])}."
    return cause


def _recommend_action(classification: str) -> str:
    """Recommend next action based on failure type."""
    actions = {
        "syntax": "Check generated code syntax. Regenerate with stricter templates.",
        "compilation": "Verify module/port names against the mental model symbol table.",
        "timing": "Review clock constraints and timing assertions. Check reset sequences.",
        "assertion_failure": "Assertion failed — compare RTL behavior against spec requirement. Likely RTL bug.",
        "rtl_bug": "Review the failing check against spec. Fix the RTL logic and re-run.",
        "testbench_bug": "Check stimulus generation and driver timing. Fix testbench and re-run.",
        "overconstraint": "Relax formal assumptions. Some properties may be vacuously true.",
        "unknown": "Manual review needed. Check the full error log for clues.",
    }
    return actions.get(classification, actions["unknown"])
