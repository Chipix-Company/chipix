"""Shared Cadence Xcelium simulation orchestration for REST, agents, and staged flow."""

from __future__ import annotations

import asyncio

import json
from pathlib import Path
from typing import Any, Optional

from sqlalchemy.orm import Session

from database.models import Project, ProjectArtifact, User
from services.mental_model import get_latest_mental_model_revision
from services.mental_model.living_agent import observe_mental_model_event
from services.mental_model.store import _rtl_root_path
from simulator_plugins.base import SimulatorRunRequest
from simulator_plugins.sandbox import create_run_sandbox, verify_rtl_unchanged
from simulator_plugins.xcelium_runner import XceliumRunner


def _artifact_dir(*args, **kwargs):
    from routes.api import _artifact_dir as api_artifact_dir

    return api_artifact_dir(*args, **kwargs)


def _resolve_active_spec_rtl(*args, **kwargs):
    from routes.api import resolve_active_spec_rtl_artifacts

    return resolve_active_spec_rtl_artifacts(*args, **kwargs)


def resolve_generated_artifacts(
    db: Session,
    project: Project,
    artifact_ids: list[str] | None = None,
) -> list[ProjectArtifact]:
    query = db.query(ProjectArtifact).filter(
        ProjectArtifact.project_id == project.id,
        ProjectArtifact.organization_id == project.organization_id,
        ProjectArtifact.artifact_type == "generated",
    )
    if artifact_ids:
        query = query.filter(ProjectArtifact.id.in_(artifact_ids))
    return [
        artifact
        for artifact in query.order_by(ProjectArtifact.revision.desc()).all()
        if Path(artifact.file_path or "").exists()
    ]


def latest_mental_model_content(db: Session, project_id: str) -> dict[str, Any]:
    latest = get_latest_mental_model_revision(db, project_id)
    if not latest:
        return {}
    try:
        return json.loads(latest.content_json or "{}")
    except Exception:
        return {}


def run_dir_for_project(project: Project, run_id: str) -> Path:
    base = _artifact_dir(project.organization_id, project.id, "simulator_runs")
    run_dir = (base / run_id).resolve()
    if not str(run_dir).startswith(str(base.resolve())):
        raise ValueError("Invalid simulator run id.")
    return run_dir


def load_cadence_run(project: Project, run_id: str) -> dict[str, Any]:
    run_dir = run_dir_for_project(project, run_id)
    result_path = run_dir / "results" / "run.json"
    if not result_path.exists():
        raise FileNotFoundError("Simulator run not found.")
    return json.loads(result_path.read_text(encoding="utf-8"))


def write_run_json(run_dir: Path, payload: dict[str, Any]) -> None:
    results_dir = run_dir / "results"
    results_dir.mkdir(parents=True, exist_ok=True)
    (results_dir / "run.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")


def cadence_next_step(status: str) -> str:
    if status == "unavailable":
        return "Xcelium is not available on this machine. Use uploaded logs or run on a Linux host with xrun."
    if status == "passed":
        return "Review coverage and the closure report."
    return (
        "Review evidence, apply generated-collateral repairs, then rerun or upload the next simulator log."
    )


def build_run_status_payload(run: dict[str, Any], *, run_id: str) -> dict[str, Any]:
    """Compact status for polling and agent tools."""

    commands = run.get("commands") or []
    last_phase = commands[-1].get("phase") if commands else "pending"
    log_excerpt = ""
    logs = run.get("logs") or {}
    for key in ("simulation", "elaborate", "compile"):
        path = logs.get(key)
        if path and Path(path).exists():
            log_excerpt = Path(path).read_text(encoding="utf-8", errors="ignore")[-2000:]
            break
    return {
        "run_id": run_id,
        "status": run.get("status"),
        "phase": last_phase,
        "analysis": run.get("analysis") or {},
        "repair_history": run.get("repair_history") or [],
        "closure_report": run.get("closure_report") or {},
        "feedback_memory": (run.get("closure_report") or {}).get("feedback_memory") or {},
        "log_excerpt": log_excerpt,
        "warnings": run.get("warnings") or [],
    }


async def run_cadence_on_artifacts(
    db: Session,
    *,
    project: Project,
    user: User | None = None,
    generated_artifact_ids: list[str] | None = None,
    top_module: str = "top_tb",
    uvm_testname: str = "",
    timeout_seconds: int = 300,
    dry_run: bool = False,
    mock_logs: dict[str, str] | None = None,
    auto_repair_generated: bool = True,
    max_repair_rounds: int = 3,
    observe_mental_model: bool = True,
) -> dict[str, Any]:
    """Run full Xcelium pipeline on existing generated artifacts."""

    _spec_artifact, rtl_artifact = _resolve_active_spec_rtl(
        db,
        project,
        auto_activate=True,
    )
    rtl_root = _rtl_root_path(rtl_artifact) if rtl_artifact else None
    generated_artifacts = resolve_generated_artifacts(db, project, generated_artifact_ids or [])
    if not generated_artifacts and not mock_logs:
        return {
            "status": "error",
            "error": "No generated verification artifacts found. Generate UVM collateral first.",
            "cadence_connected": False,
        }

    base_dir = _artifact_dir(project.organization_id, project.id, "simulator_runs")
    manifest = create_run_sandbox(
        base_dir=base_dir,
        generated_files=[artifact.file_path for artifact in generated_artifacts],
        rtl_root=rtl_root,
    )

    mental_model = latest_mental_model_content(db, project.id)
    run_request = SimulatorRunRequest(
        run_dir=Path(manifest.run_dir),
        filelist=Path(manifest.filelist),
        top_module=top_module or "top_tb",
        uvm_testname=uvm_testname,
        timeout_seconds=timeout_seconds,
        dry_run=dry_run,
        mock_logs=mock_logs or {},
        auto_repair_generated=auto_repair_generated,
        max_repair_rounds=max_repair_rounds,
        project_id=project.id,
        run_id=manifest.run_id,
    )
    result = await asyncio.to_thread(
        XceliumRunner().run,
        run_request,
        mental_model=mental_model,
        rtl_root=rtl_root,
    )
    rtl_integrity = verify_rtl_unchanged(manifest)
    result.closure_report["rtl_integrity"] = rtl_integrity

    observation: Optional[dict[str, Any]] = None
    if observe_mental_model and user is not None:
        observation = await _observe_simulator_result(
            db,
            project=project,
            user=user,
            run_id=manifest.run_id,
            result=result.to_dict(),
            generated_artifact_ids=[artifact.id for artifact in generated_artifacts],
        )
        result.closure_report["mental_model_observation"] = observation

    write_run_json(Path(manifest.run_dir), result.to_dict())
    payload = result.to_dict()
    return {
        "status": payload.get("status"),
        "run_id": manifest.run_id,
        "cadence_connected": payload.get("status") != "unavailable",
        "run": payload,
        "sandbox": manifest.to_dict(),
        "mental_model_observation": observation,
        "analysis": payload.get("analysis") or {},
        "feedback_memory": (payload.get("closure_report") or {}).get("feedback_memory") or {},
        "repair_history": payload.get("repair_history") or [],
        "closure_report": payload.get("closure_report") or {},
        "phases": [cmd.get("phase") for cmd in (payload.get("commands") or [])],
        "next_step": cadence_next_step(str(payload.get("status") or "")),
        "generated_artifact_ids": [artifact.id for artifact in generated_artifacts],
    }


async def _observe_simulator_result(
    db: Session,
    *,
    project: Project,
    user: User,
    run_id: str,
    result: dict[str, Any],
    generated_artifact_ids: list[str],
) -> Optional[dict[str, Any]]:
    latest_model = get_latest_mental_model_revision(db, project.id)
    if not latest_model:
        return None
    status = str(result.get("status") or "")
    event_type = "simulator.xcelium.passed" if status == "passed" else "simulator.xcelium.diagnosed"
    if result.get("repair_history"):
        event_type = "simulator.xcelium.repaired"
    return await observe_mental_model_event(
        db,
        model_revision=latest_model,
        user=user,
        event_type=event_type,
        payload={
            "verification_type": "uvm",
            "agent": "xcelium_simulator_plugin",
            "run_id": run_id,
            "simulator": "xcelium",
            "status": status,
            "analysis": result.get("analysis") or {},
            "evidence": result.get("evidence") or [],
            "repair_history": result.get("repair_history") or [],
            "closure_report": result.get("closure_report") or {},
            "trusted": status == "passed",
            "artifact_ids": generated_artifact_ids,
        },
        apply_approved_intent=False,
    )
