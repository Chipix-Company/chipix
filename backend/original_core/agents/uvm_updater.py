"""
Gap 3 — UVM Environment Updater.

Instead of generating UVM testbenches from scratch, this agent:
  1. Parses an EXISTING UVM environment
  2. Understands its structure (agent, driver, monitor, etc.)
  3. Generates ONLY the delta updates needed
  4. Shows diff preview before applying changes
  5. Maintains testbench integrity (no breaking changes)

Mirrors ChipStack's UVMAgent "update existing UVM" capability.
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field
from pathlib import Path

import config
from core.ai_client import AIClient
from core.logger import get_logger

logger = get_logger("UVMUpdater")


@dataclass
class UVMComponent:
    """A parsed UVM component from existing env."""
    name: str
    kind: str = ""         # env, agent, driver, monitor, scoreboard, seq_lib, etc.
    file_path: str = ""
    class_name: str = ""
    parent_class: str = ""
    methods: list[str] = field(default_factory=list)
    ports: list[str] = field(default_factory=list)
    line_count: int = 0


@dataclass
class UVMEnvironmentMap:
    """Map of an existing UVM environment."""
    root_dir: str = ""
    components: list[UVMComponent] = field(default_factory=list)
    env_class: str = ""
    top_module: str = ""

    @property
    def summary(self) -> str:
        kinds = {}
        for c in self.components:
            kinds[c.kind] = kinds.get(c.kind, 0) + 1
        kind_str = ", ".join(f"{k}:{v}" for k, v in kinds.items())
        return f"UVM Env: {len(self.components)} components ({kind_str})"


@dataclass
class UpdatePlan:
    """Plan for updating UVM environment."""
    updates: list[dict] = field(default_factory=list)
    # Each update: {file, component, change_type, description, before, after}

    @property
    def summary(self) -> str:
        return f"{len(self.updates)} planned updates"


class UVMUpdater:
    """Parse existing UVM environments and generate targeted updates."""

    def __init__(self, ai_client: AIClient) -> None:
        self.ai_client = ai_client

    # ------------------------------------------------------------------
    # 1. Parse existing UVM environment
    # ------------------------------------------------------------------

    def parse_environment(self, env_dir: str | Path) -> UVMEnvironmentMap:
        """Parse an existing UVM environment directory.

        Scans for .sv/.svh files and identifies UVM components.
        """
        env_dir = Path(env_dir)
        env_map = UVMEnvironmentMap(root_dir=str(env_dir))

        if not env_dir.exists():
            logger.warning("UVM environment directory not found: %s", env_dir)
            return env_map

        # Find all SV files
        sv_files = list(env_dir.rglob("*.sv")) + list(env_dir.rglob("*.svh"))

        for sv_file in sv_files:
            try:
                content = sv_file.read_text(encoding="utf-8", errors="replace")
                components = self._parse_sv_file(sv_file, content)
                env_map.components.extend(components)
            except Exception as e:
                logger.warning("Error parsing %s: %s", sv_file, e)

        # Identify env class
        for comp in env_map.components:
            if comp.kind == "env":
                env_map.env_class = comp.class_name
                break

        logger.info("Parsed UVM environment: %s", env_map.summary)
        return env_map

    def _parse_sv_file(self, filepath: Path, content: str) -> list[UVMComponent]:
        """Parse a single SV file for UVM components."""
        components = []

        # Find class declarations
        class_pattern = re.compile(
            r"class\s+(\w+)\s+extends\s+(\w+)",
            re.MULTILINE
        )

        for match in class_pattern.finditer(content):
            cls_name = match.group(1)
            parent = match.group(2)

            # Determine kind from parent class
            kind = self._classify_component(parent, cls_name)

            # Find methods
            methods = re.findall(r"(?:task|function)\s+\w+\s+(\w+)\s*\(", content)

            comp = UVMComponent(
                name=cls_name,
                kind=kind,
                file_path=str(filepath),
                class_name=cls_name,
                parent_class=parent,
                methods=methods,
                line_count=len(content.splitlines()),
            )
            components.append(comp)

        return components

    def _classify_component(self, parent: str, name: str) -> str:
        """Classify a UVM component by its parent class."""
        parent_lower = parent.lower()
        name_lower = name.lower()

        if "uvm_env" in parent_lower or "env" in name_lower:
            return "env"
        if "uvm_agent" in parent_lower or "agent" in name_lower:
            return "agent"
        if "uvm_driver" in parent_lower or "driver" in name_lower:
            return "driver"
        if "uvm_monitor" in parent_lower or "monitor" in name_lower:
            return "monitor"
        if "uvm_scoreboard" in parent_lower or "scoreboard" in name_lower:
            return "scoreboard"
        if "uvm_sequence_item" in parent_lower or "seq_item" in name_lower:
            return "seq_item"
        if "uvm_sequence" in parent_lower or "seq" in name_lower:
            return "sequence"
        if "uvm_test" in parent_lower or "test" in name_lower:
            return "test"
        if "uvm_subscriber" in parent_lower or "coverage" in name_lower:
            return "coverage"
        return "other"

    # ------------------------------------------------------------------
    # 2. Generate delta updates
    # ------------------------------------------------------------------

    async def plan_updates(
        self,
        env_map: UVMEnvironmentMap,
        change_request: str,
        rtl_code: str = "",
    ) -> UpdatePlan:
        """Generate an update plan based on a natural language change request.

        Args:
            env_map: Parsed existing environment
            change_request: What the user wants to change (natural language)
            rtl_code: Current RTL code for context

        Returns:
            UpdatePlan with list of targeted changes
        """
        logger.info("Planning UVM updates for: %s", change_request[:80])

        # Build environment summary for LLM
        env_summary = self._format_env_summary(env_map)

        prompt = f"""\
You are a UVM verification expert. An engineer wants to UPDATE an existing UVM environment.

EXISTING UVM ENVIRONMENT:
{env_summary}

CHANGE REQUEST (natural language):
{change_request}

RULES:
1. ONLY modify what is necessary — do NOT rewrite entire files
2. Preserve ALL existing functionality
3. For each change, specify:
   - FILE: which file to modify
   - COMPONENT: which class/module
   - CHANGE_TYPE: add_method | modify_method | add_field | add_coverpoint | add_sequence | add_test
   - DESCRIPTION: what the change does
   - CODE: the exact code to add or replace

OUTPUT: Return a JSON array of changes.
"""

        context = {"RTL CODE": rtl_code} if rtl_code else {}

        response = await self.ai_client.generate_with_context(
            system_prompt=prompt,
            context_blocks=context,
            focus_instruction="Plan targeted UVM updates",
            task_instruction=f"Process change request: {change_request}",
        )

        plan = self._parse_update_plan(response, env_map)
        logger.info("Update plan: %s", plan.summary)
        return plan

    # ------------------------------------------------------------------
    # 3. Apply updates with diff preview
    # ------------------------------------------------------------------

    def preview_diff(self, plan: UpdatePlan) -> str:
        """Generate a unified diff of all planned changes.

        Returns formatted diff string that user can review before applying.
        """
        diffs = []

        for update in plan.updates:
            filepath = update.get("file", "")
            if not filepath or not Path(filepath).exists():
                continue

            original = Path(filepath).read_text(encoding="utf-8")
            modified = self._apply_single_update(original, update)

            if original != modified:
                diff = difflib.unified_diff(
                    original.splitlines(keepends=True),
                    modified.splitlines(keepends=True),
                    fromfile=f"a/{Path(filepath).name}",
                    tofile=f"b/{Path(filepath).name}",
                    lineterm="",
                )
                diffs.append("".join(diff))

        return "\n\n".join(diffs)

    def apply_updates(self, plan: UpdatePlan) -> list[str]:
        """Apply all planned updates to files.

        Returns list of modified file paths.
        """
        modified_files = []

        for update in plan.updates:
            filepath = update.get("file", "")
            if not filepath or not Path(filepath).exists():
                continue

            original = Path(filepath).read_text(encoding="utf-8")
            modified = self._apply_single_update(original, update)

            if original != modified:
                Path(filepath).write_text(modified, encoding="utf-8")
                modified_files.append(filepath)
                logger.info("Updated: %s (%s)", Path(filepath).name, update.get("description", ""))

        return modified_files

    def _apply_single_update(self, content: str, update: dict) -> str:
        """Apply a single update to file content."""
        change_type = update.get("change_type", "")
        code = update.get("code", "")
        component = update.get("component", "")

        if change_type == "add_method":
            # Insert before endclass
            pattern = re.compile(
                rf"(class\s+{re.escape(component)}.*?)(endclass)",
                re.DOTALL,
            )
            match = pattern.search(content)
            if match:
                insertion_point = match.end(1)
                content = content[:insertion_point] + "\n" + code + "\n\n" + content[insertion_point:]

        elif change_type == "add_field":
            # Insert after class declaration
            pattern = re.compile(
                rf"(class\s+{re.escape(component)}\s+extends\s+\w+;)",
            )
            match = pattern.search(content)
            if match:
                insertion_point = match.end()
                content = content[:insertion_point] + "\n    " + code + "\n" + content[insertion_point:]

        elif change_type in ("add_coverpoint", "add_sequence", "add_test"):
            # Append before endclass
            endclass_pos = content.rfind("endclass")
            if endclass_pos > 0:
                content = content[:endclass_pos] + code + "\n\n" + content[endclass_pos:]

        elif change_type == "modify_method":
            # Replace method content (simplified — uses markers)
            old_code = update.get("before", "")
            if old_code and old_code in content:
                content = content.replace(old_code, code)

        return content

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _format_env_summary(self, env_map: UVMEnvironmentMap) -> str:
        lines = []
        for comp in env_map.components:
            methods_str = ", ".join(comp.methods[:8])
            lines.append(
                f"  {comp.kind:12} | {comp.class_name:30} | "
                f"extends {comp.parent_class} | methods: {methods_str} | "
                f"{comp.line_count} lines | {comp.file_path}"
            )
        return "\n".join(lines)

    def _parse_update_plan(self, response: str, env_map: UVMEnvironmentMap) -> UpdatePlan:
        """Parse LLM response into UpdatePlan."""
        import json
        plan = UpdatePlan()

        json_match = re.search(r"\[.*\]", response, re.DOTALL)
        if json_match:
            try:
                items = json.loads(json_match.group())
                plan.updates = items
                return plan
            except json.JSONDecodeError:
                pass

        # Fallback: treat entire response as a single update
        plan.updates.append({
            "file": "",
            "component": "",
            "change_type": "add_method",
            "description": "AI-generated update",
            "code": response,
        })
        return plan
