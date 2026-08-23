"""Agent tools for querying the project codebase knowledge graph."""

from __future__ import annotations

import json
import logging
from typing import Any

from agent_tools import register_tool
from services.codebase_graph.manager import get_build_status
from services.codebase_graph.query import (
    get_affected_by_change,
    get_god_nodes,
    get_module_neighbors,
    query_codebase_graph,
)

logger = logging.getLogger(__name__)


async def _org_for_project(project_id: str, db_session: Any) -> str:
    from database.models import Project

    project = db_session.query(Project).filter(Project.id == project_id).first()
    return str(project.organization_id) if project else ""


async def handle_query_codebase_graph(
    project_id: str,
    question: str,
    db_session: Any = None,
    **kwargs: Any,
) -> str:
    org_id = await _org_for_project(project_id, db_session)
    if not org_id:
        return json.dumps({"error": "Project not found"})
    result = query_codebase_graph(org_id, project_id, question)
    return json.dumps(result, indent=2)


async def handle_get_module_neighbors(
    project_id: str,
    module_name: str,
    db_session: Any = None,
    **kwargs: Any,
) -> str:
    org_id = await _org_for_project(project_id, db_session)
    hops = int(kwargs.get("hops") or 1)
    result = get_module_neighbors(org_id, project_id, module_name, hops=hops)
    return json.dumps(result, indent=2)


async def handle_get_affected_by_change(
    project_id: str,
    node_label: str,
    db_session: Any = None,
    **kwargs: Any,
) -> str:
    org_id = await _org_for_project(project_id, db_session)
    result = get_affected_by_change(org_id, project_id, node_label)
    return json.dumps(result, indent=2)


async def handle_get_graph_god_nodes(
    project_id: str,
    db_session: Any = None,
    **kwargs: Any,
) -> str:
    org_id = await _org_for_project(project_id, db_session)
    limit = int(kwargs.get("limit") or 12)
    result = get_god_nodes(org_id, project_id, limit=limit)
    return json.dumps(result, indent=2)


async def handle_get_codebase_graph_status(
    project_id: str,
    db_session: Any = None,
    **kwargs: Any,
) -> str:
    if not db_session:
        return json.dumps({"error": "Database session required"})
    result = get_build_status(db_session, project_id)
    return json.dumps(result, indent=2)


register_tool("queryCodebaseGraph", handle_query_codebase_graph)
register_tool("getModuleNeighbors", handle_get_module_neighbors)
register_tool("getAffectedByChange", handle_get_affected_by_change)
register_tool("getGraphGodNodes", handle_get_graph_god_nodes)
register_tool("getCodebaseGraphStatus", handle_get_codebase_graph_status)
