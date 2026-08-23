"""
Compiler Wrapper — Compile-Before-Commit Engine.

Every generated file is compiled via iverilog before acceptance.
If compilation fails, errors are parsed and fed back to the LLM for retry.

Supports:
  • Full file compilation
  • Incremental chunk compilation (with stub generation)
  • Error classification (syntax, missing module, undeclared signal)
  • Smart error formatting for LLM prompts
"""

from __future__ import annotations

import os
import re
import subprocess
import tempfile
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Optional

from core.logger import get_logger

logger = get_logger("Compiler")


class ErrorKind(str, Enum):
    """Classification of compilation errors."""
    SYNTAX = "syntax"
    MISSING_MODULE = "missing_module"
    UNDECLARED_SIGNAL = "undeclared_signal"
    TYPE_MISMATCH = "type_mismatch"
    PORT_MISMATCH = "port_mismatch"
    DUPLICATE = "duplicate_definition"
    UNKNOWN = "unknown"


@dataclass
class CompileError:
    """A single compilation error."""
    file: str
    line: int
    message: str
    kind: ErrorKind = ErrorKind.UNKNOWN
    raw_text: str = ""


@dataclass
class CompileResult:
    """Result of a compilation attempt."""
    success: bool
    errors: list[CompileError] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    stdout: str = ""
    stderr: str = ""

    @property
    def error_count(self) -> int:
        return len(self.errors)

    @property
    def error_summary(self) -> str:
        if not self.errors:
            return "No errors"
        return "; ".join(f"L{e.line}: {e.message}" for e in self.errors[:5])


class Compiler:
    """Wrapper around iverilog for compilation checks."""

    def __init__(self, simulator: str = "iverilog") -> None:
        self.simulator = simulator
        self._check_simulator()

    def _check_simulator(self) -> None:
        """Check if the simulator is available."""
        try:
            result = subprocess.run(
                [self.simulator, "--version"],
                capture_output=True, text=True, timeout=10,
            )
            if result.returncode == 0:
                version = result.stdout.strip().split("\n")[0]
                logger.info("Simulator found: %s", version)
            else:
                logger.warning("Simulator '%s' returned error. Compilation checks may fail.", self.simulator)
        except FileNotFoundError:
            logger.warning(
                "Simulator '%s' not found on PATH. "
                "Compilation checks will be skipped. "
                "Install Icarus Verilog: https://iverilog.fandom.com/wiki/Installation_Guide",
                self.simulator,
            )
            self.simulator = ""
        except Exception as e:
            logger.warning("Error checking simulator: %s", e)
            self.simulator = ""

    # ------------------------------------------------------------------
    # Full file compilation
    # ------------------------------------------------------------------

    def compile_file(
        self,
        file_path: Path,
        include_dirs: list[Path] | None = None,
        additional_files: list[Path] | None = None,
    ) -> CompileResult:
        """Compile a single SV file and return the result.

        Args:
            file_path: Path to the .sv file to compile
            include_dirs: Include search directories
            additional_files: Other files needed for compilation
        """
        if not self.simulator:
            logger.debug("Simulator not available — skipping compile check")
            return CompileResult(success=True)

        cmd = [self.simulator, "-g2012", "-Wall"]

        # Add include directories
        if include_dirs:
            for d in include_dirs:
                cmd.extend(["-I", str(d)])

        # Add additional dependency files
        if additional_files:
            for f in additional_files:
                if f.exists():
                    cmd.append(str(f))

        # Add the target file
        cmd.append(str(file_path))

        # Output to /dev/null (we only care about errors)
        cmd.extend(["-o", os.devnull])

        return self._run_compile(cmd)

    # ------------------------------------------------------------------
    # Compile from string content (for chunks and generated code)
    # ------------------------------------------------------------------

    def compile_content(
        self,
        content: str,
        filename: str = "generated.sv",
        stub_content: str = "",
        additional_files: list[Path] | None = None,
    ) -> CompileResult:
        """Compile SV code from a string (without saving to disk).

        Args:
            content: The SystemVerilog code to compile
            filename: Name for the temp file
            stub_content: Stub definitions for unresolved dependencies
            additional_files: Other files needed for compilation
        """
        if not self.simulator:
            return CompileResult(success=True)

        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)

            # Write the main file
            main_file = tmpdir_path / filename
            main_file.write_text(content, encoding="utf-8")

            # Write stubs if any
            if stub_content:
                stub_file = tmpdir_path / "_stubs.sv"
                stub_file.write_text(stub_content, encoding="utf-8")

            cmd = [self.simulator, "-g2012", "-Wall"]

            # Add stubs first (so definitions are available)
            if stub_content:
                cmd.append(str(stub_file))

            # Add additional files
            if additional_files:
                for f in additional_files:
                    if f.exists():
                        cmd.append(str(f))

            # Add main file
            cmd.append(str(main_file))
            cmd.extend(["-o", os.devnull])

            return self._run_compile(cmd)

    # ------------------------------------------------------------------
    # Run compilation
    # ------------------------------------------------------------------

    def _run_compile(self, cmd: list[str]) -> CompileResult:
        """Execute the compiler and parse results."""
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=30,
            )

            errors = self._parse_errors(result.stderr)
            warnings = self._parse_warnings(result.stderr)

            compile_result = CompileResult(
                success=(result.returncode == 0),
                errors=errors,
                warnings=warnings,
                stdout=result.stdout,
                stderr=result.stderr,
            )

            if compile_result.success:
                logger.debug("Compilation successful")
            else:
                logger.debug("Compilation failed: %d errors", len(errors))

            return compile_result

        except subprocess.TimeoutExpired:
            return CompileResult(
                success=False,
                errors=[CompileError(file="", line=0, message="Compilation timed out (30s)", kind=ErrorKind.UNKNOWN)],
            )
        except Exception as e:
            return CompileResult(
                success=False,
                errors=[CompileError(file="", line=0, message=f"Compilation error: {e}", kind=ErrorKind.UNKNOWN)],
            )

    # ------------------------------------------------------------------
    # Error parsing
    # ------------------------------------------------------------------

    def _parse_errors(self, stderr: str) -> list[CompileError]:
        """Parse iverilog stderr to extract structured errors."""
        errors = []

        # iverilog error format: filename:line: error: message
        error_pattern = re.compile(
            r"([^:]+):(\d+):\s*(?:error|syntax error):\s*(.*)", re.IGNORECASE
        )

        for match in error_pattern.finditer(stderr):
            file = match.group(1).strip()
            line = int(match.group(2))
            message = match.group(3).strip()
            kind = self._classify_error(message)

            errors.append(CompileError(
                file=file,
                line=line,
                message=message,
                kind=kind,
                raw_text=match.group(0),
            ))

        # Also catch general errors without line numbers
        general_pattern = re.compile(r"(?:error|Error):\s*(.*)")
        for match in general_pattern.finditer(stderr):
            msg = match.group(1).strip()
            # Skip if already captured above
            if not any(msg in e.message for e in errors):
                errors.append(CompileError(
                    file="", line=0, message=msg,
                    kind=self._classify_error(msg),
                ))

        return errors

    def _parse_warnings(self, stderr: str) -> list[str]:
        """Parse compilation warnings."""
        warnings = []
        pattern = re.compile(r"[^:]+:\d+:\s*warning:\s*(.*)", re.IGNORECASE)
        for match in pattern.finditer(stderr):
            warnings.append(match.group(1).strip())
        return warnings

    def _classify_error(self, message: str) -> ErrorKind:
        """Classify a compiler error message."""
        msg_lower = message.lower()

        if "syntax error" in msg_lower or "unexpected" in msg_lower:
            return ErrorKind.SYNTAX
        elif "unable to find" in msg_lower or "unknown module" in msg_lower:
            return ErrorKind.MISSING_MODULE
        elif "not declared" in msg_lower or "undefined" in msg_lower or "undeclared" in msg_lower:
            return ErrorKind.UNDECLARED_SIGNAL
        elif "type mismatch" in msg_lower or "incompatible" in msg_lower:
            return ErrorKind.TYPE_MISMATCH
        elif "port" in msg_lower and ("mismatch" in msg_lower or "missing" in msg_lower):
            return ErrorKind.PORT_MISMATCH
        elif "already defined" in msg_lower or "duplicate" in msg_lower:
            return ErrorKind.DUPLICATE
        return ErrorKind.UNKNOWN

    # ------------------------------------------------------------------
    # Stub generation
    # ------------------------------------------------------------------

    def generate_stubs(
        self,
        needed_modules: list[str],
        needed_interfaces: list[str] | None = None,
    ) -> str:
        """Generate stub module/interface definitions for dependencies
        that haven't been generated yet.

        This allows incremental compilation of files that reference
        not-yet-generated dependencies.
        """
        stubs = ["`timescale 1ns/1ps", ""]

        for module_name in needed_modules:
            stubs.append(f"// Stub for {module_name}")
            stubs.append(f"module {module_name}();")
            stubs.append(f"endmodule")
            stubs.append("")

        if needed_interfaces:
            for iface_name in needed_interfaces:
                stubs.append(f"// Stub for {iface_name}")
                stubs.append(f"interface {iface_name}(input logic clk);")
                stubs.append(f"endinterface")
                stubs.append("")

        return "\n".join(stubs)

    # ------------------------------------------------------------------
    # Smart error prompt for LLM retry
    # ------------------------------------------------------------------

    def format_errors_for_llm(self, result: CompileResult) -> str:
        """Format compilation errors for inclusion in an LLM retry prompt.

        Returns a structured error block that tells the LLM exactly what went wrong.
        """
        if result.success:
            return ""

        lines = [
            "COMPILATION FAILED. Fix these errors:",
            "",
        ]

        for i, err in enumerate(result.errors[:10], 1):
            kind_hint = {
                ErrorKind.SYNTAX: "Fix the syntax at this line.",
                ErrorKind.MISSING_MODULE: "This module is not defined. Check the name spelling.",
                ErrorKind.UNDECLARED_SIGNAL: "This signal is not declared. Check if it exists in the interface/module.",
                ErrorKind.TYPE_MISMATCH: "Type mismatch. Check port widths and types.",
                ErrorKind.PORT_MISMATCH: "Port connection error. Check the port list.",
                ErrorKind.DUPLICATE: "Duplicate definition. Remove the duplicate.",
            }.get(err.kind, "Fix this error.")

            lines.append(f"Error {i} (line {err.line}): {err.message}")
            lines.append(f"  -> Hint: {kind_hint}")
            lines.append("")

        return "\n".join(lines)
