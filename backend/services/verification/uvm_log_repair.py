"""UVM log diagnosis to safe patch proposals.

This module converts normalized UVM log diagnostics into conservative edits for
generated UVM artifacts. It intentionally targets generated verification
collateral only; original RTL remains untouched unless a future flow adds an
explicit RTL-fix approval mode.
"""

from __future__ import annotations

import difflib
import json
import re
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, List

from sqlalchemy.orm import Session

from database.models import PatchProposal, Project, ProjectArtifact, User
from services.verification.uvm_log_parser import UVMLogAnalysis, UVMLogDiagnostic


SV_EXTENSIONS = {".sv", ".svh", ".v", ".vh"}


@dataclass
class GeneratedUVMRegistry:
    packages: set[str] = field(default_factory=set)
    types: set[str] = field(default_factory=set)
    package_files: dict[str, ProjectArtifact] = field(default_factory=dict)
    files_by_name: dict[str, list[ProjectArtifact]] = field(default_factory=dict)

    @property
    def top_package(self) -> str:
        candidates = sorted(pkg for pkg in self.packages if pkg != "uvm_pkg")
        return candidates[0] if candidates else ""


@dataclass
class RepairCandidate:
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


def build_generated_uvm_registry(artifacts: Iterable[ProjectArtifact]) -> GeneratedUVMRegistry:
    registry = GeneratedUVMRegistry(packages={"uvm_pkg"})
    for artifact in artifacts:
        path = Path(artifact.file_path or "")
        registry.files_by_name.setdefault(Path(artifact.filename or path.name).name, []).append(artifact)
        if path.suffix.lower() not in SV_EXTENSIONS or not path.exists() or not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for kind, name in _sv_declaration_names(text):
            if kind == "package":
                registry.packages.add(name)
                registry.package_files[name] = artifact
            else:
                registry.types.add(name)
    return registry


def build_uvm_repair_candidates(
    *,
    analysis: UVMLogAnalysis,
    generated_artifacts: Iterable[ProjectArtifact],
) -> list[RepairCandidate]:
    artifacts = list(generated_artifacts)
    registry = build_generated_uvm_registry(artifacts)
    candidates: list[RepairCandidate] = []
    by_file = _diagnostics_by_file(analysis.diagnostics)

    for artifact in artifacts:
        path = Path(artifact.file_path or "")
        if not path.exists() or not path.is_file() or path.suffix.lower() not in SV_EXTENSIONS | {".f", ".flist"}:
            continue
        name = Path(artifact.filename or path.name).name
        file_diags = by_file.get(name, [])
        if not file_diags and not _file_may_need_global_repair(path, analysis):
            continue
        original = path.read_text(encoding="utf-8", errors="replace")
        proposed, actions = _repair_generated_uvm_text(original, path.name, file_diags, analysis, registry)
        if proposed != original and _candidate_is_safe(proposed, registry):
            candidates.append(
                RepairCandidate(
                    artifact=artifact,
                    proposed_content=proposed,
                    reason=_repair_reason(file_diags, analysis),
                    diagnostics=[diag.to_dict() for diag in (file_diags or analysis.diagnostics[:8])],
                    actions=actions,
                )
            )

    return candidates


def persist_repair_patch_proposals(
    db: Session,
    *,
    project: Project,
    user: User,
    candidates: Iterable[RepairCandidate],
    log_artifact_id: str,
    analysis: UVMLogAnalysis,
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
            source_agent="uvm_log_repair",
            title=f"Fix UVM compile issue in {candidate.artifact.filename}",
            reason=candidate.reason,
            diff_text=diff_text,
            status="awaiting_approval",
            metadata_json=json.dumps(
                {
                    "kind": "uvm_log_repair",
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


def _diagnostics_by_file(diagnostics: list[UVMLogDiagnostic]) -> dict[str, list[UVMLogDiagnostic]]:
    by_file: dict[str, list[UVMLogDiagnostic]] = {}
    for diag in diagnostics:
        name = Path(diag.file or "").name
        if name:
            by_file.setdefault(name, []).append(diag)
    return by_file


def _file_may_need_global_repair(path: Path, analysis: UVMLogAnalysis) -> bool:
    name = path.name.lower()
    has_package_order_issue = any(
        diag.root_cause == "missing_package_or_compile_order"
        for diag in analysis.diagnostics
    )
    return has_package_order_issue and name.endswith((".f", ".flist"))


def _repair_generated_uvm_text(
    text: str,
    filename: str,
    file_diags: list[UVMLogDiagnostic],
    analysis: UVMLogAnalysis,
    registry: GeneratedUVMRegistry,
) -> tuple[str, list[str]]:
    actions: list[str] = []
    repaired = text
    suffix = Path(filename).suffix.lower()

    if suffix in {".f", ".flist"}:
        repaired, changed = _repair_filelist_order(repaired, registry)
        if changed:
            actions.append("Reordered generated package before files that import it.")
        return repaired, actions

    unknown_packages = {
        diag.symbol
        for diag in file_diags
        if diag.root_cause in {"llm_introduced_unknown_package", "missing_package_or_compile_order"}
        and diag.symbol
        and diag.symbol not in registry.packages
        and diag.symbol != "uvm_pkg"
    }
    for package in sorted(unknown_packages):
        new_text = re.sub(
            rf"(?m)^\s*import\s+{re.escape(package)}::(?:\*|[a-zA-Z_]\w*)\s*;\s*\n?",
            "",
            repaired,
        )
        if new_text != repaired:
            repaired = new_text
            actions.append(f"Removed import of non-generated package {package}.")

    repaired, role_actions = _repair_role_qualified_sequencers(repaired, registry)
    actions.extend(role_actions)

    repaired, config_actions = _remove_unknown_agent_config_handles(repaired, registry)
    actions.extend(config_actions)

    if _needs_uvm_import(repaired):
        prefix = ""
        if "import uvm_pkg::*;" not in repaired:
            prefix += "import uvm_pkg::*;\n"
        if '`include "uvm_macros.svh"' not in repaired:
            prefix += '`include "uvm_macros.svh"\n'
        if prefix:
            repaired = prefix + repaired.lstrip()
            actions.append("Added UVM package/macro visibility.")

    return repaired, actions


def _repair_filelist_order(text: str, registry: GeneratedUVMRegistry) -> tuple[str, bool]:
    top_package = registry.top_package
    package_artifact = registry.package_files.get(top_package) if top_package else None
    if not package_artifact:
        return text, False
    package_name = Path(package_artifact.filename or "").name
    if not package_name:
        return text, False

    lines = text.splitlines()
    package_lines = [line for line in lines if Path(_filelist_token(line)).name == package_name]
    if not package_lines:
        package_lines = [package_artifact.file_path]
    remaining = [line for line in lines if Path(_filelist_token(line)).name != package_name]

    insert_at = 0
    for idx, line in enumerate(remaining):
        name = Path(_filelist_token(line)).name.lower()
        if name.endswith("_pkg.sv"):
            insert_at = idx + 1
        if name in {"top_tb.sv", "testbench.sv"}:
            insert_at = idx
            break

    reordered = remaining[:insert_at] + package_lines + remaining[insert_at:]
    new_text = "\n".join(reordered).rstrip() + "\n"
    return new_text, new_text != text


def _filelist_token(line: str) -> str:
    stripped = str(line or "").strip()
    if not stripped or stripped.startswith("+") or stripped.startswith("//"):
        return stripped
    return stripped.split()[0]


def _repair_role_qualified_sequencers(text: str, registry: GeneratedUVMRegistry) -> tuple[str, list[str]]:
    actions: list[str] = []

    def repl(match: re.Match[str]) -> str:
        base = match.group("base")
        canonical = f"{base}_sequencer"
        if canonical in registry.types:
            old = match.group(0)
            if old != canonical:
                actions.append(f"Replaced non-generated sequencer alias {old} with {canonical}.")
            return canonical
        return match.group(0)

    repaired = re.sub(
        r"\b(?P<base>[a-zA-Z_]\w*)_(?:master|slave|active|passive)_sequencer\b",
        repl,
        text,
    )
    return repaired, actions


def _remove_unknown_agent_config_handles(text: str, registry: GeneratedUVMRegistry) -> tuple[str, list[str]]:
    actions: list[str] = []
    repaired = text
    unknown_configs = sorted(
        set(re.findall(r"\b([a-zA-Z_]\w*_agent_config)\b", repaired)) - registry.types
    )
    for config_type in unknown_configs:
        before = repaired
        repaired = re.sub(
            rf"(?ms)^\s*if\s*\(!\s*uvm_config_db\s*#\s*\(\s*{re.escape(config_type)}\s*\)"
            rf"\s*::\s*get\s*\([^)]*\)\s*\)\s*begin\s*.*?^\s*end\s*\n?",
            "",
            repaired,
        )
        repaired = re.sub(
            rf"(?m)^\s*{re.escape(config_type)}\s+[a-zA-Z_]\w*\s*;\s*\n?",
            "",
            repaired,
        )
        if repaired != before:
            actions.append(f"Removed handle/get block for non-generated config class {config_type}.")
    return repaired, actions


def _needs_uvm_import(text: str) -> bool:
    if "uvm_pkg::*" in text:
        return False
    return bool(re.search(r"\bextends\s+uvm_|\buvm_[a-zA-Z_]\w*\s*#?\s*(?:\(|;)|`uvm_", text))


def _candidate_is_safe(content: str, registry: GeneratedUVMRegistry) -> bool:
    if "```" in content:
        return False
    stripped = _strip_comments_and_strings(content)
    for package in re.findall(r"\bimport\s+([a-zA-Z_]\w*)::", stripped):
        if package not in registry.packages and package != "uvm_pkg":
            return False
    unknown_types = _unknown_generated_types(stripped, registry)
    return not unknown_types


def _unknown_generated_types(content: str, registry: GeneratedUVMRegistry) -> list[str]:
    suffixes = (
        "_agent_config",
        "_env_config",
        "_virtual_sequencer",
        "_sequence_item",
        "_sequencer",
        "_scoreboard",
        "_coverage",
        "_base_vseq",
        "_base_test",
        "_sequence",
        "_driver",
        "_monitor",
        "_agent",
        "_env",
    )
    candidates: set[str] = set()
    for pattern in (
        r"\bextends\s+([a-zA-Z_]\w*)\b",
        r"#\s*\(\s*([a-zA-Z_]\w*)\s*\)",
        r"\b([a-zA-Z_]\w*)::(?:type_id|create|get_type)\b",
        r"(?m)^\s*(?:(?:automatic|static|rand|local|protected|virtual)\s+)*([a-zA-Z_]\w*)\s+[a-zA-Z_]\w*\s*(?:;|=|,)",
    ):
        candidates.update(re.findall(pattern, content))
    return sorted(
        token
        for token in candidates
        if token.endswith(suffixes)
        and not token.startswith("uvm_")
        and token not in registry.types
    )


def _repair_reason(file_diags: list[UVMLogDiagnostic], analysis: UVMLogAnalysis) -> str:
    source = file_diags or analysis.diagnostics
    causes = sorted({diag.root_cause for diag in source if diag.root_cause})
    symbols = sorted({diag.symbol for diag in source if diag.symbol})
    cause_text = ", ".join(causes[:3]) or "UVM compile issue"
    symbol_text = f" ({', '.join(symbols[:4])})" if symbols else ""
    return f"External simulator log reported {cause_text}{symbol_text}; this patch aligns the generated file with the emitted UVM package/type contract."


def _sv_declaration_names(text: str) -> list[tuple[str, str]]:
    stripped = _strip_comments_and_strings(text)
    names: list[tuple[str, str]] = []
    for match in re.finditer(r"\b(class|module|interface|package)\s+([a-zA-Z_]\w*)", stripped):
        prefix = stripped[max(0, match.start() - 32):match.start()].lower()
        if re.search(r"\btypedef\s+$", prefix):
            continue
        names.append((match.group(1), match.group(2)))
    return names


def _strip_comments_and_strings(content: str) -> str:
    text = re.sub(r"/\*[\s\S]*?\*/", " ", content or "")
    text = re.sub(r"//.*", " ", text)
    text = re.sub(r'"(?:\\.|[^"\\])*"', '""', text)
    return text
