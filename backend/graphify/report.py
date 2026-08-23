"""Markdown reporting for codebase graphs."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Hashable

import networkx as nx


def generate(
    graph: nx.Graph,
    communities: dict[int, list[Hashable]],
    cohesion: dict[int, float],
    community_labels: dict[int, str],
    gods: list[dict[str, Any]],
    surprises: list[dict[str, Any]],
    detection: dict[str, Any],
    *,
    token_cost: dict[str, Any] | None = None,
    root: str = "",
    suggested_questions: list[str] | None = None,
) -> str:
    del token_cost
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [
        f"# Codebase Graph Report ({timestamp})",
        "",
        "## Summary",
        f"- Root: `{root}`",
        f"- {graph.number_of_nodes()} nodes",
        f"- {graph.number_of_edges()} edges",
        f"- {len(communities)} communities",
        f"- {detection.get('file_count', 0)} RTL files detected",
        "",
        "## Most Connected Nodes",
    ]
    if gods:
        for index, node in enumerate(gods, start=1):
            lines.append(f"{index}. `{node['label']}` - {node['degree']} edges")
    else:
        lines.append("No connected nodes were found.")

    lines.extend(["", "## Communities"])
    for community_id, members in communities.items():
        labels = [str(graph.nodes[node].get("label", node)) for node in members[:12]]
        suffix = f" (+{len(members) - 12} more)" if len(members) > 12 else ""
        lines.extend(
            [
                "",
                f"### {community_labels.get(community_id, f'Community {community_id}')}",
                f"Cohesion: {cohesion.get(community_id, 0):.2f}",
                f"Nodes ({len(members)}): {', '.join(labels)}{suffix}",
            ]
        )

    lines.extend(["", "## Cross-Community Connections"])
    if surprises:
        for item in surprises:
            lines.append(
                f"- `{item['source']}` --{item['relation']}--> `{item['target']}`"
            )
    else:
        lines.append("No cross-community connections were found.")

    lines.extend(["", "## Suggested Questions"])
    for question in suggested_questions or []:
        lines.append(f"- {question}")
    return "\n".join(lines).rstrip() + "\n"
