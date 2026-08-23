"""Build NetworkX graphs from extracted Graphify-compatible JSON."""

from __future__ import annotations

from typing import Any

import networkx as nx


def build_from_json(data: dict[str, Any], *, root: str = "") -> nx.MultiDiGraph:
    graph = nx.MultiDiGraph(root=root)
    for node in data.get("nodes") or []:
        if not isinstance(node, dict):
            continue
        node_id = str(node.get("id") or "").strip()
        if not node_id:
            continue
        attrs = {key: value for key, value in node.items() if key != "id"}
        graph.add_node(node_id, **attrs)

    for edge in data.get("edges") or data.get("links") or []:
        if not isinstance(edge, dict):
            continue
        source = str(edge.get("source") or "").strip()
        target = str(edge.get("target") or "").strip()
        if not source or not target:
            continue
        if source not in graph:
            graph.add_node(source, label=source, file_type="unknown")
        if target not in graph:
            graph.add_node(target, label=target, file_type="unknown")
        attrs = {
            key: value
            for key, value in edge.items()
            if key not in {"source", "target", "key"}
        }
        graph.add_edge(source, target, **attrs)
    return graph
