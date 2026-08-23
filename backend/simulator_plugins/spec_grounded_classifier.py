"""Spec/RTL/waveform evidence assembly for simulator failures."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


COMPILE_ROOT_CAUSES = {
    "llm_introduced_unknown_package",
    "llm_introduced_unknown_class",
    "missing_package_or_compile_order",
    "uvm_dependency_not_visible",
    "missing_type_or_compile_order",
    "cascade_parse_error",
}


@dataclass
class EvidenceBundle:
    cluster_id: str
    verdict: str
    confidence: float
    reason: str
    error: dict[str, Any]
    requirement: dict[str, Any] | None = None
    rtl_context: list[dict[str, Any]] = field(default_factory=list)
    waveform: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def build_evidence_bundles(
    *,
    clusters: list[dict[str, Any]],
    mental_model: dict[str, Any] | None = None,
    rtl_root: str | Path | None = None,
    waveform_snapshot: dict[str, str] | None = None,
) -> list[EvidenceBundle]:
    model = mental_model or {}
    requirements = _requirements(model)
    bundles: list[EvidenceBundle] = []
    for cluster in clusters:
        first = cluster.get("first") or {}
        requirement = _match_requirement(first, requirements)
        rtl_context = _read_rtl_context(requirement, rtl_root) if requirement and rtl_root else []
        verdict, confidence, reason = _classify(first, requirement, waveform_snapshot or {})
        bundles.append(
            EvidenceBundle(
                cluster_id=str(cluster.get("id") or f"cluster-{len(bundles) + 1}"),
                verdict=verdict,
                confidence=confidence,
                reason=reason,
                error=first,
                requirement=requirement,
                rtl_context=rtl_context,
                waveform=waveform_snapshot or {},
            )
        )
    return bundles


def _classify(error: dict[str, Any], requirement: dict[str, Any] | None, waveform: dict[str, str]) -> tuple[str, float, str]:
    root_cause = str(error.get("root_cause") or "")
    phase = str(error.get("phase") or "")
    message = str(error.get("message") or "")
    if root_cause in COMPILE_ROOT_CAUSES or phase == "compile":
        return "uvm_bug", 1.0, "Compile/package/type failures are in generated verification collateral."
    if any(value.lower() in {"x", "z"} or "x" in value.lower() or "z" in value.lower() for value in waveform.values()):
        return "dut_or_reset_bug", 0.85, "Waveform contains X/Z values at failure time; inspect reset/init behavior."
    if requirement:
        if re.search(r"\bmismatch|assert|violation|timeout\b", message, re.IGNORECASE):
            return "needs_spec_review", 0.75, "Failure maps to a requirement; compare spec, RTL, and waveform evidence."
        return "requirement_related", 0.65, "Diagnostic mentions signals/keywords from a captured requirement."
    return "ambiguous", 0.45, "No matching requirement was found; ask user or improve spec grounding."


def _requirements(model: dict[str, Any]) -> list[dict[str, Any]]:
    raw = model.get("requirements") or []
    return [item for item in raw if isinstance(item, dict)]


def _match_requirement(error: dict[str, Any], requirements: list[dict[str, Any]]) -> dict[str, Any] | None:
    haystack = " ".join(
        str(error.get(key) or "")
        for key in ("message", "symbol", "file", "raw")
    ).lower()
    best: tuple[int, dict[str, Any] | None] = (0, None)
    for req in requirements:
        text = " ".join(str(req.get(key) or "") for key in ("id", "text", "description", "category")).lower()
        words = {
            word
            for word in re.findall(r"[a-zA-Z_]\w+", text)
            if len(word) >= 4
        }
        score = sum(1 for word in words if word in haystack)
        if score > best[0]:
            best = (score, req)
    return best[1] if best[0] else None


def _read_rtl_context(requirement: dict[str, Any], rtl_root: str | Path) -> list[dict[str, Any]]:
    refs = requirement.get("rtl_refs") or requirement.get("source_refs") or []
    if isinstance(refs, dict):
        refs = [refs]
    root = Path(rtl_root)
    contexts: list[dict[str, Any]] = []
    for ref in refs[:4]:
        if not isinstance(ref, dict):
            continue
        file_name = str(ref.get("file") or ref.get("path") or "")
        if not file_name:
            continue
        line_no = int(ref.get("line") or ref.get("start_line") or 1)
        path = next((candidate for candidate in root.rglob(Path(file_name).name) if candidate.is_file()), None)
        if not path:
            continue
        lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
        start = max(1, line_no - 5)
        end = min(len(lines), line_no + 5)
        contexts.append(
            {
                "file": str(path),
                "line": line_no,
                "snippet": "\n".join(lines[start - 1 : end]),
            }
        )
    return contexts
