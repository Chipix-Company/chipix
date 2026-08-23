"""DTOs for codebase graph build and API responses."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


class BuildStatus(str, Enum):
    PENDING = "pending"
    BUILDING = "building"
    READY = "ready"
    ERROR = "error"


@dataclass
class CodebaseGraphStatus:
    project_id: str
    status: str
    trigger: Optional[str] = None
    progress: Optional[str] = None
    node_count: int = 0
    edge_count: int = 0
    built_at: Optional[str] = None
    error: Optional[str] = None
    report_excerpt: Optional[str] = None
    html_url: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "project_id": self.project_id,
            "status": self.status,
            "trigger": self.trigger,
            "progress": self.progress,
            "node_count": self.node_count,
            "edge_count": self.edge_count,
            "built_at": self.built_at,
            "error": self.error,
            "report_excerpt": self.report_excerpt,
            "html_url": self.html_url,
        }


@dataclass
class WorkerResult:
    name: str
    nodes: list[dict[str, Any]] = field(default_factory=list)
    edges: list[dict[str, Any]] = field(default_factory=list)
    error: Optional[str] = None
