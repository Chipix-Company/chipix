"""Simulator plugin API endpoints."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from database.database import get_db
from database.models import User
from routes.api import (
    _get_current_user,
    _require_project_access,
)
from services.verification.cadence_run import (
    cadence_next_step,
    load_cadence_run,
    run_cadence_on_artifacts,
    run_dir_for_project,
)
from simulator_plugins import detect_simulators
from simulator_plugins.vcd_analyzer import WaveformAnalyzer


router = APIRouter(prefix="/api/v1", tags=["simulator-plugins"])


class SimulatorRunCreateRequest(BaseModel):
    simulator: str = Field("xcelium", description="Simulator plugin name")
    generated_artifact_ids: list[str] = Field(default_factory=list)
    top_module: str = Field("top_tb", description="Simulation top, normally top_tb")
    uvm_testname: str = ""
    timeout_seconds: int = Field(300, ge=10, le=3600)
    dry_run: bool = False
    mock_logs: dict[str, str] = Field(default_factory=dict)
    auto_repair_generated: bool = True
    max_repair_rounds: int = Field(3, ge=0, le=5)


@router.get("/tools/simulators")
def list_simulators_endpoint(
    refresh: bool = False,
    current_user: User = Depends(_get_current_user),
):
    _ = current_user
    return {"simulators": detect_simulators(refresh=refresh)}


class XceliumConfigureRequest(BaseModel):
    """Manual Cadence override applied when auto-detection fails.

    Each field is optional: ``null`` leaves the current value unchanged, an
    empty string clears it, a value sets it. Paths are validated before saving.
    """

    xrun_bin: Optional[str] = Field(
        None, description="Full path to xrun (or a name on PATH)."
    )
    setup_script: Optional[str] = Field(
        None, description="Path to a lab Cadence setup script (e.g. cshrc_kle)."
    )
    setup_shell: Optional[str] = Field(
        None, description="Shell for the setup script: csh|tcsh|bash|sh."
    )
    license: Optional[str] = Field(
        None, description="License as port@host (sets CDS_LIC_FILE)."
    )


@router.get("/tools/simulators/xcelium/config")
def get_xcelium_config_endpoint(current_user: User = Depends(_get_current_user)):
    """Return the persisted manual Cadence override (if any)."""
    _ = current_user
    from simulator_plugins.cadence_config import load_cadence_config

    return {"config": load_cadence_config()}


@router.post("/tools/simulators/xcelium/configure")
def configure_xcelium_endpoint(
    req: XceliumConfigureRequest,
    current_user: User = Depends(_get_current_user),
):
    """Manually point ChipVerify at Cadence, persist it, and re-test detection.

    Returns the saved config plus a fresh detection result so the caller can
    immediately see whether Cadence is now ``ready`` / ``no_license`` / etc.
    """
    _ = current_user
    from simulator_plugins.cadence_config import CadenceConfigError, set_cadence_config
    from simulator_plugins.xcelium import XceliumPlugin

    try:
        config = set_cadence_config(
            xrun_bin=req.xrun_bin,
            setup_script=req.setup_script,
            setup_shell=req.setup_shell,
            license=req.license,
        )
    except CadenceConfigError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    detection = XceliumPlugin().refresh().to_dict()
    return {"config": config, "detection": detection}


class XceliumIntegrationTestRequest(BaseModel):
    """Optional Cadence override fields applied before the FIFO smoke compile."""

    xrun_bin: Optional[str] = Field(None, description="Full path to xrun (or a name on PATH).")
    setup_script: Optional[str] = Field(None, description="Path to a lab Cadence setup script.")
    setup_shell: Optional[str] = Field(None, description="Shell for the setup script.")
    license: Optional[str] = Field(None, description="License as port@host.")
    timeout_seconds: int = Field(180, ge=30, le=600)


@router.post("/tools/simulators/xcelium/integration-test")
def xcelium_integration_test_endpoint(
    req: XceliumIntegrationTestRequest = Body(default_factory=XceliumIntegrationTestRequest),
    current_user: User = Depends(_get_current_user),
):
    """Run the predefined FIFO UVM fixture through the production XceliumRunner path.

    Saves any provided Cadence override first, then materializes the benchmark
    FIFO RTL plus a minimal UVM environment and runs compile → elaborate → simulate
    via xrun. Pass requires real xrun commands to succeed with log output summarized
    in ``run_summary`` for the UI.
    """
    _ = current_user
    from simulator_plugins.cadence_config import CadenceConfigError, set_cadence_config
    from simulator_plugins.cadence_integration_fixture import run_cadence_fifo_integration_test
    from simulator_plugins.xcelium import XceliumPlugin

    body = req
    has_override = any(
        value is not None
        for value in (body.xrun_bin, body.setup_script, body.setup_shell, body.license)
    )
    if has_override:
        try:
            set_cadence_config(
                xrun_bin=body.xrun_bin,
                setup_script=body.setup_script,
                setup_shell=body.setup_shell,
                license=body.license,
            )
        except CadenceConfigError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        XceliumPlugin().refresh()

    result = run_cadence_fifo_integration_test(timeout_seconds=body.timeout_seconds)
    return result


@router.delete("/tools/simulators/xcelium/config")
def clear_xcelium_config_endpoint(current_user: User = Depends(_get_current_user)):
    """Remove the manual Cadence override and re-run detection."""
    _ = current_user
    from simulator_plugins.cadence_config import clear_cadence_config
    from simulator_plugins.xcelium import XceliumPlugin

    clear_cadence_config()
    detection = XceliumPlugin().refresh().to_dict()
    return {"config": {}, "detection": detection}


class XceliumSyntaxCheckRequest(BaseModel):
    filename: str = Field("snippet.sv", description="Name (for messages/suffix)")
    content: str = Field("", description="SystemVerilog source to check")
    incdirs: list[str] = Field(default_factory=list)
    uvm: bool = False


@router.post("/tools/simulators/xcelium/syntax-check")
def xcelium_syntax_check_endpoint(
    req: XceliumSyntaxCheckRequest,
    current_user: User = Depends(_get_current_user),
):
    """Single-file syntax/parse check via Cadence `xrun -compile` (no elaboration)."""
    _ = current_user
    import tempfile

    from simulator_plugins.xcelium import XceliumPlugin

    if not req.content.strip():
        raise HTTPException(status_code=400, detail="content is required for a syntax check.")

    tmp_dir = Path(tempfile.mkdtemp(prefix="xrun_syntax_src_"))
    name = Path(req.filename).name or "snippet.sv"
    if not Path(name).suffix:
        name = f"{name}.sv"
    src = tmp_dir / name
    src.write_text(req.content, encoding="utf-8")

    # The file's own directory is always an include dir so local `include`s resolve.
    incdirs = list(req.incdirs) + [str(tmp_dir)]
    result = XceliumPlugin().check_syntax(src, incdirs=incdirs, uvm=req.uvm)
    result["filename"] = req.filename
    return result


@router.post("/projects/{project_id}/simulate")
async def create_simulator_run_endpoint(
    project_id: str,
    req: SimulatorRunCreateRequest,
    current_user: User = Depends(_get_current_user),
    db: Session = Depends(get_db),
):
    project = _require_project_access(db, current_user, project_id)
    if req.simulator.lower() != "xcelium":
        raise HTTPException(status_code=400, detail="Only the xcelium simulator plugin is available.")

    result = await run_cadence_on_artifacts(
        db,
        project=project,
        user=current_user,
        generated_artifact_ids=req.generated_artifact_ids,
        top_module=req.top_module or "top_tb",
        uvm_testname=req.uvm_testname,
        timeout_seconds=req.timeout_seconds,
        dry_run=req.dry_run,
        mock_logs=req.mock_logs,
        auto_repair_generated=req.auto_repair_generated,
        max_repair_rounds=req.max_repair_rounds,
    )
    if result.get("status") == "error" and result.get("error"):
        raise HTTPException(status_code=400, detail=result["error"])
    return {
        "project_id": project.id,
        "run": result.get("run"),
        "sandbox": result.get("sandbox"),
        "mental_model_observation": result.get("mental_model_observation"),
        "next_action": result.get("next_step") or cadence_next_step(str(result.get("status") or "")),
    }


@router.get("/projects/{project_id}/simulate/{run_id}/status")
def simulator_run_status_endpoint(
    project_id: str,
    run_id: str,
    current_user: User = Depends(_get_current_user),
    db: Session = Depends(get_db),
):
    project = _require_project_access(db, current_user, project_id)
    try:
        return load_cadence_run(project, run_id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Simulator run not found.")


@router.get("/projects/{project_id}/simulate/{run_id}/logs")
def simulator_run_logs_endpoint(
    project_id: str,
    run_id: str,
    current_user: User = Depends(_get_current_user),
    db: Session = Depends(get_db),
):
    project = _require_project_access(db, current_user, project_id)
    try:
        run = load_cadence_run(project, run_id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Simulator run not found.")
    logs = {}
    for phase, path in (run.get("logs") or {}).items():
        file_path = Path(path)
        if file_path.exists() and file_path.is_file():
            logs[phase] = file_path.read_text(encoding="utf-8", errors="ignore")
    return {"run_id": run_id, "logs": logs, "analysis": run.get("analysis") or {}}


@router.get("/projects/{project_id}/simulate/{run_id}/waveform")
def simulator_run_waveform_endpoint(
    project_id: str,
    run_id: str,
    time_ns: int = Query(0, ge=0),
    current_user: User = Depends(_get_current_user),
    db: Session = Depends(get_db),
):
    project = _require_project_access(db, current_user, project_id)
    try:
        run_dir = run_dir_for_project(project, run_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    vcd_path = run_dir / "simulation.vcd"
    if not vcd_path.exists():
        return {"run_id": run_id, "status": "missing", "signals": {}}
    analyzer = WaveformAnalyzer(vcd_path)
    return {
        "run_id": run_id,
        "status": "available",
        "time_ns": time_ns,
        "signals": analyzer.get_signals_at_time(time_ns),
        "xz_signals": analyzer.detect_x_z_signals(time_ns),
    }


@router.get("/projects/{project_id}/simulate/{run_id}/evidence")
def simulator_run_evidence_endpoint(
    project_id: str,
    run_id: str,
    current_user: User = Depends(_get_current_user),
    db: Session = Depends(get_db),
):
    project = _require_project_access(db, current_user, project_id)
    try:
        run = load_cadence_run(project, run_id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Simulator run not found.")
    return {"run_id": run_id, "evidence": run.get("evidence") or []}


@router.get("/projects/{project_id}/simulate/{run_id}/coverage")
def simulator_run_coverage_endpoint(
    project_id: str,
    run_id: str,
    current_user: User = Depends(_get_current_user),
    db: Session = Depends(get_db),
):
    project = _require_project_access(db, current_user, project_id)
    try:
        run = load_cadence_run(project, run_id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Simulator run not found.")
    return {"run_id": run_id, "coverage": run.get("coverage") or {}}


@router.get("/projects/{project_id}/simulate/{run_id}/verdict")
def simulator_run_verdict_endpoint(
    project_id: str,
    run_id: str,
    current_user: User = Depends(_get_current_user),
    db: Session = Depends(get_db),
):
    project = _require_project_access(db, current_user, project_id)
    try:
        run = load_cadence_run(project, run_id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Simulator run not found.")
    return {"run_id": run_id, "verdict": run.get("closure_report") or {}}
