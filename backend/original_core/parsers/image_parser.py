"""
Gap 5 — Multimodal Input Parser (Image/Diagram).

Accept visual inputs via Gemini Vision:
  - Hand-drawn state machines
  - Block diagrams
  - Timing diagrams
  - Architecture visuals
  - Waveform screenshots

Converts visual inputs into structured text for the mental model.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from google import genai
from google.genai import types

import config
from core.ai_client import AIClient
from core.logger import get_logger

logger = get_logger("ImageParser")


@dataclass
class ImageAnalysis:
    """Result of analyzing a visual input."""

    image_path: str = ""
    image_type: str = ""  # fsm, block_diagram, timing, waveform, architecture
    description: str = ""
    extracted_states: list[str] = field(default_factory=list)
    extracted_transitions: list[str] = field(default_factory=list)
    extracted_signals: list[str] = field(default_factory=list)
    extracted_modules: list[str] = field(default_factory=list)
    raw_analysis: str = ""

    @property
    def summary(self) -> str:
        return (
            f"Image [{self.image_type}]: {self.description[:80]}... "
            f"({len(self.extracted_states)} states, "
            f"{len(self.extracted_signals)} signals)"
        )


IMAGE_ANALYSIS_PROMPT = """\
You are a hardware design expert analyzing a visual diagram.

TASK: Extract ALL design information from this image.

Identify the type of diagram:
  - FSM (state machine): Extract states, transitions, conditions
  - Block diagram: Extract modules, interfaces, connections
  - Timing diagram: Extract signals, waveforms, timing constraints
  - Architecture: Extract hierarchy, buses, protocols
  - Waveform: Extract signal names, values, clock relationships

OUTPUT FORMAT (JSON):
{
  "image_type": "fsm|block_diagram|timing|waveform|architecture",
  "description": "Overall description of what the diagram shows",
  "states": ["list of FSM states if applicable"],
  "transitions": ["STATE_A -> STATE_B: condition"],
  "signals": ["list of signal names"],
  "modules": ["list of module/block names"],
  "interfaces": ["list of interface connections"],
  "timing_constraints": ["any timing requirements"],
  "notes": "any additional observations"
}
"""


class ImageParser:
    """Parse visual inputs using Gemini Vision API."""

    def __init__(self, ai_client: AIClient) -> None:
        self.ai_client = ai_client

    async def analyze_image(self, image_path: str | Path) -> ImageAnalysis:
        """Analyze a design image/diagram using vision AI.

        Args:
            image_path: Path to image file (PNG, JPG, SVG)

        Returns:
            ImageAnalysis with extracted design information
        """
        image_path = Path(image_path)
        if not image_path.exists():
            logger.warning("Image not found: %s", image_path)
            return ImageAnalysis(image_path=str(image_path))

        logger.info("Analyzing image: %s", image_path.name)

        # Read image bytes for Gemini Vision
        image_data = image_path.read_bytes()

        # Determine MIME type
        suffix = image_path.suffix.lower()
        mime_types = {
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".gif": "image/gif",
            ".webp": "image/webp",
            ".svg": "image/svg+xml",
        }
        mime_type = mime_types.get(suffix, "image/png")

        try:
            # Use Gemini's multimodal capability
            client = genai.Client(api_key=config.GOOGLE_API_KEY)

            image_part = types.Part.from_bytes(data=image_data, mime_type=mime_type)

            response = client.models.generate_content(
                model=config.MODEL_NAME,
                contents=[IMAGE_ANALYSIS_PROMPT, image_part],
                config=types.GenerateContentConfig(
                    temperature=0.2,
                    max_output_tokens=4096,
                ),
            )

            raw_text = response.text
            analysis = self._parse_response(raw_text, str(image_path))

            logger.info("Image analysis: %s", analysis.summary)
            return analysis

        except ImportError:
            logger.warning("google-genai not installed. Image analysis unavailable.")
            return ImageAnalysis(
                image_path=str(image_path),
                description="Image analysis requires google-genai package",
            )
        except Exception as e:
            logger.warning("Image analysis failed: %s", e)
            return ImageAnalysis(
                image_path=str(image_path),
                description=f"Analysis error: {e}",
            )

    async def analyze_multiple(
        self, image_paths: list[str | Path]
    ) -> list[ImageAnalysis]:
        """Analyze multiple images."""
        results = []
        for path in image_paths:
            analysis = await self.analyze_image(path)
            results.append(analysis)
        return results

    def to_spec_text(self, analyses: list[ImageAnalysis]) -> str:
        """Convert image analyses into specification text for the mental model.

        This text gets injected into the mental model builder's context.
        """
        blocks = []

        for analysis in analyses:
            block = [f"### Visual Input: {analysis.image_type}"]
            block.append(analysis.description)

            if analysis.extracted_states:
                block.append(f"\nFSM States: {', '.join(analysis.extracted_states)}")
            if analysis.extracted_transitions:
                block.append("Transitions:")
                for t in analysis.extracted_transitions:
                    block.append(f"  - {t}")
            if analysis.extracted_signals:
                block.append(f"\nSignals: {', '.join(analysis.extracted_signals)}")
            if analysis.extracted_modules:
                block.append(f"\nModules: {', '.join(analysis.extracted_modules)}")

            blocks.append("\n".join(block))

        return "\n\n".join(blocks)

    def _parse_response(self, response: str, image_path: str) -> ImageAnalysis:
        """Parse the LLM response into ImageAnalysis."""
        import json

        analysis = ImageAnalysis(image_path=image_path, raw_analysis=response)

        # Try JSON extraction
        json_match = re.search(r"\{.*\}", response, re.DOTALL)
        if json_match:
            try:
                data = json.loads(json_match.group())
                analysis.image_type = data.get("image_type", "unknown")
                analysis.description = data.get("description", "")
                analysis.extracted_states = data.get("states", [])
                analysis.extracted_transitions = data.get("transitions", [])
                analysis.extracted_signals = data.get("signals", [])
                analysis.extracted_modules = data.get("modules", [])
                return analysis
            except json.JSONDecodeError:
                pass

        # Fallback
        analysis.description = response[:500]
        return analysis
