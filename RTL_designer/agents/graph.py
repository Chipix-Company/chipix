"""
LangGraph StateGraph definition for the RTL Design Agent.

Supports two flows:

  SIMPLE (< 500 lines):
    START → planner → coder → reviewer → (pass→END, revise→coder)

  HIERARCHICAL (500+ lines):
    START → planner → decomposer → coder ─loop─→ composer → reviewer → (pass→END, revise→coder)
                                     ↑                                          |
                                     └── (more submodules? loop back) ──────────┘
"""

from __future__ import annotations
from langgraph.graph import StateGraph, END, START

from .state import AgentState
from .nodes import planner_node, decomposer_node, coder_node, composer_node, reviewer_node


def _route_after_planner(state: AgentState) -> str:
    """Route to decomposer for large designs, directly to coder for small ones."""
    if state.get("is_hierarchical", False):
        return "decomposer"
    return "coder"


def _route_after_coder(state: AgentState) -> str:
    """
    For hierarchical designs: loop back to coder if more submodules remain,
    or go to composer when all are done.
    For simple designs: go straight to reviewer.
    """
    if state.get("is_hierarchical", False):
        idx = state.get("current_submodule_idx", 0)
        specs = state.get("submodule_specs", [])
        if idx < len(specs):
            # More submodules to generate — loop back
            return "coder"
        else:
            # All submodules done — compose them
            return "composer"
    # Simple design — go to reviewer
    return "reviewer"


def _route_after_review(state: AgentState) -> str:
    """Conditional edge: route to END if passed, or back to coder for revisions."""
    if state.get("review_decision") == "revise":
        return "coder"
    return END


def build_rtl_agent_graph() -> StateGraph:
    """
    Constructs and compiles the RTL Design Agent graph with both
    simple and hierarchical design support.
    """
    graph = StateGraph(AgentState)

    # ── Add all nodes ─────────────────────────────────────────────
    graph.add_node("planner", planner_node)
    graph.add_node("decomposer", decomposer_node)
    graph.add_node("coder", coder_node)
    graph.add_node("composer", composer_node)
    graph.add_node("reviewer", reviewer_node)

    # ── Edges ─────────────────────────────────────────────────────
    # Start → Planner
    graph.add_edge(START, "planner")

    # Planner → (Decomposer OR Coder) based on complexity
    graph.add_conditional_edges(
        "planner",
        _route_after_planner,
        {
            "decomposer": "decomposer",
            "coder": "coder",
        },
    )

    # Decomposer → Coder (start generating submodules)
    graph.add_edge("decomposer", "coder")

    # Coder → (loop back to Coder for next submodule | Composer | Reviewer)
    graph.add_conditional_edges(
        "coder",
        _route_after_coder,
        {
            "coder": "coder",
            "composer": "composer",
            "reviewer": "reviewer",
        },
    )

    # Composer → Reviewer
    graph.add_edge("composer", "reviewer")

    # Reviewer → (END | Coder for revisions)
    graph.add_conditional_edges(
        "reviewer",
        _route_after_review,
        {
            "coder": "coder",
            END: END,
        },
    )

    return graph.compile()


# Note: do NOT build the graph at module level — LangChain/Google auth
# is initialised lazily inside _run_design_pipeline() at runtime.
