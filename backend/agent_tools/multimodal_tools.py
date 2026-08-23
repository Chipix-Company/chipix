"""
Multimodal Tools — Image-to-Model extraction for design diagrams.

ChipStack feature: engineers upload hand-drawn state machines, block diagrams,
or whiteboard sketches and the AI extracts structured design information.

Tools:
  - parseDesignImage: Send image to Vision LLM → extract DesignBlock
"""

from __future__ import annotations

import json
import logging
from typing import Any

from agent_tools import register_tool

logger = logging.getLogger(__name__)


async def handle_parse_design_image(
    project_id: str,
    image_path: str = "",
    additional_context: str = "",
    db_session: Any = None,
    ai_client: Any = None,
    **kwargs: Any,
) -> str:
    """
    Parse a design diagram image into structured mental model data.

    Accepts: PNG, JPG, WEBP, GIF, BMP images of:
      - Block diagrams / architecture visuals
      - Hand-drawn state machines
      - Port/pin tables from datasheets
      - Timing diagrams
      - Whiteboard sketches

    Returns structured JSON with modules, ports, FSMs, protocols,
    transaction flows, hierarchy — ready to merge into the mental model.
    """
    if not image_path:
        return json.dumps({
            "error": "image_path is required",
            "hint": "Provide the path to an image file (.png, .jpg, .webp)",
            "supported_formats": ["png", "jpg", "jpeg", "webp", "gif", "bmp"],
        })

    try:
        from services.mental_model.image_parser import (
            parse_image_to_model,
            parse_image_metadata_only,
        )
        from dataclasses import asdict

        # First, validate the file exists and is a supported format
        metadata = parse_image_metadata_only(image_path)
        if "error" in metadata:
            return json.dumps(metadata)

        if not metadata.get("supported", False):
            return json.dumps({
                "error": f"Unsupported image format: {metadata.get('format', 'unknown')}",
                "supported_formats": ["png", "jpg", "jpeg", "webp", "gif", "bmp"],
            })

        # Parse the image using Vision LLM
        design_block = await parse_image_to_model(
            image_path=image_path,
            llm_client=ai_client,
            additional_context=additional_context,
        )

        # Convert to JSON-serializable dict
        result = asdict(design_block)

        # Add metadata
        result["_source"] = {
            "type": "image",
            "file": image_path,
            "format": metadata.get("format", ""),
            "size": metadata.get("size_human", ""),
        }

        result["_summary"] = (
            f"Extracted from {metadata.get('filename', 'image')}: "
            f"top_module={design_block.top_module}, "
            f"{len(design_block.ports)} ports, "
            f"{len(design_block.fsms)} FSMs, "
            f"{len(design_block.modules)} modules, "
            f"{len(design_block.protocols)} protocols, "
            f"{len(design_block.transaction_flows)} transaction flows"
        )

        result["_instruction_for_llm"] = (
            "Show the user what was extracted from their diagram. "
            "Present the modules, ports, FSMs, and protocols found. "
            "Ask: 'Should I merge this into the mental model?' "
            "If user agrees, call analyzeDesign to build the full model."
        )

        return json.dumps(result, indent=2, default=str)

    except FileNotFoundError as e:
        return json.dumps({"error": str(e)})
    except Exception as e:
        logger.exception(f"Image parsing failed: {e}")
        return json.dumps({"error": f"Image parsing failed: {str(e)}"})


# Register tool
register_tool("parseDesignImage", handle_parse_design_image)
