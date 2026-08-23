"""Text query helpers for codebase graphs."""

from __future__ import annotations

import re

import networkx as nx


_STOP_WORDS = {
    "a",
    "about",
    "and",
    "does",
    "for",
    "how",
    "in",
    "is",
    "of",
    "on",
    "the",
    "to",
    "what",
    "where",
    "which",
    "who",
}


def _find_node(graph: nx.Graph, label: str) -> list:
    query = label.strip().lower()
    if not query:
        return []
    exact = [
        node_id
        for node_id, attrs in graph.nodes(data=True)
        if str(node_id).lower() == query or str(attrs.get("label", "")).lower() == query
    ]
    if exact:
        return exact
    return [
        node_id
        for node_id, attrs in graph.nodes(data=True)
        if query in str(node_id).lower() or query in str(attrs.get("label", "")).lower()
    ][:8]


def _relation(graph: nx.Graph, source, target) -> str:
    data = graph.get_edge_data(source, target, default={})
    if graph.is_multigraph() and data:
        first = next(iter(data.values()))
        return str(first.get("relation", "related_to"))
    return str(data.get("relation", "related_to")) if data else "related_to"


def _query_graph_text(
    graph: nx.Graph,
    question: str,
    *,
    mode: str = "bfs",
    depth: int = 3,
) -> str:
    del mode
    direct = _find_node(graph, question)
    terms = [
        term
        for term in re.findall(r"[A-Za-z_][A-Za-z0-9_]*", question.lower())
        if term not in _STOP_WORDS and len(term) > 2
    ]
    seeds = direct
    if not seeds:
        for term in sorted(terms, key=len, reverse=True):
            seeds = _find_node(graph, term)
            if seeds:
                break
    if not seeds:
        return "No matching node was found in the codebase graph."

    lines: list[str] = []
    visited = set(seeds)
    frontier = list(seeds)
    for seed in seeds[:3]:
        attrs = graph.nodes[seed]
        lines.append(
            f"{attrs.get('label', seed)}"
            + (f" [{attrs.get('source_file')}]" if attrs.get("source_file") else "")
        )
    for _ in range(max(1, min(depth, 5))):
        next_frontier = []
        for current in frontier:
            neighbors = set(graph.successors(current)) | set(graph.predecessors(current))
            for neighbor in sorted(neighbors, key=str):
                if neighbor in visited:
                    continue
                visited.add(neighbor)
                next_frontier.append(neighbor)
                relation = _relation(graph, current, neighbor)
                if relation == "related_to":
                    relation = _relation(graph, neighbor, current)
                lines.append(
                    f"- {relation}: {graph.nodes[neighbor].get('label', neighbor)}"
                )
                if len(lines) >= 40:
                    return "\n".join(lines)
        frontier = next_frontier
        if not frontier:
            break
    return "\n".join(lines)
