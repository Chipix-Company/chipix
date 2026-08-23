"""Tests for Cadence agent tools."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent_tools.cadence_tools import (
    handle_apply_cadence_fixes,
    handle_check_cadence_status,
    handle_get_cadence_run_status,
    handle_run_cadence_simulation,
)


@pytest.mark.asyncio
async def test_check_cadence_status_ready():
    detection = MagicMock()
    detection.available = True
    detection.path = "/opt/xrun"
    detection.guidance = ""
    detection.to_dict.return_value = {"details": {"status": "ready", "license_ready": True}}

    with patch("simulator_plugins.xcelium.XceliumPlugin") as plugin_cls:
        plugin_cls.return_value.detect.return_value = detection
        with patch("simulator_plugins.cadence_config.load_cadence_config", return_value={}):
            raw = await handle_check_cadence_status()
    data = json.loads(raw)
    assert data["available"] is True
    assert data["cadence_connected"] is True


@pytest.mark.asyncio
async def test_run_cadence_simulation_missing_project():
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = None
    raw = await handle_run_cadence_simulation("missing", db_session=db)
    data = json.loads(raw)
    assert data["status"] == "error"


@pytest.mark.asyncio
async def test_run_cadence_simulation_delegates_to_service():
    project = MagicMock()
    project.id = "proj-1"
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = project

    service_result = {
        "status": "passed",
        "run_id": "run-abc",
        "cadence_connected": True,
        "phases": ["detect", "compile", "simulate"],
        "analysis": {},
        "feedback_memory": {},
        "repair_history": [],
        "closure_report": {},
        "generated_artifact_ids": ["art-1"],
        "next_step": "ok",
    }

    with patch(
        "services.verification.cadence_run.run_cadence_on_artifacts",
        new_callable=AsyncMock,
        return_value=service_result,
    ):
        raw = await handle_run_cadence_simulation(
            "proj-1",
            db_session=db,
            generated_artifact_ids=["art-1"],
        )
    data = json.loads(raw)
    assert data["status"] == "passed"
    assert data["run_id"] == "run-abc"


@pytest.mark.asyncio
async def test_get_cadence_run_status_not_found():
    project = MagicMock()
    project.id = "proj-1"
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = project

    with patch(
        "services.verification.cadence_run.load_cadence_run",
        side_effect=FileNotFoundError("missing"),
    ):
        raw = await handle_get_cadence_run_status("proj-1", db_session=db, run_id="run-x")
    data = json.loads(raw)
    assert data["status"] == "error"


@pytest.mark.asyncio
async def test_apply_cadence_fixes_no_matching_artifacts(tmp_path):
    project = MagicMock()
    project.id = "proj-1"
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = project

    artifact = MagicMock()
    artifact.id = "a1"
    artifact.filename = "top_tb.sv"
    artifact.file_path = str(tmp_path / "top_tb.sv")
    (tmp_path / "top_tb.sv").write_text("old", encoding="utf-8")

    with patch(
        "services.verification.cadence_run.resolve_generated_artifacts",
        return_value=[artifact],
    ):
        raw = await handle_apply_cadence_fixes(
            "proj-1",
            db_session=db,
            fixes=[{
                "filename": "top_tb.sv",
                "content": "module top_tb; endmodule",
            }],
        )
    data = json.loads(raw)
    assert data["applied_count"] == 1
    assert "module top_tb" in (tmp_path / "top_tb.sv").read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_run_cadence_simulation_missing_session_project():
    raw = await handle_run_cadence_simulation(project_id=None, db_session=MagicMock())
    data = json.loads(raw)
    assert data["status"] == "error"
    assert "No active project" in data["error"]
