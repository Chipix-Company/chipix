"""Tests for verification report → run status mapping in runner."""

import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from services.runner import _derive_run_status_from_verify_report  # noqa: E402


def test_all_passed_orchestrator_report_marks_completed():
    report = {
        "status": "completed_all_passed",
        "unit_simulation": {"status": "ok", "tests_passed": 3, "tests_failed": 0},
    }
    status, summary = _derive_run_status_from_verify_report(report)
    assert status == "completed"
    assert summary["phases"]["unit_simulation"]["tests_passed"] == 3


def test_empty_results_bag_marks_failed_not_completed():
    status, _ = _derive_run_status_from_verify_report({"status": "running", "results": {}})
    assert status == "failed"


def test_completed_with_errors_marks_failed():
    report = {
        "status": "completed_with_errors",
        "unit_simulation": {"status": "error", "message": "iverilog missing"},
    }
    status, summary = _derive_run_status_from_verify_report(report)
    assert status == "failed"
    assert summary["report_status"] == "completed_with_errors"
