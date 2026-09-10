"""
Staged Verification Routes — ChipStack-style prepare → plan → refine → execute.

Endpoints:
    POST /api/v1/projects/{id}/verification/prepare   — Build model + recommend strategy
    POST /api/v1/projects/{id}/verification/plan       — Generate reviewable plan
    POST /api/v1/projects/{id}/verification/plan/refine — NL plan refinement
    POST /api/v1/projects/{id}/verification/execute     — Run after user approval
"""

from __future__ import annotations

import asyncio
import copy
import json
import logging
import os
import re
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from database.database import get_db
from database.enums import VerificationType
from common.paths import outputs_dir
from database.models import (
    MentalModelRevision,
    Project,
    ProjectArtifact,
    ProjectArtifactPointer,
    User,
)
from routes.api import (
    _create_project_artifact_revision,
    _get_current_user,
    _get_project_artifact_pointer,
    _guess_content_type,
    _is_probably_text_bytes,
    _is_text_content_type,
    _normalize_relative_artifact_path,
    _require_project_access,
    resolve_active_spec_rtl_artifacts,
    _serialize_artifact_pointer,
    _serialize_project_artifact,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/projects", tags=["staged-verification"])

VALID_VERIFICATION_TYPES = {member.value for member in VerificationType}


async def _observe_mental_model_event_safely(observer, db: Session, **kwargs) -> Dict[str, Any]:
    """Record workflow memory without allowing telemetry persistence to break planning."""
    event_type = str(kwargs.get("event_type") or "mental_model.event")
    try:
        return await observer(db, **kwargs)
    except Exception as exc:
        logger.exception("Mental-model observation failed for %s: %s", event_type, exc)
        try:
            db.rollback()
        except Exception:
            logger.exception("Failed to roll back mental-model observation for %s", event_type)
        return {
            "event_type": event_type,
            "status": "recording_failed",
            "summary": "Plan generation continued after a noncritical observation write failed.",
            "error": str(exc),
        }


def _write_json_file(path: Path, payload: Any) -> None:
    """Write JSON after ensuring packaged-runtime parent directories exist."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


# ─── Request / Response Models ────────────────────────────────────────


class PrepareRequest(BaseModel):
    target_module: Optional[str] = None


class PlanRequest(BaseModel):
    verification_type: str  # "unitsim" | "formal" | "uvm" | "all"
    mental_model_revision_id: str
    target_module: Optional[str] = None


class RefineRequest(BaseModel):
    verification_type: str
    plan_json: Dict[str, Any]
    feedback: str
    mental_model_revision_id: Optional[str] = None


class ExecuteRequest(BaseModel):
    verification_type: str
    mental_model_revision_id: str
    approved_plan: Dict[str, Any]


# ─── Helpers ──────────────────────────────────────────────────────────


def _validate_verification_type(verification_type: str) -> str:
    vtype = (verification_type or "").strip().lower()
    if vtype not in VALID_VERIFICATION_TYPES:
        raise HTTPException(
            status_code=400,
            detail="verification_type must be unitsim, formal, uvm, or all",
        )
    return vtype


def _reconcile_uvm_plan_with_generated_files(
    approved_plan: Dict[str, Any],
    uvm_out: Any,
) -> Dict[str, Any]:
    """Use the generated collateral as the validation preview source of truth."""
    raw_uvm_plan = approved_plan.get("uvm") if isinstance(approved_plan, dict) else {}
    uvm_plan = copy.deepcopy(raw_uvm_plan) if isinstance(raw_uvm_plan, dict) else {}
    generated_files = [
        Path(str(path)).name
        for path in getattr(uvm_out, "all_files", []) or []
        if Path(str(path)).name
    ]
    if generated_files:
        uvm_plan["files_preview"] = list(dict.fromkeys(generated_files))
        uvm_plan["estimated_file_count"] = len(uvm_plan["files_preview"])
    policy = getattr(uvm_out, "generation_policy", None)
    if isinstance(policy, dict):
        current_policy = uvm_plan.get("generation_policy")
        if not isinstance(current_policy, dict):
            current_policy = {}
        merged_policy = {**current_policy, **policy}
        if generated_files:
            merged_policy["generated_files_preview"] = list(dict.fromkeys(generated_files))
        uvm_plan["generation_policy"] = merged_policy
    return uvm_plan


def _resolve_active_artifacts(db: Session, project: Project):
    """Resolve spec/RTL artifacts (active pointer, else latest revision)."""
    return resolve_active_spec_rtl_artifacts(db, project, auto_activate=True)


def _collect_rtl_source_files(
    rtl_artifact: Optional[ProjectArtifact],
    mental_model_content: Optional[Dict[str, Any]] = None,
) -> List[str]:
    """Resolve RTL source paths for validation/filelist/formal checks."""
    rtl_exts = {".v", ".sv", ".vh", ".svh", ".vhd", ".vhdl"}
    content = mental_model_content if isinstance(mental_model_content, dict) else {}
    scan = content.get("project_scan", {}) if isinstance(content.get("project_scan"), dict) else {}
    root_value = str(scan.get("root_path") or "").strip()
    scan_rtl_files = scan.get("rtl_files") if isinstance(scan.get("rtl_files"), list) else []
    resolved: List[str] = []
    if root_value and scan_rtl_files:
        root_path = Path(root_value)
        for entry in scan_rtl_files:
            path = Path(str(entry))
            if not path.is_absolute():
                path = root_path / path
            if path.exists() and path.is_file() and path.suffix.lower() in rtl_exts:
                resolved.append(str(path))
        if resolved:
            return resolved

    if not rtl_artifact:
        return []

    path_value = str(getattr(rtl_artifact, "file_path", "") or "").strip()
    if not path_value:
        return []

    root = Path(path_value)
    try:
        from services.rtl_project_package import find_extracted_rtl_root

        extracted_root = find_extracted_rtl_root(root)
    except Exception:
        extracted_root = None
    if extracted_root and extracted_root.exists() and not root.exists():
        return [
            str(path)
            for path in extracted_root.rglob("*")
            if path.is_file() and path.suffix.lower() in rtl_exts
        ]
    if root.is_file() and root.suffix.lower() in rtl_exts:
        if extracted_root and extracted_root.exists():
            return [
                str(path)
                for path in extracted_root.rglob("*")
                if path.is_file() and path.suffix.lower() in rtl_exts
            ]
        return [str(root)]
    if root.is_file():
        try:
            from services.rtl_project_package import extract_rtl_archive, is_rtl_archive_filename

            if is_rtl_archive_filename(root.name):
                extract_dir = root.parent / f"{root.stem}_formal_sources"
                if not extract_dir.exists():
                    extract_rtl_archive(root, extract_dir)
                return [
                    str(path)
                    for path in extract_dir.rglob("*")
                    if path.is_file() and path.suffix.lower() in rtl_exts
                ]
        except Exception as exc:
            logger.warning("Unable to extract RTL archive for staged verification: %s", exc)
    if root.is_dir():
        return [
            str(path)
            for path in root.rglob("*")
            if path.is_file() and path.suffix.lower() in rtl_exts
        ]
    return []


def _load_full_spec_context_for_audit(
    project: Project,
    spec_artifact: Optional[ProjectArtifact],
) -> Dict[str, Any]:
    """Load every extracted spec page with stable page/source-line provenance."""
    if not spec_artifact:
        return {"metadata": {}, "pages": [], "status": "missing"}
    metadata = {
        "artifact_id": spec_artifact.id,
        "filename": spec_artifact.filename,
        "checksum_sha256": spec_artifact.checksum_sha256,
    }
    path = Path(str(spec_artifact.file_path or ""))
    try:
        from services.document_context.extract import extract_pages_from_file, read_pages_jsonl
        from services.document_context.manager import ensure_spec_document_index, get_spec_context_dir

        ensure_spec_document_index(
            organization_id=project.organization_id,
            project_id=project.id,
            artifact_id=spec_artifact.id,
            file_path=path,
            filename=spec_artifact.filename,
            checksum_sha256=spec_artifact.checksum_sha256,
        )
        context_dir = get_spec_context_dir(project.organization_id, project.id, spec_artifact.id)
        pages = read_pages_jsonl(context_dir / "pages.jsonl")
        if not pages and path.exists():
            pages = extract_pages_from_file(path, spec_artifact.filename)
        return {
            "metadata": metadata,
            "status": "ready" if pages else "empty",
            "pages": [
                {
                    "page_num": page.page_num,
                    "text": page.text,
                    "content_hash": page.content_hash,
                    "source_line_start": page.source_line_start,
                    "source_line_end": page.source_line_end,
                }
                for page in pages
            ],
        }
    except Exception as exc:
        logger.warning("Unable to load full spec context for Xcelium audit: %s", exc, exc_info=True)
        return {"metadata": metadata, "pages": [], "status": "error", "error": str(exc)}


def _xcelium_detection_summary(detection: Any) -> Dict[str, Any]:
    """Return the Cadence readiness fields that are safe to expose to clients."""
    details = getattr(detection, "details", {}) or {}
    setup_script = str(details.get("setup_script") or "")
    return {
        "status": str(details.get("status") or "not_installed"),
        "path": str(getattr(detection, "path", "") or ""),
        "version": str(getattr(detection, "version", "") or ""),
        "available": bool(getattr(detection, "available", False)),
        "uvm_available": bool(getattr(detection, "uvm_available", False)),
        "platform_supported": bool(getattr(detection, "platform_supported", False)),
        "license_ready": bool(details.get("license_ready")),
        "run_ready": bool(details.get("run_ready")),
        "guidance": str(getattr(detection, "guidance", "") or ""),
        "environment": {
            "setup_script_configured": bool(setup_script),
            "setup_script_name": Path(setup_script).name if setup_script else "",
            "setup_variables_applied": list(details.get("setup_applied") or []),
            "license_configured": bool(details.get("license_ready")),
        },
    }


def _xcelium_result_artifacts(run_dir: Path, result: Dict[str, Any]) -> List[str]:
    """Collect the small, reviewable Xcelium evidence files for publication."""
    paths: List[Path] = []
    logs = result.get("logs") if isinstance(result.get("logs"), dict) else {}
    paths.extend(Path(str(path)) for path in logs.values() if path)
    paths.extend(Path(str(path)) for path in result.get("artifact_paths", []) if path)
    paths.extend(
        run_dir / "results" / name
        for name in (
            "analysis.json",
            "evidence_chain.json",
            "verdict.json",
            "repair_history.json",
            "feedback_memory.json",
            "regression.json",
            "integrity.json",
            "coverage_closure.json",
            "traceability.json",
            "findings.json",
            "run_manifest.json",
        )
    )
    paths.extend(run_dir / name for name in ("coverage.txt", "simulation.vcd", "sandbox.json"))
    return list(
        dict.fromkeys(str(path) for path in paths if path.exists() and path.is_file())
    )


def _run_xcelium_after_uvm_validation(
    *,
    uvm_out: Any,
    uvm_dir: Path,
    validation: Dict[str, Any],
    compile_gate: Dict[str, Any],
    mental_model: Dict[str, Any],
    rtl_files: List[str],
    spec_context: Optional[Dict[str, Any]] = None,
    plugin: Any = None,
    runner: Any = None,
) -> Dict[str, Any]:
    """Run generated UVM through Xcelium when static gates and Cadence are ready."""
    from services.verification.uvm_gen import _emit_uvm_progress
    from simulator_plugins.base import SimulatorRunRequest
    from simulator_plugins.sandbox import create_run_sandbox
    from simulator_plugins.xcelium import XceliumPlugin
    from simulator_plugins.xcelium_runner import XceliumRunner

    _emit_uvm_progress({
        "type": "cadence_detect",
        "message": "Checking Cadence Xcelium readiness",
    })
    try:
        cadence_plugin = plugin or XceliumPlugin()
        detection = cadence_plugin.detect()
    except Exception as exc:
        logger.warning("Cadence detection failed during staged UVM execution: %s", exc, exc_info=True)
        result = {
            "simulator": "xcelium",
            "status": "skipped",
            "attempted": False,
            "required": False,
            "reason": "Cadence detection failed.",
            "error": str(exc),
            "detection": {"status": "error", "run_ready": False},
            "artifact_paths": [],
        }
        _emit_uvm_progress({
            "type": "cadence_skipped",
            "message": f"Cadence detection failed: {exc}",
        })
        return result

    detection_summary = _xcelium_detection_summary(detection)
    _emit_uvm_progress({
        "type": "cadence_ready",
        "message": (
            "Cadence Xcelium is ready"
            if detection_summary["run_ready"]
            else f"Cadence Xcelium is {detection_summary['status'].replace('_', ' ')}"
        ),
        "detection": detection_summary,
    })
    static_ready = bool(validation.get("trusted")) and bool(compile_gate.get("passed"))
    if not static_ready:
        reason = "Cadence was not run because UVM validation or the compile gate did not pass."
        result = {
            "simulator": "xcelium",
            "status": "skipped",
            "attempted": False,
            "required": False,
            "reason": reason,
            "detection": detection_summary,
            "artifact_paths": [],
        }
        _emit_uvm_progress({"type": "cadence_skipped", "message": reason})
        return result

    if not detection_summary["run_ready"]:
        detected_status = detection_summary["status"].replace("_", " ")
        reason = f"Cadence was not run because Xcelium is {detected_status}."
        result = {
            "simulator": "xcelium",
            "status": "skipped",
            "attempted": False,
            "required": False,
            "reason": reason,
            "detection": detection_summary,
            "artifact_paths": [],
        }
        _emit_uvm_progress({"type": "cadence_skipped", "message": reason})
        return result

    filelist_value = str(getattr(uvm_out, "filelist", "") or "").strip()
    filelist = Path(filelist_value) if filelist_value else None
    if filelist is None or not filelist.is_file():
        reason = "Cadence is ready, but the generated UVM filelist is missing."
        result = {
            "simulator": "xcelium",
            "status": "error",
            "attempted": False,
            "required": True,
            "reason": reason,
            "detection": detection_summary,
            "artifact_paths": [],
        }
        _emit_uvm_progress({"type": "cadence_complete", "message": reason, "status": "error"})
        return result

    manifest_value = str(getattr(uvm_out, "regression_manifest", "") or "").strip()
    manifest_path = Path(manifest_value) if manifest_value else uvm_dir / "uvm_regression_manifest.json"
    try:
        regression_manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}
    except Exception as exc:
        logger.warning("Unable to read UVM regression manifest: %s", exc)
        regression_manifest = {}
    _emit_uvm_progress({
        "type": "cadence_regression_manifest",
        "message": f"Prepared {len(regression_manifest.get('tests') or []) or 1} Cadence regression test(s)",
        "tests": len(regression_manifest.get("tests") or []) or 1,
    })
    run_dir = uvm_dir / f"xcelium_run_{uuid.uuid4().hex[:10]}"
    sandbox = create_run_sandbox(
        base_dir=uvm_dir,
        generated_files=list(getattr(uvm_out, "all_files", []) or []),
        rtl_files=rtl_files,
        source_filelist=filelist,
        run_id=run_dir.name,
    )
    _emit_uvm_progress({
        "type": "cadence_sandbox_integrity",
        "message": f"Sandboxed generated UVM and hash-locked {len(sandbox.rtl_checksums)} RTL file(s)",
        "rtl_files": len(sandbox.rtl_checksums),
    })
    request = SimulatorRunRequest(
        run_dir=run_dir,
        filelist=Path(sandbox.filelist),
        top_module="top_tb",
        timeout_seconds=300,
        auto_repair_generated=True,
        max_repair_rounds=2,
        regression_tests=list(regression_manifest.get("tests") or []),
        replay_failures=True,
        seed_namespace=run_dir.name,
        coverage_targets=dict(regression_manifest.get("coverage_targets") or {"functional": 100.0, "code": 90.0}),
        enforce_closure=True,
        static_validation={
            "trusted": bool(validation.get("trusted")) and bool(compile_gate.get("passed")),
            "uvm_validation": validation,
            "compile_gate": compile_gate,
        },
        rtl_checksums=dict(sandbox.rtl_checksums),
        spec_pages=list((spec_context or {}).get("pages") or []),
        spec_metadata=dict((spec_context or {}).get("metadata") or {}),
    )
    _emit_uvm_progress({
        "type": "cadence_start",
        "message": "Cadence Xcelium connected — compiling and running generated UVM",
    })

    def publish_cadence_progress(event: Dict[str, Any]) -> None:
        payload = dict(event or {})
        event_type = str(payload.pop("type", "phase_update"))
        _emit_uvm_progress({"type": f"cadence_{event_type}", **payload})

    try:
        cadence_runner = runner or XceliumRunner(plugin=cadence_plugin)
        run_result = cadence_runner.run(
            request,
            mental_model=mental_model,
            rtl_root=(Path(rtl_files[0]).parent if rtl_files else None),
            progress_callback=publish_cadence_progress,
        )
        result = run_result.to_dict()
    except Exception as exc:
        logger.warning("Cadence Xcelium staged run failed to start: %s", exc, exc_info=True)
        result = {
            "simulator": "xcelium",
            "status": "error",
            "run_id": run_dir.name,
            "run_dir": str(run_dir),
            "error": str(exc),
        }

    result.update({
        "attempted": True,
        "required": True,
        "detection": detection_summary,
    })
    result["artifact_paths"] = _xcelium_result_artifacts(run_dir, result)
    cadence_status = str(result.get("status") or "error").lower()
    _emit_uvm_progress({
        "type": "cadence_complete",
        "message": f"Cadence Xcelium run {cadence_status}",
        "status": cadence_status,
        "run_id": result.get("run_id"),
    })
    return result


def _persist_staged_verification_record(
    db: Session,
    *,
    project: Project,
    run_type: str,
    target_module: str,
    status: str,
    mental_model_revision: int,
    result: Dict[str, Any],
) -> Optional[str]:
    """Persist a compact staged verification evidence record."""
    try:
        from database.verification_models import ToolExecution, VerificationRun

        run_id = str(uuid.uuid4())
        failed = status in {"error", "failed", "partial", "validation_failed"}
        record = VerificationRun(
            id=run_id,
            project_id=project.id,
            run_type=run_type,
            target_module=target_module or "dut",
            status="failed" if failed else "passed",
            tests_passed=0 if failed else 1,
            tests_failed=1 if failed else 0,
            result_json=json.dumps(result, default=str),
            mental_model_revision=mental_model_revision,
        )
        tool_record = ToolExecution(
            id=str(uuid.uuid4()),
            verification_run_id=run_id,
            tool_name=f"{run_type}_static_trust_gate",
            args_json=json.dumps({"target_module": target_module}, default=str),
            result_json=json.dumps(result, default=str),
            status="failed" if failed else "passed",
        )
        db.add(record)
        db.add(tool_record)
        db.commit()
        return run_id
    except Exception as exc:
        db.rollback()
        logger.warning("Failed to persist staged verification evidence: %s", exc)
        return None


def _require_model_revision(
    db: Session,
    *,
    project: Project,
    mental_model_revision_id: str,
) -> MentalModelRevision:
    model_rev = (
        db.query(MentalModelRevision)
        .filter(
            MentalModelRevision.id == mental_model_revision_id,
            MentalModelRevision.project_id == project.id,
        )
        .first()
    )
    if not model_rev:
        raise HTTPException(status_code=404, detail="Mental model revision not found")
    return model_rev


def _load_model_content(model_revision) -> Dict:
    """Load and normalize mental model content from a revision record."""
    # MentalModelRevision stores content in content_json
    raw = getattr(model_revision, "content_json", None) or getattr(model_revision, "content", None) or "{}"
    if isinstance(raw, str):
        content = json.loads(raw)
    else:
        content = raw

    # Normalize: if content is wrapped as {content: {...}}, unwrap
    if isinstance(content, dict) and "content" in content and isinstance(content["content"], dict):
        inner = content["content"]
        normalized = {k: v for k, v in content.items() if k != "content"}
        normalized.update(inner)
        return normalized

    return content or {}


def _normalize_for_recommender(content: Dict):
    """
    Build a namespace object the strategy_recommender can read.
    It expects attributes like .design.ports, .design.fsms, etc.
    """
    from types import SimpleNamespace

    design_data = content.get("design", content)
    if not isinstance(design_data, dict):
        design_data = {}

    def _get(obj, key, default=None):
        if isinstance(obj, dict):
            return obj.get(key, default)
        return getattr(obj, key, default)

    def _as_int(value, default=1):
        try:
            return int(value or default)
        except (TypeError, ValueError):
            return default

    def _normalize_port(raw):
        return SimpleNamespace(
            name=_get(raw, "name", ""),
            direction=_get(raw, "direction", "input"),
            width=_as_int(_get(raw, "width", 1), 1),
            bus_range=_get(raw, "bus_range", ""),
            port_type=_get(raw, "port_type", "logic"),
            description=_get(raw, "description", ""),
            protocol_role=_get(raw, "protocol_role", ""),
        )

    def _normalize_parameter(raw):
        return SimpleNamespace(
            name=_get(raw, "name", ""),
            default_value=str(_get(raw, "default_value", _get(raw, "value", "")) or ""),
            description=_get(raw, "description", ""),
        )

    def _normalize_clock_domain(raw):
        return SimpleNamespace(
            name=_get(raw, "name", "clk"),
            frequency=_get(raw, "frequency", ""),
            associated_reset=_get(raw, "associated_reset", _get(raw, "reset", "")),
            reset_polarity=_get(raw, "reset_polarity", _get(raw, "polarity", "active_low")),
            reset_type=_get(raw, "reset_type", "synchronous"),
        )

    def _normalize_fsm(raw):
        return SimpleNamespace(
            name=_get(raw, "name", "fsm"),
            state_signal=_get(raw, "state_signal", ""),
            states=list(_get(raw, "states", []) or []),
            transitions=list(_get(raw, "transitions", []) or []),
            encoding=_get(raw, "encoding", "binary") or "binary",
        )

    def _normalize_protocol(raw):
        return SimpleNamespace(
            protocol=_get(raw, "protocol", ""),
            role=_get(raw, "role", ""),
            port_group=list(_get(raw, "port_group", []) or []),
            description=_get(raw, "description", ""),
        )

    def _normalize_register_field(raw):
        return SimpleNamespace(
            name=_get(raw, "name", ""),
            bank=_get(raw, "bank", ""),
            offset=_as_int(_get(raw, "offset", 0), 0),
            offset_basis=_get(raw, "offset_basis", ""),
            width=_as_int(_get(raw, "width", 32), 32),
            reset_value=str(_get(raw, "reset_value", "0") or "0"),
            access=str(_get(raw, "access", "RW") or "RW"),
            fields=list(_get(raw, "fields", []) or []),
            description=_get(raw, "description", ""),
            confidence=_get(raw, "confidence", 1.0),
            write_enable=_get(raw, "write_enable", ""),
            addr_signal=_get(raw, "addr_signal", ""),
            data_signal=_get(raw, "data_signal", ""),
        )

    def _normalize_expected_behavior(raw):
        stimulus = _get(raw, "stimulus", {}) or {}
        expected_output = _get(raw, "expected_output", {}) or {}
        if not isinstance(stimulus, dict):
            stimulus = {}
        if not isinstance(expected_output, dict):
            expected_output = {}
        return SimpleNamespace(
            id=str(_get(raw, "id", "") or ""),
            stimulus={str(k): str(v) for k, v in stimulus.items()},
            expected_output={str(k): str(v) for k, v in expected_output.items()},
            latency_cycles=_as_int(_get(raw, "latency_cycles", 1), 1),
            precondition=str(_get(raw, "precondition", "") or ""),
            description=str(_get(raw, "description", "") or ""),
            requirement_ids=list(_get(raw, "requirement_ids", []) or []),
            confidence=_get(raw, "confidence", 0.5),
        )

    def _normalize_transaction_flow(raw):
        return SimpleNamespace(
            name=str(_get(raw, "name", "") or ""),
            protocol=str(_get(raw, "protocol", "") or ""),
            description=str(_get(raw, "description", "") or ""),
            steps=list(_get(raw, "steps", []) or []),
            latency_cycles=_as_int(_get(raw, "latency_cycles", 0), 0),
            throughput=str(_get(raw, "throughput", "") or ""),
            constraints=list(_get(raw, "constraints", []) or []),
        )

    def _list(key, normalizer=None):
        val = design_data.get(key, [])
        if not isinstance(val, list):
            return []
        if normalizer:
            return [normalizer(item) for item in val]
        return [SimpleNamespace(**item) if isinstance(item, dict) else item for item in val]

    def _list_from(data, key, normalizer=None):
        if not isinstance(data, dict):
            return []
        val = data.get(key, [])
        if not isinstance(val, list):
            return []
        if normalizer:
            return [normalizer(item) for item in val]
        return [SimpleNamespace(**item) if isinstance(item, dict) else item for item in val]

    def _normalize_design_block(raw_design):
        if not isinstance(raw_design, dict):
            raw_design = {}
        return SimpleNamespace(
            top_module=raw_design.get("top_module", ""),
            definition_kind=raw_design.get("definition_kind", "Module"),
            file=raw_design.get("file", ""),
            description=raw_design.get("description", ""),
            ports=_list_from(raw_design, "ports", _normalize_port),
            fsms=_list_from(raw_design, "fsms", _normalize_fsm),
            protocols=_list_from(raw_design, "protocols", _normalize_protocol),
            modules=raw_design.get("modules", []),
            hierarchy_tree=raw_design.get("hierarchy_tree", {}),
            instantiations=_list_from(raw_design, "instantiations"),
            sub_instances=_list_from(raw_design, "sub_instances"),
            clock_domains=_list_from(raw_design, "clock_domains", _normalize_clock_domain),
            has_cdc_crossings=raw_design.get("has_cdc_crossings", False),
            cdc_crossings=_list_from(raw_design, "cdc_crossings"),
            register_map=_list_from(raw_design, "register_map"),
            register_fields=_list_from(raw_design, "register_fields", _normalize_register_field),
            expected_behaviors=_list_from(raw_design, "expected_behaviors", _normalize_expected_behavior),
            existing_assertions=_list_from(raw_design, "existing_assertions"),
            transaction_flows=_list_from(raw_design, "transaction_flows", _normalize_transaction_flow),
            arbitration_policies=_list_from(raw_design, "arbitration_policies"),
            internal_symbols=list(raw_design.get("internal_symbols", []) or []),
            modports=_list_from(raw_design, "modports"),
            total_lines=raw_design.get("total_lines", 0),
            total_files=raw_design.get("total_files", 0),
            parameters=_list_from(raw_design, "parameters", _normalize_parameter),
        )

    design = SimpleNamespace(
        top_module=design_data.get("top_module", ""),
        ports=_list("ports", _normalize_port),
        fsms=_list("fsms", _normalize_fsm),
        protocols=_list("protocols", _normalize_protocol),
        modules=design_data.get("modules", []),
        clock_domains=_list("clock_domains", _normalize_clock_domain),
        has_cdc_crossings=design_data.get("has_cdc_crossings", False),
        cdc_crossings=_list("cdc_crossings"),
        register_map=_list("register_map"),
        register_fields=_list("register_fields", _normalize_register_field),
        expected_behaviors=_list("expected_behaviors", _normalize_expected_behavior),
        existing_assertions=_list("existing_assertions"),
        transaction_flows=_list("transaction_flows", _normalize_transaction_flow),
        arbitration_policies=_list("arbitration_policies"),
        total_lines=design_data.get("total_lines", 0),
        parameters=_list("parameters", _normalize_parameter),
    )

    # Build verification namespace for formal_gen/unitsim compatibility
    formal_props = []
    unit_tests = []
    ver = content.get("verification", {})
    if isinstance(ver, dict):
        for fp in ver.get("formal_properties", []):
            if isinstance(fp, dict):
                formal_props.append(
                    SimpleNamespace(
                        name=fp.get("name", "property"),
                        description=fp.get("description", ""),
                        sva_sketch=fp.get("sva_sketch", ""),
                    )
                )
        for ut in ver.get("unit_tests", []):
            if isinstance(ut, dict):
                unit_tests.append(SimpleNamespace(**ut))
            else:
                unit_tests.append(ut)

    verification = SimpleNamespace(
        formal_properties=formal_props,
        unit_tests=unit_tests,
        uvm_scenarios=ver.get("uvm_scenarios", []) if isinstance(ver, dict) else [],
        uvm_agents=ver.get("uvm_agents", []) if isinstance(ver, dict) else [],
        uvm_scoreboard_checks=ver.get("uvm_scoreboard_checks", []) if isinstance(ver, dict) else [],
        uvm_coverage_points=ver.get("uvm_coverage_points", []) if isinstance(ver, dict) else [],
        uvm_files_preview=ver.get("uvm_files_preview", []) if isinstance(ver, dict) else [],
    )

    requirements = []
    for r in content.get("requirements", []):
        if isinstance(r, dict):
            requirements.append(SimpleNamespace(**r))
        else:
            requirements.append(r)

    scan_data = content.get("project_scan", {})
    if not isinstance(scan_data, dict):
        scan_data = {}
    project_scan = SimpleNamespace(
        root_path=scan_data.get("root_path", ""),
        rtl_files=list(scan_data.get("rtl_files", []) or []),
        spec_files=list(scan_data.get("spec_files", []) or []),
        total_lines=scan_data.get("total_lines", 0),
    )

    evidence = []
    for item in content.get("evidence", []) or []:
        evidence.append(SimpleNamespace(**item) if isinstance(item, dict) else item)

    raw_block_models = content.get("block_models", {})
    block_models = {}
    if isinstance(raw_block_models, dict):
        block_models = {
            str(name): _normalize_design_block(block)
            for name, block in raw_block_models.items()
            if isinstance(block, dict)
        }

    symbol_table = content.get("symbol_table", {})
    if not isinstance(symbol_table, dict):
        symbol_table = {}

    open_questions = [
        SimpleNamespace(**item) if isinstance(item, dict) else item
        for item in (content.get("open_questions", []) or [])
    ]
    risks = list(content.get("risks", []) or []) if isinstance(content.get("risks", []), list) else []
    living_agent = content.get("living_agent", {}) if isinstance(content.get("living_agent"), dict) else {}
    knowledge_base = _knowledge_base_context_for_content(content)
    setattr(design, "block_models", block_models)
    setattr(design, "knowledge_base", knowledge_base)

    model = SimpleNamespace(
        design=design,
        requirements=requirements,
        verification=verification,
        project_scan=project_scan,
        parser_engine=content.get("parser_engine", "slang"),
        parser_version=content.get("parser_version", ""),
        parser_diagnostics=content.get("parser_diagnostics", []),
        evidence=evidence,
        block_models=block_models,
        symbol_table=symbol_table,
        knowledge_base=knowledge_base,
        open_questions=open_questions,
        risks=risks,
        living_agent=living_agent,
    )
    return model


def _knowledge_base_context_for_content(content: Dict[str, Any]) -> Dict[str, Any]:
    """Return saved KB matches, or derive them for older model revisions."""
    existing = content.get("knowledge_base") if isinstance(content, dict) else None
    if isinstance(existing, dict) and (
        existing.get("signal_rules")
        or existing.get("corner_cases")
        or existing.get("formal_properties")
        or existing.get("vplan_testpoints")
    ):
        return existing
    try:
        from services.mental_model.knowledge_integration import (
            build_knowledge_base_context,
            empty_knowledge_base_context,
        )

        return build_knowledge_base_context(content)
    except Exception as exc:
        logger.warning("Knowledge-base context unavailable for staged flow: %s", exc)
        try:
            from services.mental_model.knowledge_integration import (
                empty_knowledge_base_context,
            )

            return empty_knowledge_base_context()
        except Exception:
            return {
                "plan_hints": {"unitsim": [], "formal": [], "uvm": [], "coverage": []},
                "signal_rules": [],
                "corner_cases": [],
                "formal_properties": [],
                "vplan_testpoints": [],
            }


def _serialize_test_plan(plan) -> Dict[str, Any]:
    from dataclasses import asdict

    data = {
        "module_name": plan.module_name,
        "scenarios": [asdict(s) for s in plan.scenarios],
        "total_scenarios": len(plan.scenarios),
        "coverage_goals": plan.coverage_goals,
        "estimated_sim_time": plan.estimated_sim_time,
        "summary": plan.summary,
    }
    for key in ("generation_engine", "llm_used", "grounding", "llm_error"):
        if hasattr(plan, key):
            data[key] = getattr(plan, key)
    return data


def _text_from_plan_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (str, int, float, bool)):
        return str(value).strip()
    if isinstance(value, list):
        return ", ".join(
            text for text in (_text_from_plan_value(item) for item in value) if text
        )
    if isinstance(value, dict):
        for key in (
            "text",
            "description",
            "question",
            "message",
            "reason",
            "name",
            "title",
            "summary",
        ):
            text = _text_from_plan_value(value.get(key))
            if text:
                return text
    return ""


def _plan_text_list(value: Any) -> List[str]:
    if not value:
        return []
    if isinstance(value, list):
        items = [_text_from_plan_value(item) for item in value]
    else:
        items = [_text_from_plan_value(value)]
    result: List[str] = []
    seen: set[str] = set()
    for item in items:
        if not item:
            continue
        key = item.lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def _append_plan_text(target: List[str], value: Any) -> None:
    seen = {item.lower() for item in target}
    for item in _plan_text_list(value):
        key = item.lower()
        if key in seen:
            continue
        seen.add(key)
        target.append(item)


def _list_count(value: Any) -> int:
    if isinstance(value, list):
        return len(value)
    if isinstance(value, dict):
        return len(value)
    return 0


def _build_plan_presentation(
    *,
    plan: Dict[str, Any],
    content: Dict[str, Any],
    readiness: Dict[str, Any],
) -> Dict[str, Any]:
    design = content.get("design", {}) if isinstance(content, dict) else {}
    if not isinstance(design, dict):
        design = {}

    uvm = plan.get("uvm", {}) if isinstance(plan.get("uvm"), dict) else {}
    unitsim = plan.get("unitsim", {}) if isinstance(plan.get("unitsim"), dict) else {}
    formal = plan.get("formal", {}) if isinstance(plan.get("formal"), dict) else {}
    top = (
        uvm.get("top_module")
        or unitsim.get("module_name")
        or formal.get("module_name")
        or plan.get("module_name")
        or design.get("top_module")
        or ""
    )

    summary_parts: List[str] = []
    description = _text_from_plan_value(
        design.get("description")
        or design.get("intent_summary")
        or content.get("summary")
        or content.get("summary_text")
    )
    if description:
        summary_parts.append(description)
    if top:
        summary_parts.append(f"Top module: {top}")

    facts: List[str] = []
    ports = _list_count(design.get("ports"))
    requirements = _list_count(content.get("requirements"))
    protocols = _list_count(design.get("protocols"))
    if ports:
        facts.append(f"{ports} ports")
    if requirements:
        facts.append(f"{requirements} requirements")
    if protocols:
        facts.append(f"{protocols} protocol{'s' if protocols != 1 else ''}")
    if facts:
        summary_parts.append(", ".join(facts))

    label = {
        "unitsim": "Unit simulation",
        "formal": "Formal verification",
        "uvm": "UVM environment",
        "all": "all verification strategies",
    }.get(str(plan.get("verification_type") or ""), "verification")
    summary = " - ".join(summary_parts) or f"Verification plan for {label}{f' on {top}' if top else ''}."

    assumptions: List[str] = []
    _append_plan_text(assumptions, uvm.get("assumptions"))
    _append_plan_text(assumptions, formal.get("assumptions"))
    _append_plan_text(assumptions, unitsim.get("assumptions"))

    constraints: List[str] = []
    policy = uvm.get("generation_policy") if isinstance(uvm.get("generation_policy"), dict) else {}
    _append_plan_text(constraints, policy.get("rationale"))
    if policy.get("tier"):
        _append_plan_text(constraints, f"UVM generation tier: {policy.get('tier')}")
    if isinstance(policy.get("multi_agent"), bool):
        _append_plan_text(
            constraints,
            f"Multi-agent generation: {'enabled' if policy.get('multi_agent') else 'not required'}",
        )

    risks: List[str] = []
    _append_plan_text(risks, content.get("risks"))
    _append_plan_text(risks, content.get("open_questions"))
    _append_plan_text(risks, uvm.get("open_questions"))
    _append_plan_text(risks, formal.get("open_questions"))
    _append_plan_text(risks, unitsim.get("open_questions"))
    if isinstance(readiness, dict):
        _append_plan_text(risks, readiness.get("warnings"))
        _append_plan_text(risks, readiness.get("blockers"))

    return {
        "summary": summary,
        "assumptions": assumptions,
        "constraints": constraints,
        "risks": risks,
        "readiness_warnings": _plan_text_list(readiness.get("warnings", []))
        if isinstance(readiness, dict)
        else [],
    }


def _test_plan_from_dict(plan_data: Dict[str, Any], module_name: str):
    from services.verification.test_plan import (
        StimulusStep,
        TestCheck,
        TestPlan,
        TestScenario,
    )

    scenarios = []
    for index, raw in enumerate(plan_data.get("scenarios") or []):
        if not isinstance(raw, dict):
            continue
        stimulus = [
            StimulusStep(**step)
            for step in raw.get("stimulus", [])
            if isinstance(step, dict)
        ]
        checks = [
            TestCheck(**check)
            for check in raw.get("checks", [])
            if isinstance(check, dict)
        ]
        scenarios.append(
            TestScenario(
                id=str(raw.get("id") or f"TP-{index + 1:03d}"),
                name=str(raw.get("name") or f"test_{index + 1:03d}"),
                description=str(raw.get("description") or ""),
                requirement_ids=list(raw.get("requirement_ids") or []),
                priority=str(raw.get("priority") or "medium"),
                category=str(raw.get("category") or "functional"),
                precondition=str(raw.get("precondition") or ""),
                stimulus=stimulus,
                checks=checks,
                cleanup=str(raw.get("cleanup") or ""),
            )
        )

    if not scenarios:
        raise HTTPException(
            status_code=400,
            detail="Approved UnitSim plan does not contain any scenarios",
        )

    return TestPlan(
        module_name=str(plan_data.get("module_name") or module_name or "dut"),
        scenarios=scenarios,
        coverage_goals=dict(plan_data.get("coverage_goals") or {}),
        total_scenarios=len(scenarios),
        estimated_sim_time=str(plan_data.get("estimated_sim_time") or ""),
    )


def _apply_uvm_plan_to_model(model_ns, approved_plan: Dict[str, Any]):
    uvm_plan = approved_plan.get("uvm")
    if not isinstance(uvm_plan, dict):
        raise HTTPException(status_code=400, detail="Approved UVM plan is missing")

    sequences = []
    for index, seq in enumerate(uvm_plan.get("sequences") or []):
        if not isinstance(seq, dict):
            continue
        sequences.append(
            {
                "id": seq.get("id") or f"UVM-SEQ-{index + 1:03d}",
                "name": seq.get("name") or f"uvm_sequence_{index + 1}",
                "description": seq.get("description") or "",
                "related_requirements": seq.get("requirement_ids") or [],
                "priority": seq.get("priority") or "medium",
                "status": "approved",
            }
        )

    if not sequences:
        raise HTTPException(
            status_code=400,
            detail="Approved UVM plan does not contain any sequences",
        )

    model_ns.verification.uvm_scenarios = sequences
    model_ns.verification.uvm_agents = list(uvm_plan.get("agents") or [])
    model_ns.verification.uvm_scoreboard_checks = list(uvm_plan.get("scoreboard_checks") or [])
    model_ns.verification.uvm_coverage_points = list(uvm_plan.get("coverage_points") or [])
    model_ns.verification.uvm_files_preview = list(uvm_plan.get("files_preview") or [])
    approved_markdown = (
        approved_plan.get("plan_markdown")
        or approved_plan.get("approved_plan_markdown")
        or uvm_plan.get("plan_markdown")
        or ""
    )
    if approved_markdown:
        model_ns.verification.uvm_plan_markdown = str(approved_markdown)
        model_ns.verification.approved_uvm_plan_markdown = str(approved_markdown)
    return model_ns


def _canonical_signal_map_from_content(content: Optional[Dict[str, Any]]) -> Dict[str, str]:
    """Build a case-insensitive signal map from persisted mental-model facts."""
    signal_map: Dict[str, str] = {}
    if not isinstance(content, dict):
        return signal_map

    def _add(name: Any) -> None:
        raw = str(name or "").strip()
        if not raw:
            return
        signal_map.setdefault(raw.lower(), raw)
        signal_map.setdefault(re.sub(r"[^a-z0-9]+", "", raw.lower()), raw)

    def _add_ports(block: Any) -> None:
        if not isinstance(block, dict):
            return
        for port in block.get("ports", []) or []:
            if isinstance(port, dict):
                _add(port.get("name"))
        for reg in block.get("register_fields", []) or []:
            if not isinstance(reg, dict):
                continue
            _add(reg.get("name"))
            _add(reg.get("write_enable"))
            _add(reg.get("addr_signal"))
            _add(reg.get("data_signal"))
        for item in block.get("expected_behaviors", []) or []:
            if not isinstance(item, dict):
                continue
            for bucket in ("stimulus", "expected_output"):
                values = item.get(bucket)
                if isinstance(values, dict):
                    for key in values:
                        _add(key)

    _add_ports(content.get("design", {}))

    symbol_table = content.get("symbol_table", {})
    if isinstance(symbol_table, dict):
        for symbols in symbol_table.values():
            if isinstance(symbols, list):
                for signal in symbols:
                    _add(signal)

    block_models = content.get("block_models", {})
    if isinstance(block_models, dict):
        for block in block_models.values():
            _add_ports(block)

    return signal_map


def _grounded_signals_from_feedback(feedback: str, signal_map: Dict[str, str]) -> List[str]:
    if not signal_map:
        return []
    words = re.findall(r"[a-zA-Z_][a-zA-Z0-9_]*", feedback or "")
    compact_feedback = re.sub(r"[^a-z0-9]+", "", (feedback or "").lower())
    found: List[str] = []
    for word in words:
        canonical = signal_map.get(word.lower()) or signal_map.get(re.sub(r"[^a-z0-9]+", "", word.lower()))
        if canonical and canonical not in found:
            found.append(canonical)
    if not found:
        for key, canonical in signal_map.items():
            if len(key) >= 3 and key in compact_feedback and canonical not in found:
                found.append(canonical)
            if len(found) >= 6:
                break
    return found[:8]


def _sanitize_uvm_plan_against_content(uvm: Dict[str, Any], content: Optional[Dict[str, Any]]) -> None:
    """Keep user-refined UVM plan references grounded in the mental model."""
    signal_map = _canonical_signal_map_from_content(content)
    if not signal_map:
        return

    def _canonicalize(values: Any) -> List[str]:
        canonical: List[str] = []
        source = values if isinstance(values, list) else []
        for value in source:
            key = str(value or "").strip().lower()
            mapped = signal_map.get(key) or signal_map.get(re.sub(r"[^a-z0-9]+", "", key))
            if mapped and mapped not in canonical:
                canonical.append(mapped)
        return canonical

    for check in uvm.get("scoreboard_checks") or []:
        if isinstance(check, dict):
            signals = _canonicalize(check.get("signals", []))
            if not signals:
                signals = _grounded_signals_from_feedback(
                    f"{check.get('check', '')} {check.get('description', '')}",
                    signal_map,
                )
            if signals:
                check["signals"] = signals

    for point in uvm.get("coverage_points") or []:
        if not isinstance(point, dict):
            continue
        signal = str(point.get("signal") or "").strip()
        raw_signals = point.get("signals", [])
        extra_signals = raw_signals if isinstance(raw_signals, list) else []
        signals = _canonicalize([signal] + extra_signals)
        if not signals:
            signals = _grounded_signals_from_feedback(
                f"{point.get('point', '')} {point.get('description', '')}",
                signal_map,
            )
        if signals:
            point["signal"] = signals[0]
            point["signals"] = signals

    for sequence in uvm.get("sequences") or []:
        if isinstance(sequence, dict) and sequence.get("signals"):
            sequence["signals"] = _canonicalize(sequence.get("signals", []))

    for agent in uvm.get("agents") or []:
        if isinstance(agent, dict) and agent.get("ports"):
            agent["ports"] = _canonicalize(agent.get("ports", []))


def _is_senior_dv_plan_feedback(feedback_lower: str) -> bool:
    quality_terms = (
        "clear",
        "clarity",
        "explain",
        "detail",
        "detailed",
        "broader",
        "complete",
        "comprehensive",
        "reliable",
        "context",
        "thinking",
        "senior",
        "baseline",
        "architecture",
        "strategy",
        "signoff",
        "closure",
    )
    coverage_phrases = (
        "all cases",
        "all the cases",
        "all test cases",
        "covering all",
        "cover all",
        "test cases",
        "corner cases",
        "edge cases",
    )
    return any(term in feedback_lower for term in quality_terms + coverage_phrases)


def _uvm_plan_has_item(items: Any, *needles: str) -> bool:
    if not isinstance(items, list):
        return False
    lowered_needles = [needle.lower() for needle in needles if needle]
    for item in items:
        text = ""
        if isinstance(item, dict):
            text = " ".join(
                str(item.get(field, ""))
                for field in ("name", "id", "description", "check", "point", "category")
            )
        else:
            text = str(item)
        text = text.lower()
        if any(needle in text for needle in lowered_needles):
            return True
    return False


def _apply_senior_dv_uvm_refinement(
    uvm: Dict[str, Any],
    feedback: str,
    content: Optional[Dict[str, Any]],
) -> List[Dict[str, str]]:
    """Broaden a UVM plan into a reviewable senior-DV baseline.

    This is intentionally deterministic and grounded. It does not invent signal
    names; it adds generic verification intents and leaves signal-specific
    checks to existing plan items, the mental model, or open questions.
    """

    actions: List[Dict[str, str]] = []
    design = content.get("design", {}) if isinstance(content, dict) else {}
    if not isinstance(design, dict):
        design = {}
    requirements = content.get("requirements", []) if isinstance(content, dict) else []
    if not isinstance(requirements, list):
        requirements = []
    protocols = design.get("protocols", []) if isinstance(design.get("protocols"), list) else []
    ports = design.get("ports", []) if isinstance(design.get("ports"), list) else []
    top = str(uvm.get("top_module") or design.get("top_module") or "dut")

    req_ids = [
        str(req.get("id"))
        for req in requirements
        if isinstance(req, dict) and req.get("id")
    ][:8]
    protocol_names = [
        str(proto.get("protocol") or proto.get("name") or "").strip().lower()
        for proto in protocols
        if isinstance(proto, dict)
    ]
    protocol_text = " ".join(protocol_names)
    has_protocol = bool(protocol_names)
    has_registers = bool(design.get("register_fields") or design.get("register_map"))
    def _safe_width(port: Dict[str, Any]) -> int:
        try:
            return int(port.get("width") or 1)
        except (TypeError, ValueError):
            return 1

    has_wide_ports = any(
        isinstance(port, dict) and _safe_width(port) > 1
        for port in ports
    )

    objectives = [
        "Reset and initialization reach a known legal state.",
        "Nominal legal operation satisfies each source-grounded requirement.",
        "Boundary values and interface limits are exercised.",
        "Scoreboard/predictor checks observable outputs against expected behavior.",
        "Functional coverage maps requirements, protocol states, boundary bins, and error/stress scenarios.",
        "Compile/simulation/formal evidence feeds back into TruthCore before closure.",
    ]
    uvm["verification_objectives"] = _plan_text_list(
        list(uvm.get("verification_objectives") or []) + objectives
    )

    matrix = list(uvm.get("test_case_matrix") or [])
    if not isinstance(matrix, list):
        matrix = []

    def _add_matrix(category: str, stimulus: str, checker: str, coverage: str) -> None:
        nonlocal matrix
        if _uvm_plan_has_item(matrix, category):
            return
        matrix.append({
            "category": category,
            "intent": f"Verify {category.lower()} behavior for {top}",
            "planned_stimulus": stimulus,
            "checker": checker,
            "coverage": coverage,
            "requirement_ids": req_ids,
            "source": "senior_dv_refinement",
        })

    _add_matrix(
        "Reset and initialization",
        "Assert/deassert reset, then observe legal idle/initial state.",
        "Check all observable state/status outputs settle to the expected reset value.",
        "Reset assertion/deassertion bins and post-reset legal-state bin.",
    )
    _add_matrix(
        "Nominal functional operation",
        "Drive legal transactions or operations described by the mental model.",
        "Compare outputs/status against expected behaviors and transaction flows.",
        "Functional bins for each mapped requirement and representative legal operation.",
    )
    if has_wide_ports:
        _add_matrix(
            "Boundary and data range",
            "Exercise min/max/walking/random values on multi-bit data/address/control fields.",
            "Check no overflow/underflow/out-of-range response unless explicitly allowed.",
            "Min, max, zero, all-ones, and representative random bins.",
        )
    if has_protocol:
        _add_matrix(
            "Protocol and backpressure",
            f"Exercise legal handshakes/orderings for {', '.join(protocol_names[:4])}.",
            "Check request/response ordering, payload stability while stalled, and legal ready/valid timing where present.",
            "Protocol state, channel, backpressure, and transaction-order crosses.",
        )
    if has_registers:
        _add_matrix(
            "Register and CSR access",
            "Perform reset, read, write, readback, and access-policy tests for parsed registers.",
            "Check mirrored/reference register model against observed bus/status behavior.",
            "Register access, reset value, read/write policy, and field toggle bins.",
        )
    _add_matrix(
        "Illegal/error/corner behavior",
        "Try invalid operations only where the spec or open questions allow them.",
        "Check legal error/status response or escalate as an open question if undefined.",
        "Error, illegal access, overflow/underflow, and ignored-operation bins where applicable.",
    )
    _add_matrix(
        "Stress and randomized regression",
        "Combine legal operations with constrained-random ordering and repeated transactions.",
        "Check scoreboard remains clean across long runs and no stale state leaks between operations.",
        "Cross coverage over operation type, status response, and boundary data classes.",
    )
    uvm["test_case_matrix"] = matrix[:10]

    sequences = uvm.setdefault("sequences", [])
    if not isinstance(sequences, list):
        sequences = []
        uvm["sequences"] = sequences

    desired_sequences = [
        ("seq_reset_initialization", "Reset/deassertion smoke sequence that verifies known initial state."),
        ("seq_nominal_functional_operation", "Directed legal-operation sequence mapped to the main requirements."),
        ("seq_boundary_and_range", "Boundary-value sequence for counters, addresses, data widths, and legal limits."),
        ("seq_illegal_error_corner", "Corner/error sequence for undefined, overflow, underflow, or invalid operation handling."),
        ("seq_stress_randomized_regression", "Constrained-random stress sequence combining legal operations over a longer run."),
    ]
    if has_protocol:
        desired_sequences.insert(
            3,
            (
                "seq_protocol_backpressure",
                "Protocol/backpressure sequence for ready/valid, request/response ordering, and stalled payload stability.",
            ),
        )
    if "tlul" in protocol_text or "tilelink" in protocol_text:
        desired_sequences.insert(
            4,
            (
                "seq_tlul_a_d_channel_ordering",
                "TileLink-UL A-channel request and D-channel response sequence with stalled valid/ready cases.",
            ),
        )

    added_sequences = 0
    for name, description in desired_sequences:
        if _uvm_plan_has_item(sequences, name):
            continue
        sequences.append({
            "id": f"UVM-SEQ-{len(sequences) + 1:03d}",
            "name": name,
            "description": description,
            "requirement_ids": req_ids,
            "priority": "high",
            "source": "senior_dv_refinement",
        })
        added_sequences += 1

    if added_sequences:
        actions.append({
            "action": "broaden",
            "description": f"Added {added_sequences} senior-DV scenario(s) to broaden test-case coverage",
        })

    uvm["checking_strategy"] = _plan_text_list(list(uvm.get("checking_strategy") or []) + [
        "Use monitors to publish observed transactions into the scoreboard.",
        "Use requirement-linked scoreboard checks for observable behavior.",
        "Use assertions or open questions for behavior that cannot be observed in simulation.",
        "Reject or repair generated files that reference non-generated packages/classes or non-grounded signals.",
    ])
    uvm["coverage_strategy"] = _plan_text_list(list(uvm.get("coverage_strategy") or []) + [
        "Cover reset, legal operations, boundary values, status/error paths, and stress combinations.",
        "Cross protocol phase/state with response/status when protocol metadata exists.",
        "Track requirement-to-sequence/check/coverage traceability before sign-off.",
    ])
    uvm["closure_criteria"] = _plan_text_list(list(uvm.get("closure_criteria") or []) + [
        "All planned generated files compile or have repair evidence attached.",
        "Every high-priority requirement has at least one sequence and one checker/assertion or an explicit waiver/open question.",
        "Functional coverage goals have bins for nominal, boundary, corner, and stress scenarios.",
        "Uploaded simulator logs are parsed and repair patches are reviewed before closure.",
    ])
    uvm["last_refinement_feedback"] = feedback
    actions.append({
        "action": "clarify",
        "description": "Rebuilt UVM plan as a broader senior-DV baseline",
    })
    return actions


def _refine_uvm_plan(
    plan: Dict[str, Any],
    feedback: str,
    content: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Refine the outer staged plan dict, updating its nested ``uvm`` section."""
    import uuid

    uvm = plan.get("uvm")
    if not isinstance(uvm, dict):
        return {"actions": [], "plan": plan}

    feedback_clean = (feedback or "").strip()
    feedback_lower = feedback_clean.lower()
    actions = []
    signal_map = _canonical_signal_map_from_content(content)

    def _safe_name(prefix: str, text: str) -> str:
        words = re.sub(r"[^a-z0-9_\s]", "", text.lower()).split()[:6]
        return f"{prefix}_{'_'.join(words) or 'refinement'}"

    def _target_words(extra_stop_words: set[str]) -> List[str]:
        stop_words = {
            "remove", "delete", "drop", "skip", "the", "a", "an", "for", "from",
            "uvm", "test", "item",
        } | extra_stop_words
        return [
            word
            for word in re.sub(r"[^a-z0-9_\s]", " ", feedback_lower).split()
            if word and word not in stop_words
        ]

    def _remove_from_bucket(
        bucket: str,
        label: str,
        words: List[str],
        fields: tuple[str, ...],
    ) -> None:
        before = len(uvm.get(bucket) or [])
        if not before or not words:
            return
        kept = []
        for item in uvm.get(bucket) or []:
            if not isinstance(item, dict):
                kept.append(item)
                continue
            haystack = " ".join(str(item.get(field, "")) for field in fields).lower()
            if any(word in haystack for word in words):
                continue
            kept.append(item)
        uvm[bucket] = kept
        removed = before - len(kept)
        if removed:
            actions.append({"action": "remove", "description": f"Removed {removed} UVM {label}(s)"})

    if _is_senior_dv_plan_feedback(feedback_lower) and not any(
        word in feedback_lower for word in ("remove", "delete", "drop", "skip")
    ):
        actions.extend(_apply_senior_dv_uvm_refinement(uvm, feedback_clean, content))

    elif any(word in feedback_lower for word in ("remove", "delete", "drop", "skip")):
        if "scoreboard" in feedback_lower or "check" in feedback_lower:
            words = _target_words({"scoreboard", "check", "checks"})
            _remove_from_bucket("scoreboard_checks", "scoreboard check", words, ("check", "description"))
        elif "coverage" in feedback_lower or "cover" in feedback_lower:
            words = _target_words({"coverage", "cover", "coverpoint", "point", "points"})
            _remove_from_bucket("coverage_points", "coverage point", words, ("point", "description", "signal"))
        elif "agent" in feedback_lower:
            words = _target_words({"agent", "agents"})
            _remove_from_bucket("agents", "agent", words, ("name", "description"))
        else:
            words = _target_words({"sequence", "sequences", "scenario", "scenarios"})
            _remove_from_bucket("sequences", "sequence", words, ("name", "description"))

    elif "scoreboard" in feedback_lower or "check" in feedback_lower:
        signals = _grounded_signals_from_feedback(feedback_clean, signal_map)
        if not signals and signal_map:
            uvm.setdefault("open_questions", []).append(
                "Scoreboard refinement needs a signal captured in the mental model before it can be trusted."
            )
            actions.append({
                "action": "needs_grounding",
                "description": "Scoreboard feedback did not name a mental-model signal",
            })
            plan["uvm"] = uvm
            return {"actions": actions, "plan": plan}
        check = {
            "check": _safe_name("check", feedback_clean),
            "description": feedback_clean,
            "signals": signals,
            "source": "user_refinement_grounded" if signals else "user_refinement",
        }
        uvm.setdefault("scoreboard_checks", []).append(check)
        actions.append({"action": "add", "description": f"Added scoreboard check {check['check']}"})

    elif "coverage" in feedback_lower or "cover" in feedback_lower:
        signals = _grounded_signals_from_feedback(feedback_clean, signal_map)
        if not signals and signal_map:
            uvm.setdefault("open_questions", []).append(
                "Coverage refinement needs a signal captured in the mental model before it can be trusted."
            )
            actions.append({
                "action": "needs_grounding",
                "description": "Coverage feedback did not name a mental-model signal",
            })
            plan["uvm"] = uvm
            return {"actions": actions, "plan": plan}
        point = {
            "point": _safe_name("cp", feedback_clean),
            "type": "functional",
            "description": feedback_clean,
            "signal": signals[0] if signals else "",
            "signals": signals,
            "source": "user_refinement_grounded" if signals else "user_refinement",
        }
        uvm.setdefault("coverage_points", []).append(point)
        actions.append({"action": "add", "description": f"Added coverage point {point['point']}"})

    else:
        signals = _grounded_signals_from_feedback(feedback_clean, signal_map)
        sequence = {
            "id": f"UVM-SEQ-{str(uuid.uuid4())[:8]}",
            "name": _safe_name("seq", feedback_clean),
            "description": feedback_clean,
            "priority": "high" if "high" in feedback_lower else "medium",
            "signals": signals,
            "source": "user_refinement_grounded" if signals else "user_refinement",
        }
        uvm.setdefault("sequences", []).append(sequence)
        actions.append({"action": "add", "description": f"Added UVM sequence {sequence['name']}"})

    _sanitize_uvm_plan_against_content(uvm, content)
    plan["uvm"] = uvm
    return {"actions": actions, "plan": plan}


def _existing_staged_artifact_keys(
    db: Session,
    project: Project,
) -> set[tuple[str, str, str, str]]:
    artifacts = (
        db.query(ProjectArtifact)
        .filter(
            ProjectArtifact.project_id == project.id,
            ProjectArtifact.artifact_type == "generated",
        )
        .all()
    )
    keys: set[tuple[str, str, str, str]] = set()

    for artifact in artifacts:
        if not artifact.metadata_json:
            continue
        try:
            metadata = json.loads(artifact.metadata_json)
        except Exception:
            continue
        if metadata.get("provided_as") != "staged_verification":
            continue

        model_revision_id = str(metadata.get("mental_model_revision_id") or "")
        verification_type = str(metadata.get("verification_type") or "")
        relative_path = _normalize_relative_artifact_path(
            str(metadata.get("source_relative_path") or metadata.get("relative_path") or "")
        )
        checksum = str(artifact.checksum_sha256 or "")
        if model_revision_id and verification_type and relative_path and checksum:
            keys.add((model_revision_id, verification_type, relative_path, checksum))

    return keys


def _publish_staged_generated_files(
    db: Session,
    project: Project,
    current_user: User,
    *,
    output_root: Path,
    file_paths: List[str],
    mental_model_revision_id: str,
    verification_type: str,
    phase: str,
) -> Dict[str, Any]:
    """Copy staged output files into the project generated artifact system."""
    existing_keys = _existing_staged_artifact_keys(db, project)
    created_artifacts: List[Dict[str, Any]] = []
    skipped_count = 0

    for raw_path in file_paths:
        if not raw_path:
            continue
        file_path = Path(raw_path)
        if not file_path.exists() or not file_path.is_file():
            continue

        try:
            source_relative = file_path.relative_to(output_root).as_posix()
        except ValueError:
            source_relative = file_path.name
        source_relative = _normalize_relative_artifact_path(source_relative)
        artifact_relative = _normalize_relative_artifact_path(
            f"staged/{source_relative}"
        )

        raw_bytes = file_path.read_bytes()
        if not raw_bytes:
            skipped_count += 1
            continue

        import hashlib

        checksum = hashlib.sha256(raw_bytes).hexdigest()
        dedupe_key = (
            mental_model_revision_id,
            verification_type,
            source_relative,
            checksum,
        )
        if dedupe_key in existing_keys:
            skipped_count += 1
            continue

        content_type = _guess_content_type(file_path)
        editable = _is_text_content_type(content_type) or _is_probably_text_bytes(raw_bytes)
        artifact, _ = _create_project_artifact_revision(
            db,
            project,
            current_user,
            artifact_type="generated",
            raw_bytes=raw_bytes,
            filename=file_path.name,
            source="staged_verification",
            content_type=content_type,
            metadata={
                "provided_as": "staged_verification",
                "relative_path": artifact_relative,
                "source_relative_path": source_relative,
                "mental_model_revision_id": mental_model_revision_id,
                "verification_type": verification_type,
                "phase": phase,
                "editable": editable,
            },
        )
        created_artifacts.append(_serialize_project_artifact(artifact))
        existing_keys.add(dedupe_key)

    pointer = _get_project_artifact_pointer(db, project.id)
    return {
        "created_count": len(created_artifacts),
        "skipped_count": skipped_count,
        "artifacts": created_artifacts,
        "active": _serialize_artifact_pointer(pointer),
    }


# ─── Endpoints ────────────────────────────────────────────────────────


def _plan_filename_slug(value: Any, fallback: str = "plan") -> str:
    text = re.sub(r"[^A-Za-z0-9_-]+", "_", str(value or "").strip()).strip("_")
    return (text or fallback)[:64]


def _markdown_scalar(value: Any, *, max_chars: int = 600) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        text = json.dumps(value, indent=2, ensure_ascii=True, default=str)
    else:
        text = str(value)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > max_chars:
        return text[: max_chars - 3].rstrip() + "..."
    return text


def _markdown_list(value: Any) -> List[Any]:
    if isinstance(value, list):
        return value
    if value:
        return [value]
    return []


def _append_markdown_bullets(
    lines: List[str],
    title: str,
    items: Any,
    *,
    fields: tuple[str, ...] = ("name", "id", "description"),
    limit: int = 24,
) -> None:
    values = _markdown_list(items)
    if not values:
        return
    lines.extend(["", f"## {title}"])
    for item in values[:limit]:
        if isinstance(item, dict):
            label = ""
            for field in fields:
                if item.get(field):
                    label = _markdown_scalar(item.get(field), max_chars=240)
                    break
            detail_parts = []
            for key in ("description", "text", "goal", "intent", "requirement_ids", "signals"):
                if key in item and item.get(key) and key not in fields:
                    detail_parts.append(f"{key}: {_markdown_scalar(item.get(key), max_chars=280)}")
            detail = f" - {'; '.join(detail_parts)}" if detail_parts else ""
            lines.append(f"- {label or _markdown_scalar(item, max_chars=240)}{detail}")
        else:
            lines.append(f"- {_markdown_scalar(item, max_chars=360)}")
    if len(values) > limit:
        lines.append(f"- ... {len(values) - limit} more")


def _markdown_table_cell(value: Any, *, max_chars: int = 180) -> str:
    if isinstance(value, (list, tuple, set)):
        text = ", ".join(
            _markdown_scalar(item, max_chars=max_chars)
            for item in value
            if _markdown_scalar(item, max_chars=max_chars)
        )
        if len(text) > max_chars:
            text = text[: max_chars - 1].rstrip() + "…"
    else:
        text = _markdown_scalar(value, max_chars=max_chars)
    return text.replace("|", "\\|") or "-"


def _append_markdown_table(
    lines: List[str],
    title: str,
    items: Any,
    columns: List[tuple[str, str]],
    *,
    limit: int = 24,
) -> None:
    values = [item for item in _markdown_list(items) if isinstance(item, dict)]
    if not values:
        return
    lines.extend(["", f"## {title}", ""])
    lines.append("| " + " | ".join(label for label, _field in columns) + " |")
    lines.append("| " + " | ".join("---" for _label, _field in columns) + " |")
    for item in values[:limit]:
        cells = []
        for _label, field in columns:
            if field == "requirements":
                value = item.get("requirement_ids") or item.get("requirements") or []
            elif field == "signals":
                value = item.get("signals") or item.get("signal") or item.get("ports") or []
            elif field == "category":
                value = item.get("category") or item.get("name") or item.get("id") or item.get("check") or item.get("point")
            elif field == "intent":
                value = item.get("intent") or item.get("description") or item.get("goal") or item.get("text")
            elif field == "planned_stimulus":
                value = item.get("planned_stimulus") or item.get("stimulus") or item.get("sequence") or item.get("description")
            elif field == "checker":
                value = item.get("checker") or item.get("check") or item.get("expected") or item.get("expected_behavior") or item.get("description")
            elif field == "coverage":
                value = item.get("coverage") or item.get("coverage_goal") or item.get("coverage_point") or item.get("point") or item.get("description")
            elif field == "bins":
                value = item.get("bins") or item.get("crosses") or item.get("values") or item.get("description")
            else:
                value = item.get(field)
            cells.append(_markdown_table_cell(value))
        lines.append("| " + " | ".join(cells) + " |")
    if len(values) > limit:
        lines.append(f"\n_Only first {limit} entries shown; {len(values) - limit} more remain in the JSON appendix._")


def _plan_artifact_group(
    *,
    verification_type: str,
    mental_model_revision_id: str,
    top_module: str,
) -> str:
    return ":".join(
        [
            "staged_verification_plan",
            _plan_filename_slug(verification_type, "verification"),
            str(mental_model_revision_id or "unknown"),
            _plan_filename_slug(top_module, "dut"),
        ]
    )


def _attach_plan_markdown_reference(
    plan: Dict[str, Any],
    artifact: Optional[Dict[str, Any]],
) -> None:
    if not isinstance(plan, dict) or not artifact:
        return
    metadata = artifact.get("metadata") if isinstance(artifact.get("metadata"), dict) else {}
    ref = {
        "artifact_id": artifact.get("id"),
        "filename": artifact.get("filename"),
        "revision": artifact.get("revision"),
        "plan_group": metadata.get("plan_group"),
        "content_type": artifact.get("content_type"),
    }
    plan["plan_markdown_artifact"] = {k: v for k, v in ref.items() if v is not None}
    plan["plan_markdown_artifact_id"] = artifact.get("id")
    plan["plan_markdown_filename"] = artifact.get("filename")
    if metadata.get("plan_group"):
        plan["plan_markdown_group"] = metadata.get("plan_group")


def _artifact_metadata_dict(artifact: ProjectArtifact) -> Dict[str, Any]:
    if not artifact.metadata_json:
        return {}
    try:
        parsed = json.loads(artifact.metadata_json)
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


def _read_artifact_text(artifact: ProjectArtifact) -> str:
    path = Path(artifact.file_path or "")
    if not path.exists() or not path.is_file():
        return ""
    return path.read_text(encoding="utf-8", errors="replace")


def _find_latest_plan_markdown_artifact(
    db: Session,
    project: Project,
    approved_plan: Dict[str, Any],
) -> Optional[ProjectArtifact]:
    if not isinstance(approved_plan, dict):
        return None
    artifact_ref = approved_plan.get("plan_markdown_artifact")
    artifact_id = (
        approved_plan.get("plan_markdown_artifact_id")
        or (artifact_ref.get("artifact_id") if isinstance(artifact_ref, dict) else None)
    )
    group = approved_plan.get("plan_markdown_group")
    if not group and isinstance(artifact_ref, dict):
        group = artifact_ref.get("plan_group")

    candidates = (
        db.query(ProjectArtifact)
        .filter(
            ProjectArtifact.project_id == project.id,
            ProjectArtifact.organization_id == project.organization_id,
            ProjectArtifact.artifact_type == "generated",
        )
        .order_by(ProjectArtifact.created_at.desc(), ProjectArtifact.revision.desc())
        .limit(400)
        .all()
    )

    if group:
        for artifact in candidates:
            metadata = _artifact_metadata_dict(artifact)
            if metadata.get("plan_group") == group:
                return artifact

    if artifact_id:
        for artifact in candidates:
            metadata = _artifact_metadata_dict(artifact)
            if artifact.id == artifact_id or metadata.get("parent_artifact_id") == artifact_id:
                return artifact
        return (
            db.query(ProjectArtifact)
            .filter(
                ProjectArtifact.id == artifact_id,
                ProjectArtifact.project_id == project.id,
                ProjectArtifact.organization_id == project.organization_id,
            )
            .first()
        )
    return None


def _hydrate_latest_plan_markdown(
    db: Session,
    project: Project,
    approved_plan: Dict[str, Any],
) -> Dict[str, Any]:
    plan = copy.deepcopy(approved_plan) if isinstance(approved_plan, dict) else {}
    artifact = _find_latest_plan_markdown_artifact(db, project, plan)
    markdown = _read_artifact_text(artifact) if artifact else ""
    if markdown.strip():
        plan["plan_markdown"] = markdown
        plan["approved_plan_markdown"] = markdown
        plan["plan_markdown_artifact_id"] = artifact.id
        plan["plan_markdown_filename"] = artifact.filename
        metadata = _artifact_metadata_dict(artifact)
        if metadata.get("plan_group"):
            plan["plan_markdown_group"] = metadata.get("plan_group")
        if isinstance(plan.get("uvm"), dict):
            plan["uvm"]["plan_markdown"] = markdown
            plan["uvm"]["plan_markdown_artifact_id"] = artifact.id
    return plan


def _render_verification_plan_markdown(
    *,
    project: Project,
    verification_type: str,
    plan: Dict[str, Any],
    model_content: Dict[str, Any],
    mental_model_revision_id: str,
    refined_feedback: str = "",
) -> str:
    design = model_content.get("design") if isinstance(model_content.get("design"), dict) else {}
    presentation = plan.get("presentation") if isinstance(plan.get("presentation"), dict) else {}
    top_module = (
        plan.get("module_name")
        or plan.get("top_module")
        or design.get("top_module")
        or "unknown"
    )
    generated_at = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime())
    title_type = str(verification_type or plan.get("verification_type") or "verification").upper()

    lines: List[str] = [
        f"# Verification Plan - {title_type}",
        "",
        f"- Project: {getattr(project, 'name', '') or project.id}",
        f"- Top module: {top_module}",
        f"- Mental model revision: {mental_model_revision_id}",
        f"- Generated at: {generated_at}",
    ]
    if refined_feedback:
        lines.append(f"- Refined from feedback: {_markdown_scalar(refined_feedback, max_chars=500)}")

    summary = _markdown_scalar(presentation.get("summary") or plan.get("summary"), max_chars=1600)
    if summary:
        lines.extend(["", "## What I Understood", "", summary])

    for label, key in (
        ("Assumptions", "assumptions"),
        ("Constraints", "constraints"),
        ("Risks And Open Items", "risks"),
    ):
        values = presentation.get(key)
        if values:
            lines.extend(["", f"## {label}"])
            for value in _markdown_list(values):
                lines.append(f"- {_markdown_scalar(value, max_chars=500)}")

    requirements = model_content.get("requirements") if isinstance(model_content.get("requirements"), list) else []
    if requirements:
        lines.extend(["", "## Source Requirements"])
        for req in requirements[:16]:
            if isinstance(req, dict):
                rid = _markdown_scalar(req.get("id") or "", max_chars=80)
                text = _markdown_scalar(req.get("text") or req.get("description") or "", max_chars=500)
                lines.append(f"- {rid + ': ' if rid else ''}{text}")
            else:
                lines.append(f"- {_markdown_scalar(req, max_chars=500)}")
        if len(requirements) > 16:
            lines.append(f"- ... {len(requirements) - 16} more")

    unitsim = plan.get("unitsim") if isinstance(plan.get("unitsim"), dict) else None
    if unitsim:
        lines.extend(["", "## Unit Simulation Plan"])
        if unitsim.get("summary"):
            lines.append(_markdown_scalar(unitsim.get("summary"), max_chars=800))
        _append_markdown_bullets(
            lines,
            "Unit Simulation Scenarios",
            unitsim.get("scenarios"),
            fields=("name", "id", "title", "description"),
        )

    formal = plan.get("formal") if isinstance(plan.get("formal"), dict) else None
    if formal:
        lines.extend(["", "## Formal Verification Plan"])
        if formal.get("strategy"):
            lines.append(f"- Strategy: {_markdown_scalar(formal.get('strategy'), max_chars=500)}")
        _append_markdown_bullets(
            lines,
            "Formal Properties",
            formal.get("properties") or formal.get("assertions"),
            fields=("name", "id", "property", "description"),
        )
        _append_markdown_bullets(
            lines,
            "Formal Covers",
            formal.get("covers"),
            fields=("name", "id", "cover", "description"),
        )

    uvm = plan.get("uvm") if isinstance(plan.get("uvm"), dict) else None
    if uvm:
        lines.extend(["", "## UVM Plan"])
        lines.extend(
            [
                "",
                "This Markdown file is the editable execution baseline. Generated UVM agents, "
                "sequences, scoreboards, coverage collectors, compile repair, and later log "
                "analysis should follow the latest saved version of this plan.",
            ]
        )
        generation_policy = uvm.get("generation_policy")
        if isinstance(generation_policy, dict):
            lines.append(f"- Tier: {_markdown_scalar(generation_policy.get('tier'), max_chars=120)}")
            lines.append(f"- Multi-agent: {_markdown_scalar(generation_policy.get('multi_agent'), max_chars=120)}")
            rationale = generation_policy.get("rationale")
            if rationale:
                lines.extend(["", "### Policy Rationale"])
                for value in _markdown_list(rationale):
                    lines.append(f"- {_markdown_scalar(value, max_chars=500)}")
        _append_markdown_bullets(lines, "Verification Objectives", uvm.get("verification_objectives"), fields=("name", "id"))
        _append_markdown_bullets(lines, "Test Case Matrix", uvm.get("test_case_matrix"), fields=("category", "intent"))
        _append_markdown_bullets(lines, "UVM Agents", uvm.get("agents"), fields=("name", "role", "interface", "description"))
        _append_markdown_bullets(lines, "UVM Sequences", uvm.get("sequences"), fields=("name", "id", "description"))
        _append_markdown_bullets(lines, "Scoreboard Checks", uvm.get("scoreboard_checks"), fields=("check", "name", "id", "description"))
        _append_markdown_bullets(lines, "Coverage Points", uvm.get("coverage_points"), fields=("point", "name", "id", "description"))
        _append_markdown_bullets(lines, "Checking Strategy", uvm.get("checking_strategy"), fields=("name", "id"))
        _append_markdown_bullets(lines, "Coverage Strategy", uvm.get("coverage_strategy"), fields=("name", "id"))
        _append_markdown_bullets(lines, "Closure Criteria", uvm.get("closure_criteria"), fields=("name", "id"))
        _append_markdown_bullets(lines, "Planned Files", uvm.get("files_preview"), fields=("path", "name"))

        _append_markdown_table(
            lines,
            "UVM Agent Architecture",
            uvm.get("agents"),
            [
                ("Agent", "name"),
                ("Role", "type"),
                ("Interface / Ports", "signals"),
                ("Responsibility", "description"),
            ],
        )
        _append_markdown_table(
            lines,
            "Stimulus And Scenario Matrix",
            uvm.get("test_case_matrix") or uvm.get("sequences"),
            [
                ("Category / Sequence", "category"),
                ("Intent", "intent"),
                ("Stimulus", "planned_stimulus"),
                ("Checker", "checker"),
                ("Coverage", "coverage"),
                ("Req IDs", "requirements"),
            ],
        )
        _append_markdown_table(
            lines,
            "Scoreboard And Checker Plan",
            uvm.get("scoreboard_checks"),
            [
                ("Check", "check"),
                ("Expected behavior", "description"),
                ("Signals", "signals"),
                ("Req IDs", "requirements"),
            ],
        )
        _append_markdown_table(
            lines,
            "Functional Coverage Plan",
            uvm.get("coverage_points"),
            [
                ("Coverage point", "point"),
                ("Type", "type"),
                ("Signal", "signal"),
                ("Bins / Crosses", "bins"),
                ("Req IDs", "requirements"),
            ],
        )

        open_questions = _markdown_list(uvm.get("open_questions") or model_content.get("open_questions"))
        if open_questions:
            lines.extend(["", "## Open Questions Before Closure"])
            for item in open_questions[:20]:
                lines.append(f"- {_markdown_scalar(item, max_chars=500)}")

        lines.extend(
            [
                "",
                "## Review And Edit Notes",
                "",
                "- Edit this Markdown plan before clicking **Implement** if the intended tests, checkers, coverage, or closure gates are incomplete.",
                "- Keep requirement IDs and signal names grounded to TruthCore facts where possible.",
                "- If a behavior is ambiguous, add it under Open Questions instead of inventing expected behavior.",
            ]
        )

    lines.extend([
        "",
        "## Plan JSON",
        "",
        "```json",
        json.dumps(plan, indent=2, ensure_ascii=True, default=str),
        "```",
        "",
    ])
    return "\n".join(lines)


def _create_plan_markdown_artifact(
    db: Session,
    project: Project,
    current_user: User,
    *,
    verification_type: str,
    plan: Dict[str, Any],
    model_content: Dict[str, Any],
    mental_model_revision_id: str,
    refined_feedback: str = "",
) -> Dict[str, Any]:
    design = model_content.get("design") if isinstance(model_content.get("design"), dict) else {}
    top_module = plan.get("module_name") or plan.get("top_module") or design.get("top_module") or "dut"
    timestamp = time.strftime("%Y%m%d_%H%M%S", time.gmtime())
    plan_group = _plan_artifact_group(
        verification_type=verification_type,
        mental_model_revision_id=mental_model_revision_id,
        top_module=str(top_module),
    )
    filename = (
        f"verification_plan_"
        f"{_plan_filename_slug(verification_type, 'verification')}_"
        f"{_plan_filename_slug(top_module, 'dut')}_"
        f"{timestamp}.md"
    )
    markdown = _render_verification_plan_markdown(
        project=project,
        verification_type=verification_type,
        plan=plan,
        model_content=model_content,
        mental_model_revision_id=mental_model_revision_id,
        refined_feedback=refined_feedback,
    )
    artifact, _ = _create_project_artifact_revision(
        db,
        project,
        current_user,
        artifact_type="generated",
        raw_bytes=markdown.encode("utf-8"),
        filename=filename,
        source="staged_verification",
        content_type="text/markdown",
        metadata={
            "provided_as": "staged_verification_plan",
            "relative_path": _normalize_relative_artifact_path(f"staged/plans/{filename}"),
            "source_relative_path": _normalize_relative_artifact_path(f"plans/{filename}"),
            "mental_model_revision_id": mental_model_revision_id,
            "verification_type": verification_type,
            "plan_group": plan_group,
            "plan_kind": "staged_verification_markdown",
            "top_module": str(top_module),
            "phase": "plan_refined" if refined_feedback else "plan",
            "editable": True,
        },
    )
    return _serialize_project_artifact(artifact)


@router.post("/{project_id}/verification/prepare")
async def prepare_verification(
    project_id: str,
    request: PrepareRequest,
    current_user: User = Depends(_get_current_user),
    db: Session = Depends(get_db),
):
    """
    Stage 1: Build/refresh mental model + return strategy recommendation.
    Does NOT start any verification.
    """
    from services.mental_model.store import (
        get_latest_mental_model_revision,
        evaluate_mental_model_freshness,
        create_llm_enriched_mental_model_revision,
    )
    from services.verification.strategy_recommender import recommend_verification_strategy

    project = _require_project_access(db, current_user, project_id)
    spec_artifact, rtl_artifact = _resolve_active_artifacts(db, project)

    if not spec_artifact:
        raise HTTPException(
            status_code=400,
            detail="No specification file found in this project. Upload a spec document, then build the mental model again.",
        )
    if not rtl_artifact:
        raise HTTPException(
            status_code=400,
            detail="No RTL files found in this project. Upload RTL (or an RTL folder), then build the mental model again.",
        )

    # Build or reuse an LLM-enriched mental model. A fresh parser-only revision
    # is not enough for the staged ChipStack flow because strategy and planning
    # should be driven by design intent, not only structural facts.
    latest = get_latest_mental_model_revision(db, project.id)
    freshness = evaluate_mental_model_freshness(latest, spec_artifact, rtl_artifact)
    latest_content = _load_model_content(latest) if latest is not None else {}
    latest_is_llm_enriched = (
        latest_content.get("build_mode") in {"llm_enriched", "llm_attempted_source_grounded"}
        and latest_content.get("llm_status") in {
            "parsed",
            "multi_block_parsed",
            "multi_block_partial",
            "structure_with_llm_attempt",
        }
    )

    if freshness["fresh"] and latest is not None and latest_is_llm_enriched:
        model_revision = latest
        model_status = "reused"
    else:
        try:
            model_revision, _content, _summary = await create_llm_enriched_mental_model_revision(
                db,
                project=project,
                user=current_user,
                spec_artifact=spec_artifact,
                rtl_artifact=rtl_artifact,
                target_module=request.target_module,
                require_llm=True,
            )
        except RuntimeError as exc:
            db.rollback()
            raise HTTPException(status_code=503, detail="Verification service unavailable") from exc
        except Exception as exc:
            db.rollback()
            logger.exception("LLM mental model preparation failed: %s", exc)
            detail = str(exc).strip() or exc.__class__.__name__
            raise HTTPException(
                status_code=500,
                detail=f"LLM mental model preparation failed: {detail}",
            ) from exc
        db.commit()
        model_status = "rebuilt_llm_enriched"

    # Load content
    content = _load_model_content(model_revision)
    from services.mental_model.living_agent import assess_mental_model_readiness

    mental_model_readiness = assess_mental_model_readiness(
        content,
        stage="prepare",
        verification_type="",
    )

    # Get strategy recommendation — pass content dict directly (recommender now handles dicts)
    strategy = recommend_verification_strategy(content)

    return {
        "status": "prepared",
        "project_id": project.id,
        "model_status": model_status,
        "llm_status": content.get("llm_status"),
        "llm_structured": content.get("llm_structured"),
        "llm_warning": content.get("llm_warning"),
        "llm_provider": content.get("llm_provider"),
        "llm_model": content.get("llm_model"),
        "mental_model_revision_id": model_revision.id,
        "mental_model_revision": model_revision.revision,
        "mental_model": {
            "id": model_revision.id,
            "revision": model_revision.revision,
            "status": getattr(model_revision, "status", "active"),
            "content": content,
        },
        "mental_model_readiness": mental_model_readiness,
        "recommendation": strategy.to_dict(),
    }


@router.post("/{project_id}/verification/plan")
async def generate_plan(
    project_id: str,
    request: PlanRequest,
    current_user: User = Depends(_get_current_user),
    db: Session = Depends(get_db),
):
    """
    Stage 2: Generate a reviewable plan for the chosen verification type.
    Returns plan JSON — does NOT execute anything.
    """
    from services.verification.test_plan import generate_test_plan_with_ai
    from services.verification.uvm_gen import generate_uvm_plan_with_ai
    from services.verification.formal_gen import generate_formal_plan_with_ai

    project = _require_project_access(db, current_user, project_id)
    model_rev = _require_model_revision(
        db, project=project, mental_model_revision_id=request.mental_model_revision_id
    )
    from services.mental_model.living_agent import (
        assess_mental_model_readiness,
        observe_mental_model_event,
    )

    content = _load_model_content(model_rev)
    model_ns = _normalize_for_recommender(content)
    vtype = _validate_verification_type(request.verification_type)
    if request.target_module:
        model_ns.design.top_module = request.target_module

    plan = {
        "verification_type": vtype,
        "module_name": model_ns.design.top_module,
        "mental_model_revision_id": model_rev.id,
    }
    mental_model_observations: List[Dict[str, Any]] = []
    mental_model_readiness = assess_mental_model_readiness(
        content,
        stage="plan",
        verification_type=vtype,
    )
    mental_model_observations.append(
        await _observe_mental_model_event_safely(
            observe_mental_model_event,
            db,
            model_revision=model_rev,
            user=current_user,
            event_type="mental_model.readiness.checked",
            payload={
                "verification_type": vtype,
                "agent": "mental_model",
                "readiness": mental_model_readiness,
            },
            apply_approved_intent=False,
            commit=False,
        )
    )
    if not mental_model_readiness.get("safe"):
        db.commit()
        db.refresh(model_rev)
        raise HTTPException(
            status_code=409,
            detail={
                "message": "Mental model is not ready to drive verification planning.",
                "readiness": mental_model_readiness,
            },
        )
    plan_ai_client = None
    plan_llm_metadata: Dict[str, Any] = {}

    async def _require_plan_ai_client():
        nonlocal plan_ai_client, plan_llm_metadata
        if plan_ai_client is None:
            from services.mental_model.store import get_default_ai_client_adapter

            plan_ai_client, plan_llm_metadata = await get_default_ai_client_adapter(
                require_llm=True,
            )
        return plan_ai_client, plan_llm_metadata

    mental_model_observations.append(
        await _observe_mental_model_event_safely(
            observe_mental_model_event,
            db,
            model_revision=model_rev,
            user=current_user,
            event_type="verification.strategy_selected",
            payload={
                "verification_type": vtype,
                "agent": "staged_verification",
                "plan": plan,
            },
            commit=False,
        )
    )

    if vtype in ("unitsim", "all"):
        try:
            ai_client, llm_metadata = await _require_plan_ai_client()
            tp = await generate_test_plan_with_ai(
                model_ns,
                ai_client=ai_client,
                require_llm=False,
            )
            plan["unitsim"] = _serialize_test_plan(tp)
            plan["unitsim"]["llm_provider"] = llm_metadata.get("provider")
            plan["unitsim"]["llm_model"] = llm_metadata.get("model")
            _attach_knowledge_summary_to_plan_section(plan["unitsim"], content, "unitsim")
            mental_model_observations.append(
                await _observe_mental_model_event_safely(
                    observe_mental_model_event,
                    db,
                    model_revision=model_rev,
                    user=current_user,
                    event_type="unitsim.plan.generated",
                    payload={
                        "verification_type": vtype,
                        "agent": "unitsim",
                        "plan": plan,
                    },
                    ai_client=ai_client,
                    apply_approved_intent=False,
                    commit=False,
                )
            )
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail="Verification service unavailable") from exc

    if vtype in ("formal", "all"):
        try:
            ai_client = None
            llm_metadata: Dict[str, Any] = {}
            try:
                ai_client, llm_metadata = await _require_plan_ai_client()
            except Exception as exc:
                logger.warning("Formal plan will use deterministic fallback without LLM: %s", exc)
                llm_metadata = {"status": "unavailable", "error": str(exc)}
            formal_plan = await generate_formal_plan_with_ai(
                model_ns,
                content=content,
                ai_client=ai_client,
                require_llm=False,
            )
            # FormalOutput is a dataclass — use getattr
            formal_plan["llm_provider"] = llm_metadata.get("provider")
            formal_plan["llm_model"] = llm_metadata.get("model")
            _attach_knowledge_summary_to_plan_section(formal_plan, content, "formal")
            plan["formal"] = formal_plan
            mental_model_observations.append(
                await _observe_mental_model_event_safely(
                    observe_mental_model_event,
                    db,
                    model_revision=model_rev,
                    user=current_user,
                    event_type="formal.plan.generated",
                    payload={
                        "verification_type": vtype,
                        "agent": "formal",
                        "plan": plan,
                    },
                    ai_client=ai_client,
                    apply_approved_intent=False,
                    commit=False,
                )
            )
        except Exception as e:
            logger.warning("Formal plan gen failed: %s", e)
            plan["formal"] = {"error": str(e), "properties": [], "property_count": 0}

    if vtype in ("uvm", "all"):
        try:
            fallback_uvm_plan = _generate_uvm_plan(content, model_ns)
            ai_client, llm_metadata = await _require_plan_ai_client()
            plan["uvm"] = await generate_uvm_plan_with_ai(
                model_ns,
                content=content,
                ai_client=ai_client,
                fallback_plan=fallback_uvm_plan,
                require_llm=True,
            )
            _merge_knowledge_hints_into_uvm_plan(plan["uvm"], content)
            plan["uvm"]["llm_provider"] = llm_metadata.get("provider")
            plan["uvm"]["llm_model"] = llm_metadata.get("model")
            mental_model_observations.append(
                await _observe_mental_model_event_safely(
                    observe_mental_model_event,
                    db,
                    model_revision=model_rev,
                    user=current_user,
                    event_type="uvm.plan.generated",
                    payload={
                        "verification_type": vtype,
                        "agent": "uvm",
                        "plan": plan,
                    },
                    # The UVM plan itself already used the LLM above. Do not run a
                    # second blocking living-model LLM review before returning the
                    # plan to the UI; record the event/evidence only.
                    ai_client=None,
                    apply_approved_intent=False,
                    commit=False,
                )
            )
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail="Verification service unavailable") from exc

    if mental_model_observations:
        plan["mental_model_observations"] = mental_model_observations
    plan["mental_model_readiness"] = mental_model_readiness
    plan["presentation"] = _build_plan_presentation(
        plan=plan,
        content=content,
        readiness=mental_model_readiness,
    )
    if mental_model_observations:
        db.commit()
        db.refresh(model_rev)

    from services.verification.plan_presentation import (
        build_verification_plan_presentation,
        merge_presentation_into_plan,
    )

    spec_artifact, rtl_artifact = resolve_active_spec_rtl_artifacts(
        db, project, auto_activate=True
    )
    presentation = build_verification_plan_presentation(
        plan,
        verification_type=vtype,
        model_content=content,
        spec_filename=getattr(spec_artifact, "filename", None),
        rtl_filename=getattr(rtl_artifact, "filename", None),
        readiness=mental_model_readiness,
    )
    plan = merge_presentation_into_plan(plan, presentation)
    plan_artifact_error = None
    plan_artifact = None
    try:
        plan_artifact = _create_plan_markdown_artifact(
            db,
            project,
            current_user,
            verification_type=vtype,
            plan=plan,
            model_content=content,
            mental_model_revision_id=model_rev.id,
        )
        _attach_plan_markdown_reference(plan, plan_artifact)
    except Exception as exc:
        logger.exception("Failed to save verification plan markdown artifact: %s", exc)
        plan_artifact_error = str(exc)

    return {
        "status": "plan_ready",
        "plan": plan,
        "plan_artifact": plan_artifact,
        "artifacts": [plan_artifact] if plan_artifact else [],
        "plan_artifact_error": plan_artifact_error,
        "mental_model_observations": mental_model_observations,
        "mental_model_readiness": mental_model_readiness,
    }


def _dedupe_plan_items(items: List[Dict[str, Any]], key: str) -> List[Dict[str, Any]]:
    seen: set[str] = set()
    result: List[Dict[str, Any]] = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        raw_name = str(item.get(key) or item.get("name") or item.get("description") or "")
        normalized = raw_name.strip().lower()
        if normalized and normalized in seen:
            continue
        if normalized:
            seen.add(normalized)
        result.append(item)
    return result


def _attach_knowledge_summary_to_plan_section(
    plan_section: Dict[str, Any],
    content: Dict[str, Any],
    strategy: str,
) -> None:
    """Attach compact KB provenance to a plan section without changing behavior."""
    if not isinstance(plan_section, dict):
        return
    knowledge_base = _knowledge_base_context_for_content(content)
    hints = knowledge_base.get("plan_hints", {}) if isinstance(knowledge_base, dict) else {}
    strategy_hints = list(hints.get(strategy, []) or []) if isinstance(hints, dict) else []
    plan_section["knowledge_base"] = {
        "engine": knowledge_base.get("engine", "") if isinstance(knowledge_base, dict) else "",
        "categories": knowledge_base.get("categories", []) if isinstance(knowledge_base, dict) else [],
        "traits": knowledge_base.get("traits", []) if isinstance(knowledge_base, dict) else [],
        "hints_used": [item.get("id") for item in strategy_hints if isinstance(item, dict) and item.get("id")],
        "available_hints": {
            "unitsim": len(hints.get("unitsim", []) or []) if isinstance(hints, dict) else 0,
            "formal": len(hints.get("formal", []) or []) if isinstance(hints, dict) else 0,
            "uvm": len(hints.get("uvm", []) or []) if isinstance(hints, dict) else 0,
            "coverage": len(hints.get("coverage", []) or []) if isinstance(hints, dict) else 0,
        },
    }


def _merge_knowledge_hints_into_uvm_plan(
    uvm_plan: Dict[str, Any],
    content: Dict[str, Any],
) -> None:
    """Preserve UVM plan shape while filling any free capacity with KB hints."""
    if not isinstance(uvm_plan, dict):
        return
    _attach_knowledge_summary_to_plan_section(uvm_plan, content, "uvm")
    knowledge_base = _knowledge_base_context_for_content(content)
    hints = knowledge_base.get("plan_hints", {}) if isinstance(knowledge_base, dict) else {}
    if not isinstance(hints, dict):
        return

    policy = uvm_plan.get("generation_policy", {})
    limits = policy.get("content_limits", {}) if isinstance(policy, dict) else {}

    def _limit(key: str, default: int) -> int:
        try:
            return max(1, int(limits.get(key, default)))
        except (AttributeError, TypeError, ValueError):
            return default

    def _slug(value: Any, prefix: str) -> str:
        text = re.sub(r"[^a-zA-Z0-9_]+", "_", str(value or "").strip().lower()).strip("_")
        return f"{prefix}_{text or 'knowledge_rule'}"

    sequences = list(uvm_plan.get("sequences", []) or [])
    scoreboard = list(uvm_plan.get("scoreboard_checks", []) or [])
    coverage = list(uvm_plan.get("coverage_points", []) or [])

    for hint in list(hints.get("uvm", []) or []):
        if not isinstance(hint, dict) or len(sequences) >= _limit("max_sequence_classes", 6):
            continue
        sequences.append({
            "name": _slug(hint.get("id") or hint.get("title"), "seq_kb"),
            "description": hint.get("stimulus_hint") or hint.get("description") or hint.get("title") or "",
            "source": f"knowledge_base.{hint.get('source') or 'rule'}",
            "rule_id": hint.get("id", ""),
            "severity": hint.get("severity", ""),
            "signals": list(hint.get("signals") or [])[:8],
            "requirement_ids": [],
        })

    for hint in list(hints.get("uvm", []) or []) + list(hints.get("formal", []) or []):
        if not isinstance(hint, dict) or len(scoreboard) >= _limit("max_scoreboard_checks", 10):
            continue
        scoreboard.append({
            "check": _slug(hint.get("id") or hint.get("title"), "check_kb"),
            "description": hint.get("check_hint") or hint.get("description") or hint.get("title") or "",
            "source": f"knowledge_base.{hint.get('source') or 'rule'}",
            "rule_id": hint.get("id", ""),
            "severity": hint.get("severity", ""),
            "signals": list(hint.get("signals") or [])[:8],
            "requirement_ids": [],
        })

    for hint in list(hints.get("coverage", []) or []):
        if not isinstance(hint, dict) or len(coverage) >= _limit("max_coverage_bins", 8):
            continue
        coverage.append({
            "point": _slug(hint.get("id") or hint.get("title"), "cp_kb"),
            "type": "functional",
            "description": hint.get("description") or hint.get("title") or "",
            "source": f"knowledge_base.{hint.get('source') or 'rule'}",
            "rule_id": hint.get("id", ""),
            "severity": hint.get("severity", ""),
            "signal": ", ".join(str(sig) for sig in list(hint.get("signals") or [])[:4]),
        })

    uvm_plan["sequences"] = _dedupe_plan_items(sequences, "name")
    uvm_plan["scoreboard_checks"] = _dedupe_plan_items(scoreboard, "check")
    uvm_plan["coverage_points"] = _dedupe_plan_items(coverage, "point")
    traceability = uvm_plan.setdefault("traceability", {})
    if isinstance(traceability, dict):
        rule_ids = [
            item.get("rule_id")
            for group in ("sequences", "scoreboard_checks", "coverage_points")
            for item in (uvm_plan.get(group, []) or [])
            if isinstance(item, dict) and item.get("rule_id")
        ]
        traceability["knowledge_rules_used"] = list(dict.fromkeys(rule_ids))


def _generate_uvm_plan(content: Dict, model_ns) -> Dict:
    """Generate a UVM verification plan from mental model (no file generation)."""
    from services.verification.uvm_planner import (
        compute_design_profile,
        plan_uvm_generation,
        planned_uvm_files,
    )

    design = content.get("design", content)
    if not isinstance(design, dict):
        design = {}
    profile = compute_design_profile(content)
    generation_policy = plan_uvm_generation(profile, content)
    limits = generation_policy.content_limits
    max_sequences = max(1, int(getattr(limits, "max_sequence_classes", 6) or 6))
    max_checks = max(1, int(getattr(limits, "max_scoreboard_checks", 10) or 10))
    max_coverage = max(1, int(getattr(limits, "max_coverage_bins", 8) or 8))
    max_requirements = max(1, int(getattr(limits, "max_requirements", 8) or 8))
    ports = design.get("ports", [])
    requirements = content.get("requirements", [])
    protocols = design.get("protocols", [])
    expected_behaviors = design.get("expected_behaviors", [])
    transaction_flows = design.get("transaction_flows", [])
    register_fields = design.get("register_fields", []) or design.get("register_map", [])
    verification = content.get("verification", {})
    verification_coverage = (
        verification.get("coverage_points", [])
        if isinstance(verification, dict)
        else []
    )
    model_questions = content.get("open_questions", [])
    knowledge_base = _knowledge_base_context_for_content(content)
    kb_hints = (
        knowledge_base.get("plan_hints", {})
        if isinstance(knowledge_base, dict)
        else {}
    )
    kb_uvm_hints = list(kb_hints.get("uvm", []) or []) if isinstance(kb_hints, dict) else []
    kb_coverage_hints = list(kb_hints.get("coverage", []) or []) if isinstance(kb_hints, dict) else []
    kb_formal_hints = list(kb_hints.get("formal", []) or []) if isinstance(kb_hints, dict) else []
    kb_rules_used: List[str] = []

    def _slug(value: Any, prefix: str) -> str:
        text = re.sub(r"[^a-zA-Z0-9_]+", "_", str(value or "").strip().lower()).strip("_")
        return f"{prefix}_{text or 'knowledge_rule'}"

    def _record_kb_rule(item: Dict[str, Any]) -> None:
        rule_id = str(item.get("id") or "").strip()
        if rule_id and rule_id not in kb_rules_used:
            kb_rules_used.append(rule_id)

    def _req_id(req: Dict[str, Any]) -> str:
        return str(req.get("id") or "").strip()

    def _related_requirement_ids(keywords: List[Any], limit: int = 8) -> List[str]:
        tokens: List[str] = []
        for keyword in keywords:
            text = str(keyword or "").strip().lower()
            if len(text) < 3:
                continue
            tokens.append(text)
            tokens.append(text.replace("_", " "))
            tokens.append(text.replace("-", " "))
        tokens = list(dict.fromkeys(tokens))
        linked: List[str] = []
        for raw_req in requirements:
            if not isinstance(raw_req, dict):
                continue
            req_id = _req_id(raw_req)
            if not req_id:
                continue
            haystack = " ".join(
                str(raw_req.get(key) or "")
                for key in ("id", "text", "category", "priority")
            ).lower()
            if any(token and token in haystack for token in tokens):
                linked.append(req_id)
            if len(linked) >= limit:
                break
        return linked

    def _protocol_requirement_ids(raw_protocol: Dict[str, Any]) -> List[str]:
        protocol_name = str(
            raw_protocol.get("protocol") or raw_protocol.get("name") or ""
        )
        port_group = raw_protocol.get("port_group") or raw_protocol.get("ports") or []
        return _related_requirement_ids(
            [protocol_name, "protocol", "transaction", "valid", "ready", *list(port_group or [])]
        )

    def _is_tlul_protocol(raw_protocol: Dict[str, Any]) -> bool:
        protocol_name = str(
            raw_protocol.get("protocol") or raw_protocol.get("name") or ""
        ).lower()
        compact_name = protocol_name.replace("_", "").replace("-", "").replace(" ", "")
        port_group = [
            str(port).lower().replace("_", "")
            for port in (raw_protocol.get("port_group") or raw_protocol.get("ports") or [])
        ]
        return (
            "tilelink" in protocol_name
            or "tlul" in compact_name
            or {"avalid", "aready", "dvalid", "dready"} <= set(port_group)
        )

    def _protocol_ports(raw_protocol: Dict[str, Any]) -> List[str]:
        return [
            str(port)
            for port in (raw_protocol.get("port_group") or raw_protocol.get("ports") or [])
            if str(port)
        ]

    def _tlul_signal_subset(raw_protocol: Dict[str, Any]) -> List[str]:
        wanted = {
            "avalid", "aready", "aopcode", "aparam", "asize", "asource",
            "aaddress", "amask", "adata", "dvalid", "dready", "dopcode",
            "dparam", "dsize", "dsource", "dsink", "ddata", "derror",
        }
        result: List[str] = []
        for port in _protocol_ports(raw_protocol):
            if port.lower().replace("_", "") in wanted:
                result.append(port)
        return result

    def _port_width(raw_port: Dict[str, Any]) -> int:
        try:
            return int(raw_port.get("width") or 1)
        except (TypeError, ValueError):
            return 1

    # Group ports by role
    clocks = [p for p in ports if isinstance(p, dict) and ("clk" in p.get("name", "").lower() or "clock" in p.get("name", "").lower())]
    resets = [p for p in ports if isinstance(p, dict) and ("rst" in p.get("name", "").lower() or "reset" in p.get("name", "").lower())]
    inputs = [p for p in ports if isinstance(p, dict) and p.get("direction") == "input" and p not in clocks and p not in resets]
    outputs = [p for p in ports if isinstance(p, dict) and p.get("direction") == "output"]

    # Determine agents needed. Small designs intentionally stay as one DUT
    # agent; protocol-specific splitting is reserved for real multi-interface
    # designs selected by the generation policy.
    agents = []
    if generation_policy.multi_agent and isinstance(protocols, list):
        for raw_protocol in protocols:
            if not isinstance(raw_protocol, dict):
                continue
            protocol_name = str(
                raw_protocol.get("protocol") or raw_protocol.get("name") or "interface"
            ).strip()
            port_group = [
                str(port)
                for port in (raw_protocol.get("port_group") or raw_protocol.get("ports") or [])
            ]
            if protocol_name and port_group:
                agents.append({
                    "name": f"{protocol_name.lower().replace('-', '_')}_agent",
                    "type": "active" if raw_protocol.get("role") != "monitor" else "passive",
                    "ports": port_group,
                    "description": raw_protocol.get("description") or f"Agent for {protocol_name} interface",
                })
    if not agents:
        agents.append({
            "name": "dut_agent",
            "type": "active" if inputs else "passive",
            "ports": [
                p.get("name", "")
                for p in inputs + outputs
                if isinstance(p, dict) and p.get("name")
            ],
            "description": "Single DUT-level UVM agent selected by the UVM generation policy",
        })

    sequences = []
    if clocks or resets:
        sequences.append({
            "name": "reset_sequence",
            "description": "Apply reset and verify the post-reset observable state from the mental model",
            "requirement_ids": _related_requirement_ids(["reset", "rst", "initial"]),
        })
    if isinstance(protocols, list):
        for raw_protocol in protocols:
            if not isinstance(raw_protocol, dict):
                continue
            protocol_name = str(
                raw_protocol.get("protocol") or raw_protocol.get("name") or "interface"
            ).strip()
            if not protocol_name:
                continue
            if _is_tlul_protocol(raw_protocol):
                sequences.append({
                    "name": "seq_tilelink_ul_nominal",
                    "description": (
                        "Exercise legal TileLink-UL A-channel requests and D-channel "
                        "responses with a_valid/a_ready and d_valid/d_ready backpressure."
                    ),
                    "signals": _tlul_signal_subset(raw_protocol),
                    "requirement_ids": _protocol_requirement_ids(raw_protocol),
                    "source": "mental_model.protocol.tilelink_ul",
                })
                sequences.append({
                    "name": "seq_tilelink_ul_backpressure",
                    "description": (
                        "Hold a_ready or d_ready low across multiple cycles and verify "
                        "TL-UL payload stability while each channel is stalled."
                    ),
                    "signals": _tlul_signal_subset(raw_protocol),
                    "requirement_ids": _protocol_requirement_ids(raw_protocol),
                    "source": "mental_model.protocol.tilelink_ul",
                })
                continue
            sequences.append({
                "name": f"seq_{protocol_name.lower().replace('-', '_')}_nominal",
                "description": raw_protocol.get("description") or f"Exercise nominal {protocol_name} transactions",
                "requirement_ids": _protocol_requirement_ids(raw_protocol),
            })
    if isinstance(transaction_flows, list):
        for index, raw_flow in enumerate(transaction_flows[:max_sequences]):
            if not isinstance(raw_flow, dict):
                continue
            flow_name = str(raw_flow.get("name") or f"flow_{index + 1}").strip()
            if not flow_name:
                continue
            sequences.append({
                "name": f"seq_{flow_name.lower().replace('-', '_')}",
                "description": raw_flow.get("description") or f"Exercise transaction flow {flow_name}",
                "requirement_ids": raw_flow.get("requirement_ids") or _related_requirement_ids(
                    [flow_name, raw_flow.get("protocol"), raw_flow.get("description")]
                ),
                "source": "mental_model.transaction_flow",
            })
    if isinstance(expected_behaviors, list):
        for index, behavior in enumerate(expected_behaviors[:max_sequences]):
            if not isinstance(behavior, dict):
                continue
            behavior_id = str(behavior.get("id") or f"EB-{index + 1:03d}")
            sequences.append({
                "name": f"seq_{behavior_id.lower().replace('-', '_')}",
                "description": behavior.get("description") or f"Drive stimulus for expected behavior {behavior_id}",
                "requirement_ids": behavior.get("requirement_ids") or [],
                "source": "mental_model.expected_behavior",
            })
    for hint in kb_uvm_hints[:max_sequences]:
        if not isinstance(hint, dict):
            continue
        _record_kb_rule(hint)
        title = str(hint.get("title") or hint.get("id") or "knowledge-base scenario")
        stimulus = str(hint.get("stimulus_hint") or "").strip()
        sequences.append({
            "name": _slug(hint.get("id") or title, "seq_kb"),
            "description": stimulus or str(hint.get("description") or title),
            "requirement_ids": [],
            "source": f"knowledge_base.{hint.get('source') or 'rule'}",
            "rule_id": hint.get("id", ""),
            "severity": hint.get("severity", ""),
            "signals": list(hint.get("signals") or [])[:8],
        })
    for req in requirements[:max_requirements]:
        if not isinstance(req, dict):
            continue
        req_text = req.get("text", str(req))
        sequences.append({
            "name": f"seq_{req.get('id', 'unknown')}".lower().replace("-", "_"),
            "description": f"Sequence for: {req_text[:80]}",
            "requirement_ids": [_req_id(req)] if _req_id(req) else [],
        })
    if not sequences:
        sequences.append({
            "name": "mental_model_smoke_sequence",
            "description": "Exercise legal input/output behavior derived from the approved mental model",
        })

    scoreboard = []
    if isinstance(protocols, list):
        for raw_protocol in protocols:
            if not isinstance(raw_protocol, dict):
                continue
            protocol_name = str(
                raw_protocol.get("protocol") or raw_protocol.get("name") or "interface"
            ).strip()
            if protocol_name:
                if _is_tlul_protocol(raw_protocol):
                    scoreboard.append({
                        "check": "check_tilelink_ul_request_response",
                        "description": (
                            "Check TL-UL A-channel and D-channel handshakes, stalled "
                            "payload stability, and request-to-response ordering intent."
                        ),
                        "signals": _tlul_signal_subset(raw_protocol),
                        "requirement_ids": _protocol_requirement_ids(raw_protocol),
                        "source": "mental_model.protocol.tilelink_ul",
                    })
                    continue
                scoreboard.append({
                    "check": f"check_{protocol_name.lower().replace('-', '_')}_protocol",
                    "description": raw_protocol.get("description") or f"Check {protocol_name} protocol behavior",
                    "requirement_ids": _protocol_requirement_ids(raw_protocol),
                })
    if isinstance(expected_behaviors, list):
        for index, behavior in enumerate(expected_behaviors[:max_checks]):
            if not isinstance(behavior, dict):
                continue
            expected_output = behavior.get("expected_output") or {}
            stimulus = behavior.get("stimulus") or {}
            if not isinstance(expected_output, dict) or not expected_output:
                continue
            behavior_id = str(behavior.get("id") or f"EB-{index + 1:03d}")
            signals = [str(signal) for signal in expected_output.keys()]
            scoreboard.append({
                "check": f"check_{behavior_id.lower().replace('-', '_')}",
                "description": behavior.get("description") or f"Compare outputs for expected behavior {behavior_id}",
                "signals": signals,
                "stimulus": stimulus if isinstance(stimulus, dict) else {},
                "expected_output": expected_output,
                "requirement_ids": behavior.get("requirement_ids") or [],
                "source": "mental_model.expected_behavior",
            })
    if isinstance(register_fields, list):
        for index, reg in enumerate(register_fields[:max_checks]):
            if not isinstance(reg, dict):
                continue
            reg_name = str(reg.get("name") or f"register_{index + 1}")
            scoreboard.append({
                "check": f"check_{reg_name.lower()}_model",
                "description": f"Keep a reference model for register {reg_name}",
                "signals": [
                    str(reg.get("write_enable") or ""),
                    str(reg.get("data_signal") or ""),
                ],
                "source": "mental_model.register_field",
            })
    for hint in (kb_uvm_hints + kb_formal_hints)[:max_checks]:
        if not isinstance(hint, dict):
            continue
        _record_kb_rule(hint)
        title = str(hint.get("title") or hint.get("id") or "knowledge-base check")
        check_hint = str(hint.get("check_hint") or "").strip()
        signals = list(hint.get("signals") or [])[:8]
        scoreboard.append({
            "check": _slug(hint.get("id") or title, "check_kb"),
            "description": check_hint or str(hint.get("description") or title),
            "signals": signals,
            "requirement_ids": [],
            "source": f"knowledge_base.{hint.get('source') or 'rule'}",
            "rule_id": hint.get("id", ""),
            "severity": hint.get("severity", ""),
        })
    for req in requirements[:max_requirements]:
        if not isinstance(req, dict):
            continue
        req_id = str(req.get("id") or f"req_{len(scoreboard) + 1}")
        scoreboard.append({
            "check": f"check_{req_id.lower().replace('-', '_')}",
            "description": f"Check requirement: {str(req.get('text') or '')[:100]}",
            "requirement_ids": [req_id] if req_id else [],
        })
    if not scoreboard and outputs:
        scoreboard.append({
            "check": "observable_output_consistency",
            "description": "Compare monitored outputs against the reference behavior from the mental model",
        })

    coverage = []
    for raw_cp in verification_coverage[:max_coverage]:
        if not isinstance(raw_cp, dict):
            continue
        coverage.append({
            "point": raw_cp.get("name") or raw_cp.get("point") or raw_cp.get("signal") or f"cp_{len(coverage) + 1}",
            "type": raw_cp.get("cover_type") or raw_cp.get("type") or "functional",
            "description": raw_cp.get("bins_description") or raw_cp.get("description") or "",
        })
    if isinstance(protocols, list):
        for raw_protocol in protocols:
            if not isinstance(raw_protocol, dict):
                continue
            protocol_name = str(
                raw_protocol.get("protocol") or raw_protocol.get("name") or "interface"
            ).strip()
            if protocol_name:
                if _is_tlul_protocol(raw_protocol):
                    coverage.append({
                        "point": "cp_tilelink_ul_a_d_handshakes",
                        "type": "cross",
                        "description": (
                            "Cover accepted TL-UL A-channel requests, D-channel responses, "
                            "and stalled ready/valid backpressure cases."
                        ),
                        "signal": ", ".join(_tlul_signal_subset(raw_protocol)[:4]),
                        "requirement_ids": _protocol_requirement_ids(raw_protocol),
                        "source": "mental_model.protocol.tilelink_ul",
                    })
                    continue
                coverage.append({
                    "point": f"cp_{protocol_name.lower().replace('-', '_')}_transactions",
                    "type": "functional",
                    "description": f"Cover representative {protocol_name} transactions",
                    "requirement_ids": _protocol_requirement_ids(raw_protocol),
                })
    if isinstance(expected_behaviors, list):
        for index, behavior in enumerate(expected_behaviors[:max_coverage]):
            if not isinstance(behavior, dict):
                continue
            expected_output = behavior.get("expected_output") or {}
            if not isinstance(expected_output, dict):
                continue
            for signal in list(expected_output.keys())[:2]:
                coverage.append({
                    "point": f"cp_expected_{str(signal).lower()}_{index + 1}",
                    "type": "functional",
                    "description": f"Cover expected behavior on {signal}",
                    "signal": str(signal),
                    "source": "mental_model.expected_behavior",
                })
    if isinstance(register_fields, list):
        for index, reg in enumerate(register_fields[:max_coverage]):
            if not isinstance(reg, dict):
                continue
            reg_name = str(reg.get("name") or f"register_{index + 1}")
            coverage.append({
                "point": f"cp_{reg_name.lower()}_access",
                "type": "functional",
                "description": f"Cover accesses to register {reg_name}",
                "signal": str(reg.get("write_enable") or reg.get("data_signal") or ""),
                "source": "mental_model.register_field",
            })
    for hint in kb_coverage_hints[:max_coverage]:
        if not isinstance(hint, dict):
            continue
        _record_kb_rule(hint)
        title = str(hint.get("title") or hint.get("id") or "knowledge-base coverage")
        coverage.append({
            "point": _slug(hint.get("id") or title, "cp_kb"),
            "type": "functional",
            "description": str(hint.get("description") or title),
            "signal": ", ".join(str(sig) for sig in list(hint.get("signals") or [])[:4]),
            "source": f"knowledge_base.{hint.get('source') or 'rule'}",
            "rule_id": hint.get("id", ""),
            "severity": hint.get("severity", ""),
        })
    wide_ports = [
        p for p in ports
        if isinstance(p, dict) and _port_width(p) > 1
    ]
    if wide_ports:
        coverage.append({
            "point": "wide_port_boundary_values",
            "type": "functional",
            "description": "Cover min/max values on multi-bit interface signals",
            "requirement_ids": _related_requirement_ids(
                ["boundary", "min", "max", "width", "range", *[p.get("name", "") for p in wide_ports[:max_coverage]]]
            ),
        })
    if not coverage and ports:
        coverage.append({
            "point": "interface_signal_activity",
            "type": "toggle",
            "description": "Cover activity on parsed interface signals",
        })

    top_module = design.get("top_module", "dut")
    files_preview = planned_uvm_files(top_module, generation_policy)
    sequences = _dedupe_plan_items(sequences, "name")[:max_sequences]
    scoreboard = _dedupe_plan_items(scoreboard, "check")[:max_checks]
    coverage = _dedupe_plan_items(coverage, "point")[:max_coverage]

    linked_sequence_count = sum(1 for item in sequences if item.get("requirement_ids"))
    linked_scoreboard_count = sum(1 for item in scoreboard if item.get("requirement_ids"))
    linked_coverage_count = sum(1 for item in coverage if item.get("requirement_ids"))

    return {
        "top_module": top_module,
        "interfaces": [p.get("name", "") for p in ports if isinstance(p, dict)],
        "agents": agents,
        "sequences": sequences,
        "scoreboard_checks": scoreboard,
        "coverage_points": coverage,
        "assumptions": [
            f"{len(clocks)} clock domain(s) parsed from RTL" if clocks else "No clock domain was parsed from RTL",
            f"{len(requirements)} requirement(s) mapped from the mental model",
            (
                f"Knowledge base matched {len(kb_rules_used)} rule hint(s) for planning"
                if kb_rules_used
                else "Knowledge base found no additional staged-plan hints"
            ),
        ],
        "open_questions": [
            q.get("question", str(q)) if isinstance(q, dict) else str(q)
            for q in model_questions
        ],
        "files_preview": files_preview,
        "estimated_file_count": len(files_preview),
        "generation_policy": generation_policy.to_dict(),
        "traceability": {
            "requirements_total": len([r for r in requirements if isinstance(r, dict)]),
            "linked_sequences": linked_sequence_count,
            "linked_scoreboard_checks": linked_scoreboard_count,
            "linked_coverage_points": linked_coverage_count,
            "knowledge_rules_used": list(kb_rules_used),
        },
        "knowledge_base": {
            "engine": knowledge_base.get("engine", "") if isinstance(knowledge_base, dict) else "",
            "categories": knowledge_base.get("categories", []) if isinstance(knowledge_base, dict) else [],
            "traits": knowledge_base.get("traits", []) if isinstance(knowledge_base, dict) else [],
            "signal_rules": [item.get("id") for item in knowledge_base.get("signal_rules", []) if isinstance(item, dict)] if isinstance(knowledge_base, dict) else [],
            "protocol_rules": [item.get("id") for item in knowledge_base.get("protocol_rules", []) if isinstance(item, dict)] if isinstance(knowledge_base, dict) else [],
            "corner_cases": [item.get("id") for item in knowledge_base.get("corner_cases", []) if isinstance(item, dict)] if isinstance(knowledge_base, dict) else [],
            "formal_properties": [item.get("id") for item in knowledge_base.get("formal_properties", []) if isinstance(item, dict)] if isinstance(knowledge_base, dict) else [],
            "vplan_testpoints": [item.get("id") for item in knowledge_base.get("vplan_testpoints", []) if isinstance(item, dict)] if isinstance(knowledge_base, dict) else [],
        },
    }


@router.post("/{project_id}/verification/plan/refine")
async def refine_plan(
    project_id: str,
    request: RefineRequest,
    current_user: User = Depends(_get_current_user),
    db: Session = Depends(get_db),
):
    """
    Stage 3: Refine a plan with natural language feedback.
    Reuses _parse_natural_language_feedback from plan_tuning_tools.
    """
    from agent_tools.plan_tuning_tools import (
        _parse_natural_language_feedback,
        handle_tune_test_plan,
    )

    from services.mental_model.living_agent import observe_mental_model_event
    from services.verification.plan_presentation import (
        build_verification_plan_presentation,
        merge_presentation_into_plan,
        refine_presentation_summary,
    )

    project = _require_project_access(db, current_user, project_id)
    plan = request.plan_json
    vtype = _validate_verification_type(request.verification_type)
    feedback = request.feedback
    feedback_lower = (feedback or "").lower()
    model_rev = None
    mental_model_revision_id = str(
        request.mental_model_revision_id
        or plan.get("mental_model_revision_id")
        or ""
    )
    if mental_model_revision_id:
        model_rev = _require_model_revision(
            db,
            project=project,
            mental_model_revision_id=mental_model_revision_id,
        )
    model_content = _load_model_content(model_rev) if model_rev is not None else {}

    def _refresh_plan_presentation(refined_plan: Dict[str, Any]) -> Dict[str, Any]:
        try:
            spec_artifact, rtl_artifact = resolve_active_spec_rtl_artifacts(
                db, project, auto_activate=True
            )
            presentation = build_verification_plan_presentation(
                refined_plan,
                verification_type=vtype,
                model_content=model_content,
                spec_filename=getattr(spec_artifact, "filename", None),
                rtl_filename=getattr(rtl_artifact, "filename", None),
                readiness=refined_plan.get("mental_model_readiness")
                if isinstance(refined_plan.get("mental_model_readiness"), dict)
                else None,
                refined_feedback=feedback,
            )
            return merge_presentation_into_plan(refined_plan, presentation)
        except Exception as exc:
            logger.warning("Could not rebuild refined plan presentation: %s", exc)
            return refine_presentation_summary(
                refined_plan,
                feedback,
                verification_type=vtype,
                model_content=model_content if model_content else None,
            )

    def _with_plan_artifact(response: Dict[str, Any], refined_plan: Dict[str, Any]) -> Dict[str, Any]:
        revision_id = str(
            (getattr(model_rev, "id", None) if model_rev is not None else "")
            or mental_model_revision_id
            or refined_plan.get("mental_model_revision_id")
            or ""
        )
        if not revision_id:
            return response
        try:
            artifact = _create_plan_markdown_artifact(
                db,
                project,
                current_user,
                verification_type=vtype,
                plan=refined_plan,
                model_content=model_content,
                mental_model_revision_id=revision_id,
                refined_feedback=feedback,
            )
            _attach_plan_markdown_reference(refined_plan, artifact)
            response["plan_artifact"] = artifact
            response["artifacts"] = [artifact]
        except Exception as exc:
            logger.exception("Failed to save refined verification plan markdown artifact: %s", exc)
            response["plan_artifact_error"] = str(exc)
        return response

    unitsim_terms = (
        "unitsim", "unit sim", "unit simulation", "directed",
        "testbench", "simulation", "scenario", "scenarios",
    )
    uvm_terms = (
        "uvm", "agent", "scoreboard", "coverage", "cover",
        "coverpoint", "covergroup",
    )
    mentions_unitsim = any(term in feedback_lower for term in unitsim_terms)
    mentions_uvm = any(term in feedback_lower for term in uvm_terms)
    sequence_only_for_uvm = "sequence" in feedback_lower and not mentions_unitsim

    should_refine_uvm = (
        "uvm" in plan
        and (
            vtype == "uvm"
            or (
                vtype == "all"
                and (mentions_uvm or sequence_only_for_uvm)
                and not (mentions_unitsim and not mentions_uvm)
            )
        )
    )
    if should_refine_uvm:
        refined = _refine_uvm_plan(plan, feedback, model_content)
        mental_model_observation = None
        if model_rev is not None:
            ai_client = None
            try:
                from services.mental_model.store import get_default_ai_client_adapter

                ai_client, _metadata = await get_default_ai_client_adapter(
                    require_llm=False,
                )
            except Exception as exc:
                logger.warning("Could not attach AI client for UVM plan observation: %s", exc)
            mental_model_observation = await observe_mental_model_event(
                db,
                model_revision=model_rev,
                user=current_user,
                event_type="uvm.plan.refined",
                payload={
                    "verification_type": vtype,
                    "agent": "uvm",
                    "feedback": feedback,
                    "plan": refined["plan"],
                },
                ai_client=ai_client,
                apply_approved_intent=False,
            )
            refined["plan"].setdefault("mental_model_observations", []).append(
                mental_model_observation
            )
        refined_plan = _refresh_plan_presentation(refined["plan"])
        return _with_plan_artifact({
            "status": "refined",
            "actions_applied": refined["actions"],
            "plan": refined_plan,
            "mental_model_observation": mental_model_observation,
        }, refined_plan)

    # Apply to unitsim scenarios if present
    if vtype in ("unitsim", "all") and "unitsim" in plan and plan["unitsim"].get("scenarios"):
        ai_client = None
        llm_metadata: Dict[str, Any] = {}
        try:
            from services.mental_model.store import get_default_ai_client_adapter

            ai_client, llm_metadata = await get_default_ai_client_adapter(
                require_llm=False,
            )
        except Exception as exc:
            logger.warning("Could not attach AI client for plan refinement: %s", exc)

        if ai_client:
            tuned_raw = await handle_tune_test_plan(
                project_id=project_id,
                plan_json=json.dumps(plan["unitsim"]),
                feedback=feedback,
                action="tune",
                ai_client=ai_client,
            )
            try:
                tuned = json.loads(tuned_raw)
            except json.JSONDecodeError:
                tuned = {}
            if isinstance(tuned.get("plan"), dict):
                plan["unitsim"] = tuned["plan"]
                refined_plan = _refresh_plan_presentation(plan)
                return _with_plan_artifact({
                    "status": "refined",
                    "engine": tuned.get("engine", "llm"),
                    "llm_provider": llm_metadata.get("provider"),
                    "llm_model": llm_metadata.get("model"),
                    "actions_applied": tuned.get("changes_applied", []),
                    "plan": refined_plan,
                }, refined_plan)

        scenarios = plan["unitsim"]["scenarios"]
        module_name = plan.get("module_name", "dut")
        changes = _parse_natural_language_feedback(feedback, scenarios, module_name)
        for change in changes:
            if change["action"] == "add":
                scenarios.append(change["scenario"])
            elif change["action"] == "remove":
                scenarios = [s for s in scenarios if s.get("name") != change["target"]]
            elif change["action"] == "modify":
                for s in scenarios:
                    if s.get("name") == change["target"]:
                        s.update(change.get("updates", {}))

        plan["unitsim"]["scenarios"] = scenarios
        plan["unitsim"]["total_scenarios"] = len(scenarios)

        refined_plan = _refresh_plan_presentation(plan)
        return _with_plan_artifact({
            "status": "refined",
            "actions_applied": [
                {"action": c["action"], "description": c.get("description", "")}
                for c in changes
            ],
            "plan": refined_plan,
        }, refined_plan)

    # Fallback: return plan unchanged with a note
    refined_plan = _refresh_plan_presentation(plan)
    return _with_plan_artifact({
        "status": "refined",
        "actions_applied": [],
        "note": "No applicable scenarios found for refinement",
        "plan": refined_plan,
    }, refined_plan)


@router.post("/{project_id}/verification/execute")
async def execute_verification(
    project_id: str,
    request: ExecuteRequest,
    current_user: User = Depends(_get_current_user),
    db: Session = Depends(get_db),
):
    """
    Stage 4: Execute verification after user approval.
    Only runs the selected verification type with the approved plan.
    """
    from services.mental_model.living_agent import (
        assess_mental_model_readiness,
        observe_mental_model_event,
    )

    project = _require_project_access(db, current_user, project_id)
    spec_artifact, rtl_artifact = _resolve_active_artifacts(db, project)
    model_rev = _require_model_revision(
        db, project=project, mental_model_revision_id=request.mental_model_revision_id
    )
    approved_plan = request.approved_plan or {}
    if not approved_plan:
        raise HTTPException(status_code=400, detail="approved_plan is required")
    approved_plan = _hydrate_latest_plan_markdown(db, project, approved_plan)

    # Demo short-circuit: animated RunCard + fail→fix→pass without real EDA.
    try:
        from demo import demo_mode_enabled

        if demo_mode_enabled():
            from demo.staged_execute import run_demo_staged_execute
            from services.project_tasks import (
                create_staged_verification_task,
            )

            verification_task = create_staged_verification_task(
                db,
                project=project,
                user=current_user,
                verification_type=request.verification_type,
                approved_plan=approved_plan,
                thread_id=(request.thread_id if hasattr(request, "thread_id") else None),
            )
            return await run_demo_staged_execute(
                project_id=project.id,
                verification_type=request.verification_type,
                db_session=db,
                verification_task=verification_task,
            )
    except HTTPException:
        raise
    except Exception as exc:
        logger.warning("Demo staged execute failed; falling through: %s", exc)

    from services.project_tasks import (
        create_staged_verification_task,
    finalize_staged_verification_task,
    serialize_project_task,
)

    verification_task = create_staged_verification_task(
        db,
        project=project,
        user=current_user,
        verification_type=request.verification_type,
        approved_plan=approved_plan,
        thread_id=(request.thread_id if hasattr(request, "thread_id") else None),
    )

    content = _load_model_content(model_rev)
    model_ns = _normalize_for_recommender(content)
    vtype = _validate_verification_type(request.verification_type)
    mental_model_observations: List[Dict[str, Any]] = []
    mental_model_readiness = assess_mental_model_readiness(
        content,
        stage="execute",
        verification_type=vtype,
        approved_plan=approved_plan,
    )
    mental_model_observations.append(
        await observe_mental_model_event(
            db,
            model_revision=model_rev,
            user=current_user,
            event_type="mental_model.readiness.checked",
            payload={
                "verification_type": vtype,
                "agent": "mental_model",
                "readiness": mental_model_readiness,
            },
            apply_approved_intent=False,
        )
    )
    if not mental_model_readiness.get("safe"):
        raise HTTPException(
            status_code=409,
            detail={
                "message": "Mental model is not ready to execute the approved verification plan.",
                "readiness": mental_model_readiness,
            },
        )

    results = {}
    output_root = (
        outputs_dir()
        / "projects"
        / project.id
        / "staged-verification"
        / model_rev.id
    )
    staged_ai_client = None
    staged_llm_metadata: Dict[str, Any] = {}

    async def _require_staged_ai_client():
        nonlocal staged_ai_client, staged_llm_metadata
        if staged_ai_client is None:
            from services.mental_model.store import get_default_ai_client_adapter

            staged_ai_client, staged_llm_metadata = await get_default_ai_client_adapter(
                require_llm=True,
            )
        return staged_ai_client, staged_llm_metadata

    if vtype in ("uvm", "all") and isinstance(approved_plan.get("uvm"), dict):
        observation_ai_client = None
        try:
            observation_ai_client, _metadata = await _require_staged_ai_client()
        except Exception as exc:
            logger.warning("Could not attach AI client for UVM approval observation: %s", exc)
        mental_model_observations.append(
            await observe_mental_model_event(
                db,
                model_revision=model_rev,
                user=current_user,
                event_type="uvm.plan.approved",
                payload={
                    "verification_type": vtype,
                    "agent": "uvm",
                    "plan": approved_plan,
                },
                ai_client=observation_ai_client,
                apply_approved_intent=True,
            )
        )

    if vtype in ("unitsim", "all"):
        try:
            from services.verification.unitsim_loop import run_unitsim_loop

            unit_plan = approved_plan.get("unitsim")
            if not isinstance(unit_plan, dict):
                raise HTTPException(status_code=400, detail="Approved UnitSim plan is missing")

            unit_dir = output_root / "unitsim"
            unit_dir.mkdir(parents=True, exist_ok=True)
            approved_plan_path = unit_dir / "approved_unitsim_plan.json"
            _write_json_file(approved_plan_path, unit_plan)

            unit_ai_client, llm_metadata = await _require_staged_ai_client()
            unit_result = await run_unitsim_loop(
                project_id=project.id,
                model=model_ns,
                work_dir=str(unit_dir),
                target_module=model_ns.design.top_module,
                simulator="auto",
                ai_client=unit_ai_client,
                db_session=None,
                max_fix_iterations=3,
                approved_plan=unit_plan,
            )
            generated_files = [str(approved_plan_path)] + list(unit_result.generated_files)
            if unit_result.vcd_path:
                generated_files.append(unit_result.vcd_path)
            published = _publish_staged_generated_files(
                db,
                project,
                current_user,
                output_root=output_root,
                file_paths=generated_files,
                mental_model_revision_id=model_rev.id,
                verification_type=vtype,
                phase="unitsim",
            )
            results["unitsim"] = {
                "status": unit_result.status,
                "run_id": unit_result.run_id,
                "llm_provider": llm_metadata.get("provider"),
                "llm_model": llm_metadata.get("model"),
                "ai_client_passed": True,
                "scenarios": unit_result.test_plan_scenarios,
                "tests_passed": unit_result.tests_passed,
                "tests_failed": unit_result.tests_failed,
                "assertion_failures": unit_result.assertion_failures,
                "patch_proposals": unit_result.patch_proposals,
                "files": generated_files,
                "published_artifacts": published["created_count"],
                "published_artifact_ids": [
                    artifact["id"] for artifact in published["artifacts"]
                ],
                "sim_log_excerpt": unit_result.sim_log_excerpt,
                "phases": unit_result.phases,
            }
            mental_model_observations.append(
                await observe_mental_model_event(
                    db,
                    model_revision=model_rev,
                    user=current_user,
                    event_type="unitsim.result.completed",
                    payload={
                        "verification_type": vtype,
                        "agent": "unitsim",
                        "run_id": unit_result.run_id,
                        "status": unit_result.status,
                        "trusted": unit_result.status == "passed",
                        "plan": approved_plan,
                        "file_count": len(generated_files),
                        "artifact_ids": [
                            artifact["id"] for artifact in published["artifacts"]
                        ],
                        "result": {
                            "tests_passed": unit_result.tests_passed,
                            "tests_failed": unit_result.tests_failed,
                            "assertion_failures": unit_result.assertion_failures,
                        },
                    },
                    apply_approved_intent=False,
                )
            )
        except HTTPException:
            raise
        except Exception as e:
            logger.warning("UnitSim execution failed: %s", e)
            results["unitsim"] = {"status": "error", "error": str(e)}

    if vtype in ("formal", "all"):
        try:
            from services.verification.formal_gen import generate_formal_from_model_async
            from services.eda.symbiyosys import run_sby
            formal_plan = approved_plan.get("formal")
            if not isinstance(formal_plan, dict):
                raise HTTPException(status_code=400, detail="Approved Formal plan is missing")
            formal_dir = output_root / "formal"
            formal_dir.mkdir(parents=True, exist_ok=True)
            formal_ai_client = None
            llm_metadata: Dict[str, Any] = {}
            try:
                formal_ai_client, llm_metadata = await _require_staged_ai_client()
            except Exception as exc:
                logger.warning("Formal execution will use the approved plan without LLM enhancement: %s", exc)
            formal_out = await generate_formal_from_model_async(
                model_ns,
                work_dir=str(formal_dir),
                approved_plan=formal_plan,
                rtl_files=_collect_rtl_source_files(rtl_artifact, content),
                ai_client=formal_ai_client,
                require_llm=False,
            )
            sby_result = None
            if getattr(formal_out, "sby_file", ""):
                sby_result = run_sby(
                    getattr(formal_out, "sby_file"),
                    str(formal_dir),
                    timeout_seconds=300,
                )
                sby_result_path = formal_dir / "formal_result.json"
                _write_json_file(sby_result_path, sby_result.to_dict())
            formal_files = list(getattr(formal_out, "all_files", []) or [])
            if sby_result is not None:
                formal_files.append(str(formal_dir / "formal_result.json"))
            if not formal_files:
                formal_files = [
                    str(path)
                    for path in formal_dir.glob("*")
                    if path.is_file()
                ]
            published = _publish_staged_generated_files(
                db,
                project,
                current_user,
                output_root=output_root,
                file_paths=formal_files,
                mental_model_revision_id=model_rev.id,
                verification_type=vtype,
                phase="formal",
            )
            formal_status = "generated"
            formal_trusted = False
            formal_result_dict = None
            if sby_result is not None:
                formal_result_dict = sby_result.to_dict()
                validation_errors = list(getattr(formal_out, "validation_errors", []) or [])
                if sby_result.status == "PASS" and not validation_errors:
                    formal_status = "proved"
                    formal_trusted = True
                elif sby_result.status == "FAIL":
                    formal_status = "failed"
                elif sby_result.status in {"ERROR", "TIMEOUT"}:
                    formal_status = "generated_tool_unavailable" if "not found" in (sby_result.error_message or "").lower() else sby_result.status.lower()
            validation_errors = list(getattr(formal_out, "validation_errors", []) or [])
            validation_warnings = list(getattr(formal_out, "validation_warnings", []) or [])
            if validation_errors and formal_status == "generated":
                formal_status = "validation_failed"
            results["formal"] = {
                "status": formal_status,
                "trusted": formal_trusted,
                "properties": getattr(formal_out, "property_count", 0),
                "assumptions": getattr(formal_out, "assumption_count", 0),
                "covers": getattr(formal_out, "cover_count", 0),
                "validation_errors": validation_errors,
                "validation_warnings": validation_warnings,
                "rtl_source_files": len(getattr(formal_out, "rtl_files", []) or []),
                "llm_provider": llm_metadata.get("provider"),
                "llm_model": llm_metadata.get("model"),
                "llm_called": getattr(formal_out, "llm_called", False),
                "llm_used": getattr(formal_out, "llm_used", False),
                "llm_enhanced_files": len(getattr(formal_out, "llm_enhanced_files", []) or []),
                "formal_result": formal_result_dict,
                "closure": {
                    "status": "proved" if formal_trusted else formal_status,
                    "property_count": getattr(formal_out, "property_count", 0),
                    "assumption_count": getattr(formal_out, "assumption_count", 0),
                    "cover_count": getattr(formal_out, "cover_count", 0),
                    "tool_status": formal_result_dict.get("status") if isinstance(formal_result_dict, dict) else None,
                    "manual_log_endpoint": f"/api/v1/projects/{project.id}/formal-debug/logs",
                },
                "files": formal_files,
                "published_artifacts": published["created_count"],
                "published_artifact_ids": [
                    artifact["id"] for artifact in published["artifacts"]
                ],
            }
            mental_model_observations.append(
                await observe_mental_model_event(
                    db,
                    model_revision=model_rev,
                    user=current_user,
                    event_type="formal.files.generated",
                    payload={
                        "verification_type": vtype,
                        "agent": "formal",
                        "status": formal_status,
                        "trusted": formal_trusted,
                        "plan": approved_plan,
                        "result": formal_result_dict or {},
                        "file_count": len(formal_files),
                        "artifact_ids": [
                            artifact["id"] for artifact in published["artifacts"]
                        ],
                    },
                    apply_approved_intent=False,
                )
            )
            if formal_result_dict is not None:
                mental_model_observations.append(
                    await observe_mental_model_event(
                        db,
                        model_revision=model_rev,
                        user=current_user,
                        event_type="formal.proof.completed",
                        payload={
                            "verification_type": vtype,
                            "agent": "formal",
                            "status": formal_status,
                            "trusted": formal_trusted,
                            "plan": approved_plan,
                            "result": formal_result_dict,
                            "file_count": len(formal_files),
                            "artifact_ids": [
                                artifact["id"] for artifact in published["artifacts"]
                            ],
                        },
                        apply_approved_intent=False,
                    )
                )
        except HTTPException:
            raise
        except Exception as e:
            logger.warning("Formal execution failed: %s", e)
            results["formal"] = {"status": "error", "error": str(e)}

    if vtype in ("uvm", "all"):
        uvm_out = None
        file_paths: List[str] = []
        llm_metadata: Dict[str, Any] = {}
        approved_plan_path: Optional[Path] = None
        try:
            from services.verification.uvm_gen import generate_uvm_from_model_async
            from services.verification.uvm_validator import validate_uvm_environment
            from services.verification.compile_gate import run_compile_gate_with_repair

            model_ns = _apply_uvm_plan_to_model(model_ns, approved_plan)
            uvm_dir = output_root / "uvm"
            uvm_dir.mkdir(parents=True, exist_ok=True)
            approved_plan_path = uvm_dir / "approved_uvm_plan.json"
            _write_json_file(approved_plan_path, approved_plan.get("uvm", {}))
            approved_markdown_path: Optional[Path] = None
            uvm_plan_for_markdown = approved_plan.get("uvm") if isinstance(approved_plan.get("uvm"), dict) else {}
            approved_markdown = str(
                approved_plan.get("plan_markdown")
                or approved_plan.get("approved_plan_markdown")
                or uvm_plan_for_markdown.get("plan_markdown")
                or ""
            ).strip()
            if approved_markdown:
                approved_markdown_path = uvm_dir / "approved_uvm_plan.md"
                approved_markdown_path.write_text(approved_markdown, encoding="utf-8")

            uvm_ai_client, llm_metadata = await _require_staged_ai_client()
            uvm_out = await generate_uvm_from_model_async(
                model_ns,
                work_dir=str(uvm_dir),
                ai_client=uvm_ai_client,
                generation_mode="hybrid",
                require_llm=False,
            )
            uvm_plan_for_validation = _reconcile_uvm_plan_with_generated_files(approved_plan, uvm_out)
            _write_json_file(approved_plan_path, uvm_plan_for_validation)
            file_paths = [str(approved_plan_path)] + list(getattr(uvm_out, "all_files", []))
            if approved_markdown_path:
                file_paths.insert(1, str(approved_markdown_path))

            compile_gate_result = await run_compile_gate_with_repair(
                file_paths=getattr(uvm_out, "all_files", []),
                top_module=model_ns.design.top_module,
                mental_model=content,
                ai_client=uvm_ai_client,
                max_fix_attempts=2,
            )
            compile_gate = compile_gate_result.to_dict()
            compile_gate_path = uvm_dir / "compile_gate_report.json"
            _write_json_file(compile_gate_path, compile_gate)
            file_paths.append(str(compile_gate_path))

            rtl_source_files = _collect_rtl_source_files(rtl_artifact, content)
            spec_context = _load_full_spec_context_for_audit(project, spec_artifact)
            validation_result = validate_uvm_environment(
                work_dir=uvm_dir,
                generated_files=file_paths,
                top_module=model_ns.design.top_module,
                dut_rtl_files=rtl_source_files,
                generation_plan=uvm_plan_for_validation or getattr(uvm_out, "generation_policy", None),
            )
            validation = validation_result.to_dict()
            validation_path = uvm_dir / "uvm_validation_report.json"
            _write_json_file(validation_path, validation)
            file_paths.append(str(validation_path))

            cadence = _run_xcelium_after_uvm_validation(
                uvm_out=uvm_out,
                uvm_dir=uvm_dir,
                validation=validation,
                compile_gate=compile_gate,
                mental_model=content,
                rtl_files=rtl_source_files,
                spec_context=spec_context,
            )
            file_paths.extend(cadence.get("artifact_paths") or [])

            published = _publish_staged_generated_files(
                db,
                project,
                current_user,
                output_root=output_root,
                file_paths=file_paths,
                mental_model_revision_id=model_rev.id,
                verification_type=vtype,
                phase="uvm",
            )
            report = cadence.get("report") if isinstance(cadence.get("report"), dict) else None
            if report:
                report_filename = str(report.get("filename") or "")
                report_artifact = next(
                    (
                        artifact for artifact in published["artifacts"]
                        if str(artifact.get("filename") or "") == report_filename
                    ),
                    None,
                )
                if report_artifact:
                    report["artifact_id"] = report_artifact.get("id")
                    report["artifact"] = {
                        "id": report_artifact.get("id"),
                        "filename": report_artifact.get("filename"),
                        "content_type": report_artifact.get("content_type"),
                    }
            from services.verification.compile_gate import decide_uvm_trust

            validation_trusted = bool(validation.get("trusted"))
            compile_gate_passed = bool(compile_gate.get("passed"))
            static_trusted = validation_trusted and compile_gate_passed
            trust_decision = decide_uvm_trust(validation, compile_gate)
            cadence_status = str(cadence.get("status") or "skipped").lower()
            cadence_required = bool(cadence.get("required"))
            if cadence_status == "passed":
                uvm_status = "passed"
            elif cadence_status in {"needs_action", "needs_review"}:
                uvm_status = cadence_status
            elif cadence_required:
                uvm_status = "failed"
            elif bool(cadence.get("attempted")):
                uvm_status = cadence_status or "failed"
            else:
                uvm_status = trust_decision["status"]
            trusted = static_trusted and (not cadence_required or cadence_status == "passed")
            if cadence_required and cadence_status == "passed":
                trusted = True
            uvm_result = {
                "status": uvm_status,
                "trusted": trusted,
                "static_trusted": static_trusted,
                "authoritative_compile": trust_decision.get("authoritative_compile", False),
                "generation_mode": getattr(uvm_out, "generation_mode", "hybrid"),
                "llm_provider": llm_metadata.get("provider"),
                "llm_model": llm_metadata.get("model"),
                "llm_called": getattr(uvm_out, "llm_called", False),
                "llm_status": getattr(uvm_out, "llm_status", "unknown"),
                "llm_candidate_files": len(getattr(uvm_out, "llm_candidate_files", []) or []),
                "llm_enhanced_files": len(getattr(uvm_out, "llm_enhanced_files", [])),
                "llm_errors": getattr(uvm_out, "llm_errors", []),
                "files": len(file_paths),
                "file_paths": file_paths,
                "published_artifacts": published["created_count"],
                "published_artifact_ids": [
                    artifact["id"] for artifact in published["artifacts"]
                ],
                "summary": getattr(uvm_out, "summary", ""),
                "validation": validation,
                "compile_gate": compile_gate,
                "cadence": cadence,
            }
            persisted_run_id = _persist_staged_verification_record(
                db,
                project=project,
                run_type="uvm",
                target_module=model_ns.design.top_module,
                status=str(uvm_result["status"]),
                mental_model_revision=model_rev.revision,
                result=uvm_result,
            )
            if persisted_run_id:
                uvm_result["run_id"] = persisted_run_id
            results["uvm"] = uvm_result
            mental_model_observations.append(
                await observe_mental_model_event(
                    db,
                    model_revision=model_rev,
                    user=current_user,
                    event_type="uvm.files.generated",
                    payload={
                        "verification_type": vtype,
                        "agent": "uvm",
                        "run_id": persisted_run_id,
                        "plan": approved_plan,
                        "file_count": len(file_paths),
                        "artifact_ids": [
                            artifact["id"] for artifact in published["artifacts"]
                        ],
                    },
                    apply_approved_intent=False,
                )
            )
            mental_model_observations.append(
                await observe_mental_model_event(
                    db,
                    model_revision=model_rev,
                    user=current_user,
                    event_type="uvm.validation.completed",
                    payload={
                        "verification_type": vtype,
                        "agent": "uvm",
                        "run_id": persisted_run_id,
                        "plan": approved_plan,
                        "validation": validation,
                        "compile_gate": compile_gate,
                        "cadence": cadence,
                        "trusted": trusted,
                        "file_count": len(file_paths),
                        "artifact_ids": [
                            artifact["id"] for artifact in published["artifacts"]
                        ],
                    },
                    apply_approved_intent=False,
                )
            )
        except HTTPException:
            raise
        except Exception as e:
            logger.warning("UVM execution failed: %s", e, exc_info=True)
            existing_uvm_result = results.get("uvm")
            if isinstance(existing_uvm_result, dict):
                # UVM generation/validation may already have completed successfully.
                # Do not turn a valid generated environment into a failed verdict just
                # because a later evidence/DB bookkeeping step failed.
                existing_uvm_result.setdefault("post_process_errors", []).append(str(e))
                existing_uvm_result.setdefault(
                    "post_process_warning",
                    "UVM files were generated, but a post-processing step failed.",
                )
            elif uvm_out is not None:
                generated_files = list(getattr(uvm_out, "all_files", []) or [])
                fallback_paths = file_paths or (
                    ([str(approved_plan_path)] if approved_plan_path else []) + generated_files
                )
                results["uvm"] = {
                    "status": "post_process_failed",
                    "trusted": False,
                    "error": str(e),
                    "post_process_warning": (
                        "UVM files were generated, but validation, publishing, or "
                        "evidence bookkeeping failed afterward."
                    ),
                    "generation_mode": getattr(uvm_out, "generation_mode", "hybrid"),
                    "llm_provider": llm_metadata.get("provider"),
                    "llm_model": llm_metadata.get("model"),
                    "llm_called": getattr(uvm_out, "llm_called", False),
                    "llm_status": getattr(uvm_out, "llm_status", "unknown"),
                    "llm_candidate_files": len(getattr(uvm_out, "llm_candidate_files", []) or []),
                    "llm_enhanced_files": len(getattr(uvm_out, "llm_enhanced_files", [])),
                    "llm_errors": getattr(uvm_out, "llm_errors", []),
                    "files": len(fallback_paths),
                    "file_paths": fallback_paths,
                    "summary": getattr(uvm_out, "summary", ""),
                }
            else:
                results["uvm"] = {"status": "error", "error": str(e)}

    overall = "completed"
    for r in results.values():
        if isinstance(r, dict) and r.get("status") in {
            "error",
            "failed",
            "partial",
            "validation_failed",
            "compile_gate_failed",
            "post_process_failed",
            "awaiting_fix_approval",
            "needs_action",
            "needs_review",
        }:
            overall = "partial"
            break

    final_task = finalize_staged_verification_task(db, verification_task, overall)

    return {
        "status": overall,
        "verification_type": vtype,
        "mental_model_revision_id": request.mental_model_revision_id,
        "mental_model_readiness": mental_model_readiness,
        "results": results,
        "mental_model_observations": mental_model_observations,
        "artifacts_refreshed": True,
        "task_id": final_task.id,
        "task": serialize_project_task(final_task),
    }


@router.post("/{project_id}/verification/execute/stream")
async def execute_verification_stream(
    project_id: str,
    request: ExecuteRequest,
    current_user: User = Depends(_get_current_user),
    db: Session = Depends(get_db),
):
    """
    Streaming Stage 4 endpoint.

    Emits newline-delimited JSON events while the normal execute path runs.
    The final event is {"type": "complete", "result": ...}.
    """

    async def event_stream():
        from services.verification.uvm_gen import (
            reset_uvm_progress_callback,
            set_uvm_progress_callback,
        )

        queue: asyncio.Queue[Any] = asyncio.Queue()
        done = object()

        def publish(event: Dict[str, Any]) -> None:
            event.setdefault("ts", time.time())
            queue.put_nowait(event)

        async def runner():
            token = set_uvm_progress_callback(publish)
            try:
                publish({
                    "type": "started",
                    "phase": "execute",
                    "message": "Starting staged verification execution",
                    "verification_type": request.verification_type,
                })
                result = await execute_verification(
                    project_id,
                    request,
                    current_user=current_user,
                    db=db,
                )
                publish({
                    "type": "complete",
                    "phase": "execute",
                    "message": "Verification execution finished",
                    "result": result,
                })
            except HTTPException as exc:
                publish({
                    "type": "error",
                    "phase": "execute",
                    "message": str(exc.detail),
                    "status_code": exc.status_code,
                })
            except Exception as exc:
                logger.exception("Streaming staged verification failed")
                publish({
                    "type": "error",
                    "phase": "execute",
                    "message": str(exc),
                })
            finally:
                reset_uvm_progress_callback(token)
                queue.put_nowait(done)

        task = asyncio.create_task(runner())
        try:
            while True:
                item = await queue.get()
                if item is done:
                    break
                yield json.dumps(item, default=str) + "\n"
            await task
        finally:
            if not task.done():
                task.cancel()

    return StreamingResponse(
        event_stream(),
        media_type="application/x-ndjson",
        headers={"Cache-Control": "no-cache"},
    )
