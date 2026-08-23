"""
Gap 2 — Commercial Simulator Support.

Abstract simulator interface supporting:
  - Icarus Verilog (iverilog) — open-source, free
  - Synopsys VCS — commercial
  - Cadence Xcelium — commercial
  - Mentor Questa — commercial

Each simulator has different compile/run commands, flag formats,
and output parsing. This module normalizes them all.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from core.logger import get_logger

logger = get_logger("Simulator")


class SimulatorType(str, Enum):
    """Supported simulator types."""
    IVERILOG = "iverilog"
    VCS = "vcs"
    XCELIUM = "xcelium"
    QUESTA = "questa"


@dataclass
class SimCompileResult:
    """Compilation result from any simulator."""
    success: bool = False
    simulator: str = ""
    command: str = ""
    stdout: str = ""
    stderr: str = ""
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    elapsed: float = 0.0


@dataclass
class SimRunResult:
    """Simulation run result from any simulator."""
    success: bool = False
    simulator: str = ""
    test_name: str = ""
    command: str = ""
    stdout: str = ""
    stderr: str = ""
    pass_count: int = 0
    fail_count: int = 0
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    elapsed: float = 0.0
    vcd_file: str = ""
    coverage_file: str = ""
    log_file: str = ""

    @property
    def summary(self) -> str:
        status = "PASS" if self.success else "FAIL"
        return (
            f"[{status}] {self.test_name} ({self.simulator}): "
            f"{self.pass_count}P/{self.fail_count}F, {self.elapsed:.1f}s"
        )


class SimulatorInterface:
    """Unified interface for all supported simulators."""

    def __init__(
        self,
        simulator: SimulatorType | str = SimulatorType.IVERILOG,
        work_dir: str | Path = "output/simulation",
        timeout: int = 300,
    ) -> None:
        if isinstance(simulator, str):
            simulator = SimulatorType(simulator.lower())
        self.simulator = simulator
        self.work_dir = Path(work_dir)
        self.work_dir.mkdir(parents=True, exist_ok=True)
        self.timeout = timeout
        self._check_availability()

    # ------------------------------------------------------------------
    # Auto-detect best available simulator
    # ------------------------------------------------------------------

    @staticmethod
    def detect_best() -> SimulatorType:
        """Detect the best available simulator on the system."""
        checks = [
            (SimulatorType.VCS, "vcs"),
            (SimulatorType.XCELIUM, "xrun"),
            (SimulatorType.QUESTA, "vsim"),
            (SimulatorType.IVERILOG, "iverilog"),
        ]
        for sim_type, binary in checks:
            if shutil.which(binary):
                logger.info("Detected simulator: %s (%s)", sim_type.value, binary)
                return sim_type
        logger.warning("No simulator detected, defaulting to iverilog")
        return SimulatorType.IVERILOG

    def _check_availability(self) -> None:
        """Check if the selected simulator is available."""
        binary = self._get_binary()
        if not shutil.which(binary):
            logger.warning(
                "Simulator '%s' (%s) not found on PATH. "
                "Commands will be generated but not executed.",
                self.simulator.value, binary,
            )

    def _get_binary(self) -> str:
        """Get the binary name for the selected simulator."""
        return {
            SimulatorType.IVERILOG: "iverilog",
            SimulatorType.VCS: "vcs",
            SimulatorType.XCELIUM: "xrun",
            SimulatorType.QUESTA: "vlog",
        }[self.simulator]

    # ------------------------------------------------------------------
    # Compile
    # ------------------------------------------------------------------

    def compile(
        self,
        files: list[str | Path],
        top_module: str = "tb_top",
        defines: dict[str, str] | None = None,
        includes: list[str] | None = None,
        extra_flags: list[str] | None = None,
    ) -> SimCompileResult:
        """Compile files with the selected simulator."""
        result = SimCompileResult(simulator=self.simulator.value)

        cmd = self._build_compile_cmd(files, top_module, defines, includes, extra_flags)
        result.command = " ".join(str(c) for c in cmd)

        logger.info("Compiling with %s: %d files", self.simulator.value, len(files))

        start = time.time()
        try:
            proc = subprocess.run(
                cmd, capture_output=True, text=True,
                timeout=self.timeout, cwd=str(self.work_dir),
            )
            result.elapsed = time.time() - start
            result.stdout = proc.stdout
            result.stderr = proc.stderr
            result.success = (proc.returncode == 0)
            result.errors = self._extract_errors(proc.stderr + proc.stdout)
            result.warnings = self._extract_warnings(proc.stderr + proc.stdout)

            if result.success:
                logger.info("Compilation successful (%.1fs)", result.elapsed)
            else:
                logger.warning("Compilation failed: %d errors", len(result.errors))

        except FileNotFoundError:
            result.errors.append(f"Simulator binary not found: {self._get_binary()}")
        except subprocess.TimeoutExpired:
            result.elapsed = time.time() - start
            result.errors.append(f"Compilation timed out ({self.timeout}s)")
        except Exception as e:
            result.errors.append(str(e))

        return result

    def _build_compile_cmd(self, files, top_module, defines, includes, extra_flags):
        """Build simulator-specific compile command."""
        files_str = [str(f) for f in files]

        if self.simulator == SimulatorType.IVERILOG:
            cmd = ["iverilog", "-g2012", "-Wall", "-o", "simv"] + files_str
            if defines:
                cmd.extend(f"-D{k}={v}" for k, v in defines.items())
            if includes:
                cmd.extend(f"-I{d}" for d in includes)

        elif self.simulator == SimulatorType.VCS:
            cmd = ["vcs", "-sverilog", "-full64", "-debug_access+all",
                   "-timescale=1ns/1ps", f"-top {top_module}",
                   "-o", "simv", "+lint=all"] + files_str
            if defines:
                cmd.extend(f"+define+{k}={v}" for k, v in defines.items())
            if includes:
                cmd.extend(f"+incdir+{d}" for d in includes)
            cmd.append("-cm line+cond+fsm+branch+tgl")  # Coverage

        elif self.simulator == SimulatorType.XCELIUM:
            cmd = ["xrun", "-sv", "-access +rwc", "-64bit",
                   f"-top {top_module}", "-elaborate"] + files_str
            if defines:
                cmd.extend(f"-define {k}={v}" for k, v in defines.items())
            if includes:
                cmd.extend(f"-incdir {d}" for d in includes)
            cmd.extend(["-coverage all", "-covoverwrite"])

        elif self.simulator == SimulatorType.QUESTA:
            cmd = ["vlog", "-sv", "+acc", "-timescale", "1ns/1ps"] + files_str
            if defines:
                cmd.extend(f"+define+{k}={v}" for k, v in defines.items())
            if includes:
                cmd.extend(f"+incdir+{d}" for d in includes)

        if extra_flags:
            cmd.extend(extra_flags)

        return cmd

    # ------------------------------------------------------------------
    # Run simulation
    # ------------------------------------------------------------------

    def run(
        self,
        test_name: str = "default",
        uvm_test: str = "",
        plusargs: list[str] | None = None,
        dump_vcd: bool = True,
    ) -> SimRunResult:
        """Run simulation with the selected simulator."""
        result = SimRunResult(
            simulator=self.simulator.value,
            test_name=test_name,
        )

        cmd = self._build_run_cmd(test_name, uvm_test, plusargs, dump_vcd)
        result.command = " ".join(str(c) for c in cmd)

        logger.info("Running simulation: %s (%s)", test_name, self.simulator.value)

        start = time.time()
        try:
            proc = subprocess.run(
                cmd, capture_output=True, text=True,
                timeout=self.timeout, cwd=str(self.work_dir),
            )
            result.elapsed = time.time() - start
            result.stdout = proc.stdout
            result.stderr = proc.stderr
            result.errors = self._extract_errors(proc.stdout + proc.stderr)
            result.warnings = self._extract_warnings(proc.stdout + proc.stderr)
            result.pass_count = len(re.findall(r"(?:PASS|pass|UVM_INFO.*PASSED)", proc.stdout))
            result.fail_count = len(re.findall(r"(?:FAIL|ERROR|UVM_FATAL|UVM_ERROR)", proc.stdout))
            result.success = (proc.returncode == 0 and result.fail_count == 0)

            # Save log
            log_path = self.work_dir / f"{test_name}.log"
            log_path.write_text(proc.stdout + "\n" + proc.stderr, encoding="utf-8")
            result.log_file = str(log_path)

            # Check for VCD
            vcd_path = self.work_dir / f"{test_name}.vcd"
            if vcd_path.exists():
                result.vcd_file = str(vcd_path)

            logger.info("Simulation: %s", result.summary)

        except FileNotFoundError:
            result.errors.append(f"Simulator runner not found")
        except subprocess.TimeoutExpired:
            result.elapsed = time.time() - start
            result.errors.append(f"Simulation timed out ({self.timeout}s)")
        except Exception as e:
            result.errors.append(str(e))

        return result

    def _build_run_cmd(self, test_name, uvm_test, plusargs, dump_vcd):
        """Build simulator-specific run command."""
        if self.simulator == SimulatorType.IVERILOG:
            cmd = ["vvp", "simv"]
            if dump_vcd:
                cmd.append(f"+vcd_file={test_name}.vcd")

        elif self.simulator == SimulatorType.VCS:
            cmd = ["./simv"]
            if uvm_test:
                cmd.append(f"+UVM_TESTNAME={uvm_test}")
            if dump_vcd:
                cmd.extend(["-cm", "line+cond+fsm+branch+tgl"])
            cmd.append(f"+ntb_random_seed_automatic")

        elif self.simulator == SimulatorType.XCELIUM:
            cmd = ["xrun", "-R"]
            if uvm_test:
                cmd.append(f"+UVM_TESTNAME={uvm_test}")
            cmd.extend(["-coverage all", "-covoverwrite"])

        elif self.simulator == SimulatorType.QUESTA:
            cmd = ["vsim", "-c", "-do", f"run -all; quit", "tb_top"]
            if uvm_test:
                cmd.extend(["-G", f"UVM_TESTNAME={uvm_test}"])

        if plusargs:
            cmd.extend(plusargs)

        return cmd

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _extract_errors(self, text: str) -> list[str]:
        return [
            line.strip() for line in text.splitlines()
            if re.search(r"(?:error|fatal|fail)", line, re.IGNORECASE)
        ][:20]

    def _extract_warnings(self, text: str) -> list[str]:
        return [
            line.strip() for line in text.splitlines()
            if re.search(r"warning", line, re.IGNORECASE)
        ][:20]

    def get_info(self) -> dict:
        """Get simulator info for reporting."""
        binary = self._get_binary()
        available = shutil.which(binary) is not None
        return {
            "simulator": self.simulator.value,
            "binary": binary,
            "available": available,
            "work_dir": str(self.work_dir),
        }
