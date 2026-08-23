"""
Mental Model Tools — Agent tool handlers for mental model operations.

These wrap the mental model service (builder, validator) as tool handlers
that the agentic loop can call via native function calling.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, Optional

from agent_tools import register_tool
from services.mental_model.builder import (
    build_mental_model,
    scan_project_folder,
)
from services.mental_model.validator import validate_mental_model

logger = logging.getLogger(__name__)


async def handle_scan_project(
    project_id: str,
    db_session: Any = None,
    **kwargs: Any,
) -> str:
    """Handle scanProject tool call."""
    # Resolve project root path from DB or kwargs
    root_path = kwargs.get("root_path", "")
    if not root_path and db_session:
        root_path = await _resolve_project_path(project_id, db_session)

    if not root_path:
        return json.dumps({"error": "Could not resolve project path"})

    try:
        scan = scan_project_folder(root_path)
        return json.dumps({
            "status": "success",
            "root_path": scan.root_path,
            "total_files": scan.total_files,
            "total_lines": scan.total_lines,
            "rtl_files": scan.rtl_files[:50],  # Cap for context window
            "spec_files": scan.spec_files,
            "testbench_files": scan.testbench_files[:20],
            "uvm_files": scan.uvm_files[:20],
            "formal_files": scan.formal_files[:10],
            "file_types": scan.file_types,
            "estimated_complexity": scan.estimated_complexity,
        }, indent=2)
    except FileNotFoundError as e:
        return json.dumps({"error": str(e)})


async def handle_build_mental_model(
    project_id: str,
    target_module: str = "",
    db_session: Any = None,
    ai_client: Any = None,
    **kwargs: Any,
) -> str:
    """Handle buildMentalModel tool call."""
    root_path = kwargs.get("root_path", "")
    if not root_path and db_session:
        root_path = await _resolve_project_path(project_id, db_session)

    spec_text = kwargs.get("spec_text", "")
    if not spec_text and db_session:
        spec_text = await _load_spec_text(project_id, db_session)

    if not root_path:
        return json.dumps({"error": "Could not resolve project path"})

    try:
        model = await build_mental_model(
            root_path=root_path,
            project_id=project_id,
            spec_text=spec_text,
            target_module=target_module or None,
            ai_client=ai_client,
        )

        # Validate
        validation = validate_mental_model(model)

        # Persist to DB if session available
        if db_session:
            await _persist_mental_model(model, project_id, db_session)

        return json.dumps({
            "status": "success",
            "summary": model.summary,
            "top_module": model.design.top_module,
            "modules": model.design.modules[:30],
            "ports_count": len(model.design.ports),
            "requirements_count": len(model.requirements),
            "unit_tests_planned": len(model.verification.unit_tests),
            "formal_properties_planned": len(model.verification.formal_properties),
            "coverage_points_planned": len(model.verification.coverage_points),
            "open_questions": [
                {"question": q.question, "blocking": q.blocking}
                for q in model.unanswered_questions[:10]
            ],
            "risks": model.risks[:10],
            "validation": validation.summary,
            "validation_warnings": validation.warnings[:10],
        }, indent=2)

    except Exception as e:
        logger.exception(f"Mental model build failed: {e}")
        return json.dumps({"error": f"Build failed: {str(e)}"})


async def handle_query_mental_model(
    project_id: str,
    query: str = "summary",
    db_session: Any = None,
    **kwargs: Any,
) -> str:
    """Handle queryMentalModel tool call."""
    model = kwargs.get("mental_model")
    if not model and db_session:
        model = await _load_mental_model(project_id, db_session)

    if not model:
        return json.dumps({
            "error": "No mental model found. Run buildMentalModel first."
        })

    q = query.lower()

    if q == "summary":
        return json.dumps({
            "summary": model.summary,
            "top_module": model.design.top_module,
            "revision": model.revision,
            "modules": model.design.modules,
            "requirements_count": len(model.requirements),
            "open_questions_count": len(model.unanswered_questions),
        }, indent=2)

    elif q == "requirements":
        return json.dumps({
            "requirements": [
                {
                    "id": r.id,
                    "text": r.text,
                    "priority": r.priority,
                    "category": r.category,
                    "verified_by": r.verified_by,
                }
                for r in model.requirements
            ]
        }, indent=2)

    elif q == "ports":
        return json.dumps({
            "top_module": model.design.top_module,
            "ports": [
                {
                    "name": p.name,
                    "direction": p.direction,
                    "width": p.width,
                    "bus_range": p.bus_range,
                    "description": p.description,
                    "protocol_role": p.protocol_role,
                }
                for p in model.design.ports
            ]
        }, indent=2)

    elif q == "hierarchy":
        return json.dumps({
            "top_module": model.design.top_module,
            "hierarchy_tree": model.design.hierarchy_tree,
            "sub_instances": [
                {"instance": s.instance_name, "module": s.module_name}
                for s in model.design.sub_instances
            ],
        }, indent=2)

    elif q == "verification_plan":
        return json.dumps({
            "unit_tests": [
                {"id": t.id, "name": t.name, "status": t.status}
                for t in model.verification.unit_tests
            ],
            "formal_properties": [
                {"id": f.id, "name": f.name, "type": f.property_type, "status": f.status}
                for f in model.verification.formal_properties
            ],
            "coverage_points": [
                {"id": c.id, "name": c.name, "signal": c.signal}
                for c in model.verification.coverage_points
            ],
        }, indent=2)

    elif q == "open_questions":
        return json.dumps({
            "open_questions": [
                {
                    "id": oq.id,
                    "question": oq.question,
                    "blocking": oq.blocking,
                    "answered": oq.answered,
                }
                for oq in model.open_questions
            ]
        }, indent=2)

    else:
        return json.dumps({"error": f"Unknown query type: {query}"})


# ═══════════════════════════════════════════════════════════════════════
# DB Helpers (stubs — integrate with actual DB session)
# ═══════════════════════════════════════════════════════════════════════


async def _resolve_project_path(project_id: str, db_session: Any) -> str:
    """Resolve the filesystem path for a project from DB."""
    try:
        artifact = _active_or_latest_artifact(project_id, "rtl", db_session)
        if artifact:
            from services.mental_model.store import _rtl_root_path

            return _rtl_root_path(artifact)
    except Exception as e:
        logger.warning(f"Could not resolve project path: {e}")
    return ""


async def _load_spec_text(project_id: str, db_session: Any) -> str:
    """Load specification text for a project from DB artifacts."""
    try:
        artifact = _active_or_latest_artifact(project_id, "spec", db_session)
        if artifact:
            from services.mental_model.store import _read_spec_text

            return _read_spec_text(artifact)
    except Exception as e:
        logger.warning(f"Could not load spec: {e}")
    return ""


def _active_or_latest_artifact(
    project_id: str,
    artifact_type: str,
    db_session: Any,
) -> Any:
    """Return the active artifact when selected, otherwise the latest revision."""
    from database.models import ProjectArtifact, ProjectArtifactPointer

    pointer = (
        db_session.query(ProjectArtifactPointer)
        .filter(ProjectArtifactPointer.project_id == project_id)
        .first()
    )
    active_id = None
    if pointer and artifact_type == "rtl":
        active_id = pointer.active_rtl_artifact_id
    elif pointer and artifact_type == "spec":
        active_id = pointer.active_spec_artifact_id

    if active_id:
        artifact = (
            db_session.query(ProjectArtifact)
            .filter(
                ProjectArtifact.id == active_id,
                ProjectArtifact.project_id == project_id,
                ProjectArtifact.artifact_type == artifact_type,
            )
            .first()
        )
        if artifact:
            return artifact

    return (
        db_session.query(ProjectArtifact)
        .filter(
            ProjectArtifact.project_id == project_id,
            ProjectArtifact.artifact_type == artifact_type,
        )
        .order_by(ProjectArtifact.revision.desc())
        .first()
    )


async def _persist_mental_model(
    model: Any, project_id: str, db_session: Any
) -> None:
    """Save mental model revision to DB."""
    try:
        from database.models import MentalModelRevision
        revision = MentalModelRevision(
            project_id=project_id,
            organization_id="",  # TODO: resolve from project
            user_id="",  # TODO: resolve from session
            revision=model.revision,
            schema_version=model.schema_version,
            summary_text=model.summary,
            content_json=model.to_json(),
        )
        db_session.add(revision)
        db_session.commit()
        logger.info(f"Persisted mental model rev {model.revision}")
    except Exception as e:
        logger.warning(f"Could not persist mental model: {e}")


async def _load_mental_model(project_id: str, db_session: Any) -> Optional[Any]:
    """Load latest mental model revision from DB."""
    try:
        from sqlalchemy import select
        from database.models import MentalModelRevision
        from services.mental_model.schema import MentalModelSchema
        stmt = select(MentalModelRevision.content_json).where(
            MentalModelRevision.project_id == project_id,
        ).order_by(MentalModelRevision.revision.desc()).limit(1)
        result = db_session.execute(stmt)
        row = result.scalar_one_or_none()
        if row:
            return MentalModelSchema.from_json(row)
    except Exception as e:
        logger.warning(f"Could not load mental model: {e}")
    return None


# ═══════════════════════════════════════════════════════════════════════
# Register handlers
# ═══════════════════════════════════════════════════════════════════════

register_tool("scanProject", handle_scan_project)
register_tool("buildMentalModel", handle_build_mental_model)
register_tool("queryMentalModel", handle_query_mental_model)
