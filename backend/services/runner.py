"""
Runner Service — Job lifecycle management for verification runs.

ChipStack-style: All verification flows through the unified agentic
orchestrator (agent_tools.orchestrator_tool), not the old sequential
pipeline. The runner handles threading, DB sync, and cancellation.
"""

import asyncio
import io
import json
import logging
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

from sqlalchemy import func

from database.database import SessionLocal
from database.models import Run, RunEvent

logger = logging.getLogger(__name__)

# ─── In-Memory Job State ─────────────────────────────────────────────

JOBS: Dict[str, Dict[str, Any]] = {}
EVENT_LOCK = threading.Lock()


def _is_cancel_requested(run_id: str) -> bool:
    with EVENT_LOCK:
        job = JOBS.get(run_id)
        return bool(job and job.get("cancel_requested"))


def _derive_run_status_from_verify_report(verify_result: Dict[str, Any]) -> tuple[str, Dict[str, Any]]:
    """
    Map orchestrator `handle_run_verification` JSON to DB run status + summary.

    The orchestrator returns top-level keys like unit_simulation / formal_verification,
    not a nested `results` bag — reading only `results` falsely marks every run completed.
    """
    if not isinstance(verify_result, dict):
        return "failed", {"overall_status": "failed", "error": "invalid_verification_result"}

    phase_keys = ("unit_simulation", "formal_verification", "uvm")
    phases: Dict[str, Any] = {
        k: v
        for k, v in verify_result.items()
        if k in phase_keys and isinstance(v, dict)
    }

    legacy = verify_result.get("results")
    if isinstance(legacy, dict):
        for name, payload in legacy.items():
            if isinstance(payload, dict) and name not in phases:
                phases[name] = payload

    top_status = str(verify_result.get("status") or "").lower()
    overall_status = "completed"

    if top_status in ("completed_with_errors", "error", "failed"):
        overall_status = "failed"
    elif top_status in ("completed_with_failures",):
        overall_status = "failed"

    for phase in phases.values():
        pstatus = str(phase.get("status") or "").lower()
        if pstatus in ("error", "failed", "blocked"):
            overall_status = "failed"
            break
        if int(phase.get("tests_failed") or 0) > 0:
            overall_status = "failed"
            break

    if not phases and top_status not in ("completed_all_passed",):
        overall_status = "failed"

    summary: Dict[str, Any] = {
        "overall_status": overall_status,
        "report_status": verify_result.get("status"),
        "phases": phases,
        "project_id": verify_result.get("project_id"),
        "target_module": verify_result.get("target_module"),
    }
    return overall_status, summary


# ─── DB Sync Helpers ──────────────────────────────────────────────────

def sync_job_to_db(run_id: str, updates: dict) -> None:
    """Safely apply updates to run records."""
    db = SessionLocal()
    try:
        run_record = db.query(Run).filter(Run.id == run_id).first()
        if run_record:
            for k, v in updates.items():
                setattr(run_record, k, v)
            db.commit()
            if "status" in updates:
                try:
                    from services.project_tasks import sync_run_to_linked_task

                    sync_run_to_linked_task(db, run_record)
                except Exception:
                    pass
    finally:
        db.close()


def append_run_event(
    run_id: str, message: str, phase: str = "runtime", level: str = "info"
) -> None:
    """Persist an event line and mirror it to in-memory logs."""
    with EVENT_LOCK:
        job_state = JOBS.setdefault(
            run_id,
            {
                "status": "queued",
                "logs": [],
                "error": None,
                "results": None,
                "next_seq_no": 1,
                "cancel_requested": False,
                "started_at": None,
            },
        )
        seq_no = int(job_state.get("next_seq_no", 1))
        job_state["next_seq_no"] = seq_no + 1
        job_state["logs"].append(message)

    db = SessionLocal()
    try:
        event = RunEvent(
            id=str(uuid.uuid4()),
            run_id=run_id,
            seq_no=seq_no,
            event_kind="log",
            phase=phase,
            level=level,
            message=message,
        )
        db.add(event)
        db.commit()

        run_record = db.query(Run).filter(Run.id == run_id).first()
        if run_record and run_record.logs_path:
            logs_path = Path(run_record.logs_path)
            logs_path.parent.mkdir(parents=True, exist_ok=True)
            with open(logs_path, "w", encoding="utf-8") as f:
                json.dump({"logs": job_state["logs"]}, f)
    finally:
        db.close()


def save_logs_to_file(run_id: str, output_dir: str) -> None:
    """Save memory logs to a JSON file."""
    if run_id in JOBS:
        log_path = Path(output_dir) / "logs.json"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(log_path, "w", encoding="utf-8") as f:
            json.dump({"logs": JOBS[run_id]["logs"]}, f)


# ─── Cancellation ────────────────────────────────────────────────────

def cancel_job(run_id: str) -> bool:
    """Request cancellation for an in-flight run."""
    with EVENT_LOCK:
        job = JOBS.get(run_id)
        if not job:
            return False

        current_status = str(job.get("status") or "").lower()
        if current_status in {"completed", "failed", "cancelled", "interrupted"}:
            return True

        job["cancel_requested"] = True
        job["status"] = "cancelled"
        job["error"] = "Run cancelled by user"

    append_run_event(
        run_id,
        "Cancellation requested by user. Awaiting pipeline shutdown...",
        phase="runtime",
        level="warning",
    )

    started_at = None
    with EVENT_LOCK:
        started_at = JOBS.get(run_id, {}).get("started_at")

    elapsed_s = int(time.time() - started_at) if started_at else 0
    sync_job_to_db(
        run_id,
        {
            "status": "cancelled",
            "completed_at": datetime.now(timezone.utc),
            "execution_time": f"{elapsed_s}s",
            "verification_summary": "Run cancelled by user",
        },
    )
    return True


# ─── New Agentic Pipeline Thread ──────────────────────────────────────

def run_pipeline_thread(run_id: str, rtl_path: str, spec_path: str, output_dir: str):
    """
    ChipStack-style pipeline: uses the new orchestrator tools
    (analyzeDesign → runVerification) instead of the old sequential
    ChipVerifyOrchestrator.
    """
    start_t = time.time()
    try:
        with EVENT_LOCK:
            job_state = JOBS.setdefault(
                run_id,
                {
                    "status": "queued",
                    "logs": [],
                    "error": None,
                    "results": None,
                    "next_seq_no": 1,
                    "cancel_requested": False,
                    "started_at": None,
                },
            )
            cancel_requested = bool(job_state.get("cancel_requested"))
            job_state["started_at"] = start_t
            if cancel_requested:
                job_state["status"] = "cancelled"
                job_state["error"] = "Run cancelled before execution started"
            else:
                job_state["status"] = "running"

        if cancel_requested:
            append_run_event(run_id, "Run cancelled before pipeline start.",
                             phase="runtime", level="warning")
            sync_job_to_db(run_id, {
                "status": "cancelled",
                "completed_at": datetime.now(timezone.utc),
                "execution_time": "0s",
                "verification_summary": "Run cancelled by user",
            })
            save_logs_to_file(run_id, output_dir)
            return

        append_run_event(run_id, "Starting verification pipeline...", phase="runtime")
        sync_job_to_db(run_id, {"status": "running"})

        # ── Phase 1: Analyze Design (Mental Model) ────────────────
        append_run_event(run_id, "Phase 1: Analyzing design...", phase="scan")

        import asyncio as _asyncio
        from agent_tools.orchestrator_tool import handle_analyze_design

        # handle_analyze_design is async — run it properly from this sync thread
        try:
            _loop = _asyncio.get_event_loop()
            if _loop.is_running():
                import concurrent.futures as _cf
                with _cf.ThreadPoolExecutor(max_workers=1) as _ex:
                    _fut = _ex.submit(
                        _asyncio.run,
                        handle_analyze_design(
                            project_id=_get_project_id_for_run(run_id),
                            rtl_path=rtl_path,
                            spec_path=spec_path,
                        )
                    )
                    analyze_result_raw = _fut.result(timeout=120)
            else:
                analyze_result_raw = _loop.run_until_complete(
                    handle_analyze_design(
                        project_id=_get_project_id_for_run(run_id),
                        rtl_path=rtl_path,
                        spec_path=spec_path,
                    )
                )
        except Exception:
            analyze_result_raw = _asyncio.run(
                handle_analyze_design(
                    project_id=_get_project_id_for_run(run_id),
                    rtl_path=rtl_path,
                    spec_path=spec_path,
                )
            )

        # Result may be JSON string or dict
        if isinstance(analyze_result_raw, str):
            import json as _json
            try:
                analyze_result = _json.loads(analyze_result_raw)
            except Exception:
                analyze_result = {"summary": analyze_result_raw}
        else:
            analyze_result = analyze_result_raw or {}

        if _is_cancel_requested(run_id):
            _finalize_cancelled(run_id, start_t, output_dir)
            return

        summary = analyze_result.get("summary", "Design analyzed")
        append_run_event(run_id, f"Mental model ready: {summary}", phase="understand")

        # ── Phase 2: Run All Verification ─────────────────────────
        append_run_event(run_id, "Phase 2: Running all verification strategies...",
                         phase="generate")

        from agent_tools.orchestrator_tool import handle_run_verification

        # handle_run_verification is also async — same pattern
        try:
            _loop2 = _asyncio.get_event_loop()
            if _loop2.is_running():
                with _cf.ThreadPoolExecutor(max_workers=1) as _ex2:
                    _fut2 = _ex2.submit(
                        _asyncio.run,
                        handle_run_verification(
                            project_id=_get_project_id_for_run(run_id),
                            verification_type="all",
                            rtl_path=rtl_path,
                            spec_path=spec_path,
                            output_dir=output_dir,
                        )
                    )
                    verify_result_raw = _fut2.result(timeout=300)
            else:
                verify_result_raw = _loop2.run_until_complete(
                    handle_run_verification(
                        project_id=_get_project_id_for_run(run_id),
                        verification_type="all",
                        rtl_path=rtl_path,
                        spec_path=spec_path,
                        output_dir=output_dir,
                    )
                )
        except Exception:
            verify_result_raw = _asyncio.run(
                handle_run_verification(
                    project_id=_get_project_id_for_run(run_id),
                    verification_type="all",
                    rtl_path=rtl_path,
                    spec_path=spec_path,
                    output_dir=output_dir,
                )
            )

        if isinstance(verify_result_raw, str):
            try:
                verify_result = _json.loads(verify_result_raw)
            except Exception:
                verify_result = {"results": {}}
        else:
            verify_result = verify_result_raw or {}

        if _is_cancel_requested(run_id):
            _finalize_cancelled(run_id, start_t, output_dir)
            return

        # ── Finalize ──────────────────────────────────────────────
        overall_status, summary = _derive_run_status_from_verify_report(verify_result)
        phases = summary.get("phases") or {}

        for phase_name, phase_result in phases.items():
            if isinstance(phase_result, dict):
                pstatus = phase_result.get("status", "unknown")
                tests = phase_result.get("tests_passed")
                failed = phase_result.get("tests_failed")
                detail = f"{phase_name}: {pstatus}"
                if tests is not None or failed is not None:
                    detail += f" (passed={tests or 0}, failed={failed or 0})"
                append_run_event(run_id, detail, phase="simulate", level="info")

        with EVENT_LOCK:
            JOBS[run_id]["status"] = overall_status
            JOBS[run_id]["results"] = summary

        append_run_event(
            run_id,
            f"Pipeline finished: {overall_status}.",
            phase="verdict",
            level="info",
        )

        elapsed = int(time.time() - start_t)
        sync_job_to_db(run_id, {
            "status": overall_status,
            "completed_at": datetime.now(timezone.utc),
            "execution_time": f"{elapsed}s",
            "verification_summary": json.dumps(summary),
        })
        save_logs_to_file(run_id, output_dir)

    except Exception as e:
        if _is_cancel_requested(run_id):
            _finalize_cancelled(run_id, start_t, output_dir)
            return

        with EVENT_LOCK:
            JOBS[run_id]["status"] = "failed"
            JOBS[run_id]["error"] = str(e)
        append_run_event(run_id, f"Pipeline failed: {e}",
                         phase="runtime", level="error")

        elapsed = int(time.time() - start_t)
        sync_job_to_db(run_id, {
            "status": "failed",
            "completed_at": datetime.now(timezone.utc),
            "execution_time": f"{elapsed}s",
            "verification_summary": str(e),
        })
        save_logs_to_file(run_id, output_dir)


def _finalize_cancelled(run_id: str, start_t: float, output_dir: str) -> None:
    elapsed = int(time.time() - start_t)
    with EVENT_LOCK:
        JOBS[run_id]["status"] = "cancelled"
    append_run_event(run_id, "Pipeline stopped after cancellation.",
                     phase="runtime", level="warning")
    sync_job_to_db(run_id, {
        "status": "cancelled",
        "completed_at": datetime.now(timezone.utc),
        "execution_time": f"{elapsed}s",
        "verification_summary": "Run cancelled by user",
    })
    save_logs_to_file(run_id, output_dir)


def _get_project_id_for_run(run_id: str) -> str:
    """Look up the project ID for a run from the database."""
    db = SessionLocal()
    try:
        run_record = db.query(Run).filter(Run.id == run_id).first()
        return run_record.project_id if run_record else ""
    finally:
        db.close()


# ─── Start Job ────────────────────────────────────────────────────────

def start_job(run_id: str, rtl_path: str, spec_path: str, output_dir: str):
    """Queue and start a verification run in a background thread."""
    db = SessionLocal()
    try:
        max_seq = (
            db.query(func.max(RunEvent.seq_no))
            .filter(RunEvent.run_id == run_id)
            .scalar()
        )
        next_seq_no = int(max_seq or 0) + 1
    finally:
        db.close()

    with EVENT_LOCK:
        existing = JOBS.get(run_id, {})
        JOBS[run_id] = {
            **existing,
            "status": "queued",
            "logs": existing.get("logs", []),
            "error": existing.get("error"),
            "results": existing.get("results"),
            "next_seq_no": next_seq_no,
            "cancel_requested": bool(existing.get("cancel_requested", False)),
            "started_at": existing.get("started_at"),
        }
    append_run_event(run_id, "Run queued.", phase="runtime", level="info")

    thread = threading.Thread(
        target=run_pipeline_thread, args=(run_id, rtl_path, spec_path, output_dir)
    )
    thread.daemon = True
    thread.start()


# ─── Block Smoke (Industry Profile) ──────────────────────────────────

def run_block_smoke_thread(
    run_id: str,
    rtl_path: str,
    spec_path: str,
    output_dir: str,
    mental_model_content: dict[str, Any] | None = None,
):
    _ = spec_path
    start_t = time.time()
    with EVENT_LOCK:
        job_state = JOBS.setdefault(
            run_id,
            {
                "status": "queued",
                "logs": [],
                "error": None,
                "results": None,
                "next_seq_no": 1,
                "cancel_requested": False,
                "started_at": None,
            },
        )
        cancel_requested = bool(job_state.get("cancel_requested"))
        job_state["started_at"] = start_t
        job_state["status"] = "cancelled" if cancel_requested else "running"

    if cancel_requested:
        append_run_event(
            run_id,
            "Run cancelled before block smoke verification started.",
            phase="runtime",
            level="warning",
        )
        sync_job_to_db(
            run_id,
            {
                "status": "cancelled",
                "completed_at": datetime.now(timezone.utc),
                "execution_time": "0s",
                "verification_summary": "Run cancelled by user",
            },
        )
        save_logs_to_file(run_id, output_dir)
        return

    sync_job_to_db(run_id, {"status": "running"})
    append_run_event(
        run_id,
        "Starting industry block smoke profile.",
        phase="runtime",
        level="info",
    )

    def emit(phase: str, message: str, level: str = "info") -> None:
        if not _is_cancel_requested(run_id):
            append_run_event(run_id, message, phase=phase, level=level)

    try:
        from services.block_smoke import run_block_smoke_verification

        result = run_block_smoke_verification(
            rtl_path=rtl_path,
            output_dir=output_dir,
            mental_model=mental_model_content,
            event_callback=emit,
        )
        elapsed = int(time.time() - start_t)
        if _is_cancel_requested(run_id):
            status = "cancelled"
            summary = "Run cancelled by user"
        else:
            status = str(result.get("status") or "failed")
            summary = json.dumps(result)

        with EVENT_LOCK:
            job = JOBS.setdefault(run_id, {})
            job["status"] = status
            job["results"] = result
            if status in {"failed", "blocked"}:
                job["error"] = result.get("message")

        sync_job_to_db(
            run_id,
            {
                "status": status,
                "completed_at": datetime.now(timezone.utc),
                "execution_time": f"{elapsed}s",
                "verification_summary": summary,
            },
        )
        save_logs_to_file(run_id, output_dir)
    except Exception as exc:
        elapsed = int(time.time() - start_t)
        with EVENT_LOCK:
            JOBS[run_id]["status"] = "failed"
            JOBS[run_id]["error"] = str(exc)
        append_run_event(
            run_id,
            f"Block smoke verification failed: {exc}",
            phase="unit_sim.failed",
            level="error",
        )
        sync_job_to_db(
            run_id,
            {
                "status": "failed",
                "completed_at": datetime.now(timezone.utc),
                "execution_time": f"{elapsed}s",
                "verification_summary": str(exc),
            },
        )
        save_logs_to_file(run_id, output_dir)


def start_block_smoke_job(
    run_id: str,
    rtl_path: str,
    spec_path: str,
    output_dir: str,
    mental_model_content: dict[str, Any] | None = None,
):
    db = SessionLocal()
    try:
        max_seq = (
            db.query(func.max(RunEvent.seq_no))
            .filter(RunEvent.run_id == run_id)
            .scalar()
        )
        next_seq_no = int(max_seq or 0) + 1
    finally:
        db.close()

    with EVENT_LOCK:
        JOBS[run_id] = {
            "status": "queued",
            "logs": [],
            "error": None,
            "results": None,
            "next_seq_no": next_seq_no,
            "cancel_requested": False,
            "started_at": None,
        }
    append_run_event(run_id, "Block smoke run queued.", phase="runtime", level="info")

    thread = threading.Thread(
        target=run_block_smoke_thread,
        args=(run_id, rtl_path, spec_path, output_dir, mental_model_content),
    )
    thread.daemon = True
    thread.start()


# ─── Status Query ─────────────────────────────────────────────────────

def get_job_status(run_id: str) -> dict:
    """Retrieve run status safely."""
    with EVENT_LOCK:
        job = JOBS.get(run_id)
        if not job:
            return {"status": "not_found", "logs": []}
        return {
            "status": job.get("status", "unknown"),
            "logs": list(job.get("logs", [])),
            "error": job.get("error"),
            "results": job.get("results"),
            "cancel_requested": bool(job.get("cancel_requested")),
        }
