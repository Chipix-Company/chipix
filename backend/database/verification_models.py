"""
Verification Models — Database tables for UnitSim/Formal/UVM agent results.

Tables:
    verification_runs  — A single verification run
    tool_executions    — Tool calls within a run
    coverage_results   — Coverage metrics
    debug_findings     — Bugs found during verification
"""

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from database.database import Base


def _uuid() -> str:
    return str(uuid.uuid4())


class VerificationRun(Base):
    """A single UnitSim/Formal/UVM verification run."""
    __tablename__ = "verification_runs"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    project_id: Mapped[str] = mapped_column(
        String, ForeignKey("projects.id"), nullable=False, index=True
    )
    run_type: Mapped[str] = mapped_column(
        String, nullable=False, index=True
    )  # "unitsim" | "formal" | "uvm"
    target_module: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(
        String, nullable=False, default="running"
    )  # "running" | "passed" | "failed" | "error"
    tests_passed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    tests_failed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    assertion_failures: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    sim_log: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    result_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    mental_model_revision: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )


class ToolExecution(Base):
    """Individual tool call execution within a verification run."""
    __tablename__ = "tool_executions"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    verification_run_id: Mapped[str] = mapped_column(
        String, ForeignKey("verification_runs.id"), nullable=False, index=True
    )
    tool_name: Mapped[str] = mapped_column(String, nullable=False, index=True)
    args_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    result_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String, nullable=False, default="running")
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class CoverageResult(Base):
    """Coverage metrics from a verification run."""
    __tablename__ = "coverage_results"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    verification_run_id: Mapped[str] = mapped_column(
        String, ForeignKey("verification_runs.id"), nullable=False, index=True
    )
    coverage_type: Mapped[str] = mapped_column(
        String, nullable=False
    )  # "line" | "branch" | "toggle" | "functional"
    percentage: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    details_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class DebugFinding(Base):
    """A bug or issue found during verification."""
    __tablename__ = "debug_findings"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    project_id: Mapped[str] = mapped_column(
        String, ForeignKey("projects.id"), nullable=False, index=True
    )
    verification_run_id: Mapped[Optional[str]] = mapped_column(
        String, ForeignKey("verification_runs.id"), nullable=True, index=True
    )
    classification: Mapped[str] = mapped_column(
        String, nullable=False, index=True
    )  # "syntax" | "timing" | "rtl_bug" | "testbench_bug"
    severity: Mapped[str] = mapped_column(
        String, nullable=False, default="medium"
    )  # "critical" | "high" | "medium" | "low"
    title: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    file_path: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    line_number: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    fix_suggestion: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    patch_id: Mapped[Optional[str]] = mapped_column(
        String, ForeignKey("patch_proposals.id"), nullable=True, index=True
    )
    status: Mapped[str] = mapped_column(
        String, nullable=False, default="open"
    )  # "open" | "fixed" | "wontfix" | "duplicate"
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
