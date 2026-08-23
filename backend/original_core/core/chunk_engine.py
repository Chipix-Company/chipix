"""
Chunked Generation Engine — Write 5000+ Lines Without Errors.

Instead of generating entire files in one shot, breaks each file
into logical chunks (class header, tasks, methods), generates and
compiles each incrementally, then assembles into the final file.

Strategy:
  • Small files (< 300 lines): Single-shot generation (fast)
  • Medium files (300-1000 lines): 3-5 chunks
  • Large files (1000+ lines): 6-10 chunks
  • Each chunk is compiled incrementally after generation
  • Failed chunks are retried with compiler error feedback
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field

import config
from core.ai_client import AIClient
from core.compiler import Compiler, CompileResult
from core.logger import get_logger
from core.symbol_table import SymbolTable

logger = get_logger("ChunkEngine")


@dataclass
class ChunkSpec:
    """Specification for a single generation chunk."""
    chunk_id: int
    name: str                      # e.g., "class_header", "drive_task"
    description: str               # What this chunk should contain
    start_marker: str = ""         # Expected start of chunk (e.g., "class counter_driver")
    end_marker: str = ""           # Expected end of chunk (e.g., "endclass")
    estimated_lines: int = 50


@dataclass
class ChunkResult:
    """Result of generating a single chunk."""
    chunk_id: int
    name: str
    content: str
    compiled: bool = False
    compile_errors: list[str] = field(default_factory=list)
    attempts: int = 1


@dataclass
class ChunkedFileResult:
    """Result of generating a complete file via chunked generation."""
    filename: str
    content: str                   # Full assembled content
    chunks: list[ChunkResult] = field(default_factory=list)
    total_attempts: int = 0
    total_lines: int = 0
    compilation_passed: bool = False
    generation_time: float = 0.0


class ChunkEngine:
    """Engine for generating large files in small, compilable chunks."""

    def __init__(
        self,
        ai_client: AIClient,
        compiler: Compiler,
        symbol_table: SymbolTable,
    ) -> None:
        self.ai_client = ai_client
        self.compiler = compiler
        self.symbol_table = symbol_table

    # ------------------------------------------------------------------
    # Main entry: generate a file using chunked or single-shot
    # ------------------------------------------------------------------

    async def generate_file(
        self,
        filename: str,
        system_prompt: str,
        context_blocks: dict[str, str],
        focus_instruction: str,
        task_instruction: str,
        chunk_specs: list[ChunkSpec] | None = None,
        dependency_files: dict[str, str] | None = None,
    ) -> ChunkedFileResult:
        """Generate a file using chunked or single-shot strategy.

        If chunk_specs is provided → chunked generation.
        Otherwise → single-shot generation (current behavior).
        """
        start_time = time.time()

        if chunk_specs and len(chunk_specs) > 1:
            result = await self._generate_chunked(
                filename=filename,
                system_prompt=system_prompt,
                context_blocks=context_blocks,
                focus_instruction=focus_instruction,
                task_instruction=task_instruction,
                chunk_specs=chunk_specs,
                dependency_files=dependency_files,
            )
        else:
            result = await self._generate_single_shot(
                filename=filename,
                system_prompt=system_prompt,
                context_blocks=context_blocks,
                focus_instruction=focus_instruction,
                task_instruction=task_instruction,
                dependency_files=dependency_files,
            )

        result.generation_time = time.time() - start_time
        result.total_lines = len(result.content.splitlines())

        # Register new symbols from the generated file
        self.symbol_table.register_from_generated_file(filename, result.content)

        return result

    # ------------------------------------------------------------------
    # Single-shot generation (for small files)
    # ------------------------------------------------------------------

    async def _generate_single_shot(
        self,
        filename: str,
        system_prompt: str,
        context_blocks: dict[str, str],
        focus_instruction: str,
        task_instruction: str,
        dependency_files: dict[str, str] | None = None,
    ) -> ChunkedFileResult:
        """Generate a file in a single LLM call (for files < 300 lines)."""

        # Add symbol table constraint
        constraint = self.symbol_table.generate_constraint_block()
        if constraint:
            context_blocks["REGISTERED NAMES (use ONLY these)"] = constraint

        content = await self.ai_client.generate_with_context(
            system_prompt=system_prompt,
            context_blocks=context_blocks,
            focus_instruction=focus_instruction,
            task_instruction=task_instruction,
        )

        # Extract code from response
        content = self._extract_code(content, filename)

        # Compile check
        compile_result = self._compile_with_deps(content, filename, dependency_files)

        # If compilation fails, retry with error feedback
        attempts = 1
        while not compile_result.success and attempts < config.MAX_FILE_GEN_RETRIES:
            attempts += 1
            error_feedback = self.compiler.format_errors_for_llm(compile_result)

            context_blocks["COMPILATION ERRORS FROM PREVIOUS ATTEMPT"] = error_feedback

            content = await self.ai_client.generate_with_context(
                system_prompt=system_prompt,
                context_blocks=context_blocks,
                focus_instruction=focus_instruction,
                task_instruction=task_instruction,
                temperature=0.3,  # Lower temp on retry
            )
            content = self._extract_code(content, filename)
            compile_result = self._compile_with_deps(content, filename, dependency_files)

        chunk_result = ChunkResult(
            chunk_id=0, name="full_file", content=content,
            compiled=compile_result.success,
            compile_errors=[e.message for e in compile_result.errors],
            attempts=attempts,
        )

        return ChunkedFileResult(
            filename=filename,
            content=content,
            chunks=[chunk_result],
            total_attempts=attempts,
            compilation_passed=compile_result.success,
        )

    # ------------------------------------------------------------------
    # Chunked generation (for large files)
    # ------------------------------------------------------------------

    async def _generate_chunked(
        self,
        filename: str,
        system_prompt: str,
        context_blocks: dict[str, str],
        focus_instruction: str,
        task_instruction: str,
        chunk_specs: list[ChunkSpec],
        dependency_files: dict[str, str] | None = None,
    ) -> ChunkedFileResult:
        """Generate a file in multiple chunks, compiling after each."""

        logger.info("  Chunked generation: %d chunks for %s", len(chunk_specs), filename)

        generated_chunks: list[ChunkResult] = []
        accumulated_code = ""
        total_attempts = 0

        for i, spec in enumerate(chunk_specs):
            logger.info("    Chunk %d/%d: %s", i + 1, len(chunk_specs), spec.name)

            # Build chunk-specific prompt
            chunk_context = dict(context_blocks)  # Copy base context

            # Add symbol table
            constraint = self.symbol_table.generate_constraint_block()
            if constraint:
                chunk_context["REGISTERED NAMES"] = constraint

            # Add previously generated chunks as context
            if accumulated_code:
                chunk_context["CODE GENERATED SO FAR (continue from here)"] = accumulated_code

            # Chunk-specific task instruction
            chunk_task = (
                f"You are generating CHUNK {i + 1}/{len(chunk_specs)} of {filename}.\n"
                f"Chunk name: {spec.name}\n"
                f"Description: {spec.description}\n"
                f"\n"
                f"RULES:\n"
                f"1. Generate ONLY this chunk — do NOT repeat code from previous chunks.\n"
                f"2. Use EXACT names from the symbol table above.\n"
                f"3. This chunk should be ~{spec.estimated_lines} lines.\n"
            )

            if i == 0:
                chunk_task += "4. Start the file with `timescale and any imports.\n"
            if i == len(chunk_specs) - 1:
                chunk_task += "4. Close all open blocks (endclass, endmodule, etc.).\n"

            chunk_task += f"\nOriginal task: {task_instruction}"

            # Generate the chunk
            response = await self.ai_client.generate_with_context(
                system_prompt=system_prompt,
                context_blocks=chunk_context,
                focus_instruction=focus_instruction,
                task_instruction=chunk_task,
                temperature=0.5,
            )

            chunk_code = self._extract_code(response, filename)

            # Compile incrementally (accumulated + new chunk)
            test_code = accumulated_code + "\n" + chunk_code if accumulated_code else chunk_code
            compile_result = self._compile_with_deps(test_code, filename, dependency_files)

            attempts = 1
            while not compile_result.success and attempts < config.MAX_FILE_GEN_RETRIES:
                attempts += 1
                error_feedback = self.compiler.format_errors_for_llm(compile_result)
                chunk_context["COMPILE ERRORS"] = error_feedback

                response = await self.ai_client.generate_with_context(
                    system_prompt=system_prompt,
                    context_blocks=chunk_context,
                    focus_instruction=focus_instruction,
                    task_instruction=chunk_task,
                    temperature=0.3,
                )
                chunk_code = self._extract_code(response, filename)
                test_code = accumulated_code + "\n" + chunk_code if accumulated_code else chunk_code
                compile_result = self._compile_with_deps(test_code, filename, dependency_files)

            total_attempts += attempts
            accumulated_code = test_code

            # Register any new symbols from this chunk
            self.symbol_table.register_from_generated_file(filename, chunk_code)

            generated_chunks.append(ChunkResult(
                chunk_id=spec.chunk_id,
                name=spec.name,
                content=chunk_code,
                compiled=compile_result.success,
                compile_errors=[e.message for e in compile_result.errors],
                attempts=attempts,
            ))

        # Final full-file compile
        final_compile = self._compile_with_deps(accumulated_code, filename, dependency_files)

        return ChunkedFileResult(
            filename=filename,
            content=accumulated_code,
            chunks=generated_chunks,
            total_attempts=total_attempts,
            compilation_passed=final_compile.success,
        )

    # ------------------------------------------------------------------
    # Compilation helper
    # ------------------------------------------------------------------

    def _compile_with_deps(
        self,
        content: str,
        filename: str,
        dependency_files: dict[str, str] | None,
    ) -> CompileResult:
        """Compile content with dependency files and stubs."""

        # For non-SV files, skip compilation
        if not filename.endswith((".sv", ".v", ".svh")):
            return CompileResult(success=True)

        # Generate stubs for any unresolved modules/interfaces
        from pathlib import Path
        dep_paths = []

        if dependency_files:
            import tempfile
            tmpdir = tempfile.mkdtemp()
            for dep_name, dep_content in dependency_files.items():
                dep_path = Path(tmpdir) / dep_name
                dep_path.write_text(dep_content, encoding="utf-8")
                dep_paths.append(dep_path)

        return self.compiler.compile_content(
            content=content,
            filename=filename,
            additional_files=dep_paths if dep_paths else None,
        )

    # ------------------------------------------------------------------
    # Code extraction from LLM response
    # ------------------------------------------------------------------

    def _extract_code(self, response: str, filename: str) -> str:
        """Extract code from LLM response."""
        if filename.endswith(".md"):
            md_match = re.search(r"```(?:markdown|md)\s*\n(.*?)```", response, re.DOTALL)
            if md_match:
                return md_match.group(1).strip()
            return response.strip()

        if filename == "Makefile":
            mk_match = re.search(r"```(?:makefile|make)\s*\n(.*?)```", response, re.DOTALL)
            if mk_match:
                return mk_match.group(1).strip()

        # SystemVerilog code block
        sv_match = re.search(
            r"```(?:systemverilog|verilog|sv)[^\n]*\n(.*?)```",
            response, re.DOTALL | re.IGNORECASE,
        )
        if sv_match:
            return sv_match.group(1).strip()

        # Generic code block
        generic_match = re.search(r"```\w*\s*\n(.*?)```", response, re.DOTALL)
        if generic_match:
            return generic_match.group(1).strip()

        return response.strip()


# ═══════════════════════════════════════════════════════════════════════
# Chunk templates for common file types
# ═══════════════════════════════════════════════════════════════════════

def get_chunk_specs_for_file(filename: str, estimated_lines: int = 200) -> list[ChunkSpec] | None:
    """Return chunk specifications for a file, or None for single-shot."""

    # Small files → single-shot
    if estimated_lines < 300:
        return None

    # Chunk strategies per file type
    if filename.endswith("_driver.sv") or filename == "driver.sv":
        return [
            ChunkSpec(1, "class_header", "Package imports, class declaration, constructor, virtual interface", estimated_lines=50),
            ChunkSpec(2, "build_connect", "build_phase, connect_phase, configuration", estimated_lines=40),
            ChunkSpec(3, "run_phase", "run_phase with main driving loop", estimated_lines=60),
            ChunkSpec(4, "drive_tasks", "drive_transaction, drive_reset, and helper tasks", estimated_lines=100),
            ChunkSpec(5, "closing", "Utility methods, endclass", estimated_lines=30),
        ]

    elif filename.endswith("_monitor.sv") or filename == "monitor.sv":
        return [
            ChunkSpec(1, "class_header", "Imports, class declaration, constructor, analysis port", estimated_lines=50),
            ChunkSpec(2, "run_phase", "run_phase with sampling loop", estimated_lines=60),
            ChunkSpec(3, "collect_tasks", "collect_transaction, sample_signals tasks", estimated_lines=80),
            ChunkSpec(4, "closing", "Helper methods, endclass", estimated_lines=30),
        ]

    elif filename.endswith("_env.sv") or filename == "environment.sv":
        return [
            ChunkSpec(1, "class_header", "Imports, class declaration, component handles", estimated_lines=40),
            ChunkSpec(2, "build_phase", "build_phase — instantiate all components", estimated_lines=60),
            ChunkSpec(3, "connect_phase", "connect_phase — wire mailboxes, analysis ports", estimated_lines=50),
            ChunkSpec(4, "run_report", "run_phase, report_phase, endclass", estimated_lines=40),
        ]

    elif filename.endswith("tb.sv") or filename == "tb.sv":
        return [
            ChunkSpec(1, "header_interface", "Timescale, interface instantiation, signals", estimated_lines=40),
            ChunkSpec(2, "dut_instance", "DUT instantiation with port connections", estimated_lines=50),
            ChunkSpec(3, "clock_reset", "Clock generation, reset sequence", estimated_lines=30),
            ChunkSpec(4, "env_setup", "Environment instantiation, configuration", estimated_lines=40),
            ChunkSpec(5, "test_run", "Initial block: run tests, sequences, finish", estimated_lines=50),
            ChunkSpec(6, "closing", "Endmodule, assertions bind", estimated_lines=20),
        ]

    elif filename.endswith("_scoreboard.sv") or filename == "scoreboard.sv":
        return [
            ChunkSpec(1, "class_header", "Imports, class, constructor, mailbox", estimated_lines=40),
            ChunkSpec(2, "reference_model", "Reference model logic — compute expected outputs", estimated_lines=100),
            ChunkSpec(3, "comparison", "Compare actual vs expected, report PASS/FAIL", estimated_lines=60),
            ChunkSpec(4, "closing", "Summary report, endclass", estimated_lines=30),
        ]

    # Default: no chunking
    return None
