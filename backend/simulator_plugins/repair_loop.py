"""Generated-collateral repair loop for simulator failures.

This module is deliberately scoped to files inside the simulator run sandbox.
It never edits original RTL. The repair logic reuses the existing deterministic
UVM log repair engine, applies safe generated-file fixes, and records every
change as feedback memory for the next agent step.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from services.verification.uvm_log_parser import UVMLogAnalysis
from services.verification.uvm_log_repair import build_uvm_repair_candidates


REPAIRABLE_EXTENSIONS = {".sv", ".svh", ".v", ".vh", ".f", ".flist"}


@dataclass
class RepairRound:
    round_index: int
    status: str
    diagnostics: int = 0
    repairs: list[dict[str, Any]] = field(default_factory=list)
    reason: str = ""

    @property
    def changed_files(self) -> int:
        return len({item.get("file") for item in self.repairs if item.get("file")})

    def to_dict(self) -> dict[str, Any]:
        return {
            "round": self.round_index,
            "status": self.status,
            "diagnostics": self.diagnostics,
            "changed_files": self.changed_files,
            "repairs": self.repairs,
            "reason": self.reason,
        }


def apply_generated_repairs(
    *,
    analysis: UVMLogAnalysis,
    run_dir: str | Path,
    round_index: int,
) -> RepairRound:
    """Apply safe repairs to generated files in a simulator run sandbox."""

    run_root = Path(run_dir).resolve()
    artifacts = _sandbox_generated_artifacts(run_root)
    if not artifacts:
        return RepairRound(
            round_index=round_index,
            status="skipped",
            diagnostics=len(analysis.diagnostics),
            reason="No generated simulator files were available for repair.",
        )

    candidates = build_uvm_repair_candidates(
        analysis=analysis,
        generated_artifacts=artifacts,
    )
    repairs: list[dict[str, Any]] = []
    for candidate in candidates:
        target = Path(candidate.artifact.file_path).resolve()
        if not _inside_run_dir(target, run_root):
            continue
        original = target.read_text(encoding="utf-8", errors="replace")
        target.write_text(candidate.proposed_content, encoding="utf-8")
        repairs.append(
            {
                "file": str(target),
                "filename": Path(candidate.artifact.filename).name,
                "reason": candidate.reason,
                "actions": candidate.actions,
                "diagnostics": candidate.diagnostics[:8],
                "original_size": len(original),
                "proposed_size": len(candidate.proposed_content),
                "diff": candidate.diff_text,
            }
        )

    status = "repaired" if repairs else "no_safe_repair"
    reason = "" if repairs else "No generated-file repair candidate passed safety checks."
    repair_round = RepairRound(
        round_index=round_index,
        status=status,
        diagnostics=len(analysis.diagnostics),
        repairs=repairs,
        reason=reason,
    )
    _persist_repair_round(run_root, repair_round)
    return repair_round


def build_feedback_memory(
    *,
    analysis: dict[str, Any],
    evidence: list[dict[str, Any]],
    repair_history: list[dict[str, Any]],
) -> dict[str, Any]:
    """Create compact memory for future agent/tool calls."""

    root_causes = analysis.get("root_causes") or []
    repaired_files = []
    for round_item in repair_history:
        for repair in round_item.get("repairs") or []:
            repaired_files.append(
                {
                    "file": repair.get("filename") or repair.get("file"),
                    "actions": repair.get("actions") or [],
                    "reason": repair.get("reason") or "",
                }
            )
    return {
        "latest_status": analysis.get("status") or "unknown",
        "summary": analysis.get("summary") or "",
        "root_causes": root_causes[:8],
        "repair_rounds": len(repair_history),
        "repaired_files": repaired_files[:20],
        "evidence_verdicts": [
            {
                "cluster_id": item.get("cluster_id"),
                "verdict": item.get("verdict"),
                "confidence": item.get("confidence"),
                "reason": item.get("reason"),
            }
            for item in evidence[:20]
        ],
    }


def _sandbox_generated_artifacts(run_root: Path) -> list[Any]:
    generated_dir = run_root / "generated"
    files: list[Path] = []
    if generated_dir.exists():
        files.extend(
            path
            for path in generated_dir.rglob("*")
            if path.is_file() and path.suffix.lower() in REPAIRABLE_EXTENSIONS
        )
    for extra in (generated_dir / "xcelium_filelist.f", run_root / "filelist.f"):
        if extra.exists() and extra not in files:
            files.append(extra)

    artifacts = []
    for index, path in enumerate(files, start=1):
        artifacts.append(
            SimpleNamespace(
                id=f"sandbox-generated-{index}",
                file_path=str(path),
                filename=path.name,
                artifact_type="generated",
            )
        )
    return artifacts


def _inside_run_dir(path: Path, run_root: Path) -> bool:
    try:
        path.relative_to(run_root)
        return True
    except ValueError:
        return False


def _persist_repair_round(run_root: Path, repair_round: RepairRound) -> None:
    results_dir = run_root / "results"
    results_dir.mkdir(parents=True, exist_ok=True)
    path = results_dir / f"repair_round_{repair_round.round_index}.json"
    path.write_text(json.dumps(repair_round.to_dict(), indent=2), encoding="utf-8")
