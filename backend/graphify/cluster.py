"""Deterministic community detection and cohesion scoring."""

from __future__ import annotations

from typing import Hashable

import networkx as nx


def cluster(graph: nx.Graph) -> dict[int, list[Hashable]]:
    if graph.number_of_nodes() == 0:
        return {}
    simple = nx.Graph(graph)
    if simple.number_of_edges() == 0:
        groups = [{node} for node in sorted(simple.nodes, key=str)]
    else:
        groups = list(nx.community.greedy_modularity_communities(simple))
    return {
        index: sorted(group, key=str)
        for index, group in enumerate(sorted(groups, key=lambda item: (-len(item), min(map(str, item)))))
    }


def score_all(
    graph: nx.Graph, communities: dict[int, list[Hashable]]
) -> dict[int, float]:
    simple = nx.Graph(graph)
    scores: dict[int, float] = {}
    for community_id, members in communities.items():
        member_set = set(members)
        possible = len(member_set) * (len(member_set) - 1) / 2
        internal = simple.subgraph(member_set).number_of_edges()
        scores[community_id] = round(internal / possible, 4) if possible else 1.0
    return scores
