"""
Gap 6 — Living Mental Model.

A mental model that evolves with RTL changes:
  1. Tracks RTL file modification times
  2. Detects what changed (diff-based)
  3. Incrementally updates the mental model
  4. Maintains version history

Gap 7 — Coverage Waivers.
Gap 9 — Bug Classification.
Gap 10 — Testbench Diff Preview + Tcl Generator.

All grouped here for efficiency.
"""

from __future__ import annotations

import difflib
import hashlib
import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

import config
from core.ai_client import AIClient
from core.logger import get_logger

logger = get_logger("LivingModel")


# ═══════════════════════════════════════════════════════════════════════
# Gap 6: Living Mental Model
# ═══════════════════════════════════════════════════════════════════════

@dataclass
class RTLSnapshot:
    """Snapshot of RTL state at a point in time."""
    timestamp: float = 0.0
    file_hashes: dict[str, str] = field(default_factory=dict)
    model_version: int = 0


class LivingMentalModel:
    """Mental model that tracks and adapts to RTL changes."""

    def __init__(self, ai_client: AIClient, output_dir: str | Path = "output") -> None:
        self.ai_client = ai_client
        self.output_dir = Path(output_dir)
        self.model_dir = self.output_dir / "mental_model"
        self.model_dir.mkdir(parents=True, exist_ok=True)
        self.snapshots: list[RTLSnapshot] = []
        self.current_model: str = ""
        self.version: int = 0

    def take_snapshot(self, rtl_files: list[str | Path]) -> RTLSnapshot:
        """Take a snapshot of current RTL state."""
        snapshot = RTLSnapshot(
            timestamp=time.time(),
            model_version=self.version,
        )
        for f in rtl_files:
            path = Path(f)
            if path.exists():
                content = path.read_text(encoding="utf-8", errors="replace")
                snapshot.file_hashes[str(path)] = hashlib.md5(content.encode()).hexdigest()
        return snapshot

    def detect_changes(self, rtl_files: list[str | Path]) -> dict[str, str]:
        """Detect what changed since last snapshot.

        Returns:
            Dict of changed_file -> unified_diff
        """
        if not self.snapshots:
            return {}

        last = self.snapshots[-1]
        changes = {}

        for f in rtl_files:
            path = Path(f)
            if not path.exists():
                continue

            content = path.read_text(encoding="utf-8", errors="replace")
            new_hash = hashlib.md5(content.encode()).hexdigest()
            old_hash = last.file_hashes.get(str(path), "")

            if new_hash != old_hash:
                # File changed — compute diff
                old_content = self._load_cached(str(path), last.model_version)
                if old_content:
                    diff = "\n".join(difflib.unified_diff(
                        old_content.splitlines(),
                        content.splitlines(),
                        fromfile=f"v{last.model_version}/{path.name}",
                        tofile=f"v{self.version + 1}/{path.name}",
                    ))
                    changes[str(path)] = diff
                else:
                    changes[str(path)] = f"[NEW/MODIFIED] {path.name}"

                # Cache new content
                self._cache_content(str(path), content, self.version + 1)

        return changes

    async def update_model(
        self,
        rtl_files: list[str | Path],
        current_rtl: str,
    ) -> str:
        """Incrementally update the mental model based on RTL changes.

        Only regenerates the parts that changed.
        """
        changes = self.detect_changes(rtl_files)

        if not changes:
            logger.info("No RTL changes detected — mental model up to date")
            return self.current_model

        logger.info("RTL changes in %d files — updating mental model", len(changes))

        diffs = "\n\n".join(
            f"=== {Path(f).name} ===\n{d}" for f, d in changes.items()
        )

        prompt = """\
You are updating an existing mental model based on RTL changes.

CURRENT MENTAL MODEL:
{current_model}

RTL CHANGES (diffs):
{diffs}

TASK: Update ONLY the parts of the mental model affected by these changes.
- Keep everything that hasn't changed
- Update descriptions for modified functionality
- Add new sections for new features
- Mark removed features as deprecated

OUTPUT: Return the COMPLETE updated mental model.
"""

        response = await self.ai_client.generate_with_context(
            system_prompt=prompt.format(
                current_model=self.current_model[:5000],
                diffs=diffs[:3000],
            ),
            context_blocks={"CURRENT RTL": current_rtl},
            focus_instruction="Incrementally update mental model",
            task_instruction="Apply RTL diffs to the mental model",
        )

        self.version += 1
        self.current_model = response
        self.snapshots.append(self.take_snapshot(rtl_files))

        # Save versioned model
        path = self.model_dir / f"mental_model_v{self.version}.md"
        path.write_text(response, encoding="utf-8")

        logger.info("Mental model updated to v%d", self.version)
        return response

    def _cache_content(self, filepath: str, content: str, version: int) -> None:
        cache_dir = self.model_dir / "cache"
        cache_dir.mkdir(exist_ok=True)
        name = Path(filepath).name
        (cache_dir / f"{name}.v{version}").write_text(content, encoding="utf-8")

    def _load_cached(self, filepath: str, version: int) -> str | None:
        name = Path(filepath).name
        cache_path = self.model_dir / "cache" / f"{name}.v{version}"
        if cache_path.exists():
            return cache_path.read_text(encoding="utf-8")
        return None


# ═══════════════════════════════════════════════════════════════════════
# Gap 7: Coverage Waivers
# ═══════════════════════════════════════════════════════════════════════

@dataclass
class CoverageWaiver:
    """A coverage waiver — an unreachable cover point."""
    coverpoint: str
    reason: str
    waiver_type: str = "unreachable"    # unreachable, not_applicable, deferred
    approved_by: str = ""
    timestamp: str = ""


class CoverageWaiverEngine:
    """Suggests and manages coverage waivers for sign-off."""

    def __init__(self, ai_client: AIClient) -> None:
        self.ai_client = ai_client
        self.waivers: list[CoverageWaiver] = []

    async def suggest_waivers(
        self,
        uncovered_bins: list[str],
        rtl_code: str,
        coverage_report: str = "",
    ) -> list[CoverageWaiver]:
        """Use LLM to identify unreachable coverage points.

        Some bins may be architecturally impossible to hit.
        """
        prompt = f"""\
You are a coverage closure expert.

UNCOVERED COVERAGE BINS:
{chr(10).join(f'  - {b}' for b in uncovered_bins)}

TASK: For each uncovered bin, determine if it is:
  1. UNREACHABLE — architecturally impossible (suggest waiver)
  2. NOT_APPLICABLE — not relevant to this design (suggest waiver)
  3. REACHABLE — needs a targeted test (do NOT waive)

For each waiver suggestion, provide:
  - coverpoint: the bin name
  - waiver_type: unreachable | not_applicable
  - reason: detailed explanation of why it can't be hit

OUTPUT: JSON array of waiver suggestions.
"""

        context = {"RTL CODE": rtl_code}
        if coverage_report:
            context["COVERAGE REPORT"] = coverage_report

        response = await self.ai_client.generate_with_context(
            system_prompt=prompt,
            context_blocks=context,
            focus_instruction="Analyze coverage bins for waivers",
            task_instruction="Identify unreachable coverage points",
        )

        waivers = self._parse_waivers(response)
        self.waivers.extend(waivers)

        logger.info("Coverage waivers: %d suggested", len(waivers))
        return waivers

    def _parse_waivers(self, response: str) -> list[CoverageWaiver]:
        json_match = re.search(r"\[.*\]", response, re.DOTALL)
        if json_match:
            try:
                items = json.loads(json_match.group())
                return [
                    CoverageWaiver(
                        coverpoint=item.get("coverpoint", ""),
                        reason=item.get("reason", ""),
                        waiver_type=item.get("waiver_type", "unreachable"),
                    )
                    for item in items
                ]
            except json.JSONDecodeError:
                pass
        return []

    def generate_waiver_report(self) -> str:
        """Generate waiver report for sign-off."""
        lines = ["# Coverage Waiver Report", ""]
        lines.append(f"**Total Waivers: {len(self.waivers)}**\n")
        lines.append("| Coverpoint | Type | Reason |")
        lines.append("|------------|------|--------|")
        for w in self.waivers:
            lines.append(f"| {w.coverpoint} | {w.waiver_type} | {w.reason} |")
        return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════════
# Gap 9: Bug Classification
# ═══════════════════════════════════════════════════════════════════════

class BugClassifier:
    """Classify bugs by type for triage."""

    BUG_CATEGORIES = [
        "timing",          # Setup/hold, clock domain crossing
        "protocol",        # Bus protocol violation
        "logic",           # Incorrect computation
        "connectivity",    # Wrong signal connection
        "reset",           # Reset-related issues
        "overflow",        # Arithmetic overflow/underflow
        "fsm",             # State machine issues
        "memory",          # Memory access issues
        "constraint",      # Constraint issues in randomization
        "testbench",       # Testbench bug (not RTL bug)
    ]

    def classify(self, error_message: str, context: str = "") -> dict:
        """Classify a bug based on error message and context.

        Returns:
            {category, confidence, is_rtl_bug, description}
        """
        msg_lower = error_message.lower()

        # Pattern matching for quick classification
        if any(w in msg_lower for w in ["setup", "hold", "clock", "timing", "skew"]):
            return {"category": "timing", "confidence": 0.9, "is_rtl_bug": True,
                    "description": "Timing violation detected"}

        if any(w in msg_lower for w in ["valid", "ready", "handshake", "protocol"]):
            return {"category": "protocol", "confidence": 0.85, "is_rtl_bug": True,
                    "description": "Bus protocol violation"}

        if any(w in msg_lower for w in ["mismatch", "expected", "actual", "wrong value"]):
            return {"category": "logic", "confidence": 0.8, "is_rtl_bug": True,
                    "description": "Logic/computation error"}

        if any(w in msg_lower for w in ["unconnected", "floating", "driven", "multiple drivers"]):
            return {"category": "connectivity", "confidence": 0.9, "is_rtl_bug": True,
                    "description": "Signal connectivity issue"}

        if any(w in msg_lower for w in ["reset", "initial", "undefined", "x value"]):
            return {"category": "reset", "confidence": 0.8, "is_rtl_bug": True,
                    "description": "Reset-related issue"}

        if any(w in msg_lower for w in ["overflow", "underflow", "wrap"]):
            return {"category": "overflow", "confidence": 0.85, "is_rtl_bug": True,
                    "description": "Arithmetic overflow/underflow"}

        if any(w in msg_lower for w in ["state", "fsm", "illegal state", "deadlock"]):
            return {"category": "fsm", "confidence": 0.85, "is_rtl_bug": True,
                    "description": "FSM state machine issue"}

        if any(w in msg_lower for w in ["constraint", "randomize", "solve order"]):
            return {"category": "constraint", "confidence": 0.9, "is_rtl_bug": False,
                    "description": "Testbench constraint issue"}

        if any(w in msg_lower for w in ["testbench", "sequence", "driver", "monitor"]):
            return {"category": "testbench", "confidence": 0.7, "is_rtl_bug": False,
                    "description": "Testbench infrastructure issue"}

        return {"category": "logic", "confidence": 0.5, "is_rtl_bug": True,
                "description": "Unclassified — needs manual review"}


# ═══════════════════════════════════════════════════════════════════════
# Gap 8: Tcl Script Generator
# ═══════════════════════════════════════════════════════════════════════

class TclGenerator:
    """Generate Tcl scripts for commercial EDA tools."""

    @staticmethod
    def jasper_script(
        module_name: str,
        rtl_files: list[str],
        bind_file: str = "",
        clock: str = "clk",
        reset: str = "rst_n",
        reset_active_low: bool = True,
    ) -> str:
        """Generate Jasper/JasperGold Tcl script."""
        files_list = "\n".join(f"  {f} \\" for f in rtl_files)
        bind_line = f"  {bind_file} \\" if bind_file else ""

        reset_expr = f"!{reset}" if reset_active_low else reset

        return f"""\
# JasperGold Formal Verification Script
# Module: {module_name}
# Auto-generated by ChipVerify AI

clear -all

# Read design files
analyze -sv \\
{files_list}
{bind_line}

# Elaborate
elaborate -top {module_name}

# Clock and reset
clock {clock}
reset {{{reset_expr}}}

# Set engine options
set_prove_time_limit 300s
set_max_trace_length 50

# Prove all properties
prove -all

# Report results
report -results -file {module_name}_formal_results.rpt
report -coverage -file {module_name}_formal_coverage.rpt

# Generate summary
puts "==== Formal Verification Complete ===="
puts "Properties: [get_property_count]"
puts "Proven:     [get_property_count -status proven]"
puts "Failed:     [get_property_count -status cex]"
"""

    @staticmethod
    def vc_formal_script(
        module_name: str,
        rtl_files: list[str],
        bind_file: str = "",
        clock: str = "clk",
        reset: str = "rst_n",
    ) -> str:
        """Generate Synopsys VC Formal Tcl script."""
        files_list = " \\\n  ".join(rtl_files)
        bind_line = f" \\\n  {bind_file}" if bind_file else ""

        return f"""\
# VC Formal Verification Script
# Module: {module_name}
# Auto-generated by ChipVerify AI

set_app_var search_path ". .."

# Read design
read_file -type verilog \\
  {files_list}{bind_line}

# Set top module
set_top {module_name}

# Clock and reset
create_clock {clock} -period 10
create_reset {reset} -sense low

# Configure engines
set_engine_mode {{Hp Ht B}}
set_prove_time_limit 300

# Run formal
run_formal

# Report
report_formal -results > {module_name}_vcf_results.rpt
report_formal -coverage > {module_name}_vcf_coverage.rpt

puts "==== VC Formal Complete ===="
"""

    @staticmethod
    def xcelium_script(
        module_name: str,
        rtl_files: list[str],
        tb_top: str = "tb_top",
        uvm_test: str = "",
    ) -> str:
        """Generate Cadence Xcelium run script."""
        files_list = " \\\n  ".join(rtl_files)
        test_arg = f'+UVM_TESTNAME={uvm_test}' if uvm_test else ''

        return f"""\
#!/bin/bash
# Xcelium Simulation Script
# Module: {module_name}
# Auto-generated by ChipVerify AI

xrun -sv -64bit \\
  -access +rwc \\
  -timescale 1ns/1ps \\
  -top {tb_top} \\
  -coverage all \\
  -covoverwrite \\
  {test_arg} \\
  {files_list}

echo "==== Xcelium Simulation Complete ===="
"""

    @staticmethod
    def vcs_script(
        module_name: str,
        rtl_files: list[str],
        tb_top: str = "tb_top",
        uvm_test: str = "",
    ) -> str:
        """Generate Synopsys VCS compile + run script."""
        files_list = " \\\n  ".join(rtl_files)
        test_arg = f'+UVM_TESTNAME={uvm_test}' if uvm_test else ''

        return f"""\
#!/bin/bash
# VCS Simulation Script
# Module: {module_name}
# Auto-generated by ChipVerify AI

# Compile
vcs -sverilog -full64 \\
  -debug_access+all \\
  -timescale=1ns/1ps \\
  -top {tb_top} \\
  -cm line+cond+fsm+branch+tgl \\
  +lint=all \\
  -o simv \\
  {files_list}

# Run
./simv {test_arg} \\
  -cm line+cond+fsm+branch+tgl \\
  +ntb_random_seed_automatic \\
  -l {module_name}_sim.log

echo "==== VCS Simulation Complete ===="
"""


# ═══════════════════════════════════════════════════════════════════════
# Gap 10: Diff Engine (Testbench Diff Preview)
# ═══════════════════════════════════════════════════════════════════════

class DiffEngine:
    """Generate and apply diffs for testbench changes."""

    @staticmethod
    def compute_diff(original: str, modified: str, filename: str = "file.sv") -> str:
        """Compute a unified diff between original and modified code."""
        diff = difflib.unified_diff(
            original.splitlines(keepends=True),
            modified.splitlines(keepends=True),
            fromfile=f"a/{filename}",
            tofile=f"b/{filename}",
        )
        return "".join(diff)

    @staticmethod
    def apply_patch(original: str, diff_text: str) -> str:
        """Apply a unified diff patch to original content."""
        lines = original.splitlines(keepends=True)
        result = []

        for line in diff_text.splitlines(keepends=True):
            if line.startswith("---") or line.startswith("+++") or line.startswith("@@"):
                continue
            if line.startswith("+"):
                result.append(line[1:])
            elif line.startswith("-"):
                continue  # Remove line
            elif line.startswith(" "):
                result.append(line[1:])

        return "".join(result) if result else original

    @staticmethod
    def format_diff_summary(diff_text: str) -> str:
        """Generate a human-readable summary of changes."""
        additions = sum(1 for l in diff_text.splitlines() if l.startswith("+") and not l.startswith("+++"))
        deletions = sum(1 for l in diff_text.splitlines() if l.startswith("-") and not l.startswith("---"))
        return f"+{additions}/-{deletions} lines changed"

    @staticmethod
    def preview_changes(original: str, modified: str, filename: str = "file.sv") -> dict:
        """Generate a complete change preview.

        Returns:
            {diff, summary, additions, deletions, changed_functions}
        """
        diff = DiffEngine.compute_diff(original, modified, filename)
        additions = sum(1 for l in diff.splitlines() if l.startswith("+") and not l.startswith("+++"))
        deletions = sum(1 for l in diff.splitlines() if l.startswith("-") and not l.startswith("---"))

        # Find changed functions/tasks
        changed_funcs = set()
        for line in diff.splitlines():
            if line.startswith("+") or line.startswith("-"):
                func_match = re.search(r"(?:task|function)\s+\w+\s+(\w+)", line)
                if func_match:
                    changed_funcs.add(func_match.group(1))

        return {
            "diff": diff,
            "summary": f"+{additions}/-{deletions} lines",
            "additions": additions,
            "deletions": deletions,
            "changed_functions": list(changed_funcs),
        }
