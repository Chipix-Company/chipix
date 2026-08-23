"""
Icarus Verilog Adapter — Compile and run simulations with iverilog/vvp.

Commands:
  Compile: iverilog -g2012 -o <out>.vvp <files...>
  Run:     vvp <out>.vvp
  Waves:   Generates .vcd files for waveform viewing

Supports SystemVerilog 2012 features via -g2012 flag.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class SimulationResult:
    """Structured result from a simulation run."""
    compile_success: bool = False
    sim_success: bool = False
    compile_log: str = ""
    sim_log: str = ""
    compile_errors: List[str] = field(default_factory=list)
    sim_errors: List[str] = field(default_factory=list)
    tests_passed: int = 0
    tests_failed: int = 0
    assertion_failures: int = 0
    timed_out: bool = False
    vcd_path: str = ""
    return_code: int = -1


def is_available() -> bool:
    """Check if iverilog is installed and accessible."""
    return shutil.which("iverilog") is not None


def get_version() -> str:
    """Get iverilog version string."""
    try:
        result = subprocess.run(
            ["iverilog", "-V"],
            capture_output=True, text=True, timeout=5,
        )
        for line in result.stdout.splitlines():
            if "Icarus Verilog" in line:
                return line.strip()
        return "iverilog (unknown version)"
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return ""


async def compile_and_run(
    rtl_files: List[str],
    tb_file: str,
    work_dir: str,
    top_module: str = "",
    include_dirs: Optional[List[str]] = None,
    defines: Optional[Dict[str, str]] = None,
    timeout_seconds: int = 60,
) -> SimulationResult:
    """
    Compile and run a simulation with iverilog.

    Args:
        rtl_files: List of RTL source files (absolute paths)
        tb_file: Testbench file (absolute path)
        work_dir: Working directory for output files
        top_module: Top module name (optional, iverilog auto-detects)
        include_dirs: Additional include directories
        defines: Preprocessor defines
        timeout_seconds: Max simulation time
    """
    result = SimulationResult()
    work = Path(work_dir)
    work.mkdir(parents=True, exist_ok=True)

    vvp_path = str(work / "sim.vvp")

    # ── Step 1: Compile ────────────────────────────────────────
    cmd = ["iverilog", "-g2012", "-o", vvp_path]

    if top_module:
        cmd.extend(["-s", top_module])

    if include_dirs:
        for d in include_dirs:
            cmd.extend(["-I", d])

    if defines:
        for k, v in defines.items():
            cmd.extend(["-D", f"{k}={v}" if v else f"-D{k}"])

    # Add source files: RTL first, then testbench
    cmd.extend(rtl_files)
    cmd.append(tb_file)

    logger.info(f"Compiling: {' '.join(cmd)}")

    try:
        compile_proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(work),
        )
        stdout, stderr = await asyncio.wait_for(
            compile_proc.communicate(), timeout=30,
        )

        result.compile_log = (
            stdout.decode(errors="replace") + stderr.decode(errors="replace")
        ).strip()
        result.compile_success = compile_proc.returncode == 0

        if not result.compile_success:
            result.compile_errors = _extract_errors(result.compile_log)
            logger.warning(f"Compilation failed: {result.compile_errors[:3]}")
            return result

        logger.info("Compilation successful")

    except asyncio.TimeoutError:
        result.compile_log = "Compilation timed out (30s)"
        result.compile_errors = ["Compilation timeout"]
        return result
    except FileNotFoundError:
        result.compile_log = "iverilog not found. Install with: apt install iverilog"
        result.compile_errors = ["iverilog not installed"]
        return result

    # ── Step 2: Run Simulation ─────────────────────────────────
    sim_cmd = ["vvp", vvp_path]
    logger.info(f"Simulating: {' '.join(sim_cmd)}")

    try:
        sim_proc = await asyncio.create_subprocess_exec(
            *sim_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(work),
        )
        stdout, stderr = await asyncio.wait_for(
            sim_proc.communicate(), timeout=timeout_seconds,
        )

        result.sim_log = (
            stdout.decode(errors="replace") + stderr.decode(errors="replace")
        ).strip()
        result.return_code = sim_proc.returncode
        result.sim_success = sim_proc.returncode == 0

        # Parse results
        _parse_sim_output(result)

        # Check for VCD file
        vcd_candidates = list(work.glob("*.vcd"))
        if vcd_candidates:
            result.vcd_path = str(vcd_candidates[0])

        logger.info(
            f"Simulation complete: "
            f"{result.tests_passed} passed, {result.tests_failed} failed, "
            f"{result.assertion_failures} assertion failures"
        )

    except asyncio.TimeoutError:
        result.sim_log = f"Simulation timed out ({timeout_seconds}s)"
        result.timed_out = True
        result.sim_errors = ["Simulation timeout — possible infinite loop"]
        logger.warning("Simulation timed out")

    return result


async def compile_only(
    rtl_files: List[str],
    work_dir: str,
    include_dirs: Optional[List[str]] = None,
) -> SimulationResult:
    """Compile-only check (syntax verification, no simulation)."""
    result = SimulationResult()
    work = Path(work_dir)
    work.mkdir(parents=True, exist_ok=True)

    cmd = ["iverilog", "-g2012", "-o", "/dev/null"]  # Compile only
    if include_dirs:
        for d in include_dirs:
            cmd.extend(["-I", d])
    cmd.extend(rtl_files)

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(work),
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
        result.compile_log = (
            stdout.decode(errors="replace") + stderr.decode(errors="replace")
        ).strip()
        result.compile_success = proc.returncode == 0
        if not result.compile_success:
            result.compile_errors = _extract_errors(result.compile_log)
    except (asyncio.TimeoutError, FileNotFoundError) as e:
        result.compile_log = str(e)
        result.compile_errors = [str(e)]

    return result


# ═══════════════════════════════════════════════════════════════════════
# Output Parsing
# ═══════════════════════════════════════════════════════════════════════


def _parse_sim_output(result: SimulationResult) -> None:
    """Parse simulation log for pass/fail/error patterns."""
    for line in result.sim_log.splitlines():
        line_lower = line.lower().strip()

        # Count passes
        if "[pass]" in line_lower:
            result.tests_passed += 1
        elif "[fail]" in line_lower:
            result.tests_failed += 1

        # Assertion failures
        if "assertion" in line_lower and ("fail" in line_lower or "error" in line_lower):
            result.assertion_failures += 1

        # Error lines
        if line_lower.startswith("error") or "$fatal" in line_lower:
            result.sim_errors.append(line.strip())

        # Timeout detection
        if "timeout" in line_lower:
            result.timed_out = True

    # If no explicit pass/fail markers, check return code
    if result.tests_passed == 0 and result.tests_failed == 0:
        if result.return_code == 0:
            result.tests_passed = 1  # Assume pass if no errors
        elif "fatal" in result.sim_log.lower():
            result.tests_failed = 1


def _extract_errors(log: str) -> List[str]:
    """Extract error lines from compilation log."""
    errors = []
    for line in log.splitlines():
        line_s = line.strip()
        if any(k in line_s.lower() for k in ("error", "fatal", "syntax")):
            errors.append(line_s)
    return errors[:20]
