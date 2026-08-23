"""
Utility helper functions for the RTL Design Agent.
"""

from __future__ import annotations
import re
from datetime import datetime


def extract_code_from_response(response: str) -> str:
    """
    Extract code from LLM response, handling various markdown code block formats.
    Looks for ```verilog, ```systemverilog, ```sv, or plain ``` blocks.
    """
    # Try specific HDL code blocks first
    patterns = [
        r"```(?:verilog|systemverilog|sv)\s*\n(.*?)```",
        r"```\s*\n(.*?)```",
    ]

    for pattern in patterns:
        match = re.search(pattern, response, re.DOTALL)
        if match:
            return match.group(1).strip()

    # If no code block found, return the whole response (might be raw code)
    return response.strip()


def get_file_extension(language: str) -> str:
    """Return file extension for the target language."""
    return ".sv" if language == "systemverilog" else ".v"


def extract_module_name(code: str) -> str:
    """Extract the primary module name from Verilog/SystemVerilog code."""
    match = re.search(r"module\s+(\w+)", code)
    return match.group(1) if match else "design"


def generate_filename(code: str, language: str) -> str:
    """Generate a sensible filename from the code."""
    module_name = extract_module_name(code)
    ext = get_file_extension(language)
    return f"{module_name}{ext}"


def format_timestamp() -> str:
    """Return a formatted timestamp string."""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")
