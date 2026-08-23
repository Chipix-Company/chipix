"""
Image Parser — Multimodal Design Diagram Interpretation.

Accepts images of:
  - Hand-drawn state machines
  - Block diagrams / architecture visuals
  - Whiteboard sketches of module hierarchies
  - Pin/port tables from datasheets

Sends them to a Vision LLM (Gemini Vision / GPT-4o) and extracts
structured DesignBlock fields: modules, ports, FSMs, protocols,
transaction flows, and hierarchy.

This closes the biggest gap vs ChipStack's multimodal input feature.
"""

from __future__ import annotations

import base64
import json
import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from services.mental_model.schema import (
    ArbitrationPolicy,
    ClockDomain,
    DesignBlock,
    FSMDescription,
    ParameterInfo,
    PortInfo,
    ProtocolBinding,
    SourceReference,
    SubModuleInstance,
    TransactionFlow,
)

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════════
# Image-to-Model Extraction Prompt
# ═══════════════════════════════════════════════════════════════════════

EXTRACTION_PROMPT = """\
You are a chip design architect. Analyze this image and extract ALL design information visible.

The image may contain:
- Block diagrams (module hierarchy, connections)
- State machine diagrams (states, transitions, conditions)
- Port/pin tables (signal names, directions, widths)
- Timing diagrams (clock domains, waveforms)
- Architecture overviews (data paths, control paths)
- Handwritten notes about the design

Extract as much structured information as possible and return a JSON object with these fields:

{
  "modules": ["module_name1", "module_name2"],
  "top_module": "top_module_name",
  "description": "Brief description of what the design does",
  "hierarchy": {"parent_module": ["child1", "child2"]},
  "ports": [
    {"name": "clk", "direction": "input", "width": 1, "description": "System clock"},
    {"name": "data_out", "direction": "output", "width": 8, "description": "Data output bus"}
  ],
  "parameters": [
    {"name": "DEPTH", "default_value": "16", "description": "Buffer depth"}
  ],
  "fsms": [
    {
      "name": "main_fsm",
      "states": ["IDLE", "ACTIVE", "DONE"],
      "transitions": [
        {"from": "IDLE", "to": "ACTIVE", "condition": "start"},
        {"from": "ACTIVE", "to": "DONE", "condition": "complete"}
      ]
    }
  ],
  "protocols": [
    {"protocol": "AXI4", "role": "slave", "ports": ["AWADDR", "AWVALID", "AWREADY"]}
  ],
  "transaction_flows": [
    {
      "name": "Write Transaction",
      "steps": [
        {"phase": "Address", "signals": {"AWVALID": "1"}, "cycles": "1"},
        {"phase": "Data", "signals": {"WVALID": "1"}, "cycles": "N"},
        {"phase": "Response", "signals": {"BVALID": "1"}, "cycles": "1"}
      ],
      "latency_cycles": 3
    }
  ],
  "clock_domains": [
    {"name": "clk", "frequency": "100MHz", "reset": "rst_n", "reset_polarity": "active_low"}
  ],
  "registers": [
    {"name": "CTRL_REG", "offset": "0x00", "description": "Control register"}
  ],
  "arbitration": [
    {"name": "round_robin", "masters": ["cpu", "dma"], "scheme": "round_robin"}
  ]
}

IMPORTANT:
- Only include what you can actually see in the image
- If a field is not visible, omit it (don't guess)
- Use standard Verilog/SystemVerilog naming conventions
- For handwritten text, do your best to read it accurately
- Return ONLY the JSON object, no other text
"""


def encode_image_base64(image_path: str) -> str:
    """Read an image file and return its base64 encoding."""
    path = Path(image_path)
    if not path.exists():
        raise FileNotFoundError(f"Image not found: {image_path}")

    suffix = path.suffix.lower()
    mime_map = {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".gif": "image/gif",
        ".webp": "image/webp",
        ".bmp": "image/bmp",
    }
    mime_type = mime_map.get(suffix, "image/png")

    with open(path, "rb") as f:
        data = base64.b64encode(f.read()).decode("utf-8")

    return f"data:{mime_type};base64,{data}"


async def parse_image_to_model(
    image_path: str,
    llm_client: Any = None,
    additional_context: str = "",
) -> DesignBlock:
    """
    Parse a design diagram image into a structured DesignBlock.

    Args:
        image_path: Path to the image file (.png, .jpg, .webp, etc.)
        llm_client: LLM client with vision capability (Gemini/GPT-4o)
        additional_context: Optional text context to help interpretation

    Returns:
        DesignBlock populated with extracted design information
    """
    logger.info("Parsing design diagram: %s", image_path)

    # Encode image
    image_data = encode_image_base64(image_path)

    # Build prompt with optional context
    prompt = EXTRACTION_PROMPT
    if additional_context:
        prompt += f"\n\nADDITIONAL CONTEXT from the user:\n{additional_context}\n"

    # Call Vision LLM
    raw_response = await _call_vision_llm(llm_client, image_data, prompt)

    # Parse JSON response
    extracted = _parse_llm_json(raw_response)

    if not extracted:
        logger.warning("No structured data extracted from image")
        return DesignBlock(description="Image parsing produced no structured output")

    # Build DesignBlock from extracted data
    design = _build_design_block(extracted, image_path)

    logger.info(
        "Image parsed: top=%s, %d ports, %d FSMs, %d modules",
        design.top_module,
        len(design.ports),
        len(design.fsms),
        len(design.modules),
    )

    return design


async def _call_vision_llm(llm_client: Any, image_data: str, prompt: str) -> str:
    """Call the Vision LLM with the image and extraction prompt."""

    # Try Gemini-style API first
    if llm_client and hasattr(llm_client, "generate_content"):
        try:
            import google.generativeai as genai
            response = await llm_client.generate_content([
                prompt,
                {"mime_type": image_data.split(";")[0].split(":")[1],
                 "data": image_data.split(",")[1]},
            ])
            return response.text
        except Exception as e:
            logger.warning("Gemini vision call failed: %s", e)

    # Try OpenAI-style API
    if llm_client and hasattr(llm_client, "chat"):
        try:
            response = await llm_client.chat.completions.create(
                model="gpt-4o",
                messages=[{
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": image_data}},
                    ],
                }],
                max_tokens=4096,
            )
            return response.choices[0].message.content
        except Exception as e:
            logger.warning("OpenAI vision call failed: %s", e)

    # Try our custom generate_with_context (with image in context)
    if llm_client and hasattr(llm_client, "generate_with_context"):
        try:
            response = await llm_client.generate_with_context(
                system_prompt=prompt,
                context_blocks={"IMAGE_PATH": image_data[:100] + "... (base64 image)"},
                focus_instruction="Extract design information from diagram",
                task_instruction="Parse the image and return JSON",
            )
            return response
        except Exception as e:
            logger.warning("Custom LLM vision call failed: %s", e)

    # Fallback: return empty if no LLM available
    logger.warning("No vision LLM available — returning empty extraction")
    return "{}"


def _parse_llm_json(response: str) -> Optional[Dict]:
    """Extract JSON from LLM response (may have markdown fences)."""
    if not response:
        return None

    # Try direct parse
    try:
        return json.loads(response)
    except json.JSONDecodeError:
        pass

    # Try extracting from markdown code block
    json_match = re.search(r"```(?:json)?\s*\n?(.*?)```", response, re.DOTALL)
    if json_match:
        try:
            return json.loads(json_match.group(1).strip())
        except json.JSONDecodeError:
            pass

    # Try finding the first { ... } block
    brace_match = re.search(r"\{.*\}", response, re.DOTALL)
    if brace_match:
        try:
            return json.loads(brace_match.group())
        except json.JSONDecodeError:
            pass

    logger.warning("Could not parse JSON from LLM response")
    return None


def _build_design_block(data: Dict, image_path: str) -> DesignBlock:
    """Convert parsed JSON into a DesignBlock with full type safety."""
    src = SourceReference(file=str(image_path), excerpt="Extracted from design diagram")

    # Ports
    ports = []
    for p in data.get("ports", []):
        ports.append(PortInfo(
            name=p.get("name", ""),
            direction=p.get("direction", "input"),
            width=int(p.get("width", 1)),
            description=p.get("description", ""),
            protocol_role=p.get("protocol_role", ""),
            source_ref=src,
        ))

    # Parameters
    parameters = []
    for p in data.get("parameters", []):
        parameters.append(ParameterInfo(
            name=p.get("name", ""),
            default_value=str(p.get("default_value", "")),
            description=p.get("description", ""),
            source_ref=src,
        ))

    # FSMs
    fsms = []
    for f in data.get("fsms", []):
        transitions = []
        for t in f.get("transitions", []):
            transitions.append({
                "from": t.get("from", ""),
                "to": t.get("to", ""),
                "condition": t.get("condition", ""),
            })
        fsms.append(FSMDescription(
            name=f.get("name", ""),
            states=f.get("states", []),
            transitions=transitions,
            encoding=f.get("encoding", ""),
            source_ref=src,
        ))

    # Protocols
    protocols = []
    for p in data.get("protocols", []):
        protocols.append(ProtocolBinding(
            protocol=p.get("protocol", ""),
            role=p.get("role", ""),
            port_group=p.get("ports", []),
            description=p.get("description", ""),
            source_ref=src,
        ))

    # Clock domains
    clocks = []
    for c in data.get("clock_domains", []):
        clocks.append(ClockDomain(
            name=c.get("name", ""),
            frequency=c.get("frequency", ""),
            associated_reset=c.get("reset", ""),
            reset_polarity=c.get("reset_polarity", "active_low"),
            source_ref=src,
        ))

    # Transaction flows
    flows = []
    for tf in data.get("transaction_flows", []):
        flows.append(TransactionFlow(
            name=tf.get("name", ""),
            protocol=tf.get("protocol", ""),
            steps=tf.get("steps", []),
            latency_cycles=int(tf.get("latency_cycles", 0)),
            throughput=tf.get("throughput", ""),
            constraints=tf.get("constraints", []),
            source_ref=src,
        ))

    # Arbitration
    arb_policies = []
    for a in data.get("arbitration", []):
        arb_policies.append(ArbitrationPolicy(
            name=a.get("name", ""),
            masters=a.get("masters", []),
            scheme=a.get("scheme", ""),
            priority_levels=int(a.get("priority_levels", 0)),
            qos_support=bool(a.get("qos_support", False)),
            description=a.get("description", ""),
            source_ref=src,
        ))

    # Hierarchy
    hierarchy = data.get("hierarchy", {})
    modules = data.get("modules", [])
    sub_instances = []
    for parent, children in hierarchy.items():
        for child in children:
            sub_instances.append(SubModuleInstance(
                instance_name=f"u_{child}",
                module_name=child,
                source_ref=src,
            ))

    # Register map
    registers = data.get("registers", [])

    return DesignBlock(
        top_module=data.get("top_module", ""),
        description=data.get("description", ""),
        modules=modules,
        hierarchy_tree=hierarchy,
        sub_instances=sub_instances,
        ports=ports,
        parameters=parameters,
        clock_domains=clocks,
        fsms=fsms,
        protocols=protocols,
        transaction_flows=flows,
        arbitration_policies=arb_policies,
        register_map=registers,
    )


# ═══════════════════════════════════════════════════════════════════════
# Offline / No-LLM fallback: extract what we can from filename/metadata
# ═══════════════════════════════════════════════════════════════════════

def parse_image_metadata_only(image_path: str) -> Dict[str, Any]:
    """Extract basic metadata from an image file without using LLM.

    Useful for cataloging uploaded diagrams before LLM processing.
    """
    path = Path(image_path)
    if not path.exists():
        return {"error": f"File not found: {image_path}"}

    stat = path.stat()

    return {
        "filename": path.name,
        "format": path.suffix.lower().lstrip("."),
        "size_bytes": stat.st_size,
        "size_human": f"{stat.st_size / 1024:.1f} KB",
        "supported": path.suffix.lower() in {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"},
        "source_ref": {
            "file": str(path),
            "excerpt": "Design diagram (pending LLM extraction)",
        },
    }
