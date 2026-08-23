"""Load exported graphs and calculate change impact."""

from __future__ import annotations

import json
from collections import deque
from pathlib import Path
from typing import Iterable

import networkx as nx

from graphify.build import build_from_json


def load_graph(path: str | Path) -> nx.MultiDiGraph:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    return build_from_json(payload, root=str(payload.get("graph", {}).get("root", "")))


def _matches(graph: nx.Graph, label: str) -> list:
    query = label.strip().lower()
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


def _edge_relations(graph: nx.Graph, source, target) -> set[str]:
    data = graph.get_edge_data(source, target, default={})
    if graph.is_multigraph():
        return {str(attrs.get("relation", "related_to")) for attrs in data.values()}
    return {str(data.get("relation", "related_to"))} if data else set()


def format_affected(
    graph: nx.Graph,
    node_label: str,
    *,
    relations: Iterable[str] = (),
    depth: int = 3,
) -> str:
    seeds = _matches(graph, node_label)
    if not seeds:
        return f"No graph node matches {node_label!r}."
    allowed = set(relations)
    visited = set(seeds)
    queue = deque((node_id, 0, "selected") for node_id in seeds)
    results: list[tuple[int, object, str]] = []

    while queue:
        current, distance, via = queue.popleft()
        if distance >= max(1, depth):
            continue
        neighbors = set(graph.successors(current)) | set(graph.predecessors(current))
        for neighbor in sorted(neighbors, key=str):
            if neighbor in visited:
                continue
            edge_relations = _edge_relations(graph, current, neighbor) | _edge_relations(
                graph, neighbor, current
            )
            if allowed and edge_relations.isdisjoint(allowed):
                continue
            relation = sorted(edge_relations)[0] if edge_relations else via
            visited.add(neighbor)
            results.append((distance + 1, neighbor, relation))
            queue.append((neighbor, distance + 1, relation))

    if not results:
        return f"No affected nodes found for {node_label!r}."
    lines = [f"Affected nodes for {node_label!r}:"]
    for distance, node_id, relation in results:
        label = graph.nodes[node_id].get("label", str(node_id))
        lines.append(f"- depth {distance}: {label} ({relation})")
    return "\n".join(lines)
