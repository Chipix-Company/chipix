"""
Context Manager — Smart Context Selection for AI Analysis.

Instead of merging all RTL files into one massive blob,
this module builds a lightweight structural index and
provides focused, budget-aware context packages.

Strategy (inspired by Claude Code / Cursor):
  1. STRUCTURAL INDEXING — Parse every file locally (no AI)
     to extract modules, ports, instantiations, parameters.
  2. FOCUSED RETRIEVAL — Only include code the AI actually needs.
  3. MULTI-PASS — Summary first, then deep-dive per module.
  4. TOKEN BUDGETING — Stay within limits, prioritize by relevance.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from core.logger import get_logger

logger = get_logger("ContextManager")


# ═══════════════════════════════════════════════════════════════════════
# Data Structures
# ═══════════════════════════════════════════════════════════════════════

@dataclass
class ModuleIndex:
    """Lightweight structural info for one module (no AI needed)."""
    name: str
    file_path: Path
    ports: dict[str, dict] = field(default_factory=dict)  # name → {dir, width, bus_range}
    parameters: dict[str, str] = field(default_factory=dict)
    instantiations: list[dict] = field(default_factory=list)  # [{module, instance}]
    line_count: int = 0
    has_fsm: bool = False
    start_line: int = 0
    end_line: int = 0
    raw_code: str = ""

    @property
    def input_ports(self) -> list[str]:
        return [n for n, p in self.ports.items() if p.get("dir") == "input"]

    @property
    def output_ports(self) -> list[str]:
        return [n for n, p in self.ports.items() if p.get("dir") == "output"]

    def interface_summary(self) -> str:
        """Compact port-only view (no body code)."""
        lines = [f"module {self.name} ("]
        port_strs = []
        for pname, pinfo in self.ports.items():
            d = pinfo.get("dir", "input")
            w = pinfo.get("bus_range", "")
            if w:
                port_strs.append(f"  {d} {w} {pname}")
            else:
                port_strs.append(f"  {d} {pname}")
        lines.append(",\n".join(port_strs))
        lines.append(f"); // {self.line_count} lines, {len(self.instantiations)} sub-instances")
        lines.append("endmodule")
        return "\n".join(lines)


@dataclass
class DesignIndex:
    """Index of the entire RTL project."""
    modules: dict[str, ModuleIndex] = field(default_factory=dict)
    top_module: str = ""
    files: list[Path] = field(default_factory=list)
    defines: dict[str, str] = field(default_factory=dict)
    include_files: list[Path] = field(default_factory=list)
    total_lines: int = 0


# ═══════════════════════════════════════════════════════════════════════
# Context Manager
# ═══════════════════════════════════════════════════════════════════════

class ContextManager:
    """Smart context selection for AI analysis.

    Instead of sending 50,000 lines to the AI, this class:
    1. Builds a structural index locally (regex, no AI)
    2. Provides focused context packages per task
    3. Manages token budgets
    4. Prioritizes modules by relevance

    Usage:
        cm = ContextManager()
        cm.index_project(rtl_files)

        # For RTL analysis — get focused context
        ctx = cm.get_analysis_context("AHB_APB_Bridge")

        # For UVM generation — per-file tailored context
        ctx = cm.get_generation_context("driver", "AHB_APB_Bridge")
    """

    # Approximate tokens per character (conservative)
    CHARS_PER_TOKEN = 3.5
    DEFAULT_BUDGET = 80_000  # tokens

    def __init__(self, token_budget: int | None = None) -> None:
        self.token_budget = token_budget or self.DEFAULT_BUDGET
        self.index = DesignIndex()
        self._file_cache: dict[str, str] = {}  # path → content

    # ─────────────────────────────────────────────────────────────────
    # Step 1: Build structural index (LOCAL, no AI)
    # ─────────────────────────────────────────────────────────────────

    def index_project(self, files: list[Path]) -> DesignIndex:
        """Scan all RTL files and build a searchable index.

        This is FAST and LOCAL — no AI calls, just regex parsing.
        Inspired by how Cursor uses Tree-sitter to index codebases.
        """
        self.index = DesignIndex(files=list(files))

        for f in files:
            try:
                code = f.read_text(encoding="utf-8", errors="replace")
                self._file_cache[str(f)] = code
                self._index_file(f, code)
            except Exception as e:
                logger.warning("Failed to index %s: %s", f.name, e)

        # Determine top module
        self._find_top_module()

        # Count totals
        self.index.total_lines = sum(m.line_count for m in self.index.modules.values())

        logger.info(
            "Indexed %d files → %d modules, top=%s, %d total lines",
            len(files), len(self.index.modules),
            self.index.top_module, self.index.total_lines,
        )
        return self.index

    def _index_file(self, path: Path, code: str) -> None:
        """Extract structural info from one file."""
        # Track includes
        for inc in re.finditer(r'`include\s+"([^"]+)"', code):
            self.index.include_files.append(Path(inc.group(1)))

        # Track defines
        for dfn in re.finditer(r'`define\s+(\w+)\s+(.*?)$', code, re.MULTILINE):
            self.index.defines[dfn.group(1)] = dfn.group(2).strip()

        # Find all modules
        mod_pat = re.compile(
            r"module\s+(\w+)\s*(?:#\s*\(.*?\))?\s*\((.*?)\)\s*;",
            re.DOTALL,
        )
        for match in mod_pat.finditer(code):
            name = match.group(1)
            ports_raw = match.group(2)
            start_line = code[:match.start()].count("\n") + 1

            # Find endmodule
            endmod = re.search(r"\bendmodule\b", code[match.end():])
            if endmod:
                body = code[match.end():match.end() + endmod.start()]
                end_line = start_line + code[match.start():match.end() + endmod.end()].count("\n")
                full_code = code[match.start():match.end() + endmod.end()]
            else:
                body = code[match.end():]
                end_line = start_line + body.count("\n")
                full_code = code[match.start():]

            # Parse ports (ANSI + V95)
            ports = self._parse_ports(ports_raw, body)

            # Parse parameters
            params = {}
            for pm in re.finditer(r"\bparameter\s+(?:\w+\s+)?(\w+)\s*=\s*([^;,\)]+)", full_code):
                params[pm.group(1)] = pm.group(2).strip()

            # Find instantiations
            kw = {"module","endmodule","function","task","always","always_ff",
                  "always_comb","initial","assign","if","else","begin","end",
                  "case","for","while","generate","wire","reg","logic","input",
                  "output","inout","parameter","localparam","integer","real","time"}
            instances = []
            for im in re.finditer(r"^\s*(\w+)\s+(?:#\s*\(.*?\)\s+)?(\w+)\s*\(", body, re.MULTILINE):
                if im.group(1).lower() not in kw:
                    instances.append({"module": im.group(1), "instance": im.group(2)})

            # Detect FSM
            has_fsm = bool(re.search(
                r"typedef\s+enum|parameter\s+\w*(IDLE|STATE|INIT)\w*\s*=", full_code, re.IGNORECASE
            ))

            self.index.modules[name] = ModuleIndex(
                name=name,
                file_path=path,
                ports=ports,
                parameters=params,
                instantiations=instances,
                line_count=end_line - start_line + 1,
                has_fsm=has_fsm,
                start_line=start_line,
                end_line=end_line,
                raw_code=full_code,
            )

    def _parse_ports(self, ports_raw: str, body: str) -> dict[str, dict]:
        """Parse ports from both ANSI and V95 style declarations."""
        ports = {}

        # Try ANSI style first
        for pm in re.finditer(
            r"(input|output|inout)\s+(?:(wire|reg|logic)\s+)?(?:(\[[^\]]+\])\s*)?(\w+)",
            ports_raw
        ):
            ports[pm.group(4)] = {
                "dir": pm.group(1),
                "type": pm.group(2) or "logic",
                "bus_range": pm.group(3) or "",
            }

        # V95 fallback
        if not ports:
            for pm in re.finditer(
                r"^\s*(input|output|inout)\s+(?:(?:reg|wire|logic|integer)\s+)?(?:(\[[^\]]+\])\s*)?([^;]+);",
                body, re.MULTILINE | re.IGNORECASE
            ):
                bus = pm.group(2) or ""
                for pn in pm.group(3).split(","):
                    pn = pn.strip()
                    if pn and re.match(r"^\w+$", pn):
                        ports[pn] = {"dir": pm.group(1), "type": "wire", "bus_range": bus}

        return ports

    def _find_top_module(self) -> None:
        """Top module = module that's never instantiated by others."""
        instantiated = set()
        for mod in self.index.modules.values():
            for inst in mod.instantiations:
                instantiated.add(inst["module"])

        candidates = [n for n in self.index.modules if n not in instantiated]
        if candidates:
            # Prefer the one with most sub-instances
            self.index.top_module = max(
                candidates,
                key=lambda n: len(self.index.modules[n].instantiations)
            )
        elif self.index.modules:
            self.index.top_module = next(iter(self.index.modules))

    # ─────────────────────────────────────────────────────────────────
    # DUT Identification — Smart Target Selection
    # ─────────────────────────────────────────────────────────────────

    def identify_dut(
        self,
        spec_module_name: str | None = None,
        user_hint: str | None = None,
    ) -> str:
        """Decide which module is the DUT (Design Under Test).

        Uses 4 strategies in priority order:

        Strategy 1: USER HINT
            If user explicitly says "verify AHB_APB_Bridge", use that.

        Strategy 2: SPEC NAME MATCH
            If the spec document names a module (e.g. "AHB_APB_Bridge"),
            match it against the index.

        Strategy 3: STRUCTURAL HEURISTICS
            Score each module on "verification interest":
            - High port count → more complex interface → more to verify
            - Has FSM → stateful logic → critical to test
            - Protocol signals → protocol compliance to check
            - Mid-hierarchy depth → leaf modules too simple,
              top wrappers too thin
            - High line count → more logic → more bugs possible

        Strategy 4: FALLBACK
            Use the top module.

        Returns:
            Module name to use as DUT.
        """
        modules = self.index.modules

        if not modules:
            return ""

        # ── Strategy 1: User Hint ─────────────────────────────────
        if user_hint:
            # Fuzzy match: user might say "bridge" → match "AHB_APB_Bridge"
            hint_lower = user_hint.lower()
            for name in modules:
                if name.lower() == hint_lower:
                    logger.info("DUT (user hint, exact): %s", name)
                    return name
            # Partial match
            for name in modules:
                if hint_lower in name.lower() or name.lower() in hint_lower:
                    logger.info("DUT (user hint, partial): %s", name)
                    return name

        # ── Strategy 2: Spec Name Match ───────────────────────────
        if spec_module_name:
            spec_lower = spec_module_name.lower()
            for name in modules:
                if name.lower() == spec_lower:
                    logger.info("DUT (spec match): %s", name)
                    return name
            # Partial match (spec says "Bridge", module is "AHB_APB_Bridge")
            for name in modules:
                if spec_lower in name.lower() or name.lower() in spec_lower:
                    logger.info("DUT (spec partial match): %s", name)
                    return name

        # ── Strategy 3: Structural Heuristics ─────────────────────
        # Score every module and pick the "most interesting" one.
        if len(modules) == 1:
            only = next(iter(modules))
            logger.info("DUT (only module): %s", only)
            return only

        scores: dict[str, float] = {}
        max_lines = max(m.line_count for m in modules.values()) or 1
        max_ports = max(len(m.ports) for m in modules.values()) or 1

        for name, mod in modules.items():
            score = 0.0

            # Port complexity (0–25 pts)
            # More ports = more complex interface = more to verify
            port_ratio = len(mod.ports) / max_ports
            score += port_ratio * 25

            # Code complexity (0–25 pts)
            # More lines = more logic = more potential bugs
            line_ratio = mod.line_count / max_lines
            score += line_ratio * 25

            # FSM bonus (+20 pts)
            # Stateful modules are the most important to verify
            if mod.has_fsm:
                score += 20

            # Protocol signal bonus (+15 pts)
            # Protocol modules have compliance requirements
            proto_signals = sum(
                1 for sig in mod.ports
                if any(p in sig.upper() for p in
                       ["AXI", "AHB", "APB", "SPI", "I2C", "UART",
                        "VALID", "READY", "GRANT", "REQ", "ACK",
                        "PSEL", "PENABLE", "HREADY", "HTRANS"])
            )
            if proto_signals > 0:
                score += min(proto_signals * 3, 15)

            # Hierarchy position scoring
            # Penalize pure wrappers (lots of instances, few own lines)
            own_logic_ratio = mod.line_count / max(1, len(mod.instantiations) * 20 + mod.line_count)
            if own_logic_ratio < 0.3 and len(mod.instantiations) > 2:
                # Likely a thin wrapper — low verification value
                score *= 0.4
                score -= 10

            # Bonus for mid-hierarchy modules (depth 1-2 from top)
            # They have the most interesting local logic
            depth_from_top = self._depth_from_top(name)
            if depth_from_top == 1:
                score += 10  # Direct sub-module of top
            elif depth_from_top == 0 and len(modules) > 2:
                score -= 5   # Top module in multi-module project (often wrapper)

            # Penalize leaf modules with very few lines
            if not mod.instantiations and mod.line_count < 15:
                score -= 10  # Too simple to be interesting DUT

            # Parameter count bonus (configurable modules)
            if mod.parameters:
                score += min(len(mod.parameters) * 2, 8)

            scores[name] = score

        # Pick highest scoring module
        best = max(scores, key=lambda n: scores[n])

        # Log the ranking
        ranked = sorted(scores.items(), key=lambda x: -x[1])
        logger.info("DUT scoring:")
        for name, sc in ranked[:5]:
            marker = " <-- SELECTED" if name == best else ""
            logger.info("  %6.1f  %s%s", sc, name, marker)

        logger.info("DUT (heuristic): %s (score=%.1f)", best, scores[best])
        return best

    def _depth_from_top(self, target: str) -> int:
        """How many levels deep is this module from the top?"""
        if target == self.index.top_module:
            return 0

        def _find(current: str, depth: int) -> int:
            mod = self.index.modules.get(current)
            if not mod:
                return -1
            for inst in mod.instantiations:
                if inst["module"] == target:
                    return depth + 1
                found = _find(inst["module"], depth + 1)
                if found >= 0:
                    return found
            return -1

        result = _find(self.index.top_module, 0)
        return result if result >= 0 else 999

    # ─────────────────────────────────────────────────────────────────
    # Step 2: Generate context packages (FOCUSED, budget-aware)
    # ─────────────────────────────────────────────────────────────────

    def get_design_summary(self) -> str:
        """Layer 1: Compact architecture summary (~200 lines).

        Always included in every AI call. Gives the AI a map
        of the entire design without sending full source code.
        """
        lines = []
        lines.append(f"// ═══ DESIGN SUMMARY ═══")
        lines.append(f"// Project: {len(self.index.files)} files, "
                      f"{len(self.index.modules)} modules, "
                      f"{self.index.total_lines} total lines")
        lines.append(f"// Top Module: {self.index.top_module}")
        lines.append("")

        # Hierarchy tree
        lines.append("// ── Module Hierarchy ──")
        self._build_tree(self.index.top_module, lines, indent=0, visited=set())
        lines.append("")

        # All module interfaces (ports only)
        lines.append("// ── Module Interfaces ──")
        for name, mod in self.index.modules.items():
            lines.append(mod.interface_summary())
            lines.append("")

        # Parameters
        if self.index.defines:
            lines.append("// ── Global Defines ──")
            for k, v in list(self.index.defines.items())[:30]:
                lines.append(f"`define {k} {v}")
            lines.append("")

        return "\n".join(lines)

    def _build_tree(self, module: str, lines: list, indent: int, visited: set) -> None:
        """Recursively build an ASCII hierarchy tree."""
        if module in visited:
            lines.append("  " * indent + f"├── {module} (circular ref)")
            return
        visited.add(module)

        mod = self.index.modules.get(module)
        if not mod:
            lines.append("  " * indent + f"├── {module} (external)")
            return

        prefix = "  " * indent + ("└── " if indent > 0 else "")
        fsm = " [FSM]" if mod.has_fsm else ""
        lines.append(f"{prefix}{module} ({mod.line_count}L, "
                      f"{len(mod.input_ports)}in/{len(mod.output_ports)}out){fsm}")

        for inst in mod.instantiations:
            self._build_tree(inst["module"], lines, indent + 1, visited.copy())

    def get_module_code(self, module_name: str) -> str:
        """Get the full source code for a specific module."""
        mod = self.index.modules.get(module_name)
        if not mod:
            return f"// Module '{module_name}' not found in index"
        return mod.raw_code

    def get_analysis_context(self, target_module: str | None = None) -> str:
        """Complete context package for RTL analysis.

        5-layer focused context (never merge-all):
          Layer 1: Design summary (hierarchy + all interfaces)
          Layer 2: DUT module (full code) - the verification target
          Layer 3: PARENT module (full code) - shows how DUT is wired
          Layer 4: Sub-modules of DUT (full if budget, else interface)
          Layer 5: Connected/sibling modules + includes
        """
        target = target_module or self.index.top_module
        mod = self.index.modules.get(target)

        parts = []
        budget_chars = int(self.token_budget * self.CHARS_PER_TOKEN)
        used = 0
        included_modules = set()  # track what we've already added

        # Layer 1: Design summary (always included)
        summary = self.get_design_summary()
        parts.append(summary)
        used += len(summary)

        # Layer 2: DUT full code
        if mod:
            header = f"\n// {'='*60}\n// TARGET DUT: {target} (FULL CODE)\n// {'='*60}\n"
            parts.append(header + mod.raw_code)
            used += len(mod.raw_code) + len(header)
            included_modules.add(target)

        # Layer 3: Parent module full code (SoC_Top or whoever instantiates DUT)
        parent = self._find_parent(target)
        if parent and parent != target and parent not in included_modules:
            parent_mod = self.index.modules.get(parent)
            if parent_mod:
                header = (f"\n// {'='*60}\n"
                          f"// PARENT: {parent} (FULL CODE - shows DUT instantiation & wiring)\n"
                          f"// {'='*60}\n")
                parts.append(header + parent_mod.raw_code)
                used += len(parent_mod.raw_code) + len(header)
                included_modules.add(parent)
                logger.info("Context includes parent: %s (%dL)", parent, parent_mod.line_count)

        # Layer 4: Direct sub-modules of DUT
        if mod:
            for inst in mod.instantiations:
                sub_name = inst["module"]
                if sub_name in included_modules:
                    continue
                sub = self.index.modules.get(sub_name)
                if not sub:
                    continue

                sub_code = sub.raw_code
                if used + len(sub_code) < budget_chars * 0.7:
                    header = f"\n// --- SUB-MODULE: {sub.name} (FULL CODE) ---\n"
                    parts.append(header + sub_code)
                    used += len(sub_code) + len(header)
                else:
                    intf = sub.interface_summary()
                    header = f"\n// --- SUB-MODULE: {sub.name} (INTERFACE ONLY) ---\n"
                    parts.append(header + intf)
                    used += len(intf) + len(header)
                included_modules.add(sub_name)

        # Layer 5: Sibling modules connected to DUT (share signals via parent)
        siblings = self._find_connected_modules(target)
        for sib_name in siblings:
            if sib_name in included_modules:
                continue
            sib = self.index.modules.get(sib_name)
            if not sib:
                continue
            if used + len(sib.raw_code) < budget_chars * 0.85:
                header = f"\n// --- CONNECTED: {sib.name} (sibling sharing signals with DUT) ---\n"
                parts.append(header + sib.raw_code)
                used += len(sib.raw_code) + len(header)
            else:
                intf = sib.interface_summary()
                header = f"\n// --- CONNECTED: {sib.name} (INTERFACE ONLY) ---\n"
                parts.append(header + intf)
                used += len(intf) + len(header)
            included_modules.add(sib_name)

        # Layer 6: Include files / shared defines
        for inc_path in self.index.include_files:
            for f in self.index.files:
                if f.name == inc_path.name:
                    inc_code = self._file_cache.get(str(f), "")
                    if inc_code and used + len(inc_code) < budget_chars * 0.9:
                        header = f"\n// --- INCLUDE: {f.name} ---\n"
                        parts.append(header + inc_code)
                        used += len(inc_code) + len(header)
                    break

        tokens_est = int(used / self.CHARS_PER_TOKEN)
        logger.info(
            "Context for '%s': %d modules included, %d chars ~ %dK tokens "
            "(budget: %dK, %.0f%% used)",
            target, len(included_modules), used, tokens_est // 1000,
            self.token_budget // 1000, (tokens_est / self.token_budget) * 100,
        )
        return "\n".join(parts)

    def _find_parent(self, module_name: str) -> str | None:
        """Find which module instantiates the given module."""
        for name, mod in self.index.modules.items():
            for inst in mod.instantiations:
                if inst["module"] == module_name:
                    return name
        return None

    def _find_connected_modules(self, module_name: str) -> list[str]:
        """Find sibling modules that share connections with the target.

        If SoC_Top instantiates both AHB_APB_Bridge and Memory_Controller,
        and they share wire connections, Memory_Controller is a 'connected
        sibling' of AHB_APB_Bridge — important for integration context.
        """
        parent = self._find_parent(module_name)
        if not parent:
            return []

        parent_mod = self.index.modules.get(parent)
        if not parent_mod:
            return []

        # All siblings = everything the parent instantiates except the target
        siblings = [
            inst["module"] for inst in parent_mod.instantiations
            if inst["module"] != module_name
            and inst["module"] in self.index.modules
        ]
        return siblings

    def get_generation_context(
        self, file_type: str, target_module: str | None = None,
    ) -> str:
        """Tailored context for generating a specific UVM file.

        Different files need different context:
          • driver → DUT interface + transaction types + protocol rules
          • scoreboard → DUT behavior + expected outputs
          • coverage → port map + requirements + protocol spec
          • assertions → FSM states + timing relationships
        """
        target = target_module or self.index.top_module
        mod = self.index.modules.get(target)

        parts = []

        # Always include: module interface + summary
        summary = self.get_design_summary()
        parts.append(summary)

        if mod:
            parts.append(f"\n// ═══ DUT MODULE (FULL) ═══\n{mod.raw_code}")

        # File-type specific additions
        if file_type in ("driver", "monitor", "interface", "seq_item"):
            # Need: port details, protocol info, timing
            parts.append(self._get_protocol_context(target))

        elif file_type in ("scoreboard", "checker"):
            # Need: functional behavior, expected I/O relationships
            if mod:
                for inst in mod.instantiations:
                    sub = self.index.modules.get(inst["module"])
                    if sub:
                        parts.append(f"\n// Sub-module behavior:\n{sub.raw_code}")

        elif file_type in ("coverage", "assertions"):
            # Need: port map, FSM states, all signals
            parts.append(self._get_signal_summary(target))

        elif file_type in ("test", "sequence"):
            # Need: interface + reset + clock info
            pass

        return "\n".join(parts)

    def _get_protocol_context(self, module: str) -> str:
        """Protocol-specific context (AXI/APB/AHB handshake rules)."""
        mod = self.index.modules.get(module)
        if not mod:
            return ""

        signals = list(mod.ports.keys())
        ctx = ["\n// ═══ PROTOCOL SIGNALS ═══"]
        for sig in signals:
            upper = sig.upper()
            if any(proto in upper for proto in ["AXI", "AHB", "APB", "SPI", "I2C", "UART"]):
                pinfo = mod.ports[sig]
                ctx.append(f"// {pinfo.get('dir','?'):6s} {pinfo.get('bus_range',''):10s} {sig}")
        return "\n".join(ctx)

    def _get_signal_summary(self, module: str) -> str:
        """All signals for coverage/assertion context."""
        mod = self.index.modules.get(module)
        if not mod:
            return ""

        ctx = ["\n// ═══ SIGNAL SUMMARY ═══"]
        for pn, pi in mod.ports.items():
            ctx.append(f"// {pi.get('dir','?'):6s} {pi.get('bus_range',''):10s} {pn}")
        if mod.has_fsm:
            ctx.append("// NOTE: FSM detected — include state coverage")
        return "\n".join(ctx)

    # ─────────────────────────────────────────────────────────────────
    # Step 3: Search & Query
    # ─────────────────────────────────────────────────────────────────

    def search_signal(self, signal_name: str) -> list[dict]:
        """Find which modules use a given signal."""
        results = []
        for name, mod in self.index.modules.items():
            if signal_name in mod.ports:
                results.append({
                    "module": name,
                    "usage": "port",
                    "info": mod.ports[signal_name],
                })
            # Also check in raw code for internal usage
            if signal_name in mod.raw_code:
                count = mod.raw_code.count(signal_name)
                results.append({
                    "module": name,
                    "usage": "internal",
                    "occurrences": count,
                })
        return results

    def get_hierarchy_depth(self, module: str | None = None, visited: set | None = None) -> int:
        """Get the maximum depth of the instantiation hierarchy."""
        module = module or self.index.top_module
        visited = visited or set()
        if module in visited or module not in self.index.modules:
            return 0
        visited.add(module)
        mod = self.index.modules[module]
        if not mod.instantiations:
            return 1
        return 1 + max(
            self.get_hierarchy_depth(inst["module"], visited.copy())
            for inst in mod.instantiations
        )

    def get_all_modules_sorted(self) -> list[str]:
        """Return modules sorted by analysis priority.

        Priority: top module first, then by depth, then by complexity.
        """
        top = self.index.top_module
        modules = list(self.index.modules.keys())

        def priority(name: str) -> tuple:
            mod = self.index.modules[name]
            is_top = 0 if name == top else 1
            depth = -self.get_hierarchy_depth(name)
            complexity = -(mod.line_count + len(mod.instantiations) * 50)
            return (is_top, depth, complexity)

        return sorted(modules, key=priority)
