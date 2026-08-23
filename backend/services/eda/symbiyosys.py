"""
SymbiYosys Runner — Execute formal verification via SymbiYosys (sby).

Generates .sby configuration files, executes them, and parses results
to produce structured per-property PASS/FAIL reports.
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# ─── Data Types ──────────────────────────────────────────────────────

@dataclass
class PropertyResult:
    """Result for a single formal property."""
    name: str
    status: str  # "PASS" | "FAIL" | "UNKNOWN"
    depth: int = 0
    counterexample_vcd: str = ""
    engine: str = ""
    time_seconds: float = 0.0


@dataclass
class SbyResult:
    """Full result from a SymbiYosys run."""
    status: str  # "PASS" | "FAIL" | "UNKNOWN" | "TIMEOUT" | "ERROR"
    properties: List[PropertyResult] = field(default_factory=list)
    log_file: str = ""
    elapsed_seconds: float = 0.0
    engine: str = "smtbmc"
    error_message: str = ""
    work_dir: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status,
            "properties": [
                {
                    "name": p.name,
                    "status": p.status,
                    "depth": p.depth,
                    "counterexample": p.counterexample_vcd,
                    "engine": p.engine,
                }
                for p in self.properties
            ],
            "log_file": self.log_file,
            "elapsed_seconds": self.elapsed_seconds,
            "engine": self.engine,
            "error_message": self.error_message,
            "pass_count": sum(1 for p in self.properties if p.status == "PASS"),
            "fail_count": sum(1 for p in self.properties if p.status == "FAIL"),
            "total_count": len(self.properties),
        }


# ─── .sby Config Generation ─────────────────────────────────────────

def generate_sby_config(
    rtl_files: List[str],
    sva_file: str,
    top_module: str,
    work_dir: str,
    mode: str = "bmc",
    depth: int = 20,
    engine: str = "smtbmc",
) -> str:
    """
    Generate a .sby configuration file for SymbiYosys.

    Args:
        rtl_files: List of RTL source file paths
        sva_file: Path to the SVA/assertions file
        top_module: Top-level module name
        work_dir: Working directory for output
        mode: Verification mode — "bmc", "prove", "cover"
        depth: BMC depth (number of cycles)
        engine: SMT engine — "smtbmc", "aiger", "abc"

    Returns:
        Path to the generated .sby file.
    """
    work = Path(work_dir)
    work.mkdir(parents=True, exist_ok=True)

    # Collect all source files (resolve to absolute paths)
    all_sources = []
    for f in rtl_files:
        abs_path = str(Path(f).resolve())
        all_sources.append(abs_path)
    if sva_file:
        abs_sva = str(Path(sva_file).resolve())
        all_sources.append(abs_sva)

    # Determine file read commands
    read_commands = []
    for src in all_sources:
        ext = Path(src).suffix.lower()
        if ext in (".sv", ".svh"):
            read_commands.append(f"read -sv {src}")
        elif ext in (".v", ".vh"):
            read_commands.append(f"read -vlog2k {src}")
        elif ext in (".vhd", ".vhdl"):
            read_commands.append(f"read -vhdl {src}")
        else:
            read_commands.append(f"read -sv {src}")

    # Engine configuration
    if engine == "smtbmc":
        engine_line = f"smtbmc z3"
    elif engine == "aiger":
        engine_line = "aiger avy"
    elif engine == "abc":
        engine_line = "abc pdr"
    else:
        engine_line = f"smtbmc z3"

    # Build .sby content
    sby_content = f"""[tasks]
{mode}

[options]
{mode}:
mode {mode}
depth {depth}

[engines]
{mode}:
{engine_line}

[script]
{chr(10).join(read_commands)}
prep -top {top_module}

[files]
{chr(10).join(all_sources)}
"""

    sby_path = str(work / f"{top_module}_formal.sby")
    with open(sby_path, "w", encoding="utf-8") as f:
        f.write(sby_content)

    logger.info(f"Generated .sby config: {sby_path} (mode={mode}, depth={depth})")
    return sby_path


# ─── SymbiYosys Execution ───────────────────────────────────────────

def _find_sby_binary() -> Optional[str]:
    """Find the sby binary on PATH or via environment variable."""
    # Check env var first
    env_bin = os.environ.get("CHIPVERIFY_SBY_BIN")
    if env_bin and Path(env_bin).exists():
        return env_bin

    # Check PATH
    import shutil
    sby_path = shutil.which("sby")
    if sby_path:
        return sby_path

    return None


def run_sby(
    sby_path: str,
    work_dir: str,
    timeout_seconds: int = 300,
) -> SbyResult:
    """
    Execute SymbiYosys on a .sby configuration file.

    Args:
        sby_path: Path to the .sby file
        work_dir: Working directory
        timeout_seconds: Maximum execution time

    Returns:
        SbyResult with per-property status and log file path.
    """
    sby_bin = _find_sby_binary()
    if not sby_bin:
        return SbyResult(
            status="ERROR",
            error_message=(
                "SymbiYosys (sby) not found. "
                "Install it: https://symbiyosys.readthedocs.io/en/latest/install.html "
                "Or set CHIPVERIFY_SBY_BIN environment variable."
            ),
        )

    sby_file = Path(sby_path)
    if not sby_file.exists():
        return SbyResult(
            status="ERROR",
            error_message=f".sby file not found: {sby_path}",
        )

    start_t = time.time()
    log_file = str(Path(work_dir) / f"{sby_file.stem}.log")

    try:
        result = subprocess.run(
            [sby_bin, "-f", str(sby_file)],
            cwd=work_dir,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            env={**os.environ, "LANG": "C"},
        )

        elapsed = time.time() - start_t

        # Save log
        combined_output = result.stdout + "\n" + result.stderr
        with open(log_file, "w", encoding="utf-8") as f:
            f.write(combined_output)

        # Parse results
        parsed = parse_sby_log(log_file)
        parsed.elapsed_seconds = elapsed
        parsed.log_file = log_file
        parsed.work_dir = work_dir

        # Overall status from return code
        if result.returncode == 0:
            parsed.status = "PASS"
        elif result.returncode == 1:
            parsed.status = "FAIL"
        else:
            if parsed.status not in ("PASS", "FAIL"):
                parsed.status = "ERROR"
                parsed.error_message = (
                    parsed.error_message
                    or f"sby exited with code {result.returncode}"
                )

        logger.info(
            f"SymbiYosys completed: {parsed.status} "
            f"({parsed.elapsed_seconds:.1f}s, "
            f"{len(parsed.properties)} properties)"
        )
        return parsed

    except subprocess.TimeoutExpired:
        elapsed = time.time() - start_t
        return SbyResult(
            status="TIMEOUT",
            elapsed_seconds=elapsed,
            log_file=log_file,
            error_message=f"Timed out after {timeout_seconds}s",
        )

    except Exception as e:
        elapsed = time.time() - start_t
        return SbyResult(
            status="ERROR",
            elapsed_seconds=elapsed,
            log_file=log_file,
            error_message=str(e),
        )


# ─── Log Parsing ────────────────────────────────────────────────────

# Regex patterns for SymbiYosys log parsing
RE_STATUS = re.compile(
    r"SBY\s+[\d:.]+\s+\[(\w+)\]\s+engine_(\d+):\s+Status returned by engine:\s+(\w+)"
)
RE_PROPERTY_PASS = re.compile(
    r"SBY\s+[\d:.]+\s+\[(\w+)\]\s+Checking assertion\s+(\S+)\s+in step\s+(\d+)\.\.\.\s*$"
)
RE_ASSERT_RESULT = re.compile(
    r"SBY\s+[\d:.]+\s+\[(\w+)\]\s+(Checking|Proved|Assert failed)\s+.*?(\S+\.(\w+))"
)
RE_SUMMARY = re.compile(
    r"SBY\s+[\d:.]+\s+\[(\w+)\]\s+summary:\s+(.*)"
)
RE_TRACE = re.compile(
    r"SBY\s+[\d:.]+\s+\[(\w+)\]\s+writing trace to\s+(\S+)"
)
RE_PROPERTY_STATUS = re.compile(
    r"SBY\s+[\d:.]+\s+\[(\w+)\]\s+(PASS|FAIL)\s+-\s+(.+)"
)
RE_BASE_STATUS = re.compile(
    r"SBY\s+[\d:.]+\s+\[(\w+)\]\s+basecase\s+(?:passed|failed)"
)
RE_INDUCTION = re.compile(
    r"SBY\s+[\d:.]+\s+\[(\w+)\]\s+induction\s+(?:passed|failed)"
)


def parse_sby_log(log_path: str) -> SbyResult:
    """
    Parse a SymbiYosys log file to extract per-property results.

    Handles both BMC and prove mode output formats.
    """
    result = SbyResult(status="UNKNOWN", log_file=log_path)

    try:
        with open(log_path, "r", encoding="utf-8", errors="ignore") as f:
            log_content = f.read()
    except OSError as e:
        result.status = "ERROR"
        result.error_message = f"Cannot read log file: {e}"
        return result

    lines = log_content.splitlines()
    properties: Dict[str, PropertyResult] = {}
    trace_files: Dict[str, str] = {}

    for line in lines:
        # Engine-level status
        m = RE_STATUS.match(line)
        if m:
            task, engine_id, status = m.group(1), m.group(2), m.group(3)
            result.engine = f"engine_{engine_id}"
            if status.upper() == "PASS":
                result.status = "PASS"
            elif status.upper() == "FAIL":
                result.status = "FAIL"
            continue

        # Per-property PASS/FAIL
        m = RE_PROPERTY_STATUS.match(line)
        if m:
            task, status, prop_name = m.group(1), m.group(2), m.group(3).strip()
            prop = properties.setdefault(
                prop_name, PropertyResult(name=prop_name, status="UNKNOWN")
            )
            prop.status = status.upper()
            continue

        # Trace file (counterexample)
        m = RE_TRACE.match(line)
        if m:
            task, trace_path = m.group(1), m.group(2)
            trace_files[task] = trace_path
            continue

        # Summary lines for depth info
        m = RE_SUMMARY.match(line)
        if m:
            task, summary_text = m.group(1), m.group(2)
            depth_match = re.search(r"bound reached at (\d+)", summary_text)
            if depth_match:
                depth = int(depth_match.group(1))
                for prop in properties.values():
                    if prop.depth == 0:
                        prop.depth = depth
            continue

    # If no per-property results found, create a single aggregate
    if not properties:
        properties["(all)"] = PropertyResult(
            name="(all properties)",
            status=result.status,
        )

    # Attach counterexamples to FAIL properties
    for prop in properties.values():
        if prop.status == "FAIL":
            for task, trace in trace_files.items():
                prop.counterexample_vcd = trace
                break

    result.properties = list(properties.values())
    return result


# ─── High-Level API ──────────────────────────────────────────────────

def run_formal_verification(
    rtl_files: List[str],
    sva_file: str,
    top_module: str,
    work_dir: str,
    mode: str = "bmc",
    depth: int = 20,
    timeout_seconds: int = 300,
) -> SbyResult:
    """
    End-to-end formal verification:
    1. Generate .sby config
    2. Run SymbiYosys
    3. Parse and return results

    This is the main entry point for formal verification.
    """
    # Generate config
    sby_path = generate_sby_config(
        rtl_files=rtl_files,
        sva_file=sva_file,
        top_module=top_module,
        work_dir=work_dir,
        mode=mode,
        depth=depth,
    )

    # Execute
    result = run_sby(sby_path, work_dir, timeout_seconds)

    return result
