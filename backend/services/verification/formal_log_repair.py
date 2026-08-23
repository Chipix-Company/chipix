"""Formal log diagnosis to conservative patch proposals.

The repair policy is intentionally careful:
- generated SVA/bind collateral may be patched after user approval;
- RTL is never changed by this service;
- failing properties are treated as evidence, not auto-fixed RTL bugs.
"""

from __future__ import annotations

import difflib
import json
import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from sqlalchemy.orm import Session

from database.models import PatchProposal, Project, ProjectArtifact, User
from services.verification.formal_log_parser import FormalLogAnalysis, FormalLogDiagnostic


FORMAL_EXTENSIONS = {".sv", ".svh", ".sby", ".tcl"}


@dataclass
class FormalRepairCandidate:
    artifact: ProjectArtifact
    proposed_content: str
    reason: str
    diagnostics: list[dict[str, Any]]
    actions: list[str]

    @property
    def original_content(self) -> str:
        return Path(self.artifact.file_path).read_text(encoding="utf-8", errors="replace")

    @property
    def diff_text(self) -> str:
        return "".join(
            difflib.unified_diff(
                self.original_content.splitlines(keepends=True),
                self.proposed_content.splitlines(keepends=True),
                fromfile=f"a/{self.artifact.filename}",
                tofile=f"b/{self.artifact.filename}",
            )
        )


def build_formal_repair_candidates(
    *,
    analysis: FormalLogAnalysis,
    generated_artifacts: Iterable[ProjectArtifact],
) -> list[FormalRepairCandidate]:
    artifacts = list(generated_artifacts)
    by_file = _diagnostics_by_file(analysis.diagnostics)
    candidates: list[FormalRepairCandidate] = []

    for artifact in artifacts:
        path = Path(artifact.file_path or "")
        if not path.exists() or not path.is_file() or path.suffix.lower() not in FORMAL_EXTENSIONS:
            continue
        name = Path(artifact.filename or path.name).name
        file_diags = by_file.get(name, [])
        if not file_diags and not _global_formal_repair_relevant(analysis, path):
            continue
        original = path.read_text(encoding="utf-8", errors="replace")
        proposed, actions = _repair_generated_formal_text(original, path.name, file_diags, analysis)
        if proposed != original and _candidate_is_safe(original, proposed):
            candidates.append(
                FormalRepairCandidate(
                    artifact=artifact,
                    proposed_content=proposed,
                    reason=_repair_reason(file_diags, analysis),
                    diagnostics=[diag.to_dict() for diag in (file_diags or analysis.diagnostics[:8])],
                    actions=actions,
                )
            )
    return candidates


def persist_formal_repair_patch_proposals(
    db: Session,
    *,
    project: Project,
    user: User,
    candidates: Iterable[FormalRepairCandidate],
    log_artifact_id: str,
    analysis: FormalLogAnalysis,
) -> list[PatchProposal]:
    proposals: list[PatchProposal] = []
    for candidate in candidates:
        diff_text = candidate.diff_text
        if not diff_text.strip():
            continue
        proposal = PatchProposal(
            id=str(uuid.uuid4()),
            organization_id=project.organization_id,
            project_id=project.id,
            user_id=user.id,
            target_artifact_id=candidate.artifact.id,
            source_agent="formal_log_repair",
            title=f"Repair generated formal collateral in {candidate.artifact.filename}",
            reason=candidate.reason,
            diff_text=diff_text,
            status="awaiting_approval",
            metadata_json=json.dumps(
                {
                    "kind": "formal_log_repair",
                    "log_artifact_id": log_artifact_id,
                    "analysis_summary": analysis.summary,
                    "root_causes": [cause.to_dict() for cause in analysis.root_causes],
                    "actions": candidate.actions,
                    "files": [
                        {
                            "artifact_id": candidate.artifact.id,
                            "filename": candidate.artifact.filename,
                            "file_path": candidate.artifact.file_path,
                            "original_checksum": candidate.artifact.checksum_sha256,
                            "proposed_content": candidate.proposed_content,
                            "diagnostics": candidate.diagnostics,
                        }
                    ],
                },
                default=str,
            ),
        )
        db.add(proposal)
        proposals.append(proposal)
    if proposals:
        db.commit()
        for proposal in proposals:
            db.refresh(proposal)
    return proposals


def _diagnostics_by_file(diagnostics: list[FormalLogDiagnostic]) -> dict[str, list[FormalLogDiagnostic]]:
    by_file: dict[str, list[FormalLogDiagnostic]] = {}
    for diag in diagnostics:
        name = Path(diag.file or "").name
        if name:
            by_file.setdefault(name, []).append(diag)
    return by_file


def _global_formal_repair_relevant(analysis: FormalLogAnalysis, path: Path) -> bool:
    if path.suffix.lower() not in {".sv", ".svh"}:
        return False
    return any(
        diag.root_cause in {"ungrounded_formal_reference", "sva_compile_error"}
        for diag in analysis.diagnostics
    )


def _repair_generated_formal_text(
    text: str,
    filename: str,
    file_diags: list[FormalLogDiagnostic],
    analysis: FormalLogAnalysis,
) -> tuple[str, list[str]]:
    actions: list[str] = []
    repaired = text

    if "```" in repaired:
        repaired = re.sub(r"```(?:systemverilog|verilog|sv)?\s*", "", repaired, flags=re.IGNORECASE)
        repaired = repaired.replace("```", "")
        actions.append("Removed markdown fences from generated formal source.")

    if Path(filename).suffix.lower() in {".sv", ".svh"}:
        symbols = sorted(
            {
                diag.symbol
                for diag in (file_diags or analysis.diagnostics)
                if diag.root_cause == "ungrounded_formal_reference" and diag.symbol
            }
        )
        for symbol in symbols:
            repaired, changed = _disable_formal_statements_with_symbol(repaired, symbol)
            if changed:
                actions.append(f"Disabled generated formal statement that referenced unknown symbol {symbol}.")

    return repaired, actions


def _disable_formal_statements_with_symbol(text: str, symbol: str) -> tuple[str, bool]:
    lines = text.splitlines()
    changed = False
    repaired: list[str] = []
    skip_else = False
    for line in lines:
        stripped = line.strip()
        if skip_else and stripped.startswith("else "):
            repaired.append("// FORMAL_REPAIR_DISABLED: " + line)
            changed = True
            skip_else = False
            continue
        skip_else = False
        if (
            symbol
            and re.search(rf"\b{re.escape(symbol)}\b", line)
            and re.search(r"\b(assert|assume|cover)\s+property\b", line)
        ):
            repaired.append(f"  // FORMAL_REPAIR_DISABLED: unknown symbol {symbol}")
            repaired.append("// FORMAL_REPAIR_DISABLED: " + line)
            changed = True
            skip_else = "assert property" in line
        else:
            repaired.append(line)
    new_text = "\n".join(repaired)
    if text.endswith("\n"):
        new_text += "\n"
    return new_text, changed


def _candidate_is_safe(original: str, proposed: str) -> bool:
    if "```" in proposed:
        return False
    if proposed.count("module ") != original.count("module "):
        return False
    if proposed.count("endmodule") != original.count("endmodule"):
        return False
    return True


def _repair_reason(file_diags: list[FormalLogDiagnostic], analysis: FormalLogAnalysis) -> str:
    source = file_diags or analysis.diagnostics
    causes = sorted({diag.root_cause for diag in source if diag.root_cause})
    symbols = sorted({diag.symbol for diag in source if diag.symbol})
    cause_text = ", ".join(causes[:3]) or "formal compile/proof issue"
    symbol_text = f" ({', '.join(symbols[:4])})" if symbols else ""
    return (
        f"External formal log reported {cause_text}{symbol_text}; this patch only adjusts "
        "generated formal collateral and leaves RTL unchanged."
    )
