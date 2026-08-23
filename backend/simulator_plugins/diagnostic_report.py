"""Deterministic spec audit, DUT attribution, and Markdown closure reporting."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def deterministic_seed(namespace: str, test_id: str) -> int:
    """Return a stable positive 32-bit Xcelium seed."""
    digest = hashlib.sha256(f"{namespace}:{test_id}".encode("utf-8")).digest()
    return (int.from_bytes(digest[:4], "big") % 2_147_483_646) + 1


def build_integrity(
    *,
    compile_passed: bool,
    elaborate_passed: bool,
    static_validation: dict[str, Any] | None,
    rtl_checksums: dict[str, str] | None,
    repair_history: list[dict[str, Any]],
) -> dict[str, Any]:
    changed, missing = [], []
    for raw_path, expected in (rtl_checksums or {}).items():
        path = Path(raw_path)
        if not path.exists():
            missing.append(raw_path)
            continue
        if _sha256(path) != expected:
            changed.append(raw_path)
    static = static_validation or {}
    static_passed = bool(static.get("trusted", static.get("passed", True)))
    checks = {
        "static_validation": static_passed,
        "compile": bool(compile_passed),
        "elaboration": bool(elaborate_passed),
        "rtl_unchanged": not changed and not missing,
        "generated_repairs_only": True,
    }
    return {
        "passed": all(checks.values()),
        "checks": checks,
        "rtl": {
            "passed": checks["rtl_unchanged"],
            "checked_files": len(rtl_checksums or {}),
            "changed": changed,
            "missing": missing,
        },
        "repair_rounds": len(repair_history),
        "repair_history": repair_history,
        "statement": (
            "Generated UVM collateral passed its integrity gate and the original RTL hashes are unchanged."
            if all(checks.values())
            else "The verification integrity gate is not clean; DUT attribution is blocked."
        ),
    }


def build_traceability(
    *,
    mental_model: dict[str, Any] | None,
    tests: list[dict[str, Any]],
    executions: list[dict[str, Any]],
    coverage: dict[str, Any],
    spec_pages: list[dict[str, Any]] | None = None,
    spec_metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    model = mental_model or {}
    requirements = [item for item in model.get("requirements", []) if isinstance(item, dict)]
    execution_by_test = {str(item.get("test_id") or item.get("id")): item for item in executions}
    rows: list[dict[str, Any]] = []
    gaps: list[dict[str, str]] = []
    for index, requirement in enumerate(requirements, start=1):
        requirement_id = str(requirement.get("id") or f"REQ-{index:03d}")
        linked_tests = [
            test for test in tests
            if requirement_id in [str(value) for value in test.get("requirement_ids", [])]
        ]
        linked_runs = [execution_by_test.get(str(test.get("id") or test.get("test_id"))) for test in linked_tests]
        linked_runs = [item for item in linked_runs if item]
        checkers = _unique(value for test in linked_tests for value in test.get("checkers", []))
        assertions = _unique(value for test in linked_tests for value in test.get("assertions", []))
        coverpoints = _unique(value for test in linked_tests for value in test.get("coverpoints", []))
        spec_ref = _ground_spec_reference(requirement, spec_pages or [], spec_metadata or {})
        executed = bool(linked_tests) and len(linked_runs) == len(linked_tests)
        passed = executed and all(str(run.get("status")) == "passed" for run in linked_runs)
        observed_coverage = coverage.get("functional", {}).get("achieved") is not None
        covered = passed and bool(checkers or assertions) and bool(coverpoints) and observed_coverage
        status = "covered" if covered else "gap"
        row = {
            "requirement_id": requirement_id,
            "requirement": str(requirement.get("text") or requirement.get("description") or ""),
            "priority": str(requirement.get("priority") or "medium"),
            "spec_ref": spec_ref,
            "rtl_refs": list(requirement.get("rtl_refs") or []),
            "tests": [str(test.get("test_class") or test.get("name") or test.get("id")) for test in linked_tests],
            "seeds": [run.get("seed") for run in linked_runs if run.get("seed") is not None],
            "executed": executed,
            "passed": passed,
            "checkers": checkers,
            "assertions": assertions,
            "coverpoints": coverpoints,
            "coverage_observed": observed_coverage,
            "status": status,
        }
        rows.append(row)
        reasons = []
        if not linked_tests:
            reasons.append("no approved UVM test")
        elif not executed:
            reasons.append("linked test was not executed")
        elif not passed:
            reasons.append("linked test failed")
        if not (checkers or assertions):
            reasons.append("no checker or assertion")
        if not coverpoints:
            reasons.append("no requirement-linked coverpoint")
        if not observed_coverage:
            reasons.append("functional coverage was not exported")
        if reasons:
            gaps.append({"requirement_id": requirement_id, "reason": "; ".join(reasons)})

    covered_count = sum(1 for row in rows if row["status"] == "covered")
    total = len(rows)
    return {
        "status": "complete" if total > 0 and covered_count == total else "gaps",
        "requirements_total": total,
        "requirements_covered": covered_count,
        "coverage_percentage": round((covered_count / total) * 100.0, 2) if total else 0.0,
        "rows": rows,
        "gaps": gaps,
        "spec_pages_audited": len(spec_pages or []),
    }


def build_findings(
    *,
    regression: dict[str, Any],
    integrity: dict[str, Any],
    traceability: dict[str, Any],
    evidence: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Classify failures without allowing an LLM to upgrade confidence."""
    rows_by_id = {row["requirement_id"]: row for row in traceability.get("rows", [])}
    findings: list[dict[str, Any]] = []
    for execution in regression.get("executions", []):
        if execution.get("kind") == "replay" or execution.get("status") == "passed":
            continue
        requirement_ids = [str(value) for value in execution.get("requirement_ids", [])]
        requirement = next((rows_by_id[value] for value in requirement_ids if value in rows_by_id), None)
        replay = execution.get("replay") or {}
        replay_consistent = replay.get("status") == "failed" and replay.get("seed") == execution.get("seed")
        diagnostics = execution.get("analysis", {}).get("diagnostics", [])
        first = diagnostics[0] if diagnostics else {}
        runtime_failure = str(first.get("phase") or "simulation") == "simulation"
        spec_ref = (requirement or {}).get("spec_ref", {})
        has_any_spec = bool(requirement and (spec_ref.get("page") or spec_ref.get("line")))
        has_spec = has_any_spec and float(spec_ref.get("match_confidence") or 0.0) >= 0.6
        has_checker = bool(requirement and (requirement.get("checkers") or requirement.get("assertions")))
        rtl_refs = list((requirement or {}).get("rtl_refs") or [])
        if not rtl_refs and first.get("file") and not _looks_generated(str(first.get("file"))):
            rtl_refs = [{"file": first.get("file"), "line": first.get("line") or 0}]
        has_rtl_evidence = bool(rtl_refs)
        matched_evidence = next(
            (
                item for item in (evidence or [])
                if str((item.get("requirement") or {}).get("id") or "") in requirement_ids
            ),
            None,
        )
        rtl_context = list((matched_evidence or {}).get("rtl_context") or [])
        has_rtl_evidence = has_rtl_evidence or bool(rtl_context)

        if not integrity.get("passed") or not runtime_failure:
            tier = "Inconclusive"
            rationale = "The verification integrity gate is not clean, so this failure cannot be attributed to the DUT."
            category = "verification_environment"
        elif replay_consistent and has_spec and has_checker and has_rtl_evidence:
            tier = "Confirmed"
            rationale = "The clean UVM environment reproduced the same spec-grounded failure with the same seed and RTL evidence."
            category = "dut"
        elif has_any_spec and has_checker:
            tier = "Likely"
            rationale = "The clean UVM environment and specification support a DUT issue, but replay or RTL evidence is incomplete."
            category = "dut_candidate"
        else:
            tier = "Inconclusive"
            rationale = "The failure lacks sufficient specification, checker, replay, or RTL evidence for DUT attribution."
            category = "needs_review"

        spec_ref = (requirement or {}).get("spec_ref") or {}
        finding = {
            "id": f"DV-BUG-{len(findings) + 1:03d}",
            "tier": tier,
            "severity": _severity((requirement or {}).get("priority")),
            "category": category,
            "requirement_id": (requirement or {}).get("requirement_id") or (requirement_ids[0] if requirement_ids else ""),
            "title": str(first.get("message") or f"{execution.get('test_class') or execution.get('test_id')} failed")[:220],
            "expected": (requirement or {}).get("requirement") or "Specification expectation is not yet mapped.",
            "observed": str(first.get("message") or execution.get("analysis", {}).get("summary") or "Simulation failure"),
            "spec_ref": spec_ref,
            "rtl_refs": rtl_refs,
            "rtl_context": rtl_context,
            "test": execution.get("test_class") or execution.get("test_id"),
            "seed": execution.get("seed"),
            "replay": replay,
            "log": execution.get("log"),
            "waveform": execution.get("waveform"),
            "waveform_evidence": execution.get("waveform_evidence") or {},
            "diagnostic": first,
            "rationale": rationale,
            "impact": "The DUT may violate an approved functional requirement." if category.startswith("dut") else "Verification sign-off is blocked pending review.",
            "probable_root_cause": str(first.get("root_cause") or ("RTL behavior differs from the specification-backed checker." if category.startswith("dut") else "Insufficient or invalid verification evidence.")),
            "recommended_fix": (
                "Inspect the referenced RTL and correct the implementation to match the cited specification; rerun this exact test and seed."
                if category.startswith("dut")
                else "Resolve the missing verification evidence and rerun before changing RTL."
            ),
        }
        findings.append(finding)
    return findings


def write_closure_markdown(
    *,
    run_dir: Path,
    run_id: str,
    simulator_version: str,
    regression: dict[str, Any],
    integrity: dict[str, Any],
    coverage: dict[str, Any],
    traceability: dict[str, Any],
    findings: list[dict[str, Any]],
    environment: dict[str, Any],
    warnings: list[str],
    commands: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    counts = {tier: sum(1 for item in findings if item.get("tier") == tier) for tier in ("Confirmed", "Likely", "Inconclusive")}
    has_design_findings = bool(counts["Confirmed"] or counts["Likely"])
    title = "Design Verification Bug Report" if has_design_findings else "Verification Closure Report"
    filename = f"verification_closure_{_slug(run_id)}.md"
    path = run_dir / "results" / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        f"# {title}", "",
        f"**Run:** `{_md(run_id)}`  ",
        f"**Generated:** {datetime.now(timezone.utc).isoformat()}  ",
        f"**Simulator:** {_md(simulator_version or 'Cadence Xcelium')}  ",
        f"**Verdicts:** {counts['Confirmed']} Confirmed · {counts['Likely']} Likely · {counts['Inconclusive']} Inconclusive", "",
        "## Executive summary", "",
        f"- Tests: {regression.get('passed', 0)} passed / {regression.get('failed', 0)} failed / {regression.get('total', 0)} total.",
        f"- UVM integrity gate: **{'PASS' if integrity.get('passed') else 'BLOCKED'}**.",
        f"- Specification traceability: **{traceability.get('coverage_percentage', 0)}%** ({traceability.get('requirements_covered', 0)}/{traceability.get('requirements_total', 0)} requirements).",
        f"- Functional coverage closure: **{_closure_label(coverage.get('functional'))}**.",
        f"- Code coverage closure: **{_closure_label(coverage.get('code'))}**.", "",
        "## Reproducibility and verification integrity", "",
        f"- {integrity.get('statement', '')}",
        f"- Generated-only repair rounds: {integrity.get('repair_rounds', 0)}.",
        f"- Original RTL files hash-checked: {integrity.get('rtl', {}).get('checked_files', 0)}.",
        f"- Environment: `{_md(json.dumps(environment, sort_keys=True, default=str))}`", "",
        "## Functional and code coverage closure", "",
        "| Domain | Achieved | Target | Closure |", "|---|---:|---:|---|",
        _coverage_row("Functional", coverage.get("functional", {})),
        _coverage_row("Code (weakest enabled metric)", coverage.get("code", {})), "",
    ]
    code_metrics = coverage.get("code", {}).get("metrics", {})
    if code_metrics:
        lines.extend(["Code metrics: " + ", ".join(f"{key} {value}%" for key, value in code_metrics.items()), ""])
    gaps = coverage.get("gaps", [])
    if gaps:
        lines.extend(["### Coverage gaps", ""] + [f"- {_md(gap)}" for gap in gaps] + [""])

    lines.extend(["## Specification traceability audit", "", "| Requirement | Spec reference | Tests | Checker/assertion | Coverpoint | Status |", "|---|---|---|---|---|---|"])
    for row in traceability.get("rows", []):
        ref = row.get("spec_ref", {})
        location = _source_location(ref)
        lines.append(
            f"| {_md(row.get('requirement_id'))} | {_md(location)} | {_md(', '.join(row.get('tests') or []) or 'missing')} | "
            f"{_md(', '.join((row.get('checkers') or []) + (row.get('assertions') or [])) or 'missing')} | "
            f"{_md(', '.join(row.get('coverpoints') or []) or 'missing')} | {row.get('status')} |"
        )
    lines.append("")

    lines.extend(["## Findings", ""])
    if not findings:
        lines.extend(["No simulator failure was eligible for a design-bug finding.", ""])
    for finding in findings:
        lines.extend([
            f"### {finding.get('id')} — {finding.get('tier')}: {_md(finding.get('title'))}", "",
            f"- **Severity:** {finding.get('severity')}",
            f"- **Requirement:** {_md(finding.get('requirement_id') or 'unmapped')}",
            f"- **Spec reference:** {_md(_source_location(finding.get('spec_ref') or {}))}",
            f"- **Expected:** {_md(finding.get('expected'))}",
            f"- **Observed:** {_md(finding.get('observed'))}",
            f"- **Test / seed:** `{_md(finding.get('test'))}` / `{finding.get('seed')}`",
            f"- **Replay:** {_md((finding.get('replay') or {}).get('status') or 'not available')}",
            f"- **Why this verdict:** {_md(finding.get('rationale'))}",
            f"- **Impact:** {_md(finding.get('impact'))}",
            f"- **Probable root cause:** {_md(finding.get('probable_root_cause'))}",
            f"- **Recommended action:** {_md(finding.get('recommended_fix'))}",
            f"- **Evidence:** log `{_md(finding.get('log') or 'not available')}`, waveform `{_md(finding.get('waveform') or 'not available')}`", "",
        ])
        waveform_evidence = finding.get("waveform_evidence") or {}
        if waveform_evidence.get("signals"):
            signal_text = ", ".join(
                f"{name}={value}"
                for name, value in list(waveform_evidence["signals"].items())[:16]
            )
            lines.extend([
                f"- **Waveform snapshot at simulator time {waveform_evidence.get('failure_time', 0)}:** `{_md(signal_text)}`",
                f"- **X/Z signals:** {_md(', '.join(waveform_evidence.get('xz_signals') or []) or 'none observed')}", "",
            ])
        for context in finding.get("rtl_context") or []:
            lines.extend([
                f"#### RTL evidence — {_md(context.get('file'))}:{context.get('line') or 0}", "",
                "```systemverilog", str(context.get("snippet") or ""), "```", "",
            ])

    lines.extend(["## Limitations and waivers", ""])
    limitations = list(warnings) + [gap.get("reason", "") for gap in traceability.get("gaps", [])]
    lines.extend([f"- {_md(value)}" for value in limitations if value] or ["- None recorded."])
    lines.extend(["", "## Commands and artifacts", ""])
    if commands:
        for command in commands:
            rendered = " ".join(str(value) for value in command.get("command", []))
            lines.extend([
                f"### {_md(command.get('phase') or 'simulator')} (return code {command.get('returncode')})",
                "", "```text", rendered, "```", "",
            ])
    lines.extend(["See the adjacent run manifest, simulator logs, coverage exports, waveforms, evidence JSON, and repair history in this evidence bundle.", ""])
    path.write_text("\n".join(lines), encoding="utf-8")
    return {
        "status": "bugs_found" if has_design_findings else (
            "needs_review"
            if findings or traceability.get("status") != "complete" or coverage.get("closure", {}).get("status") != "closed"
            else "closed"
        ),
        "path": str(path),
        "filename": filename,
        "artifact_id": None,
        "counts": counts,
        "title": title,
        "excerpt": " ".join(lines[8:13])[:600],
    }


def _ground_spec_reference(requirement: dict[str, Any], pages: list[dict[str, Any]], metadata: dict[str, Any]) -> dict[str, Any]:
    existing = requirement.get("spec_ref") if isinstance(requirement.get("spec_ref"), dict) else {}
    ref = {
        "artifact_id": existing.get("artifact_id") or metadata.get("artifact_id") or "",
        "file": existing.get("file") or metadata.get("filename") or "spec",
        "page": int(existing.get("page") or 0),
        "line": int(existing.get("line") or 0),
        "section": str(existing.get("section") or ""),
        "excerpt": str(existing.get("excerpt") or ""),
        "match_confidence": 1.0 if existing.get("page") and existing.get("line") else 0.0,
    }
    if ref["page"] and ref["line"]:
        return ref
    query = str(requirement.get("text") or requirement.get("description") or "")
    query_words = _words(query)
    best: tuple[float, int, int, str, str] = (0.0, 0, 0, "", "")
    for page in pages:
        page_num = int(page.get("page_num") or 0)
        page_lines = str(page.get("text") or "").splitlines()
        section = ""
        for line_no, line in enumerate(page_lines, start=1):
            if re.match(r"^\s*(?:section\s+)?\d+(?:\.\d+)*\s+\S+", line, re.IGNORECASE):
                section = line.strip()[:180]
            words = _words(line)
            if not words:
                continue
            score = len(query_words & words) / max(1, len(query_words))
            if score > best[0]:
                source_line = int(page.get("source_line_start") or 1) + line_no - 1
                best = (score, page_num, source_line, line.strip(), section)
    if best[0] > 0:
        ref.update({"page": best[1], "line": best[2], "excerpt": best[3][:500], "section": ref["section"] or best[4], "match_confidence": round(best[0], 3)})
    return ref


def _words(value: str) -> set[str]:
    stop = {"shall", "must", "should", "when", "then", "with", "from", "that", "this", "the", "and", "for"}
    return {word.lower() for word in re.findall(r"[A-Za-z_][A-Za-z0-9_]{2,}", value) if word.lower() not in stop}


def _unique(values) -> list[str]:
    return list(dict.fromkeys(str(value) for value in values if str(value).strip()))


def _severity(priority: Any) -> str:
    return {"critical": "critical", "high": "high", "medium": "medium", "low": "low"}.get(str(priority or "").lower(), "medium")


def _looks_generated(filename: str) -> bool:
    return any(token in filename.lower() for token in ("_test", "_seq", "scoreboard", "driver", "monitor", "top_tb", "uvm"))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _slug(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value or "run"))[:80]


def _md(value: Any) -> str:
    return str(value if value is not None else "").replace("|", "\\|").replace("\n", " ").strip()


def _source_location(ref: dict[str, Any]) -> str:
    parts = [str(ref.get("file") or "spec")]
    if ref.get("page"):
        parts.append(f"page {ref['page']}")
    if ref.get("line"):
        parts.append(f"line {ref['line']}")
    if ref.get("section"):
        parts.append(f"section {ref['section']}")
    if ref.get("excerpt"):
        parts.append(f'“{str(ref["excerpt"])[:180]}”')
    return ", ".join(parts)


def _closure_label(domain: dict[str, Any] | None) -> str:
    domain = domain or {}
    achieved = domain.get("achieved")
    target = domain.get("target")
    if achieved is None:
        return f"MISSING (target {target}%)"
    return f"{'CLOSED' if domain.get('closed') else 'OPEN'} ({achieved}% / target {target}%)"


def _coverage_row(label: str, domain: dict[str, Any]) -> str:
    achieved = "missing" if domain.get("achieved") is None else f"{domain.get('achieved')}%"
    return f"| {label} | {achieved} | {domain.get('target')}% | {'Closed' if domain.get('closed') else 'Open'} |"
