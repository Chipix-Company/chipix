"""Filesystem persistence for per-project codebase graphs."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from common.paths import outputs_dir
from typing import Any, Optional

from services.codebase_graph.schemas import BuildStatus, CodebaseGraphStatus

_STATUS_FILE = "status.json"
_BUILD_LOG = "build_log.jsonl"


def _outputs_dir() -> Path:
    return outputs_dir()


def get_codebase_graph_dir(organization_id: str, project_id: str) -> Path:
    return (
        _outputs_dir()
        / "orgs"
        / organization_id
        / "projects"
        / project_id
        / "codebase_graph"
    )


def read_status(graph_dir: Path) -> dict[str, Any]:
    path = graph_dir / _STATUS_FILE
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def write_status(graph_dir: Path, payload: dict[str, Any]) -> None:
    graph_dir.mkdir(parents=True, exist_ok=True)
    (graph_dir / _STATUS_FILE).write_text(
        json.dumps(payload, indent=2),
        encoding="utf-8",
    )


def append_build_log(graph_dir: Path, entry: dict[str, Any]) -> None:
    graph_dir.mkdir(parents=True, exist_ok=True)
    line = json.dumps({**entry, "ts": datetime.now(timezone.utc).isoformat()})
    with (graph_dir / _BUILD_LOG).open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def load_graph_json(graph_dir: Path) -> Optional[dict[str, Any]]:
    path = graph_dir / "graph.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def read_report(graph_dir: Path, *, max_chars: int = 8000) -> Optional[str]:
    path = graph_dir / "GRAPH_REPORT.md"
    if not path.exists():
        return None
    text = path.read_text(encoding="utf-8", errors="replace")
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "\n\n…"


def report_excerpt(graph_dir: Path, *, max_chars: int = 2400) -> Optional[str]:
    text = read_report(graph_dir, max_chars=max_chars)
    return text


def build_status_dto(
    project_id: str,
    graph_dir: Path,
    *,
    api_base: str = "/api/v1",
) -> CodebaseGraphStatus:
    raw = read_status(graph_dir)
    graph = load_graph_json(graph_dir)
    node_count = len((graph or {}).get("nodes") or [])
    edge_count = len((graph or {}).get("links") or (graph or {}).get("edges") or [])

    status = raw.get("status") or (BuildStatus.READY.value if graph else BuildStatus.PENDING.value)
    html_url = None
    if (graph_dir / "graph.html").exists():
        html_url = f"{api_base}/projects/{project_id}/codebase-graph/html"

    return CodebaseGraphStatus(
        project_id=project_id,
        status=status,
        trigger=raw.get("trigger"),
        progress=raw.get("progress"),
        node_count=node_count,
        edge_count=edge_count,
        built_at=raw.get("built_at"),
        error=raw.get("error"),
        report_excerpt=report_excerpt(graph_dir) if status == BuildStatus.READY.value else None,
        html_url=html_url,
    )
