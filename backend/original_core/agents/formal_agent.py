"""
Gap 1 — Formal Verification Agent.

End-to-end formal verification automation:
  1. Generate formal test plan (properties, assumptions, covers)
  2. Generate SVA bind module
  3. Generate Tcl scripts for Jasper / VC Formal
  4. Run formal verification tool
  5. Parse results (proven, failed, inconclusive)
  6. Debug guidance with auto-fix

Mirrors ChipStack FormalAgent capabilities.
"""

from __future__ import annotations

import re
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

import config
from core.ai_client import AIClient
from core.logger import get_logger
from core.models import DesignSpecification, MentalModel, RTLAnalysis

logger = get_logger("FormalAgent")


# ═══════════════════════════════════════════════════════════════════════
# Data models
# ═══════════════════════════════════════════════════════════════════════

@dataclass
class FormalProperty:
    """A single formal verification property."""
    name: str
    kind: str = "assert"             # assert, assume, cover
    description: str = ""
    sva_code: str = ""
    status: str = "unknown"          # proven, failed, inconclusive, unknown
    cex_trace: str = ""              # Counter-example trace


@dataclass
class FormalTestPlan:
    """Formal verification test plan."""
    properties: list[FormalProperty] = field(default_factory=list)
    assumptions: list[FormalProperty] = field(default_factory=list)
    covers: list[FormalProperty] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.properties) + len(self.assumptions) + len(self.covers)

    @property
    def summary(self) -> str:
        return (
            f"Formal Plan: {len(self.properties)} assertions, "
            f"{len(self.assumptions)} assumptions, {len(self.covers)} covers"
        )


@dataclass
class FormalResult:
    """Result of a formal verification run."""
    tool_used: str = ""
    total_properties: int = 0
    proven: int = 0
    failed: int = 0
    inconclusive: int = 0
    covered: int = 0
    uncovered: int = 0
    runtime_seconds: float = 0.0
    log: str = ""
    failed_properties: list[FormalProperty] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return self.failed == 0 and self.proven > 0

    @property
    def summary(self) -> str:
        return (
            f"Formal [{self.tool_used}]: {self.proven} proven, "
            f"{self.failed} failed, {self.inconclusive} inconclusive, "
            f"{self.covered}/{self.covered + self.uncovered} covers hit "
            f"({self.runtime_seconds:.1f}s)"
        )


# ═══════════════════════════════════════════════════════════════════════
# System prompts for formal
# ═══════════════════════════════════════════════════════════════════════

FORMAL_TESTPLAN_PROMPT = """\
You are an expert in formal verification (SVA / SystemVerilog Assertions).

TASK: Create a comprehensive formal verification test plan for the given RTL design.

For each property, provide:
  - Name (e.g., p_reset_behavior, p_no_x_output)
  - Kind: assert | assume | cover
  - Description of what it checks
  - Complete SVA property code

CATEGORIES to cover:
  1. Reset behavior (all outputs defined after reset)
  2. No X/Z propagation on outputs
  3. Handshake protocols (valid/ready stability)
  4. FSM state transitions (no illegal states)
  5. Data integrity (correct computation)
  6. Boundary conditions (overflow, underflow)
  7. Liveness (progress guarantees)
  8. Interface protocol compliance
  9. Cover properties (reachability of key states)

RULES:
  - Use `disable iff (!rst_n)` or equivalent for reset
  - Use named properties with `assert property` and `cover property`
  - Include `$error` messages on assertion failures
  - Generate at least 10 assertions, 3 assumptions, and 5 covers

OUTPUT: Return a JSON array with objects: {name, kind, description, sva_code}
"""

FORMAL_BIND_PROMPT = """\
You are an SVA formal verification expert.

TASK: Generate a complete SVA bind module for formal verification.

RULES:
1. Module name: {module}_formal_bind
2. Accept all DUT ports as inputs
3. Include ALL the properties from the test plan (provided below)
4. Use `bind {module} {module}_formal_bind inst_formal (.*)` at the bottom
5. Group properties by category with comments
6. Include `default clocking cb @(posedge clk); endclocking`
7. Include `default disable iff (!rst_n)`
8. Must be synthesis-clean and compile with commercial tools

OUTPUT: Return ONLY the SystemVerilog code inside a ```systemverilog code block.
"""

FORMAL_TCL_PROMPT = """\
You are an expert in formal verification tool scripting.

TASK: Generate Tcl scripts for running formal verification.

Generate scripts for:
1. Cadence Jasper (JasperGold)
2. Synopsys VC Formal

Each script should:
  - Read RTL files
  - Read SVA bind module
  - Set clock and reset
  - Configure engine settings (BMC depth, k-induction, etc.)
  - Run all properties
  - Report results
  - Generate coverage report

OUTPUT: Return TWO scripts in separate code blocks:
```tcl
# jasper_run.tcl
...
```
```tcl
# vcformal_run.tcl
...
```
"""


class FormalAgent:
    """Agent for end-to-end formal verification."""

    def __init__(self, ai_client: AIClient, output_dir: str | Path = "output") -> None:
        self.ai_client = ai_client
        self.output_dir = Path(output_dir)
        self.formal_dir = self.output_dir / "formal"
        self.formal_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # 1. Generate formal test plan
    # ------------------------------------------------------------------

    async def create_testplan(
        self,
        rtl_code: str,
        spec: DesignSpecification,
        mental_model: MentalModel,
        rtl_analysis: RTLAnalysis,
    ) -> FormalTestPlan:
        """Generate a comprehensive formal test plan."""
        logger.info("Creating formal verification test plan...")

        context = {
            "RTL CODE": rtl_code,
            "SPECIFICATION": self._spec_summary(spec),
            "MENTAL MODEL": self._model_summary(mental_model),
            "RTL ANALYSIS": f"Module: {rtl_analysis.top_module}, "
                           f"FSM states: {rtl_analysis.fsm_states}",
        }

        response = await self.ai_client.generate_with_context(
            system_prompt=FORMAL_TESTPLAN_PROMPT,
            context_blocks=context,
            focus_instruction=f"Create formal test plan for {spec.module_name}",
            task_instruction="Generate assertions, assumptions, and cover properties",
        )

        plan = self._parse_testplan(response)
        self._save_testplan(plan)

        logger.info("Formal test plan: %s", plan.summary)
        return plan

    # ------------------------------------------------------------------
    # 2. Generate SVA bind module
    # ------------------------------------------------------------------

    async def create_bind_module(
        self,
        rtl_code: str,
        spec: DesignSpecification,
        testplan: FormalTestPlan,
    ) -> str:
        """Generate SVA bind module from test plan."""
        logger.info("Generating SVA bind module...")

        # Format properties for context
        props_text = "\n".join(
            f"  {p.kind}: {p.name} — {p.description}\n    {p.sva_code}"
            for p in testplan.properties + testplan.assumptions + testplan.covers
        )

        prompt = FORMAL_BIND_PROMPT.replace("{module}", spec.module_name)

        context = {
            "RTL CODE": rtl_code,
            "FORMAL TEST PLAN PROPERTIES": props_text,
        }

        response = await self.ai_client.generate_with_context(
            system_prompt=prompt,
            context_blocks=context,
            focus_instruction=f"Generate SVA bind module for {spec.module_name}",
            task_instruction="Create a complete bind module with all properties",
        )

        # Extract code
        sv_match = re.search(r"```(?:systemverilog|verilog|sv)\s*\n(.*?)```",
                             response, re.DOTALL | re.IGNORECASE)
        bind_code = sv_match.group(1).strip() if sv_match else response.strip()

        # Save
        path = self.formal_dir / f"{spec.module_name}_formal_bind.sv"
        path.write_text(bind_code, encoding="utf-8")
        logger.info("SVA bind module: %s (%d lines)", path.name, len(bind_code.splitlines()))

        return bind_code

    # ------------------------------------------------------------------
    # 3. Generate Tcl scripts
    # ------------------------------------------------------------------

    async def create_tcl_scripts(
        self,
        spec: DesignSpecification,
        rtl_files: list[str],
    ) -> dict[str, str]:
        """Generate Tcl scripts for Jasper and VC Formal."""
        logger.info("Generating Tcl scripts for formal tools...")

        context = {
            "MODULE NAME": spec.module_name,
            "RTL FILES": "\n".join(rtl_files),
            "BIND MODULE": f"{spec.module_name}_formal_bind.sv",
        }

        response = await self.ai_client.generate_with_context(
            system_prompt=FORMAL_TCL_PROMPT,
            context_blocks=context,
            focus_instruction=f"Generate Tcl scripts for {spec.module_name}",
            task_instruction="Create scripts for Jasper and VC Formal",
        )

        scripts = {}

        # Extract Jasper script
        jasper_match = re.search(
            r"```tcl\s*\n#\s*jasper.*?\n(.*?)```", response, re.DOTALL | re.IGNORECASE
        )
        if jasper_match:
            scripts["jasper_run.tcl"] = jasper_match.group(1).strip()
        else:
            # Try first tcl block
            first_tcl = re.search(r"```tcl\s*\n(.*?)```", response, re.DOTALL)
            if first_tcl:
                scripts["jasper_run.tcl"] = first_tcl.group(1).strip()

        # Extract VC Formal script
        vcf_match = re.search(
            r"```tcl\s*\n#\s*vc\s*formal.*?\n(.*?)```", response, re.DOTALL | re.IGNORECASE
        )
        if vcf_match:
            scripts["vcformal_run.tcl"] = vcf_match.group(1).strip()
        else:
            # Try second tcl block
            all_tcl = re.findall(r"```tcl\s*\n(.*?)```", response, re.DOTALL)
            if len(all_tcl) >= 2:
                scripts["vcformal_run.tcl"] = all_tcl[1].strip()

        # Save scripts
        for name, content in scripts.items():
            path = self.formal_dir / name
            path.write_text(content, encoding="utf-8")
            logger.info("Tcl script: %s (%d lines)", name, len(content.splitlines()))

        return scripts

    # ------------------------------------------------------------------
    # 4. Run formal tool
    # ------------------------------------------------------------------

    def run_formal(self, tool: str = "jasper") -> FormalResult:
        """Run formal verification tool.

        Args:
            tool: "jasper" or "vcformal"
        """
        result = FormalResult(tool_used=tool)

        tcl_file = self.formal_dir / f"{tool}_run.tcl"
        if not tcl_file.exists():
            logger.warning("Tcl script not found: %s", tcl_file)
            result.log = f"Tcl script {tcl_file} not found"
            return result

        # Build command based on tool
        if tool == "jasper":
            cmd = ["jg", "-batch", str(tcl_file)]
        elif tool == "vcformal":
            cmd = ["vcf", "-batch", str(tcl_file)]
        else:
            cmd = [tool, "-batch", str(tcl_file)]

        logger.info("Running formal verification: %s...", " ".join(cmd))
        start = time.time()

        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=config.SIMULATION_TIMEOUT * 5,  # Formal takes longer
                cwd=str(self.formal_dir),
            )
            result.runtime_seconds = time.time() - start
            result.log = proc.stdout + "\n" + proc.stderr

            # Parse results
            self._parse_formal_results(proc.stdout, result)

            logger.info("Formal verification: %s", result.summary)

        except FileNotFoundError:
            result.log = f"Tool '{tool}' not found on PATH"
            logger.warning("Formal tool not found: %s", tool)
        except subprocess.TimeoutExpired:
            result.runtime_seconds = time.time() - start
            result.log = "Formal verification timed out"
        except Exception as e:
            result.log = f"Error: {e}"

        # Save log
        log_path = self.formal_dir / f"{tool}_results.log"
        log_path.write_text(result.log, encoding="utf-8")

        return result

    # ------------------------------------------------------------------
    # 5. Debug guidance
    # ------------------------------------------------------------------

    async def debug_failures(
        self,
        result: FormalResult,
        rtl_code: str,
        bind_code: str,
    ) -> str:
        """Generate debug guidance for failed properties."""
        if not result.failed_properties:
            return "No failures to debug."

        failures = "\n".join(
            f"  FAILED: {p.name}\n    SVA: {p.sva_code}\n    CEX: {p.cex_trace}"
            for p in result.failed_properties
        )

        prompt = """\
You are an expert formal verification debugger.

TASK: Diagnose why the following formal properties FAILED.

FAILED PROPERTIES:
{failures}

For each failure, provide:
1. ROOT CAUSE: Why did the property fail?
2. IS IT A BUG? Is this an RTL bug or a testbench issue?
3. BUG CLASSIFICATION: timing / protocol / logic / connectivity / constraint
4. SUGGESTED FIX: Exact code change to fix (RTL or SVA)
"""

        context = {
            "RTL CODE": rtl_code,
            "BIND MODULE": bind_code,
            "FORMAL LOG": result.log[-2000:],  # Last 2000 chars
        }

        response = await self.ai_client.generate_with_context(
            system_prompt=prompt.format(failures=failures),
            context_blocks=context,
            focus_instruction="Debug formal verification failures",
            task_instruction="Diagnose root causes and suggest fixes",
            temperature=0.3,
        )

        # Save debug report
        path = self.formal_dir / "formal_debug_report.md"
        path.write_text(response, encoding="utf-8")

        return response

    # ------------------------------------------------------------------
    # Full flow
    # ------------------------------------------------------------------

    async def run_full_flow(
        self,
        rtl_code: str,
        spec: DesignSpecification,
        mental_model: MentalModel,
        rtl_analysis: RTLAnalysis,
        rtl_files: list[str],
        tool: str = "jasper",
    ) -> FormalResult:
        """Run end-to-end formal verification.

        1. Create test plan
        2. Generate bind module
        3. Generate Tcl scripts
        4. Run tool
        5. Debug if needed
        """
        # 1. Test plan
        testplan = await self.create_testplan(rtl_code, spec, mental_model, rtl_analysis)

        # 2. Bind module
        bind_code = await self.create_bind_module(rtl_code, spec, testplan)

        # 3. Tcl scripts
        await self.create_tcl_scripts(spec, rtl_files)

        # 4. Run
        result = self.run_formal(tool)

        # 5. Debug if failures
        if result.failed > 0:
            await self.debug_failures(result, rtl_code, bind_code)

        return result

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _parse_testplan(self, response: str) -> FormalTestPlan:
        """Parse LLM response into FormalTestPlan."""
        import json
        plan = FormalTestPlan()

        # Try JSON parse
        json_match = re.search(r"\[.*\]", response, re.DOTALL)
        if json_match:
            try:
                items = json.loads(json_match.group())
                for item in items:
                    prop = FormalProperty(
                        name=item.get("name", "unnamed"),
                        kind=item.get("kind", "assert"),
                        description=item.get("description", ""),
                        sva_code=item.get("sva_code", ""),
                    )
                    if prop.kind == "assert":
                        plan.properties.append(prop)
                    elif prop.kind == "assume":
                        plan.assumptions.append(prop)
                    elif prop.kind == "cover":
                        plan.covers.append(prop)
                return plan
            except json.JSONDecodeError:
                pass

        # Fallback: regex parse
        for match in re.finditer(
            r"(assert|assume|cover)\s+property\s+(\w+)", response, re.IGNORECASE
        ):
            prop = FormalProperty(name=match.group(2), kind=match.group(1).lower())
            if prop.kind == "assert":
                plan.properties.append(prop)
            elif prop.kind == "assume":
                plan.assumptions.append(prop)
            elif prop.kind == "cover":
                plan.covers.append(prop)

        return plan

    def _parse_formal_results(self, output: str, result: FormalResult) -> None:
        """Parse formal tool output for proven/failed/inconclusive."""
        result.proven = len(re.findall(r"(?:proven|PROVEN|passed)", output))
        result.failed = len(re.findall(r"(?:FAILED|failed|CEX found)", output))
        result.inconclusive = len(re.findall(r"(?:inconclusive|INCONCLUSIVE|undetermined)", output))
        result.covered = len(re.findall(r"(?:covered|COVERED|hit)", output))
        result.uncovered = len(re.findall(r"(?:uncovered|UNCOVERED|unreachable)", output))
        result.total_properties = result.proven + result.failed + result.inconclusive

    def _save_testplan(self, plan: FormalTestPlan) -> None:
        """Save test plan as markdown."""
        lines = ["# Formal Verification Test Plan", ""]
        lines.append(f"**{plan.summary}**\n")

        lines.append("## Assertions")
        for p in plan.properties:
            lines.append(f"### {p.name}")
            lines.append(f"{p.description}\n```systemverilog\n{p.sva_code}\n```\n")

        lines.append("## Assumptions")
        for p in plan.assumptions:
            lines.append(f"### {p.name}")
            lines.append(f"{p.description}\n```systemverilog\n{p.sva_code}\n```\n")

        lines.append("## Cover Properties")
        for p in plan.covers:
            lines.append(f"### {p.name}")
            lines.append(f"{p.description}\n```systemverilog\n{p.sva_code}\n```\n")

        path = self.formal_dir / "formal_testplan.md"
        path.write_text("\n".join(lines), encoding="utf-8")

    def _spec_summary(self, spec: DesignSpecification) -> str:
        ports = "\n".join(f"  {p.direction.value} {p.name} [{p.width-1}:0]" for p in spec.ports)
        reqs = "\n".join(f"  - {r}" for r in spec.functional_requirements)
        return f"Module: {spec.module_name}\nPorts:\n{ports}\nRequirements:\n{reqs}"

    def _model_summary(self, model: MentalModel) -> str:
        plan = "\n".join(f"  - {t.name}: {t.description}" for t in model.verification_plan[:10])
        corners = "\n".join(f"  - {c}" for c in model.corner_cases[:10])
        return f"Verification Plan:\n{plan}\nCorner Cases:\n{corners}"
