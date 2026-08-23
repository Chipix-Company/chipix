"""Query helpers wrapping graphify serve logic."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from services.codebase_graph.store import get_codebase_graph_dir, load_graph_json


def _load_nx_graph(graph_dir: Path):
    from graphify.affected import load_graph

    path = graph_dir / "graph.json"
    if not path.exists():
        return None
    return load_graph(path)


def query_codebase_graph(
    organization_id: str,
    project_id: str,
    question: str,
    *,
    depth: int = 3,
    mode: str = "bfs",
) -> dict[str, Any]:
    graph_dir = get_codebase_graph_dir(organization_id, project_id)
    if not (graph_dir / "graph.json").exists():
        return {"status": "not_ready", "answer": "Codebase graph not built yet."}

    from graphify.serve import _query_graph_text

    G = _load_nx_graph(graph_dir)
    if G is None:
        return {"status": "error", "answer": "Failed to load graph."}

    answer = _query_graph_text(G, question, mode=mode, depth=depth)
    return {
        "status": "ok",
        "question": question,
        "answer": answer,
        "node_count": G.number_of_nodes(),
        "edge_count": G.number_of_edges(),
    }


def get_god_nodes(organization_id: str, project_id: str, *, limit: int = 12) -> dict[str, Any]:
    graph_dir = get_codebase_graph_dir(organization_id, project_id)
    data = load_graph_json(graph_dir)
    if not data:
        return {"status": "not_ready", "nodes": []}

    G = _load_nx_graph(graph_dir)
    if G is None:
        return {"status": "error", "nodes": []}

    from graphify.analyze import god_nodes

    gods = god_nodes(G)[:limit]
    return {"status": "ok", "nodes": gods}


def get_module_neighbors(
    organization_id: str,
    project_id: str,
    module_name: str,
    *,
    hops: int = 1,
) -> dict[str, Any]:
    graph_dir = get_codebase_graph_dir(organization_id, project_id)
    G = _load_nx_graph(graph_dir)
    if G is None:
        return {"status": "not_ready", "neighbors": []}

    from graphify.serve import _find_node

    seeds = _find_node(G, module_name)
    if not seeds:
        return {"status": "ok", "neighbors": [], "message": f"No node matching {module_name!r}"}

    visited = set(seeds)
    frontier = list(seeds)
    for _ in range(max(1, hops)):
        next_frontier = []
        for nid in frontier:
            for neighbor in G.neighbors(nid):
                if neighbor not in visited:
                    visited.add(neighbor)
                    next_frontier.append(neighbor)
        frontier = next_frontier

    neighbors = [
        {
            "id": nid,
            "label": G.nodes[nid].get("label", nid),
            "file_type": G.nodes[nid].get("file_type"),
        }
        for nid in sorted(visited - set(seeds))
    ]
    return {"status": "ok", "module": module_name, "neighbors": neighbors}


def get_affected_by_change(
    organization_id: str,
    project_id: str,
    node_label: str,
) -> dict[str, Any]:
    graph_dir = get_codebase_graph_dir(organization_id, project_id)
    path = graph_dir / "graph.json"
    if not path.exists():
        return {"status": "not_ready", "affected": []}

    try:
        from graphify.affected import format_affected, load_graph

        G = load_graph(path)
        text = format_affected(
            G,
            node_label,
            relations=(
                "instantiates",
                "requires",
                "warns_about",
                "has_port",
                "defines",
                "contains",
                "imports_from",
            ),
            depth=3,
        )
        return {"status": "ok", "label": node_label, "affected": text}
    except Exception as exc:
        return {"status": "error", "error": str(exc), "affected": ""}
