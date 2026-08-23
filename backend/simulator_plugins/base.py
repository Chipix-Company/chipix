"""Common simulator plugin data structures."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class ToolDetectionResult:
    name: str
    available: bool
    path: str = ""
    version: str = ""
    license_env: str = ""
    uvm_available: bool = False
    platform_supported: bool = True
    guidance: str = ""
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class SimulatorCommandResult:
    phase: str
    command: list[str]
    returncode: int
    log_path: str
    stdout: str = ""
    stderr: str = ""
    timed_out: bool = False

    @property
    def passed(self) -> bool:
        return self.returncode == 0 and not self.timed_out

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class SimulatorRunRequest:
    run_dir: Path
    filelist: Path
    top_module: str = "top_tb"
    uvm_testname: str = ""
    timeout_seconds: int = 300
    coverage: bool = True
    waveform: bool = True
    dry_run: bool = False
    mock_logs: dict[str, str] = field(default_factory=dict)
    auto_repair_generated: bool = True
    max_repair_rounds: int = 3
    regression_tests: list[dict[str, Any]] = field(default_factory=list)
    replay_failures: bool = True
    seed_namespace: str = ""
    coverage_targets: dict[str, float] = field(default_factory=dict)
    enforce_closure: bool = False
    static_validation: dict[str, Any] = field(default_factory=dict)
    rtl_checksums: dict[str, str] = field(default_factory=dict)
    spec_pages: list[dict[str, Any]] = field(default_factory=list)
    spec_metadata: dict[str, Any] = field(default_factory=dict)
    project_id: str = ""
    run_id: str = ""
    emit_events: bool = True


@dataclass
class SimulatorRunResult:
    simulator: str
    run_id: str
    status: str
    run_dir: str
    commands: list[SimulatorCommandResult] = field(default_factory=list)
    logs: dict[str, str] = field(default_factory=dict)
    analysis: dict[str, Any] = field(default_factory=dict)
    evidence: list[dict[str, Any]] = field(default_factory=list)
    coverage: dict[str, Any] = field(default_factory=dict)
    closure_report: dict[str, Any] = field(default_factory=dict)
    repair_history: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    regression: dict[str, Any] = field(default_factory=dict)
    integrity: dict[str, Any] = field(default_factory=dict)
    traceability: dict[str, Any] = field(default_factory=dict)
    findings: list[dict[str, Any]] = field(default_factory=list)
    report: dict[str, Any] = field(default_factory=dict)
    environment: dict[str, Any] = field(default_factory=dict)
    artifact_paths: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["commands"] = [command.to_dict() for command in self.commands]
        return data
