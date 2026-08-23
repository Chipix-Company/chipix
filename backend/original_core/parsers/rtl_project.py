"""
Multi-File RTL Project Ingestion.

Handles large SoC designs with 100+ files:
  • Recursive directory scanning for .v/.sv/.svh files
  • `include directive resolution
  • `define macro tracking
  • Module hierarchy building (parent → child tree)
  • Topological sort for correct compile order
  • Top module detection (module never instantiated)
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from core.logger import get_logger
from parsers.rtl_parser import parse_rtl

logger = get_logger("RTLProject")


@dataclass
class RTLProject:
    """Represents a full RTL design project (potentially 100+ files)."""

    # Input
    source_files: list[Path] = field(default_factory=list)
    include_dirs: list[Path] = field(default_factory=list)

    # Parsed
    defines: dict[str, str] = field(default_factory=dict)
    module_to_file: dict[str, Path] = field(default_factory=dict)       # module_name → file
    file_to_modules: dict[str, list[str]] = field(default_factory=dict) # file → [modules]
    instantiation_map: dict[str, list[str]] = field(default_factory=dict)  # parent → [children]

    # Derived
    top_module: str = ""
    compile_order: list[Path] = field(default_factory=list)
    hierarchy_depth: int = 0

    @property
    def file_count(self) -> int:
        return len(self.source_files)

    @property
    def module_count(self) -> int:
        return len(self.module_to_file)

    def summary(self) -> str:
        return (
            f"RTLProject: {self.file_count} files, {self.module_count} modules, "
            f"top={self.top_module}, depth={self.hierarchy_depth}"
        )


class RTLProjectParser:
    """Parse an entire RTL project directory."""

    def __init__(self) -> None:
        pass

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    def parse_project(
        self,
        paths: list[str | Path],
        include_dirs: list[str | Path] | None = None,
    ) -> RTLProject:
        """Parse an RTL project from one or more paths.

        Args:
            paths: List of file or directory paths.
                   Directories are scanned recursively.
            include_dirs: Additional include search directories.

        Returns:
            RTLProject with full hierarchy and compile order.
        """
        project = RTLProject()

        if include_dirs:
            project.include_dirs = [Path(d) for d in include_dirs]

        # Step 1: Collect all source files
        for p in paths:
            p = Path(p)
            if p.is_file():
                if p.suffix.lower() in (".v", ".sv", ".svh"):
                    project.source_files.append(p)
            elif p.is_dir():
                project.source_files.extend(self._scan_directory(p))
                project.include_dirs.append(p)

        # Deduplicate
        project.source_files = list(dict.fromkeys(project.source_files))

        if not project.source_files:
            logger.warning("No RTL files found!")
            return project

        logger.info("Found %d RTL files", len(project.source_files))

        # Step 2: Parse each file for modules and instantiations
        for source_file in project.source_files:
            self._parse_file_structure(source_file, project)

        # Step 3: Resolve includes
        self._resolve_includes(project)

        # Step 4: Detect top module
        project.top_module = self._find_top_module(project)

        # Step 5: Build hierarchy depth
        project.hierarchy_depth = self._compute_hierarchy_depth(project)

        # Step 6: Compute compile order (topological sort)
        project.compile_order = self._topological_sort(project)

        logger.info("Project parsed: %s", project.summary())
        return project

    # ------------------------------------------------------------------
    # Directory scanning
    # ------------------------------------------------------------------

    def _scan_directory(self, directory: Path) -> list[Path]:
        """Recursively find all Verilog/SV files in a directory."""
        files = []
        for ext in ("*.v", "*.sv", "*.svh"):
            files.extend(directory.rglob(ext))

        # Sort for deterministic order
        files.sort()
        return files

    # ------------------------------------------------------------------
    # File-level parsing (lightweight — just modules + instantiations)
    # ------------------------------------------------------------------

    def _parse_file_structure(self, file_path: Path, project: RTLProject) -> None:
        """Parse a single file for module declarations and instantiations."""
        try:
            content = file_path.read_text(encoding="utf-8")
        except Exception as e:
            logger.warning("Could not read %s: %s", file_path, e)
            return

        # Find module declarations
        modules = re.findall(r"\bmodule\s+(\w+)", content)
        project.file_to_modules[str(file_path)] = modules
        for mod in modules:
            project.module_to_file[mod] = file_path

        # Find module instantiations
        for mod in modules:
            # Extract everything between module...endmodule
            pattern = re.compile(
                rf"\bmodule\s+{re.escape(mod)}\b.*?\bendmodule\b",
                re.DOTALL,
            )
            match = pattern.search(content)
            if not match:
                continue

            body = match.group(0)
            instantiations = self._find_instantiations(body)
            if instantiations:
                project.instantiation_map[mod] = instantiations

        # Find `define macros
        for match in re.finditer(r"`define\s+(\w+)\s+(.*?)$", content, re.MULTILINE):
            project.defines[match.group(1)] = match.group(2).strip()

    def _find_instantiations(self, module_body: str) -> list[str]:
        """Find module instantiations within a module body."""
        instances = []

        # Pattern: <module_name> [#(...)] <instance_name> (...)
        pattern = re.compile(
            r"^\s*(\w+)\s+(?:#\s*\(.*?\)\s+)?(\w+)\s*\(",
            re.MULTILINE,
        )

        keywords = {
            "module", "endmodule", "function", "endfunction", "task", "endtask",
            "always", "always_ff", "always_comb", "initial", "assign", "if",
            "else", "begin", "end", "case", "for", "while", "generate",
            "wire", "reg", "logic", "input", "output", "inout", "parameter",
            "localparam", "integer", "real", "time", "class", "endclass",
            "interface", "endinterface",
        }

        for match in pattern.finditer(module_body):
            module_name = match.group(1)
            if module_name.lower() not in keywords:
                instances.append(module_name)

        return list(set(instances))  # Deduplicate

    # ------------------------------------------------------------------
    # Include resolution
    # ------------------------------------------------------------------

    def _resolve_includes(self, project: RTLProject) -> None:
        """Resolve `include directives across all files."""
        for source_file in project.source_files:
            try:
                content = source_file.read_text(encoding="utf-8")
            except Exception:
                continue

            for match in re.finditer(r'`include\s+"([^"]+)"', content):
                include_name = match.group(1)
                resolved = self._find_include(include_name, source_file, project.include_dirs)
                if resolved and resolved not in project.source_files:
                    project.source_files.append(resolved)
                    logger.debug("Resolved include: %s → %s", include_name, resolved)

    def _find_include(
        self,
        filename: str,
        source_file: Path,
        include_dirs: list[Path],
    ) -> Path | None:
        """Resolve an include filename to an actual path."""
        # Check relative to source file
        candidate = source_file.parent / filename
        if candidate.exists():
            return candidate

        # Check include directories
        for inc_dir in include_dirs:
            candidate = inc_dir / filename
            if candidate.exists():
                return candidate

        logger.debug("Could not resolve include: %s", filename)
        return None

    # ------------------------------------------------------------------
    # Top module detection
    # ------------------------------------------------------------------

    def _find_top_module(self, project: RTLProject) -> str:
        """Find the top-level module (the one that's never instantiated)."""
        all_modules = set(project.module_to_file.keys())
        instantiated = set()

        for children in project.instantiation_map.values():
            instantiated.update(children)

        # Top modules = declared but never instantiated
        top_candidates = all_modules - instantiated

        if not top_candidates:
            # Fallback: first module found
            return next(iter(all_modules)) if all_modules else ""

        # If multiple, prefer the one with the most instantiations (likely the top)
        if len(top_candidates) == 1:
            return top_candidates.pop()

        # Heuristic: the module with the deepest hierarchy
        best = ""
        best_depth = 0
        for mod in top_candidates:
            depth = self._get_depth(mod, project.instantiation_map)
            if depth > best_depth:
                best = mod
                best_depth = depth

        return best or top_candidates.pop()

    def _get_depth(self, module: str, inst_map: dict[str, list[str]], visited: set | None = None) -> int:
        """Get the hierarchical depth of a module."""
        if visited is None:
            visited = set()
        if module in visited:
            return 0  # Circular (shouldn't happen in valid RTL)
        visited.add(module)

        children = inst_map.get(module, [])
        if not children:
            return 0
        return 1 + max(self._get_depth(c, inst_map, visited) for c in children)

    # ------------------------------------------------------------------
    # Hierarchy depth
    # ------------------------------------------------------------------

    def _compute_hierarchy_depth(self, project: RTLProject) -> int:
        """Compute the maximum hierarchy depth."""
        if not project.top_module:
            return 0
        return self._get_depth(project.top_module, project.instantiation_map)

    # ------------------------------------------------------------------
    # Topological sort (compile order)
    # ------------------------------------------------------------------

    def _topological_sort(self, project: RTLProject) -> list[Path]:
        """Sort files in correct compilation order (dependencies first)."""
        # Build file dependency graph
        file_deps: dict[str, set[str]] = {}

        for file_str, modules in project.file_to_modules.items():
            deps = set()
            for mod in modules:
                children = project.instantiation_map.get(mod, [])
                for child in children:
                    child_file = project.module_to_file.get(child)
                    if child_file and str(child_file) != file_str:
                        deps.add(str(child_file))
            file_deps[file_str] = deps

        # Kahn's algorithm for topological sort
        in_degree: dict[str, int] = {f: 0 for f in file_deps}
        for f, deps in file_deps.items():
            for dep in deps:
                if dep in in_degree:
                    in_degree[f] = in_degree.get(f, 0)  # ensure exists

        # Recalculate in-degrees
        in_degree = {f: 0 for f in file_deps}
        for f, deps in file_deps.items():
            for dep in deps:
                if dep not in in_degree:
                    in_degree[dep] = 0

        for f, deps in file_deps.items():
            for dep in deps:
                in_degree[f] = in_degree.get(f, 0) + 1

        # Start with files that have no dependencies
        queue = [f for f, d in in_degree.items() if d == 0]
        result = []

        while queue:
            f = queue.pop(0)
            result.append(f)
            # Find files that depend on f
            for other_f, deps in file_deps.items():
                if f in deps:
                    in_degree[other_f] -= 1
                    if in_degree[other_f] == 0:
                        queue.append(other_f)

        # Add any remaining files (circular deps or unlinked)
        for f in file_deps:
            if f not in result:
                result.append(f)

        # Add files not in the graph (e.g., header files)
        all_file_strs = [str(f) for f in project.source_files]
        for f in all_file_strs:
            if f not in result:
                result.insert(0, f)  # Headers go first

        return [Path(f) for f in result]


def parse_rtl_project(
    paths: list[str | Path],
    include_dirs: list[str | Path] | None = None,
) -> RTLProject:
    """Convenience function to parse an RTL project."""
    parser = RTLProjectParser()
    return parser.parse_project(paths, include_dirs)
