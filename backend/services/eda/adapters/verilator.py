"""
Verilator Adapter — Lint and simulation with Verilator.

Verilator is primarily a linter + cycle-accurate simulator.
Two modes:
  Lint:    verilator --lint-only --Wall <files>
  Sim:     verilator --binary --top-module <tb> <files> && ./obj_dir/V<tb>
"""

from __future__ import annotations

import asyncio
import logging
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class LintResult:
    """Structured result from Verilator lint."""
    success: bool = False
    warnings: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    log: str = ""


def is_available() -> bool:
    """Check if verilator is installed."""
    return shutil.which("verilator") is not None


def get_version() -> str:
    """Get verilator version."""
    import subprocess
    try:
        result = subprocess.run(
            ["verilator", "--version"],
            capture_output=True, text=True, timeout=5,
        )
        return result.stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return ""


async def lint(
    rtl_files: List[str],
    work_dir: str,
    top_module: str = "",
    include_dirs: Optional[List[str]] = None,
) -> LintResult:
    """Run Verilator lint-only check."""
    result = LintResult()
    work = Path(work_dir)
    work.mkdir(parents=True, exist_ok=True)

    cmd = ["verilator", "--lint-only", "-Wall"]
    if top_module:
        cmd.extend(["--top-module", top_module])
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
        result.log = (
            stdout.decode(errors="replace") + stderr.decode(errors="replace")
        ).strip()
        result.success = proc.returncode == 0

        for line in result.log.splitlines():
            line_s = line.strip()
            if "%Warning" in line_s:
                result.warnings.append(line_s)
            elif "%Error" in line_s:
                result.errors.append(line_s)

    except (asyncio.TimeoutError, FileNotFoundError) as e:
        result.log = str(e)
        result.errors = [str(e)]

    return result


async def compile_and_run(
    rtl_files: List[str],
    tb_file: str,
    work_dir: str,
    top_module: str = "",
    include_dirs: Optional[List[str]] = None,
    timeout_seconds: int = 60,
) -> Dict:
    """Compile and run simulation with Verilator."""
    from services.eda.adapters.icarus import SimulationResult

    result = SimulationResult()
    work = Path(work_dir)
    work.mkdir(parents=True, exist_ok=True)

    if not top_module:
        top_module = Path(tb_file).stem

    # Compile
    cmd = [
        "verilator", "--binary", "-j", "0",
        "--top-module", top_module,
        "-Wno-fatal",  # Don't abort on warnings
    ]
    if include_dirs:
        for d in include_dirs:
            cmd.extend(["-I", d])
    cmd.extend(rtl_files)
    cmd.append(tb_file)

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(work),
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=60)
        result.compile_log = (
            stdout.decode(errors="replace") + stderr.decode(errors="replace")
        ).strip()
        result.compile_success = proc.returncode == 0

        if not result.compile_success:
            result.compile_errors = [
                l.strip() for l in result.compile_log.splitlines()
                if "%Error" in l
            ][:20]
            return result

        # Run
        binary = work / "obj_dir" / f"V{top_module}"
        if binary.exists():
            sim_proc = await asyncio.create_subprocess_exec(
                str(binary),
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

    except asyncio.TimeoutError:
        result.timed_out = True
        result.sim_errors = ["Timeout"]
    except FileNotFoundError:
        result.compile_errors = ["verilator not found"]

    return result
