"""
Demo scenario state machine for the SHA-256 fail → fix → pass loop.

Env:
  CHIPVERIFY_DEMO_SCENARIO=sha256_fix_loop  (default when demo mode is on)
  CHIPVERIFY_DEMO_SCENARIO=pass               (always pass — legacy)
"""

from __future__ import annotations

import os
import threading
from typing import Any

_lock = threading.Lock()
# project_id -> step: "run_1" | "await_apply" | "run_2" | "done"
_state: dict[str, str] = {}

SCENARIO_FIX_LOOP = "sha256_fix_loop"
SCENARIO_PASS = "pass"

TB_FILE = "tb_sha256_unit.sv"

# Intentional "buggy" vs fixed scoreboard excerpt for DiffCard.
TB_ORIGINAL_SNIPPET = """\
  // BUG (demo): multi-block padding drops msg_last on the final beat
  task automatic drive_padded_message(input integer nbytes);
    begin
      // ... truncated for demo ...
      drive_block(block, 1'b0); // should be msg_last=1 on final block
    end
  endtask
"""

TB_FIXED_SNIPPET = """\
  // FIX: assert msg_last on the final padded block
  task automatic drive_padded_message(input integer nbytes);
    begin
      // ... truncated for demo ...
      drive_block(block, 1'b1); // msg_last=1 on final block
    end
  endtask
"""


def scenario_name() -> str:
    raw = (os.getenv("CHIPVERIFY_DEMO_SCENARIO") or "").strip().lower()
    if raw in {SCENARIO_FIX_LOOP, "fix_loop", "fail_then_fix"}:
        return SCENARIO_FIX_LOOP
    if raw in {SCENARIO_PASS, "always_pass", "legacy"}:
        return SCENARIO_PASS
    # Default for demo mode: tell the product story.
    return SCENARIO_FIX_LOOP


def reset_scenario(project_id: str | None = None) -> None:
    with _lock:
        if project_id:
            _state.pop(str(project_id), None)
        else:
            _state.clear()


def get_step(project_id: str) -> str:
    pid = str(project_id or "")
    with _lock:
        return _state.get(pid, "run_1")


def mark_applied(project_id: str) -> str:
    """Advance after DiffCard Apply — next execute is run_2 (pass)."""
    pid = str(project_id or "")
    with _lock:
        _state[pid] = "run_2"
        return _state[pid]


def advance_after_fail(project_id: str) -> str:
    pid = str(project_id or "")
    with _lock:
        _state[pid] = "await_apply"
        return _state[pid]


def advance_after_pass(project_id: str) -> str:
    pid = str(project_id or "")
    with _lock:
        _state[pid] = "done"
        return _state[pid]


def unitsim_result(project_id: str) -> dict[str, Any]:
    """JSON-serializable UnitSim fixture for the current scenario step."""
    if scenario_name() != SCENARIO_FIX_LOOP:
        return _pass_unitsim(project_id)

    step = get_step(project_id)
    if step in {"run_1", "await_apply"}:
        return {
            "status": "FAIL",
            "verdict": "FAIL",
            "tests_passed": 2,
            "tests_failed": 1,
            "tests_total": 3,
            "assertion_failures": [
                {
                    "test": "multi_block_padding",
                    "message": "Expected digest for 55-byte message; got stale H chain (msg_last never asserted)",
                    "file": TB_FILE,
                    "line": 88,
                }
            ],
            "test_results": [
                {"name": "empty_nist", "status": "PASS", "detail": "NIST empty digest matched"},
                {"name": "single_block_abc", "status": "PASS", "detail": "\"abc\" digest matched"},
                {
                    "name": "multi_block_padding",
                    "status": "FAIL",
                    "detail": "msg_last not driven on final padded block — digest hold stale",
                },
            ],
            "duration_ms": 860,
            "log_excerpt": (
                "PASS: empty_nist\n"
                "PASS: single_block_abc\n"
                "FAIL: multi_block_padding — expected digest mismatch\n"
                "UNITSIM FAIL — sha256_accelerator (2/3)\n"
            ),
            "coverage": {"line": 0.72, "functional": 0.61},
            "engine": "demo_fixture",
            "demo": True,
            "demo_step": step,
            "project_id": project_id,
        }

    # run_2 / done
    return _pass_unitsim(project_id, coverage_line=0.94, coverage_fn=0.91)


def _pass_unitsim(
    project_id: str,
    *,
    coverage_line: float = 0.94,
    coverage_fn: float = 0.91,
) -> dict[str, Any]:
    return {
        "status": "PASS",
        "verdict": "PASS",
        "tests_passed": 3,
        "tests_failed": 0,
        "tests_total": 3,
        "assertion_failures": [],
        "test_results": [
            {"name": "empty_nist", "status": "PASS", "detail": "NIST empty digest matched"},
            {"name": "single_block_abc", "status": "PASS", "detail": "\"abc\" digest matched"},
            {"name": "multi_block_padding", "status": "PASS", "detail": "Final msg_last + digest OK"},
        ],
        "duration_ms": 640,
        "log_excerpt": (
            "PASS: empty_nist\n"
            "PASS: single_block_abc\n"
            "PASS: multi_block_padding\n"
            "UNITSIM PASS — sha256_accelerator (3/3)\n"
        ),
        "coverage": {"line": coverage_line, "functional": coverage_fn},
        "engine": "demo_fixture",
        "demo": True,
        "demo_step": get_step(project_id),
        "project_id": project_id,
    }


def formal_result(project_id: str) -> dict[str, Any]:
    """Skip formal on first fail run; prove on pass run."""
    if scenario_name() == SCENARIO_FIX_LOOP and get_step(project_id) in {"run_1", "await_apply"}:
        return {
            "status": "SKIPPED",
            "verdict": "SKIPPED",
            "mode": "prove",
            "depth": 64,
            "properties": {},
            "proven": 0,
            "failed": 0,
            "bounded": 0,
            "engine": "demo_fixture",
            "demo": True,
            "demo_step": get_step(project_id),
            "project_id": project_id,
            "target_module": "sha256_accelerator",
            "summary": "Formal deferred until UnitSim multi_block_padding is green.",
        }
    return {
        "status": "PROVEN",
        "verdict": "PROVEN",
        "mode": "prove",
        "depth": 64,
        "properties": {
            "a_reset_clears_hash": "PROVEN",
            "a_msg_transfer_stable": "PROVEN",
            "a_hash_hold": "PROVEN",
        },
        "proven": 3,
        "failed": 0,
        "bounded": 0,
        "engine": "demo_fixture",
        "demo": True,
        "demo_step": get_step(project_id),
        "project_id": project_id,
        "target_module": "sha256_accelerator",
    }


def ensure_fix_patch(project_id: str, db_session: Any = None) -> dict[str, Any] | None:
    """Create a pending DiffCard patch for the TB padding bug (idempotent-ish)."""
    if scenario_name() != SCENARIO_FIX_LOOP:
        return None
    if get_step(project_id) not in {"run_1", "await_apply"}:
        return None
    try:
        from services.verification.patch_service import create_patch_proposal, list_pending_patches

        pending = list_pending_patches(project_id, db_session) or []
        for p in pending:
            title = str(p.get("title") or "") if isinstance(p, dict) else ""
            if "msg_last" in title.lower() or "padding" in title.lower():
                advance_after_fail(project_id)
                return {"id": p.get("id"), "reused": True}

        proposal = create_patch_proposal(
            project_id=project_id,
            source_agent="unitsim",
            title="Fix msg_last on final padded block",
            reason=(
                "UnitSim multi_block_padding failed: the testbench never asserted "
                "msg_last on the final block, so the DUT never presented the digest."
            ),
            file_path=TB_FILE,
            original_content=TB_ORIGINAL_SNIPPET,
            proposed_content=TB_FIXED_SNIPPET,
            db_session=db_session,
        )
        advance_after_fail(project_id)
        return {"id": proposal.id, "title": proposal.title, "created": True}
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc)}
