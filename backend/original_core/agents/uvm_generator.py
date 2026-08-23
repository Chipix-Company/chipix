"""
Phase B — UVM File Generator.

Full UVM methodology support with 18 per-file system prompts.
Each file gets its own tailored prompt, validator, and dependency chain.

Files generated (in order):
  1. *_pkg.sv        — Package with types, enums, parameters
  2. *_if.sv         — Interface with clocking blocks + modports
  3. *_seq_item.sv   — Transaction class (uvm_sequence_item)
  4. *_driver.sv     — UVM driver
  5. *_monitor.sv    — UVM monitor
  6. *_sequencer.sv  — UVM sequencer (typedef)
  7. *_agent.sv      — UVM agent (driver + monitor + sequencer)
  8. *_scoreboard.sv — Reference model + checker
  9. *_coverage.sv   — Functional coverage collector
  10. *_env.sv        — UVM environment
  11. *_base_test.sv  — Base test class
  12. *_seq_lib.sv    — Sequence library
  13. *_tests.sv      — Directed test cases
  14. *_assertions.sv — SVA bind module
  15. *_ral.sv        — Register Abstraction Layer (optional)
  16. tb_top.sv       — Top testbench module
  17. Makefile        — Build + run + regression
  18. test_plan.md    — Verification plan document
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from pathlib import Path

import config
from core.ai_client import AIClient
from core.hallucination_guard import HallucinationGuard
from core.logger import get_logger
from core.models import (
    DesignSpecification,
    MentalModel,
    RTLAnalysis,
)
from core.symbol_table import SymbolTable

logger = get_logger("UVMGenerator")


# ═══════════════════════════════════════════════════════════════════════
# UVM System Prompts — One per file
# ═══════════════════════════════════════════════════════════════════════

UVM_SYSTEM_PROMPTS: dict[str, str] = {

    # ── 1. Package ────────────────────────────────────────────────────
    "pkg.sv": """\
You are a UVM verification expert. Generate a SystemVerilog PACKAGE file.

TASK: Create a package that defines all types, enums, parameters, and typedefs needed by the verification environment.

RULES:
1. Package name: {module}_pkg
2. Include `import uvm_pkg::*;` and ``include "uvm_macros.svh"`
3. Define transaction-related enums (e.g., operation types, states)
4. Define any needed parameters (data widths, address widths)
5. Define typedefs for convenience
6. This package will be imported by ALL other verification files
7. Must compile with `iverilog -g2012`

OUTPUT: Return ONLY the SystemVerilog code inside a ```systemverilog code block.
""",

    # ── 2. Interface ──────────────────────────────────────────────────
    "if.sv": """\
You are a UVM verification expert. Generate a SystemVerilog INTERFACE file.

TASK: Create an interface that encapsulates all DUT signals with clocking blocks and modports.

RULES:
1. Interface name: {module}_if
2. Include ALL DUT ports as interface signals
3. Create clocking blocks:
   - cb_driver: for driving signals (output skew)
   - cb_monitor: for sampling signals (input skew)
4. Create modports:
   - DRV: for driver (uses cb_driver)
   - MON: for monitor (uses cb_monitor)
   - DUT: for DUT connection (direct)
5. Accept clock as input parameter
6. Must compile with `iverilog -g2012`

OUTPUT: Return ONLY the SystemVerilog code inside a ```systemverilog code block.
""",

    # ── 3. Sequence Item ──────────────────────────────────────────────
    "seq_item.sv": """\
You are a UVM verification expert. Generate a SystemVerilog SEQUENCE ITEM file.

TASK: Create a UVM sequence item (transaction class) that represents a single stimulus/response.

RULES:
1. Class name: {module}_seq_item extends uvm_sequence_item
2. Register with `uvm_object_utils
3. Include rand fields for all inputs
4. Include non-rand fields for all outputs (for comparison)
5. Implement: new(), do_copy(), do_compare(), convert2string()
6. Add meaningful constraints
7. Import {module}_pkg

OUTPUT: Return ONLY the SystemVerilog code inside a ```systemverilog code block.
""",

    # ── 4. Driver ─────────────────────────────────────────────────────
    "driver.sv": """\
You are a UVM verification expert. Generate a SystemVerilog UVM DRIVER file.

TASK: Create a UVM driver that drives transactions to the DUT through the interface.

RULES:
1. Class name: {module}_driver extends uvm_driver #({module}_seq_item)
2. Register with `uvm_component_utils
3. Declare virtual interface: virtual {module}_if vif
4. Implement build_phase: get virtual interface from config_db
5. Implement run_phase: forever loop calling seq_item_port.get_next_item()
6. Create task drive_transaction({module}_seq_item txn)
7. Drive using vif.cb_driver clocking block
8. Call seq_item_port.item_done() after driving

OUTPUT: Return ONLY the SystemVerilog code inside a ```systemverilog code block.
""",

    # ── 5. Monitor ────────────────────────────────────────────────────
    "monitor.sv": """\
You are a UVM verification expert. Generate a SystemVerilog UVM MONITOR file.

TASK: Create a UVM monitor that observes DUT outputs and sends transactions to analysis ports.

RULES:
1. Class name: {module}_monitor extends uvm_monitor
2. Register with `uvm_component_utils
3. Declare virtual interface and analysis port
4. Implement build_phase: get vif, create analysis port
5. Implement run_phase: forever loop sampling transactions
6. Task collect_transaction: sample from vif.cb_monitor, create seq_item, write to analysis port
7. Sample on clock edges using the clocking block
8. Passive — never drives signals

OUTPUT: Return ONLY the SystemVerilog code inside a ```systemverilog code block.
""",

    # ── 6. Sequencer ──────────────────────────────────────────────────
    "sequencer.sv": """\
You are a UVM verification expert. Generate a SystemVerilog UVM SEQUENCER file.

TASK: Create a UVM sequencer typedef.

RULES:
1. Typedef: typedef uvm_sequencer #({module}_seq_item) {module}_sequencer;
2. Import the package
3. Keep it minimal — UVM sequencer needs no customization for basic use
4. Include any needed imports

OUTPUT: Return ONLY the SystemVerilog code inside a ```systemverilog code block.
""",

    # ── 7. Agent ──────────────────────────────────────────────────────
    "agent.sv": """\
You are a UVM verification expert. Generate a SystemVerilog UVM AGENT file.

TASK: Create a UVM agent that encapsulates driver, monitor, and sequencer.

RULES:
1. Class name: {module}_agent extends uvm_agent
2. Register with `uvm_component_utils
3. Declare handles: driver, monitor, sequencer
4. Implement build_phase: create components based on is_active
5. Implement connect_phase: connect driver.seq_item_port to sequencer.seq_item_export
6. Active agent: driver + sequencer + monitor
7. Passive agent: monitor only (when is_active == UVM_PASSIVE)

OUTPUT: Return ONLY the SystemVerilog code inside a ```systemverilog code block.
""",

    # ── 8. Scoreboard ─────────────────────────────────────────────────
    "scoreboard.sv": """\
You are a UVM verification expert. Generate a SystemVerilog UVM SCOREBOARD file.

TASK: Create a scoreboard with a reference model that checks DUT outputs.

RULES:
1. Class name: {module}_scoreboard extends uvm_scoreboard
2. Register with `uvm_component_utils
3. Declare uvm_tlm_analysis_fifo for receiving transactions
4. Implement build_phase: create FIFOs
5. Implement run_phase: get transactions from FIFOs, compare actual vs expected
6. Create function compute_expected(): golden reference model
7. Track pass/fail counts
8. Implement report_phase: print summary with PASS/FAIL counts

OUTPUT: Return ONLY the SystemVerilog code inside a ```systemverilog code block.
""",

    # ── 9. Coverage ───────────────────────────────────────────────────
    "coverage.sv": """\
You are a UVM verification expert. Generate a SystemVerilog COVERAGE COLLECTOR file.

TASK: Create a functional coverage collector as a UVM subscriber.

RULES:
1. Class name: {module}_coverage extends uvm_subscriber #({module}_seq_item)
2. Register with `uvm_component_utils
3. Define covergroups for:
   - Input signal values and ranges
   - Output signal values
   - Cross coverage between related signals
   - Transition coverage for state changes
4. Implement write() function to sample covergroups
5. Target >= 95% coverage
6. Include bins for corner cases

OUTPUT: Return ONLY the SystemVerilog code inside a ```systemverilog code block.
""",

    # ── 10. Environment ───────────────────────────────────────────────
    "env.sv": """\
You are a UVM verification expert. Generate a SystemVerilog UVM ENVIRONMENT file.

TASK: Create a UVM environment that wires all components together.

RULES:
1. Class name: {module}_env extends uvm_env
2. Register with `uvm_component_utils
3. Declare handles: agent, scoreboard, coverage
4. Implement build_phase: create all components, set config_db for vif
5. Implement connect_phase: wire agent.monitor.analysis_port to scoreboard and coverage
6. The env is the main container — no test logic here

OUTPUT: Return ONLY the SystemVerilog code inside a ```systemverilog code block.
""",

    # ── 11. Base Test ─────────────────────────────────────────────────
    "base_test.sv": """\
You are a UVM verification expert. Generate a SystemVerilog UVM BASE TEST file.

TASK: Create a base test class that all specific tests extend.

RULES:
1. Class name: {module}_base_test extends uvm_test
2. Register with `uvm_component_utils
3. Declare environment handle
4. Implement build_phase: create environment, configure it
5. Implement end_of_elaboration_phase: print topology
6. Implement report_phase: print test summary
7. All specific tests will extend this base class

OUTPUT: Return ONLY the SystemVerilog code inside a ```systemverilog code block.
""",

    # ── 12. Sequence Library ──────────────────────────────────────────
    "seq_lib.sv": """\
You are a UVM verification expert. Generate a SystemVerilog SEQUENCE LIBRARY file.

TASK: Create multiple sequences for stimulus generation.

RULES:
1. Base sequence: {module}_base_seq extends uvm_sequence #({module}_seq_item)
2. Include these sequences:
   - {module}_reset_seq: Reset sequence
   - {module}_basic_seq: Normal operation
   - {module}_random_seq: Fully randomized stimulus
   - {module}_corner_seq: Corner cases and boundary conditions
   - {module}_stress_seq: Back-to-back rapid stimulus
3. Each sequence: register with `uvm_object_utils, implement body()
4. Use `uvm_do_with or start_item/finish_item

OUTPUT: Return ONLY the SystemVerilog code inside a ```systemverilog code block.
""",

    # ── 13. Tests ─────────────────────────────────────────────────────
    "tests.sv": """\
You are a UVM verification expert. Generate a SystemVerilog UVM TESTS file.

TASK: Create specific test classes that extend the base test.

RULES:
1. Each test extends {module}_base_test
2. Include these tests:
   - {module}_basic_test: Run basic_seq
   - {module}_random_test: Run random_seq for N iterations
   - {module}_corner_test: Run corner_seq
   - {module}_full_regression_test: Run all sequences
3. Each test: create sequence in run_phase, start on sequencer
4. Use phase.raise_objection / drop_objection
5. Register with `uvm_component_utils

OUTPUT: Return ONLY the SystemVerilog code inside a ```systemverilog code block.
""",

    # ── 14. Assertions ────────────────────────────────────────────────
    "assertions.sv": """\
You are a UVM verification expert. Generate a SystemVerilog ASSERTIONS module.

TASK: Create an SVA assertions module that binds to the DUT.

RULES:
1. Module name: {module}_assertions
2. Accept all DUT ports as inputs
3. Write SVA properties for:
   - Reset behavior
   - Functional correctness
   - Protocol compliance
   - No X/Z on outputs after reset
4. Use `assert property` with `$error on failure
5. Include cover properties for reachability
6. Use bind statement to attach to DUT: bind {module} {module}_assertions inst_assertions (.*)

OUTPUT: Return ONLY the SystemVerilog code inside a ```systemverilog code block.
""",

    # ── 15. RAL (Register Abstraction Layer) ──────────────────────────
    "ral.sv": """\
You are a UVM verification expert. Generate a SystemVerilog UVM RAL file.

TASK: Create a Register Abstraction Layer if the design has registers.

RULES:
1. If no registers detected, create a minimal placeholder
2. Class: {module}_reg_block extends uvm_reg_block
3. Define registers with fields
4. Implement build() to create registers and add to map
5. Create adapter class for front-door access

OUTPUT: Return ONLY the SystemVerilog code inside a ```systemverilog code block.
""",

    # ── 16. Testbench Top ─────────────────────────────────────────────
    "tb_top.sv": """\
You are a UVM verification expert. Generate a SystemVerilog TESTBENCH TOP file.

TASK: Create the top-level testbench module that instantiates everything.

RULES:
1. Module name: tb_top (no ports)
2. Instantiate the interface: {module}_if
3. Instantiate the DUT: {module} with interface connections
4. Generate clock: 10ns period (100MHz)
5. Generate reset: assert for 100ns then deassert
6. Set virtual interface in config_db
7. Call run_test() to start UVM
8. Bind assertions module
9. Add VCD dump for waveforms
10. Must compile with `iverilog -g2012`

OUTPUT: Return ONLY the SystemVerilog code inside a ```systemverilog code block.
""",

    # ── 17. Makefile ──────────────────────────────────────────────────
    "Makefile": """\
You are a build automation expert.

TASK: Create a Makefile for compiling and running UVM simulations.

RULES:
1. Targets: all, compile, sim, waves, clean, regression
2. Use iverilog -g2012 with correct file order
3. Use +UVM_TESTNAME to select tests
4. Default test: {module}_basic_test
5. Regression target: run all tests sequentially
6. VCD waveform support
7. List all .sv files in dependency order

OUTPUT: Return ONLY the Makefile content inside a ```makefile code block.
""",

    # ── 18. Test Plan ─────────────────────────────────────────────────
    "test_plan.md": """\
You are a verification planning expert.

TASK: Create a comprehensive verification plan document.

RULES:
1. Include sections: Overview, Features, Test Strategy, Coverage Plan, Assertions, Schedule
2. Use markdown tables for traceability
3. Map each spec requirement to tests and assertions
4. Define coverage goals (functional, code, assertion)
5. Include sign-off criteria

OUTPUT: Return ONLY markdown content.
""",
}


# ═══════════════════════════════════════════════════════════════════════
# UVM File Generation Order + Dependencies
# ═══════════════════════════════════════════════════════════════════════

UVM_FILE_ORDER = [
    "pkg.sv",
    "if.sv",
    "seq_item.sv",
    "driver.sv",
    "monitor.sv",
    "sequencer.sv",
    "agent.sv",
    "scoreboard.sv",
    "coverage.sv",
    "env.sv",
    "base_test.sv",
    "seq_lib.sv",
    "tests.sv",
    "assertions.sv",
    "ral.sv",
    "tb_top.sv",
    "Makefile",
    "test_plan.md",
]

UVM_DEPENDENCIES: dict[str, list[str]] = {
    "pkg.sv": [],
    "if.sv": [],
    "seq_item.sv": ["pkg.sv"],
    "driver.sv": ["pkg.sv", "if.sv", "seq_item.sv"],
    "monitor.sv": ["pkg.sv", "if.sv", "seq_item.sv"],
    "sequencer.sv": ["pkg.sv", "seq_item.sv"],
    "agent.sv": ["driver.sv", "monitor.sv", "sequencer.sv"],
    "scoreboard.sv": ["pkg.sv", "seq_item.sv"],
    "coverage.sv": ["pkg.sv", "seq_item.sv"],
    "env.sv": ["agent.sv", "scoreboard.sv", "coverage.sv"],
    "base_test.sv": ["env.sv"],
    "seq_lib.sv": ["pkg.sv", "seq_item.sv"],
    "tests.sv": ["base_test.sv", "seq_lib.sv"],
    "assertions.sv": [],
    "ral.sv": ["pkg.sv"],
    "tb_top.sv": ["if.sv", "env.sv", "assertions.sv", "tests.sv"],
    "Makefile": [],
    "test_plan.md": [],
}

# Per-file required keywords for validation
UVM_VALIDATORS: dict[str, dict] = {
    "pkg.sv": {"required": ["package", "endpackage"], "min_lines": 10},
    "if.sv": {"required": ["interface", "endinterface", "modport", "clocking"], "min_lines": 15},
    "seq_item.sv": {"required": ["class", "endclass", "uvm_sequence_item", "rand"], "min_lines": 20},
    "driver.sv": {"required": ["class", "endclass", "uvm_driver", "run_phase", "build_phase"], "min_lines": 30},
    "monitor.sv": {"required": ["class", "endclass", "uvm_monitor", "run_phase"], "min_lines": 25},
    "sequencer.sv": {"required": ["uvm_sequencer"], "min_lines": 3},
    "agent.sv": {"required": ["class", "endclass", "uvm_agent", "is_active"], "min_lines": 20},
    "scoreboard.sv": {"required": ["class", "endclass", "uvm_scoreboard"], "min_lines": 30},
    "coverage.sv": {"required": ["class", "endclass", "covergroup"], "min_lines": 20},
    "env.sv": {"required": ["class", "endclass", "uvm_env", "build_phase", "connect_phase"], "min_lines": 25},
    "base_test.sv": {"required": ["class", "endclass", "uvm_test"], "min_lines": 20},
    "seq_lib.sv": {"required": ["class", "endclass", "uvm_sequence", "body"], "min_lines": 30},
    "tests.sv": {"required": ["class", "endclass", "run_phase"], "min_lines": 20},
    "assertions.sv": {"required": ["module", "endmodule", "property", "assert"], "min_lines": 15},
    "ral.sv": {"required": ["class", "endclass"], "min_lines": 5},
    "tb_top.sv": {"required": ["module", "endmodule", "run_test"], "min_lines": 20},
    "Makefile": {"required": ["iverilog", "clean"], "min_lines": 10},
    "test_plan.md": {"required": ["Coverage", "Test"], "min_lines": 20},
}


class UVMGenerator:
    """Generate full UVM testbench — 18 files in dependency order."""

    def __init__(
        self,
        ai_client: AIClient,
        output_dir: str | Path = "output",
        symbol_table: SymbolTable | None = None,
    ) -> None:
        self.ai_client = ai_client
        self.output_dir = Path(output_dir) / "verification"
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.generated_files: dict[str, str] = {}  # filename -> content
        # Anti-hallucination guard (wired to project symbol table)
        self._symbol_table = symbol_table or SymbolTable()
        self._guard = HallucinationGuard(self._symbol_table)

    async def generate_all(
        self,
        rtl_code: str,
        rtl_analysis: RTLAnalysis,
        spec: DesignSpecification,
        mental_model: MentalModel,
    ) -> dict[str, dict]:
        """Generate all 18 UVM files in order with hallucination defense.

        For each file:
          1. Layer 1 (Prevention): inject exact port list into prompt
          2. Generate code (with retry loop)
          3. Layer 2 (Detection): compute grounding score
          4. Layer 3/4 (Correct/Retry): fix hallucinations if found

        Returns:
            Dict of filename -> {content, lines, passed, attempts, grounding}
        """
        module_name = spec.module_name or rtl_analysis.top_module
        results = {}

        logger.info("UVM Generator: creating 18 files for module '%s'", module_name)

        # Layer 1: Build the prevention prompt ONCE (shared by all files)
        prevention_block = self._guard.build_prevention_prompt(
            module_name=module_name,
            file_type="UVM testbench",
        )

        for i, filename in enumerate(UVM_FILE_ORDER, 1):
            # Build the actual filename with module prefix
            actual_name = (
                f"{module_name}_{filename}"
                if filename not in ("Makefile", "test_plan.md", "tb_top.sv")
                else filename
            )

            logger.info("  [%d/18] Generating %s...", i, actual_name)

            # Get system prompt — PREPEND the prevention block
            base_prompt = UVM_SYSTEM_PROMPTS[filename].replace("{module}", module_name)
            # Only inject prevention into SV files (not Makefile/test_plan)
            if filename.endswith(".sv") or filename.endswith(".v"):
                system_prompt = prevention_block + "\n\n" + base_prompt
            else:
                system_prompt = base_prompt

            # Build context
            context = self._build_context(
                filename, module_name, rtl_code, rtl_analysis, spec, mental_model
            )

            # Focus instruction
            focus = (
                f"You are generating file {i}/18: {actual_name}\n"
                f"Module under test: {module_name}\n"
                f"Use EXACT port names from the RTL (listed above). Do NOT invent names."
            )

            content = ""
            attempts = 0
            passed = False
            grounding_score = 1.0
            current_prompt = system_prompt
            hall_report = None

            for attempt in range(config.MAX_FILE_GEN_RETRIES):
                attempts += 1
                try:
                    response = await self.ai_client.generate_with_context(
                        system_prompt=current_prompt,
                        context_blocks=context,
                        focus_instruction=focus,
                        task_instruction=f"Generate {actual_name} for the {module_name} UVM testbench.",
                    )

                    content = self._extract_code(response, filename)
                    passed = self._validate(filename, content)

                    # Layer 2: Hallucination detection (SV files only)
                    if filename.endswith(".sv") and content:
                        hall_report = self._guard.detect(actual_name, content)
                        grounding_score = hall_report.confidence_before
                        decision = self._guard.decide(hall_report)

                        # Emit grounding score to console for Streamlit
                        logger.info(
                            "__GROUNDING__:%s:%.2f:%d_hallucinations",
                            actual_name, grounding_score,
                            hall_report.hallucination_count,
                        )

                        if decision == "accept":
                            break  # Good quality — done

                        elif decision == "correct" and attempt == 0:
                            # Layer 3: Auto-correct low-severity hallucinations
                            content, corrections = self._guard.correct(content, hall_report)
                            if corrections:
                                logger.info(
                                    "    Auto-corrected %d names in %s",
                                    len(corrections), actual_name,
                                )
                                for c in corrections[:3]:
                                    logger.info("      %s", c)
                            break  # Accept the corrected version

                        elif decision in ("retry", "correct") and attempt < config.MAX_FILE_GEN_RETRIES - 1:
                            # Layer 4: Build specific retry prompt
                            logger.info(
                                "    Grounding %.0f%% — retrying with correction prompt",
                                grounding_score * 100,
                            )
                            current_prompt = self._guard.build_retry_prompt(
                                system_prompt, content, hall_report
                            )
                            # Don't break — loop continues with new prompt

                        elif decision == "halt":
                            logger.warning(
                                "    Grounding %.0f%% — too many hallucinations in %s",
                                grounding_score * 100, actual_name,
                            )
                            break  # Accept what we have, move on

                    elif passed:
                        break
                    else:
                        logger.info("    Attempt %d: validation failed, retrying...", attempts)

                except Exception as e:
                    logger.warning("    Attempt %d failed: %s", attempts, e)

            # Save file
            self.generated_files[actual_name] = content
            self._save_file(actual_name, content)

            line_count = len(content.splitlines())
            hall_count = hall_report.hallucination_count if hall_report else 0
            grounding_pct = f"{grounding_score:.0%}"

            results[actual_name] = {
                "content": content,
                "lines": line_count,
                "passed": passed,
                "attempts": attempts,
                "grounding": grounding_score,
                "hallucinations": hall_count,
            }

            status = "[PASS]" if passed else "[WARN]"
            hall_flag = f" [{hall_count} halluc?]" if hall_count > 0 else ""
            logger.info(
                "    %s %s: %d lines (%d attempts, grounding=%s%s)",
                status, actual_name, line_count, attempts, grounding_pct, hall_flag,
            )

        return results

    def _build_context(
        self,
        filename: str,
        module_name: str,
        rtl_code: str,
        rtl_analysis: RTLAnalysis,
        spec: DesignSpecification,
        mental_model: MentalModel,
    ) -> dict[str, str]:
        """Build structured context for a specific file."""
        ctx: dict[str, str] = {}

        # Always include RTL
        ctx["RTL DESIGN CODE"] = rtl_code

        # Include spec summary
        ports_str = "\n".join(
            f"  {p.direction.value:6} {p.name:15} [{p.width-1}:0]  -- {p.description}"
            for p in spec.ports
        )
        ctx["SPECIFICATION"] = (
            f"Module: {spec.module_name}\n"
            f"Ports:\n{ports_str}\n\n"
            f"Requirements:\n" + "\n".join(f"  - {r}" for r in spec.functional_requirements)
        )

        # Include dependency files
        deps = UVM_DEPENDENCIES.get(filename, [])
        for dep in deps:
            dep_name = f"{module_name}_{dep}" if dep not in ("Makefile", "test_plan.md", "tb_top.sv") else dep
            if dep_name in self.generated_files:
                ctx[f"DEPENDENCY: {dep_name}"] = self.generated_files[dep_name]

        # Include mental model for certain files
        if filename in ("scoreboard.sv", "coverage.sv", "seq_lib.sv", "tests.sv", "assertions.sv", "test_plan.md"):
            model_summary = (
                f"Verification Plan:\n" +
                "\n".join(f"  - {t.name}: {t.description}" for t in mental_model.verification_plan[:10]) +
                f"\n\nCorner Cases:\n" +
                "\n".join(f"  - {c}" for c in mental_model.corner_cases[:10])
            )
            ctx["MENTAL MODEL"] = model_summary

        return ctx

    def _extract_code(self, response: str, filename: str) -> str:
        """Extract code from LLM response."""
        if filename == "test_plan.md":
            md = re.search(r"```(?:markdown|md)\s*\n(.*?)```", response, re.DOTALL)
            if md:
                return md.group(1).strip()
            return response.strip()

        if filename == "Makefile":
            mk = re.search(r"```(?:makefile|make)\s*\n(.*?)```", response, re.DOTALL)
            if mk:
                return mk.group(1).strip()

        sv = re.search(r"```(?:systemverilog|verilog|sv)[^\n]*\n(.*?)```", response, re.DOTALL | re.IGNORECASE)
        if sv:
            return sv.group(1).strip()

        generic = re.search(r"```\w*\s*\n(.*?)```", response, re.DOTALL)
        if generic:
            return generic.group(1).strip()

        return response.strip()

    def _validate(self, filename: str, content: str) -> bool:
        """Validate generated content against per-file rules."""
        rules = UVM_VALIDATORS.get(filename, {})
        required = rules.get("required", [])
        min_lines = rules.get("min_lines", 5)

        if len(content.splitlines()) < min_lines:
            return False

        content_lower = content.lower()
        for kw in required:
            if kw.lower() not in content_lower:
                return False

        return True

    def _save_file(self, filename: str, content: str) -> None:
        """Save generated file to disk."""
        path = self.output_dir / filename
        path.write_text(content, encoding="utf-8")
