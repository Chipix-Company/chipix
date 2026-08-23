"""
Agent 1 — Spec Parser

Parses uploaded specification documents (PDF/DOCX/TXT) and extracts
structured requirements into a DesignSpecification object.

Uses the configured AI runtime for intelligent extraction of:
  • Module name, ports, functional requirements
  • Clock/reset strategy, constraints, edge cases
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from core.ai_client import AIClient
from core.logger import get_logger
from core.models import DesignSpecification, PortDefinition, PortDirection
from parsers.pdf_parser import parse_pdf
from parsers.docx_parser import parse_docx
from parsers.txt_parser import parse_txt

logger = get_logger("Agent1.SpecParser")

# ── System prompt for structured spec extraction ──────────────────────
SYSTEM_PROMPT = """\
You are an expert chip design analyst. Extract all design requirements into JSON.
RULES:
1. Extract every port (name, direction, width).
2. List functional requirements as testable statements.
3. Identify clock domains and reset strategy.
4. List edge cases for testing.
OUTPUT FORMAT (JSON ONLY):
{
    "module_name": "string",
    "description": "string",
    "ports": [
        {
            "name": "string",
            "direction": "input|output|inout",
            "width": 1,
            "bus_range": "e.g. [3:0]",
            "port_type": "logic|wire|reg",
            "description": "string"
        }
    ],
    "functional_requirements": ["string"],
    "constraints": ["string"],
    "clock_domains": ["string"],
    "reset_strategy": "string",
    "edge_cases": ["string"],
    "performance_targets": {},
    "protocol_info": "string"
}
"""


class SpecParser:
    """Agent 1: Parses specification documents into DesignSpecification."""

    def __init__(self, ai_client: AIClient) -> None:
        self.ai_client = ai_client

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    async def parse(self, spec_file_path: str | Path) -> DesignSpecification:
        """Parse a specification file and return a structured DesignSpecification.

        Supports: .pdf, .docx, .doc, .txt, .md
        """
        spec_file_path = Path(spec_file_path)
        logger.info("[*]  Spec Parser: Parsing %s …", spec_file_path.name)

        # Step 1: Extract raw text from the file
        raw_text = self._extract_text(spec_file_path)
        logger.info("[*]  Spec Parser: Extracted %d chars of text", len(raw_text))

        # Step 2: Send to AI runtime for structured extraction
        response = await self.ai_client.generate_with_context(
            system_prompt=SYSTEM_PROMPT,
            context_blocks={
                "SPECIFICATION DOCUMENT": raw_text,
            },
            focus_instruction=(
                "Extract module, ports, requirements, reset/clock strategy, constraints, "
                "and edge cases with strict JSON output."
            ),
            task_instruction=(
                "Return only valid JSON matching the exact schema in the system prompt."
            ),
            temperature=0.7,  # Increased temperature for small 1.5B/2B models to prevent generation collapse
        )

        # Step 3: Parse JSON response into DesignSpecification
        spec = self._parse_response(response, raw_text)

        logger.info(
            "[*]  Spec Parser: Extracted — module=%s, %d ports, %d requirements, %d edge cases",
            spec.module_name,
            len(spec.ports),
            len(spec.functional_requirements),
            len(spec.edge_cases),
        )

        return spec

    # ------------------------------------------------------------------
    # Text extraction
    # ------------------------------------------------------------------
    def _extract_text(self, file_path: Path) -> str:
        """Route to the appropriate parser based on file extension."""
        suffix = file_path.suffix.lower()

        if suffix == ".pdf":
            return parse_pdf(file_path)
        elif suffix in (".docx", ".doc"):
            return parse_docx(file_path)
        elif suffix in (".txt", ".md", ".text"):
            return parse_txt(file_path)
        else:
            # Try reading as plain text
            logger.warning(
                "Unknown file type '%s', attempting plain text read.", suffix
            )
            return file_path.read_text(encoding="utf-8")

    # ------------------------------------------------------------------
    # Response parsing
    # ------------------------------------------------------------------
    def _parse_response(self, response: str, raw_text: str) -> DesignSpecification:
        """Parse the model JSON response into a DesignSpecification."""

        # Extract JSON from response (may be wrapped in markdown code block)
        json_str = self._extract_json(response)

        try:
            data = json.loads(json_str)
        except json.JSONDecodeError as e:
            logger.error("Failed to parse JSON response: %s", e)
            logger.debug("Raw response: %s", response[:500])
            return self._fallback_from_text(raw_text)

        fallback = self._fallback_from_text(raw_text)

        # Build DesignSpecification from parsed data
        ports = []
        for p in data.get("ports", []):
            try:
                ports.append(
                    PortDefinition(
                        name=p.get("name", ""),
                        direction=PortDirection(p.get("direction", "input")),
                        width=int(p.get("width", 1)),
                        bus_range=p.get("bus_range", ""),
                        port_type=p.get("port_type", "logic"),
                        description=p.get("description", ""),
                    )
                )
            except (ValueError, KeyError) as e:
                logger.warning("Skipping invalid port: %s — %s", p, e)

        return DesignSpecification(
            module_name=data.get("module_name", "") or fallback.module_name,
            description=data.get("description", "") or fallback.description,
            ports=ports or fallback.ports,
            functional_requirements=(
                data.get("functional_requirements", [])
                or fallback.functional_requirements
            ),
            constraints=data.get("constraints", []) or fallback.constraints,
            clock_domains=data.get("clock_domains", []) or fallback.clock_domains,
            reset_strategy=data.get("reset_strategy", "") or fallback.reset_strategy,
            edge_cases=data.get("edge_cases", []) or fallback.edge_cases,
            performance_targets=data.get("performance_targets", {}),
            protocol_info=data.get("protocol_info", "") or fallback.protocol_info,
            raw_text=raw_text,
        )

    def _fallback_from_text(self, raw_text: str) -> DesignSpecification:
        """Build a deterministic best-effort spec when AI JSON is malformed."""
        lines = [line.strip() for line in raw_text.splitlines() if line.strip()]

        module_name = ""
        module_patterns = [
            r"\bmodule\s+name\s*[:=\-]\s*([A-Za-z_][A-Za-z0-9_]*)",
            r"\bmodule\s*[:=\-]\s*([A-Za-z_][A-Za-z0-9_]*)",
            r"\bmodule\s+([A-Za-z_][A-Za-z0-9_]*)",
        ]
        for pattern in module_patterns:
            module_match = re.search(pattern, raw_text, re.IGNORECASE)
            if module_match:
                candidate = module_match.group(1)
                if candidate.lower() in {"name", "module"}:
                    continue
                module_name = candidate
                break

        ports: list[PortDefinition] = []
        seen_ports: set[str] = set()
        port_regex_direction_first = re.compile(
            r"\b(input|output|inout)\b\s*(\[[^\]]+\])?\s*([A-Za-z_][A-Za-z0-9_]*)",
            re.IGNORECASE,
        )
        port_regex_name_first = re.compile(
            r"\b([A-Za-z_][A-Za-z0-9_]*)\b\s*[:=\-]\s*(input|output|inout)\b\s*(\[[^\]]+\])?",
            re.IGNORECASE,
        )

        for line in lines:
            direction_raw = ""
            bus_range = ""
            port_name = ""

            match_direction_first = port_regex_direction_first.search(line)
            if match_direction_first:
                direction_raw, bus_range, port_name = match_direction_first.groups()
            else:
                match_name_first = port_regex_name_first.search(line)
                if not match_name_first:
                    continue
                port_name, direction_raw, bus_range = match_name_first.groups()

            if port_name in seen_ports:
                continue
            seen_ports.add(port_name)

            width = 1
            if bus_range:
                bus_match = re.match(r"\[(\d+)\s*:\s*(\d+)\]", bus_range)
                if bus_match:
                    msb = int(bus_match.group(1))
                    lsb = int(bus_match.group(2))
                    width = abs(msb - lsb) + 1

            description = ""
            if "-" in line:
                description = line.split("-", 1)[1].strip()
            elif ":" in line:
                description = line.split(":", 1)[1].strip()

            direction = PortDirection(direction_raw.lower())
            ports.append(
                PortDefinition(
                    name=port_name,
                    direction=direction,
                    width=width,
                    bus_range=bus_range or "",
                    port_type="logic",
                    description=description,
                )
            )

        req_pattern = re.compile(r"^\s*(?:\d+[\)\.:\-]|[-*•])\s+(.+)")
        functional_requirements: list[str] = []
        for line in lines:
            req_match = req_pattern.match(line)
            if not req_match:
                continue
            req_text = req_match.group(1).strip()
            if len(req_text) >= 20:
                functional_requirements.append(req_text)

        if not functional_requirements:
            sentences = re.split(r"(?<=[.!?])\s+", raw_text)
            functional_requirements = [
                s.strip() for s in sentences if len(s.strip()) >= 30
            ][:8]

        constraints = [
            line
            for line in lines
            if any(token in line.lower() for token in ("must", "shall", "constraint"))
        ][:12]

        clock_domains: list[str] = []
        for token in re.findall(r"\b[A-Za-z_][A-Za-z0-9_]*\b", raw_text):
            token_l = token.lower()
            if "clk" in token_l or "clock" in token_l:
                if token not in clock_domains:
                    clock_domains.append(token)
            if len(clock_domains) >= 4:
                break

        reset_lines = [
            line for line in lines if "reset" in line.lower() or "rst" in line.lower()
        ]
        reset_strategy = reset_lines[0] if reset_lines else ""

        edge_cases = [
            line
            for line in lines
            if any(
                token in line.lower()
                for token in ("edge", "corner", "overflow", "underflow", "boundary")
            )
        ][:10]

        description = ""
        if lines:
            description = lines[0][:240]

        return DesignSpecification(
            module_name=module_name,
            description=description,
            ports=ports,
            functional_requirements=functional_requirements,
            constraints=constraints,
            clock_domains=clock_domains,
            reset_strategy=reset_strategy,
            edge_cases=edge_cases,
            performance_targets={},
            protocol_info="",
            raw_text=raw_text,
        )

    @staticmethod
    def _extract_json(text: str) -> str:
        """Extract JSON from a response that may contain markdown code blocks."""
        # Try to find JSON in a code block
        json_match = re.search(r"```(?:json)?\s*\n(.*?)```", text, re.DOTALL)
        if json_match:
            return json_match.group(1).strip()

        # Try to find raw JSON (starts with {)
        brace_start = text.find("{")
        if brace_start != -1:
            # Find matching closing brace
            depth = 0
            for i in range(brace_start, len(text)):
                if text[i] == "{":
                    depth += 1
                elif text[i] == "}":
                    depth -= 1
                    if depth == 0:
                        return text[brace_start : i + 1]

        # Last resort: return the whole thing
        return text.strip()
