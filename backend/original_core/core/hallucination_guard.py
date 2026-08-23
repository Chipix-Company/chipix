"""
Hallucination Guard — Multi-layer defense against LLM hallucinations.

When an agent hallucinates:
  1. SymbolTable catches unknown signal names
  2. Compiler catches syntax/semantic errors
  3. HallucinationGuard identifies and corrects common patterns
  4. RetryOrchestrator re-prompts with specific correction instructions

When the codebase is large:
  1. ContextManager provides focused, budget-aware context (not merge-all)
  2. ChunkAnalyzer breaks large modules into digestible chunks
  3. ProgressiveAnalysis builds understanding incrementally
  4. CrossReferenceCache avoids re-analyzing unchanged files
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from core.logger import get_logger
from core.symbol_table import SymbolTable, SymbolKind

logger = get_logger("HallucinationGuard")


# ═══════════════════════════════════════════════════════════════════════
# Common Hallucination Patterns
# ═══════════════════════════════════════════════════════════════════════

# Signals that LLMs commonly invent for RTL (not real port names)
HALLUCINATED_SIGNAL_PATTERNS = [
    r"\bdata_valid\b",       # LLM often adds this when not in spec
    r"\bvalid_out\b",
    r"\bready_in\b",
    r"\back_o\b",
    r"\berr_o\b",
    r"\bwren\b",
    r"\brden\b",
]

# Common hallucinated class/task names in UVM
HALLUCINATED_UVM_PATTERNS = [
    r"\bget_next_item\b.*\bitem\b",  # Correct: seq_item, not item
    r"\bcheck_output\b",              # Non-standard scoreboard task name
    r"\bref_model\b",                 # Often hallucinated reference model
]

# ── File types that should SKIP grounding checks ──────────────────────
# These files have their own naming conventions unrelated to DUT ports.
_SKIP_GROUNDING_SUFFIXES = {
    "_pkg.sv",    # Package files: typedefs, enums, structs — no DUT port names
}

# ── Lenient file types (lower grounding thresholds) ───────────────────
# These files reference DUT signals only in clocking blocks / modports.
_LENIENT_GROUNDING_SUFFIXES = {
    "_if.sv",     # Interface file: defines its own signals
    "_cov.sv",    # Coverage file: often uses abstract names
}

# ── Whitelisted UVM / testbench identifiers ───────────────────────────
# These are VALID standard UVM/TB names — never flag them as hallucinations.
UVM_SAFE_NAMES = {
    # UVM lifecycle
    "build_phase", "connect_phase", "run_phase", "start_of_simulation_phase",
    "end_of_elaboration_phase", "report_phase", "check_phase",
    # UVM utility
    "vif", "cfg", "env", "agent", "drv", "mon", "sqr", "scb",
    "trans", "pkt", "item", "req", "rsp", "seq", "seq_item",
    # Interface / clocking common names
    "Clocking", "clocking", "DRV", "MON", "Inputs", "Outputs",
    "master", "slave", "mp", "cb", "posedge", "negedge",
    # Randomization
    "rand", "randc", "randomize", "pre_randomize", "post_randomize",
    # Common signal abbreviations valid in TB context
    "busy", "stall", "flush",
    # UVM macros / fields
    "UVM_LOW", "UVM_MEDIUM", "UVM_HIGH", "UVM_NONE", "UVM_DEBUG",
    "UVM_PHASE_DONE", "UVM_ACTIVE", "UVM_PASSIVE",
    # Generic loop/temp vars already filtered by len>1 but kept for clarity
    "ii", "jj", "kk", "idx", "cnt", "tmp",
}


@dataclass
class HallucinationReport:
    """Report of detected and corrected hallucinations."""
    filename: str
    suspected_hallucinations: list[dict] = field(default_factory=list)
    corrections_applied: list[str] = field(default_factory=list)
    confidence_before: float = 1.0
    confidence_after: float = 1.0
    needs_regeneration: bool = False

    @property
    def hallucination_count(self) -> int:
        return len(self.suspected_hallucinations)

    def summary(self) -> str:
        return (
            f"{self.filename}: {self.hallucination_count} suspected hallucinations, "
            f"{len(self.corrections_applied)} corrections, "
            f"confidence: {self.confidence_before:.0%} → {self.confidence_after:.0%}, "
            f"needs_regen: {self.needs_regeneration}"
        )


class HallucinationGuard:
    """Multi-layer defense against LLM hallucinations.

    Defense layers (in order of application):

    Layer 1: PREVENTION (before generation)
        - SymbolTable constraint block injected into every prompt
        - "You MUST only use these names: [exact list]"
        - Temperature set low (0.1-0.3) for code generation

    Layer 2: DETECTION (after generation, before compilation)
        - Compare identifiers in generated code vs SymbolTable
        - Calculate grounding score (% of known names)
        - Flag unknown names as potential hallucinations

    Layer 3: CORRECTION (try to fix without regenerating)
        - Simple name substitution (common typos)
        - Port width correction (from SymbolTable data_type)
        - Remove obviously wrong code patterns

    Layer 4: RETRY (if correction fails)
        - Re-prompt with specific correction instructions
        - "You used 'data_valid' which doesn't exist. Use 'HREADY' instead."
        - Max 3 retry attempts

    Layer 5: COMPILE VALIDATION (final gate)
        - Actually compile the file with iverilog
        - Compilation errors reveal remaining hallucinations
        - Errors fed back as correction context

    Large Codebase Strategy:
        - ContextManager: focused context, never merge-all
        - Progressive: analyze top-level first, deep-dive on demand
        - Cached index: don't re-parse unchanged files
        - Module-by-module: analyze each module independently
    """

    # Grounding thresholds
    GROUNDING_ACCEPT = 0.75    # Above this: accept
    GROUNDING_RETRY = 0.50     # Below this and above halt: retry
    GROUNDING_HALT = 0.25      # Below this: halt, escalate

    def __init__(self, symbol_table: SymbolTable) -> None:
        self.symbol_table = symbol_table
        self._correction_history: list[dict] = []

    # ─────────────────────────────────────────────────────────────────
    # Layer 1: Prevention — Build constraint block for prompts
    # ─────────────────────────────────────────────────────────────────

    def build_prevention_prompt(
        self,
        module_name: str,
        file_type: str,
        extra_context: str = "",
    ) -> str:
        """Build a constraint block to PREPEND to every generation prompt.

        This is Layer 1 — prevention before the LLM even generates code.
        """
        ports = self.symbol_table.lookup_kind(SymbolKind.PORT)
        port_names = sorted(set(p.name for p in ports))
        signals = self.symbol_table.lookup_kind(SymbolKind.SIGNAL)
        signal_names = sorted(set(s.name for s in signals))
        params = self.symbol_table.get_names_by_kind(SymbolKind.PARAMETER)
        fsm_states = self.symbol_table.get_names_by_kind(SymbolKind.ENUM_VALUE)

        lines = [
            "╔══════════════════════════════════════════════════════════════╗",
            "║  ANTI-HALLUCINATION CONSTRAINTS — READ BEFORE GENERATING    ║",
            "╚══════════════════════════════════════════════════════════════╝",
            "",
            f"DUT Module: {module_name}",
            f"Generating: {file_type}",
            "",
            "EXACT PORT NAMES FROM RTL (case-sensitive, do NOT alter):",
        ]
        for p in ports:
            lines.append(
                f"  {p.direction:6s} {p.data_type:15s} {p.name}"
                + (f"  [{p.width}b]" if p.width > 1 else "")
            )

        if signal_names:
            lines.append("\nINTERNAL SIGNALS (registered, use as-is):")
            lines.append(f"  {', '.join(signal_names[:20])}")

        if params:
            lines.append(f"\nPARAMETERS: {', '.join(params)}")

        if fsm_states:
            lines.append(f"\nFSM STATES: {', '.join(fsm_states)}")

        lines += [
            "",
            "STRICT RULES:",
            "  1. NEVER invent signal/port names not listed above",
            "  2. NEVER change port directions or widths",
            "  3. NEVER add ports that don't exist in the DUT",
            "  4. If a signal you want doesn't exist, use the closest real one",
            "  5. Copy port names EXACTLY, including case (HADDR not haddr)",
            "",
        ]

        if extra_context:
            lines.append(extra_context)

        return "\n".join(lines)

    # ─────────────────────────────────────────────────────────────────
    # Layer 2: Detection — Scan generated code for hallucinations
    # ─────────────────────────────────────────────────────────────────

    def detect(self, filename: str, code: str) -> HallucinationReport:
        """Scan generated code and identify suspected hallucinations.

        File-type awareness:
          - *_pkg.sv  → SKIP grounding entirely (package definitions)
          - *_if.sv   → lenient thresholds (interface-specific naming)
          - everything else → normal grounding check
        """
        report = HallucinationReport(filename=filename)

        # ── Skip grounding for package files ─────────────────────────
        fname_lower = filename.lower()
        if any(fname_lower.endswith(suf) for suf in _SKIP_GROUNDING_SUFFIXES):
            logger.info(
                "Skipping hallucination grounding for package file: %s", filename
            )
            report.confidence_before = 1.0   # Treat as 100% grounded
            return report

        # ── Lenient mode for interface / coverage files ───────────────
        _lenient = any(fname_lower.endswith(suf) for suf in _LENIENT_GROUNDING_SUFFIXES)

        # Get grounding score from symbol table
        grounding = self.symbol_table.validate_names_in_code(code)
        report.confidence_before = grounding["grounding_score"]

        # Unknown names = potential hallucinations
        for name in grounding["unknown_names"]:
            # ── Never flag UVM-standard safe names ───────────────────
            if name in UVM_SAFE_NAMES or name.lower() in {n.lower() for n in UVM_SAFE_NAMES}:
                continue

            # Find context around the name
            context_match = re.search(
                rf".{{0,40}}\b{re.escape(name)}\b.{{0,40}}", code
            )
            context_snippet = context_match.group(0).strip() if context_match else ""

            # Estimate severity (lenient mode = downgrade severity by one level)
            severity = self._estimate_severity(name, code)
            if _lenient:
                severity = {"high": "medium", "medium": "low"}.get(severity, "low")

            report.suspected_hallucinations.append({
                "name": name,
                "context": context_snippet,
                "severity": severity,
                "suggestion": self._find_closest_real_name(name),
            })

        # Also check known hallucination patterns (not for lenient files)
        if not _lenient:
            for pattern in HALLUCINATED_SIGNAL_PATTERNS:
                if re.search(pattern, code, re.IGNORECASE):
                    m = re.search(pattern, code, re.IGNORECASE)
                    if m:
                        hallucinated_name = m.group(0).strip()
                        if not self.symbol_table.exists(hallucinated_name):
                            report.suspected_hallucinations.append({
                                "name": hallucinated_name,
                                "context": f"Pattern match: {pattern}",
                                "severity": "medium",
                                "suggestion": self._find_closest_real_name(hallucinated_name),
                            })

        # Log summary
        high_severity = [
            h for h in report.suspected_hallucinations if h.get("severity") == "high"
        ]
        if report.suspected_hallucinations:
            logger.warning(
                "Detected %d potential hallucinations in %s "
                "(grounding=%.0f%%, %d high-severity)%s",
                len(report.suspected_hallucinations), filename,
                report.confidence_before * 100, len(high_severity),
                " [LENIENT MODE]" if _lenient else "",
            )
        else:
            logger.info(
                "No hallucinations detected in %s (grounding=%.0f%%)",
                filename, report.confidence_before * 100,
            )

        return report

    def _estimate_severity(self, name: str, code: str) -> str:
        """Estimate how bad a hallucination is."""
        # Count occurrences — more uses = more severe
        count = len(re.findall(rf"\b{re.escape(name)}\b", code))

        # Port-like names are most dangerous (wrong connections)
        port_like = any(suffix in name.lower() for suffix in
                        ["_i", "_o", "_n", "clk", "rst", "addr", "data", "valid", "ready"])

        if count > 5 or port_like:
            return "high"
        elif count > 2:
            return "medium"
        else:
            return "low"

    def _find_closest_real_name(self, hallucinated: str) -> str:
        """Find the closest real name in the symbol table."""
        all_names = list(self.symbol_table._symbols.keys())
        if not all_names:
            return ""

        # Try case-insensitive exact match first
        low = hallucinated.lower()
        for name in all_names:
            if name.lower() == low:
                return name

        # Try prefix match
        for name in all_names:
            if name.lower().startswith(low[:4]) or low.startswith(name.lower()[:4]):
                return name

        # Try Levenshtein-like (simple: count common chars)
        def similarity(a: str, b: str) -> float:
            a, b = a.lower(), b.lower()
            common = sum(1 for c in a if c in b)
            return common / max(len(a), len(b), 1)

        best = max(all_names, key=lambda n: similarity(hallucinated, n), default="")
        return best

    # ─────────────────────────────────────────────────────────────────
    # Layer 3: Correction — Fix without regenerating
    # ─────────────────────────────────────────────────────────────────

    def correct(self, code: str, report: HallucinationReport) -> tuple[str, list[str]]:
        """Apply automatic corrections for detected hallucinations.

        CONSERVATIVE POLICY:
        - Only auto-correct names that are LOW severity AND have an
          exact (case-insensitive) match in the symbol table.
        - NEVER auto-correct medium/high severity — these corrections
          risk replacing valid UVM/TB-specific names with RTL port names.

        Returns:
            (corrected_code, list_of_corrections_applied)
        """
        corrections = []
        corrected = code

        for h in report.suspected_hallucinations:
            name = h["name"]
            suggestion = h["suggestion"]

            if not suggestion or suggestion == name:
                continue

            # ── Only auto-correct LOW severity with an exact symbol match ──
            if h["severity"] != "low":
                continue

            # Verify suggestion exists in the symbol table
            if not self.symbol_table.exists(suggestion):
                continue

            # Safety: don't replace if name is a known-safe UVM term
            if name in UVM_SAFE_NAMES:
                continue

            # Apply word-boundary replacement
            corrected = re.sub(rf"\b{re.escape(name)}\b", suggestion, corrected)
            corrections.append(f"Corrected: '{name}' → '{suggestion}'")

        report.corrections_applied = corrections
        return corrected, corrections

    # ─────────────────────────────────────────────────────────────────
    # Layer 4: Retry prompt — Tell LLM exactly what was wrong
    # ─────────────────────────────────────────────────────────────────

    def build_retry_prompt(
        self, original_prompt: str, code: str, report: HallucinationReport,
    ) -> str:
        """Build a correction prompt for the retry attempt.

        Instead of generic "fix the code", we tell the AI exactly
        what it hallucinated and what the correct names are.
        """
        lines = [
            "╔══════════════════════════════════════════════════════════════╗",
            "║  CORRECTION REQUIRED — HALLUCINATIONS DETECTED              ║",
            "╚══════════════════════════════════════════════════════════════╝",
            "",
            "Your previous output contained the following ERRORS:",
            "(These names do NOT exist in the RTL design)",
            "",
        ]

        for h in report.suspected_hallucinations[:10]:
            suggestion_text = (
                f" → Use '{h['suggestion']}' instead" if h["suggestion"] else ""
            )
            lines.append(
                f"  ❌ '{h['name']}' does not exist"
                f"{suggestion_text}"
                f" (severity: {h['severity']})"
            )
            if h.get("context"):
                lines.append(f"     Context: ...{h['context']}...")

        lines += [
            "",
            "CORRECTION INSTRUCTIONS:",
            "1. Replace all invalid names with their correct RTL counterparts",
            "2. Only use port/signal names from the REGISTERED NAMES list above",
            "3. Do NOT add new ports not in the DUT",
            "4. Do NOT change port widths or directions",
            "",
            "PREVIOUS GENERATED CODE (for reference):",
            "```systemverilog",
            code[:2000] + ("..." if len(code) > 2000 else ""),
            "```",
            "",
            "Now regenerate the COMPLETE corrected version.",
        ]

        return "\n".join([
            self.build_prevention_prompt("DUT", "correction"),
            "\n".join(lines),
        ])

    # ─────────────────────────────────────────────────────────────────
    # Decisions
    # ─────────────────────────────────────────────────────────────────

    def decide(self, report: HallucinationReport) -> str:
        """Decide what to do based on hallucination severity.

        Returns: 'accept' | 'correct' | 'retry' | 'halt'
        """
        high = [h for h in report.suspected_hallucinations if h["severity"] == "high"]
        score = report.confidence_before

        # Package files and clean files are always accepted
        if not report.suspected_hallucinations:
            return "accept"

        if score >= self.GROUNDING_ACCEPT and not high:
            return "accept"
        elif len(high) <= 2 and score >= self.GROUNDING_RETRY:
            return "correct"   # Try auto-correction for low-severity only
        elif score >= self.GROUNDING_HALT:
            return "retry"     # Re-prompt with specific corrections
        else:
            return "halt"      # Too many hallucinations, must escalate


# ═══════════════════════════════════════════════════════════════════════
# Large Codebase Strategy
# ═══════════════════════════════════════════════════════════════════════

class LargeCodebaseStrategy:
    """Strategy for handling very large RTL codebases (100+ files).

    Problem: A 500-file SoC cannot fit in any LLM context window.

    Solution: Progressive, hierarchical analysis:
      Phase 1: Index everything locally (no AI) → build module map
      Phase 2: Analyze top-level structure (hierarchy only) → identify DUT
      Phase 3: Focused deep-dive on DUT + its direct neighbors
      Phase 4: Per-module incremental analysis (cached)
      Phase 5: Only the DUT's context goes to verification generation

    This is exactly what Claude Code does for large repos.
    """

    def __init__(self) -> None:
        self._analysis_cache: dict[str, dict] = {}  # module_name → cached analysis
        self._changed_files: set[str] = set()

    def get_analysis_order(self, modules: dict, top_module: str) -> list[str]:
        """Return the order to analyze modules.

        Order: top → direct children → deeper levels
        Only analyze modules relevant to the verification target.
        """
        order = []
        visited = set()

        def _dfs(name: str, depth: int) -> None:
            if name in visited or depth > 5:  # Cap depth
                return
            visited.add(name)
            order.append((depth, name))
            mod = modules.get(name)
            if mod:
                for inst in (mod.instantiations or []):
                    _dfs(inst.get("module", ""), depth + 1)

        _dfs(top_module, 0)
        # Sort by depth (top first) then by name
        order.sort(key=lambda x: x[0])
        return [name for _, name in order]

    def should_deep_analyze(self, module_name: str, dut_name: str, modules: dict) -> bool:
        """Decide whether a module needs full AI analysis.

        Only these modules need AI analysis:
          - The DUT itself (always)
          - The DUT's parent (always — for integration context)
          - Direct sub-modules of the DUT (behavioral understanding)
          - Modules the DUT directly interfaces with

        Other modules only need structural parsing (regex, no AI).
        """
        if module_name == dut_name:
            return True  # DUT always needs deep analysis

        mod = modules.get(module_name, {})

        # Check if this module instantiates the DUT (is a parent)
        instantiates_dut = any(
            inst.get("module") == dut_name
            for inst in getattr(mod, "instantiations", []) or []
        )
        if instantiates_dut:
            return True  # Parent needs deep analysis

        # Check if DUT instantiates this module (is a child)
        dut_mod = modules.get(dut_name, {})
        dut_instantiates = any(
            inst.get("module") == module_name
            for inst in getattr(dut_mod, "instantiations", []) or []
        )
        if dut_instantiates:
            return True  # Direct child needs deep analysis

        # Everything else: structural only (no AI cost)
        return False

    def get_ai_call_budget(self, total_modules: int, dut_name: str, modules: dict) -> dict:
        """Calculate how many AI calls are needed.

        Shows the massive savings vs naive approach.
        """
        deep_modules = [
            m for m in modules
            if self.should_deep_analyze(m, dut_name, modules)
        ]

        return {
            "total_modules": total_modules,
            "ai_analysis_needed": len(deep_modules),
            "regex_only": total_modules - len(deep_modules),
            "ai_calls_naive": total_modules,           # Old approach
            "ai_calls_smart": len(deep_modules),       # New approach
            "savings_percent": int((1 - len(deep_modules) / max(total_modules, 1)) * 100),
            "modules_for_ai": deep_modules,
        }
