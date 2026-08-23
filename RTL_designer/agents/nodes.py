"""
LangGraph node functions for the RTL Design Agent pipeline.

Supports two modes:
  SIMPLE:      Planner → Coder → Reviewer  (< 500 lines)
  HIERARCHICAL: Planner → Decomposer → Coder(per-submodule loop) → Composer → Reviewer  (500+ lines)
"""

from __future__ import annotations
import os
import re
import sys
from langchain_core.messages import HumanMessage, SystemMessage, AIMessage
from dotenv import load_dotenv

# Add backend to path so llm_provider is importable
_backend_dir = os.path.join(os.path.dirname(__file__), "..", "..", "backend")
if _backend_dir not in sys.path:
    sys.path.insert(0, _backend_dir)

from .state import AgentState, SubmoduleSpec
from .prompts import (
    PLANNER_SYSTEM_PROMPT,
    DECOMPOSER_SYSTEM_PROMPT,
    CODER_SYSTEM_PROMPT_VERILOG,
    CODER_SYSTEM_PROMPT_SV,
    CODER_SUBMODULE_PROMPT_VERILOG,
    CODER_SUBMODULE_PROMPT_SV,
    COMPOSER_SYSTEM_PROMPT,
    REVIEWER_SYSTEM_PROMPT,
)

load_dotenv()


def _get_llm(temperature: float = 0.2):
    """Create an LLM instance using the configured provider (Gemini, NVIDIA NIM, or OpenAI-compatible)."""
    from llm_provider import get_langchain_llm

    return get_langchain_llm(temperature=temperature)


def _detect_complexity(spec_text: str) -> str:
    """Parse the complexity estimate from the planner's spec output."""
    match = re.search(
        r"\bCOMPLEXITY\s*:\s*(VERY[\s_-]*LARGE|LARGE|MEDIUM|SMALL)\b",
        str(spec_text or ""),
        flags=re.IGNORECASE,
    )
    if not match:
        return "small"

    normalized = re.sub(r"[\s-]+", "_", match.group(1).strip().lower())
    if normalized == "very_large":
        return "very_large"
    if normalized == "large":
        return "large"
    if normalized == "medium":
        return "medium"
    return "small"


_SUBMODULE_START_RE = re.compile(
    r"^\s*(?:`{3}[a-zA-Z0-9_-]*\s*)?[-*_]{3,}\s*SUBMODULE\s*[-*_]{0,}"
    r"\s*(?:`{3})?\s*$",
    flags=re.IGNORECASE | re.MULTILINE,
)
_SUBMODULE_END_RE = re.compile(
    r"^\s*(?:`{3}[a-zA-Z0-9_-]*\s*)?[-*_]{3,}\s*END[\s_-]*SUBMODULE"
    r"\s*[-*_]{0,}\s*(?:`{3})?\s*$",
    flags=re.IGNORECASE | re.MULTILINE,
)


def _sanitize_module_name(raw_name: str, fallback: str) -> str:
    """Return a clean Verilog identifier from a decomposer heading."""
    first_line = str(raw_name or "").splitlines()[0].strip()
    first_line = re.sub(r"[`*#]", "", first_line)
    match = re.search(r"[A-Za-z_][A-Za-z0-9_]*", first_line)
    return match.group(0) if match else fallback


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# PLANNER NODE
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def planner_node(state: AgentState) -> dict:
    """
    Analyzes the user prompt and generates a structured RTL specification.
    Also estimates complexity to decide if hierarchical decomposition is needed.
    """
    llm = _get_llm(temperature=0.3)
    language = state.get("language", "verilog")
    lang_label = "Verilog" if language == "verilog" else "SystemVerilog"

    messages = [
        SystemMessage(content=PLANNER_SYSTEM_PROMPT),
        HumanMessage(
            content=(
                f"Create a detailed RTL specification for the following design request. "
                f"Target language: {lang_label}.\n\n"
                f"User Request:\n{state['user_prompt']}"
            )
        ),
    ]

    response = llm.invoke(messages)
    spec = response.content

    # Determine if we need hierarchical decomposition
    complexity = _detect_complexity(spec)
    is_hierarchical = complexity in ("large", "very_large")

    next_status = "decomposing" if is_hierarchical else "coding"
    complexity_label = complexity.upper().replace("_", " ")

    return {
        "spec": spec,
        "is_hierarchical": is_hierarchical,
        "status": next_status,
        "messages": [
            AIMessage(
                content=(
                    f"📋 **Specification Generated** — Complexity: **{complexity_label}**\n"
                    f"{'🔀 *Hierarchical decomposition will be used.*' if is_hierarchical else '📦 *Single-module generation.*'}\n\n"
                    f"{spec}"
                ),
                name="Planner",
            )
        ],
    }


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# DECOMPOSER NODE (only for large designs)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def decomposer_node(state: AgentState) -> dict:
    """
    Breaks a large design specification into individual submodule specs.
    Each submodule can be generated independently under ~400 lines.
    """
    llm = _get_llm(temperature=0.3)

    messages = [
        SystemMessage(content=DECOMPOSER_SYSTEM_PROMPT),
        HumanMessage(
            content=(
                f"Decompose the following design into independent submodules:\n\n"
                f"{state['spec']}"
            )
        ),
    ]

    response = llm.invoke(messages)
    decomposition = response.content

    # Parse submodule specs from the decomposition
    submodule_specs = _parse_submodule_specs(decomposition)

    if not submodule_specs:
        # Fallback: if parsing fails, treat as single module
        submodule_specs = [
            SubmoduleSpec(
                name="top_module",
                spec=state["spec"],
                code="",
                status="pending",
            )
        ]

    sub_list = "\n".join(
        f"  {i + 1}. **{s['name']}** — {s['status']}"
        for i, s in enumerate(submodule_specs)
    )

    return {
        "submodule_specs": submodule_specs,
        "current_submodule_idx": 0,
        "accumulated_code": "",
        "status": "coding",
        "messages": [
            AIMessage(
                content=(
                    f"🔀 **Design Decomposed** into {len(submodule_specs)} submodules:\n\n"
                    f"{sub_list}\n\n"
                    f"Each will be generated independently."
                ),
                name="Decomposer",
            )
        ],
    }


def _parse_submodule_specs(text: str) -> list[SubmoduleSpec]:
    """Parse the decomposer's output into SubmoduleSpec objects."""
    specs = []
    seen_names: set[str] = set()

    # Accept plain markers as well as markdown-emphasized/fenced variants.
    chunks = _SUBMODULE_START_RE.split(str(text or ""))

    for chunk_index, chunk in enumerate(chunks, start=1):
        chunk = chunk.strip()
        if not chunk or not re.search(
            r"###\s*Module Name\s*:", chunk, flags=re.IGNORECASE
        ):
            continue

        # Remove end marker
        chunk = _SUBMODULE_END_RE.sub("", chunk).strip()

        # Extract module name
        name_match = re.search(
            r"###\s*Module Name\s*:\s*([^\r\n]+)",
            chunk,
            flags=re.IGNORECASE,
        )
        if not name_match:
            continue

        name = _sanitize_module_name(
            name_match.group(1),
            fallback=f"submodule_{chunk_index}",
        )
        if name in seen_names:
            suffix = 2
            unique_name = f"{name}_{suffix}"
            while unique_name in seen_names:
                suffix += 1
                unique_name = f"{name}_{suffix}"
            name = unique_name
        seen_names.add(name)

        specs.append(
            SubmoduleSpec(
                name=name,
                spec=chunk,
                code="",
                status="pending",
            )
        )

    return specs


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# CODER NODE (handles both simple and hierarchical)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def coder_node(state: AgentState) -> dict:
    """
    Generates RTL code. Two modes:
    - SIMPLE: generates all code in one shot from the spec.
    - HIERARCHICAL: generates ONE submodule per invocation, loops via graph edges.
    """
    is_hierarchical = state.get("is_hierarchical", False)

    if is_hierarchical:
        return _coder_hierarchical(state)
    else:
        return _coder_simple(state)


def _coder_simple(state: AgentState) -> dict:
    """Generate all code in one LLM call (for small/medium designs)."""
    llm = _get_llm(temperature=0.1)
    language = state.get("language", "verilog")

    system_prompt = (
        CODER_SYSTEM_PROMPT_VERILOG if language == "verilog" else CODER_SYSTEM_PROMPT_SV
    )

    revision_count = state.get("revision_count", 0)
    if revision_count > 0 and state.get("review"):
        coding_prompt = (
            f"Here is the specification:\n\n{state['spec']}\n\n"
            f"Here is your previous code that needs revision:\n\n{state['rtl_code']}\n\n"
            f"Here is the reviewer's feedback:\n\n{state['review']}\n\n"
            f"Please fix ALL issues identified and produce the corrected code. "
            f"This is revision {revision_count} of max 3."
        )
    else:
        coding_prompt = f"Generate RTL code based on the following specification:\n\n{state['spec']}"

    messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=coding_prompt),
    ]

    response = llm.invoke(messages)
    rtl_code = response.content

    revision_label = f" (Revision {revision_count})" if revision_count > 0 else ""
    return {
        "rtl_code": rtl_code,
        "status": "reviewing",
        "messages": [
            AIMessage(
                content=f"💻 **RTL Code Generated{revision_label}**\n\n{rtl_code}",
                name="Coder",
            )
        ],
    }


def _coder_hierarchical(state: AgentState) -> dict:
    """
    Generate ONE submodule per call. The graph loops back to this node
    until all submodules are generated, then routes to composer.
    """
    llm = _get_llm(temperature=0.1)
    language = state.get("language", "verilog")
    idx = state.get("current_submodule_idx", 0)
    submodule_specs = state.get("submodule_specs", [])
    revision_count = state.get("revision_count", 0)
    starting_revision_pass = (
        bool(submodule_specs)
        and state.get("review_decision") == "revise"
        and revision_count > 0
        and idx >= len(submodule_specs)
    )

    if starting_revision_pass:
        # The reviewer returns here after all modules have already been coded.
        # Restart once and rebuild the composed buffer from revised modules.
        idx = 0
        accumulated = ""
    else:
        accumulated = state.get("accumulated_code", "")

    if idx >= len(submodule_specs):
        # All submodules done, move to composer
        return {
            "status": "composing",
            "messages": [
                AIMessage(
                    content="✅ All submodules generated. Moving to composition...",
                    name="Coder",
                )
            ],
        }

    current_sub = submodule_specs[idx]

    # Choose submodule-specific system prompt
    system_prompt = (
        CODER_SUBMODULE_PROMPT_VERILOG
        if language == "verilog"
        else CODER_SUBMODULE_PROMPT_SV
    )

    # Build context: show the names and port signatures of already-generated siblings
    sibling_context = ""
    for i, sub in enumerate(submodule_specs):
        if i != idx and sub.get("code"):
            # Extract just the module declaration line for context
            code = sub["code"]
            mod_decl = re.search(r"(module\s+\w+[\s\S]*?;)", code)
            if mod_decl:
                sibling_context += (
                    f"\n// Already generated: {sub['name']}\n{mod_decl.group(1)}\n"
                )

    # Handle revisions for hierarchical mode
    if revision_count > 0 and current_sub.get("code") and state.get("review"):
        coding_prompt = (
            f"## Submodule: {current_sub['name']} (Revision {revision_count})\n\n"
            f"## Specification:\n{current_sub['spec']}\n\n"
            f"## Previous Code:\n```\n{current_sub['code']}\n```\n\n"
            f"## Reviewer Feedback:\n{state['review']}\n\n"
            f"Fix ALL issues and regenerate this submodule."
        )
    else:
        coding_prompt = (
            f"## Submodule {idx + 1} of {len(submodule_specs)}: {current_sub['name']}\n\n"
            f"## Full Design Spec:\n{state['spec']}\n\n"
            f"## This Submodule's Spec:\n{current_sub['spec']}\n\n"
        )
        if sibling_context:
            coding_prompt += (
                f"## Already-generated sibling module declarations (for port matching):\n"
                f"```\n{sibling_context}\n```\n\n"
            )
        coding_prompt += "Generate ONLY this submodule's code."

    messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=coding_prompt),
    ]

    response = llm.invoke(messages)
    submodule_code = response.content

    # Update the submodule spec with generated code
    updated_specs = list(submodule_specs)  # shallow copy
    updated_specs[idx] = SubmoduleSpec(
        name=current_sub["name"],
        spec=current_sub["spec"],
        code=submodule_code,
        status="generated",
    )

    # Accumulate code
    from rtl_utils.helpers import extract_code_from_response

    clean_code = extract_code_from_response(submodule_code)
    separator = (
        f"\n\n// {'=' * 70}\n// Module: {current_sub['name']}\n// {'=' * 70}\n\n"
    )
    accumulated += separator + clean_code

    next_idx = idx + 1
    all_done = next_idx >= len(submodule_specs)

    return {
        "submodule_specs": updated_specs,
        "current_submodule_idx": next_idx,
        "accumulated_code": accumulated,
        "review_decision": (
            "" if starting_revision_pass else state.get("review_decision", "")
        ),
        "status": "composing" if all_done else "coding",
        "messages": [
            AIMessage(
                content=(
                    f"💻 **Submodule {idx + 1}/{len(submodule_specs)}: `{current_sub['name']}`** generated\n\n"
                    f"{submodule_code}"
                ),
                name="Coder",
            )
        ],
    }


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# COMPOSER NODE (only for hierarchical designs)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def composer_node(state: AgentState) -> dict:
    """
    Takes all individually-generated submodules and composes them into
    a single, clean, well-organized RTL file with proper ordering and
    interface fixes.
    """
    llm = _get_llm(temperature=0.1)
    accumulated = state.get("accumulated_code", "")

    messages = [
        SystemMessage(content=COMPOSER_SYSTEM_PROMPT),
        HumanMessage(
            content=(
                f"Compose the following individually-generated submodules into a single, "
                f"clean RTL file. Fix any interface mismatches. Treat the original "
                f"specification as the source of truth for port names, widths, hierarchy, "
                f"and connection intent.\n\n"
                f"## Original Specification\n{state.get('spec', '')}\n\n"
                f"## Individual Submodule Code:\n```\n{accumulated}\n```"
            )
        ),
    ]

    response = llm.invoke(messages)
    composed_code = response.content

    return {
        "rtl_code": composed_code,
        "status": "reviewing",
        "messages": [
            AIMessage(
                content=f"🔗 **Composed Design** — All submodules integrated\n\n{composed_code}",
                name="Composer",
            )
        ],
    }


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# REVIEWER NODE
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def reviewer_node(state: AgentState) -> dict:
    """
    Reviews the generated RTL code against the spec.
    Decides: PASS (code is good) or REVISE (send back to coder).
    """
    llm = _get_llm(temperature=0.1)

    messages = [
        SystemMessage(content=REVIEWER_SYSTEM_PROMPT),
        HumanMessage(
            content=(
                f"Review the following RTL code against the specification.\n\n"
                f"## Specification\n{state['spec']}\n\n"
                f"## Code\n{state['rtl_code']}\n\n"
                f"Current revision count: {state.get('revision_count', 0)} of max 3."
            )
        ),
    ]

    response = llm.invoke(messages)
    review = response.content

    # Parse the decision
    review_upper = review.upper()
    if "### DECISION: PASS" in review_upper or "DECISION: PASS" in review_upper:
        decision = "pass"
    elif "### DECISION: REVISE" in review_upper or "DECISION: REVISE" in review_upper:
        decision = "revise"
    else:
        decision = "pass"

    revision_count = state.get("revision_count", 0)

    # Force pass after max revisions
    if decision == "revise" and revision_count >= 3:
        decision = "pass"
        review += "\n\n⚠️ Max revision limit reached. Accepting current code."

    new_revision_count = revision_count + 1 if decision == "revise" else revision_count
    status = "complete" if decision == "pass" else "coding"

    status_emoji = "✅" if decision == "pass" else "🔄"
    return {
        "review": review,
        "review_decision": decision,
        "revision_count": new_revision_count,
        "status": status,
        "messages": [
            AIMessage(
                content=f"{status_emoji} **Code Review**\n\n{review}",
                name="Reviewer",
            )
        ],
    }
