"""
Agent 2 — RTL Analyzer

Parses the uploaded Verilog/SystemVerilog RTL code to extract structural
and behavioral information.

Two-stage approach:
  1. Regex-based parsing (fast, deterministic) for structure
    2. AI runtime analysis (smart, contextual) for behavior/intent
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from core.ai_client import AIClient, AIClientError
from core.logger import get_logger
from core.models import (
    AlwaysBlock,
    ModuleInfo,
    PortDefinition,
    PortDirection,
    RTLAnalysis,
    SignalInfo,
)
from parsers.rtl_parser import parse_rtl

logger = get_logger("Agent2.RTLAnalyzer")

# ── System prompt for RTL analysis ────────────────────────────────────
SYSTEM_PROMPT = """\
You are an expert RTL design analyst. Identify RTL behavior and intent.
RULES:
1. Explain each always block's function.
2. Identify FSM states and transitions.
3. Identify clock domain crossings (CDC).
4. Identify verification challenges.
OUTPUT FORMAT (JSON ONLY):
{
    "design_summary": "string",
    "always_block_descriptions": ["string"],
    "fsm_analysis": {
        "has_fsm": true,
        "states": ["S1"],
        "transitions": ["S1->S2"]
    },
    "clock_domain_crossings": ["string"],
    "verification_challenges": ["string"],
    "design_intent": "string"
}
"""


class RTLAnalyzer:
    """Agent 2: Analyzes RTL code to extract structure and behavior."""

    def __init__(self, ai_client: AIClient) -> None:
        self.ai_client = ai_client

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    async def analyze(self, rtl_file_path: str | Path) -> tuple[str, RTLAnalysis]:
        """Analyse an RTL file.

        Returns:
            tuple of (raw_rtl_code, RTLAnalysis)
        """
        rtl_file_path = Path(rtl_file_path)
        logger.info("[*]  RTL Analyzer: Parsing %s …", rtl_file_path.name)

        # Stage 1: Regex-based structural parsing (fast)
        raw_code, analysis = parse_rtl(rtl_file_path)

        # Stage 2: AI-powered behavioral analysis (smart)
        logger.info("[*]  RTL Analyzer: Running AI behavioral analysis …")
        try:
            await self._enrich_with_ai(raw_code, analysis)
        except AIClientError as exc:
            logger.warning(
                "AI behavioral enrichment unavailable (%s) — continuing with structural analysis.",
                exc,
            )
        except Exception as exc:
            logger.warning(
                "Unexpected AI enrichment failure (%s) — continuing with structural analysis.",
                exc,
            )

        logger.info(
            "[*]  RTL Analyzer: Complete — %s | top=%s | %d ports | FSM=%s",
            analysis.code_style,
            analysis.top_module,
            len(analysis.port_map),
            "yes" if analysis.has_fsm else "no",
        )

        return raw_code, analysis

    # ------------------------------------------------------------------
    # AI enrichment
    # ------------------------------------------------------------------
    async def _enrich_with_ai(self, raw_code: str, analysis: RTLAnalysis) -> None:
        """Use the AI runtime to add behavioral understanding to the structural analysis."""

        # Build a summary of what we already know (from regex parsing)
        structural_summary = self._build_structural_summary(analysis)

        response = await self.ai_client.generate_with_context(
            system_prompt=SYSTEM_PROMPT,
            context_blocks={
                "RTL CODE": raw_code,
                "STRUCTURAL INFORMATION": structural_summary,
            },
            focus_instruction=(
                "Explain behavior, always blocks, FSM/state transitions, CDC concerns, "
                "and verification challenges."
            ),
            task_instruction=(
                "Return only valid JSON with the exact schema from the system prompt."
            ),
            temperature=0.7,
        )

        # Parse the AI response and enrich the analysis
        self._apply_ai_enrichment(response, analysis)

    def _build_structural_summary(self, analysis: RTLAnalysis) -> str:
        """Build a human-readable summary of the regex-extracted structure."""
        lines = []
        lines.append(f"Top module: {analysis.top_module}")
        lines.append(f"Code style: {analysis.code_style}")
        lines.append(f"Modules found: {len(analysis.modules)}")

        lines.append("\nPorts:")
        for name, port in analysis.port_map.items():
            lines.append(f"  {port.declaration}")

        if analysis.internal_signals:
            lines.append(f"\nInternal signals: {len(analysis.internal_signals)}")
            for sig in analysis.internal_signals[:10]:
                lines.append(f"  {sig.signal_type} {sig.bus_range} {sig.name}".strip())

        if analysis.always_blocks:
            lines.append(f"\nAlways blocks: {len(analysis.always_blocks)}")
            for blk in analysis.always_blocks:
                lines.append(f"  {blk.block_type} @({blk.sensitivity})")

        if analysis.parameters:
            lines.append(f"\nParameters:")
            for k, v in analysis.parameters.items():
                lines.append(f"  {k} = {v}")

        if analysis.instantiations:
            lines.append(f"\nInstantiations: {', '.join(analysis.instantiations)}")

        return "\n".join(lines)

    def _apply_ai_enrichment(self, response: str, analysis: RTLAnalysis) -> None:
        """Apply AI-generated behavioral analysis to the RTLAnalysis."""

        # Extract JSON
        json_str = self._extract_json(response)
        try:
            data = json.loads(json_str)
        except json.JSONDecodeError:
            logger.warning(
                "Could not parse AI behavioral analysis — using regex-only results."
            )
            return

        # Enrich always block descriptions
        descriptions = data.get("always_block_descriptions", [])
        for i, desc in enumerate(descriptions):
            if i < len(analysis.always_blocks):
                analysis.always_blocks[i].description = desc

        # Enrich FSM info
        fsm = data.get("fsm_analysis", {})
        if fsm.get("has_fsm") and fsm.get("states"):
            analysis.fsm_states = fsm["states"]

        logger.debug("AI enrichment applied: %s", data.get("design_summary", "")[:100])

    @staticmethod
    def _extract_json(text: str) -> str:
        """Extract JSON from a response that may contain markdown code blocks."""
        json_match = re.search(r"```(?:json)?\s*\n(.*?)```", text, re.DOTALL)
        if json_match:
            return json_match.group(1).strip()

        brace_start = text.find("{")
        if brace_start != -1:
            depth = 0
            for i in range(brace_start, len(text)):
                if text[i] == "{":
                    depth += 1
                elif text[i] == "}":
                    depth -= 1
                    if depth == 0:
                        return text[brace_start : i + 1]

        return text.strip()
