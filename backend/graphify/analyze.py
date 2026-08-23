"""Graph analysis helpers used by reports and API responses."""

from __future__ import annotations

from typing import Any, Hashable

import networkx as nx


def god_nodes(graph: nx.Graph, *, limit: int = 20) -> list[dict[str, Any]]:
    ranked = sorted(graph.degree, key=lambda item: (-item[1], str(item[0])))[:limit]
    return [
        {
            "id": node_id,
            "label": graph.nodes[node_id].get("label", str(node_id)),
            "degree": degree,
            "source_file": graph.nodes[node_id].get("source_file", ""),
        }
        for node_id, degree in ranked
    ]


def _membership(communities: dict[int, list[Hashable]]) -> dict[Hashable, int]:
    return {
        node_id: community_id
        for community_id, members in communities.items()
        for node_id in members
    }


def surprising_connections(
    graph: nx.Graph,
    communities: dict[int, list[Hashable]],
    *,
    limit: int = 12,
) -> list[dict[str, Any]]:
    membership = _membership(communities)
    surprises: list[dict[str, Any]] = []
    for source, target, data in graph.edges(data=True):
        if membership.get(source) == membership.get(target):
            continue
        surprises.append(
            {
                "source": graph.nodes[source].get("label", str(source)),
                "target": graph.nodes[target].get("label", str(target)),
                "relation": data.get("relation", "related_to"),
                "confidence": data.get("confidence", "EXTRACTED"),
            }
        )
        if len(surprises) >= limit:
            break
    return surprises


def suggest_questions(
    graph: nx.Graph,
    communities: dict[int, list[Hashable]],
    community_labels: dict[int, str],
) -> list[str]:
    questions = [f"What depends on {hub['label']}?" for hub in god_nodes(graph, limit=5)]
    for community_id, members in list(communities.items())[:3]:
        if members:
            label = community_labels.get(community_id, f"Community {community_id}")
            questions.append(f"How does {label} work?")
    return questions
