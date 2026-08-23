"""Cadence Xcelium agent tools — status, simulation, polling, and fix application."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from agent_tools import register_tool

logger = logging.getLogger(__name__)


def _load_project(db_session: Any, project_id: str):
    from database.models import Project

    return db_session.query(Project).filter(Project.id == project_id).first()


async def handle_check_cadence_status(
    db_session: Any = None,
    project_id: str | None = None,
    **kwargs: Any,
) -> str:
    """Return Cadence Xcelium detection and license readiness."""
    _ = (db_session, project_id, kwargs)
    try:
        from simulator_plugins.cadence_config import load_cadence_config
        from simulator_plugins.xcelium import XceliumPlugin

        detection = XceliumPlugin().detect()
        details = (detection.to_dict() or {}).get("details", {}) or {}
        config = load_cadence_config()
        return json.dumps(
            {
                "status": "ok",
                "available": detection.available,
                "license_ready": details.get("license_ready"),
                "detection_status": details.get("status"),
                "path": detection.path,
                "guidance": detection.guidance,
                "config": config,
                "cadence_connected": detection.available and bool(details.get("license_ready", detection.available)),
            },
            indent=2,
        )
    except Exception as exc:
        logger.exception("checkCadenceStatus failed: %s", exc)
        return json.dumps({"status": "error", "error": str(exc), "cadence_connected": False})


async def handle_run_cadence_simulation(
    project_id: str | None = None,
    db_session: Any = None,
    ai_client: Any = None,
    **kwargs: Any,
) -> str:
    """Run full Cadence pipeline on existing generated UVM artifacts."""
    _ = ai_client
    if not db_session:
        return json.dumps({"status": "error", "error": "Database session unavailable."})
    if not project_id:
        return json.dumps(
            {
                "status": "error",
                "error": "No active project in session. Open a project workspace and retry.",
            }
        )

    project = _load_project(db_session, project_id)
    if not project:
        return json.dumps({"status": "error", "error": f"Project {project_id} not found."})

    artifact_ids = kwargs.get("generated_artifact_ids") or kwargs.get("artifact_ids") or []
    if isinstance(artifact_ids, str):
        artifact_ids = [artifact_ids]

    user = kwargs.get("current_user")
    try:
        from services.verification.cadence_run import run_cadence_on_artifacts

        result = await run_cadence_on_artifacts(
            db_session,
            project=project,
            user=user,
            generated_artifact_ids=list(artifact_ids) if artifact_ids else None,
            top_module=str(
                kwargs.get("top_module")
                or kwargs.get("uvm_top_module")
                or "top_tb"
            ),
            uvm_testname=str(kwargs.get("uvm_testname") or ""),
            timeout_seconds=int(kwargs.get("timeout_seconds") or 300),
            auto_repair_generated=bool(kwargs.get("auto_repair", True)),
            max_repair_rounds=int(kwargs.get("max_repair_rounds") or 3),
        )
        if result.get("error"):
            return json.dumps(result, indent=2)
        compact = {
            "status": result.get("status"),
            "run_id": result.get("run_id"),
            "cadence_connected": result.get("cadence_connected"),
            "phases": (result.get("phases") or [])[-6:],
            "next_step": result.get("next_step") or "",
        }
        feedback = result.get("feedback_memory") or {}
        if isinstance(feedback, dict) and feedback.get("root_causes"):
            compact["feedback_memory"] = {
                "root_causes": (feedback.get("root_causes") or [])[:8],
            }
        analysis = result.get("analysis") or {}
        if isinstance(analysis, dict) and analysis:
            compact["analysis"] = {
                k: analysis[k]
                for k in ("verdict", "summary", "failure_phase", "error_count")
                if k in analysis
            }
        closure = result.get("closure_report") or {}
        if isinstance(closure, dict) and closure:
            compact["closure_report"] = {
                k: closure[k]
                for k in ("overall_status", "summary", "passed", "failed")
                if k in closure
            }
        repair_history = result.get("repair_history") or []
        if repair_history:
            compact["repair_history"] = repair_history[-3:]
        compact["generated_artifact_ids"] = result.get("generated_artifact_ids") or []
        compact["_note"] = "Use getCadenceRunStatus(run_id) for full simulator logs."
        return json.dumps(compact, indent=2)
    except Exception as exc:
        logger.exception("runCadenceSimulation failed: %s", exc)
        return json.dumps({"status": "error", "error": str(exc), "cadence_connected": False})


async def handle_get_cadence_run_status(
    project_id: str | None = None,
    db_session: Any = None,
    **kwargs: Any,
) -> str:
    """Poll a Cadence simulator run by run_id."""
    run_id = str(kwargs.get("run_id") or "").strip()
    if not run_id:
        return json.dumps({"status": "error", "error": "run_id is required."})
    if not db_session:
        return json.dumps({"status": "error", "error": "Database session unavailable."})
    if not project_id:
        return json.dumps(
            {
                "status": "error",
                "error": "No active project in session. Open a project workspace and retry.",
            }
        )

    project = _load_project(db_session, project_id)
    if not project:
        return json.dumps({"status": "error", "error": f"Project {project_id} not found."})

    try:
        from services.verification.cadence_run import build_run_status_payload, load_cadence_run

        run = load_cadence_run(project, run_id)
        payload = build_run_status_payload(run, run_id=run_id)
        payload["status"] = "ok"
        return json.dumps(payload, indent=2)
    except FileNotFoundError:
        return json.dumps({"status": "error", "error": f"Cadence run {run_id} not found."})
    except Exception as exc:
        logger.exception("getCadenceRunStatus failed: %s", exc)
        return json.dumps({"status": "error", "error": str(exc)})


async def handle_apply_cadence_fixes(
    project_id: str | None = None,
    db_session: Any = None,
    **kwargs: Any,
) -> str:
    """Apply sandbox repair diffs back to project generated artifacts."""
    if not db_session:
        return json.dumps({"status": "error", "error": "Database session unavailable."})
    if not project_id:
        return json.dumps(
            {
                "status": "error",
                "error": "No active project in session. Open a project workspace and retry.",
            }
        )

    project = _load_project(db_session, project_id)
    if not project:
        return json.dumps({"status": "error", "error": f"Project {project_id} not found."})

    run_id = str(kwargs.get("run_id") or "").strip()
    repair_history = kwargs.get("repair_history")
    explicit_fixes = kwargs.get("fixes") or []

    try:
        if repair_history is None and run_id:
            from services.verification.cadence_run import load_cadence_run

            run = load_cadence_run(project, run_id)
            repair_history = run.get("repair_history") or []

        from database.models import ProjectArtifact
        from services.verification.cadence_run import resolve_generated_artifacts

        artifacts = resolve_generated_artifacts(db_session, project, None)
        by_name = {Path(a.filename or "").name: a for a in artifacts}
        applied: list[dict[str, Any]] = []

        repairs_to_apply: list[dict[str, Any]] = []
        if isinstance(explicit_fixes, list) and explicit_fixes:
            repairs_to_apply = explicit_fixes
        elif isinstance(repair_history, list):
            for round_item in repair_history:
                for repair in round_item.get("repairs") or []:
                    repairs_to_apply.append(repair)

        for repair in repairs_to_apply:
            filename = Path(
                str(repair.get("filename") or repair.get("file") or repair.get("path") or "")
            ).name
            if not filename:
                continue
            artifact = by_name.get(filename)
            if not artifact:
                continue
            content = repair.get("proposed_content") or repair.get("content")
            if not content and repair.get("file"):
                sandbox_path = Path(str(repair["file"]))
                if sandbox_path.exists():
                    content = sandbox_path.read_text(encoding="utf-8", errors="replace")
            if not content:
                continue
            file_path = Path(artifact.file_path or "")
            if not file_path.exists():
                continue
            file_path.write_text(str(content), encoding="utf-8")
            applied.append(
                {
                    "artifact_id": artifact.id,
                    "filename": filename,
                    "reason": repair.get("reason") or "",
                }
            )

        return json.dumps(
            {
                "status": "applied" if applied else "no_changes",
                "applied_count": len(applied),
                "applied": applied,
                "message": (
                    f"Applied {len(applied)} fix(es) to generated artifacts."
                    if applied
                    else "No matching generated artifacts were updated."
                ),
            },
            indent=2,
        )
    except Exception as exc:
        logger.exception("applyCadenceFixes failed: %s", exc)
        return json.dumps({"status": "error", "error": str(exc)})


register_tool("checkCadenceStatus", handle_check_cadence_status)
register_tool("runCadenceSimulation", handle_run_cadence_simulation)
register_tool("getCadenceRunStatus", handle_get_cadence_run_status)
register_tool("applyCadenceFixes", handle_apply_cadence_fixes)
