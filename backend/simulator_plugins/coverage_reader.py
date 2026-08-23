"""Parse functional and code coverage summaries and compute closure."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any


DEFAULT_FUNCTIONAL_TARGET = 100.0
DEFAULT_CODE_TARGET = 90.0


def parse_coverage_report(
    path_or_text: str | Path,
    *,
    targets: dict[str, float] | None = None,
) -> dict[str, Any]:
    text = _read(path_or_text)
    metrics: dict[str, float] = {}
    patterns = {
        "functional": r"(?:Functional|Covergroup)(?:\s+Coverage)?[^%\n]{0,36}?([0-9]+(?:\.[0-9]+)?)\s*%",
        "statement": r"(?:Statement|Stmt|Line|Block)(?:\s+Coverage)?[^%\n]{0,36}?([0-9]+(?:\.[0-9]+)?)\s*%",
        "branch": r"Branch(?:\s+Coverage)?[^%\n]{0,36}?([0-9]+(?:\.[0-9]+)?)\s*%",
        "toggle": r"Toggle(?:\s+Coverage)?[^%\n]{0,36}?([0-9]+(?:\.[0-9]+)?)\s*%",
        "fsm": r"FSM(?:\s+Coverage)?[^%\n]{0,36}?([0-9]+(?:\.[0-9]+)?)\s*%",
        "assertion": r"Assertion(?:\s+Coverage)?[^%\n]{0,36}?([0-9]+(?:\.[0-9]+)?)\s*%",
        "expression": r"Expression(?:\s+Coverage)?[^%\n]{0,36}?([0-9]+(?:\.[0-9]+)?)\s*%",
        "code": r"(?:Overall\s+)?Code(?:\s+Coverage)?[^%\n]{0,36}?([0-9]+(?:\.[0-9]+)?)\s*%",
    }
    for key, pattern in patterns.items():
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            metrics[key] = float(match.group(1))

    gaps = []
    for line in text.splitlines():
        lowered = line.lower()
        if any(token in lowered for token in ("missing", "uncovered", "hole", "waiver")):
            gaps.append(line.strip())

    target_values = targets or {}
    functional_target = _target(target_values.get("functional"), DEFAULT_FUNCTIONAL_TARGET)
    code_target = _target(target_values.get("code"), DEFAULT_CODE_TARGET)
    code_metric_names = ("statement", "branch", "toggle", "fsm", "assertion", "expression")
    code_values = [metrics[name] for name in code_metric_names if name in metrics]
    code_achieved = metrics.get("code")
    if code_achieved is None and code_values:
        code_achieved = min(code_values)
    functional_achieved = metrics.get("functional")
    functional_closed = functional_achieved is not None and functional_achieved >= functional_target
    code_closed = code_achieved is not None and code_achieved >= code_target
    available = bool(metrics or gaps)
    closure_status = "closed" if functional_closed and code_closed else ("open" if available else "missing")
    functional_gaps = [
        gap for gap in gaps
        if re.search(r"\b(?:bin|coverpoint|covergroup|cross|functional)\b", gap, re.IGNORECASE)
    ]
    code_gaps = [gap for gap in gaps if gap not in functional_gaps]
    return {
        "status": "available" if metrics or gaps else "missing",
        "metrics": metrics,
        "gaps": gaps[:50],
        "functional": {
            "achieved": functional_achieved,
            "target": functional_target,
            "closed": functional_closed,
            "gaps": functional_gaps[:50],
        },
        "code": {
            "achieved": code_achieved,
            "target": code_target,
            "closed": code_closed,
            "metrics": {name: metrics[name] for name in code_metric_names if name in metrics},
            "gaps": code_gaps[:50],
        },
        "closure": {
            "status": closure_status,
            "functional_closed": functional_closed,
            "code_closed": code_closed,
            "blocking_gaps": gaps[:50],
        },
    }


def merge_coverage_reports(
    paths: list[str | Path],
    *,
    targets: dict[str, float] | None = None,
) -> dict[str, Any]:
    """Merge IMC/Xcelium text exports, retaining all visible metrics."""
    texts = []
    sources = []
    for raw in paths:
        path = Path(raw)
        if path.exists() and path.is_file():
            texts.append(path.read_text(encoding="utf-8", errors="ignore"))
            sources.append(str(path))
    parsed_reports = [parse_coverage_report(text, targets=targets) for text in texts]
    best_metrics: dict[str, float] = {}
    gaps: list[str] = []
    labels = {
        "functional": "Functional Coverage",
        "statement": "Statement Coverage",
        "branch": "Branch Coverage",
        "toggle": "Toggle Coverage",
        "fsm": "FSM Coverage",
        "assertion": "Assertion Coverage",
        "expression": "Expression Coverage",
        "code": "Code Coverage",
    }
    for report in parsed_reports:
        for name, value in report.get("metrics", {}).items():
            best_metrics[name] = max(best_metrics.get(name, 0.0), float(value))
        gaps.extend(str(value) for value in report.get("gaps", []))
    normalized = [f"{labels.get(name, name)}: {value}%" for name, value in best_metrics.items()]
    normalized.extend(list(dict.fromkeys(gaps)))
    merged = parse_coverage_report("\n".join(normalized), targets=targets)
    merged["sources"] = sources
    return merged


def _target(value: Any, default: float) -> float:
    try:
        return max(0.0, min(100.0, float(value)))
    except (TypeError, ValueError):
        return default


def _read(path_or_text: str | Path) -> str:
    if isinstance(path_or_text, str) and ("\n" in path_or_text or "\r" in path_or_text):
        return path_or_text
    path = Path(path_or_text)
    if path.exists() and path.is_file():
        return path.read_text(encoding="utf-8", errors="ignore")
    return str(path_or_text or "")
