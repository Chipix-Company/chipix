"""
Agent 4 — File Generator

Generates 11 verification files ONE AT A TIME, each with:
  • Its own system prompt (tailored per file type)
  • Its own context (full dependency files with structured labels)
  • Its own validator (required keywords, forbidden patterns, custom checks)

Uses the configured AI runtime via the shared AI client.
Full code is passed with structured labels — no extraction needed.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from pathlib import Path

import config
from core.ai_client import AIClient
from core.logger import get_logger
from core.models import (
    DesignSpecification,
    FileToGenerate,
    GeneratedFile,
    MentalModel,
    RTLAnalysis,
    ValidationResult,
)

logger = get_logger("Agent4.FileGenerator")


# ═══════════════════════════════════════════════════════════════════════
# Per-file system prompts (each file gets its own LLM persona)
# ═══════════════════════════════════════════════════════════════════════

SYSTEM_PROMPTS: dict[str, str] = {
    # ── File 1: interface.sv ──────────────────────────────────────────
    "interface.sv": """\
You are an expert SystemVerilog verification engineer.

TASK: Create a SystemVerilog interface for the given design.

RULES:
1. Define a SystemVerilog `interface` that bundles ALL DUT signals.
2. Include a clock input in the interface.
3. Include clocking blocks for DRIVER and MONITOR with appropriate directions.
4. Define modports: DRIVER, MONITOR, and DUT.
5. Match ALL ports from the RTL EXACTLY (same names, widths, types).
6. Include `timescale 1ns/1ps` at the top.
7. Interface name should be: {module_name}_if

OUTPUT: Return ONLY the SystemVerilog code inside a single ```systemverilog code block.
Do NOT include any explanation outside the code block.
""",
    # ── File 2: coverage.sv ───────────────────────────────────────────
    "coverage.sv": """\
You are an expert SystemVerilog functional coverage engineer.

TASK: Create a functional coverage model for the given design.

RULES:
1. Create covergroups that sample all important signals.
2. Include coverpoints for EACH input and output port.
3. Add cross-coverage between related signals.
4. Include transition coverage for signals that change state (e.g., counter values).
5. If FSM detected, add state and transition coverage.
6. Use `covergroup ... @(posedge clk);` sampling syntax.
7. Target ≥95% coverage completeness.
8. Wrap everything in a class for easy instantiation.
9. Must compile with `iverilog -g2012`.

OUTPUT: Return ONLY the SystemVerilog code inside a single ```systemverilog code block.
""",
    # ── File 3: assertions.sv ─────────────────────────────────────────
    "assertions.sv": """\
You are an expert SystemVerilog Assertions (SVA) engineer.

TASK: Create assertion properties for the given design.

RULES:
1. Write `property` and `assert property` blocks for every behavioral requirement.
2. Include `assume property` for input constraints (e.g., valid reset behavior).
3. Include `cover property` directives for reachability of important states.
4. Use `default clocking` if appropriate.
5. Label EVERY assertion with a meaningful name (e.g., `reset_clears_output`).
6. Create a module that can be bound to the DUT or instantiated in tb.
7. Must compile with `iverilog -g2012`.
8. Use `timescale 1ns/1ps`.

OUTPUT: Return ONLY the SystemVerilog code inside a single ```systemverilog code block.
""",
    # ── File 4: driver.sv ─────────────────────────────────────────────
    "driver.sv": """\
You are an expert SystemVerilog verification engineer.

TASK: Create a driver component that drives stimulus to the DUT through the interface.

RULES:
1. Use the EXACT interface and modport names from the previously generated interface.sv.
2. Create a class with these tasks:
   - reset_dut() — apply reset sequence
   - drive_normal() — normal operation stimulus
   - drive_corner_cases() — edge case stimulus
   - drive_random(int num_transactions) — randomised inputs
3. Accept a virtual interface in the constructor.
4. Use the interface's DRIVER modport or clocking block.
5. Must compile with `iverilog -g2012`.

OUTPUT: Return ONLY the SystemVerilog code inside a single ```systemverilog code block.
""",
    # ── File 5: monitor.sv ────────────────────────────────────────────
    "monitor.sv": """\
You are an expert SystemVerilog verification engineer.

TASK: Create a monitor component that observes DUT outputs through the interface.

RULES:
1. Use the EXACT interface and modport names from the previously generated interface.sv.
2. Create a class that:
   - Samples DUT outputs on clock edges
   - Packages observed values into transactions
   - Sends transactions to the scoreboard via a mailbox
3. Accept a virtual interface and mailbox in the constructor.
4. Use the interface's MONITOR modport or clocking block.
5. Must compile with `iverilog -g2012`.

OUTPUT: Return ONLY the SystemVerilog code inside a single ```systemverilog code block.
""",
    # ── File 6: scoreboard.sv ─────────────────────────────────────────
    "scoreboard.sv": """\
You are an expert chip verification engineer.

TASK: Create a scoreboard that verifies DUT outputs against expected behavior.

RULES:
1. Implement a REFERENCE MODEL that computes expected outputs from the spec behavior.
2. Receive observed transactions from the monitor via mailbox.
3. Compare DUT output with expected output from the reference model.
4. Report mismatches with detailed diagnostics (expected vs actual).
5. Track pass/fail counts.
6. Print "TEST PASSED" for matches, "TEST FAILED" for mismatches.
7. At the end, print a summary: "SCOREBOARD: X passed, Y failed".
8. Must compile with `iverilog -g2012`.

OUTPUT: Return ONLY the SystemVerilog code inside a single ```systemverilog code block.
""",
    # ── File 7: sequences.sv ──────────────────────────────────────────
    "sequences.sv": """\
You are an expert SystemVerilog verification engineer.

TASK: Create test sequences that use the driver to exercise the design.

RULES:
1. Use the EXACT driver class and task names from the previously generated driver.sv.
2. Create a class with these test sequences as separate tasks:
   - basic_test(): Normal operation verification
   - reset_test(): Reset behavior verification
   - corner_test(): Edge cases and boundary conditions
   - stress_test(): Rapid stimulus changes
   - random_test(): Randomised inputs (at least 100 transactions)
   - run_all_tests(): Calls all of the above in order
3. Accept the driver in the constructor.
4. Each test should print its name at the start and PASS/FAIL at the end.
5. Must compile with `iverilog -g2012`.

OUTPUT: Return ONLY the SystemVerilog code inside a single ```systemverilog code block.
""",
    # ── File 8: environment.sv ────────────────────────────────────────
    "environment.sv": """\
You are an expert SystemVerilog verification architect.

TASK: Create the verification environment that ties all components together.

RULES:
1. Instantiate ALL of these using EXACT class names from the previously generated files:
   - Driver (from driver.sv)
   - Monitor (from monitor.sv)
   - Scoreboard (from scoreboard.sv)
   - Coverage (from coverage.sv)
2. Create the mailbox to connect monitor → scoreboard.
3. Accept a virtual interface in the constructor.
4. Provide methods: build(), run(), report()
5. Wire everything correctly — driver and monitor get the interface, scoreboard gets the mailbox.
6. Must compile with `iverilog -g2012`.

OUTPUT: Return ONLY the SystemVerilog code inside a single ```systemverilog code block.
""",
    # ── File 9: tb.sv (MAIN TESTBENCH) ────────────────────────────────
    "tb.sv": """\
You are an expert SystemVerilog verification engineer.

TASK: Create the top-level testbench module that runs everything.

RULES:
1. Include `timescale 1ns/1ps` at the top.
2. Instantiate the DUT using the EXACT module name and port names from the RTL.
3. Instantiate the interface.
4. Connect DUT ports to interface signals.
5. Generate clock: 10ns period (5ns high, 5ns low).
6. Generate reset: assert low, wait 20ns, deassert.
7. Instantiate the environment, sequences, and assertions.
8. Run all test sequences via sequences.run_all_tests().
9. Call environment.report() for final summary.
10. Call $finish at the end.
11. Include $dumpfile / $dumpvars for waveform dumping.
12. Must compile with `iverilog -g2012`.

OUTPUT: Return ONLY the SystemVerilog code inside a single ```systemverilog code block.
""",
    # ── File 10: Makefile ─────────────────────────────────────────────
    "Makefile": """\
You are a build automation expert for HDL simulation.

TASK: Create a Makefile for compiling and running the simulation.

RULES:
1. Use Icarus Verilog (iverilog) with the -g2012 flag.
2. Include these targets: all, compile, simulate, waves, clean.
3. List ALL .sv files in correct compilation order.
4. Output the simulation binary as "simv".
5. VCD waveform file should be "dump.vcd".
6. 'all' should run compile then simulate.
7. 'waves' should open GTKWave with the VCD file.
8. 'clean' should remove simv, dump.vcd, and any temp files.
9. Use TABS for indentation (Makefile syntax requirement).

OUTPUT: Return ONLY the Makefile content inside a single ```makefile code block.
""",
    # ── File 11: test_plan.md ─────────────────────────────────────────
    "test_plan.md": """\
You are a verification planning expert for ASIC/FPGA designs.

TASK: Create a comprehensive test plan document in Markdown format.

RULES:
1. Include these sections:
   - Overview: Design description and verification goals
   - Test Strategy: Directed + randomised approach
   - Test Scenarios: Table with columns [Name | Description | Priority | Spec Requirement | Status]
   - Assertion Coverage: Table listing all SVA assertions
   - Functional Coverage: Table listing all coverpoints and targets
   - Corner Cases: List of all edge cases tested
   - Exit Criteria: Conditions for verification sign-off
   - Traceability Matrix: Spec requirement → Test → Assertion mapping
2. Reference ACTUAL test names, assertion names, and coverage points from the generated files.
3. Use proper markdown with tables.

OUTPUT: Return ONLY the markdown content (no code block wrapper needed).
""",
}


# ═══════════════════════════════════════════════════════════════════════
# Per-file validator configuration
# ═══════════════════════════════════════════════════════════════════════


@dataclass
class FileValidatorConfig:
    """Validation rules for a single file type."""

    required_keywords: list[str] = field(default_factory=list)
    forbidden_patterns: list[str] = field(default_factory=list)
    min_lines: int = 5
    max_lines: int = 2000
    custom_checks: list[str] = field(default_factory=list)  # Named check IDs


VALIDATOR_CONFIGS: dict[str, FileValidatorConfig] = {
    "interface.sv": FileValidatorConfig(
        required_keywords=["interface", "modport"],
        forbidden_patterns=["module tb", "$finish"],
        min_lines=10,
        max_lines=300,
        custom_checks=["all_ports_present"],
    ),
    "coverage.sv": FileValidatorConfig(
        required_keywords=["covergroup", "coverpoint"],
        forbidden_patterns=["module tb"],
        min_lines=15,
        max_lines=500,
        custom_checks=[],
    ),
    "assertions.sv": FileValidatorConfig(
        required_keywords=["property"],
        forbidden_patterns=[],
        min_lines=10,
        max_lines=500,
        custom_checks=["assertions_named"],
    ),
    "driver.sv": FileValidatorConfig(
        required_keywords=["class", "task"],
        forbidden_patterns=["module tb"],
        min_lines=15,
        max_lines=500,
        custom_checks=[],
    ),
    "monitor.sv": FileValidatorConfig(
        required_keywords=["class", "task"],
        forbidden_patterns=["module tb"],
        min_lines=15,
        max_lines=500,
        custom_checks=[],
    ),
    "scoreboard.sv": FileValidatorConfig(
        required_keywords=["class"],
        forbidden_patterns=[],
        min_lines=15,
        max_lines=500,
        custom_checks=["has_pass_fail"],
    ),
    "sequences.sv": FileValidatorConfig(
        required_keywords=["class", "task"],
        forbidden_patterns=[],
        min_lines=20,
        max_lines=600,
        custom_checks=[],
    ),
    "environment.sv": FileValidatorConfig(
        required_keywords=["class"],
        forbidden_patterns=[],
        min_lines=20,
        max_lines=500,
        custom_checks=[],
    ),
    "tb.sv": FileValidatorConfig(
        required_keywords=["module", "$finish", "initial"],
        forbidden_patterns=[],
        min_lines=30,
        max_lines=1000,
        custom_checks=["has_clock_gen", "has_dut_instance"],
    ),
    "Makefile": FileValidatorConfig(
        required_keywords=["iverilog", "clean"],
        forbidden_patterns=[],
        min_lines=5,
        max_lines=100,
        custom_checks=[],
    ),
    "test_plan.md": FileValidatorConfig(
        required_keywords=["#", "Test"],
        forbidden_patterns=[],
        min_lines=20,
        max_lines=500,
        custom_checks=["has_table"],
    ),
}


# ═══════════════════════════════════════════════════════════════════════
# File Generator Agent
# ═══════════════════════════════════════════════════════════════════════


class FileGenerator:
    """Agent 4: Generates 11 verification files, one LLM call per file."""

    def __init__(
        self,
        ai_client: AIClient,
        output_dir: str | Path = "output",
    ) -> None:
        self.ai_client = ai_client
        self.output_dir = Path(output_dir)
        self.generated_files: dict[str, GeneratedFile] = {}  # registry

    # ------------------------------------------------------------------
    # Public API — Generate all files
    # ------------------------------------------------------------------
    async def generate_all(
        self,
        rtl_code: str,
        rtl_analysis: RTLAnalysis,
        spec: DesignSpecification,
        mental_model: MentalModel,
    ) -> dict[str, GeneratedFile]:
        """Generate all 11 verification files in dependency order.

        Each file gets ONE dedicated LLM call with:
          - Its own system prompt
          - Full dependency files as structured context
          - Its own validator
        """
        logger.info(
            "✍️  File Generator: Starting generation of %d files …",
            len(mental_model.files_to_generate),
        )

        # Ensure output directories exist
        ver_dir = self.output_dir / "verification"
        ver_dir.mkdir(parents=True, exist_ok=True)

        for file_spec in mental_model.files_to_generate:
            logger.info(
                "  ┌─ [%d/11] Generating %s (%s) …",
                file_spec.file_id,
                file_spec.filename,
                file_spec.display_name,
            )

            generated = await self._generate_single_file(
                file_spec=file_spec,
                rtl_code=rtl_code,
                rtl_analysis=rtl_analysis,
                spec=spec,
                mental_model=mental_model,
            )

            self.generated_files[file_spec.filename] = generated

            status = "✅ PASS" if generated.validation.passed else "⚠️ WARN"
            logger.info(
                "  └─ %s  %s (%d lines, %d attempts, %.1fs)",
                status,
                file_spec.filename,
                generated.line_count,
                generated.attempts,
                generated.generation_time_seconds,
            )

        logger.info(
            "✍️  File Generator: All %d files generated!", len(self.generated_files)
        )
        return self.generated_files

    # ------------------------------------------------------------------
    # Single file generation (with retries)
    # ------------------------------------------------------------------
    async def _generate_single_file(
        self,
        file_spec: FileToGenerate,
        rtl_code: str,
        rtl_analysis: RTLAnalysis,
        spec: DesignSpecification,
        mental_model: MentalModel,
    ) -> GeneratedFile:
        """Generate a single file with validation and retry logic."""

        system_prompt = self._get_system_prompt(file_spec, rtl_analysis)
        best_content = ""
        best_validation = ValidationResult(passed=False, errors=["Not generated yet"])

        for attempt in range(1, config.MAX_FILE_GEN_RETRIES + 1):
            start_time = time.time()

            # Build the context with structured labels
            user_prompt = self._build_user_prompt(
                file_spec,
                rtl_code,
                rtl_analysis,
                spec,
                mental_model,
                previous_errors=best_validation.errors if attempt > 1 else None,
            )

            # Call active AI provider/runtime
            response = await self.ai_client.generate(
                user_prompt,
                system_prompt=system_prompt,
                temperature=0.5 if attempt == 1 else 0.3,  # Lower temp on retry
            )

            elapsed = time.time() - start_time

            # Extract code from response
            content = self._extract_code(response, file_spec.filename)

            # Validate
            validation = self._validate(file_spec.filename, content, rtl_analysis)

            if validation.passed:
                # Save to disk
                self._save_file(file_spec.filename, content)
                return GeneratedFile(
                    filename=file_spec.filename,
                    content=content,
                    validation=validation,
                    attempts=attempt,
                    generation_time_seconds=elapsed,
                )

            # Track best attempt
            if not best_content or len(validation.errors) < len(best_validation.errors):
                best_content = content
                best_validation = validation

            logger.warning(
                "    ⚠ Attempt %d/%d failed validation: %s",
                attempt,
                config.MAX_FILE_GEN_RETRIES,
                validation.error_summary,
            )

        # All retries exhausted — save best attempt with warning
        logger.warning(
            "    ❌ Max retries reached for %s — saving best attempt.",
            file_spec.filename,
        )
        self._save_file(file_spec.filename, best_content)
        return GeneratedFile(
            filename=file_spec.filename,
            content=best_content,
            validation=best_validation,
            attempts=config.MAX_FILE_GEN_RETRIES,
            generation_time_seconds=0,
        )

    # ------------------------------------------------------------------
    # System prompt builder
    # ------------------------------------------------------------------
    def _get_system_prompt(
        self, file_spec: FileToGenerate, analysis: RTLAnalysis
    ) -> str:
        """Get the system prompt for this file, with module name injected."""
        prompt = SYSTEM_PROMPTS.get(file_spec.filename, "Generate the requested file.")
        return prompt.replace("{module_name}", analysis.top_module)

    # ------------------------------------------------------------------
    # User prompt builder (structured context)
    # ------------------------------------------------------------------
    def _build_user_prompt(
        self,
        file_spec: FileToGenerate,
        rtl_code: str,
        rtl_analysis: RTLAnalysis,
        spec: DesignSpecification,
        mental_model: MentalModel,
        previous_errors: list[str] | None = None,
    ) -> str:
        """Build the structured user prompt with FOCUS INSTRUCTION and labeled context."""

        sections = []

        # ── Header ────────────────────────────────────────────────────
        sections.append(
            f"╔══════════════════════════════════════════════════════════════╗\n"
            f"║  GENERATING: {file_spec.filename:<46} ║\n"
            f"║  PURPOSE: {file_spec.display_name:<49} ║\n"
            f"╚══════════════════════════════════════════════════════════════╝"
        )

        # ── Focus instruction ─────────────────────────────────────────
        focus = self._build_focus_instruction(file_spec, rtl_analysis)
        sections.append(
            f"┌─── FOCUS INSTRUCTION ───────────────────────────────────────┐\n"
            f"{focus}\n"
            f"└─────────────────────────────────────────────────────────────┘"
        )

        # ── Retry feedback (if applicable) ─────────────────────────────
        if previous_errors:
            error_text = "\n".join(f"  • {e}" for e in previous_errors)
            sections.append(
                f"⚠️  PREVIOUS ATTEMPT FAILED VALIDATION:\n"
                f"{error_text}\n"
                f"FIX these issues in your new generation."
            )

        # ── RTL code (always included) ─────────────────────────────────
        sections.append(
            f"═══ RTL DESIGN CODE ═══════════════════════════════════════════\n"
            f"{rtl_code}"
        )

        # ── Specification (for files that need it) ─────────────────────
        if file_spec.filename in (
            "interface.sv",
            "coverage.sv",
            "assertions.sv",
            "scoreboard.sv",
            "test_plan.md",
        ):
            sections.append(
                f"═══ DESIGN SPECIFICATION ═════════════════════════════════════\n"
                f"{spec.raw_text}"
            )

        # ── Mental model section (relevant part only) ──────────────────
        mm_section = self._get_mental_model_section(file_spec.filename, mental_model)
        if mm_section:
            sections.append(
                f"═══ VERIFICATION PLAN (from Mental Model) ════════════════════\n"
                f"{mm_section}"
            )

        # ── Dependency files (full code with labels) ───────────────────
        for dep_name in file_spec.depends_on:
            if dep_name in self.generated_files:
                dep_content = self.generated_files[dep_name].content
                sections.append(
                    f"═══ PREVIOUSLY GENERATED: {dep_name} ════════════════════════\n"
                    f"{dep_content}"
                )

        # ── Task instruction ───────────────────────────────────────────
        sections.append(
            f"═══ YOUR TASK ═════════════════════════════════════════════════\n"
            f"Generate {file_spec.filename} following ALL rules in your system prompt.\n"
            f"Use the EXACT names from the code and files above — do NOT invent new names.\n"
            f"Notes: {file_spec.generation_notes}"
        )

        return "\n\n".join(sections)

    # ------------------------------------------------------------------
    # Focus instruction builder
    # ------------------------------------------------------------------
    def _build_focus_instruction(
        self,
        file_spec: FileToGenerate,
        analysis: RTLAnalysis,
    ) -> str:
        """Build the FOCUS INSTRUCTION block that tells the LLM what names to use."""

        lines = [
            f"│ You are generating: {file_spec.filename} ({file_spec.display_name})",
            f"│",
            f"│ DUT module name: {analysis.top_module}",
            f"│ Clock signal: {analysis.clock_signal}",
            f"│ Reset signal: {analysis.reset_signal}",
            f"│",
        ]

        # Add dependency-specific names
        if "interface.sv" in self.generated_files:
            # Extract interface name
            iface_code = self.generated_files["interface.sv"].content
            iface_match = re.search(r"interface\s+(\w+)", iface_code)
            if iface_match:
                lines.append(f"│ Interface name: {iface_match.group(1)}")

        if "driver.sv" in self.generated_files:
            drv_code = self.generated_files["driver.sv"].content
            drv_match = re.search(r"class\s+(\w+)", drv_code)
            if drv_match:
                lines.append(f"│ Driver class: {drv_match.group(1)}")

        if "monitor.sv" in self.generated_files:
            mon_code = self.generated_files["monitor.sv"].content
            mon_match = re.search(r"class\s+(\w+)", mon_code)
            if mon_match:
                lines.append(f"│ Monitor class: {mon_match.group(1)}")

        if "scoreboard.sv" in self.generated_files:
            scr_code = self.generated_files["scoreboard.sv"].content
            scr_match = re.search(r"class\s+(\w+)", scr_code)
            if scr_match:
                lines.append(f"│ Scoreboard class: {scr_match.group(1)}")

        if "coverage.sv" in self.generated_files:
            cov_code = self.generated_files["coverage.sv"].content
            cov_match = re.search(r"class\s+(\w+)", cov_code)
            if cov_match:
                lines.append(f"│ Coverage class: {cov_match.group(1)}")

        if "environment.sv" in self.generated_files:
            env_code = self.generated_files["environment.sv"].content
            env_match = re.search(r"class\s+(\w+)", env_code)
            if env_match:
                lines.append(f"│ Environment class: {env_match.group(1)}")

        # Port names
        port_names = ", ".join(analysis.port_names[:10])
        lines.append(f"│ Port names: {port_names}")

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Mental model section extractor
    # ------------------------------------------------------------------
    def _get_mental_model_section(self, filename: str, model: MentalModel) -> str:
        """Get the relevant section of the mental model for this file."""

        if filename == "assertions.sv":
            lines = ["Assertions to generate:"]
            for a in model.assertion_plan:
                lines.append(
                    f"  • {a.name}: {a.property_description} [{a.assertion_type}]"
                )
                if a.related_signals:
                    lines.append(f"    Signals: {', '.join(a.related_signals)}")
            return "\n".join(lines)

        elif filename == "coverage.sv":
            lines = ["Coverage points to generate:"]
            for c in model.coverage_plan:
                lines.append(
                    f"  • {c.name}: {c.signal} [{c.cover_type}] — {c.bins_description}"
                )
            return "\n".join(lines)

        elif filename in ("sequences.sv", "driver.sv"):
            lines = [
                f"Clock scheme: {model.clock_scheme}",
                f"Reset sequence: {model.reset_sequence}",
                "",
                "Test scenarios:",
            ]
            for t in model.verification_plan:
                lines.append(f"  • {t.name} [{t.priority}]: {t.description}")
                lines.append(f"    Stimulus: {t.stimulus}")

            lines.append("\nInput sequences:")
            for s in model.input_sequences:
                lines.append(f"  • {s.name}: {s.description}")
                for step in s.steps:
                    lines.append(f"      - {step}")

            lines.append("\nCorner cases:")
            for c in model.corner_cases:
                lines.append(f"  • {c}")

            return "\n".join(lines)

        elif filename == "scoreboard.sv":
            lines = ["Spec-to-RTL mapping (for reference model):"]
            for spec_req, rtl_construct in model.spec_to_rtl_mapping.items():
                lines.append(f"  • {spec_req} → {rtl_construct}")
            return "\n".join(lines)

        elif filename == "tb.sv":
            lines = [
                f"Clock scheme: {model.clock_scheme}",
                f"Reset sequence: {model.reset_sequence}",
                "",
                "Critical verification paths:",
            ]
            for p in model.critical_paths:
                lines.append(f"  • {p}")
            return "\n".join(lines)

        elif filename == "test_plan.md":
            lines = ["Full verification plan summary:", ""]

            lines.append("Test scenarios:")
            for t in model.verification_plan:
                lines.append(
                    f"  • {t.name} [{t.priority}]: {t.description} → Spec: {t.spec_requirement}"
                )

            lines.append("\nAssertions:")
            for a in model.assertion_plan:
                lines.append(f"  • {a.name}: {a.property_description}")

            lines.append("\nCoverage points:")
            for c in model.coverage_plan:
                lines.append(f"  • {c.name}: {c.signal} ({c.target_percentage}%)")

            lines.append("\nCorner cases:")
            for c in model.corner_cases:
                lines.append(f"  • {c}")

            return "\n".join(lines)

        return ""

    # ------------------------------------------------------------------
    # Code extraction from LLM response
    # ------------------------------------------------------------------
    def _extract_code(self, response: str, filename: str) -> str:
        """Extract code from a response that contains a fenced code block."""

        if filename == "test_plan.md":
            # For markdown, remove code block wrappers if present
            md_match = re.search(
                r"```(?:markdown|md)\s*\n(.*?)```", response, re.DOTALL
            )
            if md_match:
                return md_match.group(1).strip()
            return response.strip()

        if filename == "Makefile":
            mk_match = re.search(
                r"```(?:makefile|make)\s*\n(.*?)```", response, re.DOTALL
            )
            if mk_match:
                return mk_match.group(1).strip()

        # SystemVerilog code block
        sv_match = re.search(
            r"```(?:systemverilog|verilog|sv)[^\n]*\n(.*?)```",
            response,
            re.DOTALL | re.IGNORECASE,
        )
        if sv_match:
            return sv_match.group(1).strip()

        # Generic code block
        generic_match = re.search(r"```\w*\s*\n(.*?)```", response, re.DOTALL)
        if generic_match:
            return generic_match.group(1).strip()

        # No code block found — use raw response
        logger.warning(
            "No code block found in response for %s — using raw text.", filename
        )
        return response.strip()

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------
    def _validate(
        self,
        filename: str,
        content: str,
        analysis: RTLAnalysis,
    ) -> ValidationResult:
        """Validate a generated file against its rules."""

        cfg = VALIDATOR_CONFIGS.get(filename)
        if not cfg:
            return ValidationResult(passed=True)

        errors = []
        warnings = []
        lines = content.splitlines()
        line_count = len(lines)

        # Check line count
        if line_count < cfg.min_lines:
            errors.append(f"Too short: {line_count} lines (minimum {cfg.min_lines})")
        if line_count > cfg.max_lines:
            warnings.append(f"Very long: {line_count} lines (maximum {cfg.max_lines})")

        # Check required keywords
        content_lower = content.lower()
        for kw in cfg.required_keywords:
            if kw.lower() not in content_lower:
                errors.append(f"Missing required keyword: '{kw}'")

        # Check forbidden patterns
        for pat in cfg.forbidden_patterns:
            if pat.lower() in content_lower:
                errors.append(f"Contains forbidden pattern: '{pat}'")

        # Custom checks
        for check in cfg.custom_checks:
            check_result = self._run_custom_check(check, content, analysis)
            if check_result:
                errors.append(check_result)

        passed = len(errors) == 0
        return ValidationResult(passed=passed, errors=errors, warnings=warnings)

    def _run_custom_check(
        self, check_name: str, content: str, analysis: RTLAnalysis
    ) -> str | None:
        """Run a named custom validation check. Returns error string or None."""

        if check_name == "all_ports_present":
            for port_name in analysis.port_names:
                if port_name not in content:
                    return f"Port '{port_name}' not found in generated code"

        elif check_name == "has_clock_gen":
            if "always" not in content.lower() and "#" not in content:
                return "No clock generation detected (expected 'always' or '#' delay)"

        elif check_name == "has_dut_instance":
            if analysis.top_module not in content:
                return f"DUT module '{analysis.top_module}' not instantiated"

        elif check_name == "assertions_named":
            # Check that assertions have labels
            assert_count = content.lower().count("assert property")
            label_count = len(
                re.findall(r"\w+\s*:\s*assert\s+property", content, re.IGNORECASE)
            )
            if assert_count > 0 and label_count == 0:
                return "Assertions should have labels (e.g., 'label: assert property')"

        elif check_name == "has_pass_fail":
            if "PASSED" not in content.upper() and "FAILED" not in content.upper():
                return "Scoreboard should print TEST PASSED / TEST FAILED"

        elif check_name == "has_table":
            if "|" not in content:
                return "Test plan should contain at least one markdown table"

        return None

    # ------------------------------------------------------------------
    # File saving
    # ------------------------------------------------------------------
    def _save_file(self, filename: str, content: str) -> None:
        """Save a generated file to disk."""
        ver_dir = self.output_dir / "verification"
        ver_dir.mkdir(parents=True, exist_ok=True)
        file_path = ver_dir / filename
        file_path.write_text(content, encoding="utf-8")
        logger.debug("Saved: %s (%d lines)", file_path, len(content.splitlines()))
